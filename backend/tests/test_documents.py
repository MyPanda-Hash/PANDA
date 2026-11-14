import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.documents import _read_upload_bytes
from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.models.auth import User
from app.models.documents import Document
from app.repositories import documents as document_repository
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests._auth import bootstrap_admin_response


@pytest.mark.anyio
async def test_document_upload_reader_stops_at_configured_limit(monkeypatch) -> None:
    from io import BytesIO
    from fastapi import UploadFile

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    payload = b"x" * (2 * 1024 * 1024)
    upload = UploadFile(file=BytesIO(payload), filename="large.txt")
    try:
        assert await _read_upload_bytes(upload) == payload[: 1024 * 1024 + 1]
    finally:
        await upload.close()


@pytest.fixture(autouse=True)
def _stub_document_processing(monkeypatch):
    """Avoid real MinIO / knowledge pipeline calls during document API tests."""
    monkeypatch.setattr(
        "app.api.documents.minio_storage.is_minio_enabled", lambda: False
    )

    def fake_run_knowledge_pipeline(*args, **kwargs):
        return {"chunk_count": 1, "vector_ids": ["vec-test"]}

    monkeypatch.setattr(
        "app.api.documents.run_knowledge_pipeline", fake_run_knowledge_pipeline
    )


def _accept_and_login(client: TestClient, username: str, password: str) -> dict:
    invited = client.post(
        "/admin/users",
        headers=_admin_headers(client),
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    ).json()
    accepted = client.post(
        f"/auth/invitations/{invited['invite_token']}/accept",
        json={"password": password, "password_confirmation": password},
    )
    assert accepted.status_code == 200
    return client.post(
        "/auth/login", json={"username": username, "password": password}
    ).json()


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = bootstrap_admin_response(client)
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _member_headers(
    client: TestClient, space_id: str, username: str, role: str
) -> dict[str, str]:
    admin_headers = _admin_headers(client)
    member = _accept_and_login(client, username, "Member-password-123")
    response = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=admin_headers,
        json={"user_id": member["user"]["id"], "role": role},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {member['access_token']}"}


def _create_space(client: TestClient, name: str) -> tuple[dict, dict[str, str]]:
    admin_headers = _admin_headers(client)
    response = client.post(
        "/knowledge-spaces",
        headers=admin_headers,
        json={"name": name, "description": ""},
    )
    assert response.status_code == 201
    return response.json(), admin_headers


def _create_document(
    client: TestClient,
    space_id: str,
    headers: dict[str, str],
    name: str,
    content: bytes | None = None,
    extension: str = "txt",
) -> dict:
    response = client.post(
        f"/knowledge-spaces/{space_id}/documents",
        headers=headers,
        files={
            "file": (
                f"{name}.{extension}",
                content or f"{name} test content.".encode(),
                "text/plain",
            )
        },
        data={"name": name},
    )
    assert response.status_code == 201, (
        f"Upload failed: {response.json().get('detail', '')}"
    )
    return response.json()


def test_archived_space_documents_remain_readable_but_not_writable(tmp_path: Path):
    original = settings.db_path, settings.database_url, settings.upload_dir
    settings.db_path = tmp_path / "archived-space.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    try:
        with TestClient(app) as client:
            space, owner_headers = _create_space(client, "归档只读资料")
            space_url = f"/knowledge-spaces/{space['id']}"
            reader_headers = _member_headers(
                client, space["id"], "archive-reader", "reader"
            )
            outsider = _accept_and_login(client, "archive-outsider", "Member-password-123")
            outsider_headers = {"Authorization": f"Bearer {outsider['access_token']}"}
            category = client.post(
                f"{space_url}/categories", headers=owner_headers, json={"name": "归档分类"}
            )
            assert category.status_code == 201
            content = "归档前的原始资料内容".encode()
            created = _create_document(
                client, space["id"], owner_headers, "归档资料", content=content
            )
            document_id = created["document"]["id"]
            document_url = f"/documents/{document_id}"
            archived = client.post(f"{space_url}:archive", headers=owner_headers)
            assert archived.status_code == 200
            assert archived.json()["status"] == "archived"

            for headers in (owner_headers, reader_headers):
                listed = client.get(
                    f"{space_url}/documents",
                    params={"offset": 0, "limit": 10, "include_counts": True, "q": "归档"},
                    headers=headers,
                )
                assert listed.status_code == 200, listed.text
                assert [item["id"] for item in listed.json()["items"]] == [document_id]
                assert listed.json()["total"] == 1
                assert listed.json()["counts"]["all"] == 1
                categories = client.get(f"{space_url}/categories", headers=headers)
                assert categories.status_code == 200
                assert category.json()["id"] in {item["id"] for item in categories.json()}
                detail = client.get(document_url, headers=headers)
                assert detail.status_code == 200
                assert detail.json()["name"] == "归档资料"
                preview = client.get(f"{document_url}/preview", headers=headers)
                assert preview.status_code == 200
                downloaded = client.get(f"{document_url}/download", headers=headers)
                assert downloaded.status_code == 200
                assert downloaded.content == content

            read_paths = (
                f"{space_url}/documents", f"{space_url}/categories",
                document_url, f"{document_url}/preview", f"{document_url}/download",
            )
            for path in read_paths:
                assert client.get(path, headers=outsider_headers).status_code == 404
            assert client.post(
                f"{space_url}/documents", headers=owner_headers,
                files={"file": ("blocked.txt", b"must not upload", "text/plain")},
            ).status_code == 404
            assert client.patch(
                document_url, headers=owner_headers, json={"name": "不得重命名"}
            ).status_code == 404
            assert client.delete(document_url, headers=owner_headers).status_code == 404
            assert client.post(
                f"{space_url}/categories", headers=owner_headers, json={"name": "不得新增"}
            ).status_code == 404
            assert client.get(document_url, headers=reader_headers).json()["name"] == "归档资料"

            assert client.delete(space_url, headers=owner_headers).status_code == 204
            for path in read_paths:
                assert client.get(path, headers=owner_headers).status_code == 404
    finally:
        settings.db_path, settings.database_url, settings.upload_dir = original
        database.reset_database(tmp_path / "archived-space.sqlite3")


def test_chat_attachment_upload_is_private_and_has_no_knowledge_space(
    tmp_path: Path,
):
    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "chat-attachments.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 86400
    settings.temporary_attachment_cleanup_interval_seconds = 0
    try:
        with TestClient(app) as client:
            owner_headers = _admin_headers(client)
            session = client.post(
                "/chat/sessions",
                headers=owner_headers,
                json={"title": "临时附件会话"},
            )
            assert session.status_code == 201, session.text
            session_id = session.json()["id"]
            other_session = client.post(
                "/chat/sessions",
                headers=owner_headers,
                json={"title": "另一个会话"},
            )
            assert other_session.status_code == 201, other_session.text
            other_session_id = other_session.json()["id"]

            created = client.post(
                "/chat/attachments",
                headers=owner_headers,
                files={"file": ("项目说明.txt", "临时资料内容".encode(), "text/plain")},
                data={"name": "项目说明", "chat_session_id": session_id},
            )
            assert created.status_code == 201, created.text
            document = created.json()["document"]
            assert created.json()["task"]["resource_path"] == f"/chat/{session_id}"
            assert document["space_id"] is None
            assert document["is_temporary"] is True
            assert document["chat_session_id"] == session_id
            assert document["expires_at"]
            assert (
                client.get(
                    f"/documents/{document['id']}", headers=owner_headers
                ).status_code
                == 200
            )
            from app.services.agent_capability import (
                CapabilityGrantError, expand_citation, preview_document, search_evidence,
            )
            from app.services.chat_knowledge import build_knowledge_context, resolve_chat_writing_context

            owner = client.get("/auth/me", headers=owner_headers).json()
            with transaction() as db:
                db_owner = db.get(User, owner["id"])
                assert db_owner
                augmented, retrieval = build_knowledge_context(
                    db,
                    user=db_owner,
                    message="请总结临时资料",
                    space_ids=[],
                    document_ids=[document["id"]],
                    chat_session_id=session_id,
                )
                evidence = search_evidence(
                    db,
                    db_owner,
                    {
                        "space_mode": "accessible",
                        "space_ids": [],
                        "conversation_id": session_id,
                        "document_ids": [document["id"]],
                    },
                    {"query": "临时资料", "document_ids": [document["id"]]},
                )
                for conversation_id in (None, session_id):
                    unselected_claims = {
                        "space_mode": "accessible",
                        "space_ids": [],
                        "conversation_id": conversation_id,
                    }
                    for operation, arguments in (
                        (search_evidence, {"query": "临时资料", "document_ids": [document["id"]]}),
                        (preview_document, {"document_id": document["id"]}),
                        (expand_citation, {"document_id": document["id"]}),
                    ):
                        with pytest.raises(CapabilityGrantError):
                            operation(db, db_owner, unselected_claims, arguments)
            project_space = client.post(
                "/knowledge-spaces", headers=owner_headers,
                json={"name": "文稿固定范围"},
            )
            assert project_space.status_code == 201, project_space.text
            for project_spaces in ([], [project_space.json()["id"]]):
                project = client.post(
                    "/writing/projects", headers=owner_headers,
                    json={"title": "无临时附件授权的文稿", "space_ids": project_spaces},
                )
                assert project.status_code == 201, project.text
                with transaction() as db:
                    db_owner = db.get(User, owner["id"])
                    assert db_owner is not None
                    with pytest.raises(HTTPException) as rejected:
                        resolve_chat_writing_context(
                            db, user=db_owner,
                            context={"project_id": project.json()["id"]},
                            space_ids=[], document_ids=[document["id"]],
                        )
                    assert rejected.value.status_code == 422
            assert document["id"] in retrieval["document_ids"]
            assert document["id"] in {
                item["document_id"] for item in evidence["evidence"]
            }
            assert "临时资料内容" in augmented
            attachment_reference = next(
                item
                for item in retrieval["references"]
                if item["document_id"] == document["id"]
            )
            expected_attribution = {
                "created_by_user_id": owner["id"],
                "created_by_display_name": owner["display_name"] or owner["username"],
                "created_at": datetime.fromisoformat(document["created_at"])
                .replace(tzinfo=timezone.utc)
                .isoformat(),
            }
            assert {
                key: attachment_reference[key] for key in expected_attribution
            } == expected_attribution
            metadata = next(
                json.loads(line)
                for line in augmented.splitlines()
                if line.startswith("[{")
            )
            assert {
                key: metadata[0][key] for key in expected_attribution
            } == expected_attribution
            assert datetime.fromisoformat(metadata[0]["created_at"]).tzinfo is not None

            with transaction() as db:
                db_owner = db.get(User, owner["id"])
                assert db_owner
                with pytest.raises(HTTPException) as rejected:
                    build_knowledge_context(
                        db,
                        user=db_owner,
                        message="请总结临时资料",
                        space_ids=[],
                        document_ids=[document["id"]],
                        chat_session_id=other_session_id,
                    )
                assert rejected.value.status_code == 422

            other_login = _accept_and_login(
                client, "chat-attachment-other", "Other-password-123"
            )
            other_headers = {"Authorization": f"Bearer {other_login['access_token']}"}
            assert (
                client.get(
                    f"/documents/{document['id']}", headers=other_headers
                ).status_code
                == 404
            )
            assert client.get(
                f"/documents/{document['id']}/download", headers=owner_headers
            ).status_code == 200
            assert client.get(
                f"/documents/{document['id']}/download", headers=other_headers
            ).status_code == 404

            listed = client.get(
                f"/chat/attachments?chat_session_id={session_id}",
                headers=owner_headers,
            )
            assert listed.status_code == 200, listed.text
            items = listed.json()["items"]
            assert any(item["id"] == document["id"] for item in items)
            assert all(item["is_temporary"] is True for item in items)
            assert all(item["space_id"] is None for item in items)
            assert all(item["chat_session_id"] == session_id for item in items)

            listed_other_session = client.get(
                f"/chat/attachments?chat_session_id={other_session_id}",
                headers=owner_headers,
            )
            assert listed_other_session.status_code == 200
            assert listed_other_session.json()["items"] == []

            other_listed = client.get(
                f"/chat/attachments?chat_session_id={session_id}",
                headers=other_headers,
            )
            assert other_listed.status_code == 404
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "chat-attachments.sqlite3")


def _mark_document_ready(client: TestClient, document_id: str) -> None:
    with transaction() as db:
        db.execute(
            text(
                "UPDATE document_versions SET processing_stage = 'ready', queryable = 1, "
                "is_current_published = 1, chunk_count = 1 WHERE document_id = :document_id"
            ),
            {"document_id": document_id},
        )


def test_chat_attachment_does_not_replace_knowledge_scope(tmp_path: Path, monkeypatch) -> None:
    """模板附件与公司证据并存，显式范围和失效附件不能被放大。"""
    from app.api.chat import _resolve_chat_citations, _stream_capability_preflight
    from app.core.database import SessionLocal
    from app.services.chat_knowledge import build_knowledge_context

    path = tmp_path / "attachment-knowledge-scope.sqlite3"
    monkeypatch.setattr(settings, "db_path", path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "temporary_attachment_cleanup_interval_seconds", 0)
    monkeypatch.setattr(settings, "temporary_attachment_ttl_seconds", 86400)
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            owner_id = client.get("/auth/me", headers=headers).json()["id"]
            session = client.post("/chat/sessions", headers=headers, json={"title": "附件与知识"}).json()
            space, _ = _create_space(client, "公司事实资料")
            company = _create_document(
                client, space["id"], headers, "星海公司介绍",
                "星海公司成立于2022年12月，研发数字员工。".encode(),
            )["document"]
            attached = client.post(
                "/chat/attachments", headers=headers,
                files={"file": ("模板.txt", "模板章节：公司概况，产品介绍。".encode(), "text/plain")},
                data={"name": "样式模板", "chat_session_id": session["id"]},
            )
            assert attached.status_code == 201, attached.text
            attachment_id = attached.json()["document"]["id"]
            for document_id in (company["id"], attachment_id):
                _mark_document_ready(client, document_id)
            with SessionLocal() as db:
                owner = db.get(User, owner_id)
                assert owner is not None
                def preflight(spaces, documents, context=None):
                    return _stream_capability_preflight(
                        principal=owner, model=None, message="星海公司成立时间",
                        space_ids=spaces, document_ids=documents, shared_contexts=[],
                        turn_context_tokens=0, local_session_id=session["id"],
                        writing_context=context,
                    ).knowledge_scope

                scope = preflight([], [attachment_id])
                augmented, retrieval = build_knowledge_context(
                    db, user=owner, message="星海公司成立时间", space_ids=[],
                    document_ids=[attachment_id], chat_session_id=session["id"], scope=scope,
                )
                assert "2022年12月" in augmented
                reference = next(item for item in retrieval["references"] if item["document_id"] == company["id"])
                resolved, statuses, _ = _resolve_chat_citations(
                    db, user=owner, session_id=session["id"],
                    message={"content": f"成立于2022年12月 ^[{reference['id']}]", "references": [reference]},
                    request={"space_ids": [], "document_ids": [attachment_id]}, history={},
                )
                assert [item["document_id"] for item in resolved] == [company["id"]]
                assert "unavailable" not in statuses.values()

                other_space, _ = _create_space(client, "用户明确选择的空空间")
                limited = preflight([other_space["id"]], [attachment_id])
                _, selected_retrieval = build_knowledge_context(
                    db, user=owner, message="星海公司成立时间", space_ids=[other_space["id"]],
                    document_ids=[attachment_id], chat_session_id=session["id"], scope=limited,
                )
                assert company["id"] not in {item["document_id"] for item in selected_retrieval["references"]}
                empty_project = preflight([], [], {"project_id": "空项目"})
                _, empty_retrieval = build_knowledge_context(
                    db, user=owner, message="星海公司成立时间", space_ids=[],
                    document_ids=[], chat_session_id=session["id"], scope=empty_project,
                )
                assert empty_retrieval["references"] == []
            with transaction() as db:
                attachment = db.get(Document, attachment_id)
                assert attachment is not None
                attachment.status = "deleted"
            with pytest.raises(HTTPException) as rejected:
                preflight([], [attachment_id])
            assert rejected.value.status_code == 422
    finally:
        database.reset_database(path)


