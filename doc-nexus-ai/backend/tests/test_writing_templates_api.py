"""Retirement boundaries for the former presentation-template library."""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from sqlalchemy import func, select

from app.core import database
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import now
from app.main import app
from app.models.artifacts import Artifact
from app.models.writing import WritingDocTemplate, WritingDocTemplateRevision, WritingOperation, WritingProject
from tests._auth import bootstrap_admin_response


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "retired-template-api.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr("app.services.minio_storage.load_minio_settings", lambda: None)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client)
            assert admin.status_code == 200, admin.text
            headers = {"Authorization": f"Bearer {admin.json()['access_token']}"}
            yield SimpleNamespace(client=client, headers=headers, owner_id=admin.json()["user"]["id"])
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


@pytest.mark.parametrize("method,path", [
    ("GET", "/writing/templates"),
    ("POST", "/writing/templates"),
    ("GET", "/writing/templates/capabilities"),
    ("GET", "/writing/templates/selectable"),
    ("GET", "/writing/templates/old"),
    ("PATCH", "/writing/templates/old"),
    ("POST", "/writing/templates/old/revisions"),
    ("POST", "/writing/templates/old/revisions/rev:publish"),
    ("POST", "/writing/templates/old/revisions/rev:retry"),
    ("GET", "/writing/templates/old/revisions/rev/previews/slide"),
    ("POST", "/writing/templates/chat-deck:render"),
    ("POST", "/writing/templates/chat-deck:render/status"),
])
def test_library_routes_are_removed(api_env, method, path):
    response = api_env.client.request(method, path, headers=api_env.headers)
    assert response.status_code == 404, response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WritingDocTemplate)) == 0
        assert db.scalar(select(func.count()).select_from(WritingDocTemplateRevision)) == 0


def _project(env):
    response = env.client.post("/writing/projects", headers=env.headers, json={
        "title": "普通演示稿", "space_ids": [], "brief": {"document_type": "proposal"},
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_new_template_bindings_and_export_selections_are_rejected(api_env):
    env = api_env
    rejected = env.client.post("/writing/projects", headers=env.headers, json={
        "title": "不能新建", "space_ids": [], "pptx_template_revision_id": "old-revision",
    })
    assert rejected.status_code == 422 and "演示模板库已停用" in rejected.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WritingProject)) == 0
    project_id = _project(env)
    for selection in (None, "old-revision"):
        response = env.client.patch(f"/writing/projects/{project_id}", headers=env.headers,
                                   json={"pptx_template_revision_id": selection})
        assert response.status_code == 422 and "演示模板库已停用" in response.text
    for action in ("exports:preflight", "exports"):
        for removed in ({"template": {"mode": "revision", "revision_id": "old-revision"}},
                        {"template": {"mode": "project"}}, {"presentation_style": "inspired"}):
            response = env.client.post(f"/writing/projects/{project_id}/{action}", headers=env.headers,
                                       json={"format": "pptx", **removed})
            assert response.status_code == 422 and "演示模板库已停用" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WritingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(Artifact)) == 0
        assert db.scalar(select(func.count()).select_from(WritingDocTemplate)) == 0


def test_historical_project_binding_is_preserved_but_ordinary_export_ignores_it(api_env):
    env = api_env
    project_id = _project(env)
    stamp = now()
    with SessionLocal() as db:
        db.add(WritingDocTemplate(id="old-template", name="历史模板", created_by_user_id=env.owner_id,
                                 create_idempotency_key="old", create_request_sha256="a" * 64,
                                 created_at=stamp, updated_at=stamp))
        db.flush()
        db.add(WritingDocTemplateRevision(id="old-revision", template_id="old-template", revision_no=1,
                                          created_by_user_id=env.owner_id, request_sha256="b" * 64,
                                          source_format="pptx", state="published",
                                          source_storage_key="retained-original", render_package_key="retained-package",
                                          created_at=stamp, updated_at=stamp))
        db.flush()
        project = db.get(WritingProject, project_id)
        assert project is not None
        project.pptx_template_revision_id = "old-revision"
        db.commit()
    saved = env.client.patch(f"/writing/projects/{project_id}", headers=env.headers, json={"title": "普通导出"})
    assert saved.status_code == 200
    assert saved.json()["pptx_template_revision_id"] == "old-revision"
    preflight = env.client.post(f"/writing/projects/{project_id}/exports:preflight", headers=env.headers,
                                json={"format": "pptx"})
    assert preflight.status_code == 200
    assert "resolved_template" not in preflight.json()
    stale = env.client.post(f"/writing/projects/{project_id}/exports", headers=env.headers,
                            json={"format": "pptx", "preflight_fingerprint": "retired-plan"})
    assert stale.status_code == 409
    exported = env.client.post(f"/writing/projects/{project_id}/exports", headers=env.headers,
                               json={"format": "pptx", "preflight_fingerprint": preflight.json()["preflight_fingerprint"]})
    assert exported.status_code == 201, exported.text
    artifact_id = exported.json()["result"]["artifact_id"]
    downloaded = env.client.post(f"/artifacts/{artifact_id}/download", headers=env.headers)
    assert downloaded.status_code == 200
    assert len(Presentation(BytesIO(downloaded.content)).slides) > 0
    with SessionLocal() as db:
        artifact = db.get(Artifact, artifact_id)
        assert artifact is not None
        manifest = json.loads(artifact.manifest_json)
        assert manifest["renderer_version"] == "presentation-deck/1"
        assert manifest["template"] == {"mode": "builtin"}
        assert artifact.checksum_sha256 == hashlib.sha256(downloaded.content).hexdigest()
        project = db.get(WritingProject, project_id)
        revision = db.get(WritingDocTemplateRevision, "old-revision")
        assert project is not None and revision is not None
        assert project.pptx_template_revision_id == "old-revision"
        assert revision.source_storage_key == "retained-original"
        # Historical template artifacts remain downloadable, but may not be regenerated.
        manifest["template"] = {"mode": "revision", "revision_id": "old-revision"}
        artifact.manifest_json = json.dumps(manifest)
        original = artifact.content
        db.commit()
    historical = env.client.get(f"/files/artifacts/{artifact_id}/historical.pptx", headers=env.headers)
    assert historical.status_code == 200 and historical.content == original
    with SessionLocal() as db:
        artifact = db.get(Artifact, artifact_id)
        assert artifact is not None
        artifact.status = "expired"
        operation_count = db.scalar(select(func.count()).select_from(WritingOperation))
        db.commit()
    refused = env.client.post(f"/artifacts/{artifact_id}:regenerate", headers=env.headers)
    assert refused.status_code == 410 and "演示模板库已停用" in refused.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WritingOperation)) == operation_count
        artifact = db.get(Artifact, artifact_id)
        assert artifact is not None and artifact.content == original
