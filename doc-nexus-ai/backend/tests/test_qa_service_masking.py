"""qa_service 出网前必须脱敏：这是上一版遗漏的第五条出网路径。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import qa_service


def _endpoint(url: str = "https://api.deepseek.com/v1") -> SimpleNamespace:
    return SimpleNamespace(
        id="mep_test",
        model="deepseek-chat",
        provider="deepseek",
        adapter="openai_compatible",
        api_protocol="chat_completions",
        endpoint_url=url,
        secret_ref=None,
    )


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "你是知识库问答助手。"},
        {
            "role": "user",
            "content": "客户张三手机 13800138000，合同金额 128.5 元，怎么处理？",
        },
    ]


def test_cloud_call_masks_pii_and_restores_answer(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):  # noqa: A002 - 与 httpx 签名对齐
        captured["payload"] = json
        # 让模型「复述」占位符，验证还原链路
        phone_token = "〔PHONE_1〕"
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": f"已记录 {phone_token} 的来电。"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(qa_service.httpx, "post", fake_post)

    answer, _usage = qa_service._call_llm(_endpoint(), _messages())

    sent = json.dumps(captured["payload"], ensure_ascii=False)
    assert "13800138000" not in sent
    assert "128.5" not in sent
    assert "PHONE_1" in sent or "PHONE_" in sent
    # 返回给用户的内容要还原成真实号码
    assert "13800138000" in answer
    assert "〔PHONE_1〕" not in answer


def test_local_endpoint_is_sent_verbatim(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured["payload"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    monkeypatch.setattr(qa_service.httpx, "post", fake_post)

    qa_service._call_llm(_endpoint("http://127.0.0.1:8001/v1"), _messages())

    sent = json.dumps(captured["payload"], ensure_ascii=False)
    # 回环地址视为可信，不脱敏（本地模型不需要）
    assert "13800138000" in sent


@pytest.mark.parametrize(
    ("url", "should_mask"),
    [
        ("https://api.openai.com/v1", True),
        ("http://10.0.0.8:8080/v1", False),  # 私网视为可信，按设计不脱敏
        ("http://127.0.0.1:8001/v1", False),
    ],
)
def test_mask_decision_follows_endpoint_trust(url, should_mask):
    from app.services import data_guard

    session = data_guard.session_for_endpoint(url)
    masked = session.mask_text("联系 13800138000")
    assert ("13800138000" not in masked) is should_mask