def test_persist_temporary_chat_attachment_to_knowledge_space(tmp_path: Path) -> None:
    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "persist-temporary.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 86400
    settings.temporary_attachment_cleanup_interval_seconds = 0
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            session = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "入库会话"},
            )
            assert session.status_code == 201, session.text
            session_id = session.json()["id"]
            space, _ = _create_space(client, "方案资料库")

            created = client.post(
                "/chat/attachments",
                headers=headers,
                files={
                    "file": (
                        "实施方案.txt",
                        "师资管理AI系统项目实施方案内容".encode(),
                        "text/plain",
                    )
                },
                data={
                    "name": "师资管理AI系统项目实施方案v2",
                    "chat_session_id": session_id,
                },
            )
            assert created.status_code == 201, created.text
            document = created.json()["document"]
            _mark_document_ready(client, document["id"])

            persisted = client.post(
                f"/documents/{document['id']}:persist-to-space",
                headers=headers,
                json={"space_id": space["id"]},
            )
            assert persisted.status_code == 200, persisted.text
            payload = persisted.json()
            assert payload["space_id"] == space["id"]
            assert payload["is_temporary"] is False
            assert payload["chat_session_id"] is None
            assert payload["expires_at"] is None
            assert payload["current_published_version"]["queryable"] is True

            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents?queryable=true",
                headers=headers,
            )
            assert listed.status_code == 200
            assert any(item["id"] == document["id"] for item in listed.json()["items"])

            attachments = client.get(
                f"/chat/attachments?chat_session_id={session_id}",
                headers=headers,
            )
            assert attachments.status_code == 200
            assert all(
                item["id"] != document["id"] for item in attachments.json()["items"]
            )
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "persist-temporary.sqlite3")


def test_persist_temporary_attachment_keeps_relocated_minio_object(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.documents import DocumentVersion
    from app.services import minio_storage
    from tests.test_document_text_extract import _minimal_xlsx

    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "persist-temporary-minio.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 86400
    settings.temporary_attachment_cleanup_interval_seconds = 0
    objects: dict[str, bytes] = {}
    deleted: list[str] = []
    minio_config = SimpleNamespace(bucket="dgmeta")

    def fake_upload_bytes(
        *,
        content: bytes,
        object_key: str,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> str:
        del content_type
        uri = minio_storage.build_minio_uri(bucket or minio_config.bucket, object_key)
        objects[uri] = content
        return uri

    def fake_download_bytes(
        *, minio_uri: str | None = None, object_key: str | None = None
    ) -> bytes:
        uri = minio_uri or minio_storage.build_minio_uri(
            minio_config.bucket, object_key or ""
        )
        if uri not in objects:
            raise FileNotFoundError(uri)
        return objects[uri]

    def fake_delete_minio_object(uri: str) -> bool:
        deleted.append(uri)
        objects.pop(uri, None)
        return True

    monkeypatch.setattr(
        "app.api.documents.minio_storage.is_minio_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.api.documents.minio_storage.load_minio_settings",
        lambda: minio_config,
    )
    monkeypatch.setattr(
        "app.api.documents.minio_storage.upload_bytes", fake_upload_bytes
    )
    monkeypatch.setattr(
        "app.api.documents.minio_storage.download_bytes", fake_download_bytes
    )
    monkeypatch.setattr(
        "app.api.documents._delete_minio_object", fake_delete_minio_object
    )

    spreadsheet = _minimal_xlsx(
        sheet_name="服务清单",
        rows=[["服务事项", "责任部门"], ["企业登记", "市场监管局"]],
    )
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            session = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "MinIO 入库会话"},
            )
            assert session.status_code == 201, session.text
            space, _ = _create_space(client, "MinIO 方案资料库")

            created = client.post(
                "/chat/attachments",
                headers=headers,
                files={
                    "file": (
                        "服务清单.xlsx",
                        spreadsheet,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                data={
                    "name": "服务清单",
                    "chat_session_id": session.json()["id"],
                },
            )
            assert created.status_code == 201, created.text
            document = created.json()["document"]
            version_id = document["latest_version"]["id"]
            temporary_uri = next(
                uri
                for uri in objects
                if "/temporary_" in uri and f"/{version_id}.xlsx" in uri
            )
            _mark_document_ready(client, document["id"])

            persisted = client.post(
                f"/documents/{document['id']}:persist-to-space",
                headers=headers,
                json={"space_id": space["id"]},
            )
            assert persisted.status_code == 200, persisted.text
            with transaction() as db:
                version = db.get(DocumentVersion, version_id)
                assert version is not None
                target_uri = version.file_path or ""

            assert f"/{space['id']}/{document['id']}/{version_id}.xlsx" in target_uri
            assert objects[target_uri] == spreadsheet
            assert temporary_uri not in objects
            assert temporary_uri in deleted
            assert target_uri not in deleted

            preview = client.get(
                f"/documents/{document['id']}/preview", headers=headers
            )
            assert preview.status_code == 200, preview.text
            assert "企业登记" in (preview.json()["content"] or "")
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "persist-temporary-minio.sqlite3")


def test_deleting_chat_session_cleans_bound_temporary_attachments(
    tmp_path: Path, monkeypatch
):
    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "chat-session-temp-cleanup.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 86400
    settings.temporary_attachment_cleanup_interval_seconds = 0
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            session = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "待删会话"},
            )
            assert session.status_code == 201, session.text
            session_id = session.json()["id"]
            created = client.post(
                "/chat/attachments",
                headers=headers,
                files={"file": ("笔记.txt", "会话附件".encode(), "text/plain")},
                data={"name": "笔记", "chat_session_id": session_id},
            )
            assert created.status_code == 201, created.text
            document = created.json()["document"]
            document_id = document["id"]
            with transaction() as db:
                row = db.get(document_repository.Document, document_id)
                assert row
                file_path = row.versions[0].file_path
            assert file_path
            assert Path(file_path).is_file()

            monkeypatch.setattr(
                "app.api.documents._cleanup_local_document_vectors",
                lambda _document: None,
            )
            assert (
                client.delete(
                    f"/chat/sessions/{session_id}", headers=headers
                ).status_code
                == 204
            )
            assert (
                client.get(f"/chat/sessions/{session_id}", headers=headers).status_code
                == 404
            )
            assert (
                client.get(f"/documents/{document_id}", headers=headers).status_code
                == 404
            )
            assert not Path(file_path).exists()
            with transaction() as db:
                row = db.get(document_repository.Document, document_id)
                assert row
                assert row.status == "deleted"
                assert row.temporary_cleanup_at is not None
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "chat-session-temp-cleanup.sqlite3")


def test_upload_preserves_safe_source_relative_path_and_rejects_traversal(
    tmp_path: Path,
):
    original_path, original_url, original_upload_dir = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
    )
    settings.db_path = tmp_path / "document-source-path.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "目录资料")
            created = client.post(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=admin_headers,
                files={"file": ("指南.txt", "内容".encode(), "text/plain")},
                data={"source_relative_path": "产品文档/指南.txt"},
            )
            assert created.status_code == 201
            document = created.json()["document"]
            assert (
                document["latest_version"]["source_relative_path"]
                == "产品文档/指南.txt"
            )

            rejected = client.post(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=admin_headers,
                files={"file": ("指南.txt", "内容".encode(), "text/plain")},
                data={"source_relative_path": "../指南.txt"},
            )
            assert rejected.status_code == 400
    finally:
        settings.db_path, settings.database_url, settings.upload_dir = (
            original_path,
            original_url,
            original_upload_dir,
        )
        database.reset_database(tmp_path / "document-source-path.sqlite3")


def test_auto_category_uses_path_and_document_category_can_be_updated(
    tmp_path: Path,
):
    original_path, original_url, original_upload_dir = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
    )
    settings.db_path = tmp_path / "document-auto-category.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "自动分类资料")
            category_response = client.post(
                f"/knowledge-spaces/{space['id']}/categories",
                headers=admin_headers,
                json={"name": "资质文件-来酷智联"},
            )
            assert category_response.status_code == 201, category_response.text
            category_id = category_response.json()["id"]

            auto_created = client.post(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=admin_headers,
                files={
                    "file": (
                        "营业执照.pdf",
                        "营业执照正文".encode(),
                        "application/pdf",
                    )
                },
                data={
                    "source_relative_path": "资质文件-来酷智联/营业执照.pdf",
                },
            )
            assert auto_created.status_code == 201, auto_created.text
            document = auto_created.json()["document"]
            assert document["category_id"] == category_id

            cleared = client.patch(
                f"/documents/{document['id']}",
                headers=admin_headers,
                json={"category_id": None},
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["category_id"] is None

            reassigned = client.patch(
                f"/documents/{document['id']}",
                headers=admin_headers,
                json={"category_id": category_id},
            )
            assert reassigned.status_code == 200, reassigned.text
            assert reassigned.json()["category_id"] == category_id
    finally:
        settings.db_path, settings.database_url, settings.upload_dir = (
            original_path,
            original_url,
            original_upload_dir,
        )
        database.reset_database(tmp_path / "document-auto-category.sqlite3")


@pytest.fixture
def rename_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "document-rename.sqlite3")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{settings.db_path}")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "worker_role", "api")
    try:
        with TestClient(app) as client:
            yield client
    finally:
        database.reset_database(tmp_path / "document-rename.sqlite3")


