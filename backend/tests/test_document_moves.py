import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import documents as documents_api
from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.models.access import DocumentCategory, KnowledgeSpace, SpaceMember
from app.models.auth import AuditEvent, User
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.document_uploads import DocumentUploadSession
from app.repositories import documents
from app.services import minio_storage
from app.services.document_processor import derive_category, stable_knowledge_id
from app.services.pgvector_store import PgVectorStore
from tests.test_documents import (
    _create_document, _create_space, _member_headers, _multipart_session,
    multipart_documents,
)


@pytest.fixture
def move_client(tmp_path, monkeypatch):
    path = tmp_path / "document-moves.sqlite3"
    monkeypatch.setattr(settings, "db_path", path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "temporary_attachment_cleanup_interval_seconds", 0)
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(minio_storage, "load_minio_settings", lambda: None)
    monkeypatch.setattr(documents_api, "_compile_knowledge_graph", lambda *args: None)
    monkeypatch.setattr(documents_api, "run_knowledge_pipeline", lambda **kwargs: {"chunk_count": 1})
    try:
        with TestClient(app) as client:
            yield client
    finally:
        database.reset_database(path)


def _seed_versions(client, source, headers):
    uploaded = _create_document(client, source["id"], headers, "公司介绍", "星海公司成立于2022年12月。".encode())["document"]
    with transaction() as db:
        document = db.get(Document, uploaded["id"])
        assert document is not None
        current = documents.latest_version(db, document.id)
        assert current is not None and current.processing_stage == "ready"
        assert current.file_path is not None
        uploaded["current_published_version"] = {"id": current.id}
        old_path = settings.upload_dir / source["id"] / "previous.txt"
        old_path.write_bytes("星海公司历史资料。".encode())
        historical = DocumentVersion(
            id="ver_move_history", document_id=document.id, version_number=2,
            processing_stage="ready", queryable=True, retryable=False,
            file_path=str(old_path), source_relative_path="previous.txt",
            chunk_count=1, is_current_published=False,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        db.add(historical)
        db.flush()
        return uploaded, {current.id: Path(current.file_path).read_bytes(), historical.id: old_path.read_bytes()}


@pytest.mark.parametrize("object_storage", [False, True], ids=["local", "minio"])
def test_move_preserves_versions_storage_scope_and_citations(move_client, monkeypatch, object_storage):
    client = move_client
    source, headers = _create_space(client, "原文件夹")
    target, _ = _create_space(client, "目标文件夹")
    document, contents = _seed_versions(client, source, headers)
    source_reader = _member_headers(client, source["id"], "source-reader", "reader")
    target_reader = _member_headers(client, target["id"], "target-reader", "reader")
    objects = {}
    purged = []
    if object_storage:
        config = SimpleNamespace(bucket="moves")
        monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: True)
        monkeypatch.setattr(minio_storage, "load_minio_settings", lambda: config)

        def upload(*, content, object_key, **kwargs):
            uri = minio_storage.build_minio_uri(config.bucket, object_key)
            objects[uri] = content
            return uri

        def remove(uri):
            # 删除任一旧对象时，全部版本的新对象与数据库路径都已就绪。
            with transaction() as db:
                moved = db.get(Document, document["id"])
                assert moved is not None
                assert moved.space_id == target["id"]
                assert all(objects[version.file_path] == contents[version.id] for version in moved.versions)
            purged.append(uri)
            objects.pop(uri, None)
            return True

        monkeypatch.setattr(minio_storage, "upload_bytes", upload)
        monkeypatch.setattr(minio_storage, "download_bytes", lambda *, minio_uri: objects[minio_uri])
        monkeypatch.setattr(documents_api, "_delete_minio_object", remove)
        with transaction() as db:
            stored = db.get(Document, document["id"])
            assert stored is not None
            for version in stored.versions:
                key = minio_storage.object_key_for_version(space_id=source["id"], document_id=stored.id, version_id=version.id, filename="original.txt")
                version.file_path = upload(content=contents[version.id], object_key=key)

    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None
        old_refs = [version.file_path for version in stored.versions if version.file_path]
        category = DocumentCategory(id="cat_move_target", space_id=target["id"], name="公司资料", sort_order=0, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        db.add(category)
        old_updated_at = stored.updated_at

    # 真正写入/清理 SQLite 向量表，只替换外部 embedding 与流水线模型调用。
    import app.services.pgvector_store as vector_module
    dimensions = vector_module._configured_embedding_dimensions()
    monkeypatch.setattr(vector_module, "_build_embedding_function", lambda **kwargs: lambda texts: [[1.0] + [0.0] * (dimensions - 1) for _ in texts])
    store = PgVectorStore()
    for version_id, content in contents.items():
        store.chunk_add(derive_category(source["id"]), file_id=str(stable_knowledge_id(version_id)), document=content.decode(), metadata={"chunk_index": 0})

    def pipeline(**kwargs):
        vector_id = store.chunk_add(kwargs["category"], file_id=kwargs["file_id"], document=kwargs["text"], metadata={"chunk_index": 0})
        return {"chunk_count": 1, "vector_ids": [vector_id]}

    monkeypatch.setattr(documents_api, "run_knowledge_pipeline", pipeline)
    moved = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"], "category_id": "cat_move_target"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["category_id"] == "cat_move_target"
    assert moved.json()["current_published_version"]["id"] == document["current_published_version"]["id"]
    for space, expected in [(source, False), (target, True)]:
        listing = client.get(f"/knowledge-spaces/{space['id']}/documents", headers=headers)
        assert (document["id"] in {item["id"] for item in listing.json()["items"]}) is expected
    assert client.get(f"/documents/{document['id']}", headers=source_reader).status_code == 404
    assert client.get(f"/documents/{document['id']}", headers=target_reader).status_code == 200
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None
        assert stored.updated_at > old_updated_at
        assert len({version.file_path for version in stored.versions}) == 2
        for version in stored.versions:
            assert version.file_path is not None
            assert version.queryable and version.processing_stage == "ready"
            assert documents_api._load_version_bytes(version)[0] == contents[version.id]
            assert target["id"] in version.file_path
        assert all(job.space_id == target["id"] for job in db.scalars(select(DocumentJob).where(DocumentJob.document_id == stored.id)))
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "document_moved_to_space", AuditEvent.target_id == stored.id)) is not None
        chunks = db.execute(select(vector_module._DOCUMENT_EMBEDDINGS.c.category, vector_module._DOCUMENT_EMBEDDINGS.c.file_id)).all()
        assert {(row.category, row.file_id) for row in chunks} == {(derive_category(target["id"]), str(stable_knowledge_id(version_id))) for version_id in contents}
    if object_storage:
        assert all(ref in purged and ref not in objects for ref in old_refs)
    else:
        assert all(not Path(ref).exists() for ref in old_refs)

    from app.services.chat_knowledge import build_knowledge_context
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    with transaction() as db:
        owner = db.get(User, owner_id)
        assert owner is not None
        _, old_retrieval = build_knowledge_context(db, user=owner, message="星海公司成立", space_ids=[source["id"]], document_ids=[])
        _, new_retrieval = build_knowledge_context(db, user=owner, message="星海公司成立", space_ids=[target["id"]], document_ids=[])
        assert not old_retrieval["references"]
        assert any(ref["document_id"] == document["id"] and ref["space_id"] == target["id"] for ref in new_retrieval["references"])
    repeated = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"]})
    assert repeated.status_code == 422


