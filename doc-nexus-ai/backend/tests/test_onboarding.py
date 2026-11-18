from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import admin_user
from app.core import database
from app.core.config import settings
from app.main import app
from app.models.auth import User
from app.repositories import system_config
from app.schemas.onboarding import OnboardingMessageResponse
from app.services import onboarding as onboarding_service


@pytest.fixture
def onboarding_admin(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    original_linkmind = settings.linkmind_enabled
    path = tmp_path / "onboarding.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_enabled = True
    database.reset_database(path)
    database.initialize_database()
    admin = User(id="usr-onboarding-admin", username="admin", role="admin", first_login=False)
    app.dependency_overrides[admin_user] = lambda: admin
    try:
        yield admin
    finally:
        app.dependency_overrides.pop(admin_user, None)
        settings.linkmind_enabled = original_linkmind
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_onboarding_status_and_completion_persist_identity(onboarding_admin):
    with TestClient(app) as client:
        initial = client.get("/admin/onboarding/status")
        assert initial.status_code == 200
        assert initial.json()["required"] is True

        completed = client.post(
            "/admin/onboarding/complete",
            json={"company_name": "缔智元科技", "persona_name": "知夏"},
        )
        assert completed.status_code == 200
        assert completed.json() == {
            "required": False,
            "completed": True,
            "company_name": "缔智元科技",
            "persona_name": "知夏",
        }

        status = client.get("/admin/onboarding/status")
        assert status.json()["completed"] is True

    with database.transaction() as db:
        stored = system_config.get_all(db)
    assert stored["app_name"] == "缔智元科技"
    assert stored["pi_persona_name"] == "知夏"
    assert stored["pi_persona_title"] == "缔智元科技数字员工"
    assert stored["onboarding_completed"] == "1"


def test_onboarding_message_returns_llm_decision(onboarding_admin):
    response = OnboardingMessageResponse(
        stage="company",
        decision="clarify",
        reply="这是品牌名还是团队名呢？",
    )
    with (
        patch("app.api.onboarding.resolve_model_endpoint", return_value=object()),
        patch("app.api.onboarding.onboarding.converse", return_value=response) as converse,
        TestClient(app) as client,
    ):
        result = client.post(
            "/admin/onboarding/message",
            json={
                "stage": "company",
                "message": "我们叫星河",
                "history": [{"role": "assistant", "content": "你的公司名是什么？"}],
            },
        )

    assert result.status_code == 200
    assert result.json()["decision"] == "clarify"
    converse.assert_called_once()
    assert converse.call_args.kwargs["user_id"] == onboarding_admin.id
    assert converse.call_args.kwargs["message"] == "我们叫星河"


def test_onboarding_rejects_unreasonable_final_identity(onboarding_admin):
    with TestClient(app) as client:
        result = client.post(
            "/admin/onboarding/complete",
            json={"company_name": "不知道", "persona_name": "随便"},
        )
    assert result.status_code == 422


def test_onboarding_persona_accepts_allowed_user_chosen_name():
    llm_reply = """{
        "decision": "clarify",
        "content_policy": "allowed",
        "reply": "这个名字不够专业，建议换一个。",
        "normalized_value": "酸菜",
        "suggestions": []
    }"""
    with patch(
        "app.services.onboarding._call_llm",
        return_value=(llm_reply, None),
    ):
        result = onboarding_service.converse(
            target=object(),
            user_id="usr-onboarding-admin",
            stage="persona",
            message="就叫酸菜",
            company_name="缔智元科技",
            history=[],
        )

    assert result.decision == "accept"
    assert result.normalized_value == "酸菜"
    assert result.reply == "好的，已经记下了。以后就叫我「酸菜」。"


def test_onboarding_persona_rejects_blocked_name_even_if_llm_accepts():
    llm_reply = """{
        "decision": "accept",
        "content_policy": "blocked",
        "reply": "好的，已经记录。",
        "normalized_value": "违规称呼",
        "suggestions": []
    }"""
    with patch(
        "app.services.onboarding._call_llm",
        return_value=(llm_reply, None),
    ):
        result = onboarding_service.converse(
            target=object(),
            user_id="usr-onboarding-admin",
            stage="persona",
            message="就叫这个",
            company_name="缔智元科技",
            history=[],
        )

    assert result.decision == "clarify"
    assert result.normalized_value == ""
    assert result.reply == "这个称呼不符合内容安全规定，请换一个。"
