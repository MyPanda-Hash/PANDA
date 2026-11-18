"""plain_llm：payload 组装、脱敏、空内容与超时归因。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import plain_llm


def _target(url: str = "https://api.deepseek.com/v1", provider: str = "deepseek", secret="sk-test"):
    endpoint = SimpleNamespace(
        id="mep_test",
        provider=provider,
        adapter="openai_compatible",
        api_protocol="chat_completions",
        endpoint_url=url,
        secret_ref=secret,
        timeout_ms=120000,
    )
    return SimpleNamespace(
        endpoint=endpoint,
        model="deepseek-flash",
        endpoint_url=url,
    )


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("POST", "https://example.com"), response=None
            )

    def json(self):
        return self._payload


def _install_transport(monkeypatch, captured, reply_text="ok"):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json, headers):  # noqa: A002
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": reply_text}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            )

    monkeypatch.setattr(plain_llm.httpx, "AsyncClient", FakeClient)


@pytest.mark.asyncio
async def test_json_mode_and_thinking_switch_are_sent_for_deepseek(monkeypatch):
    captured: dict = {}
    _install_transport(monkeypatch, captured)
    monkeypatch.setattr(plain_llm, "resolve_model_endpoint", lambda db, slot: _target())

    await plain_llm.complete_chat(
        None, [{"role": "user", "content": "hi"}],
        response_format_json=True, disable_thinking=True,
    )

    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_thinking_switch_is_not_sent_to_non_deepseek_providers(monkeypatch):
    captured: dict = {}
    _install_transport(monkeypatch, captured)
    monkeypatch.setattr(
        plain_llm, "resolve_model_endpoint",
        lambda db, slot: _target(url="https://api.openai.com/v1", provider="openai"),
    )

    await plain_llm.complete_chat(
        None, [{"role": "user", "content": "hi"}], disable_thinking=True
    )

    assert "thinking" not in captured["payload"]


@pytest.mark.asyncio
async def test_cloud_payload_is_masked_before_sending(monkeypatch):
    captured: dict = {}
    _install_transport(monkeypatch, captured, reply_text="好的")
    monkeypatch.setattr(plain_llm, "resolve_model_endpoint", lambda db, slot: _target())

    await plain_llm.complete_chat(
        None, [{"role": "user", "content": "客户电话 13800138000"}]
    )

    sent = json.dumps(captured["payload"], ensure_ascii=False)
    assert "13800138000" not in sent


@pytest.mark.asyncio
async def test_empty_content_raises_instead_of_parsing_garbage(monkeypatch):
    captured: dict = {}
    _install_transport(monkeypatch, captured, reply_text="")
    monkeypatch.setattr(plain_llm, "resolve_model_endpoint", lambda db, slot: _target())

    with pytest.raises(plain_llm.LlmCallFailed) as excinfo:
        await plain_llm.complete_chat(None, [{"role": "user", "content": "hi"}])

    assert "空" in str(excinfo.value)


@pytest.mark.asyncio
async def test_no_endpoint_raises_unavailable(monkeypatch):
    monkeypatch.setattr(plain_llm, "resolve_model_endpoint", lambda db, slot: None)

    with pytest.raises(plain_llm.LlmUnavailable):
        await plain_llm.complete_chat(None, [{"role": "user", "content": "hi"}])