def test_move_rejects_invalid_targets_permissions_and_temporary_documents(move_client):
    client = move_client
    source, headers = _create_space(client, "源")
    target, _ = _create_space(client, "目标")
    archived, _ = _create_space(client, "归档目标")
    document = _create_document(client, source["id"], headers, "资料")["document"]
    source_only = _member_headers(client, source["id"], "source-only", "contributor")
    target_only = _member_headers(client, target["id"], "target-only", "contributor")
    source_reader = _member_headers(client, source["id"], "reader-only", "reader")
    with transaction() as db:
        archived_space = db.get(KnowledgeSpace, archived["id"])
        assert archived_space is not None
        archived_space.status = "archived"
        db.add(DocumentCategory(id="cat_wrong_space", space_id=source["id"], name="源分类", sort_order=0, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    for target_id in ["missing", " ", source["id"], archived["id"]]:
        rejected = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target_id})
        assert rejected.status_code == 422, rejected.text
    for denied in [source_only, target_only, source_reader]:
        rejected = client.post(f"/documents/{document['id']}:move-to-space", headers=denied, json={"space_id": target["id"]})
        assert rejected.status_code == 404, rejected.text
    wrong_category = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"], "category_id": "cat_wrong_space"})
    assert wrong_category.status_code == 404
    assert client.post("/documents/missing:move-to-space", headers=headers, json={"space_id": target["id"]}).status_code == 404
    session = client.post("/chat/sessions", headers=headers, json={"title": "临时附件"}).json()
    attachment = client.post("/chat/attachments", headers=headers, files={"file": ("temp.txt", b"temporary attachment", "text/plain")}, data={"chat_session_id": session["id"]}).json()["document"]
    assert client.post(f"/documents/{attachment['id']}:move-to-space", headers=headers, json={"space_id": target["id"]}).status_code == 422
    # 管理员身份不能代替任一侧的实际成员身份。
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None
        owner_id = stored.created_by_user_id
        other_owner = db.scalar(select(SpaceMember.user_id).where(SpaceMember.space_id == target["id"], SpaceMember.user_id != owner_id))
        target_space = db.get(KnowledgeSpace, target["id"])
        assert target_space is not None and other_owner is not None
        target_space.owner_user_id = other_owner
        member = db.scalar(select(SpaceMember).where(SpaceMember.space_id == target["id"], SpaceMember.user_id == owner_id))
        assert member is not None
        db.delete(member)
    assert client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"]}).status_code == 404
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None and stored.space_id == source["id"]


