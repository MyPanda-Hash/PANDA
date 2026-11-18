from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.invite_origins import (
    invite_origin_payload,
    is_lan_origin,
    prefer_invite_origin,
)
from tests._auth import bootstrap_admin_response


def test_prefer_invite_origin_picks_lan_over_localhost():
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.88.100:5173",
    ]
    assert prefer_invite_origin(origins) == "http://192.168.88.100:5173"
    assert is_lan_origin("http://192.168.88.100:5173")
    assert not is_lan_origin("http://localhost:5173")
    payload = invite_origin_payload(
        "http://localhost:5173,http://192.168.1.20:5173"
    )
    assert payload["preferred"] == "http://192.168.1.20:5173"
    assert payload["lan_origins"] == ["http://192.168.1.20:5173"]


def test_admin_invite_origins_endpoint(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    old_origin = settings.frontend_origin
    settings.db_path = tmp_path / "invite-origins.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.frontend_origin = (
        "http://localhost:5173,http://10.0.0.8:5173,http://127.0.0.1:5173"
    )
    try:
        with TestClient(app) as client:
            login = bootstrap_admin_response(client)
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            response = client.get("/admin/users/invite-origins", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["preferred"] == "http://10.0.0.8:5173"
            assert "http://10.0.0.8:5173" in body["lan_origins"]
            assert "http://localhost:5173" in body["origins"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        settings.frontend_origin = old_origin
