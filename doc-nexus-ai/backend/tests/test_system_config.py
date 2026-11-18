from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.system_config import _build_response
from app.core.assistant_identity import DEFAULT_COMPANY_NAME
from app.schemas.system_config import SystemConfigUpdateItem


def test_chat_group_exposes_welcome_suggestions():
    response = _build_response({})

    chat = next(group for group in response.groups if group.id == "chat")
    chat_by_key = {item.key: item for item in chat.items}
    assert chat_by_key["chat_welcome_suggestions"].multiline is True
    assert chat_by_key["chat_welcome_suggestions"].importance == "recommended"
    assert chat_by_key["chat_welcome_suggestions"].default_value == ""


def test_writing_group_exposes_backend_pi_fields():
    response = _build_response({})

    writing = next(group for group in response.groups if group.id == "writing")
    assert writing.label == "智能写作"
    by_key = {item.key: item for item in writing.items}
    assert "writing_provider" not in by_key
    assert "pi_writing_model" in by_key
    assert "pi_writing_default_model" in by_key
    assert by_key["writing_pi_auto_publish"].default_value == "0"


def test_persona_prompt_default_reflects_configured_identity():
    response = _build_response({})
    by_key = {item.key: item for item in response.items}
    assert by_key["app_name"].default_value == DEFAULT_COMPANY_NAME
    assert by_key["pi_persona_system_prompt"].value == ""
    assert by_key["pi_persona_system_prompt"].default_value.startswith(
        f"你叫「翠花」，是{DEFAULT_COMPANY_NAME}的数字员工。"
    )

    response = _build_response(
        {"app_name": "北京星河科技有限公司", "pi_persona_name": "小助理"}
    )
    prompt = {item.key: item for item in response.items}[
        "pi_persona_system_prompt"
    ].default_value
    assert prompt.startswith("你叫「小助理」，是北京星河科技有限公司的数字员工。")


def test_persona_prompt_allows_long_text():
    long_prompt = "x" * 5000
    item = SystemConfigUpdateItem(key="pi_persona_system_prompt", value=long_prompt)
    assert item.value == long_prompt


def test_other_config_rejects_text_over_default_limit():
    with pytest.raises(ValidationError):
        SystemConfigUpdateItem(key="chat_welcome_suggestions", value="x" * 2001)
