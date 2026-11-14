"""Enterprise WeChat callback crypto, message parsing, and delivery."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from defusedxml import ElementTree as ET

from app.core.config import settings
from app.core.database import transaction
from app.core.security import as_utc
from app.models.auth import User
from app.repositories import user_channel_bindings as binding_repo
from app.repositories import user_memory as memory_repo
from app.services import user_memory as memory_service
from app.services.agent_capability import (
    MEMORY_CAPABILITY_TOOLS,
    chat_capability_tools,
    create_capability_token,
)
from app.services.pi_local import LocalPiAgentClient
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID
from app.services.wecom_knowledge import answer_wecom_knowledge

logger = logging.getLogger(__name__)

WECOM_MAX_CALLBACK_BYTES = 1_048_576
WECOM_MAX_TEXT_BYTES = 2_048
WECOM_AIBOT_STREAM_MAX_BYTES = 20_480
WECOM_PKCS7_BLOCK_BITS = 256
_TOKEN_EXPIRY_SKEW_SECONDS = 60
_FALLBACK_REPLY_TEMPLATE = "{name}暂时无法回复，请稍后重试。"


def _fallback_reply() -> str:
    from app.services.pi_runtime.persona import load_pi_persona

    return _FALLBACK_REPLY_TEMPLATE.format(name=load_pi_persona().name)


class WeComError(RuntimeError):
    """Base class for callback and outbound API errors."""


class WeComConfigurationError(WeComError):
    """Raised when the callback cannot operate without configuration."""


class WeComSignatureError(WeComError):
    """Raised when a callback signature or encrypted payload is invalid."""


@dataclass(frozen=True)
class WeComConfig:
    corp_id: str
    agent_id: int
    secret: str
    token: str
    encoding_aes_key: str
    timeout_seconds: float = 30.0

    @property
    def callback_configured(self) -> bool:
        return bool(self.corp_id and self.token and self.encoding_aes_key)

    @property
    def configured(self) -> bool:
        return bool(self.callback_configured and self.agent_id > 0 and self.secret)


@dataclass(frozen=True)
class WeComMessage:
    to_user_name: str
    from_user_name: str
    create_time: str
    msg_type: str
    content: str = ""
    msg_id: str | None = None
    agent_id: str | None = None
    chat_id: str | None = None
    event: str | None = None
    event_key: str | None = None
    chat_type: str | None = None
    verified_channel_key: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class WeComAiBotConfig:
    aibot_id: str
    token: str
    encoding_aes_key: str
    timeout_seconds: float = 30.0

    @property
    def callback_configured(self) -> bool:
        return bool(self.token and self.encoding_aes_key)

    @property
    def configured(self) -> bool:
        return bool(self.callback_configured and self.aibot_id)


def _validate_aibot_response_url(response_url: str) -> None:
    parsed = urllib.parse.urlsplit(response_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise WeComSignatureError("企业微信智能机器人 response_url 无效") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "qyapi.weixin.qq.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/cgi-bin/aibot/response"
        or "response_code" not in urllib.parse.parse_qs(parsed.query)
    ):
        raise WeComSignatureError("企业微信智能机器人 response_url 无效")


@dataclass(frozen=True)
class WeComAiBotMessage:
    msg_id: str
    aibot_id: str
    chat_id: str | None
    chat_type: str
    from_user_id: str
    msg_type: str
    response_url: str = ""
    content: str = ""
    stream_id: str | None = None
    verified_channel_key: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class _CachedAccessToken:
    value: str
    expires_at: float


_access_token_cache: dict[str, _CachedAccessToken] = {}
_access_token_lock = threading.Lock()
_incoming_processed: dict[str, float] = {}
_incoming_inflight: set[str] = set()
_incoming_processed_lock = threading.Lock()
_INCOMING_DEDUP_TTL_SECONDS = 600


@dataclass
class _AibotStreamState:
    msg_id: str
    stream_id: str
    audience_key: str = ""
    source_hash: str = ""
    content: str = ""
    finish: bool = False
    processing: bool = False
    updated_at: float = 0.0


_aibot_streams_by_msg_id: dict[str, _AibotStreamState] = {}
_aibot_streams_by_id: dict[str, _AibotStreamState] = {}
_aibot_streams_lock = threading.Lock()
_AIBOT_STREAM_TTL_SECONDS = 600


def load_wecom_config() -> WeComConfig:
    """Load self-built application callback settings."""
    return WeComConfig(
        corp_id=settings.wecom_corp_id.strip(),
        agent_id=settings.wecom_agent_id,
        secret=settings.wecom_secret.strip(),
        token=settings.wecom_token.strip(),
        encoding_aes_key=settings.wecom_encoding_aes_key.strip(),
        timeout_seconds=settings.wecom_timeout_seconds,
    )


def load_wecom_aibot_config() -> WeComAiBotConfig:
    """Load intelligent bot callback settings for group @ mentions."""
    return WeComAiBotConfig(
        aibot_id=settings.wecom_aibot_id.strip(),
        token=settings.wecom_aibot_token.strip(),
        encoding_aes_key=settings.wecom_aibot_encoding_aes_key.strip(),
        timeout_seconds=settings.wecom_aibot_timeout_seconds,
    )


def _require_config(config: WeComConfig) -> None:
    if not config.callback_configured:
        raise WeComConfigurationError(
            "企业微信回调未配置，请设置 CorpID、Token 和 EncodingAESKey"
        )


def _require_aibot_config(config: WeComAiBotConfig) -> None:
    if not config.callback_configured:
        raise WeComConfigurationError(
            "企业微信智能机器人回调未配置，请设置 Token 和 EncodingAESKey"
        )


def callback_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    """Return the SHA-1 signature required by Enterprise WeChat."""
    values = sorted((token, timestamp, nonce, encrypted))
    # Enterprise WeChat mandates SHA-1 for this callback protocol.
    digest = hashlib.sha1(  # nosec B324
        "".join(values).encode("utf-8")
    )
    return digest.hexdigest()


def _verify_token_signature(
    *,
    token: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
) -> None:
    expected = callback_signature(token, timestamp, nonce, encrypted)
    if not hmac.compare_digest(expected, msg_signature.strip().lower()):
        raise WeComSignatureError("企业微信回调签名无效")


def verify_callback_signature(
    *,
    config: WeComConfig,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
) -> None:
    _verify_token_signature(
        token=config.token,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
    )


def _aes_key(encoding_aes_key: str) -> bytes:
    try:
        key = base64.b64decode(f"{encoding_aes_key.rstrip('=')}=", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WeComSignatureError("企业微信 EncodingAESKey 无效") from exc
    if len(key) != 32:
        raise WeComSignatureError("企业微信 EncodingAESKey 长度无效")
    return key


def _decrypt_payload(encrypted: str, *, encoding_aes_key: str, receive_id: str) -> str:
    key = _aes_key(encoding_aes_key)
    try:
        ciphertext = base64.b64decode("".join(encrypted.split()), validate=True)
        algorithm = algorithms.AES(key)
        decryptor = Cipher(
            algorithm, modes.CBC(key[: algorithm.block_size // 8])
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = PKCS7(WECOM_PKCS7_BLOCK_BITS).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except (binascii.Error, ValueError) as exc:
        raise WeComSignatureError("企业微信消息解密失败") from exc

    if len(plaintext) < 20:
        raise WeComSignatureError("企业微信消息格式无效")
    message_length = int.from_bytes(plaintext[16:20], byteorder="big")
    message_end = 20 + message_length
    if message_end > len(plaintext):
        raise WeComSignatureError("企业微信消息长度无效")
    try:
        message = plaintext[20:message_end].decode("utf-8")
        actual_receive_id = plaintext[message_end:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WeComSignatureError("企业微信消息编码无效") from exc
    if actual_receive_id != receive_id:
        raise WeComSignatureError("企业微信接收方不匹配")
    return message


def _encrypt_payload(message: str, *, encoding_aes_key: str, receive_id: str) -> str:
    key = _aes_key(encoding_aes_key)
    message_bytes = message.encode("utf-8")
    plaintext = (
        os.urandom(16)
        + len(message_bytes).to_bytes(4, byteorder="big")
        + message_bytes
        + receive_id.encode("utf-8")
    )
    padder = PKCS7(WECOM_PKCS7_BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()
    algorithm = algorithms.AES(key)
    encryptor = Cipher(
        algorithm, modes.CBC(key[: algorithm.block_size // 8])
    ).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def encrypt_aibot_response(
    payload: dict[str, Any],
    *,
    config: WeComAiBotConfig,
    timestamp: str,
    nonce: str,
) -> dict[str, Any]:
    """Encrypt a passive intelligent bot reply for Enterprise WeChat."""
    _require_aibot_config(config)
    response_timestamp = timestamp.strip()
    if not response_timestamp.isdigit():
        raise WeComSignatureError("企业微信回调时间戳无效")
    try:
        response_timestamp_value = int(response_timestamp)
    except ValueError as exc:
        raise WeComSignatureError("企业微信回调时间戳无效") from exc
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encrypted = _encrypt_payload(
        plaintext,
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )
    return {
        "encrypt": encrypted,
        "msgsignature": callback_signature(
            config.token, response_timestamp, nonce, encrypted
        ),
        "timestamp": response_timestamp_value,
        "nonce": nonce,
    }


def _decrypt(encrypted: str, *, config: WeComConfig) -> str:
    return _decrypt_payload(
        encrypted,
        encoding_aes_key=config.encoding_aes_key,
        receive_id=config.corp_id,
    )


def decrypt_echo(
    *,
    config: WeComConfig,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> str:
    _require_config(config)
    verify_callback_signature(
        config=config,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=echostr,
    )
    return _decrypt(echostr, config=config)


def decrypt_aibot_echo(
    *,
    config: WeComAiBotConfig,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> str:
    _require_aibot_config(config)
    _verify_token_signature(
        token=config.token,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=echostr,
    )
    return _decrypt_payload(
        echostr,
        encoding_aes_key=config.encoding_aes_key,
        receive_id="",
    )


def _parse_aibot_message(plaintext: str) -> WeComAiBotMessage:
    try:
        payload = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise WeComSignatureError("企业微信智能机器人消息 JSON 无效") from exc
    if not isinstance(payload, dict):
        raise WeComSignatureError("企业微信智能机器人消息格式无效")

    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise WeComSignatureError(f"企业微信智能机器人消息缺少 {name}")
        return value.strip()

    sender = payload.get("from")
    if not isinstance(sender, dict):
        raise WeComSignatureError("企业微信智能机器人消息缺少 from")
    from_user_id = sender.get("userid")
    if not isinstance(from_user_id, str) or not from_user_id.strip():
        raise WeComSignatureError("企业微信智能机器人消息缺少 from.userid")

    msg_type = required_text("msgtype").lower()
    response_url = payload.get("response_url")
    if not isinstance(response_url, str):
        response_url = ""
    response_url = response_url.strip()
    stream_payload = payload.get("stream")
    stream_id = None
    if isinstance(stream_payload, dict):
        raw_stream_id = stream_payload.get("id")
        if isinstance(raw_stream_id, str) and raw_stream_id.strip():
            stream_id = raw_stream_id.strip()
    if msg_type == "text" and not response_url:
        raise WeComSignatureError("企业微信智能机器人消息缺少 response_url")
    if msg_type == "stream" and not stream_id:
        raise WeComSignatureError("企业微信智能机器人消息缺少 stream.id")

    text_payload = payload.get("text")
    content = ""
    if isinstance(text_payload, dict) and isinstance(text_payload.get("content"), str):
        content = text_payload["content"]
    raw_chat_id = payload.get("chatid")
    chat_id = raw_chat_id.strip() if isinstance(raw_chat_id, str) else ""
    return WeComAiBotMessage(
        msg_id=required_text("msgid"),
        aibot_id=required_text("aibotid"),
        chat_id=chat_id or None,
        chat_type=required_text("chattype").lower(),
        from_user_id=from_user_id.strip(),
        msg_type=msg_type,
        response_url=response_url,
        content=content,
        stream_id=stream_id,
    )


def decrypt_aibot_message(
    body: bytes,
    *,
    config: WeComAiBotConfig,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> WeComAiBotMessage:
    _require_aibot_config(config)
    if len(body) > WECOM_MAX_CALLBACK_BYTES:
        raise WeComSignatureError("企业微信智能机器人消息体过大")
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeComSignatureError("企业微信智能机器人加密 JSON 无效") from exc
    if not isinstance(envelope, dict):
        raise WeComSignatureError("企业微信智能机器人加密消息格式无效")
    encrypted = envelope.get("encrypt")
    if not isinstance(encrypted, str) or not encrypted.strip():
        raise WeComSignatureError("企业微信智能机器人消息缺少 encrypt")
    encrypted = encrypted.strip()
    _verify_token_signature(
        token=config.token,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
    )
    message = _parse_aibot_message(
        _decrypt_payload(
            encrypted,
            encoding_aes_key=config.encoding_aes_key,
            receive_id="",
        )
    )
    if not config.aibot_id or message.aibot_id != config.aibot_id:
        raise WeComSignatureError("企业微信智能机器人 ID 不匹配")
    if message.response_url:
        _validate_aibot_response_url(message.response_url)
    return replace(
        message,
        verified_channel_key=binding_repo.verified_channel_key("aibot", config.aibot_id),
    )


def _xml_text(root: Any, name: str, *, required: bool = False) -> str:
    value = root.findtext(name)
    text = (value or "").strip()
    if required and not text:
        raise WeComSignatureError(f"企业微信消息缺少 {name}")
    return text


def _parse_message_xml(xml_text: str) -> WeComMessage:
    if len(xml_text.encode("utf-8")) > WECOM_MAX_CALLBACK_BYTES:
        raise WeComSignatureError("企业微信消息体过大")
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise WeComSignatureError("企业微信消息格式无效")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeComSignatureError("企业微信消息 XML 无效") from exc
    if root.tag != "xml":
        raise WeComSignatureError("企业微信消息根节点无效")
    return WeComMessage(
        to_user_name=_xml_text(root, "ToUserName", required=True),
        from_user_name=_xml_text(root, "FromUserName", required=True),
        create_time=_xml_text(root, "CreateTime"),
        msg_type=_xml_text(root, "MsgType", required=True).lower(),
        content=root.findtext("Content") or "",
        msg_id=_xml_text(root, "MsgId") or None,
        agent_id=_xml_text(root, "AgentID") or None,
        chat_id=_xml_text(root, "ChatId") or None,
        event=_xml_text(root, "Event") or None,
        event_key=_xml_text(root, "EventKey") or None,
        chat_type=_xml_text(root, "ChatType").lower() or None,
    )


def decrypt_callback_message(
    body: bytes,
    *,
    config: WeComConfig,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> WeComMessage:
    """Verify and decrypt one Enterprise WeChat encrypted XML callback."""
    _require_config(config)
    if config.agent_id <= 0:
        raise WeComConfigurationError("企业微信 AgentID 未配置")
    if len(body) > WECOM_MAX_CALLBACK_BYTES:
        raise WeComSignatureError("企业微信消息体过大")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise WeComSignatureError("企业微信消息格式无效")
    try:
        outer = ET.fromstring(body)
    except ET.ParseError as exc:
        raise WeComSignatureError("企业微信消息 XML 无效") from exc
    if outer.tag != "xml":
        raise WeComSignatureError("企业微信消息根节点无效")
    encrypted = _xml_text(outer, "Encrypt", required=True)
    verify_callback_signature(
        config=config,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
    )
    message = _parse_message_xml(_decrypt(encrypted, config=config))
    if message.to_user_name != config.corp_id:
        raise WeComSignatureError("企业微信消息接收方不匹配")
    if message.agent_id and message.agent_id != str(config.agent_id):
        raise WeComSignatureError("企业微信 AgentID 不匹配")
    return replace(
        message,
        verified_channel_key=binding_repo.verified_channel_key("app", config.corp_id, config.agent_id),
    )


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise WeComError("企业微信服务返回了无效 JSON") from exc
    if not isinstance(body, dict):
        raise WeComError("企业微信服务返回格式无效")
    return body


def _wecom_pi_session_id(external_user_id: str) -> str:
    digest = hashlib.sha256(external_user_id.encode("utf-8")).hexdigest()[:32]
    return f"wecom-pi-{digest}"


def _pi_text_delta(event: dict[str, Any]) -> str:
    if event.get("type") != "message_update":
        return ""
    message_event = event.get("assistantMessageEvent")
    if not isinstance(message_event, dict) or message_event.get("type") != "text_delta":
        return ""
    delta = message_event.get("delta")
    return delta if isinstance(delta, str) else ""


@dataclass
class _WeComMemoryRun:
    runtime_session_id: str
    user_id: str = PI_RUNTIME_AGENT_ID
    capability_token: str | None = None
    turn_id: str | None = None
    binding_id: str | None = None
    binding_version: int | None = None
    memory_status: str = "none"


def _private_app_message(message: WeComMessage) -> bool:
    return not message.chat_id and message.chat_type in {None, "single"}


def _binding_command(
    content: str, *, channel_kind: str, channel_key: str | None,
    sender: str, private: bool,
) -> str | None:
    if not content.strip().startswith("/绑定"):
        return None
    if not private:
        return "请在企业微信私聊中发送绑定码，群聊不会读取或保存个人记忆。"
    if not channel_key:
        return "当前消息未通过可信通道校验，无法绑定账号。"
    parts = content.strip().split()
    if len(parts) != 2 or parts[0] != "/绑定":
        return "请登录网页，在个人记忆中申请绑定码，再发送 /绑定 <绑定码>。"
    try:
        with transaction() as db:
            try:
                binding_repo.consume_challenge(
                    db, code=parts[1], channel_kind=channel_kind,
                    verified_channel_key=channel_key, external_user_id=sender,
                )
            except binding_repo.ChannelBindingError as exc:
                # Rejected attempts still consume the durable sender rate limit.
                reply = str(exc)
            else:
                reply = "账号绑定成功。个人记忆仅在此账号的私聊中使用；可随时在网页中解除绑定。"
        return reply
    except Exception:
        logger.warning("wecom binding could not be completed", exc_info=False)
        return "账号绑定暂时不可用，请稍后重试。"


def _prepare_wecom_memory(
    *, raw_text: str, channel_kind: str, channel_key: str | None,
    sender: str, private: bool, event_id: str | None, external_session_id: str,
) -> _WeComMemoryRun:
    run = _WeComMemoryRun(runtime_session_id=_wecom_pi_session_id(external_session_id))
    if not private or not channel_key:
        return run
    with transaction() as db:
        binding = binding_repo.get_active_binding(
            db, channel_kind=channel_kind, verified_channel_key=channel_key,
            external_user_id=sender,
        )
        if binding is None:
            return run
        state = memory_repo.lock_owned_state(db, binding.owner_user_id)
        binding = binding_repo.get_active_binding_by_id(
            db, owner_user_id=binding.owner_user_id, binding_id=binding.id,
            version=binding.version,
        )
        if binding is None:
            raise WeComError("企业微信账号绑定已失效")
        user = db.get(User, binding.owner_user_id)
        if user is None or user.status != "active":
            raise WeComError("企业微信账号绑定已失效")
        run.user_id = user.id
        run.runtime_session_id = binding_repo.binding_runtime_session_id(binding)
        run.binding_id = binding.id
        run.binding_version = binding.version
        grant: dict[str, Any] = {
            "memory_scope": "none", "memory_channel_binding_id": binding.id,
            "memory_channel_binding_version": binding.version,
        }
        memory_tools: list[str] = []
        if settings.personal_memory_enabled and state.use_enabled:
            grant.update(memory_scope="read", memory_epoch=state.epoch, auth_version=user.auth_version)
            memory_tools = ["get_my_memory"]
            run.memory_status = "read_only"
            if event_id:
                safe_text, too_large = memory_service.sanitize_source_snapshot(raw_text)
                source_usable = (
                    not too_large and safe_text == raw_text
                    and not memory_service.contains_secret(raw_text)
                )
                event_hash = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
                turn = memory_repo.register_turn(
                    db, owner_user_id=user.id, channel="wecom",
                    request_key=f"wecom:{binding.id}:{binding.version}:{channel_key}:{event_hash}",
                    raw_text=raw_text, runtime_session_id=run.runtime_session_id,
                    source_text_safe=safe_text if source_usable else None,
                    source_text_retention=memory_service.source_retention() if source_usable else None,
                )
                run.turn_id = turn.id
                if (
                    source_usable and turn.status == "live" and turn.epoch == state.epoch
                    and turn.runtime_session_id == run.runtime_session_id
                    and memory_service.resolve_turn_source_text(db, turn) is not None
                ):
                    grant.update(
                        memory_scope="propose", memory_turn_id=turn.id,
                        memory_source_hash=turn.source_hash,
                    )
                    memory_tools = list(MEMORY_CAPABILITY_TOOLS)
                    run.memory_status = "none"
                    if state.learning_mode != "manual" and (
                        state.automatic_source_cutoff_at is None
                        or as_utc(turn.created_at) >= as_utc(state.automatic_source_cutoff_at)
                    ):
                        memory_repo.enqueue_extraction_job(
                            db, owner_user_id=user.id, turn_id=turn.id,
                            source_hash_value=turn.source_hash,
                            extractor_version=settings.personal_memory_extractor_version,
                            epoch=state.epoch,
                        )
                elif not source_usable:
                    run.memory_status = "source_too_large" if too_large else "unavailable"
        run.capability_token = create_capability_token(
            user_id=user.id, agent_id=PI_RUNTIME_AGENT_ID,
            conversation_id=run.runtime_session_id, space_mode="accessible",
            personal_data_scope="own",
            allowed_tools=list(chat_capability_tools(db, user)) + memory_tools, **grant,
        )
    return run


def _finish_wecom_memory(run: _WeComMemoryRun | None) -> str:
    if run is None:
        return ""
    if run.turn_id:
        try:
            with transaction() as db:
                memory_repo.close_turn(db, owner_user_id=run.user_id, turn_id=run.turn_id)
            with transaction() as db:
                status = memory_service.read_turn_status(db, owner_user_id=run.user_id, turn_id=run.turn_id)
                if run.memory_status not in {"read_only", "source_too_large", "unavailable"} or status.receipts:
                    run.memory_status = status.memory_status
        except Exception:
            logger.warning("wecom memory completion could not be read", exc_info=False)
            run.memory_status = "unavailable"
    notice = {
        "active": "个人记忆：已保存。",
        "needs_confirmation": "个人记忆：待确认，请在网页个人记忆中确认后生效。",
        "pending": "个人记忆：仍在处理中，可在网页查看结果。",
        "read_only": "个人记忆：本轮仅使用已有记忆，不自动保存。",
        "source_too_large": "个人记忆：原文过长，未提炼或保存；请选定短片段后重试。",
        "unavailable": "个人记忆：暂时无法确认保存结果，请在网页查看。",
    }.get(run.memory_status, "")
    return f"\n\n{notice}" if notice else ""


def _require_current_wecom_binding(run: _WeComMemoryRun | None) -> None:
    if run is None or not run.binding_id or run.binding_version is None:
        return
    with transaction() as db:
        if binding_repo.get_active_binding_by_id(
            db, owner_user_id=run.user_id, binding_id=run.binding_id,
            version=run.binding_version,
        ) is None:
            raise WeComError("企业微信账号绑定已失效")


def _local_pi_client(user_id: str = PI_RUNTIME_AGENT_ID) -> LocalPiAgentClient:
    return LocalPiAgentClient(
        agent_id=PI_RUNTIME_AGENT_ID, user_id=user_id, capability="chat",
    )


def _ask_cuihua_prompt(
    *,
    content: str,
    external_user_id: str,
    idempotency_key: str | None,
    timeout_seconds: float,
    memory_run: _WeComMemoryRun | None = None,
) -> str:
    del idempotency_key

    async def run() -> str:
        _require_current_wecom_binding(memory_run)
        result, _ = await asyncio.wait_for(
            (_local_pi_client(memory_run.user_id) if memory_run else _local_pi_client()).chat_async(
                content,
                session_id=memory_run.runtime_session_id if memory_run else _wecom_pi_session_id(external_user_id),
                **({"capability_token": memory_run.capability_token} if memory_run and memory_run.capability_token else {}),
            ),
            timeout=timeout_seconds,
        )
        reply = getattr(result, "reply", "")
        if not isinstance(reply, str) or not reply.strip():
            raise WeComError("数字员工没有返回可发送的文本回复")
        return reply.strip()

    return asyncio.run(run())


def _stream_cuihua_prompt(
    *,
    content: str,
    external_user_id: str,
    idempotency_key: str | None,
    timeout_seconds: float,
    on_delta: Callable[[str], None] | None = None,
    memory_run: _WeComMemoryRun | None = None,
) -> Iterator[str]:
    """Yield local Pi text deltas for forwarding to WeCom stream replies."""
    del idempotency_key
    deltas: list[str] = []

    async def on_event(event: dict[str, Any]) -> None:
        delta = _pi_text_delta(event)
        if delta:
            deltas.append(delta)
            if on_delta is not None:
                on_delta(delta)

    async def run() -> str:
        _require_current_wecom_binding(memory_run)
        result, _ = await asyncio.wait_for(
            (_local_pi_client(memory_run.user_id) if memory_run else _local_pi_client()).chat_async(
                content,
                session_id=memory_run.runtime_session_id if memory_run else _wecom_pi_session_id(external_user_id),
                on_event=on_event,
                **({"capability_token": memory_run.capability_token} if memory_run and memory_run.capability_token else {}),
            ),
            timeout=timeout_seconds,
        )
        reply = getattr(result, "reply", "")
        return reply.strip() if isinstance(reply, str) else ""

    final_reply = asyncio.run(run())
    emitted = "".join(deltas)
    yield from deltas
    if not emitted:
        if final_reply:
            if on_delta is not None:
                on_delta(final_reply)
            yield final_reply
    elif final_reply.startswith(emitted) and len(final_reply) > len(emitted):
        if on_delta is not None:
            on_delta(final_reply[len(emitted) :])
        yield final_reply[len(emitted) :]


def _ask_cuihua(
    message: WeComMessage, *, config: WeComConfig, memory_run: _WeComMemoryRun | None = None,
) -> str:
    return _ask_cuihua_prompt(
        content=message.content,
        external_user_id=f"wecom:{config.corp_id}:{message.from_user_name}",
        idempotency_key=f"wecom:{message.msg_id}" if message.msg_id else None,
        timeout_seconds=config.timeout_seconds,
        memory_run=memory_run,
    )


def _get_access_token(config: WeComConfig) -> str:
    now = time.monotonic()
    with _access_token_lock:
        cached = _access_token_cache.get(config.corp_id)
        if cached and cached.expires_at > now:
            return cached.value
    try:
        with httpx.Client(timeout=httpx.Timeout(config.timeout_seconds)) as client:
            response = client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": config.corp_id, "corpsecret": config.secret},
            )
    except httpx.HTTPError as exc:
        raise WeComError("无法连接企业微信 access_token 接口") from exc
    if response.status_code >= 400:
        raise WeComError(f"企业微信 access_token 接口返回 HTTP {response.status_code}")
    body = _response_json(response)
    if body.get("errcode") not in (0, "0"):
        raise WeComError(f"企业微信 access_token 获取失败（{body.get('errcode')}）")
    value = body.get("access_token")
    if not isinstance(value, str) or not value.strip():
        raise WeComError("企业微信 access_token 响应缺少 token")
    try:
        expires_in = max(1, int(body.get("expires_in", 7200)))
    except (TypeError, ValueError):
        expires_in = 7200
    cached = _CachedAccessToken(
        value=value.strip(),
        expires_at=time.monotonic() + max(1, expires_in - _TOKEN_EXPIRY_SKEW_SECONDS),
    )
    with _access_token_lock:
        _access_token_cache[config.corp_id] = cached
    return cached.value


def _text_chunks(text: str, max_bytes: int = WECOM_MAX_TEXT_BYTES) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if current and current_size + char_size > max_bytes:
            chunks.append("".join(current))
            current = []
            current_size = 0
        if char_size > max_bytes:
            continue
        current.append(char)
        current_size += char_size
    if current:
        chunks.append("".join(current))
    return chunks or [""]


def _send_text(config: WeComConfig, *, to_user: str, content: str) -> None:
    access_token = _get_access_token(config)
    url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
    try:
        with httpx.Client(timeout=httpx.Timeout(config.timeout_seconds)) as client:
            for chunk in _text_chunks(content):
                response = client.post(
                    url,
                    params={"access_token": access_token},
                    json={
                        "touser": to_user,
                        "msgtype": "text",
                        "agentid": config.agent_id,
                        "text": {"content": chunk},
                    },
                )
                if response.status_code >= 400:
                    raise WeComError(
                        f"企业微信消息发送失败（HTTP {response.status_code}）"
                    )
                body = _response_json(response)
                if body.get("errcode") not in (None, 0, "0"):
                    raise WeComError(f"企业微信消息发送失败（{body.get('errcode')}）")
    except httpx.HTTPError as exc:
        raise WeComError("无法连接企业微信消息发送接口") from exc


def _normalize_aibot_prompt(message: WeComAiBotMessage) -> str:
    text = message.content.strip()
    if not text:
        return ""
    if message.chat_type != "group":
        return text
    if not text.startswith("@"):
        return ""
    without_mention = re.sub(r"^@\S+\s*", "", text, count=1)
    return without_mention or text


def _markdown_to_wecom_text(markdown: str) -> str:
    """Render Markdown as readable text for WeCom's plain-text stream."""

    def render_inline(value: str) -> str:
        code_spans: list[str] = []

        def protect_code(match: re.Match[str]) -> str:
            code_spans.append(match.group(2))
            return f"\ue000{len(code_spans) - 1}\ue001"

        value = re.sub(r"(`+)(.+?)\1", protect_code, value)
        value = re.sub(r"<((?:https?://|mailto:)[^<>\s]+)>", r"\1", value)
        value = re.sub(
            r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)",
            lambda match: (
                f"图片 {match.group(1).strip()}：{match.group(2)}"
                if match.group(1).strip()
                else f"图片：{match.group(2)}"
            ),
            value,
        )
        value = re.sub(
            r"\[([^\]]+)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)",
            lambda match: (
                match.group(2)
                if match.group(1).strip() == match.group(2)
                else f"{match.group(1).strip()}：{match.group(2)}"
            ),
            value,
        )
        value = re.sub(r"</?[A-Za-z][^>\n]*>", "", value)
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
        value = re.sub(r"__(.+?)__", r"\1", value)
        value = re.sub(r"~~(.+?)~~", r"\1", value)
        value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
        value = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"\1", value)
        value = value.replace("**", "").replace("__", "").replace("~~", "")
        value = value.replace("`", "")
        value = re.sub(r"\\([\\`*{}\[\]()#+\-.!_>~|])", r"\1", value)
        for index, code in enumerate(code_spans):
            value = value.replace(f"\ue000{index}\ue001", code)
        return value

    lines: list[str] = []
    fence_char: str | None = None
    normalized_markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in normalized_markdown.split("\n"):
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", raw_line)
        if fence:
            marker = fence.group(1)[0]
            if fence_char is None:
                fence_char = marker
                language = render_inline(fence.group(2).strip())
                lines.append(f"【代码 · {language}】" if language else "【代码】")
            elif marker == fence_char:
                fence_char = None
            else:
                lines.append(raw_line.rstrip())
            continue
        if fence_char is not None:
            lines.append(raw_line.rstrip())
            continue

        stripped = raw_line.strip()
        if re.fullmatch(r"(?:[-*_]\s*){3,}", stripped):
            continue

        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", raw_line)
        if heading:
            lines.append(f"【{render_inline(heading.group(1))}】")
            continue

        if raw_line.lstrip().startswith(">"):
            quote = re.sub(r"^\s*(?:>\s*)+", "", raw_line)
            lines.append(f"引用：{render_inline(quote)}")
            continue

        table_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if stripped.startswith("|") and stripped.endswith("|") and len(table_cells) > 1:
            if all(
                re.fullmatch(r":?-{3,}:?", cell.replace(" ", ""))
                for cell in table_cells
            ):
                continue
            lines.append(" ｜ ".join(render_inline(cell) for cell in table_cells))
            continue

        bullet = re.match(r"^(\s*)[-+*]\s+(.*)$", raw_line)
        if bullet:
            item = render_inline(bullet.group(2))
            item = re.sub(r"^\[(?:x|X)\]\s+", "☑ ", item)
            item = re.sub(r"^\[ \]\s+", "☐ ", item)
            lines.append(f"{bullet.group(1)}• {item}")
            continue

        ordered = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", raw_line)
        if ordered:
            lines.append(
                f"{ordered.group(1)}{ordered.group(2)}. "
                f"{render_inline(ordered.group(3))}"
            )
            continue

        lines.append(render_inline(raw_line).rstrip())

    compact: list[str] = []
    for line in lines:
        if line.strip():
            compact.append(line)
        elif compact and compact[-1] != "":
            compact.append("")
    return "\n".join(compact).strip()


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _aibot_stream_id(msg_id: str) -> str:
    return f"wecom-aibot-{hashlib.sha256(msg_id.encode('utf-8')).hexdigest()[:32]}"


