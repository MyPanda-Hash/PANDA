from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.main import app
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.schedules import Schedule, ScheduleDraft
from app.services.agent_capability import create_capability_token
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID
from app.services.schedule_skills import get_schedule_skill_application
from tests._auth import bootstrap_admin_response


def _capability_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }


def _schedule_config(*, kind: str = "reminder") -> dict:
    return {
        "name": "每日整理",
        "kind": kind,
        "instruction": "整理当天明确选择的资料并生成摘要"
        if kind == "skill"
        else "提醒我整理当天资料",
        "timezone": "Asia/Shanghai",
        "rule": {"kind": "daily", "time": "09:30"},
        "space_ids": [],
        "document_ids": [],
        "late_window_minutes": 60 if kind == "skill" else 15,
        "budget_tokens": 500000,
        "timeout_seconds": 600,
        "max_attempts": 3,
        "notification": {
            "sound": False,
            "desktop": False,
            "on_success": False,
            "on_failure": True,
        },
    }


def test_schedule_capability_persists_idempotent_draft_but_never_confirms(
    tmp_path: Path, monkeypatch
) -> None:
    old_path, old_url, old_runtime = (
        settings.db_path,
        settings.database_url,
        settings.pi_runtime_dir,
    )
    path = tmp_path / "schedule-proposals.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    settings.pi_runtime_dir = tmp_path / "pi-runtime"
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(
        settings, "agent_capability_secret", "schedule-capability-tests-secret"
    )
    try:
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            owner_id = login["user"]["id"]
            stamp = now()
            with transaction() as db:
                db.add(
                    ChatSession(
                        id="chs_schedule_proposal",
                        owner_user_id=owner_id,
                        title="定时整理",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.flush()
                db.add(
                    ChatMessage(
                        id="chm_schedule_proposal",
                        session_id="chs_schedule_proposal",
                        owner_user_id=owner_id,
                        role="user",
                        content="请每天九点半提醒我整理资料",
                        created_at=stamp,
                    )
                )
                db.add(
                    User(
                        id="schedule-other-user",
                        username="schedule-other-user",
                        display_name="另一名员工",
                        password_hash="unused",
                        role="user",
                        status="active",
                        first_login=False,
                        auth_version=0,
                        created_at=datetime.now(timezone.utc),
                    )
                )

            token = create_capability_token(
                user_id=owner_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="chs_schedule_proposal",
                history_turn_message_id="chm_schedule_proposal",
                personal_data_scope="own",
                allowed_tools=["propose_schedule"],
            )
            request = {
                "arguments": {
                    "capability_mode": "task_template",
                    "config": _schedule_config(),
                },
                "request_id": "proposal-call-1",
            }
            first = client.post(
                "/internal/v1/agent-capabilities/propose_schedule",
                headers=_capability_headers(token),
                json=request,
            )
            assert first.status_code == 200, first.text
            draft_id = first.json()["inline"]["draft"]["id"]
            assert first.json()["inline"]["confirmation_required"] is True
            assert first.json()["inline"]["draft"]["status"] == "draft"

            replay = client.post(
                "/internal/v1/agent-capabilities/propose_schedule",
                headers=_capability_headers(token),
                json={**request, "request_id": "proposal-call-replayed"},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["inline"]["draft"]["id"] == draft_id

            unbound_token = create_capability_token(
                user_id=owner_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                personal_data_scope="own",
                allowed_tools=["propose_schedule"],
            )
            unbound = client.post(
                "/internal/v1/agent-capabilities/propose_schedule",
                headers=_capability_headers(unbound_token),
                json={**request, "request_id": "unbound-attempt"},
            )
            assert unbound.status_code == 422
            assert "inline" not in unbound.json()

            foreign_token = create_capability_token(
                user_id="schedule-other-user",
                agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="chs_schedule_proposal",
                history_turn_message_id="chm_schedule_proposal",
                personal_data_scope="own",
                allowed_tools=["propose_schedule"],
            )
            denied = client.post(
                "/internal/v1/agent-capabilities/propose_schedule",
                headers=_capability_headers(foreign_token),
                json={**request, "request_id": "foreign-attempt"},
            )
            assert denied.status_code in {404, 422}
            assert "inline" not in denied.json()

            with transaction() as db:
                assert db.get(ScheduleDraft, draft_id) is not None
                assert db.query(Schedule).count() == 0
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url
        settings.pi_runtime_dir = old_runtime


def test_skill_request_replay_survives_mapping_failure_and_audit_delivery(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import schedule_skills
    from app.services.harness.skill_installation import SkillInstaller

    monkeypatch.setattr(settings, "pi_runtime_dir", tmp_path / "runtime")
    payload = {
        "owner_user_id": "request-owner",
        "source_session_id": "source-session",
        "source_message_id": "source-message",
        "request_key": "stable-request",
        "skill_id": "safe-digest",
        "version": "1.0.0",
        "description": "整理已授权资料",
        "instructions": "只读任务明确绑定的资料并输出摘要。",
        "files": [],
    }

    def fail_mapping(*args, **kwargs):
        raise OSError("模拟安装记录已保存、申请映射尚未保存时中断")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(schedule_skills, "_write_state", fail_mapping)
        with pytest.raises(OSError):
            schedule_skills.submit_schedule_skill_application(**payload)
    installer = SkillInstaller(
        settings.pi_runtime_dir / "skill-installations" / "_system"
    )
    original = installer.list()[0]
    installer.flush_pending_audits(lambda event: None)

    recovered = schedule_skills.submit_schedule_skill_application(**payload)
    assert recovered.install_request_id == original.request_id
    assert (
        schedule_skills.schedule_skill_application_view(recovered)["state"]
        == "awaiting_approval"
    )
    assert [record.request_id for record in installer.list()] == [original.request_id]
    with pytest.raises(schedule_skills.ScheduleSkillError):
        schedule_skills.submit_schedule_skill_application(
            **{**payload, "instructions": "同一申请不能替换为另一份候选。"}
        )


def test_new_skill_proposal_stops_at_approval_and_is_owner_projected(
    tmp_path: Path, monkeypatch
) -> None:
    old_path, old_url, old_runtime = (
        settings.db_path,
        settings.database_url,
        settings.pi_runtime_dir,
    )
    path = tmp_path / "schedule-skill-proposal.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    settings.pi_runtime_dir = tmp_path / "pi-runtime"
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(
        settings, "agent_capability_secret", "schedule-capability-tests-secret"
    )
    try:
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            owner_id = login["user"]["id"]
            auth_headers = {"Authorization": f"Bearer {login['access_token']}"}
            endpoint = client.post(
                "/admin/model-endpoints",
                headers=auth_headers,
                json={
                    "provider": "schedule-test",
                    "adapter": "openai_compatible",
                    "api_protocol": "chat_completions",
                    "model": "schedule-test-model",
                    "capability": "chat",
                    "endpoint_url": "http://localhost:9/v1/chat/completions",
                    "secret_ref": "test-only",
                    "allowed_data_level": "restricted",
                },
            )
            assert endpoint.status_code == 201, endpoint.text
            stamp = now()
            with transaction() as db:
                db.add(
                    ChatSession(
                        id="chs_new_schedule_skill",
                        owner_user_id=owner_id,
                        title="新能力",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.flush()
                db.add(
                    ChatMessage(
                        id="chm_new_schedule_skill",
                        session_id="chs_new_schedule_skill",
                        owner_user_id=owner_id,
                        role="user",
                        content="每天把指定资料按我的四段模板整理；没有能力就先申请创建",
                        created_at=stamp,
                    )
                )

            token = create_capability_token(
                user_id=owner_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="chs_new_schedule_skill",
                history_turn_message_id="chm_new_schedule_skill",
                personal_data_scope="own",
                allowed_tools=["propose_schedule"],
            )
            response = client.post(
                "/internal/v1/agent-capabilities/propose_schedule",
                headers=_capability_headers(token),
                json={
                    "request_id": "new-skill-call",
                    "arguments": {
                        "capability_mode": "new_skill",
                        "config": {
                            **_schedule_config(kind="skill"),
                            "model": "schedule-test-model",
                        },
                        "new_skill": {
                            "skill_id": "four-part-digest",
                            "version": "1.0.0",
                            "description": "按固定四段结构整理已授权资料",
                            "instructions": "仅处理任务绑定的资料，输出背景、要点、风险和后续四段摘要。",
                            "files": [],
                        },
                    },
                },
            )
            assert response.status_code == 200, response.text
            inline = response.json()["inline"]
            application = inline["skill_application"]
            assert application["state"] == "awaiting_approval"
            assert application["ready"] is False
            assert inline["draft"]["status"] == "waiting_skill"

            listed = client.get(
                "/schedule-proposals/skill-requests?session_id=chs_new_schedule_skill",
                headers=auth_headers,
            )
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()["items"]] == [
                application["id"]
            ]
            assert (
                get_schedule_skill_application(
                    application["id"], owner_user_id="another-owner"
                )
                is None
            )
            stored_application = get_schedule_skill_application(
                application["id"], owner_user_id=owner_id
            )
            approved = client.post(
                "/admin/harness/skills/install/_system/"
                f"{stored_application.install_request_id}/approve-enable",
                headers=auth_headers,
            )
            assert approved.status_code == 200, approved.text
            refreshed = client.post(
                f"/schedule-proposals/skill-requests/{application['id']}/refresh",
                headers=auth_headers,
            )
            assert refreshed.status_code == 200, refreshed.text
            ready = refreshed.json()
            assert ready["application"]["ready"] is True
            bound_draft = ready["drafts"][0]
            assert bound_draft["status"] == "draft", bound_draft
            assert bound_draft["config"]["skill_id"] == "four-part-digest"
            assert (
                bound_draft["config"]["skill_digest"]
                == ready["application"]["approved_digest"]
            )
            previewed = client.post(
                f"/schedule-drafts/{bound_draft['id']}/preview",
                headers=auth_headers,
                json={"expected_revision": bound_draft["revision"]},
            )
            assert previewed.status_code == 200, previewed.text
            assert previewed.json()["preview_token"]
            assert previewed.json()["blockers"] == []
            with transaction() as db:
                assert db.query(Schedule).count() == 0
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url
        settings.pi_runtime_dir = old_runtime