def test_document_rename_preserves_category_versions_and_original(rename_client):
    from email.message import Message

    client = rename_client
    space, headers = _create_space(client, "名称维护")
    category = client.post(
        f"/knowledge-spaces/{space['id']}/categories",
        headers=headers,
        json={"name": "产品文档"},
    ).json()
    content = "原件正文不因改名改变".encode()
    doc = _create_document(client, space["id"], headers, "旧用户手册", content)["document"]
    path = f"/documents/{doc['id']}"
    assert client.patch(path, headers=headers, json={"category_id": category["id"]}).status_code == 200
    before = client.get(path, headers=headers).json()
    with transaction() as db:
        version = document_repository.latest_version(db, doc["id"])
        original_version = {column.name: getattr(version, column.name) for column in version.__table__.columns}

    renamed = client.post(f"{path}:rename", headers=headers, json={"name": "  工具箱-用户手册  "})
    assert renamed.status_code == 200, renamed.text
    after = client.get(path, headers=headers).json()
    assert after["name"] == "工具箱-用户手册"
    assert after["updated_at"] != before["updated_at"]
    for field in ("category_id", "space_id", "created_at", "created_by_user_id", "versions", "current_published_version"):
        assert after[field] == before[field]
    with transaction() as db:
        version = document_repository.latest_version(db, doc["id"])
        assert {column.name: getattr(version, column.name) for column in version.__table__.columns} == original_version
    downloaded = client.get(f"{path}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == content
    attachment = Message()
    attachment["Content-Disposition"] = downloaded.headers["content-disposition"]
    assert attachment.get_filename() == "工具箱-用户手册.txt"
    listing = f"/knowledge-spaces/{space['id']}/documents"
    assert [item["id"] for item in client.get(listing, headers=headers, params={"q": "工具箱"}).json()["items"]] == [doc["id"]]
    assert client.get(listing, headers=headers, params={"q": "旧用户手册"}).json()["items"] == []

    for invalid in ("", " \t\n ", "长" * 256, None):
        rejected = client.post(f"{path}:rename", headers=headers, json={"name": invalid})
        assert rejected.status_code == 422
        assert client.get(path, headers=headers).json()["name"] == "工具箱-用户手册"
    boundary = "名" * 255
    assert client.post(f"{path}:rename", headers=headers, json={"name": boundary}).status_code == 200
    assert client.get(path, headers=headers).json()["name"] == boundary


def test_document_rename_requires_current_writable_membership(rename_client):
    from app.repositories import spaces

    client = rename_client
    space, owner = _create_space(client, "受保护资料")
    contributor = _member_headers(client, space["id"], "rename-contributor", "contributor")
    reader = _member_headers(client, space["id"], "rename-reader", "reader")
    document = _create_document(client, space["id"], owner, "负责人上传的原件")["document"]
    path = f"/documents/{document['id']}"
    assert client.post(f"{path}:rename", headers=reader, json={"name": "越权修改"}).status_code == 404
    assert client.get(path, headers=owner).json()["name"] == document["name"]
    assert client.post(f"{path}:rename", headers=contributor, json={"name": "成员修正名称"}).status_code == 200
    assert client.get(path, headers=reader).json()["name"] == "成员修正名称"

    user_id = client.get("/auth/me", headers=contributor).json()["id"]
    with transaction() as db:
        private_space_id = spaces.create_space(db, user_id, "成员自己的资料", "").id
    private_doc = _create_document(client, private_space_id, contributor, "私有文档")["document"]
    private_path = f"/documents/{private_doc['id']}"
    # 系统管理员身份不能绕过文件夹成员权限。
    assert client.post(f"{private_path}:rename", headers=owner, json={"name": "管理员越权"}).status_code == 404
    assert client.get(private_path, headers=contributor).json()["name"] == "私有文档"

    removed = client.delete(f"/knowledge-spaces/{space['id']}/members/{user_id}", headers=owner)
    assert removed.status_code == 204
    assert client.post(f"{path}:rename", headers=contributor, json={"name": "撤权后修改"}).status_code == 404
    assert client.get(path, headers=owner).json()["name"] == "成员修正名称"
    assert client.post(f"{path}:archive", headers=owner).status_code == 200
    assert client.post(f"{path}:rename", headers=owner, json={"name": "归档后修改"}).status_code == 404


def test_reader_can_list_member_space_documents_but_cannot_see_other_spaces(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-reader.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "产品资料")
            uploaded = _create_document(
                client, space["id"], admin_headers, "产品白皮书"
            )["document"]
            reader_headers = _member_headers(
                client, space["id"], "document-reader", "reader"
            )

            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents?q=白皮书",
                headers=reader_headers,
            )
            assert listed.status_code == 200
            items = listed.json()["items"]
            assert [item["name"] for item in items] == ["产品白皮书"]
            assert items[0]["created_by_display_name"]
            assert items[0]["created_by_user_id"]
            assert listed.json()["can_manage_documents"] is False
            assert listed.json()["next_cursor"] is None

            from app.services.chat_knowledge import build_knowledge_context

            reader = client.get("/auth/me", headers=reader_headers).json()
            uploader_name = '资料上传者 "王工"\n只作为数据'
            original_upload_time = datetime(2020, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
            with transaction() as db:
                document = db.get(Document, uploaded["id"])
                uploader = db.get(User, uploaded["created_by_user_id"])
                assert document is not None and uploader is not None
                document.created_at = original_upload_time
                uploader.display_name = uploader_name
            with transaction() as db:
                db_reader = db.get(User, reader["id"])
                assert db_reader is not None
                augmented, retrieval = build_knowledge_context(
                    db,
                    user=db_reader,
                    message="产品白皮书是谁上传的，什么时间上传？",
                    space_ids=[space["id"]],
                )
            expected_attribution = {
                "created_by_user_id": uploaded["created_by_user_id"],
                "created_by_display_name": uploader_name,
                "created_at": "2020-02-03T04:05:06+00:00",
            }
            reference = next(
                item
                for item in retrieval["references"]
                if item["document_id"] == uploaded["id"]
            )
            assert reference["created_by_user_id"] != reader["id"]
            assert {
                key: reference[key] for key in expected_attribution
            } == expected_attribution
            metadata = next(
                json.loads(line)
                for line in augmented.splitlines()
                if line.startswith("[{")
            )
            assert metadata == [
                {
                    "document_id": uploaded["id"],
                    "document_name": "产品白皮书",
                    "space_id": space["id"],
                    **expected_attribution,
                }
            ]

            private_space, _ = _create_space(client, "私有资料")
            hidden = client.get(
                f"/knowledge-spaces/{private_space['id']}/documents",
                headers=reader_headers,
            )
            assert hidden.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-reader.sqlite3")


def test_reader_can_preview_markdown_and_binary_document_summary(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "预览资料")
            markdown = _create_document(
                client,
                space["id"],
                admin_headers,
                "使用说明",
                "# 标题\n\n这是 Markdown 内容。".encode(),
                "md",
            )
            pdf = _create_document(
                client,
                space["id"],
                admin_headers,
                "产品手册",
                b"%PDF-1.4",
                "pdf",
            )
            reader_headers = _member_headers(
                client, space["id"], "document-preview-reader", "reader"
            )

            markdown_preview = client.get(
                f"/documents/{markdown['document']['id']}/preview",
                headers=reader_headers,
            )
            assert markdown_preview.status_code == 200
            assert markdown_preview.json()["kind"] == "markdown"
            assert markdown_preview.json()["content"] == "# 标题\n\n这是 Markdown 内容。"
            assert markdown_preview.json()["version_id"] == markdown["document"]["latest_version"]["id"]

            pdf_preview = client.get(
                f"/documents/{pdf['document']['id']}/preview", headers=reader_headers
            )
            assert pdf_preview.status_code == 200
            assert pdf_preview.json()["kind"] == "pdf"
            pdf_file = client.get(
                f"/documents/{pdf['document']['id']}/preview/file",
                headers=reader_headers,
            )
            assert pdf_file.status_code == 200
            assert pdf_file.content == b"%PDF-1.4"
            assert pdf_file.headers["content-type"] == "application/pdf"
            assert pdf_file.headers["content-disposition"] == "inline"

            private_space, _ = _create_space(client, "无权预览")
            private = _create_document(
                client, private_space["id"], _admin_headers(client), "私有文档"
            )
            denied = client.get(
                f"/documents/{private['document']['id']}/preview",
                headers=reader_headers,
            )
            assert denied.status_code == 404
            denied_pdf_file = client.get(
                f"/documents/{private['document']['id']}/preview/file",
                headers=reader_headers,
            )
            assert denied_pdf_file.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-preview.sqlite3")


def test_office_docx_preview_renders_pdf_when_converter_available(
    tmp_path: Path, monkeypatch
):
    """Word preview returns kind=pdf and serves converted bytes once cached."""
    from tests.test_document_text_extract import _minimal_docx

    from app.services import doc_preview as doc_preview_service

    original_path, original_url, original_upload = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
    )
    settings.db_path = tmp_path / "office-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    fake_pdf = b"%PDF-1.4\nconverted-office"
    monkeypatch.setattr(
        doc_preview_service, "office_preview_cached", lambda content: False
    )
    monkeypatch.setattr(
        doc_preview_service,
        "convert_office_to_pdf",
        lambda content, filename: fake_pdf,
    )
    monkeypatch.setattr(
        "app.api.documents.office_preview_cached", lambda content: False
    )
    monkeypatch.setattr(
        "app.api.documents.render_office_preview_pdf",
        lambda content, filename: fake_pdf,
    )
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "Office 预览")
            docx = _create_document(
                client,
                space["id"],
                admin_headers,
                "带图方案",
                _minimal_docx(["第一章 带图正文内容", "图片与排版需保留"]),
                "docx",
            )
            reader_headers = _member_headers(
                client, space["id"], "office-preview-reader", "reader"
            )

            preview = client.get(
                f"/documents/{docx['document']['id']}/preview",
                headers=reader_headers,
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["kind"] == "pdf"

            pdf_file = client.get(
                f"/documents/{docx['document']['id']}/preview/file",
                headers=reader_headers,
            )
            assert pdf_file.status_code == 200, pdf_file.text
            assert pdf_file.content == fake_pdf
            assert pdf_file.headers["content-type"] == "application/pdf"
            assert pdf_file.headers["content-disposition"] == "inline"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.upload_dir = original_upload
        database.reset_database(tmp_path / "office-preview.sqlite3")


def test_office_docx_preview_falls_back_to_text_when_converter_missing(
    tmp_path: Path, monkeypatch
):
    """Without LibreOffice, preview degrades to plain text, never markdown."""
    from tests.test_document_text_extract import _minimal_docx

    from app.services import doc_preview as doc_preview_service

    original_path, original_url, original_upload = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
    )
    settings.db_path = tmp_path / "office-fallback.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        doc_preview_service, "office_preview_cached", lambda content: False
    )
    monkeypatch.setattr(
        doc_preview_service,
        "convert_office_to_pdf",
        lambda content, filename: (_ for _ in ()).throw(
            doc_preview_service.LibreOfficeUnavailable(
                "文档渲染组件不可用，请下载原文件查看"
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.documents.office_preview_cached", lambda content: False
    )
    monkeypatch.setattr(
        "app.api.documents.render_office_preview_pdf",
        lambda content, filename: (_ for _ in ()).throw(
            doc_preview_service.LibreOfficeUnavailable(
                "文档渲染组件不可用，请下载原文件查看"
            )
        ),
    )
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "Office 降级")
            docx = _create_document(
                client,
                space["id"],
                admin_headers,
                "纯文字稿",
                _minimal_docx(["第一段内容", "第二段内容"]),
                "docx",
            )
            reader_headers = _member_headers(
                client, space["id"], "office-fallback-reader", "reader"
            )

            preview = client.get(
                f"/documents/{docx['document']['id']}/preview",
                headers=reader_headers,
            )
            assert preview.status_code == 200, preview.text
            body = preview.json()
            assert body["kind"] == "text"
            assert "第一段内容" in (body["content"] or "")
            assert "第二段内容" in (body["content"] or "")
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.upload_dir = original_upload
        database.reset_database(tmp_path / "office-fallback.sqlite3")


def test_office_conversion_runs_in_isolated_profile_and_tempdir(
    tmp_path: Path, monkeypatch
):
    """Each conversion uses a private LibreOffice profile and private temp dir.

    Concurrent previews must not share the default LibreOffice user profile
    (which causes lock contention) and must never write next to the original.
    """
    from app.services import doc_preview as doc_preview_service

    invoked: list[list[str]] = []
    fake_pdf = b"%PDF-1.4 converted"

    class FakeCompleted:
        returncode = 0
        stdout = "convert ok"
        stderr = ""

    def fake_which(name: str) -> str:
        return "/usr/bin/soffice"

    def fake_run(command: list[str], **kwargs) -> FakeCompleted:
        invoked.append(command)
        out_dir = Path(command[command.index("--outdir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "source.pdf").write_bytes(fake_pdf)
        return FakeCompleted()

    monkeypatch.setattr(doc_preview_service.shutil, "which", fake_which)
    monkeypatch.setattr(doc_preview_service.subprocess, "run", fake_run)

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )

    pdf = doc_preview_service.convert_office_to_pdf(buf.getvalue(), "sample.docx")
    assert pdf == fake_pdf
    assert len(invoked) == 1
    command = invoked[0]
    profile_args = [
        arg for arg in command if arg.startswith("-env:UserInstallation=file://")
    ]
    # Writer exports must ask for "page only" (InitialView=0): LibreOffice
    # otherwise writes /PageMode /UseOutlines into the PDF catalog, and viewers
    # that honour it (pdf.js) then open the document outline over every preview.
    # The value is pinned, not just the key: any other InitialView
    # (1=outline, 2=thumbnails) restores the bug this guards.
    filter_arg = command[command.index("--convert-to") + 1]
    assert filter_arg.startswith("pdf:writer_pdf_Export:")
    assert '"InitialView":{"type":"long","value":"0"}' in filter_arg
    assert len(profile_args) == 1
    profile_path = Path(profile_args[0].removeprefix("-env:UserInstallation=file://"))
    # The profile lives inside a throwaway temp dir, never the repo or uploads,
    # and the whole directory (source, profile, output) is removed afterwards.
    assert "doc-nexus-office-" in str(profile_path)
    assert not profile_path.exists()
    # Output dir is under the same private temp dir, distinct from any source.
    out_dir = Path(command[command.index("--outdir") + 1])
    assert "doc-nexus-office-" in str(out_dir)
    assert out_dir != profile_path
    assert not out_dir.exists()


def test_office_cache_write_is_atomic_and_validates_pdf(tmp_path, monkeypatch):
    """Cache hits require a real PDF; writes go through temp file + replace."""
    from app.services import doc_preview as doc_preview_service

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True)

    content = b"some docx bytes"
    # A non-PDF/partial cache file must NOT be treated as a usable hit.
    digest = hashlib.sha256(content).hexdigest()
    cached = doc_preview_service._cached_pdf_path(digest)
    cached.write_bytes(b"partial-not-a-pdf")
    assert doc_preview_service.office_preview_cached(content) is False
    # A PDF written by an older pipeline (unversioned name) is not reused.
    legacy = doc_preview_service._cache_root() / f"{digest}.pdf"
    legacy.write_bytes(b"%PDF-1.4 from the previous renderer")
    assert doc_preview_service.office_preview_cached(content) is False

    monkeypatch.setattr(
        doc_preview_service,
        "convert_office_to_pdf",
        lambda content, filename: b"%PDF-1.4 complete",
    )
    pdf = doc_preview_service.render_office_preview_pdf(content, "x.docx")
    assert pdf == b"%PDF-1.4 complete"
    # Written via atomic replace: target now complete and cache hit works.
    assert cached.read_bytes() == b"%PDF-1.4 complete"
    assert doc_preview_service.office_preview_cached(content) is True
    # No stray temp files remain next to the target.
    leftovers = [
        p.name for p in cached.parent.iterdir() if p.name.startswith(".office-cache-")
    ]
    assert leftovers == []


def test_office_cache_survives_concurrent_writers(tmp_path, monkeypatch):
    """Concurrent renders of identical content never expose a partial PDF."""
    import concurrent.futures

    from app.services import doc_preview as doc_preview_service

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True)
    content = b"shared docx bytes"
    digest = hashlib.sha256(content).hexdigest()
    cached = doc_preview_service._cached_pdf_path(digest)

    def convert(content, filename):
        # Slow conversion lets multiple writers overlap on the same key.
        time.sleep(0.02)
        return b"%PDF-1.4 concurrent " + content

    monkeypatch.setattr(doc_preview_service, "convert_office_to_pdf", convert)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: doc_preview_service.render_office_preview_pdf(
                    content, "x.docx"
                ),
                range(6),
            )
        )
    expected = b"%PDF-1.4 concurrent " + content
    assert all(result == expected for result in results)
    # Cache holds one complete artifact, never a torn write.
    assert cached.read_bytes() == expected
    leftovers = [
        p.name for p in cached.parent.iterdir() if p.name.startswith(".office-cache-")
    ]
    assert leftovers == []


