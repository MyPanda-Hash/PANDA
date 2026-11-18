from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.main import app
from app.models.chat import ChatMessage
from app.models.documents import Document, DocumentVersion
from tests._auth import bootstrap_admin_response


def test_admin_data_audit_is_read_only_privileged_and_audited(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "admin-data-audit.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created_user = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "audit-target",
                    "display_name": "审计目标用户",
                    "password": "Audit-target-123",
                },
            )
            assert created_user.status_code == 201, created_user.text
            owner_id = created_user.json()["user_id"]
            login = client.post(
                "/auth/login",
                json={
                    "username": "audit-target",
                    "password": "Audit-target-123",
                },
            )
            assert login.status_code == 200, login.text
            user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            project = client.post(
                "/writing/projects",
                headers=user_headers,
                json={"title": "普通用户审计文稿", "space_ids": []},
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            outlined = client.put(
                f"/writing/projects/{project_id}/outline",
                headers=user_headers,
                json={"chapters": [{"title": "审计章节"}]},
            )
            assert outlined.status_code == 200, outlined.text
            chapter_id = outlined.json()["outline"]["chapters"][0]["id"]
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=user_headers,
                json={"content": "只读审计正文"},
            )
            assert saved.status_code == 201, saved.text

            session = client.post(
                "/chat/sessions",
                headers=user_headers,
                json={"title": "普通用户审计会话"},
            )
            assert session.status_code == 201, session.text
            session_id = session.json()["id"]
            messages = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=user_headers,
                json={
                    "replace": True,
                    "messages": [
                        {
                            "id": "audit-message-user",
                            "role": "user",
                            "content": "审计可见对话正文",
                        },
                        {
                            "id": "audit-message-assistant",
                            "role": "assistant",
                            "content": "审计回复",
                        },
                    ],
                },
            )
            assert messages.status_code == 200, messages.text
            with transaction() as db:
                assistant_message = db.scalar(
                    select(ChatMessage).where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "assistant",
                    )
                )
                assert assistant_message is not None
                assistant_message.thinking = "不得通过审计接口暴露的思考"
                assistant_message.parts_json = (
                    '[{"type":"tool","args":{"api_key":"secret-tool-key"}}]'
                )
                created_at = now()
                document = Document(
                    id="doc-admin-audit",
                    space_id=None,
                    category_id=None,
                    name="普通用户上传.pdf",
                    status="active",
                    is_temporary=True,
                    chat_session_id=session_id,
                    expires_at=None,
                    temporary_cleanup_at=None,
                    created_by_user_id=owner_id,
                    created_at=created_at,
                    updated_at=created_at,
                )
                version = DocumentVersion(
                    id="doc-version-admin-audit",
                    document_id=document.id,
                    version_number=1,
                    processing_stage="failed",
                    queryable=False,
                    retryable=True,
                    retry_of_version_id=None,
                    failure_code="parse_failed",
                    failure_message="文档解析失败",
                    source_relative_path="private/source.pdf",
                    file_path="/private/uploads/source.pdf",
                    ext_task_id="private-task-id",
                    chunk_count=0,
                    summary=None,
                    extraction_method=None,
                    page_count=3,
                    ocr_page_count=0,
                    vision_page_count=0,
                    vision_usage_json="{}",
                    extraction_warning_code=None,
                    is_current_published=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
                db.add_all([document, version])

            writing_list = client.get(
                "/admin/data-audit/writing-projects",
                headers=admin_headers,
                params={"owner_user_id": owner_id, "q": "审计文稿"},
            )
            assert writing_list.status_code == 200, writing_list.text
            assert writing_list.json()["total"] == 1
            assert writing_list.json()["items"][0]["chapter_count"] == 1
            writing_detail = client.get(
                f"/admin/data-audit/writing-projects/{project_id}",
                headers=admin_headers,
            )
            assert writing_detail.status_code == 200, writing_detail.text
            assert writing_detail.json()["chapters"][0]["content"] == "只读审计正文"

            chat_list = client.get(
                "/admin/data-audit/chat-sessions",
                headers=admin_headers,
                params={"owner_user_id": owner_id, "q": "审计会话"},
            )
            assert chat_list.status_code == 200, chat_list.text
            assert chat_list.json()["items"][0]["message_count"] == 2
            chat_detail = client.get(
                f"/admin/data-audit/chat-sessions/{session_id}",
                headers=admin_headers,
            )
            assert chat_detail.status_code == 200, chat_detail.text
            assert chat_detail.json()["messages"][0]["content"] == "审计可见对话正文"
            assert "不得通过审计接口暴露的思考" not in chat_detail.text
            assert "secret-tool-key" not in chat_detail.text

            uploads = client.get(
                "/admin/data-audit/uploads",
                headers=admin_headers,
                params={"owner_user_id": owner_id, "q": "上传"},
            )
            assert uploads.status_code == 200, uploads.text
            assert uploads.json()["total"] == 1
            latest = uploads.json()["items"][0]["latest_version"]
            assert latest["processing_stage"] == "failed"
            assert latest["failure_code"] == "parse_failed"
            assert "file_path" not in uploads.text
            assert "source_relative_path" not in uploads.text
            assert "ext_task_id" not in uploads.text

            forbidden_paths = [
                (
                    "/admin/data-audit/writing-projects",
                    {"owner_user_id": owner_id},
                ),
                (
                    f"/admin/data-audit/writing-projects/{project_id}",
                    None,
                ),
                (
                    "/admin/data-audit/chat-sessions",
                    {"owner_user_id": owner_id},
                ),
                (f"/admin/data-audit/chat-sessions/{session_id}", None),
                ("/admin/data-audit/uploads", {"owner_user_id": owner_id}),
            ]
            for endpoint, params in forbidden_paths:
                response = client.get(
                    endpoint,
                    headers=user_headers,
                    params=params,
                )
                assert response.status_code == 403, response.text

            events = client.get(
                "/admin/audit-events?limit=100",
                headers=admin_headers,
            )
            assert events.status_code == 200
            event_types = {item["event_type"] for item in events.json()["items"]}
            assert {
                "admin_writing_audit_list",
                "admin_writing_audit_detail",
                "admin_chat_audit_list",
                "admin_chat_audit_detail",
                "admin_upload_audit_list",
            } <= event_types
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)
