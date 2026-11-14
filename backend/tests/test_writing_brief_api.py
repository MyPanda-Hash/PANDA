from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from app.models.writing import WritingProject
from tests._auth import bootstrap_admin_response


def _headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_create_and_patch_brief_invalidates_pi_session(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "writing-brief.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            created = client.post(
                "/writing/projects",
                headers=headers,
                json={
                    "title": "方案草稿",
                    "space_ids": [],
                    "brief": {
                        "document_type": "proposal",
                        "purpose": "写一份实施计划",
                        "audience": "管理层",
                        "must_cover": ["里程碑", "风险"],
                    },
                },
            )
            assert created.status_code == 201
            project = created.json()
            assert project["brief"]["revision"] == 1
            assert project["brief"]["purpose"] == "写一份实施计划"
            assert project["brief"]["must_cover"] == ["里程碑", "风险"]

            with database.SessionLocal() as db:
                stored = db.get(WritingProject, project["id"])
                assert stored is not None
                stored.pi_agent_session_id = "old-session"
                stored.pi_session_stage = "outline"
                db.commit()

            patched = client.patch(
                f"/writing/projects/{project['id']}",
                headers=headers,
                json={"brief": {"purpose": "更新后的目标", "document_type": "proposal"}},
            )
            assert patched.status_code == 200
            assert patched.json()["brief"]["revision"] == 2
            assert patched.json()["brief"]["purpose"] == "更新后的目标"

            with database.SessionLocal() as db:
                stored = db.get(WritingProject, project["id"])
                assert stored is not None
                assert stored.pi_agent_session_id is None
                assert stored.pi_session_stage is None
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_changing_document_type_switches_supported_export_formats(tmp_path: Path):
    """A chat-seeded project has no document_type, so PPTX stays unavailable until
    the author picks a skill. The workspace sends the whole brief back because the
    API replaces it wholesale; that round trip must not drop writing requirements."""
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "writing-document-type.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            created = client.post(
                "/writing/projects",
                headers=headers,
                json={
                    "title": "聊天沉淀的稿子",
                    "space_ids": [],
                    "brief": {
                        "purpose": "把聊天里的内容整理成文稿",
                        "notes": "来自自由对话",
                        "must_cover": ["关键数字"],
                    },
                },
            )
            assert created.status_code == 201
            project = created.json()
            assert project["brief"]["document_type"] == ""
            assert project["supported_export_formats"] == ["docx"]

            brief = {
                key: value
                for key, value in project["brief"].items()
                if key != "revision"
            }
            patched = client.patch(
                f"/writing/projects/{project['id']}",
                headers=headers,
                json={"brief": {**brief, "document_type": "proposal"}},
            )
            assert patched.status_code == 200
            body = patched.json()
            assert body["supported_export_formats"] == ["docx", "pptx"]
            assert body["brief"]["document_type"] == "proposal"
            assert body["brief"]["purpose"] == "把聊天里的内容整理成文稿"
            assert body["brief"]["notes"] == "来自自由对话"
            assert body["brief"]["must_cover"] == ["关键数字"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)