def _docx_with_sdt(*, body_xml: str | None = None) -> bytes:
    """Build a DOCX package whose body contains structured-document tags."""
    import io
    import zipfile

    default_body = (
        "<w:p><w:r><w:t>前置段落</w:t></w:r></w:p>"
        "<w:sdt>"
        "<w:sdtContent>"
        "<w:p><w:r><w:t>目录条目一</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>目录条目二</w:t></w:r></w:p>"
        "</w:sdtContent>"
        "</w:sdt>"
        "<w:p><w:r><w:t>正文段落</w:t></w:r></w:p>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + (body_xml if body_xml is not None else default_body)
        + "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_strip_body_sdt_unwraps_toc_wrapper_and_keeps_text():
    """LibreOffice-incompatible TOC sdt wrappers are unwrapped, text preserved."""
    from app.services import doc_preview as doc_preview_service

    payload = _docx_with_sdt()
    stripped = doc_preview_service._strip_body_sdt(payload)
    assert stripped is not None

    import io
    import zipfile

    with (
        zipfile.ZipFile(io.BytesIO(payload)) as source,
        zipfile.ZipFile(io.BytesIO(stripped)) as archive,
    ):
        document = archive.read("word/document.xml").decode("utf-8")
        assert "<w:sdt>" not in document
        assert "目录条目一" in document
        assert "目录条目二" in document
        assert "正文段落" in document
        assert sorted(archive.namelist()) == sorted(source.namelist())
        for name in source.namelist():
            if name != "word/document.xml":
                assert archive.read(name) == source.read(name)


def test_strip_body_sdt_unwraps_attribute_and_nested_controls():
    """Attribute-bearing and newly exposed nested sdt wrappers are all removed."""
    from app.services import doc_preview as doc_preview_service

    body_xml = (
        '<w:sdt w:rsidR="00112233"><w:sdtContent>'
        "<w:p><w:r><w:t>外层前段</w:t></w:r></w:p>"
        '<w:sdt w:tag="nested"><w:sdtContent>'
        "<w:p><w:r><w:t>内层段落</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
        "<w:p><w:r><w:t>外层后段</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
        "<w:sdt><w:sdtContent>"
        "<w:p><w:r><w:t>后续无属性段落</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
    )
    stripped = doc_preview_service._strip_body_sdt(_docx_with_sdt(body_xml=body_xml))
    assert stripped is not None

    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(stripped)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "<w:sdt" not in document
    paragraphs = ("外层前段", "内层段落", "外层后段", "后续无属性段落")
    assert all(paragraph in document for paragraph in paragraphs)
    assert [document.index(paragraph) for paragraph in paragraphs] == sorted(
        document.index(paragraph) for paragraph in paragraphs
    )


def test_convert_retries_once_after_unwrapping_sdt(monkeypatch):
    """convert_office_to_pdf falls back to the unwrapped docx on first failure."""
    from app.services import doc_preview as doc_preview_service

    payload = _docx_with_sdt()
    fake_pdf = b"%PDF-1.4 unwrapped"
    calls: list[bytes] = []

    def fake_convert_bytes(content: bytes, suffix: str, **_kwargs):
        calls.append(content)
        if content == payload:
            return None  # original fails (LibreOffice cannot load it)
        return fake_pdf

    monkeypatch.setattr(doc_preview_service, "_convert_bytes", fake_convert_bytes)
    monkeypatch.setattr(
        doc_preview_service, "_soffice_command", lambda: "/usr/bin/soffice"
    )
    pdf = doc_preview_service.convert_office_to_pdf(payload, "toc.docx")
    assert pdf == fake_pdf
    assert len(calls) == 2
    # Retried bytes differ from the original (sdt unwrapped).
    assert calls[1] != calls[0]


def test_convert_raises_when_sdt_unwrap_also_fails(monkeypatch):
    """A genuinely broken docx still raises DocumentConversionError."""
    from app.services import doc_preview as doc_preview_service

    payload = _docx_with_sdt()
    monkeypatch.setattr(
        doc_preview_service, "_convert_bytes", lambda c, s, **_kwargs: None
    )
    monkeypatch.setattr(
        doc_preview_service, "_soffice_command", lambda: "/usr/bin/soffice"
    )
    with pytest.raises(doc_preview_service.DocumentConversionError):
        doc_preview_service.convert_office_to_pdf(payload, "broken.docx")


def test_convert_repairs_gridless_tables_and_skips_merged_ones(monkeypatch):
    """A table without w:tblGrid renders as one empty column in LibreOffice."""
    import io
    import zipfile

    from app.services import doc_preview as doc_preview_service

    cell = '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc>'
    body = (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
        "<w:tr>" + cell.format("编号") + cell.format("说明") + "</w:tr>"
        "<w:tr>" + cell.format("[1]") + cell.format("实施四阶段说明文本") + "</w:tr>"
        "</w:tbl>"
    )
    # Same package builder as the sdt fixture, with a generic body.
    payload = _docx_with_sdt(body_xml=body)
    calls: list[bytes] = []

    def fake_convert_bytes(content: bytes, suffix: str, **_kwargs):
        calls.append(content)
        return b"%PDF-1.4 repaired"

    monkeypatch.setattr(doc_preview_service, "_convert_bytes", fake_convert_bytes)
    monkeypatch.setattr(
        doc_preview_service, "_soffice_command", lambda: "/usr/bin/soffice"
    )

    pdf = doc_preview_service.convert_office_to_pdf(payload, "sources.docx")
    assert pdf == b"%PDF-1.4 repaired"
    assert len(calls) == 1
    assert calls[0] != payload
    with zipfile.ZipFile(io.BytesIO(calls[0])) as archive:
        repaired = archive.read("word/document.xml").decode()
    assert repaired.count("<w:gridCol") == 2
    # Every cell carries an explicit dxa width matching its grid column.
    assert repaired.count('w:type="dxa"') == 4

    # Merged cells are left alone: a computed grid could contradict the spans.
    merged = _docx_with_sdt(
        body_xml=body.replace(
            '<w:tcPr><w:tcW w:w="0" w:type="auto"/>',
            '<w:tcPr><w:tcW w:w="0" w:type="auto"/><w:gridSpan w:val="2"/>',
            1,
        )
    )
    assert doc_preview_service._ensure_table_grids(merged) is None

    # A table that already declares its grid keeps its bytes.
    gridded = _docx_with_sdt(
        body_xml=body.replace(
            "</w:tblPr>",
            '</w:tblPr><w:tblGrid><w:gridCol w:w="4513"/><w:gridCol w:w="4513"/></w:tblGrid>',
            1,
        )
    )
    assert doc_preview_service._ensure_table_grids(gridded) is None

    # A header-only multi-column table is repaired as well.
    header_only = _docx_with_sdt(
        body_xml=(
            '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
            "<w:tr>" + cell.format("编号") + cell.format("说明") + "</w:tr>"
            "</w:tbl>"
        )
    )
    repaired_header = doc_preview_service._ensure_table_grids(header_only)
    assert repaired_header is not None
    with zipfile.ZipFile(io.BytesIO(repaired_header)) as archive:
        assert archive.read("word/document.xml").decode().count("<w:gridCol") == 2

    # Untrusted XML that declares entities is refused instead of expanded.
    doctype = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<!DOCTYPE w:document [<!ENTITY boom "expanded">]>'
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        document = archive.read("word/document.xml").decode()
        parts = [(info, archive.read(info.filename)) for info in archive.infolist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in parts:
            archive.writestr(
                info,
                (doctype + document[document.index("<w:document") :]).encode()
                if info.filename == "word/document.xml"
                else data,
            )
    assert doc_preview_service._ensure_table_grids(buffer.getvalue()) is None

    # A package declaring an outsized uncompressed size is not expanded.
    monkeypatch.setattr(doc_preview_service, "_MAX_REPAIR_PACKAGE_BYTES", 1)
    assert doc_preview_service._ensure_table_grids(payload) is None


def test_reader_can_preview_chinese_spreadsheet_instead_of_mojibake(tmp_path: Path):
    from app.models.documents import DocumentVersion
    from app.services.document_processor import extracted_sidecar_path
    from tests.test_document_text_extract import _minimal_xlsx

    original_path, original_url, original_upload = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
    )
    settings.db_path = tmp_path / "spreadsheet-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "表格资料")
            xlsx = _create_document(
                client,
                space["id"],
                admin_headers,
                "人员名单",
                _minimal_xlsx(
                    sheet_name="人员", rows=[["姓名", "部门"], ["张三", "研发"]]
                ),
                "xlsx",
            )
            html_xls = (
                "<html><head><meta http-equiv='Content-Type' "
                "content='text/html; charset=gb2312'></head><body><table>"
                "<tr><td>姓名</td><td>部门</td></tr>"
                "<tr><td>李四</td><td>财务</td></tr></table></body></html>"
            ).encode("gb2312")
            xls = _create_document(
                client,
                space["id"],
                admin_headers,
                "导出表",
                html_xls,
                "xls",
            )
            reader_headers = _member_headers(
                client, space["id"], "spreadsheet-preview-reader", "reader"
            )

            xlsx_preview = client.get(
                f"/documents/{xlsx['document']['id']}/preview", headers=reader_headers
            )
            assert xlsx_preview.status_code == 200
            xlsx_body = xlsx_preview.json()
            assert xlsx_body["kind"] == "markdown"
            assert "张三" in xlsx_body["content"]
            assert "研发" in xlsx_body["content"]
            assert "\ufffd" not in xlsx_body["content"]

            with transaction() as db:
                version = db.get(
                    DocumentVersion, xlsx["document"]["latest_version"]["id"]
                )
                sidecar = extracted_sidecar_path(version.file_path if version else None)
                assert sidecar is not None
                sidecar.write_text("PK" + "\ufffd" * 40, encoding="utf-8")

            xlsx_preview_again = client.get(
                f"/documents/{xlsx['document']['id']}/preview", headers=reader_headers
            )
            assert "张三" in xlsx_preview_again.json()["content"]
            assert "\ufffd" not in xlsx_preview_again.json()["content"]

            xls_preview = client.get(
                f"/documents/{xls['document']['id']}/preview", headers=reader_headers
            )
            assert xls_preview.status_code == 200
            xls_body = xls_preview.json()
            assert xls_body["kind"] == "markdown"
            assert "李四" in xls_body["content"]
            assert "财务" in xls_body["content"]
            assert "\ufffd" not in xls_body["content"]
    finally:
        settings.db_path, settings.database_url, settings.upload_dir = (
            original_path,
            original_url,
            original_upload,
        )
        database.reset_database(tmp_path / "spreadsheet-preview.sqlite3")


def test_contributor_can_upload_and_archive_but_reader_cannot_manage(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-contributor.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "协作资料")
            contributor_headers = _member_headers(
                client, space["id"], "document-contributor", "contributor"
            )
            reader_headers = _member_headers(
                client, space["id"], "document-manage-reader", "reader"
            )

            created = _create_document(
                client, space["id"], contributor_headers, "协作规范"
            )
            assert created["document"]["latest_version"]["processing_stage"] in (
                "queued",
                "scanning",
                "processing",
                "failed",
                "ready",
            )
            assert created["task"]["status"] in (
                "queued",
                "scanning",
                "processing",
                "failed",
                "completed",
            )
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/documents",
                    headers=reader_headers,
                    files={"file": ("should-fail.txt", b"blocked", "text/plain")},
                    data={"name": "不应上传"},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/documents/{created['document']['id']}:archive",
                    headers=reader_headers,
                ).status_code
                == 404
            )
            archived = client.post(
                f"/documents/{created['document']['id']}:archive",
                headers=contributor_headers,
            )
            assert archived.status_code == 200
            assert archived.json()["status"] == "archived"

            other = _create_document(
                client, space["id"], contributor_headers, "待删除文档"
            )
            assert (
                client.delete(
                    f"/documents/{other['document']['id']}", headers=reader_headers
                ).status_code
                == 404
            )
            deleted = client.delete(
                f"/documents/{other['document']['id']}", headers=contributor_headers
            )
            assert deleted.status_code == 200
            assert deleted.json()["status"] == "deleted"
            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=contributor_headers,
            )
            assert listed.status_code == 200
            assert listed.json()["can_manage_documents"] is True
            assert all(
                item["id"] != other["document"]["id"] for item in listed.json()["items"]
            )

    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-contributor.sqlite3")