def _cleanup_aibot_streams_locked(now: float) -> None:
    expired = [
        stream_id
        for stream_id, state in _aibot_streams_by_id.items()
        if now - state.updated_at >= _AIBOT_STREAM_TTL_SECONDS
    ]
    for stream_id in expired:
        state = _aibot_streams_by_id.pop(stream_id, None)
        if state:
            _aibot_streams_by_msg_id.pop(state.msg_id, None)


def _aibot_audience(message: WeComAiBotMessage) -> str:
    binding_identity = None
    if message.verified_channel_key and message.chat_type == "single":
        with transaction() as db:
            binding = binding_repo.get_active_binding(
                db, channel_kind="aibot", verified_channel_key=message.verified_channel_key,
                external_user_id=message.from_user_id,
            )
            if binding is not None:
                binding_identity = [binding.id, binding.version, binding.owner_user_id]
    namespace = json.dumps([
        message.verified_channel_key, message.aibot_id, message.chat_type,
        message.chat_id, message.from_user_id, binding_identity,
    ], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def _ensure_aibot_stream(message: WeComAiBotMessage) -> tuple[_AibotStreamState, bool]:
    now = time.monotonic()
    audience_key = _aibot_audience(message)
    event_key = f"{audience_key}:{message.msg_id}"
    source_hash = memory_repo.source_hash(message.content)
    with _aibot_streams_lock:
        _cleanup_aibot_streams_locked(now)
        existing = _aibot_streams_by_msg_id.get(event_key)
        if existing is not None:
            if existing.source_hash != source_hash:
                raise WeComSignatureError("企业微信消息标识已用于其他内容")
            existing.updated_at = now
            return existing, False
        state = _AibotStreamState(
            msg_id=event_key,
            stream_id=_aibot_stream_id(event_key),
            audience_key=audience_key,
            source_hash=source_hash,
            updated_at=now,
        )
        _aibot_streams_by_msg_id[event_key] = state
        _aibot_streams_by_id[state.stream_id] = state
        return state, True


def _claim_aibot_stream(message: WeComAiBotMessage) -> _AibotStreamState | None:
    state, _ = _ensure_aibot_stream(message)
    with _aibot_streams_lock:
        if state.finish or state.processing:
            return None
        state.processing = True
        state.updated_at = time.monotonic()
        return state


def _get_aibot_stream(stream_id: str, *, audience_key: str) -> _AibotStreamState | None:
    with _aibot_streams_lock:
        _cleanup_aibot_streams_locked(time.monotonic())
        state = _aibot_streams_by_id.get(stream_id)
        if state is not None and state.audience_key != audience_key:
            return None
        if state is not None:
            state.updated_at = time.monotonic()
        return state


def _aibot_stream_response(state: _AibotStreamState) -> dict[str, Any]:
    with _aibot_streams_lock:
        content = _truncate_utf8(
            _markdown_to_wecom_text(state.content),
            WECOM_AIBOT_STREAM_MAX_BYTES,
        )
        return {
            "msgtype": "stream",
            "stream": {
                "id": state.stream_id,
                "finish": state.finish,
                "content": content,
            },
        }


def _append_aibot_stream_content(state: _AibotStreamState, content: str) -> None:
    if not content:
        return
    with _aibot_streams_lock:
        state.content = _truncate_utf8(
            state.content + content,
            WECOM_AIBOT_STREAM_MAX_BYTES,
        )
        state.updated_at = time.monotonic()


def _finish_aibot_stream(state: _AibotStreamState, content: str | None = None) -> None:
    with _aibot_streams_lock:
        if content is not None:
            state.content = _truncate_utf8(content, WECOM_AIBOT_STREAM_MAX_BYTES)
        state.finish = True
        state.processing = False
        state.updated_at = time.monotonic()


def aibot_callback_response(
    message: WeComAiBotMessage,
) -> tuple[dict[str, Any], bool]:
    """Return the passive stream response and whether processing should start."""
    if message.msg_type == "stream":
        state = _get_aibot_stream(message.stream_id or "", audience_key=_aibot_audience(message))
        if state is None:
            state = _AibotStreamState(
                msg_id=message.msg_id,
                stream_id=message.stream_id or _aibot_stream_id(message.msg_id),
                content=_fallback_reply(),
                finish=True,
                updated_at=time.monotonic(),
            )
        return _aibot_stream_response(state), False
    if message.msg_type != "text" or (
        message.chat_type == "group" and not message.chat_id
    ):
        return {}, False
    if not _normalize_aibot_prompt(message):
        return {}, False
    state, created = _ensure_aibot_stream(message)
    return _aibot_stream_response(state), created


def process_incoming_aibot_message(
    message: WeComAiBotMessage, *, config: WeComAiBotConfig
) -> None:
    """Generate a reply and publish it through passive callback refreshes."""
    timeout_seconds = config.timeout_seconds
    if message.msg_type != "text":
        return
    if message.chat_type == "group" and not message.chat_id:
        return
    prompt = _normalize_aibot_prompt(message)
    if not prompt:
        return
    state = _claim_aibot_stream(message)
    if state is None:
        return
    external_user_id = (
        f"wecom-aibot:{message.aibot_id}:{message.chat_type}:"
        f"{message.chat_id or message.from_user_id}"
    )
    memory_run = None
    failed_reply = None
    try:
        reply = _binding_command(
            prompt, channel_kind="aibot", channel_key=message.verified_channel_key,
            sender=message.from_user_id, private=message.chat_type == "single",
        )
        if reply is not None:
            state.audience_key = _aibot_audience(message)
            _finish_aibot_stream(state, reply)
            return
        memory_run = _prepare_wecom_memory(
            raw_text=message.content, channel_kind="aibot", channel_key=message.verified_channel_key,
            sender=message.from_user_id, private=message.chat_type == "single",
            event_id=message.msg_id, external_session_id=external_user_id,
        )
        reply = answer_wecom_knowledge(prompt)
        streamed_by_callback = False
        emitted = False

        def append_stream_delta(content: str) -> None:
            nonlocal emitted, streamed_by_callback
            streamed_by_callback = True
            emitted = True
            for chunk in _text_chunks(content, WECOM_AIBOT_STREAM_MAX_BYTES):
                if chunk:
                    _append_aibot_stream_content(state, chunk)

        content_parts = (
            iter((reply,))
            if reply is not None
            else _stream_cuihua_prompt(
                content=prompt,
                external_user_id=external_user_id,
                idempotency_key=f"wecom-aibot:{message.msg_id}",
                timeout_seconds=timeout_seconds,
                on_delta=append_stream_delta,
                memory_run=memory_run,
            )
        )
        for content in content_parts:
            if streamed_by_callback:
                continue
            for chunk in _text_chunks(content, WECOM_AIBOT_STREAM_MAX_BYTES):
                if chunk:
                    _append_aibot_stream_content(state, chunk)
                    emitted = True
        _require_current_wecom_binding(memory_run)
        if not emitted:
            raise WeComError("数字员工没有返回可显示的内容")
    except Exception:
        logger.warning("wecom aibot knowledge/agent stream failed", exc_info=False)
        failed_reply = _fallback_reply()
    finally:
        notice = _finish_wecom_memory(memory_run)
        if failed_reply is not None:
            _finish_aibot_stream(state, failed_reply + notice)
        else:
            if notice:
                _append_aibot_stream_content(state, notice)
            _finish_aibot_stream(state)


def _claim_incoming_message(msg_id: str | None) -> bool:
    if not msg_id:
        return True
    now = time.monotonic()
    with _incoming_processed_lock:
        expired = [
            key
            for key, processed_at in _incoming_processed.items()
            if now - processed_at >= _INCOMING_DEDUP_TTL_SECONDS
        ]
        for key in expired:
            _incoming_processed.pop(key, None)
        if msg_id in _incoming_processed or msg_id in _incoming_inflight:
            return False
        _incoming_inflight.add(msg_id)
        return True


def _finish_incoming_message(msg_id: str | None, *, delivered: bool) -> None:
    if not msg_id:
        return
    with _incoming_processed_lock:
        _incoming_inflight.discard(msg_id)
        if delivered:
            _incoming_processed[msg_id] = time.monotonic()


def process_incoming_message(message: WeComMessage, *, config: WeComConfig) -> None:
    """Answer text messages asynchronously and acknowledge failures safely."""
    if message.msg_type != "text" or not message.content.strip():
        return
    channel_key = binding_repo.verified_channel_key("app", config.corp_id, config.agent_id)
    event_key = (
        f"{channel_key}:{message.from_user_name}:{message.msg_id}" if message.msg_id else None
    )
    if not _claim_incoming_message(event_key):
        logger.info("duplicate wecom callback ignored msg_id=%s", message.msg_id)
        return

    delivered = False
    memory_run = None
    try:
        try:
            reply = _binding_command(
                message.content, channel_kind="app", channel_key=message.verified_channel_key,
                sender=message.from_user_name, private=_private_app_message(message),
            )
            if reply is None:
                memory_run = _prepare_wecom_memory(
                    raw_text=message.content, channel_kind="app",
                    channel_key=message.verified_channel_key, sender=message.from_user_name,
                    private=_private_app_message(message), event_id=message.msg_id,
                    external_session_id=f"wecom:{channel_key}:{message.from_user_name}",
                )
                reply = answer_wecom_knowledge(message.content)
                if reply is None:
                    reply = _ask_cuihua(message, config=config, memory_run=memory_run)
                _require_current_wecom_binding(memory_run)
        except Exception:
            logger.warning("wecom knowledge/agent reply failed", exc_info=False)
            reply = _fallback_reply()
        finally:
            reply_notice = _finish_wecom_memory(memory_run)
        try:
            _require_current_wecom_binding(memory_run)
            _send_text(config, to_user=message.from_user_name, content=reply + reply_notice)
        except Exception:
            logger.warning("wecom message delivery failed", exc_info=False)
        else:
            delivered = True
    finally:
        _finish_incoming_message(event_key, delivered=delivered)


def clear_access_token_cache() -> None:
    """Clear cached access tokens; intended for tests and controlled rotation."""
    with _access_token_lock:
        _access_token_cache.clear()


def clear_aibot_processed_cache() -> None:
    """Clear passive intelligent bot stream state for tests."""
    with _aibot_streams_lock:
        _aibot_streams_by_msg_id.clear()
        _aibot_streams_by_id.clear()


def clear_incoming_message_cache() -> None:
    """Clear self-built application callback state for tests and rotation."""
    with _incoming_processed_lock:
        _incoming_processed.clear()
        _incoming_inflight.clear()
