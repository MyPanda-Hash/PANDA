from __future__ import annotations

import json
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import files as files_api
from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.models import ChatMessage, ChatSession, User
from tests._auth import bootstrap_admin_response


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member_login(client: TestClient, username: str) -> tuple[dict[str, str], str]:
    invited = client.post(
        "/admin/users",
        headers=_admin_headers(client),
        json={"username": username, "email": f"{username}@example.com", "display_name": username},
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={"password": "Member-password-123", "password_confirmation": "Member-password-123"},
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login", json={"username": username, "password": "Member-password-123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, login.json()["user"]["id"]


def _export(client: TestClient, headers: dict[str, str], title: str, *, space_ids: list[str] | None = None) -> tuple[str, str]:
    project = client.post(
        "/writing/projects", headers=headers, json={"title": title, "space_ids": space_ids or []}
    )
    assert project.status_code == 201
    outline = client.put(
        f"/writing/projects/{project.json()['id']}/outline",
        headers=headers,
        json={"chapters": [{"title": "交付章节"}]},
    )
    assert outline.status_code == 200
    saved = client.put(
        f"/writing/projects/{project.json()['id']}/chapters/{outline.json()['outline']['chapters'][0]['id']}",
        headers=headers,
        json={"content": f"{title} 的不可变导出正文"},
    )
    assert saved.status_code == 201
    exported = client.post(f"/writing/projects/{project.json()['id']}/exports", headers=headers)
    assert exported.status_code == 201
    return project.json()["id"], exported.json()["id"]


def _isolated_client(tmp_path: Path, name: str) -> tuple[Path, str, Path]:
    original_path, original_url = settings.db_path, settings.database_url
    database_path = tmp_path / name
    settings.db_path, settings.database_url = database_path, f"sqlite:///{database_path}"
    return original_path, original_url, database_path


def _restore_database(original_path: Path, original_url: str, database_path: Path) -> None:
    settings.db_path, settings.database_url = original_path, original_url
    database.reset_database(database_path)


def test_artifact_trash_restore_preserves_bytes_without_republishing(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "artifact-trash.sqlite3")
    try:
        with TestClient(app) as client:
            owner, _ = _member_login(client, "trash-owner")
            other, _ = _member_login(client, "trash-other")
            project_id, _ = _export(client, owner, "可恢复成果")
            artifact = client.get("/artifacts", headers=owner).json()["items"][0]
            path = f"/artifacts/{artifact['id']}"
            original = client.post(f"{path}/download", headers=owner).content
            assert client.delete(f"{path}/permanent", headers=owner).status_code == 409
            renamed = client.patch(path, headers=owner, json={"name": "新名称"})
            assert renamed.status_code == 200
            assert renamed.json()["name"] == "新名称.docx"
            assert client.patch(path, headers=owner, json={"name": "../unsafe"}).status_code == 422
            assert client.patch(path, headers=other, json={"name": "越权"}).status_code == 404
            share = client.post(f"{path}/share", headers=owner, json={"share_scope": "all_internal"}).json()
            token = share["share_path"].split("share=")[1]
            metadata = f"/artifacts/shared/{token}"
            href = client.get(metadata, headers=other).json()["href"].removeprefix("/api")
            assert client.get(href, headers=other).content == original
            assert client.delete(path, headers=other).status_code == 404
            assert client.delete(path, headers=owner).status_code == 204
            assert client.get("/artifacts", headers=owner).json()["items"] == []
            trashed = client.get("/artifacts?view=trash", headers=owner).json()["items"][0]
            assert trashed["deleted_at"] and trashed["share_path"] is None
            assert client.get("/artifacts?view=trash", headers=other).json()["items"] == []
            assert client.post(f"{path}/download", headers=owner).status_code == 404
            assert client.get(f"/files/artifacts/{artifact['id']}/file.docx?preview=true", headers=owner).status_code == 404
            assert client.get(metadata, headers=other).status_code == 404
            assert client.get(href, headers=other).status_code == 404
            assert client.post(f"{path}/restore", headers=other).status_code == 404
            restored = client.post(f"{path}/restore", headers=owner)
            assert restored.status_code == 200
            assert restored.json()["share_path"] is None
            assert client.post(f"{path}/download", headers=owner).content == original
            assert client.get(metadata, headers=other).status_code == 404
            assert client.get(f"/writing/projects/{project_id}", headers=owner).status_code == 200
            assert client.delete(path, headers=owner).status_code == 204
            assert client.delete(f"{path}/permanent", headers=other).status_code == 404
            assert client.delete(f"{path}/permanent", headers=owner).status_code == 204
            assert client.post(f"{path}/restore", headers=owner).status_code == 404
            assert client.get(f"/writing/projects/{project_id}", headers=owner).status_code == 200
    finally:
        _restore_database(original_path, original_url, database_path)


def test_artifact_space_share_rechecks_membership_and_owner_status(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "artifact-share.sqlite3")
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner, owner_id = _member_login(client, "share-owner")
            reader, reader_id = _member_login(client, "share-reader")
            outsider, _ = _member_login(client, "share-outsider")
            space_id = client.post("/knowledge-spaces", headers=admin, json={"name": "成果协作空间", "description": ""}).json()["id"]
            for user_id in (owner_id, reader_id):
                assert client.post(f"/knowledge-spaces/{space_id}/members", headers=admin, json={"user_id": user_id, "role": "reader"}).status_code == 201
            _export(client, owner, "团队成果", space_ids=[space_id])
            artifact = client.get("/artifacts", headers=owner).json()["items"][0]
            path = f"/artifacts/{artifact['id']}"
            share = client.post(f"{path}/share", headers=owner, json={"share_scope": "space"})
            assert share.status_code == 200
            token = share.json()["share_path"].split("share=")[1]
            metadata = f"/artifacts/shared/{token}"
            href = client.get(metadata, headers=reader).json()["href"].removeprefix("/api")
            client.cookies.clear()
            assert client.get(metadata).status_code == 401
            assert client.get(href).status_code == 401
            assert client.get(metadata, headers=outsider).status_code == 404
            assert client.get(href, headers=outsider).status_code == 404
            assert client.get(f"{href}?preview=true", headers=reader).status_code == 200
            assert client.delete(f"/knowledge-spaces/{space_id}/members/{reader_id}", headers=admin).status_code == 204
            assert client.get(metadata, headers=reader).status_code == 404
            assert client.get(href, headers=reader).status_code == 404
            assert client.post(f"/knowledge-spaces/{space_id}/members", headers=admin, json={"user_id": reader_id, "role": "reader"}).status_code == 201
            assert client.get(metadata, headers=reader).status_code == 200
            with transaction() as db:
                db.execute(text("UPDATE users SET status = 'disabled' WHERE id = :id"), {"id": owner_id})
            assert client.get(metadata, headers=reader).status_code == 404
            assert client.get(href, headers=reader).status_code == 404
    finally:
        _restore_database(original_path, original_url, database_path)


def test_artifact_batch_download_is_atomic_and_regeneration_revokes_share(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "artifact-batch.sqlite3")
    try:
        with TestClient(app) as client:
            owner, _ = _member_login(client, "batch-owner")
            other, _ = _member_login(client, "batch-other")
            _export(client, owner, "同名")
            _export(client, owner, "同名")
            _export(client, other, "不可泄露")
            owned = client.get("/artifacts", headers=owner).json()["items"]
            foreign = client.get("/artifacts", headers=other).json()["items"][0]["id"]
            ids = [item["id"] for item in owned]
            batch = client.post("/artifacts:download", headers=owner, json={"artifact_ids": [*ids, ids[0]]})
            assert batch.status_code == 200
            with ZipFile(io.BytesIO(batch.content)) as archive:
                assert archive.namelist() == ["01-同名.docx", "02-同名.docx"]
                assert hashlib.sha256(archive.read("01-同名.docx")).hexdigest() == owned[0]["checksum_sha256"]
            assert client.post("/artifacts:download", headers=owner, json={"artifact_ids": [ids[0], foreign]}).status_code == 404
            assert client.post("/artifacts:download", headers=owner, json={"artifact_ids": []}).status_code == 422
            path = f"/artifacts/{ids[0]}"
            assert client.post(f"{path}/share", headers=owner, json={"share_scope": "space"}).status_code == 422
            share = client.post(f"{path}/share", headers=owner, json={"share_scope": "all_internal"}).json()
            token = share["share_path"].split("share=")[1]
            with transaction() as db:
                db.execute(text("UPDATE artifacts SET status = 'expired' WHERE id = :id"), {"id": ids[0]})
            regenerated = client.post(f"{path}:regenerate", headers=owner)
            assert regenerated.status_code == 200
            assert regenerated.json()["share_path"] is None
            assert client.get(f"/artifacts/shared/{token}", headers=other).status_code == 404
    finally:
        _restore_database(original_path, original_url, database_path)


def test_usage_operations_are_owner_scoped_and_never_project_question_or_body(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "usage-owner.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, _ = _member_login(client, "usage-owner")
            other_headers, _ = _member_login(client, "usage-other")
            owner_project_id, owner_operation_id = _export(client, owner_headers, "仅所有者可见的方案正文")
            _, other_operation_id = _export(client, other_headers, "其他人的私密方案")

            listed = client.get(
                "/usage/operations",
                params={"feature": "writing", "status": "completed", "model": "deterministic_writing_fixture"},
                headers=owner_headers,
            )

            assert listed.status_code == 200
            payload = listed.json()
            exports = [item for item in payload["items"] if item["action"] == "export"]
            assert [item["id"] for item in exports] == [owner_operation_id]
            assert exports[0]["resource_path"] == f"/writing/{owner_project_id}"
            assert "question" not in exports[0]
            assert "body" not in exports[0]
            assert "仅所有者可见的方案正文" not in listed.text
            assert other_operation_id not in listed.text

            detail = client.get(f"/usage/operations/{owner_operation_id}", headers=owner_headers)
            assert detail.status_code == 200
            assert detail.json()["usage_events"][0]["model"] == "deterministic_writing_fixture"
            assert "仅所有者可见的方案正文" not in detail.text
            assert client.get(f"/usage/operations/{owner_operation_id}", headers=other_headers).status_code == 404
            assert client.get(f"/usage/operations/{owner_operation_id}", headers=_admin_headers(client)).status_code == 404
    finally:
        _restore_database(original_path, original_url, database_path)


def test_cancelled_operation_keeps_its_actual_usage(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "usage-cancelled.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, _ = _member_login(client, "usage-cancelled")
            _, operation_id = _export(client, owner_headers, "已取消但已产生用量")
            actual_usage = {
                "provider": "model-a",
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "cost": "0.018",
                "latency_ms": 432,
            }
            with transaction() as db:
                db.execute(
                    text("UPDATE writing_operations SET status = 'stopped', usage = :usage WHERE id = :id"),
                    {"id": operation_id, "usage": json.dumps(actual_usage)},
                )

            detail = client.get(f"/usage/operations/{operation_id}", headers=owner_headers)

            assert detail.status_code == 200
            payload = detail.json()
            assert payload["status"] == "cancelled"
            assert payload["usage"]["total_tokens"] == 150
            assert payload["usage"]["cost"] == "0.018"
            assert payload["usage_events"] == [
                {
                    "provider": "model-a",
                    "model": "model-a",
                    "purpose": "export",
                    "status": "cancelled",
                    "input_tokens": 120,
                    "input_uncached_tokens": 120,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "cost": "0.018",
                    "latency_ms": 432,
                }
            ]
    finally:
        _restore_database(original_path, original_url, database_path)


def test_artifact_download_reads_an_immutable_persisted_snapshot(
    tmp_path: Path, monkeypatch
):
    original_path, original_url, database_path = _isolated_client(tmp_path, "artifact-download.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner_id = _member_login(client, "artifact-owner")
            other_headers, _ = _member_login(client, "artifact-other")
            project_id, _ = _export(client, owner_headers, "可下载的正式导出")

            listed = client.get("/artifacts", headers=owner_headers)

            assert listed.status_code == 200
            artifact = listed.json()["items"][0]
            assert artifact["resource_path"] == f"/writing/{project_id}"
            assert artifact["status"] == "ready"
            assert "url" not in artifact
            assert "download" not in artifact
            assert artifact["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            assert artifact["content_size"] > 0
            assert len(artifact["checksum_sha256"]) == 64
            assert len(artifact["source_version_ids"]) == 1
            detail = client.get(f"/artifacts/{artifact['id']}", headers=owner_headers)
            assert detail.status_code == 200
            assert detail.json()["id"] == artifact["id"]
            assert "url" not in detail.text
            assert client.get(f"/artifacts/{artifact['id']}", headers=other_headers).status_code == 404

            download = client.post(f"/artifacts/{artifact['id']}/download", headers=owner_headers)
            assert download.status_code == 200
            assert download.headers["content-type"].startswith(artifact["content_type"])
            assert "attachment" in download.headers["content-disposition"]
            assert len(download.content) == artifact["content_size"]
            assert hashlib.sha256(download.content).hexdigest() == artifact["checksum_sha256"]
            with ZipFile(io.BytesIO(download.content)) as document:
                rendered = document.read("word/document.xml").decode()
            assert "可下载的正式导出 的不可变导出正文" in rendered

            project = client.get(f"/writing/projects/{project_id}", headers=owner_headers).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            assert client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner_headers,
                json={"content": "导出后新保存的正文不得改变既有 Artifact"},
            ).status_code == 201
            repeat_download = client.post(f"/artifacts/{artifact['id']}/download", headers=owner_headers)
            assert repeat_download.status_code == 200
            assert repeat_download.content == download.content
            preview_url = f"/files/artifacts/{artifact['id']}/export.docx?preview=true"
            preview = client.get(preview_url, headers=owner_headers)
            assert preview.status_code == 200
            assert "可下载的正式导出 的不可变导出正文" in preview.json()["content"]
            assert "导出后新保存的正文" not in preview.json()["content"]
            assert client.get(preview_url, headers=other_headers).status_code == 404

            monkeypatch.setattr(
                files_api,
                "render_office_preview_pdf",
                lambda content, filename: b"%PDF-1.7 rendered export",
            )
            rendered = client.get(
                f"/files/artifacts/{artifact['id']}/export.docx?preview=pdf",
                headers=owner_headers,
            )
            assert rendered.status_code == 200
            assert rendered.headers["content-type"] == "application/pdf"
            assert rendered.content == b"%PDF-1.7 rendered export"
            assert client.get(
                f"/files/artifacts/{artifact['id']}/export.docx?preview=pdf",
                headers=other_headers,
            ).status_code == 404

            with transaction() as db:
                db.execute(text("UPDATE artifacts SET checksum_sha256 = :checksum WHERE id = :id"), {"checksum": "0" * 64, "id": artifact["id"]})
            corrupted = client.post(f"/artifacts/{artifact['id']}/download", headers=owner_headers)
            assert corrupted.status_code == 409
            assert client.get(preview_url, headers=owner_headers).status_code == 409

            with transaction() as db:
                db.execute(text("UPDATE users SET status = 'disabled' WHERE id = :id"), {"id": owner_id})
            revoked = client.post(f"/artifacts/{artifact['id']}/download", headers=owner_headers)
            assert revoked.status_code == 401
            assert "不可变导出正文" not in revoked.text
            assert client.get(preview_url, headers=owner_headers).status_code == 401
    finally:
        _restore_database(original_path, original_url, database_path)


def test_expired_artifact_regeneration_finishes_with_current_project_snapshot(
    tmp_path: Path,
):
    original_path, original_url, database_path = _isolated_client(
        tmp_path, "artifact-regeneration.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, _ = _member_login(client, "artifact-regeneration-owner")
            project_id, _ = _export(client, owner_headers, "需要再生成的交付稿")
            artifact = client.get("/artifacts", headers=owner_headers).json()["items"][0]
            original_checksum = artifact["checksum_sha256"]
            project = client.get(
                f"/writing/projects/{project_id}", headers=owner_headers
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner_headers,
                json={"content": "重新生成后必须交付这段最新正文"},
            )
            assert saved.status_code == 201
            with transaction() as db:
                db.execute(
                    text("UPDATE artifacts SET status = 'expired' WHERE id = :id"),
                    {"id": artifact["id"]},
                )

            regenerated = client.post(
                f"/artifacts/{artifact['id']}:regenerate", headers=owner_headers
            )

            assert regenerated.status_code == 200, regenerated.text
            body = regenerated.json()
            assert body["id"] == artifact["id"]
            assert body["status"] == "ready"
            assert body["checksum_sha256"] != original_checksum
            assert body["manifest"]["content_sha256"] == body["checksum_sha256"]
            download = client.post(
                f"/artifacts/{artifact['id']}/download", headers=owner_headers
            )
            assert download.status_code == 200
            with ZipFile(io.BytesIO(download.content)) as document:
                rendered = document.read("word/document.xml").decode()
            assert "重新生成后必须交付这段最新正文" in rendered
    finally:
        _restore_database(original_path, original_url, database_path)


def test_usage_date_filter_accepts_naive_bounds_with_aware_created_at():
    from app.repositories.usage import OperationReadModel, _matches

    date_from = datetime(2026, 8, 25)
    date_to = datetime(2026, 8, 27)
    kwargs = dict(feature=None, model=None, status=None, date_from=date_from, date_to=date_to)

    pg_item = OperationReadModel(
        id="op_pg",
        owner_user_id="user_1",
        feature="writing",
        action="export",
        status="completed",
        usage={},
        resource_path="/writing/p1",
        created_at=datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=None,
    )
    sqlite_item = OperationReadModel(
        id="op_sqlite",
        owner_user_id="user_1",
        feature="writing",
        action="export",
        status="completed",
        usage={},
        resource_path="/writing/p1",
        created_at=datetime(2026, 8, 26, 12, 0, 0),
        completed_at=None,
    )
    end_of_range_item = OperationReadModel(
        id="op_end",
        owner_user_id="user_1",
        feature="writing",
        action="export",
        status="completed",
        usage={},
        resource_path="/writing/p1",
        created_at=datetime(2026, 8, 27, 23, 59, 0, tzinfo=timezone.utc),
        completed_at=None,
    )

    assert _matches(pg_item, **kwargs)
    assert _matches(sqlite_item, **kwargs)
    assert _matches(end_of_range_item, **kwargs)
    assert not _matches(
        pg_item,
        feature=None,
        model=None,
        status=None,
        date_from=datetime(2026, 8, 28),
        date_to=None,
    )


def test_admin_usage_summary_aggregates_all_users_and_requires_permission(tmp_path: Path):
    original_path, original_url, database_path = _isolated_client(tmp_path, "admin-usage-summary.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner_id = _member_login(client, "usage-summary-owner")
            other_headers, other_id = _member_login(client, "usage-summary-other")
            _, owner_operation_id = _export(client, owner_headers, "管理员可见用量 A")
            _, other_operation_id = _export(client, other_headers, "管理员可见用量 B")

            with transaction() as db:
                db.execute(
                    text("UPDATE writing_operations SET usage = :usage WHERE id = :id"),
                    {
                        "id": owner_operation_id,
                        "usage": json.dumps(
                            {
                                "provider": "model-a",
                                "input_tokens": 100,
                                "output_tokens": 40,
                                "total_tokens": 140,
                                "cost": "0.014",
                                "latency_ms": 200,
                            }
                        ),
                    },
                )
                db.execute(
                    text("UPDATE writing_operations SET usage = :usage WHERE id = :id"),
                    {
                        "id": other_operation_id,
                        "usage": json.dumps(
                            {
                                "provider": "model-b",
                                "input_tokens": 50,
                                "output_tokens": 10,
                                "total_tokens": 60,
                                "cost": "0.006",
                                "latency_ms": 100,
                            }
                        ),
                    },
                )

            forbidden = client.get("/admin/usage/summary", headers=owner_headers)
            assert forbidden.status_code == 403

            summary = client.get("/admin/usage/summary", headers=_admin_headers(client))
            assert summary.status_code == 200
            payload = summary.json()
            assert payload["total_tokens"] >= 200
            assert payload["users_with_usage"] >= 2
            assert "管理员可见用量 A" not in summary.text
            assert "管理员可见用量 B" not in summary.text

            by_user = {item["user_id"]: item for item in payload["items"]}
            assert by_user[owner_id]["total_tokens"] == 140
            assert by_user[owner_id]["input_tokens"] == 100
            assert by_user[owner_id]["output_tokens"] == 40
            assert by_user[owner_id]["by_feature"]["writing"] == 140
            assert by_user[owner_id]["by_model"]["model-a"] == 140
            assert by_user[owner_id]["by_model_detail"]["model-a"]["total_tokens"] == 140
            assert by_user[other_id]["total_tokens"] == 60
            assert by_user[other_id]["by_model"]["model-b"] == 60
            assert float(payload["total_cost"]) >= 0.02

            by_model = {m["model"]: m for m in payload["by_model"]}
            assert by_model["model-a"]["total_tokens"] == 140
            assert by_model["model-a"]["operation_count"] == 1
            assert by_model["model-b"]["total_tokens"] == 60
            assert by_model["model-b"]["operation_count"] == 1

            # Non-real fixture providers must not appear in by_model.
            for forbidden in ("deterministic_metadata", "deterministic_writing_fixture", "none", "unknown"):
                assert forbidden not in by_model, f"fixture model '{forbidden}' leaked into by_model"
                for item in payload["items"]:
                    assert forbidden not in (item.get("by_model") or {}), f"fixture '{forbidden}' leaked into user by_model"

            date_filtered = client.get(
                "/admin/usage/summary",
                params={"date_from": "2026-08-25", "date_to": "2026-08-27"},
                headers=_admin_headers(client),
            )
            assert date_filtered.status_code == 200

            writing_only = client.get(
                "/admin/usage/summary",
                params={"feature": "writing", "include_zero": "false"},
                headers=_admin_headers(client),
            )
            assert writing_only.status_code == 200
            writing_payload = writing_only.json()
            assert all(item["total_tokens"] > 0 or item["operation_count"] > 0 for item in writing_payload["items"])
            assert all(
                "writing" in item["by_feature"]
                for item in writing_payload["items"]
                if item["user_id"] in {owner_id, other_id}
            )
    finally:
        _restore_database(original_path, original_url, database_path)


def test_admin_usage_summary_expands_chat_vision_calls_without_double_counting_turns(
    tmp_path: Path,
):
    original_path, original_url, database_path = _isolated_client(
        tmp_path, "usage-vision-calls.sqlite3"
    )
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            with transaction() as db:
                admin = db.query(User).filter(User.username == settings.bootstrap_username).one()
                stamp = datetime.now(timezone.utc)
                session = ChatSession(
                    id="usage-vision-session",
                    owner_user_id=admin.id,
                    title="视觉用量测试",
                    created_at=stamp,
                    updated_at=stamp,
                )
                db.add(session)
                db.add(
                    ChatMessage(
                        id="usage-vision-message",
                        session_id=session.id,
                        owner_user_id=admin.id,
                        role="assistant",
                        content="不得出现在聚合响应中的回答正文",
                        usage=json.dumps(
                            {
                                "provider": "answer-provider",
                                "model": "answer-model",
                                "input_tokens": 20,
                                "output_tokens": 8,
                                "total_tokens": 43,
                                "cost": "0.028",
                                "latency_ms": 100,
                                "route_mode": "vision_fallback",
                                "vision_fallback_used": True,
                                "calls": [
                                    {
                                        "purpose": "vision_parse",
                                        "provider": "vision-provider",
                                        "model": "vision-model",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "total_tokens": 15,
                                        "cost": "0.005",
                                        "latency_ms": 20,
                                        "status": "completed",
                                    },
                                    {
                                        "purpose": "answer_generation",
                                        "provider": "answer-provider",
                                        "model": "answer-model",
                                        "input_tokens": 20,
                                        "output_tokens": 8,
                                        "total_tokens": 28,
                                        "cost": "0.023",
                                        "latency_ms": 80,
                                        "status": "completed",
                                    },
                                ],
                            },
                            ensure_ascii=False,
                        ),
                        created_at=stamp,
                    )
                )

            response = client.get("/admin/usage/summary", headers=headers)
            assert response.status_code == 200, response.text
            payload = response.json()
            item = next(entry for entry in payload["items"] if entry["user_id"] == admin.id)
            assert item["operation_count"] == 1
            assert item["total_tokens"] == 43
            assert item["by_model"]["vision-model"] == 15
            assert item["by_model"]["answer-model"] == 28
            assert float(item["cost"]) == 0.028
            by_model = {entry["model"]: entry for entry in payload["by_model"]}
            assert by_model["vision-model"]["total_tokens"] == 15
            assert by_model["answer-model"]["total_tokens"] == 28
            assert "不得出现在聚合响应中的回答正文" not in response.text

            filtered = client.get(
                "/usage/operations",
                params={"model": "vision-model"},
                headers=headers,
            )
            assert filtered.status_code == 200
            assert [entry["id"] for entry in filtered.json()["items"]] == [
                "usage-vision-message"
            ]
    finally:
        _restore_database(original_path, original_url, database_path)