def test_move_rolls_back_storage_failure_and_blocks_reentrant_move(move_client, monkeypatch):
    client = move_client
    source, headers = _create_space(client, "源")
    target, _ = _create_space(client, "目标")
    third, _ = _create_space(client, "另一目标")
    document, contents = _seed_versions(client, source, headers)
    original_load = documents_api._load_version_bytes
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None
        old_refs = [version.file_path for version in stored.versions if version.file_path]

    def broken_load(version):
        if version.id == "ver_move_history":
            raise OSError("copy failed")
        return original_load(version)

    monkeypatch.setattr(documents_api, "_load_version_bytes", broken_load)
    failed = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"]})
    assert failed.status_code == 502
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None
        assert stored.space_id == source["id"]
        assert all(original_load(version)[0] == contents[version.id] for version in stored.versions)
        assert all(version.processing_stage == "ready" for version in stored.versions)
    assert all(Path(ref).exists() for ref in old_refs)
    assert not list((settings.upload_dir / target["id"]).rglob("*.txt"))
    monkeypatch.setattr(documents_api, "_load_version_bytes", original_load)
    original_reindex = documents_api._reindex_persisted_document
    blocked = []

    async def reindex(**kwargs):
        response = await asyncio.to_thread(
            client.post, f"/documents/{document['id']}:move-to-space",
            headers=headers, json={"space_id": third["id"]},
        )
        blocked.append(response.status_code)
        return await original_reindex(**kwargs)

    monkeypatch.setattr(documents_api, "_reindex_persisted_document", reindex)
    moved = client.post(f"/documents/{document['id']}:move-to-space", headers=headers, json={"space_id": target["id"]})
    assert moved.status_code == 200, moved.text
    assert blocked == [422, 422]


def test_move_compare_and_swap_rejects_stale_document(move_client):
    client = move_client
    source, headers = _create_space(client, "源")
    target, _ = _create_space(client, "目标")
    third, _ = _create_space(client, "另一目标")
    document = _create_document(client, source["id"], headers, "资料")["document"]
    with database.SessionLocal() as stale_db:
        stale = stale_db.get(Document, document["id"])
        assert stale is not None
        with transaction() as db:
            fresh = db.get(Document, document["id"])
            assert fresh is not None
            assert documents.move_document_to_space(db, fresh, space_id=target["id"], category_id=None)
        assert not documents.move_document_to_space(stale_db, stale, space_id=third["id"], category_id=None)
        stale_db.rollback()
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None and stored.space_id == target["id"]


