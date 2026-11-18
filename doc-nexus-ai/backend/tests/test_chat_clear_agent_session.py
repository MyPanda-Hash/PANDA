from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from tests._auth import bootstrap_admin_response


def test_create_chat_session_rejects_client_runtime_session_id(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    database_path = tmp_path / "chat-clear-agent-session.sqlite3"
    settings.db_path = database_path
    settings.database_url = f"sqlite:///{database_path}"
    try:
        with TestClient(app) as client:
            login = bootstrap_admin_response(client)
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "停止中的对话", "agent_session_id": "trial-stale"},
            )
            assert created.status_code == 422
            assert "不能在创建会话时指定" in created.json()["detail"]
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(database_path)
