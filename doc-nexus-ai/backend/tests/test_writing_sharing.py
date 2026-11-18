import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.security import now as utcnow
from app.main import app
from app.models.writing import (
    AgentRunBinding,
    WritingOperation,
    WritingProjectPublication,
    WritingToolReceipt,
)
from tests._auth import bootstrap_admin_response


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member_login(client: TestClient, username: str) -> dict[str, str]:
    admin = _admin_headers(client)
    invited = client.post(
        "/admin/users",
        headers=admin,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": "Member-password-123",
            "password_confirmation": "Member-password-123",
        },
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login", json={"username": username, "password": "Member-password-123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_retired_writing_sharing_preserves_historical_snapshot_and_revoke(
    tmp_path: Path, seed_writing_publication,
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "writing-sharing.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            owner = _member_login(client, "writing-share-owner")
            other = _member_login(client, "writing-share-other")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "项目方案", "space_ids": []},
            )
            assert created.status_code == 201
            project_id = created.json()["id"]
            outlined = client.put(
                f"/writing/projects/{project_id}/outline",
                headers=owner,
                json={"chapters": [{"title": "正文"}]},
            )
            assert outlined.status_code == 200
            chapter = outlined.json()["outline"]["chapters"][0]

            empty = client.post(
                f"/writing/projects/{project_id}/publish",
                headers=owner,
                json={},
            )
            assert empty.status_code == 404

            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter['id']}",
                headers=owner,
                json={"content": "第一版正文"},
            )
            assert saved.status_code == 201

            first_id = seed_writing_publication(project_id)
            rejected = client.post(
                f"/writing/projects/{project_id}/publish", headers=owner, json={},
            )
            assert rejected.status_code == 404
            assert client.get("/writing/shared-projects", headers=other).status_code == 404
            with database.SessionLocal() as db:
                assert db.query(WritingProjectPublication).count() == 1
            detail = client.get(
                f"/writing/shared-projects/{first_id}", headers=other
            )
            assert detail.status_code == 200
            assert detail.json()["chapters"][0]["content"] == "第一版正文"

            saved_again = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter['id']}",
                headers=owner,
                json={"content": "第二版正文"},
            )
            assert saved_again.status_code == 201
            old_detail = client.get(
                f"/writing/shared-projects/{first_id}", headers=other
            )
            assert old_detail.json()["chapters"][0]["content"] == "第一版正文"

            forbidden = client.delete(
                f"/writing/shared-projects/{first_id}", headers=other
            )
            assert forbidden.status_code == 404
            revoked = client.delete(
                f"/writing/shared-projects/{first_id}", headers=owner
            )
            assert revoked.status_code == 204
            assert (
                client.get(
                    f"/writing/shared-projects/{first_id}", headers=other
                ).status_code
                == 404
            )

            # 撤销仅改变历史快照状态，不删除快照或原文稿。
            with database.SessionLocal() as db:
                assert db.get(WritingProjectPublication, first_id).status == "revoked"
            original = client.get(f"/writing/projects/{project_id}", headers=owner)
            assert original.status_code == 200
            assert original.json()["outline"]["chapters"][0]["content"] == "第二版正文"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_space_scoped_writing_publication_requires_current_membership(
    tmp_path: Path, seed_writing_publication,
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "writing-space-sharing.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "writing-space-owner")
            teammate = _member_login(client, "writing-space-teammate")
            outsider = _member_login(client, "writing-space-outsider")
            owner_id = client.get("/auth/me", headers=owner).json()["id"]
            teammate_id = client.get("/auth/me", headers=teammate).json()["id"]
            space = client.post(
                "/knowledge-spaces",
                headers=admin,
                json={"name": "投标协作空间", "description": ""},
            )
            assert space.status_code == 201
            space_id = space.json()["id"]
            for user_id in (owner_id, teammate_id):
                granted = client.post(
                    f"/knowledge-spaces/{space_id}/members",
                    headers=admin,
                    json={"user_id": user_id, "role": "reader"},
                )
                assert granted.status_code == 201

            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "团队投标方案", "space_ids": [space_id]},
            )
            assert project.status_code == 201
            project_id = project.json()["id"]
            outlined = client.put(
                f"/writing/projects/{project_id}/outline",
                headers=owner,
                json={"chapters": [{"title": "项目理解"}]},
            )
            chapter_id = outlined.json()["outline"]["chapters"][0]["id"]
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": "仅团队成员可见"},
            )
            assert saved.status_code == 201
            publication_id = seed_writing_publication(project_id, share_scope="space")
            teammate_detail = client.get(
                f"/writing/shared-projects/{publication_id}",
                headers=teammate,
            )
            assert teammate_detail.status_code == 200
            assert teammate_detail.json()["chapters"][0]["content"] == "仅团队成员可见"

            assert client.get("/writing/shared-projects", headers=outsider).status_code == 404
            outsider_detail = client.get(
                f"/writing/shared-projects/{publication_id}",
                headers=outsider,
            )
            assert outsider_detail.status_code == 404
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def _record_retrieved_citation(
    *, project_id: str, run_id: str, evidence: list[dict]
) -> None:
    """Insert one succeeded search_evidence run bound to ``project_id``."""
    stamp = utcnow()
    with database.SessionLocal() as db:
        db.add(
            WritingOperation(
                id=f"wop_{run_id}",
                project_id=project_id,
                kind="section",
                status="completed",
                idempotency_key=None,
                input_revision=None,
                input_payload="{}",
                error_code=None,
                error_message_safe=None,
                last_event_cursor=0,
                usage="{}",
                result="{}",
                created_at=stamp,
                started_at=stamp,
                completed_at=stamp,
            )
        )
        db.flush()
        db.add(
            AgentRunBinding(
                id=f"arb_{run_id}",
                writing_operation_id=f"wop_{run_id}",
                platform_run_id=run_id,
                team_id="team",
                team_version=1,
                status="completed",
                last_event_cursor=0,
                manifest_sha256=None,
                usage_summary="{}",
                created_at=stamp,
                finished_at=stamp,
            )
        )
        db.add(
            WritingToolReceipt(
                id=f"wtr_{run_id}",
                platform_run_id=run_id,
                tool_call_id=f"call_{run_id}",
                action_key=f"search_evidence:{run_id}",
                tool_name="search_evidence",
                request_sha256="0" * 64,
                status="succeeded",
                result_ref=None,
                result_sha256=None,
                result_json=json.dumps(
                    {"inline": {"evidence": evidence}},
                    ensure_ascii=False,
                ),
                attempt_count=1,
                error_code=None,
                created_at=stamp,
                completed_at=stamp,
            )
        )
        db.commit()