def test_deleted_document_blocks_access(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-delete-lifecycle.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, headers = _create_space(client, "删除闭环")
            created = _create_document(client, space["id"], headers, "待清理文档")
            document = created["document"]
            document_id = document["id"]
            version_id = document["latest_version"]["id"]
            job_id = created["task"]["id"]

            deleted = client.delete(f"/documents/{document_id}", headers=headers)

            assert deleted.status_code == 200
            assert deleted.json()["status"] == "deleted"

            assert (
                client.get(f"/documents/{document_id}", headers=headers).status_code
                == 404
            )
            assert (
                client.get(
                    f"/documents/{document_id}/preview", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/documents/{document_id}/preview/file", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/documents/{document_id}/download", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/documents/{document_id}/versions/{version_id}:retry",
                    headers=headers,
                ).status_code
                == 404
            )
            assert client.get(f"/jobs/{job_id}", headers=headers).status_code == 404
            with transaction() as db:
                job = db.get(document_repository.DocumentJob, job_id)
                assert job is not None
                assert job.status in document_repository.TERMINAL_JOB_STATUSES
            workspace = client.get("/workspace", headers=headers)
            assert workspace.status_code == 200
            assert job_id not in {
                item["job_id"] for item in workspace.json()["running_tasks"]
            }
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-delete-lifecycle.sqlite3")


def test_batch_delete_removes_documents(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-batch-delete.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, headers = _create_space(client, "批量删除")
            first = _create_document(client, space["id"], headers, "批量一")
            second = _create_document(client, space["id"], headers, "批量二")
            document_ids = [first["document"]["id"], second["document"]["id"]]

            deleted = client.request(
                "DELETE",
                f"/knowledge-spaces/{space['id']}/documents/batch",
                headers=headers,
                json={"document_ids": document_ids},
            )

            assert deleted.status_code == 200
            assert deleted.json() == {"deleted": 2}
            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents", headers=headers
            )
            assert listed.status_code == 200
            assert listed.json()["items"] == []
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-batch-delete.sqlite3")


def test_purge_stored_files_removes_local_sidecar_and_minio_ref(
    tmp_path: Path, monkeypatch
):
    from app.api.documents import _purge_stored_files
    from app.services.document_processor import extracted_sidecar_path

    local = tmp_path / "document.txt"
    local.write_text("source", encoding="utf-8")
    sidecar = extracted_sidecar_path(str(local))
    assert sidecar is not None
    sidecar.write_text("extracted", encoding="utf-8")
    minio_deleted: list[str] = []
    monkeypatch.setattr(
        "app.api.documents._delete_minio_object",
        lambda file_path: minio_deleted.append(file_path),
    )

    _purge_stored_files([str(local), "minio://dgmeta/doc-nexus/doc/ver.txt"])

    assert not local.exists()
    assert not sidecar.exists()
    assert minio_deleted == ["minio://dgmeta/doc-nexus/doc/ver.txt"]


def test_expired_temporary_document_is_cleaned_and_marked(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    from app.api.documents import cleanup_expired_temporary_documents
    from app.core.security import now as utc_now

    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "temporary-document-cleanup.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 3600
    settings.temporary_attachment_cleanup_interval_seconds = 0
    try:
        with TestClient(app) as client:
            space, headers = _create_space(client, "临时附件")
            created = client.post(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=headers,
                files={"file": ("附件.txt", "临时内容".encode(), "text/plain")},
                data={"temporary": "true"},
            )
            assert created.status_code == 201
            document = created.json()["document"]
            assert document["is_temporary"] is True
            assert document["expires_at"]

            with transaction() as db:
                row = db.get(document_repository.Document, document["id"])
                assert row
                row.expires_at = utc_now() - timedelta(minutes=1)
                file_path = row.versions[0].file_path
            assert file_path
            assert Path(file_path).is_file()

            monkeypatch.setattr(
                "app.api.documents._cleanup_local_document_vectors",
                lambda _document: None,
            )
            stats = cleanup_expired_temporary_documents(now_at=utc_now())

            assert stats == {"scanned": 1, "cleaned": 1, "failed": 0, "skipped": 0}
            assert not Path(file_path).exists()
            with transaction() as db:
                row = db.get(document_repository.Document, document["id"])
                assert row
                assert row.status == "deleted"
                assert row.temporary_cleanup_at is not None
            assert (
                client.get(f"/documents/{document['id']}", headers=headers).status_code
                == 404
            )
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "temporary-document-cleanup.sqlite3")


def test_failed_temporary_cleanup_remains_retryable(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    from app.api.documents import cleanup_expired_temporary_documents
    from app.core.security import now as utc_now

    original = (
        settings.db_path,
        settings.database_url,
        settings.upload_dir,
        settings.temporary_attachment_ttl_seconds,
        settings.temporary_attachment_cleanup_interval_seconds,
    )
    settings.db_path = tmp_path / "temporary-document-retry.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.temporary_attachment_ttl_seconds = 3600
    settings.temporary_attachment_cleanup_interval_seconds = 0
    try:
        with TestClient(app) as client:
            space, headers = _create_space(client, "临时附件重试")
            created = client.post(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=headers,
                files={"file": ("重试.txt", "临时内容".encode(), "text/plain")},
                data={"temporary": "true"},
            )
            assert created.status_code == 201
            document_id = created.json()["document"]["id"]
            with transaction() as db:
                row = db.get(document_repository.Document, document_id)
                assert row
                row.expires_at = utc_now() - timedelta(minutes=1)
                file_path = row.versions[0].file_path
            assert file_path

            monkeypatch.setattr(
                "app.api.documents._cleanup_local_document_vectors",
                lambda _document: (_ for _ in ()).throw(
                    RuntimeError("向量存储暂不可用")
                ),
            )
            failed = cleanup_expired_temporary_documents(now_at=utc_now())
            assert failed == {"scanned": 1, "cleaned": 0, "failed": 1, "skipped": 0}
            with transaction() as db:
                row = db.get(document_repository.Document, document_id)
                assert row
                assert row.status == "deleted"
                assert row.temporary_cleanup_at is None
            assert Path(file_path).is_file()

            monkeypatch.setattr(
                "app.api.documents._cleanup_local_document_vectors",
                lambda _document: None,
            )
            retried = cleanup_expired_temporary_documents(now_at=utc_now())
            assert retried == {"scanned": 1, "cleaned": 1, "failed": 0, "skipped": 0}
            assert not Path(file_path).exists()
    finally:
        (
            settings.db_path,
            settings.database_url,
            settings.upload_dir,
            settings.temporary_attachment_ttl_seconds,
            settings.temporary_attachment_cleanup_interval_seconds,
        ) = original
        database.reset_database(tmp_path / "temporary-document-retry.sqlite3")


def test_platform_admin_without_space_membership_cannot_mutate_documents(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-platform-admin.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, owner_headers = _create_space(client, "受限资料")
            document = _create_document(client, space["id"], owner_headers, "内部规范")
            platform_admin = _accept_and_login(
                client, "nonmember-platform-admin", "Platform-admin-123"
            )
            with transaction() as db:
                user = db.get(User, platform_admin["user"]["id"])
                assert user
                user.role = "admin"
            platform_admin_headers = {
                "Authorization": f"Bearer {platform_admin['access_token']}"
            }

            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents",
                headers=platform_admin_headers,
            )
            assert listed.status_code == 200
            assert listed.json()["can_manage_documents"] is False
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/documents",
                    headers=platform_admin_headers,
                    files={"file": ("blocked.txt", b"blocked", "text/plain")},
                    data={"name": "不应上传"},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/documents/{document['document']['id']}:archive",
                    headers=platform_admin_headers,
                ).status_code
                == 404
            )
            assert (
                client.delete(
                    f"/documents/{document['document']['id']}",
                    headers=platform_admin_headers,
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/documents/{document['document']['id']}/versions/{document['document']['latest_version']['id']}:retry",
                    headers=platform_admin_headers,
                ).status_code
                == 404
            )
            assert (
                client.request(
                    "DELETE",
                    f"/knowledge-spaces/{space['id']}/documents/batch",
                    headers=platform_admin_headers,
                    json={"document_ids": [document["document"]["id"]]},
                ).status_code
                == 404
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-platform-admin.sqlite3")


def test_ready_only_queryability_and_retry_preserves_previous_published_version(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-versions.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "版本资料")
            created = _create_document(client, space["id"], admin_headers, "版本化文档")
            document = created["document"]
            first_version_id = document["latest_version"]["id"]
            assert document["latest_version"]["queryable"] is False

            failed_version_id = "ver_failed_later"
            recorded_at = datetime.now(timezone.utc).isoformat()
            with transaction() as db:
                db.execute(
                    text(
                        "UPDATE document_versions SET processing_stage = 'ready', queryable = 1, "
                        "retryable = 0, is_current_published = 1 WHERE id = :id"
                    ),
                    {"id": first_version_id},
                )
                db.execute(
                    text(
                        "INSERT INTO document_versions "
                        "(id, document_id, version_number, processing_stage, queryable, retryable, "
                        "failure_code, failure_message, is_current_published, created_at, updated_at) "
                        "VALUES (:id, :document_id, 2, 'failed', 0, 1, 'parse_failed', "
                        "'Parser traceback: object-key/minio/private/source.docx', 0, :created_at, :updated_at)"
                    ),
                    {
                        "id": failed_version_id,
                        "document_id": document["id"],
                        "created_at": recorded_at,
                        "updated_at": recorded_at,
                    },
                )

            detail = client.get(f"/documents/{document['id']}", headers=admin_headers)
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["current_published_version"]["id"] == first_version_id
            assert payload["current_published_version"]["queryable"] is True
            failed = next(
                version
                for version in payload["versions"]
                if version["id"] == failed_version_id
            )
            assert failed["processing_stage"] == "failed"
            assert failed["queryable"] is False
            assert failed["retryable"] is True
            assert failed["failure_message"] == "文档解析失败，请重试。"
            assert "object-key" not in (failed["failure_message"] or "")
            assert "traceback" not in (failed["failure_message"] or "").lower()

            # No file_path on failed version → retry creates a failed job (file_missing)
            retried = client.post(
                f"/documents/{document['id']}/versions/{failed_version_id}:retry",
                headers=admin_headers,
            )
            assert retried.status_code == 200
            assert retried.json()["id"] != failed_version_id
            assert retried.json()["status"] == "failed"
            assert retried.json()["job_id"] == retried.json()["id"]
            assert retried.json()["retryable"] is True

            preserved = client.get(
                f"/documents/{document['id']}", headers=admin_headers
            ).json()
            assert preserved["current_published_version"]["id"] == first_version_id
            original_failed = next(
                version
                for version in preserved["versions"]
                if version["id"] == failed_version_id
            )
            assert original_failed["processing_stage"] == "failed"
            latest = preserved["latest_version"]
            assert latest["processing_stage"] == "failed"
            assert latest["failure_code"] == "file_missing"
            assert (
                client.post(
                    f"/documents/{document['id']}/versions/{failed_version_id}:retry",
                    headers=admin_headers,
                ).status_code
                == 409
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-versions.sqlite3")


def test_queryable_document_list_includes_published_docs_when_latest_version_failed(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-queryable-list.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "可检索列表")
            ready = _create_document(client, space["id"], admin_headers, "仍可检索")
            failed_latest = _create_document(
                client, space["id"], admin_headers, "最新版失败但仍可检索"
            )
            recorded_at = datetime.now(timezone.utc).isoformat()
            with transaction() as db:
                for document_id in (
                    ready["document"]["id"],
                    failed_latest["document"]["id"],
                ):
                    db.execute(
                        text(
                            "UPDATE document_versions SET processing_stage = 'ready', queryable = 1, "
                            "retryable = 0, is_current_published = 1 WHERE document_id = :id"
                        ),
                        {"id": document_id},
                    )
                db.execute(
                    text(
                        "INSERT INTO document_versions "
                        "(id, document_id, version_number, processing_stage, queryable, retryable, "
                        "failure_code, failure_message, is_current_published, created_at, updated_at) "
                        "VALUES ('ver_failed_latest', :document_id, 2, 'failed', 0, 1, 'parse_failed', "
                        "'解析失败', 0, :created_at, :updated_at)"
                    ),
                    {
                        "document_id": failed_latest["document"]["id"],
                        "created_at": recorded_at,
                        "updated_at": recorded_at,
                    },
                )

            by_latest_ready = client.get(
                f"/knowledge-spaces/{space['id']}/documents?status=ready",
                headers=admin_headers,
            )
            assert by_latest_ready.status_code == 200
            assert [item["name"] for item in by_latest_ready.json()["items"]] == [
                "仍可检索"
            ]

            by_queryable = client.get(
                f"/knowledge-spaces/{space['id']}/documents?queryable=true",
                headers=admin_headers,
            )
            assert by_queryable.status_code == 200
            assert sorted(item["name"] for item in by_queryable.json()["items"]) == [
                "仍可检索",
                "最新版失败但仍可检索",
            ]
            listed = next(
                item
                for item in by_queryable.json()["items"]
                if item["name"] == "最新版失败但仍可检索"
            )
            assert listed["latest_version"]["processing_stage"] == "failed"
            assert listed["current_published_version"]["queryable"] is True
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-queryable-list.sqlite3")


def test_document_download_returns_original_bytes(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    original_upload_dir = settings.upload_dir
    settings.db_path = tmp_path / "document-download.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "下载资料")
            created = _create_document(
                client, space["id"], admin_headers, "可下载文档", content=b"download-me"
            )
            document = created["document"]
            reader_headers = _member_headers(
                client, space["id"], "document-download-reader", "reader"
            )
            response = client.get(
                f"/documents/{document['id']}/download", headers=reader_headers
            )
            assert response.status_code == 200
            assert response.content == b"download-me"
            assert "attachment" in response.headers.get("content-disposition", "")
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.upload_dir = original_upload_dir
        database.reset_database(tmp_path / "document-download.sqlite3")


def test_document_download_preserves_space_and_version_boundaries(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    original_upload_dir = settings.upload_dir
    settings.db_path = tmp_path / "document-download-permissions.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "可读资料")
            created = _create_document(client, space["id"], admin_headers, "可读文档")
            reader_headers = _member_headers(
                client, space["id"], "download-boundary-reader", "reader"
            )
            private_space, _ = _create_space(client, "私有资料")
            private = _create_document(
                client, private_space["id"], admin_headers, "不可读文档"
            )
            for action in ("preview", "download"):
                denied = client.get(
                    f"/documents/{private['document']['id']}/{action}",
                    headers=reader_headers,
                )
                assert denied.status_code == 404
                foreign_version = client.get(
                    f"/documents/{created['document']['id']}/{action}",
                    params={"version_id": private["document"]["latest_version"]["id"]},
                    headers=reader_headers,
                )
                assert foreign_version.status_code == 404
            anonymous = client.get(f"/documents/{created['document']['id']}/download")
            assert anonymous.status_code == 401
            reader_id = client.get("/auth/me", headers=reader_headers).json()["id"]
            revoked = client.delete(
                f"/knowledge-spaces/{space['id']}/members/{reader_id}",
                headers=admin_headers,
            )
            assert revoked.status_code == 204
            for action in ("preview", "download"):
                denied = client.get(
                    f"/documents/{created['document']['id']}/{action}",
                    headers=reader_headers,
                )
                assert denied.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.upload_dir = original_upload_dir
        database.reset_database(tmp_path / "document-download-permissions.sqlite3")


def test_retry_resubmits_via_local_pipeline_when_file_exists(
    tmp_path: Path, monkeypatch
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-retry-ingest.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    pipeline_calls: list[dict] = []

    def capturing_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return {"chunk_count": 1, "vector_ids": ["vec-test"]}

    monkeypatch.setattr("app.api.documents.run_knowledge_pipeline", capturing_pipeline)

    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "重试资料")
            created = _create_document(
                client, space["id"], admin_headers, "可重试文档", content=b"retry body"
            )
            document = created["document"]
            version_id = document["latest_version"]["id"]
            assert len(pipeline_calls) == 1

            file_path = settings.upload_dir / space["id"] / f"{document['id']}.txt"
            assert file_path.is_file()

            with transaction() as db:
                db.execute(
                    text(
                        "UPDATE document_versions SET processing_stage = 'failed', queryable = 0, "
                        "retryable = 1, failure_code = 'process_failed', failure_message = 'boom', "
                        "file_path = :file_path, is_current_published = 0 WHERE id = :id"
                    ),
                    {"id": version_id, "file_path": str(file_path)},
                )

            retried = client.post(
                f"/documents/{document['id']}/versions/{version_id}:retry",
                headers=admin_headers,
            )
            assert retried.status_code == 200
            assert retried.json()["status"] in (
                "queued",
                "scanning",
                "processing",
                "completed",
            )

            detail = client.get(
                f"/documents/{document['id']}", headers=admin_headers
            ).json()
            assert detail["latest_version"]["id"] != version_id
            assert detail["latest_version"]["processing_stage"] in (
                "scanning",
                "processing",
                "ready",
            )
            assert detail["latest_version"]["processing_stage"] != "failed"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-retry-ingest.sqlite3")


def test_document_migration_generates_postgresql_boolean_constraints_and_partial_index(
    monkeypatch,
):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(op, "_proxy", Operations(context), raising=False)
    try:
        migration_path = (
            Path(__file__).parents[1]
            / "alembic"
            / "versions"
            / "20260727_0004_documents.py"
        )
        spec = importlib.util.spec_from_file_location(
            "documents_migration", migration_path
        )
        assert spec and spec.loader
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.upgrade()
    finally:
        pass

    ddl = output.getvalue().lower()
    assert "queryable is false or processing_stage = 'ready'" in ddl
    assert "is_current_published is false or processing_stage = 'ready'" in ddl
    assert "where is_current_published is true" in ddl
    assert "queryable = 0" not in ddl
    assert "is_current_published = 0" not in ddl


def test_uploaded_document_job_survives_workspace_reload_and_can_be_refetched(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-jobs.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "任务资料")
            contributor_headers = _member_headers(
                client, space["id"], "document-task-contributor", "contributor"
            )
            created = _create_document(
                client, space["id"], contributor_headers, "待处理规范"
            )
            task = created["task"]
            assert task["job_id"]

            detail = client.get(
                f"/documents/{created['document']['id']}", headers=contributor_headers
            )
            assert detail.status_code == 200
            stage = detail.json()["latest_version"]["processing_stage"]

            workspace = client.get("/workspace", headers=contributor_headers)
            assert workspace.status_code == 200
            running_ids = {item["job_id"] for item in workspace.json()["running_tasks"]}
            if stage in document_repository.PROCESSING_STAGES:
                assert task["job_id"] in running_ids
            else:
                assert task["job_id"] not in running_ids
                assert stage in ("ready", "failed")

            refreshed = client.get(
                f"/jobs/{task['job_id']}", headers=contributor_headers
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["job_id"] == task["job_id"]
            assert (
                refreshed.json()["resource_path"]
                == f"/spaces/{space['id']}/documents/{created['document']['id']}"
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-jobs.sqlite3")


def test_queued_document_job_can_be_cancelled_and_leaves_task_center(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-job-cancel.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "取消任务资料")
            contributor_headers = _member_headers(
                client, space["id"], "document-cancel-contributor", "contributor"
            )
            reader_headers = _member_headers(
                client, space["id"], "document-cancel-reader", "reader"
            )
            created = _create_document(
                client, space["id"], contributor_headers, "卡住的规范"
            )
            document = created["document"]
            task = created["task"]
            assert (
                task["resource_path"]
                == f"/spaces/{space['id']}/documents/{document['id']}"
            )

            with transaction() as db:
                version = db.get(
                    document_repository.DocumentVersion,
                    document["latest_version"]["id"],
                )
                job = db.get(document_repository.DocumentJob, task["job_id"])
                assert version is not None and job is not None
                version.processing_stage = "queued"
                version.queryable = False
                version.is_current_published = False
                version.retryable = False
                version.failure_code = None
                version.failure_message = None
                job.status = "queued"

            snapshot = client.get(
                f"/jobs/{task['job_id']}", headers=contributor_headers
            )
            assert snapshot.status_code == 200
            payload = snapshot.json()
            assert payload["cancel_action"] == {
                "path": f"/jobs/{task['job_id']}:cancel",
                "method": "POST",
            }
            assert (
                payload["resource_path"]
                == f"/spaces/{space['id']}/documents/{document['id']}"
            )

            assert (
                client.post(
                    f"/jobs/{task['job_id']}:cancel", headers=reader_headers
                ).status_code
                == 404
            )

            cancelled = client.post(
                f"/jobs/{task['job_id']}:cancel", headers=contributor_headers
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["cancel_action"] is None

            workspace = client.get("/workspace", headers=contributor_headers)
            assert workspace.status_code == 200
            running_ids = {item["job_id"] for item in workspace.json()["running_tasks"]}
            assert task["job_id"] not in running_ids

            detail = client.get(
                f"/documents/{document['id']}", headers=contributor_headers
            )
            assert detail.status_code == 200
            assert detail.json()["latest_version"]["processing_stage"] == "failed"
            assert detail.json()["latest_version"]["failure_code"] == "cancelled"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-job-cancel.sqlite3")


def test_deleting_document_cancels_queued_job_and_leaves_task_center(
    tmp_path: Path, monkeypatch
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-delete-cancels-job.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(
        "app.api.documents._cleanup_local_document_vectors", lambda _document: None
    )
    try:
        with TestClient(app) as client:
            space, headers = _create_space(client, "删除清任务")
            created = _create_document(client, space["id"], headers, "待删文档")
            document = created["document"]
            job_id = created["task"]["job_id"]
            with transaction() as db:
                version = db.get(
                    document_repository.DocumentVersion,
                    document["latest_version"]["id"],
                )
                job = db.get(document_repository.DocumentJob, job_id)
                assert version is not None and job is not None
                version.processing_stage = "queued"
                version.queryable = False
                version.is_current_published = False
                version.retryable = False
                version.chunk_count = 0
                job.status = "queued"

            deleted = client.delete(f"/documents/{document['id']}", headers=headers)
            assert deleted.status_code == 200

            with transaction() as db:
                job = db.get(document_repository.DocumentJob, job_id)
                assert job is not None
                assert job.status == "cancelled"

            workspace = client.get("/workspace", headers=headers)
            assert workspace.status_code == 200
            assert job_id not in {
                item["job_id"] for item in workspace.json()["running_tasks"]
            }
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-delete-cancels-job.sqlite3")


def test_finished_version_drops_stale_queued_job_from_workspace(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-job-stale.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "过期任务资料")
            created = _create_document(client, space["id"], admin_headers, "已完成文档")
            document = created["document"]
            job_id = created["task"]["job_id"]
            with transaction() as db:
                version = db.get(
                    document_repository.DocumentVersion,
                    document["latest_version"]["id"],
                )
                job = db.get(document_repository.DocumentJob, job_id)
                assert version is not None and job is not None
                document_repository.complete_version(db, version, chunk_count=1)
                job.status = "queued"

            workspace = client.get("/workspace", headers=admin_headers)
            assert workspace.status_code == 200
            running_ids = {item["job_id"] for item in workspace.json()["running_tasks"]}
            assert job_id not in running_ids
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-job-stale.sqlite3")


def test_document_list_accepts_processing_aggregate_and_signed_keyset_cursor(
    tmp_path: Path,
):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-cursor.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "分页资料")
            admin_user_id = client.get("/auth/me", headers=admin_headers).json()["id"]
            with transaction() as db:
                for index in range(51):
                    document_repository.create_document(
                        db,
                        space_id=space["id"],
                        category_id=None,
                        name=f"分页文档 {index:02d}",
                        created_by_user_id=admin_user_id,
                    )

            first = client.get(
                f"/knowledge-spaces/{space['id']}/documents?status=processing",
                headers=admin_headers,
            )
            assert first.status_code == 200
            first_payload = first.json()
            assert len(first_payload["items"]) == 50
            cursor = first_payload["next_cursor"]
            assert cursor and cursor.count(".") == 2

            with transaction() as db:
                document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=None,
                    name="分页文档 新到达",
                    created_by_user_id=admin_user_id,
                )

            second = client.get(
                f"/knowledge-spaces/{space['id']}/documents?status=processing&cursor={cursor}",
                headers=admin_headers,
            )
            assert second.status_code == 200
            assert {item["id"] for item in first_payload["items"]}.isdisjoint(
                {item["id"] for item in second.json()["items"]}
            )
            tampered = f"{cursor[:-1]}{'a' if cursor[-1] != 'a' else 'b'}"
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}/documents?cursor={tampered}",
                    headers=admin_headers,
                ).status_code
                == 422
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-cursor.sqlite3")


def test_document_list_supports_offset_limit_with_total(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-offset.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "偏移分页资料")
            admin_user_id = client.get("/auth/me", headers=admin_headers).json()["id"]
            with transaction() as db:
                for index in range(12):
                    document_repository.create_document(
                        db,
                        space_id=space["id"],
                        category_id=None,
                        name=f"偏移文档 {index:02d}",
                        created_by_user_id=admin_user_id,
                    )

            first = client.get(
                f"/knowledge-spaces/{space['id']}/documents?offset=0&limit=10",
                headers=admin_headers,
            )
            assert first.status_code == 200
            first_payload = first.json()
            assert len(first_payload["items"]) == 10
            assert first_payload["total"] == 12
            assert first_payload["next_cursor"] is None

            second = client.get(
                f"/knowledge-spaces/{space['id']}/documents?offset=10&limit=10",
                headers=admin_headers,
            )
            assert second.status_code == 200
            second_payload = second.json()
            assert len(second_payload["items"]) == 2
            assert second_payload["total"] == 12
            assert {item["id"] for item in first_payload["items"]}.isdisjoint(
                {item["id"] for item in second_payload["items"]}
            )

            conflict = client.get(
                f"/knowledge-spaces/{space['id']}/documents?offset=0&limit=10&cursor=bad",
                headers=admin_headers,
            )
            assert conflict.status_code == 422
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-offset.sqlite3")


def test_document_list_counts_ignore_filters_and_respect_visibility(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-counts.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "计数资料")
            other_space, _ = _create_space(client, "其他计数资料")
            category_ids = {}
            for category_name in ("重点分类", "其他分类", "空分类"):
                category_response = client.post(
                    f"/knowledge-spaces/{space['id']}/categories",
                    headers=admin_headers,
                    json={"name": category_name},
                )
                assert category_response.status_code == 201, category_response.text
                category_ids[category_name] = category_response.json()["id"]
            primary_category_id = category_ids["重点分类"]
            other_category_id = category_ids["其他分类"]
            empty_category_id = category_ids["空分类"]
            admin_user_id = client.get("/auth/me", headers=admin_headers).json()["id"]

            with transaction() as db:
                multi_version, first_version = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=primary_category_id,
                    name="目标报告A",
                    created_by_user_id=admin_user_id,
                )
                document_repository.update_version_stage(
                    db,
                    first_version,
                    "failed",
                    failure_code="parse_failed",
                    failure_message="故意制造旧版本",
                )
                latest_version = document_repository.retry_failed_version(
                    db, multi_version, first_version
                )
                document_repository.complete_version(db, latest_version)

                _, ready_version = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=primary_category_id,
                    name="目标补充A",
                    created_by_user_id=admin_user_id,
                )
                document_repository.complete_version(db, ready_version)

                document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=primary_category_id,
                    name="目标处理中A",
                    created_by_user_id=admin_user_id,
                )

                _, other_category_version = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=other_category_id,
                    name="目标报告B",
                    created_by_user_id=admin_user_id,
                )
                document_repository.complete_version(db, other_category_version)

                _, uncategorized_version = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=None,
                    name="目标未分类",
                    created_by_user_id=admin_user_id,
                )
                document_repository.complete_version(db, uncategorized_version)

                archived, _ = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=primary_category_id,
                    name="目标归档",
                    created_by_user_id=admin_user_id,
                )
                document_repository.archive_document(archived)

                deleted, _ = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=other_category_id,
                    name="目标删除",
                    created_by_user_id=admin_user_id,
                )
                document_repository.delete_document(db, deleted)

                document_repository.create_document(
                    db,
                    space_id=other_space["id"],
                    category_id=None,
                    name="目标外部",
                    created_by_user_id=admin_user_id,
                )

            list_url = f"/knowledge-spaces/{space['id']}/documents"
            without_counts = client.get(list_url, headers=admin_headers)
            assert without_counts.status_code == 200, without_counts.text
            assert without_counts.json()["counts"] is None

            unfiltered = client.get(
                f"{list_url}?offset=0&limit=1&include_counts=true",
                headers=admin_headers,
            )
            assert unfiltered.status_code == 200, unfiltered.text
            assert unfiltered.json()["total"] == 5
            unfiltered_counts = unfiltered.json()["counts"]
            assert unfiltered_counts == {
                "all": 5,
                "by_category": {
                    primary_category_id: 3,
                    other_category_id: 1,
                },
            }
            assert empty_category_id not in unfiltered_counts["by_category"]
            assert unfiltered_counts["all"] == (
                sum(unfiltered_counts["by_category"].values()) + 1
            )

            filtered = client.get(
                f"{list_url}?category_id={primary_category_id}"
                "&status=ready&q=目标&offset=0&limit=1&include_counts=true",
                headers=admin_headers,
            )
            assert filtered.status_code == 200, filtered.text
            filtered_payload = filtered.json()
            assert filtered_payload["total"] == 2
            assert len(filtered_payload["items"]) == 1
            assert filtered_payload["counts"] == unfiltered_counts

            reader_headers = _member_headers(
                client, space["id"], "document-count-reader", "reader"
            )
            reader_counts = client.get(
                f"{list_url}?include_counts=true", headers=reader_headers
            )
            assert reader_counts.status_code == 200, reader_counts.text
            assert reader_counts.json()["counts"] == unfiltered_counts

            hidden_counts = client.get(
                f"/knowledge-spaces/{other_space['id']}/documents?include_counts=true",
                headers=reader_headers,
            )
            assert hidden_counts.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-counts.sqlite3")