def test_move_reports_reindex_failure_without_losing_originals(move_client, monkeypatch):
    client = move_client
    source, headers = _create_space(client, "源")
    target, _ = _create_space(client, "目标")
    document = _create_document(client, source["id"], headers, "资料")["document"]
    with transaction() as db:
        version = documents.latest_version(db, document["id"])
        assert version is not None
        original = documents_api._load_version_bytes(version)[0]

    def fail_pipeline(**kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(documents_api, "run_knowledge_pipeline", fail_pipeline)
    response = client.post(
        f"/documents/{document['id']}:move-to-space", headers=headers,
        json={"space_id": target["id"]},
    )
    assert response.status_code == 502, response.text
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None and stored.space_id == target["id"]
        version = documents.latest_version(db, document["id"])
        assert version is not None
        assert version.processing_stage == "failed" and version.retryable
        assert not version.queryable and not version.is_current_published
        assert documents_api._load_version_bytes(version)[0] == original


@pytest.mark.parametrize("object_storage", [True, False], ids=["minio", "local"])
def test_multipart_move_reindex_failure_can_retry(
    multipart_documents, monkeypatch, object_storage,
):
    ctx = multipart_documents
    monkeypatch.setattr(documents_api, "_compile_knowledge_graph", lambda *args: None)
    monkeypatch.setattr(documents_api, "run_knowledge_pipeline", lambda **kwargs: {"chunk_count": 1})
    created = _multipart_session(ctx).json()
    completed = ctx.client.post(
        f"/document-uploads/{created['id']}/complete", headers=ctx.headers, json={},
    )
    assert completed.status_code == 200, completed.text
    assert ctx.service.DocumentUploadDispatcher().run_once()
    with transaction() as db:
        source_upload = db.get(DocumentUploadSession, created["id"])
        assert source_upload is not None and source_upload.status == "completed"
        document_id, version_id = source_upload.document_id, source_upload.version_id
        old_object = (source_upload.canonical_bucket, source_upload.canonical_object_key)
    target, _ = _create_space(ctx.client, "移动后的重试")
    with transaction() as db:
        stamp = datetime.now(timezone.utc)
        db.add(DocumentCategory(
            id="cat_multipart_move", space_id=target["id"], name="目标分类",
            sort_order=0, created_at=stamp, updated_at=stamp,
        ))

    def upload(*, content, object_key, **kwargs):
        ctx.objects["documents", object_key] = content
        return minio_storage.build_minio_uri("documents", object_key)

    def delete(uri):
        ctx.objects.pop(minio_storage.parse_minio_uri(uri), None)
        return True

    async def fail_reindex(**kwargs):
        return False

    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: object_storage)
    monkeypatch.setattr(minio_storage, "upload_bytes", upload)
    monkeypatch.setattr(
        minio_storage, "download_bytes",
        lambda *, minio_uri: ctx.objects[minio_storage.parse_minio_uri(minio_uri)],
    )
    monkeypatch.setattr(documents_api, "_delete_minio_object", delete)
    monkeypatch.setattr(documents_api, "_reindex_persisted_document", fail_reindex)
    moved = ctx.client.post(
        f"/documents/{document_id}:move-to-space", headers=ctx.headers,
        json={"space_id": target["id"], "category_id": "cat_multipart_move"},
    )
    assert moved.status_code == 502, moved.text
    assert old_object not in ctx.objects
    retried = ctx.client.post(
        f"/documents/{document_id}/versions/{version_id}:retry", headers=ctx.headers,
    )
    assert retried.status_code == 200, retried.text
    with transaction() as db:
        source_upload = db.get(DocumentUploadSession, created["id"])
        version = db.get(DocumentVersion, version_id)
        assert source_upload is not None and version is not None
        assert source_upload.space_id == target["id"]
        assert source_upload.category_id == "cat_multipart_move"
        retry = db.scalar(select(DocumentUploadSession).where(
            DocumentUploadSession.source_session_id == created["id"],
        ))
        if object_storage:
            assert retry is not None and retry.status == "queued"
            assert retry.space_id == target["id"]
            assert retry.category_id == "cat_multipart_move"
            assert (retry.canonical_bucket, retry.canonical_object_key) == minio_storage.parse_minio_uri(version.file_path)
            assert ctx.objects[retry.canonical_bucket, retry.canonical_object_key] == ctx.payload
        else:
            assert retry is None
            assert source_upload.canonical_object_key is None
            assert Path(version.file_path).read_bytes() == ctx.payload
    if object_storage:
        assert ctx.service.DocumentUploadDispatcher().run_once()
    detail = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()
    assert detail["latest_version"]["processing_stage"] == "ready"
    with transaction() as db:
        retry_version = db.get(DocumentVersion, detail["latest_version"]["id"])
        assert retry_version is not None and retry_version.retry_of_version_id == version_id


