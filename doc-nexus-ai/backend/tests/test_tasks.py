from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.security import hash_password, now
from app.main import app
from app.models import (
    ChatMessage,
    ChatSession,
    Document,
    DocumentJob,
    DocumentVersion,
    ImageGenerationTask,
    User,
    VideoGenerationTask,
    WritingOperation,
    WritingProject,
)
from app.core.database import transaction
from app.repositories.tasks import _normalize_status


def _admin_headers(client: TestClient) -> dict[str, str]:
    password = "Admin-task-center-123"
    initialized = client.post(
        "/auth/setup",
        json={
            "new_password": password,
            "password_confirmation": password,
        },
    )
    assert initialized.status_code == 200, initialized.text
    return {"Authorization": f"Bearer {initialized.json()['access_token']}"}


def _seed_tasks() -> str:
    stamp = now()
    with transaction() as db:
        admin = db.scalar(
            __import__("sqlalchemy").select(User).where(
                User.username == settings.bootstrap_username
            )
        )
        assert admin
        bob = User(
            id="usr_task_bob",
            username="task-bob",
            email="task-bob@example.com",
            display_name="Bob",
            password_hash=hash_password("Task-bob-password-123"),
            role="user",
            status="active",
            first_login=False,
            created_at=stamp,
        )
        db.add(bob)
        db.flush()

        active_document = Document(
            id="doc_task_active",
            space_id=None,
            category_id=None,
            name="进行中文档",
            status="active",
            is_temporary=True,
            chat_session_id=None,
            expires_at=None,
            temporary_cleanup_at=None,
            created_by_user_id=admin.id,
            created_at=stamp,
            updated_at=stamp,
        )
        active_version = DocumentVersion(
            id="ver_task_active",
            document_id=active_document.id,
            version_number=1,
            processing_stage="parsing",
            queryable=False,
            retryable=False,
            is_current_published=False,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add_all([active_document, active_version])
        db.flush()
        db.add(
            DocumentJob(
                id="job_task_active",
                document_id=active_document.id,
                version_id=active_version.id,
                space_id=None,
                created_by_user_id=admin.id,
                kind="document_processing",
                status="running",
                created_at=stamp,
                updated_at=stamp,
            )
        )

        old_document = Document(
            id="doc_task_old",
            space_id=None,
            category_id=None,
            name="过期终态文档",
            status="active",
            is_temporary=True,
            chat_session_id=None,
            expires_at=None,
            temporary_cleanup_at=None,
            created_by_user_id=admin.id,
            created_at=stamp - timedelta(days=31),
            updated_at=stamp - timedelta(days=31),
        )
        old_version = DocumentVersion(
            id="ver_task_old",
            document_id=old_document.id,
            version_number=1,
            processing_stage="ready",
            queryable=True,
            retryable=False,
            is_current_published=True,
            created_at=stamp - timedelta(days=31),
            updated_at=stamp - timedelta(days=31),
        )
        db.add_all([old_document, old_version])
        db.flush()
        db.add(
            DocumentJob(
                id="job_task_old",
                document_id=old_document.id,
                version_id=old_version.id,
                space_id=None,
                created_by_user_id=admin.id,
                kind="document_processing",
                status="completed",
                created_at=stamp - timedelta(days=31),
                updated_at=stamp - timedelta(days=31),
            )
        )

        project = WritingProject(
            id="wproj_task",
            owner_user_id=admin.id,
            title="失败写作任务",
            selected_space_ids="[]",
            selected_document_ids="[]",
            brief_json="{}",
            brief_revision=1,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(project)
        db.flush()
        db.add(
            WritingOperation(
                id="wop_task_failed",
                project_id=project.id,
                kind="export",
                status="failed",
                idempotency_key=None,
                attempt=1,
                input_revision=None,
                input_payload="{}",
                error_code="export_failed",
                error_message_safe="导出失败，请重试",
                last_event_cursor=0,
                usage="{}",
                result="{}",
                created_at=stamp,
                started_at=stamp,
                completed_at=stamp,
            )
        )

        session = ChatSession(
            id="chat_task",
            owner_user_id=admin.id,
            title="生成任务",
            agent_session_id=None,
            preferred_model=None,
            category_id=None,
            pinned=False,
            writing_project_id=None,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(session)
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    id="msg_task_image_user",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="user",
                    content="图片",
                    created_at=stamp,
                ),
                ChatMessage(
                    id="msg_task_image_assistant",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="assistant",
                    content="图片完成",
                    created_at=stamp,
                ),
                ChatMessage(
                    id="msg_task_image_unknown_user",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="user",
                    content="再画一张",
                    created_at=stamp,
                ),
                ChatMessage(
                    id="msg_task_image_unknown_assistant",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="assistant",
                    content="图片结果未知",
                    created_at=stamp,
                ),
                ChatMessage(
                    id="msg_task_video_user",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="user",
                    content="视频",
                    created_at=stamp,
                ),
                ChatMessage(
                    id="msg_task_video_assistant",
                    session_id=session.id,
                    owner_user_id=admin.id,
                    role="assistant",
                    content="视频取消",
                    created_at=stamp,
                ),
            ]
        )
        db.flush()
        db.add(
            ImageGenerationTask(
                id="igt_task",
                owner_user_id=admin.id,
                chat_session_id=session.id,
                user_message_id="msg_task_image_user",
                assistant_message_id="msg_task_image_assistant",
                provider_task_id="provider-image-task",
                model="image-model",
                aspect_ratio="1:1",
                resolution="1k",
                prompt="图片",
                status="succeeded",
                image_url="https://example.com/image.png",
                error_message=None,
                created_at=stamp,
                updated_at=stamp,
                completed_at=stamp,
            )
        )
        db.add(
            ImageGenerationTask(
                id="igt_task_unknown",
                owner_user_id=admin.id,
                chat_session_id=session.id,
                user_message_id="msg_task_image_unknown_user",
                assistant_message_id="msg_task_image_unknown_assistant",
                provider_task_id=None,
                model="image-model",
                aspect_ratio="1:1",
                resolution="1k",
                prompt="再画一张",
                status="outcome_unknown",
                image_url=None,
                error_message="供应商结果未知，请勿重复提交。",
                created_at=stamp,
                updated_at=stamp,
                completed_at=None,
            )
        )
        db.add(
            VideoGenerationTask(
                id="vgt_task",
                owner_user_id=admin.id,
                chat_session_id=session.id,
                user_message_id="msg_task_video_user",
                assistant_message_id="msg_task_video_assistant",
                provider_task_id="provider-video-task",
                source_file_ref=None,
                model="video-model",
                mode="std",
                duration=5,
                prompt="视频",
                status="canceled",
                video_url=None,
                error_message="视频生成任务已取消。",
                created_at=stamp,
                updated_at=stamp,
                completed_at=stamp,
            )
        )

        bob_document = Document(
            id="doc_task_bob",
            space_id=None,
            category_id=None,
            name="Bob 的文档",
            status="active",
            is_temporary=True,
            chat_session_id=None,
            expires_at=None,
            temporary_cleanup_at=None,
            created_by_user_id=bob.id,
            created_at=stamp,
            updated_at=stamp,
        )
        bob_version = DocumentVersion(
            id="ver_task_bob",
            document_id=bob_document.id,
            version_number=1,
            processing_stage="ready",
            queryable=True,
            retryable=False,
            is_current_published=True,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add_all([bob_document, bob_version])
        db.flush()
        db.add(
            DocumentJob(
                id="job_task_bob",
                document_id=bob_document.id,
                version_id=bob_version.id,
                space_id=None,
                created_by_user_id=bob.id,
                kind="document_processing",
                status="completed",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        return admin.id


def test_task_projection_normalizes_sources_and_enforces_owner(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "tasks.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            owner_id = _seed_tasks()

            history = client.get("/tasks?view=history&limit=50", headers=headers)
            assert history.status_code == 200, history.text
            body = history.json()
            assert {item["source"] for item in body["items"]} == {
                "document",
                "writing",
                "image",
                "video",
            }
            assert all(item["owner_user_id"] == owner_id for item in body["items"])
            assert all("prompt" not in item for item in body["items"])

            attention = client.get("/tasks?view=attention", headers=headers)
            assert attention.status_code == 200, attention.text
            attention_body = attention.json()
            attention_ids = {item["id"] for item in attention_body["items"]}
            assert "document:job_task_active" in attention_ids
            assert "writing:wop_task_failed" in attention_ids
            assert "document:job_task_old" not in attention_ids
            assert "image:igt_task_unknown" in attention_ids
            unknown_item = next(
                item
                for item in attention_body["items"]
                if item["id"] == "image:igt_task_unknown"
            )
            # 结果未知不能再冒充「处理中」，否则任务永远无法从需关注里清除。
            assert unknown_item["status"] == "outcome_unknown"
            assert unknown_item["unread"] is True
            assert unknown_item["cancel_action"] is None
            assert unknown_item["retry_action"] is None
            assert attention_body["active_count"] == 1
            assert attention_body["unread_count"] == 3
            assert attention_body["attention_count"] == 5

            assert client.get("/tasks?view=invalid", headers=headers).status_code == 422
            assert (
                client.get(
                    "/tasks?view=history&kind=secret", headers=headers
                ).status_code
                == 422
            )
            assert (
                client.get("/tasks?view=history&offset=1&limit=1", headers=headers)
                .json()["items"]
            )

            ack = client.post(
                "/tasks/acknowledgements",
                headers=headers,
                json={"task_ids": ["writing:wop_task_failed"]},
            )
            assert ack.status_code == 200, ack.text
            after_ack = client.get("/tasks?view=attention", headers=headers).json()
            assert after_ack["unread_count"] == 2
            assert after_ack["attention_count"] == 4
            assert "writing:wop_task_failed" not in {
                item["id"] for item in after_ack["items"]
            }

            # 结果未知只能靠用户确认清除，确认后落到历史记录里可查。
            unknown_ack = client.post(
                "/tasks/acknowledgements",
                headers=headers,
                json={"task_ids": ["image:igt_task_unknown"]},
            )
            assert unknown_ack.status_code == 200, unknown_ack.text
            assert unknown_ack.json()["unread_count"] == 2
            after_unknown_ack = client.get(
                "/tasks?view=attention", headers=headers
            ).json()
            assert after_unknown_ack["attention_count"] == 3
            assert after_unknown_ack["active_count"] == 1
            assert "image:igt_task_unknown" not in {
                item["id"] for item in after_unknown_ack["items"]
            }
            history_ids = {
                item["id"]
                for item in client.get(
                    "/tasks?view=history&limit=50", headers=headers
                ).json()["items"]
            }
            assert "image:igt_task_unknown" in history_ids

            forged = client.post(
                "/tasks/acknowledgements",
                headers=headers,
                json={"task_ids": ["document:job_task_bob"]},
            )
            assert forged.status_code == 404
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_task_status_classifier_covers_canonical_states():
    assert _normalize_status("queued", source="writing") == "queued"
    assert _normalize_status("running", source="writing") == "running"
    assert _normalize_status("processing", source="writing") == "processing"
    assert _normalize_status("awaiting_user", source="writing") == "awaiting_user"
    assert _normalize_status("completed", source="writing") == "completed"
    assert _normalize_status("failed", source="writing") == "failed"
    assert _normalize_status("cancelled", source="writing") == "cancelled"
    assert _normalize_status("outcome_unknown", source="image") == "outcome_unknown"
    assert _normalize_status("outcome_unknown", source="video") == "outcome_unknown"
    assert _normalize_status("submitting", source="image") == "running"
    assert _normalize_status("skipped", source="schedule") == "skipped"
    assert _normalize_status("outcome_unknown", source="schedule") == "outcome_unknown"