def test_document_processing_completes_via_local_pipeline(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-summary.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "简介资料")
            created = _create_document(client, space["id"], admin_headers, "安全通报")
            document_id = created["document"]["id"]
            detail = client.get(f"/documents/{document_id}", headers=admin_headers)
            assert detail.status_code == 200
            version = detail.json()["latest_version"]
            assert version["processing_stage"] == "ready"

            listed = client.get(
                f"/knowledge-spaces/{space['id']}/documents", headers=admin_headers
            )
            assert listed.status_code == 200
            assert (
                listed.json()["items"][0]["latest_version"]["processing_stage"]
                == "ready"
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-summary.sqlite3")


def test_document_ready_survives_knowledge_graph_compile(tmp_path: Path, monkeypatch):
    """Regression: local pipeline completed + extractable text must still mark ready.

    Previously KnowledgeCompiler omitted required entity fields, flush poisoned the
    session, and complete_version(ready) was rolled back — UI stuck on 处理中.
    """

    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-kg-ready.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "图谱资料")
            created = _create_document(client, space["id"], admin_headers, "规范文档")
            document_id = created["document"]["id"]
            detail = client.get(f"/documents/{document_id}", headers=admin_headers)
            assert detail.status_code == 200
            version = detail.json()["latest_version"]
            assert version["processing_stage"] in ("ready", "scanning")
            assert version["chunk_count"] in (0, 1)
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-kg-ready.sqlite3")