def test_move_invalidates_mixed_source_graph_and_preserves_target(move_client, monkeypatch, caplog):
    import json
    from app.knowledge_engine import KnowledgeCompiler, RawEntity
    from app.models.knowledge_graph import KnowledgeEntity, KnowledgeEntityDocument, KnowledgeRelation

    client = move_client
    source, headers = _create_space(client, "源图谱")
    target, _ = _create_space(client, "目标图谱")
    document_a = _create_document(client, source["id"], headers, "公开资料A")["document"]
    document_b = _create_document(client, source["id"], headers, "迁移资料B")["document"]
    document_c = _create_document(client, target["id"], headers, "目标原有资料C")["document"]
    reader = _member_headers(client, source["id"], "graph-source-reader", "reader")
    stamp = datetime.now(timezone.utc)

    def entity(entity_id, space_id, source_ids, description):
        return KnowledgeEntity(
            id=entity_id, space_id=space_id, canonical_name=entity_id,
            entity_type="organization", description=description,
            aliases_json="[]", properties_json="{}",
            source_document_version_ids_json=json.dumps(source_ids),
            created_at=stamp, updated_at=stamp,
        )

    with transaction() as db:
        version_a = documents.current_published_version(db, document_a["id"])
        version_b = documents.current_published_version(db, document_b["id"])
        version_c = documents.current_published_version(db, document_c["id"])
        assert version_a and version_b and version_c
        a_id, b_id, c_id = version_a.id, version_b.id, version_c.id
        shared = entity("ent_shared", source["id"], [a_id], "A的公开描述")
        db.add_all([
            shared,
            entity("ent_b_only", source["id"], [b_id], "B独有原文"),
            entity("ent_b_link_only", source["id"], [], "B链接来源原文"),
            entity("ent_orphan", source["id"], [], "无来源正文"),
            entity("ent_a_clean", source["id"], [a_id], "A的独立实体"),
            entity("ent_target_existing", target["id"], [c_id], "目标原有实体"),
        ])
        db.flush()
        db.add_all([
            KnowledgeEntityDocument(entity_id="ent_shared", document_version_id=a_id, created_at=stamp),
            KnowledgeEntityDocument(entity_id="ent_b_link_only", document_version_id=b_id, created_at=stamp),
            KnowledgeEntityDocument(entity_id="ent_a_clean", document_version_id=a_id, created_at=stamp),
            KnowledgeEntityDocument(entity_id="ent_target_existing", document_version_id=c_id, created_at=stamp),
        ])
        KnowledgeCompiler(db)._supplement_entity(
            shared,
            RawEntity(
                canonical_name="共享实体", entity_type="organization",
                description="B补充的敏感原文", aliases=["B敏感别名"],
                properties={"private": "B敏感属性"},
            ),
            version_b, stamp,
        )
        assert "B补充的敏感原文" in shared.description
        db.add(KnowledgeRelation(
            id="rel_mixed", space_id=source["id"],
            subject_entity_id="ent_shared", object_entity_id="ent_a_clean",
            relation_type="related_to", description="B补充关系", created_at=stamp,
        ))

    def compile_target(db, version, text, snapshot):
        assert version.id == b_id
        db.add(entity("ent_target_rebuilt", target["id"], [b_id], "B的目标实体"))
        db.flush()
        db.add(KnowledgeEntityDocument(
            entity_id="ent_target_rebuilt", document_version_id=b_id, created_at=stamp,
        ))

    monkeypatch.setattr(documents_api, "_compile_knowledge_graph", compile_target)
    response = client.post(
        f"/documents/{document_b['id']}:move-to-space", headers=headers,
        json={"space_id": target["id"]},
    )
    assert response.status_code == 200, response.text
    listed = client.get(f"/knowledge-graph/spaces/{source['id']}/entities", headers=reader)
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()} == {"ent_a_clean"}
    assert "B敏感" not in listed.text and "B补充" not in listed.text
    assert client.get(f"/documents/{document_a['id']}", headers=reader).status_code == 200
    target_list = client.get(f"/knowledge-graph/spaces/{target['id']}/entities", headers=headers)
    assert target_list.status_code == 200
    assert {item["id"] for item in target_list.json()} == {"ent_target_existing", "ent_target_rebuilt"}
    with transaction() as db:
        source_links = db.execute(
            select(KnowledgeEntityDocument.entity_id)
            .join(KnowledgeEntity, KnowledgeEntity.id == KnowledgeEntityDocument.entity_id)
            .where(KnowledgeEntity.space_id == source["id"], KnowledgeEntityDocument.document_version_id == b_id)
        ).all()
        assert source_links == []
        assert db.get(KnowledgeRelation, "rel_mixed") is None
        assert db.get(KnowledgeEntity, "ent_shared") is None
        assert db.get(KnowledgeEntityDocument, ("ent_shared", a_id)) is None
        assert db.get(KnowledgeEntityDocument, ("ent_target_existing", c_id)) is not None
        assert documents.invalidate_moved_document_graph(
            db, source_space_id=source["id"], version_ids=[b_id]
        ) == []
        assert db.get(KnowledgeEntityDocument, ("ent_target_rebuilt", b_id)) is not None
    assert "源实体已失效" in caplog.text and a_id in caplog.text