def test_writing_publication_freezes_verified_citations_and_never_drifts(
    tmp_path: Path, monkeypatch, seed_writing_publication,
):
    """历史引用集合与正文在后续检索、编辑后仍保持冻结。"""
    from app.models.documents import Document, DocumentVersion
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "writing-publication-freeze.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            owner = _member_login(client, "writing-freeze-owner")
            owner_id = client.get("/auth/me", headers=owner).json()["id"]
            admin = _admin_headers(client)
            space_id = client.post(
                "/knowledge-spaces", headers=admin,
                json={"name": "冻结引用资料", "description": ""},
            ).json()["id"]
            assert client.post(
                f"/knowledge-spaces/{space_id}/members", headers=admin,
                json={"user_id": owner_id, "role": "reader"},
            ).status_code == 201
            sources = []
            with database.SessionLocal() as db:
                stamp = utcnow()
                db.add(Document(
                    id="doc_publication_sources", name="生成时资料", space_id=space_id,
                    status="active", created_by_user_id=owner_id,
                    created_at=stamp, updated_at=stamp,
                ))
                for number, (version_id, text) in enumerate(
                    [("ver_frozen", "正式来源文本"), ("ver_later", "后续来源文本")], start=1,
                ):
                    digest = freeze_text(version_id, text)
                    sources.append({
                        "id": make_citation_id(version_id, digest, 0, len(text)),
                        "document_id": "doc_publication_sources",
                        "document_version_id": version_id, "text_sha256": digest,
                        "char_start": 0, "char_end": len(text), "excerpt": text,
                        "document_name": "生成时资料", "version_number": number,
                    })
                    db.add(DocumentVersion(
                        id=version_id, document_id="doc_publication_sources",
                        version_number=number, processing_stage="ready",
                        queryable=True, is_current_published=number == 1,
                        created_at=stamp, updated_at=stamp,
                    ))
                db.commit()
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "冻结引用集", "space_ids": [space_id]},
            )
            assert created.status_code == 201
            project_id = created.json()["id"]
            outlined = client.put(
                f"/writing/projects/{project_id}/outline",
                headers=owner,
                json={"chapters": [{"title": "正文"}]},
            )
            chapter_id = outlined.json()["outline"]["chapters"][0]["id"]

            cited, later = (source["id"] for source in sources)
            retrieved_only = {"id": "writing-evidence:ver_never_written:1-9"}
            _record_retrieved_citation(
                project_id=project_id,
                run_id="prun_freeze_1",
                evidence=[sources[0], retrieved_only],
            )
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": f"正文引用了 ^[{cited}]"},
            )
            assert saved.status_code == 201

            publication_id = seed_writing_publication(
                project_id, evidence_sources=[sources[0]],
            )

            with database.SessionLocal() as db:
                row = db.get(WritingProjectPublication, publication_id)
                frozen = json.loads(row.citation_refs_json)
                snapshot = json.loads(row.snapshot_json)
            assert frozen == {"items": [cited], "truncated": False}
            # The frozen set can never name a citation the stored snapshot omits.
            snapshot_text = "".join(
                chapter["content"] for chapter in snapshot["chapters"]
            )
            assert all(cid in snapshot_text for cid in frozen["items"])

            # A later run — and a later edit — must not move version 1.
            _record_retrieved_citation(
                project_id=project_id,
                run_id="prun_freeze_2",
                evidence=sources,
            )
            saved_again = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={
                    "content": (
                        f"正文引用了 ^[{cited}] 以及 ^[{later}]"
                    )
                },
            )
            assert saved_again.status_code == 201

            with database.SessionLocal() as db:
                again = db.get(WritingProjectPublication, publication_id)
                assert json.loads(again.citation_refs_json)["items"] == [cited]

            detail = client.get(
                f"/writing/shared-projects/{publication_id}", headers=owner,
            )
            assert detail.status_code == 200
            assert later not in detail.json()["chapters"][0]["content"]
            assert detail.json()["evidence_sources"] == [sources[0]]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)