def test_recover_stuck_processing_marks_interrupted_jobs_failed(tmp_path: Path):
    from app.api.documents import recover_stuck_processing
    from app.models.documents import DocumentVersion

    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "document-recover.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "恢复资料")
            admin_user_id = client.get("/auth/me", headers=admin_headers).json()["id"]
            with transaction() as db:
                document, version = document_repository.create_document(
                    db,
                    space_id=space["id"],
                    category_id=None,
                    name="中断文档",
                    created_by_user_id=admin_user_id,
                )
                document_repository.update_version_stage(db, version, "processing")
                document_repository.create_job(
                    db,
                    document=document,
                    version=version,
                    created_by_user_id=admin_user_id,
                )
                version_id = version.id

            stats = recover_stuck_processing()
            assert stats["failed"] == 1
            with transaction() as db:
                restored = db.get(DocumentVersion, version_id)
                assert restored is not None
                assert restored.processing_stage == "failed"
                assert restored.retryable is True
                assert restored.failure_code == "process_timeout"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "document-recover.sqlite3")


@pytest.fixture
def multipart_documents(tmp_path, monkeypatch):
    """Keep the API/worker/database real; replace only the remote object store."""
    from app.services import document_uploads, minio_storage

    path = tmp_path / "multipart.sqlite3"
    monkeypatch.setattr(settings, "db_path", path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "document_uploads_enabled", True)
    monkeypatch.setattr(settings, "worker_role", "api")
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        minio_storage,
        "load_minio_settings",
        lambda: SimpleNamespace(bucket="documents", temp_bucket="staging"),
    )
    monkeypatch.setattr(minio_storage, "ensure_configured_buckets", lambda *args: [])

    payload = b"Actual document content for integrity and worker recovery."
    objects = {}
    uploads = {}
    sequence = 0

    def location(values):
        if values.get("minio_uri"):
            return minio_storage.parse_minio_uri(values["minio_uri"])
        return values["bucket"], values["object_key"]

    def initiate(*, bucket, object_key, **kwargs):
        nonlocal sequence
        sequence += 1
        upload_id = f"multipart-{sequence}"
        uploads[upload_id] = (bucket, object_key)
        return upload_id

    def list_parts(*, upload_id, **kwargs):
        if upload_id not in uploads:
            raise FileNotFoundError(upload_id)
        return [{"part_number": 1, "size_bytes": len(payload), "etag": "stored-part"}]

    def complete(*, upload_id, **kwargs):
        objects[uploads.pop(upload_id)] = payload

    def download(*, path, **kwargs):
        path.write_bytes(objects[location(kwargs)])

    def copy(*, source_bucket, source_key, target_bucket, target_key):
        objects[target_bucket, target_key] = objects[source_bucket, source_key]

    monkeypatch.setattr(minio_storage, "initiate_multipart_upload", initiate)
    monkeypatch.setattr(minio_storage, "list_multipart_parts", list_parts)
    monkeypatch.setattr(minio_storage, "complete_multipart_upload", complete)
    monkeypatch.setattr(
        minio_storage,
        "abort_multipart_upload",
        lambda *, upload_id, **kwargs: uploads.pop(upload_id, None),
    )
    monkeypatch.setattr(
        minio_storage,
        "stat_object",
        lambda **kwargs: SimpleNamespace(size=len(objects[location(kwargs)])),
    )
    monkeypatch.setattr(minio_storage, "download_to_path", download)
    monkeypatch.setattr(minio_storage, "list_multipart_uploads", lambda **kwargs: [])
    monkeypatch.setattr(minio_storage, "copy_object", copy)
    monkeypatch.setattr(
        minio_storage,
        "delete_object",
        lambda **kwargs: objects.pop(location(kwargs), None),
    )
    with TestClient(app) as client:
        space, headers = _create_space(client, "Multipart contracts")
        yield SimpleNamespace(
            client=client,
            space=space,
            headers=headers,
            payload=payload,
            service=document_uploads,
            objects=objects,
        )
    database.reset_database(path)


def _multipart_session(
    context, *, headers=None, checksum=None, client_id="resume-file"
):
    return context.client.post(
        f"/knowledge-spaces/{context.space['id']}/document-uploads",
        headers=headers or context.headers,
        json={
            "client_upload_id": client_id,
            "filename": "source.txt",
            "size_bytes": len(context.payload),
            "sha256": checksum or hashlib.sha256(context.payload).hexdigest(),
        },
    )


@pytest.mark.parametrize("valid_checksum", [True, False])
def test_multipart_integrity_gates_document_publication(
    multipart_documents, valid_checksum
):
    ctx = multipart_documents
    created = _multipart_session(ctx, checksum=None if valid_checksum else "0" * 64)
    assert created.status_code == 201
    upload_id = created.json()["id"]
    completed = ctx.client.post(
        f"/document-uploads/{upload_id}/complete", headers=ctx.headers, json={}
    )
    # This also covers SQLite's datetime-sensitive compare-and-swap path.
    assert completed.status_code == 200
    assert ctx.service.DocumentUploadDispatcher().run_once()
    observed = ctx.client.get(
        f"/document-uploads/{upload_id}", headers=ctx.headers
    ).json()
    if valid_checksum:
        assert observed["status"] == "completed"
        assert observed["result"]["document"]["latest_version"]["queryable"] is True
        replay = ctx.client.post(
            f"/document-uploads/{upload_id}/complete", headers=ctx.headers, json={}
        ).json()
        assert (
            replay["result"]["document"]["id"] == observed["result"]["document"]["id"]
        )
        assert replay["result"]["task"]["id"] == observed["result"]["task"]["id"]
    else:
        assert observed["status"] == "failed"
        assert observed["result"] is None
    listed = ctx.client.get(
        f"/knowledge-spaces/{ctx.space['id']}/documents", headers=ctx.headers
    ).json()
    assert len(listed["items"]) == (1 if valid_checksum else 0)


def test_multipart_resume_identity_and_owner_are_not_interchangeable(
    multipart_documents,
):
    ctx = multipart_documents
    owner = _member_headers(ctx.client, ctx.space["id"], "upload-owner", "contributor")
    created = _multipart_session(ctx, headers=owner).json()
    resumed = _multipart_session(ctx, headers=owner)
    assert resumed.status_code == 201
    assert resumed.json()["id"] == created["id"]
    assert (
        ctx.client.get(
            f"/document-uploads/{created['id']}", headers=ctx.headers
        ).status_code
        == 404
    )
    changed = _multipart_session(ctx, headers=owner, checksum="1" * 64)
    assert changed.status_code == 409
    assert (
        ctx.client.get(f"/document-uploads/{created['id']}", headers=owner).json()[
            "status"
        ]
        == "uploading"
    )


def test_multipart_revoked_contributor_cannot_publish_during_indexing(
    multipart_documents,
    monkeypatch,
):
    from sqlalchemy import delete
    from app.models.access import SpaceMember
    from app.models.document_uploads import DocumentUploadSession
    import app.api.documents as document_api

    ctx = multipart_documents
    owner = _member_headers(
        ctx.client, ctx.space["id"], "revoked-uploader", "contributor"
    )
    owner_id = ctx.client.get("/auth/me", headers=owner).json()["id"]
    upload_id = _multipart_session(ctx, headers=owner).json()["id"]
    assert (
        ctx.client.post(
            f"/document-uploads/{upload_id}/complete", headers=owner, json={}
        ).status_code
        == 200
    )

    def revoke_during_indexing(**kwargs):
        with transaction() as db:
            db.execute(
                delete(SpaceMember).where(
                    SpaceMember.space_id == ctx.space["id"],
                    SpaceMember.user_id == owner_id,
                )
            )
        return {"chunk_count": 1, "vector_ids": ["completed-embedding"]}

    monkeypatch.setattr(document_api, "run_knowledge_pipeline", revoke_during_indexing)
    assert ctx.service.DocumentUploadDispatcher().run_once()
    with transaction() as db:
        record = db.get(DocumentUploadSession, upload_id)
        document_id = record.document_id
    detail = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()
    assert detail["latest_version"]["queryable"] is False
    assert detail["latest_version"]["processing_stage"] == "failed"
    assert (
        ctx.client.get(f"/document-uploads/{upload_id}", headers=owner).status_code
        == 404
    )


def test_multipart_reclaims_promoted_object_after_worker_crash(
    multipart_documents,
    monkeypatch,
):
    from datetime import timedelta
    from app.core.security import now
    from app.models.document_uploads import DocumentUploadSession
    import app.api.documents as document_api

    class WorkerCrash(BaseException):
        pass

    ctx = multipart_documents
    upload_id = _multipart_session(ctx).json()["id"]
    assert (
        ctx.client.post(
            f"/document-uploads/{upload_id}/complete", headers=ctx.headers, json={}
        ).status_code
        == 200
    )
    original_pipeline = document_api.run_knowledge_pipeline

    def crash(**kwargs):
        raise WorkerCrash()

    monkeypatch.setattr(document_api, "run_knowledge_pipeline", crash)
    with pytest.raises(WorkerCrash):
        ctx.service.DocumentUploadDispatcher("before-crash").run_once()
    interrupted = ctx.client.get(
        f"/document-uploads/{upload_id}", headers=ctx.headers
    ).json()
    original_document_id = interrupted["result"]["document"]["id"]
    original_job_id = interrupted["result"]["task"]["id"]
    assert document_api.recover_stuck_processing()["failed"] == 0
    with transaction() as db:
        record = db.get(DocumentUploadSession, upload_id)
        record.lease_expires_at = now() - timedelta(seconds=1)
    monkeypatch.setattr(document_api, "run_knowledge_pipeline", original_pipeline)
    assert ctx.service.DocumentUploadDispatcher("after-crash").run_once()
    recovered = ctx.client.get(
        f"/document-uploads/{upload_id}", headers=ctx.headers
    ).json()
    assert recovered["status"] == "completed"
    assert recovered["result"]["document"]["id"] == original_document_id
    assert recovered["result"]["task"]["id"] == original_job_id
    detail = ctx.client.get(
        f"/documents/{original_document_id}", headers=ctx.headers
    ).json()
    assert len(detail["versions"]) == 1
    assert detail["latest_version"]["queryable"] is True


def test_multipart_cleanup_advances_after_consumed_upload_ids(
    multipart_documents, monkeypatch
):
    from sqlalchemy import select

    from app.models.document_uploads import DocumentUploadSession

    class NoSuchUpload(Exception):
        code = "NoSuchUpload"

    ctx = multipart_documents
    first = _multipart_session(ctx, client_id="cleanup-consumed-first").json()
    second = _multipart_session(ctx, client_id="cleanup-consumed-second").json()
    for upload in (first, second):
        assert (
            ctx.client.post(
                f"/document-uploads/{upload['id']}/complete",
                headers=ctx.headers,
                json={},
            ).status_code
            == 200
        )
        assert ctx.service.DocumentUploadDispatcher().run_once()

    def abort_missing(**kwargs):
        raise NoSuchUpload()

    monkeypatch.setattr(
        ctx.service.minio_storage, "abort_multipart_upload", abort_missing
    )
    first_stats = ctx.service.cleanup_expired_uploads(limit=1)
    second_stats = ctx.service.cleanup_expired_uploads(limit=1)
    assert first_stats["scanned"] == 1
    assert first_stats["cleaned"] == 1
    assert second_stats["scanned"] == 1
    assert second_stats["cleaned"] == 1

    with transaction() as db:
        rows = list(
            db.scalars(
                select(DocumentUploadSession).where(
                    DocumentUploadSession.id.in_([first["id"], second["id"]])
                )
            )
        )
        assert len(rows) == 2
        assert all(row.staging_object_key == "" for row in rows)
        assert all(row.multipart_upload_id is None for row in rows)


def test_multipart_stored_retry_allows_current_contributor_not_original_creator(
    multipart_documents, monkeypatch
):
    from sqlalchemy import select

    from app.models.document_uploads import DocumentUploadSession
    import app.api.documents as document_api

    ctx = multipart_documents
    source_owner = _member_headers(
        ctx.client, ctx.space["id"], "stored-retry-source-owner", "contributor"
    )
    current_contributor = _member_headers(
        ctx.client, ctx.space["id"], "stored-retry-current-contributor", "contributor"
    )
    denied_reader = _member_headers(
        ctx.client, ctx.space["id"], "stored-retry-reader", "reader"
    )
    created = _multipart_session(
        ctx, headers=source_owner, client_id="stored-retry-contributor"
    ).json()
    assert (
        ctx.client.post(
            f"/document-uploads/{created['id']}/complete",
            headers=source_owner,
            json={},
        ).status_code
        == 200
    )

    def fail_pipeline(**kwargs):
        raise RuntimeError("intentional stored retry failure")

    monkeypatch.setattr(document_api, "run_knowledge_pipeline", fail_pipeline)
    assert ctx.service.DocumentUploadDispatcher("stored-retry-source").run_once()
    with transaction() as db:
        source_session = db.get(DocumentUploadSession, created["id"])
        assert source_session is not None
        document_id = source_session.document_id
        source_version_id = source_session.version_id
        source_session_id = source_session.id
        assert document_id is not None
        assert source_version_id is not None

    denied = ctx.client.post(
        f"/documents/{document_id}/versions/{source_version_id}:retry",
        headers=denied_reader,
    )
    assert denied.status_code == 404

    retried = ctx.client.post(
        f"/documents/{document_id}/versions/{source_version_id}:retry",
        headers=current_contributor,
    )
    assert retried.status_code == 200, retried.text
    current_user_id = ctx.client.get("/auth/me", headers=current_contributor).json()[
        "id"
    ]
    with transaction() as db:
        retry_sessions = list(
            db.scalars(
                select(DocumentUploadSession).where(
                    DocumentUploadSession.source_session_id == source_session_id
                )
            )
        )
        assert len(retry_sessions) == 1
        retry_session = retry_sessions[0]
        assert retry_session.owner_user_id == current_user_id
        assert retry_session.document_id == document_id
        assert retry_session.space_id == ctx.space["id"]
        assert retry_session.canonical_object_key == source_session.canonical_object_key


