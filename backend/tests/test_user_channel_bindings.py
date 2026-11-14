"""Security boundaries at real Open and signed WeCom ingress routes."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from xml.sax.saxutils import escape
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import open_chat, user_channel_bindings, wecom as wecom_api
from app.core import database
from app.core.config import settings
from app.core.security import create_access_token, now
from app.models.auth import User, UserApiKey
from app.models.user_channel_bindings import UserChannelBindingChallenge
from app.services import user_memory as memory_service
from app.models.user_memory import UserMemoryJob, UserMemoryTurn
from app.repositories import user_channel_bindings as repo
from app.services import agent_capability, wecom


@pytest.fixture
def channel_client(tmp_path, monkeypatch):
    original = settings.db_path, settings.database_url
    path = tmp_path / "channel-memory.sqlite3"
    database.reset_database(path)
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "personal_memory_enabled", True)
    database.initialize_database()
    with database.transaction() as db:
        for owner in ("employee-a", "employee-b"):
            db.add(
                User(
                    id=owner,
                    username=owner,
                    display_name="同名员工",
                    password_hash="unused",
                    api_key=f"dnx_{owner}",
                    first_login=False,
                    status="active",
                    created_at=now(),
                )
            )
    application = FastAPI()
    application.include_router(user_channel_bindings.router)
    application.include_router(open_chat.router)
    application.include_router(wecom_api.router)
    config = wecom.WeComAiBotConfig(
        aibot_id="verified-bot",
        token="fixture-only-callback-token",
        encoding_aes_key=base64.b64encode(b"x" * 32).decode().rstrip("="),
    )
    monkeypatch.setattr(
        user_channel_bindings, "load_wecom_aibot_config", lambda: config
    )
    monkeypatch.setattr(
        user_channel_bindings,
        "load_wecom_config",
        lambda: wecom.WeComConfig("", 0, "", "", ""),
    )
    monkeypatch.setattr(wecom_api, "load_wecom_aibot_config", lambda: config)
    wecom.clear_aibot_processed_cache()
    wecom.clear_incoming_message_cache()
    try:
        with TestClient(application) as client:
            yield client, config
    finally:
        wecom.clear_aibot_processed_cache()
        wecom.clear_incoming_message_cache()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _headers(owner="employee-a"):
    return {"Authorization": f"Bearer {create_access_token(owner, 'user')}"}


def _challenge(client, owner="employee-a"):
    response = client.post(
        "/me/channel-bindings/wecom/challenges",
        headers=_headers(owner),
        json={"channel_kind": "aibot"},
    )
    assert response.status_code == 200
    return response.json()["code"]


def _signed_bot(
    config,
    *,
    content="",
    event="event-1",
    sender="external-1",
    chat_type="single",
    stream_id=None,
):
    payload = {
        "msgid": event,
        "aibotid": config.aibot_id,
        "chattype": chat_type,
        "from": {"userid": sender},
        "msgtype": "stream" if stream_id else "text",
    }
    if chat_type == "group":
        payload["chatid"] = "group-1"
    if stream_id:
        payload["stream"] = {"id": stream_id}
    else:
        payload["response_url"] = (
            "https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=fixture"
        )
        payload["text"] = {"content": content}
    encrypted = wecom._encrypt_payload(
        json.dumps(payload, ensure_ascii=False),
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    timestamp, nonce = str(int(now().timestamp())), "fixture-nonce"
    return json.dumps({"encrypt": encrypted}).encode(), {
        "timestamp": timestamp,
        "nonce": nonce,
        "msg_signature": wecom.callback_signature(
            config.token, timestamp, nonce, encrypted
        ),
    }


def _post_bot(client, config, **kwargs):
    body, params = _signed_bot(config, **kwargs)
    response = client.post(
        "/integrations/wecom/aibot/callback", content=body, params=params
    )
    assert response.status_code == 200
    return json.loads(
        wecom._decrypt_payload(
            response.json()["encrypt"],
            encoding_aes_key=config.encoding_aes_key,
            receive_id="",
        )
    )


def test_signed_binding_command_is_private_channel_scoped_and_never_reaches_pi(
    channel_client, monkeypatch
):
    client, config = channel_client
    code = _challenge(client)

    def forbidden(*args, **kwargs):
        pytest.fail("binding command reached a knowledge/model path")

    monkeypatch.setattr(wecom, "answer_wecom_knowledge", forbidden)
    monkeypatch.setattr(wecom, "LocalPiAgentClient", forbidden)
    _post_bot(
        client,
        config,
        content=f"/绑定 {code}",
        chat_type="group",
        event="group-command",
    )
    assert client.get("/me/channel-bindings", headers=_headers()).json()["items"] == []
    other_bot = replace(config, aibot_id="other-verified-bot")
    monkeypatch.setattr(wecom_api, "load_wecom_aibot_config", lambda: other_bot)
    _post_bot(client, other_bot, content=f"/绑定 {code}", event="wrong-bot-command")
    assert client.get("/me/channel-bindings", headers=_headers()).json()["items"] == []
    monkeypatch.setattr(wecom_api, "load_wecom_aibot_config", lambda: config)
    _post_bot(client, config, content=f"/绑定 {code}", event="private-command")
    binding = client.get("/me/channel-bindings", headers=_headers()).json()["items"][0]
    assert binding["status"] == "active"
    assert "external-1" not in json.dumps(binding)
    assert (
        client.get("/me/channel-bindings", headers=_headers("employee-b")).json()[
            "items"
        ]
        == []
    )
    denied = client.request(
        "DELETE",
        f"/me/channel-bindings/{binding['id']}",
        headers=_headers("employee-b"),
        json={"expected_version": binding["version"]},
    )
    assert denied.status_code == 404
    with database.transaction() as db:
        stored = db.scalar(select(UserChannelBindingChallenge))
        assert stored is not None
        assert stored.used_at is not None and stored.token_hash != code
        assert list(db.scalars(select(UserMemoryTurn))) == []


def test_challenge_expiry_single_use_and_durable_attempt_limit(channel_client):
    client, config = channel_client
    key = repo.verified_channel_key("aibot", config.aibot_id)
    code = _challenge(client)
    with database.transaction() as db:
        challenge = db.scalar(select(UserChannelBindingChallenge))
        assert challenge is not None
        challenge.expires_at = now() - timedelta(seconds=1)
    with database.transaction() as db:
        with pytest.raises(repo.ChannelBindingError) as expired:
            repo.consume_challenge(
                db,
                code=code,
                channel_kind="aibot",
                verified_channel_key=key,
                external_user_id="expired-sender",
            )
        assert expired.value.code == "binding_challenge_invalid"
    code = _challenge(client)
    with database.transaction() as db:
        repo.consume_challenge(
            db,
            code=code,
            channel_kind="aibot",
            verified_channel_key=key,
            external_user_id="single-use",
        )
    with database.transaction() as db:
        with pytest.raises(repo.ChannelBindingError) as replay:
            repo.consume_challenge(
                db,
                code=code,
                channel_kind="aibot",
                verified_channel_key=key,
                external_user_id="another-sender",
            )
        assert replay.value.code == "binding_challenge_invalid"
    for _ in range(10):
        with database.transaction() as db:
            with pytest.raises(repo.ChannelBindingError) as invalid:
                repo.consume_challenge(
                    db,
                    code="invalid",
                    channel_kind="aibot",
                    verified_channel_key=key,
                    external_user_id="rate-limited",
                )
            assert invalid.value.code == "binding_challenge_invalid"
    with database.transaction() as db:
        with pytest.raises(repo.ChannelBindingError) as limited:
            repo.consume_challenge(
                db,
                code=code,
                channel_kind="aibot",
                verified_channel_key=key,
                external_user_id="rate-limited",
            )
        assert limited.value.status_code == 429
    for _ in range(3):
        _challenge(client)
    too_many = client.post(
        "/me/channel-bindings/wecom/challenges",
        headers=_headers(),
        json={"channel_kind": "aibot"},
    )
    assert too_many.status_code == 429


def test_rebinding_fences_old_grant_turn_and_stream_history(
    channel_client, monkeypatch
):
    client, config = channel_client
    code = _challenge(client)
    _post_bot(client, config, content=f"/绑定 {code}", event="bind-first")
    first = client.get("/me/channel-bindings", headers=_headers()).json()["items"][0]
    calls = []

    class Pi:
        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(reply="仅第一位员工可见的回复"), None

    monkeypatch.setattr(wecom, "LocalPiAgentClient", Pi)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda question: None)
    initial = _post_bot(client, config, content="请记住我叫李梅", event="memory-first")
    visible = _post_bot(
        client, config, event="refresh-first", stream_id=initial["stream"]["id"]
    )
    assert "仅第一位员工可见" in visible["stream"]["content"]
    claims = agent_capability.decode_capability_token(calls[0]["capability_token"])
    with database.transaction() as db:
        assert agent_capability.require_grant_user(db, claims).id == "employee-a"
    second_code = _challenge(client, "employee-b")
    _post_bot(client, config, content=f"/绑定 {second_code}", event="cannot-transfer")
    assert (
        client.get("/me/channel-bindings", headers=_headers("employee-b")).json()[
            "items"
        ]
        == []
    )
    revoked = client.request(
        "DELETE",
        f"/me/channel-bindings/{first['id']}",
        headers=_headers(),
        json={"expected_version": first["version"]},
    )
    assert revoked.status_code == 204
    with database.transaction() as db:
        with pytest.raises(agent_capability.CapabilityGrantError):
            agent_capability.require_grant_user(db, claims)
        turn = db.get(UserMemoryTurn, claims["memory_turn_id"])
        assert turn is not None
        assert turn.status == "invalidated" and turn.source_text_safe is None
    _post_bot(client, config, content=f"/绑定 {second_code}", event="bind-second")
    replacement = client.get(
        "/me/channel-bindings", headers=_headers("employee-b")
    ).json()["items"][0]
    assert replacement["id"] != first["id"]
    hidden = _post_bot(
        client, config, event="refresh-old", stream_id=initial["stream"]["id"]
    )
    assert "仅第一位员工可见" not in hidden["stream"]["content"]
    _post_bot(client, config, content="我叫方敏", event="memory-second")
    assert calls[0]["session_id"] != calls[1]["session_id"]
    with database.transaction() as db:
        with pytest.raises(agent_capability.CapabilityGrantError):
            agent_capability.require_grant_user(db, claims)


def _open_model(monkeypatch):
    calls = []

    class Pi:
        fail = False

        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            claims = agent_capability.decode_capability_token(
                kwargs["capability_token"]
            )
            with database.transaction() as db:
                agent_capability.require_grant_user(db, claims)
            calls.append(claims)
            if self.fail:
                raise RuntimeError("fixture upstream failure")
            return SimpleNamespace(
                reply="正常回复", token_usage={"total_tokens": 7}
            ), None

    monkeypatch.setattr(open_chat, "LocalPiAgentClient", Pi)
    monkeypatch.setattr(
        open_chat,
        "resolve_model_endpoint",
        lambda *args, **kwargs: SimpleNamespace(model="fixture-model"),
    )
    monkeypatch.setattr(
        open_chat, "endpoint_model_names", lambda endpoint: ["fixture-model"]
    )
    monkeypatch.setattr(open_chat, "chat_capability_tools", lambda *args: [])
    monkeypatch.setattr(
        open_chat,
        "build_knowledge_context",
        lambda db, **kwargs: (kwargs["message"] + "\nRAG context", None),
    )
    return Pi, calls


def test_open_request_identity_read_only_and_failed_call_fencing(
    channel_client, monkeypatch
):
    client, _ = channel_client
    pi, calls = _open_model(monkeypatch)
    headers = {"Authorization": "Bearer dnx_employee-a"}
    old = client.post("/api/open/chat", headers=headers, json={"message": "你好"})
    assert old.status_code == 200 and old.json()["memory_status"] == "read_only"
    assert (
        old.json()["memory_auto_save"] is False and old.json()["memory_turn_id"] is None
    )
    first = client.post(
        "/api/open/chat",
        headers=headers,
        json={"message": "我叫李梅", "request_id": "request-same"},
    )
    assert first.status_code == 200
    turn_id = first.json()["memory_turn_id"]
    replay = client.post(
        "/api/open/chat",
        headers=headers,
        json={"message": "我叫李梅", "request_id": "request-same"},
    )
    assert replay.json()["memory_turn_id"] == turn_id
    assert calls[-1]["memory_scope"] == "read"
    conflict = client.post(
        "/api/open/chat",
        headers=headers,
        json={"message": "我叫别人", "request_id": "request-same"},
    )
    assert conflict.status_code == 409
    other = client.post(
        "/api/open/chat",
        headers={"Authorization": "Bearer dnx_employee-b"},
        json={"message": "我叫方敏", "request_id": "request-same"},
    )
    assert other.json()["memory_turn_id"] != turn_id
    pi.fail = True
    failed = client.post(
        "/api/open/chat",
        headers=headers,
        json={"message": "请记住我喜欢中文", "request_id": "failed-call"},
    )
    assert failed.status_code == 502 and failed.json()["memory_status"] != "active"
    with database.transaction() as db:
        source = db.get(UserMemoryTurn, turn_id)
        assert source is not None
        assert source.source_text_safe == "我叫李梅"
        failed_turn = db.get(UserMemoryTurn, failed.json()["memory_turn_id"])
        assert failed_turn is not None and failed_turn.status == "closed"
        assert list(db.scalars(select(UserMemoryJob))) == []
        owner = db.get(User, "employee-a")
        assert owner is not None
        owner.status = "deleted"
    denied = client.post(
        "/api/open/chat", headers=headers, json={"message": "停用账号"}
    )
    assert denied.status_code == 401


def test_open_rejects_unsafe_source_without_losing_successful_reply(
    channel_client, monkeypatch
):
    client, _ = channel_client
    _pi, calls = _open_model(monkeypatch)
    headers = {"Authorization": "Bearer dnx_employee-a"}
    response = client.post(
        "/api/open/chat",
        headers=headers,
        json={
            "message": "api_key=sk-123456789012345678901234567890",
            "request_id": "secret-input",
        },
    )
    assert response.status_code == 200 and response.json()["answer"] == "正常回复"
    assert response.json()["usage"]["total_tokens"] == 7
    assert response.json()["memory_status"] == "unavailable"
    assert calls[-1]["memory_scope"] == "read"
    with database.transaction() as db:
        turn = db.get(UserMemoryTurn, response.json()["memory_turn_id"])
        assert turn is not None
        assert turn.source_text_safe is None

    def unavailable_status(*args, **kwargs):
        raise RuntimeError("fixture status failure")

    monkeypatch.setattr(memory_service, "read_turn_status", unavailable_status)
    failed_status = client.post(
        "/api/open/chat",
        headers=headers,
        json={"message": "请记住我叫李梅", "request_id": "status-failure"},
    )
    assert failed_status.status_code == 200
    assert (
        failed_status.json()["answer"] == "正常回复"
        and failed_status.json()["usage"]["total_tokens"] == 7
    )
    assert failed_status.json()["memory_status"] == "unavailable"

    with database.transaction() as db:
        turn = db.get(UserMemoryTurn, failed_status.json()["memory_turn_id"])
        assert turn is not None and turn.status == "closed"


def test_app_verified_agent_and_nonstream_binding_scope(channel_client, monkeypatch):
    client, _ = channel_client
    app_config = wecom.WeComConfig(
        corp_id="corp-fixture",
        agent_id=101,
        secret="fixture-only-secret",
        token="fixture-only-token",
        encoding_aes_key=base64.b64encode(b"a" * 32).decode().rstrip("="),
    )
    monkeypatch.setattr(user_channel_bindings, "load_wecom_config", lambda: app_config)
    delivered = []
    monkeypatch.setattr(
        wecom,
        "_send_text",
        lambda config, **kwargs: delivered.append(kwargs["content"]),
    )
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda question: None)
    sessions = []

    class Pi:
        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            claims = agent_capability.decode_capability_token(
                kwargs["capability_token"]
            )
            with database.transaction() as db:
                owner = agent_capability.require_grant_user(db, claims)
                reply = owner.id
            sessions.append(kwargs["session_id"])
            return SimpleNamespace(reply=reply), None

    monkeypatch.setattr(wecom, "LocalPiAgentClient", Pi)

    def decrypted(config, content, event, agent_id=None):
        inner = (
            f"<xml><ToUserName>{config.corp_id}</ToUserName>"
            f"<FromUserName>same-external-user</FromUserName><CreateTime>1</CreateTime>"
            f"<MsgType>text</MsgType><Content>{escape(content)}</Content><MsgId>{event}</MsgId>"
            f"<AgentID>{agent_id or config.agent_id}</AgentID></xml>"
        )
        encrypted = wecom._encrypt_payload(
            inner, encoding_aes_key=config.encoding_aes_key, receive_id=config.corp_id
        )
        timestamp, nonce = "1", "app-fixture"
        return wecom.decrypt_callback_message(
            f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode(),
            config=config,
            timestamp=timestamp,
            nonce=nonce,
            msg_signature=wecom.callback_signature(
                config.token, timestamp, nonce, encrypted
            ),
        )

    with pytest.raises(wecom.WeComSignatureError):
        decrypted(app_config, "我叫李梅", "wrong-agent", agent_id=999)
    for owner, config in (
        ("employee-a", app_config),
        ("employee-b", replace(app_config, agent_id=202)),
    ):
        monkeypatch.setattr(user_channel_bindings, "load_wecom_config", lambda: config)
        challenge = client.post(
            "/me/channel-bindings/wecom/challenges",
            headers=_headers(owner),
            json={"channel_kind": "app"},
        )
        assert challenge.status_code == 200
        command = decrypted(
            config, f"/绑定 {challenge.json()['code']}", "same-bind-event"
        )
        wecom.process_incoming_message(command, config=config)
        message = decrypted(config, "  请记住我喜欢中文  ", "same-message-event")
        wecom.process_incoming_message(message, config=config)
        assert delivered[-1] == owner
    assert sessions[0] != sessions[1]
    with database.transaction() as db:
        turns = list(db.scalars(select(UserMemoryTurn)))
        assert {turn.owner_user_id for turn in turns} == {"employee-a", "employee-b"}
        assert all(turn.source_text_safe == "  请记住我喜欢中文  " for turn in turns)


def test_bound_bot_group_unknown_and_unconfigured_bot_never_gain_memory(
    channel_client, monkeypatch
):
    client, config = channel_client
    code = _challenge(client)
    _post_bot(client, config, content=f"/绑定 {code}", event="bind-for-group")
    body, params = _signed_bot(config, content="个人资料", event="empty-config")
    with pytest.raises(wecom.WeComSignatureError):
        wecom.decrypt_aibot_message(body, config=replace(config, aibot_id=""), **params)
    calls = []

    class Pi:
        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(reply="普通聊天"), None

    monkeypatch.setattr(wecom, "LocalPiAgentClient", Pi)
    monkeypatch.setattr(wecom, "answer_wecom_knowledge", lambda question: None)
    _post_bot(
        client,
        config,
        content="@翠花 我的名字是什么",
        chat_type="group",
        event="group-memory",
    )
    _post_bot(
        client,
        config,
        content="我的名字是什么",
        chat_type="unknown",
        event="unknown-memory",
    )
    assert len(calls) == 2
    assert all("capability_token" not in call for call in calls)
    with database.transaction() as db:
        assert list(db.scalars(select(UserMemoryTurn))) == []


def test_revoked_integration_key_never_falls_back_to_legacy_user_key(channel_client):
    client, _ = channel_client
    with database.transaction() as db:
        db.add(
            UserApiKey(
                id="revoked-key",
                user_id="employee-a",
                api_key="dnx_employee-a",
                status="revoked",
                created_at=now(),
            )
        )
    denied = client.post(
        "/api/open/chat",
        headers={"Authorization": "Bearer dnx_employee-a"},
        json={"message": "旧密钥不应继续访问个人记忆"},
    )
    assert denied.status_code == 401
