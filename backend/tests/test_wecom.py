from __future__ import annotations

import base64
import hashlib
import json
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

import app.services.wecom as wecom
from app.api import wecom as wecom_api
from app.api.wecom import _read_callback_body
from app.main import app
from app.services.wecom import (
    WeComAiBotConfig,
    WeComAiBotMessage,
    WeComConfig,
    WeComError,
    WeComMessage,
    WeComSignatureError,
    _decrypt_payload,
    _normalize_aibot_prompt,
    _text_chunks,
    aibot_callback_response,
    callback_signature,
    clear_access_token_cache,
    clear_aibot_processed_cache,
    clear_incoming_message_cache,
    decrypt_aibot_message,
    decrypt_callback_message,
    process_incoming_aibot_message,
    process_incoming_message,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_callback_body_stream_stops_when_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(wecom_api, "WECOM_MAX_CALLBACK_BYTES", 5)
    yielded: list[bytes] = []

    class ChunkedRequest:
        headers: dict[str, str] = {}

        async def stream(self):
            for chunk in (b"123", b"456", b"should-not-be-read"):
                yielded.append(chunk)
                yield chunk

    with pytest.raises(HTTPException) as caught:
        await _read_callback_body(cast(Any, ChunkedRequest()))

    assert getattr(caught.value, "status_code", None) == 413
    assert yielded == [b"123", b"456"]


def _config() -> WeComConfig:
    return WeComConfig(
        corp_id="ww-test-corp",
        agent_id=1000002,
        secret="wecom-secret",  # noqa: S106 - explicit fake test credential
        token="callback-token",  # noqa: S106 - explicit fake test credential
        encoding_aes_key=base64.b64encode(b"a" * 32).decode().rstrip("="),
    )


def _aibot_config() -> WeComAiBotConfig:
    return WeComAiBotConfig(
        aibot_id="aibot-1",
        token="aibot-token",  # noqa: S106 - explicit fake test credential
        encoding_aes_key=base64.b64encode(b"b" * 32).decode().rstrip("="),
    )


def _encrypt_payload(plaintext: str, *, encoding_aes_key: str, receive_id: str) -> str:
    key = base64.b64decode(f"{encoding_aes_key}=")
    message = plaintext.encode("utf-8")
    raw = (
        os.urandom(16) + len(message).to_bytes(4, "big") + message + receive_id.encode()
    )
    padder = PKCS7(256).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _encrypt(plaintext: str, config: WeComConfig) -> str:
    return _encrypt_payload(
        plaintext,
        encoding_aes_key=config.encoding_aes_key,
        receive_id=config.corp_id,
    )


def _callback_xml(config: WeComConfig) -> tuple[bytes, dict[str, str]]:
    inner = (
        "<xml>"
        "<ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[user-1]]></FromUserName>"
        "<CreateTime>1720000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[你好，翠花]]></Content>"
        "<MsgId>msg-1</MsgId>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    )
    encrypted = _encrypt(inner, config)
    timestamp, nonce = "1720000000", "nonce-1"
    return (
        f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode(),
        {
            "msg_signature": callback_signature(
                config.token, timestamp, nonce, encrypted
            ),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )


def _aibot_callback(
    config: WeComAiBotConfig,
) -> tuple[bytes, dict[str, str]]:
    inner = (
        "{"
        '"msgid":"bot-msg-1",'
        '"aibotid":"aibot-1",'
        '"chatid":"chat-1",'
        '"chattype":"group",'
        '"from":{"userid":"user-1"},'
        '"response_url":"https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",'
        '"msgtype":"text",'
        '"text":{"content":"@翠花 请总结这段内容"}'
        "}"
    )
    encrypted = _encrypt_payload(
        inner,
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    timestamp, nonce = "1720000000", "nonce-aibot"
    return (
        f'{{"encrypt":"{encrypted}"}}'.encode(),
        {
            "msg_signature": callback_signature(
                config.token, timestamp, nonce, encrypted
            ),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )


def _aibot_stream_callback(
    config: WeComAiBotConfig, *, stream_id: str
) -> tuple[bytes, dict[str, str]]:
    inner = json.dumps(
        {
            "msgid": "bot-msg-1",
            "aibotid": config.aibot_id,
            "chatid": "chat-1",
            "chattype": "group",
            "from": {"userid": "user-1"},
            "msgtype": "stream",
            "stream": {"id": stream_id},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encrypted = _encrypt_payload(
        inner,
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    timestamp, nonce = "1720000001", "nonce-aibot-stream"
    return (
        f'{{"encrypt":"{encrypted}"}}'.encode(),
        {
            "msg_signature": callback_signature(
                config.token, timestamp, nonce, encrypted
            ),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )


def _decode_aibot_response(response, config: WeComAiBotConfig) -> dict:
    envelope = response.json()
    assert set(envelope) == {"encrypt", "msgsignature", "timestamp", "nonce"}
    timestamp = str(envelope["timestamp"])
    assert envelope["msgsignature"] == callback_signature(
        config.token, timestamp, envelope["nonce"], envelope["encrypt"]
    )
    plaintext = _decrypt_payload(
        envelope["encrypt"],
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    payload = json.loads(plaintext)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.anyio
async def test_callback_get_returns_decrypted_echo():
    config = _config()
    encrypted = _encrypt("verify-me", config)
    timestamp, nonce = "1720000000", "nonce-echo"
    params = {
        "msg_signature": callback_signature(config.token, timestamp, nonce, encrypted),
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": encrypted,
    }
    with patch.object(wecom_api, "load_wecom_config", return_value=config):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/integrations/wecom/callback", params=params)
    assert response.status_code == 200
    assert response.text == "verify-me"


@pytest.mark.anyio
async def test_callback_rejects_invalid_signature():
    config = _config()
    encrypted = _encrypt("verify-me", config)
    with patch.object(wecom_api, "load_wecom_config", return_value=config):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/integrations/wecom/callback",
                params={
                    "msg_signature": "invalid",
                    "timestamp": "1720000000",
                    "nonce": "nonce-echo",
                    "echostr": encrypted,
                },
            )
    assert response.status_code == 403
    assert response.json()["detail"] == "企业微信回调校验失败"


@pytest.mark.anyio
async def test_callback_acknowledges_and_schedules_message_processing():
    config = _config()
    body, params = _callback_xml(config)
    processor = MagicMock()
    with (
        patch.object(wecom_api, "load_wecom_config", return_value=config),
        patch.object(wecom_api, "process_incoming_message", processor),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/integrations/wecom/callback", params=params, content=body
            )
    assert response.status_code == 200
    assert response.text == "success"
    processor.assert_called_once()
    message = processor.call_args.args[0]
    assert message.from_user_name == "user-1"
    assert processor.call_args.kwargs["config"] == config


def test_decrypt_callback_accepts_whitespace_in_encrypted_payload():
    config = _config()
    body, params = _callback_xml(config)
    encrypted = body.decode().split("<![CDATA[", 1)[1].split("]]>", 1)[0]
    wrapped = f"{encrypted[:64]}\n{encrypted[64:]}"
    wrapped_body = f"<xml><Encrypt><![CDATA[{wrapped}]]></Encrypt></xml>".encode()
    params["msg_signature"] = callback_signature(
        config.token, params["timestamp"], params["nonce"], wrapped
    )

    message = decrypt_callback_message(
        wrapped_body,
        config=config,
        msg_signature=params["msg_signature"],
        timestamp=params["timestamp"],
        nonce=params["nonce"],
    )

    assert message.content == "你好，翠花"


@pytest.mark.parametrize("content", ["你会做啥呢？", "你好啊", "你好"])
def test_decrypt_callback_handles_varied_chinese_message_lengths(content: str):
    config = _config()
    inner = (
        "<xml>"
        "<ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[user-1]]></FromUserName>"
        "<CreateTime>1720000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "<MsgId>msg-varied</MsgId>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    )
    encrypted = _encrypt(inner, config)
    timestamp, nonce = "1720000000", f"nonce-{len(content)}"
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()

    message = decrypt_callback_message(
        body,
        config=config,
        msg_signature=callback_signature(config.token, timestamp, nonce, encrypted),
        timestamp=timestamp,
        nonce=nonce,
    )

    assert message.content == content


@pytest.mark.anyio
async def test_aibot_callback_get_returns_decrypted_echo():
    config = _aibot_config()
    encrypted = _encrypt_payload(
        "verify-aibot",
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    timestamp, nonce = "1720000000", "nonce-aibot-echo"
    params = {
        "msg_signature": callback_signature(config.token, timestamp, nonce, encrypted),
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": encrypted,
    }
    with patch.object(wecom_api, "load_wecom_aibot_config", return_value=config):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/integrations/wecom/aibot/callback", params=params
            )
    assert response.status_code == 200
    assert response.text == "verify-aibot"


@pytest.mark.anyio
async def test_aibot_callback_acknowledges_group_mention():
    config = _aibot_config()
    body, params = _aibot_callback(config)
    processor = MagicMock()
    with (
        patch.object(wecom_api, "load_wecom_aibot_config", return_value=config),
        patch.object(wecom_api, "process_incoming_aibot_message", processor),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/integrations/wecom/aibot/callback",
                params=params,
                content=body,
            )
    assert response.status_code == 200
    payload = _decode_aibot_response(response, config)
    assert payload["msgtype"] == "stream"
    assert payload["stream"]["finish"] is False
    assert payload["stream"]["content"] == ""
    assert payload["stream"]["id"]
    processor.assert_called_once()
    message = processor.call_args.args[0]
    assert message.chat_type == "group"
    assert message.chat_id == "chat-1"
    assert processor.call_args.kwargs["config"] == config


@pytest.mark.anyio
async def test_aibot_stream_callback_returns_completed_state():
    config = _aibot_config()
    clear_aibot_processed_cache()
    body, params = _aibot_callback(config)
    processor = MagicMock()
    with (
        patch.object(wecom_api, "load_wecom_aibot_config", return_value=config),
        patch.object(wecom_api, "process_incoming_aibot_message", processor),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial_response = await client.post(
                "/integrations/wecom/aibot/callback",
                params=params,
                content=body,
            )
    initial_payload = _decode_aibot_response(initial_response, config)
    stream_id = initial_payload["stream"]["id"]
    message = processor.call_args.args[0]
    with patch(
        "app.services.wecom.answer_wecom_knowledge",
        return_value="已完成",
    ):
        process_incoming_aibot_message(message, config=config)

    stream_body, stream_params = _aibot_stream_callback(config, stream_id=stream_id)
    with patch.object(wecom_api, "load_wecom_aibot_config", return_value=config):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/integrations/wecom/aibot/callback",
                params=stream_params,
                content=stream_body,
            )

    assert response.status_code == 200
    assert _decode_aibot_response(response, config) == {
        "msgtype": "stream",
        "stream": {"id": stream_id, "finish": True, "content": "已完成"},
    }


def test_aibot_message_rejects_untrusted_response_url():
    config = _aibot_config()
    timestamp, nonce = "1720000000", "nonce-untrusted-url"
    plaintext = json.dumps(
        {
            "msgid": "bot-msg-2",
            "aibotid": config.aibot_id,
            "chatid": "chat-1",
            "chattype": "group",
            "from": {"userid": "user-1"},
            "response_url": "https://127.0.0.1/internal",
            "msgtype": "text",
            "text": {"content": "@翠花 请回答"},
        },
        ensure_ascii=False,
    )
    encrypted = _encrypt_payload(
        plaintext,
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    with pytest.raises(WeComSignatureError):
        decrypt_aibot_message(
            json.dumps({"encrypt": encrypted}).encode(),
            config=config,
            msg_signature=callback_signature(config.token, timestamp, nonce, encrypted),
            timestamp=timestamp,
            nonce=nonce,
        )


def test_aibot_prompt_removes_group_mention_only():
    message = WeComAiBotMessage(
        msg_id="msg-1",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="@翠花 请回答",
    )
    assert _normalize_aibot_prompt(message) == "请回答"


def test_aibot_prompt_ignores_group_message_without_mention():
    message = WeComAiBotMessage(
        msg_id="msg-2",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="普通群消息",
    )
    assert _normalize_aibot_prompt(message) == ""


class _FakeLocalPiClient:
    calls: list[dict] = []
    events: list[dict] = []
    reply = "翠花收到：可以继续。"
    error: BaseException | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).calls.append({"init": kwargs})

    async def chat_async(self, message, **kwargs):
        type(self).calls[-1].update(message=message, chat=kwargs)
        error = type(self).error
        if error is not None:
            raise error
        on_event = kwargs.get("on_event")
        if on_event is not None:
            for event in type(self).events:
                await on_event(event)
        return SimpleNamespace(reply=type(self).reply), object()


class _ForbiddenLegacyClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("legacy WeCom HTTP agent path was used")


def _reset_fake_pi() -> None:
    _FakeLocalPiClient.calls = []
    _FakeLocalPiClient.events = []
    _FakeLocalPiClient.reply = "翠花收到：可以继续。"
    _FakeLocalPiClient.error = None


def _forbid_legacy_agent(monkeypatch) -> None:
    def fail_config():
        raise AssertionError("legacy ChatAgentConfig path was used")

    monkeypatch.setattr(wecom, "load_chat_agent_config", fail_config, raising=False)
    monkeypatch.setattr(wecom.httpx, "Client", _ForbiddenLegacyClient)


def test_processes_text_with_local_pi_and_sends_wecom_message(monkeypatch):
    config = _config()
    message = WeComMessage(
        to_user_name=config.corp_id,
        from_user_name="user-1",
        create_time="1720000000",
        msg_type="text",
        content="请继续",
        msg_id="msg-1",
    )
    _reset_fake_pi()
    clear_incoming_message_cache()
    clear_access_token_cache()
    monkeypatch.setattr(wecom, "LocalPiAgentClient", _FakeLocalPiClient, raising=False)
    _forbid_legacy_agent(monkeypatch)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda _question: None)
    send = MagicMock()
    monkeypatch.setattr(wecom, "_send_text", send)

    process_incoming_message(message, config=config)

    assert len(_FakeLocalPiClient.calls) == 1
    call = _FakeLocalPiClient.calls[0]
    assert call["init"]["user_id"] == "doc-nexus-pi"
    assert call["message"] == "请继续"
    assert "capability_token" not in call["chat"]
    send.assert_called_once_with(
        config, to_user="user-1", content="翠花收到：可以继续。"
    )


def test_processes_aibot_mention_with_local_pi_deltas_and_replies_through_callback_refresh(
    monkeypatch,
):
    config = _aibot_config()
    message = WeComAiBotMessage(
        msg_id="bot-msg-1",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        msg_type="text",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        content="@翠花 请继续",
    )
    _reset_fake_pi()
    _FakeLocalPiClient.events = [
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "翠花收到："},
        },
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "可以继续。"},
        },
    ]
    _FakeLocalPiClient.reply = "翠花收到：可以继续。尾部"
    clear_aibot_processed_cache()
    initial, should_process = aibot_callback_response(message)
    assert should_process is True
    assert initial["msgtype"] == "stream"
    assert initial["stream"]["finish"] is False
    monkeypatch.setattr(wecom, "LocalPiAgentClient", _FakeLocalPiClient, raising=False)
    _forbid_legacy_agent(monkeypatch)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda _question: None)
    process_incoming_aibot_message(message, config=config)
    refresh = WeComAiBotMessage(
        msg_id=message.msg_id,
        aibot_id=message.aibot_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        from_user_id=message.from_user_id,
        msg_type="stream",
        stream_id=initial["stream"]["id"],
    )
    final, should_process = aibot_callback_response(refresh)

    assert len(_FakeLocalPiClient.calls) == 1
    call = _FakeLocalPiClient.calls[0]
    assert call["init"]["user_id"] == "doc-nexus-pi"
    assert call["message"] == "请继续"
    expected_session = (
        "wecom-pi-"
        + hashlib.sha256(b"wecom-aibot:aibot-1:group:chat-1").hexdigest()[:32]
    )
    assert call["chat"]["session_id"] == expected_session
    assert call["chat"]["on_event"] is not None
    assert should_process is False
    assert final["stream"]["id"] == initial["stream"]["id"]
    assert final["stream"]["finish"] is True
    assert final["stream"]["content"] == "翠花收到：可以继续。尾部"


def test_aibot_knowledge_question_avoids_agent_timeout_path():
    config = _aibot_config()
    message = WeComAiBotMessage(
        msg_id="bot-msg-knowledge",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="@翠花 分析一下公司的产品",
    )
    clear_aibot_processed_cache()
    initial, should_process = aibot_callback_response(message)
    assert should_process is True
    with (
        patch(
            "app.services.wecom.answer_wecom_knowledge",
            return_value="基于知识库的产品分析",
        ),
        patch("app.services.wecom._stream_cuihua_prompt") as agent,
    ):
        process_incoming_aibot_message(message, config=config)

    refresh = WeComAiBotMessage(
        msg_id=message.msg_id,
        aibot_id=message.aibot_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        from_user_id=message.from_user_id,
        msg_type="stream",
        stream_id=initial["stream"]["id"],
    )
    final, should_process = aibot_callback_response(refresh)
    agent.assert_not_called()
    assert should_process is False
    assert final["stream"]["content"] == "基于知识库的产品分析"
    assert final["stream"]["finish"] is True


def test_aibot_stream_response_renders_markdown_without_mutating_raw_state():
    markdown = """# 结论

**支持**、*稳定*、~~旧方案~~，使用 `nexus_search`。

- 第一项
1) 第二项
> 注意事项
[查看文档](https://example.com/docs)
| 字段 | 值 |
| --- | --- |
| 状态 | 正常 |
```python
def run(**kwargs):
    return "<ok>"
```
<span>完成</span>
"""
    state = wecom._AibotStreamState(msg_id="markdown", stream_id="stream-markdown")

    wecom._append_aibot_stream_content(state, markdown)
    payload = wecom._aibot_stream_response(state)

    assert state.content == markdown
    assert (
        payload["stream"]["content"]
        == """【结论】

支持、稳定、旧方案，使用 nexus_search。

• 第一项
1. 第二项
引用：注意事项
查看文档：https://example.com/docs
字段 ｜ 值
状态 ｜ 正常
【代码 · python】
def run(**kwargs):
    return "<ok>"
完成"""
    )


def test_aibot_stream_response_truncates_rendered_utf8_content():
    state = wecom._AibotStreamState(msg_id="large", stream_id="stream-large")
    wecom._append_aibot_stream_content(state, "- 翠花\n" * 4_000)

    content = wecom._aibot_stream_response(state)["stream"]["content"]

    assert content.startswith("• 翠花")
    assert len(content.encode("utf-8")) <= wecom.WECOM_AIBOT_STREAM_MAX_BYTES


def test_text_chunks_respect_wecom_utf8_limit():
    text = "翠花" * 2_000
    chunks = _text_chunks(text)
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= 2_048 for chunk in chunks)


def test_aibot_stream_without_pi_delta_uses_final_reply(monkeypatch):
    config = _aibot_config()
    message = WeComAiBotMessage(
        msg_id="bot-msg-stream-no-delta",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="@翠花 请继续",
    )
    _reset_fake_pi()
    _FakeLocalPiClient.reply = "最终回复"
    clear_aibot_processed_cache()
    initial, should_process = aibot_callback_response(message)
    assert should_process is True
    monkeypatch.setattr(wecom, "LocalPiAgentClient", _FakeLocalPiClient, raising=False)
    _forbid_legacy_agent(monkeypatch)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda _question: None)
    process_incoming_aibot_message(message, config=config)

    refresh = WeComAiBotMessage(
        msg_id=message.msg_id,
        aibot_id=message.aibot_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        from_user_id=message.from_user_id,
        msg_type="stream",
        stream_id=initial["stream"]["id"],
    )
    final, should_process = aibot_callback_response(refresh)
    assert should_process is False
    assert final["stream"]["finish"] is True
    assert final["stream"]["content"] == "最终回复"


def test_aibot_stream_failure_finishes_with_fallback(monkeypatch):
    config = _aibot_config()
    message = WeComAiBotMessage(
        msg_id="bot-msg-stream-failure",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="@翠花 请继续",
    )
    clear_aibot_processed_cache()
    initial, should_process = aibot_callback_response(message)
    assert should_process is True
    _reset_fake_pi()
    _FakeLocalPiClient.error = WeComError("upstream stream failed")
    monkeypatch.setattr(wecom, "LocalPiAgentClient", _FakeLocalPiClient, raising=False)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda _question: None)
    process_incoming_aibot_message(message, config=config)

    refresh = WeComAiBotMessage(
        msg_id=message.msg_id,
        aibot_id=message.aibot_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        from_user_id=message.from_user_id,
        msg_type="stream",
        stream_id=initial["stream"]["id"],
    )
    final, should_process = aibot_callback_response(refresh)
    assert should_process is False
    assert final["stream"]["finish"] is True
    assert final["stream"]["content"] == "翠花暂时无法回复，请稍后重试。"


def test_aibot_stream_success_is_deduplicated():
    config = _aibot_config()
    message = WeComAiBotMessage(
        msg_id="bot-msg-stream-dedup",
        aibot_id="aibot-1",
        chat_id="chat-1",
        chat_type="group",
        from_user_id="user-1",
        response_url="https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=test",
        msg_type="text",
        content="@翠花 请继续",
    )
    clear_aibot_processed_cache()
    initial, should_process = aibot_callback_response(message)
    assert should_process is True
    with patch(
        "app.services.wecom.answer_wecom_knowledge", return_value="已完成"
    ) as answer:
        process_incoming_aibot_message(message, config=config)
        process_incoming_aibot_message(message, config=config)

    refresh = WeComAiBotMessage(
        msg_id=message.msg_id,
        aibot_id=message.aibot_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        from_user_id=message.from_user_id,
        msg_type="stream",
        stream_id=initial["stream"]["id"],
    )
    final, should_process = aibot_callback_response(refresh)
    assert answer.call_count == 1
    assert should_process is False
    assert final["stream"]["content"] == "已完成"
    assert final["stream"]["finish"] is True