def test_multipart_live_processing_lease_outlives_session_ttl(multipart_documents):
    from datetime import timedelta

    from app.core.security import now
    from app.models.document_uploads import DocumentUploadSession

    ctx = multipart_documents
    upload_id = _multipart_session(ctx, client_id="live-lease-ttl").json()["id"]
    with transaction() as db:
        session = db.get(DocumentUploadSession, upload_id)
        assert session is not None
        session.status = "processing"
        session.expires_at = now() - timedelta(seconds=1)
        session.lease_owner = "live-processing-worker"
        session.lease_expires_at = now() + timedelta(minutes=5)
        session.updated_at = now()

    observed = ctx.client.get(f"/document-uploads/{upload_id}", headers=ctx.headers)
    assert observed.status_code == 200
    assert observed.json()["status"] == "processing"
    with transaction() as db:
        session = db.get(DocumentUploadSession, upload_id)
        assert session is not None
        assert session.status == "processing"
        assert session.lease_owner == "live-processing-worker"


def test_multipart_poll_expiry_does_not_overwrite_a_renewed_lease(
    multipart_documents, monkeypatch
):
    from datetime import timedelta
    from sqlalchemy import update

    from app.core.security import now
    from app.models.document_uploads import DocumentUploadSession

    ctx = multipart_documents
    upload_id = _multipart_session(ctx, client_id="renew-during-poll").json()["id"]
    with transaction() as db:
        session = db.get(DocumentUploadSession, upload_id)
        session.status = "processing"
        session.expires_at = now() - timedelta(seconds=2)
        session.lease_owner = "renewing-worker"
        session.lease_expires_at = now() - timedelta(seconds=1)

    original_expire = ctx.service._expire_session_checkpoint

    def renew_before_expiry_write(db, session):
        with transaction() as heartbeat_db:
            heartbeat_db.execute(
                update(DocumentUploadSession)
                .where(DocumentUploadSession.id == upload_id)
                .values(lease_expires_at=now() + timedelta(minutes=5))
            )
        original_expire(db, session)

    monkeypatch.setattr(
        ctx.service, "_expire_session_checkpoint", renew_before_expiry_write
    )
    response = ctx.client.get(f"/document-uploads/{upload_id}", headers=ctx.headers)
    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    with transaction() as db:
        session = db.get(DocumentUploadSession, upload_id)
        assert session.lease_owner == "renewing-worker"


def test_multipart_copy_failure_requeues_without_losing_checkpoint(
    multipart_documents, monkeypatch
):
    from app.models.document_uploads import DocumentUploadSession
    from app.models.documents import DocumentJob, DocumentVersion

    ctx = multipart_documents
    upload_id = _multipart_session(ctx, client_id="copy-retry").json()["id"]
    assert (
        ctx.client.post(
            f"/document-uploads/{upload_id}/complete",
            headers=ctx.headers,
            json={},
        ).status_code
        == 200
    )
    original_copy = ctx.service.minio_storage.copy_object
    copy_attempts = 0

    def flaky_copy(**kwargs):
        nonlocal copy_attempts
        copy_attempts += 1
        if copy_attempts == 1:
            raise RuntimeError("temporary canonical copy failure")
        return original_copy(**kwargs)

    monkeypatch.setattr(ctx.service.minio_storage, "copy_object", flaky_copy)
    assert ctx.service.DocumentUploadDispatcher("copy-first").run_once()
    with transaction() as db:
        first = db.get(DocumentUploadSession, upload_id)
        assert first is not None
        assert first.status == "queued"
        assert first.lease_owner is None
        assert first.document_id is not None
        assert first.version_id is not None
        assert first.job_id is not None
        assert first.canonical_object_key is None
        assert first.staging_object_key
        assert (first.staging_bucket, first.staging_object_key) in ctx.objects
        version_id = first.version_id
        document_id = first.document_id
        job_id = first.job_id
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.file_path is None

    assert ctx.service.DocumentUploadDispatcher("copy-second").run_once()
    assert copy_attempts == 2
    with transaction() as db:
        second = db.get(DocumentUploadSession, upload_id)
        assert second is not None
        assert second.status == "completed"
        assert second.document_id == document_id
        assert second.version_id == version_id
        assert second.job_id == job_id
        assert second.canonical_object_key
        version = db.get(DocumentVersion, version_id)
        job = db.get(DocumentJob, job_id)
        assert version is not None
        assert job is not None
        assert version.file_path
        assert job.status == "completed"
        assert (
            second.canonical_bucket,
            second.canonical_object_key,
        ) in ctx.objects

    detail = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers)
    assert detail.status_code == 200
    assert len(detail.json()["versions"]) == 1
    assert detail.json()["latest_version"]["queryable"] is True


def test_multipart_revocation_after_lease_preflight_cannot_publish(
    multipart_documents,
    monkeypatch,
):
    from sqlalchemy import delete

    from app.models.access import SpaceMember
    from app.models.document_uploads import DocumentUploadSession
    from app.models.documents import DocumentJob, DocumentVersion
    import app.api.documents as document_api

    ctx = multipart_documents
    owner = _member_headers(
        ctx.client, ctx.space["id"], "revoked-after-preflight", "contributor"
    )
    owner_id = ctx.client.get("/auth/me", headers=owner).json()["id"]
    upload_id = _multipart_session(ctx, headers=owner).json()["id"]
    assert (
        ctx.client.post(
            f"/document-uploads/{upload_id}/complete", headers=owner, json={}
        ).status_code
        == 200
    )

    pipeline_finished = False
    original_pipeline = document_api.run_knowledge_pipeline

    def finish_pipeline(**kwargs):
        nonlocal pipeline_finished
        result = original_pipeline(**kwargs)
        pipeline_finished = True
        return result

    monkeypatch.setattr(document_api, "run_knowledge_pipeline", finish_pipeline)

    original_lease_is_owned = ctx.service._lease_is_owned
    revoked = False

    def revoke_after_preflight(session_id, worker_owner, *, db=None):
        nonlocal revoked
        owned = (
            original_lease_is_owned(session_id, worker_owner)
            if db is None
            else original_lease_is_owned(session_id, worker_owner, db=db)
        )
        if owned and db is None and pipeline_finished and not revoked:
            with transaction() as revoke_db:
                revoke_db.execute(
                    delete(SpaceMember).where(
                        SpaceMember.space_id == ctx.space["id"],
                        SpaceMember.user_id == owner_id,
                    )
                )
            revoked = True
        return owned

    monkeypatch.setattr(ctx.service, "_lease_is_owned", revoke_after_preflight)
    assert ctx.service.DocumentUploadDispatcher().run_once()
    assert revoked

    with transaction() as db:
        record = db.get(DocumentUploadSession, upload_id)
        assert record is not None
        document_id = record.document_id
        version = db.get(DocumentVersion, record.version_id)
        job = db.get(DocumentJob, record.job_id)
        assert version is not None
        assert job is not None
        assert record.status == "failed"
        assert record.lease_owner is None
        assert job.status == "failed"
        assert version.processing_stage == "failed"
        assert version.queryable is False

    detail = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()
    assert detail["latest_version"]["queryable"] is False


@pytest.mark.parametrize("uncertainty,warning,support_state", [
    ("", None, "supported"), ("生产日期模糊", "image_vision_unclear", "uncertain"),
])
def test_image_vision_index_retry_reuses_parse_and_keeps_versioned_evidence(
    multipart_documents, monkeypatch, uncertainty, warning, support_state,
):
    from io import BytesIO
    from PIL import Image
    from app.models.documents import DocumentVersion
    from app.services import minio_storage, vision_fallback
    from app.services.vision_fallback import VisionParseResult
    from app.repositories.document_pages import location_for_char_range
    import app.api.documents as document_api

    ctx = multipart_documents
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(document_api, "resolve_vision_endpoint", lambda *_a, **_k: object())
    monkeypatch.setattr("app.services.pdf_ocr._run_tesseract", lambda *_a: pytest.fail("图片不得调用 OCR"))
    model_calls = []
    def parse(*args, **kwargs):
        model_calls.append(kwargs["operation_id"])
        return VisionParseResult(
            endpoint_id="image-test", provider="vision-provider", model="vision-model",
            context="", usage={"provider": "vision-provider", "model": "vision-model",
                               "input_tokens": 12, "output_tokens": 8, "total_tokens": 20, "cost": "0"},
            latency_ms=1, content_hash="test",
            items=({"visible_text": "产品 PX-104，选配双电源", "tables": "|功率|10 W|",
                    "observations": "银色外观", "uncertainty": uncertainty},),
        )
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    attempts = []
    def index(**kwargs):
        attempts.append(kwargs["text"])
        if len(attempts) == 1:
            raise RuntimeError("模拟索引服务暂时不可用")
        return {"chunk_count": 1, "vector_ids": ["image-vector"]}
    monkeypatch.setattr(document_api, "run_knowledge_pipeline", index)
    output = BytesIO()
    Image.new("RGB", (80, 40), "white").save(output, "PNG")
    original = output.getvalue()
    created = _create_document(ctx.client, ctx.space["id"], ctx.headers, "产品图", original, "png")
    document_id = created["document"]["id"]
    first = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()["latest_version"]
    assert first["processing_stage"] == "failed" and not first["queryable"]
    preview = ctx.client.get(f"/documents/{document_id}/preview", headers=ctx.headers).json()
    assert preview["kind"] == "image" and preview["page_count"] == 1
    assert "PX-104" in preview["content"]
    if uncertainty:
        assert uncertainty in preview["content"]
    retried = ctx.client.post(
        f"/documents/{document_id}/versions/{first['id']}:retry", headers=ctx.headers,
    )
    assert retried.status_code == 200, retried.text
    current = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()["latest_version"]
    assert current["id"] != first["id"]
    assert current["queryable"] and current["extraction_method"] == "vision"
    assert current["ocr_page_count"] == 0 and current["vision_page_count"] == 1
    assert current["extraction_warning_code"] == warning
    assert len(model_calls) == 1 and attempts[0] == attempts[1]
    with transaction() as db:
        version = db.get(DocumentVersion, current["id"])
        initial = db.get(DocumentVersion, first["id"])
        assert Path(version.file_path).read_bytes() == original
        assert sum(call["total_tokens"] for call in json.loads(initial.vision_usage_json)["calls"]) == 20
        assert json.loads(version.vision_usage_json)["calls"] == []
        location = location_for_char_range(db, version.id, 0, len(attempts[-1]))
        assert location.support_state == support_state and location.page_number == 1
        assert location.bbox is None
    reader = _member_headers(ctx.client, ctx.space["id"], "image-reader", "reader")
    evidence = f"/documents/{document_id}/versions/{current['id']}/pages/1/image"
    shown = ctx.client.get(evidence, headers=reader)
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/png"
    assert Image.open(BytesIO(shown.content)).size == (80, 40)
    assert ctx.client.get(
        f"/documents/{document_id}/preview/file?version_id={current['id']}&page_number=2",
        headers=reader,
    ).status_code == 404
    other_space, _ = _create_space(ctx.client, "其他图片文件夹")
    outsider = _member_headers(ctx.client, other_space["id"], "image-outsider", "reader")
    assert ctx.client.get(evidence, headers=outsider).status_code == 404
    forged = ctx.client.post(
        f"/knowledge-spaces/{ctx.space['id']}/documents", headers=ctx.headers,
        files={"file": ("fake.jpg", original, "image/jpeg")},
    )
    assert forged.status_code == 422


def test_image_missing_model_preserves_original_without_claiming_no_text(
    multipart_documents, monkeypatch,
):
    from io import BytesIO
    from PIL import Image
    from app.services import minio_storage
    import app.api.documents as document_api

    ctx = multipart_documents
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(document_api, "resolve_vision_endpoint", lambda *_a, **_k: None)
    output = BytesIO()
    Image.new("RGB", (40, 40), "red").save(output, "BMP")
    created = _create_document(ctx.client, ctx.space["id"], ctx.headers, "离线原图", output.getvalue(), "bmp")
    document_id = created["document"]["id"]
    version = ctx.client.get(f"/documents/{document_id}", headers=ctx.headers).json()["latest_version"]
    assert not version["queryable"] and version["failure_code"] == "image_vision_endpoint_unavailable"
    preview = ctx.client.get(f"/documents/{document_id}/preview", headers=ctx.headers).json()
    assert preview["kind"] == "image" and preview["content"] is None
    image = ctx.client.get(
        f"/documents/{document_id}/preview/file?version_id={version['id']}", headers=ctx.headers,
    )
    assert image.status_code == 200
    assert Image.open(BytesIO(image.content)).getpixel((0, 0)) == (255, 0, 0)


def test_multipart_image_bytes_are_validated_before_publication(multipart_documents):
    ctx = multipart_documents
    created = ctx.client.post(
        f"/knowledge-spaces/{ctx.space['id']}/document-uploads", headers=ctx.headers,
        json={"client_upload_id": "forged-image", "filename": "forged.png",
              "size_bytes": len(ctx.payload), "sha256": hashlib.sha256(ctx.payload).hexdigest()},
    )
    assert created.status_code == 201
    upload_id = created.json()["id"]
    assert ctx.client.post(
        f"/document-uploads/{upload_id}/complete", headers=ctx.headers, json={},
    ).status_code == 200
    assert ctx.service.DocumentUploadDispatcher().run_once()
    observed = ctx.client.get(f"/document-uploads/{upload_id}", headers=ctx.headers).json()
    assert observed["status"] == "failed" and observed["result"] is None


@pytest.mark.parametrize("entrypoint", ["chat", "space"])
def test_temporary_document_upload_does_not_enable_image_ingestion(
    multipart_documents, monkeypatch, entrypoint,
):
    from io import BytesIO
    from PIL import Image
    from app.services import minio_storage
    import app.api.documents as document_api

    ctx = multipart_documents
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(document_api, "resolve_vision_endpoint", lambda *_a, **_k: None)
    image = BytesIO()
    Image.new("RGB", (40, 40), "white").save(image, "PNG")
    if entrypoint == "chat":
        session = ctx.client.post("/chat/sessions", headers=ctx.headers, json={"title": "临时边界"})
        assert session.status_code == 201
        session_id = session.json()["id"]
        endpoint = "/chat/attachments"
        data = {"chat_session_id": session_id}
        listing = f"/chat/attachments?chat_session_id={session_id}"
    else:
        endpoint = f"/knowledge-spaces/{ctx.space['id']}/documents"
        data = {"temporary": "true"}
        listing = endpoint
    uploaded = ctx.client.post(
        endpoint, headers=ctx.headers,
        files={"file": ("temporary-image.png", image.getvalue(), "image/png")}, data=data,
    )
    assert uploaded.status_code == 422, uploaded.text
    listed = ctx.client.get(listing, headers=ctx.headers)
    assert listed.status_code == 200
    assert listed.json()["items"] == []