def test_move_source_graph_failure_rolls_back_entire_move(move_client, monkeypatch, caplog):
    import json
    from app.models.knowledge_graph import KnowledgeEntity, KnowledgeEntityDocument

    client = move_client
    source, headers = _create_space(client, "源图谱")
    target, _ = _create_space(client, "目标图谱")
    document = _create_document(client, source["id"], headers, "资料")["document"]
    with transaction() as db:
        version = documents.current_published_version(db, document["id"])
        assert version is not None
        version_id = version.id
        original_path = version.file_path
        original_content = documents_api._load_version_bytes(version)[0]
        stamp = datetime.now(timezone.utc)
        db.add(KnowledgeEntity(
            id="ent_invalidation_failure", space_id=source["id"],
            canonical_name="事务回滚实体", entity_type="organization",
            source_document_version_ids_json=json.dumps([version_id]),
            created_at=stamp, updated_at=stamp,
        ))
        db.flush()
        db.add(KnowledgeEntityDocument(
            entity_id="ent_invalidation_failure", document_version_id=version_id,
            created_at=stamp,
        ))

    original = documents.invalidate_moved_document_graph

    def fail_after_delete(db, **kwargs):
        original(db, **kwargs)
        raise RuntimeError("graph transaction unavailable")

    monkeypatch.setattr(documents, "invalidate_moved_document_graph", fail_after_delete)
    response = client.post(
        f"/documents/{document['id']}:move-to-space", headers=headers,
        json={"space_id": target["id"]},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "文档移动失败，请稍后重试"
    with transaction() as db:
        stored = db.get(Document, document["id"])
        assert stored is not None and stored.space_id == source["id"]
        version = documents.current_published_version(db, document["id"])
        assert version is not None and version.queryable
        assert version.file_path == original_path
        assert documents_api._load_version_bytes(version)[0] == original_content
        assert all(job.space_id == source["id"] for job in db.scalars(
            select(DocumentJob).where(DocumentJob.document_id == document["id"])
        ))
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "document_moved_to_space",
            AuditEvent.target_id == document["id"],
        )) is None
        assert db.get(KnowledgeEntity, "ent_invalidation_failure") is not None
        assert db.get(KnowledgeEntityDocument, ("ent_invalidation_failure", version_id)) is not None
    listed = client.get(f"/knowledge-graph/spaces/{source['id']}/entities", headers=headers)
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {"ent_invalidation_failure"}
    assert not list((settings.upload_dir / target["id"]).rglob("*.txt"))
    assert "document move transaction failed" in caplog.text
    assert document["id"] in caplog.text

    monkeypatch.setattr(documents, "invalidate_moved_document_graph", original)
    retried = client.post(
        f"/documents/{document['id']}:move-to-space", headers=headers,
        json={"space_id": target["id"]},
    )
    assert retried.status_code == 200, retried.text
    repeated = client.post(
        f"/documents/{document['id']}:move-to-space", headers=headers,
        json={"space_id": target["id"]},
    )
    assert repeated.status_code == 422
