from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.api.deps import admin_user
from app.core import database
from app.core.config import settings
from app.main import app
from app.models.auth import User


@pytest.fixture
def override_admin(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "provider-keys-test.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()

    mock_admin = User(
        id="usr-test-admin",
        username="admin",
        role="admin",
        first_login=False,
    )
    app.dependency_overrides[admin_user] = lambda: mock_admin
    yield mock_admin
    app.dependency_overrides.pop(admin_user, None)
    settings.db_path = original_path
    settings.database_url = original_url
    database.reset_database(path)


def test_provider_meta(override_admin):
    with TestClient(app) as client:
        resp = client.get("/admin/provider-keys/meta")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert data["linkmind_enabled"] is False
        provider_ids = [p["id"] for p in data["providers"]]
        assert "xiaomi" in provider_ids
        assert "deepseek" in provider_ids
        assert "ailibaba" in provider_ids
        assert "minimax" in provider_ids
        assert "moonshot" in provider_ids


def test_register_provider_key_requires_linkmind(override_admin):
    with TestClient(app) as client:
        resp = client.post(
            "/admin/provider-keys/register",
            json={
                "provider": "xiaomi",
                "apiKey": "test-key-123",
                "name": "xiaomi",
            },
        )
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_register_provider_key_success(override_admin):
    original_linkmind_enabled = settings.linkmind_enabled
    original_linkmind_base_url = settings.linkmind_base_url
    settings.linkmind_enabled = True
    settings.linkmind_base_url = "http://linkmind.example:8080"
    try:
        with (
            patch("app.api.provider_keys.httpx.AsyncClient") as mock_client_cls,
            patch("app.main.linkmind.sync_user_by_id"),
            patch("app.main.linkmind.ensure_user_key", return_value="bootstrap-key"),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = Response(200, json={"code": 0, "msg": "ok"})
            mock_client_cls.return_value = mock_client
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/provider-keys/register",
                    json={
                        "provider": "xiaomi",
                        "apiKey": "test-key-123",
                        "name": "xiaomi",
                    },
                )
                assert resp.status_code == 200
                assert mock_client.post.await_count == 1
                called_kwargs = mock_client.post.call_args.kwargs
                assert mock_client.post.call_args.args[0] == (
                    "http://linkmind.example:8080/apiKey/add"
                )
                assert called_kwargs["json"]["provider"] == "xiaomi"
                assert called_kwargs["json"]["apiKey"] == "test-key-123"
    finally:
        settings.linkmind_enabled = original_linkmind_enabled
        settings.linkmind_base_url = original_linkmind_base_url


@pytest.mark.asyncio
async def test_probe_and_setup_endpoints(override_admin):
    with patch("app.api.provider_keys.probe_default_endpoints", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = {
            "all_ready": True,
            "chat": {
                "reachable": True,
                "endpoint_url": "http://host.docker.internal:8080/v1",
                "model": "qwen2.5-7b",
            },
            "embedding": {
                "reachable": True,
                "endpoint_url": "http://host.docker.internal:8888",
                "model": "bge-m3",
            },
        }
        with TestClient(app) as client:
            resp = client.post("/admin/provider-keys/probe-and-setup")
            assert resp.status_code == 200
            data = resp.json()
            assert data["all_ready"] is True
            assert data["auto_provisioned"] is True
            assert data["chat"]["model"] == "qwen2.5-7b"
            assert data["embedding"]["model"] == "bge-m3"
            mock_probe.assert_awaited_once_with(override_admin.id)
