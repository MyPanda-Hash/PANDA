"""独立于站内事件的外部通知投递适配器。"""
from __future__ import annotations
import binascii

import base64
import hashlib
import hmac
import json
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class NotificationSecretError(ValueError):
    pass


@dataclass(frozen=True)
class DeliveryResult:
    status: str  # accepted / retry_wait / unknown / failed
    code: str
    provider_message_id: str | None = None


def _keys() -> dict[str, str]:
    try:
        raw = json.loads(settings.notification_fernet_keys or "{}")
    except (TypeError, ValueError) as exc:
        raise NotificationSecretError("通知加密密钥配置无效") from exc
    if not isinstance(raw, dict):
        raise NotificationSecretError("通知加密密钥配置无效")
    return raw


def _fernet(version: str) -> Fernet:
    key = _keys().get(str(version))
    if not isinstance(key, str) or not key:
        raise NotificationSecretError("通知加密密钥不可用")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise NotificationSecretError("通知加密密钥配置无效") from exc


def encrypt_secret(value: str) -> tuple[str, str]:
    version = str(settings.notification_fernet_current_version)
    return _fernet(version).encrypt(value.encode("utf-8")).decode("ascii"), version


def challenge_digest(target_id: str, revision: int, code: str, version: str | None) -> str:
    """验证码不可用无密钥摘要离线枚举；密钥版本绑定该目标。"""
    if version is None:
        raise NotificationSecretError("通知密钥不可用")
    try:
        key = base64.urlsafe_b64decode(_keys()[version])
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise NotificationSecretError("通知密钥不可用") from exc
    return hmac.new(key, f"{target_id}:{revision}:{code}".encode(), hashlib.sha256).hexdigest()


def operation_digests(payload: str) -> tuple[str, list[str]]:
    """幂等摘要使用独立通知密钥 HMAC，避免存储可离线枚举的 SMTP 口令哈希。"""
    versions = _keys()
    current = str(settings.notification_fernet_current_version)
    if current not in versions:
        raise NotificationSecretError("通知密钥不可用")
    try:
        digests = {
            version: hmac.new(base64.urlsafe_b64decode(key), payload.encode(), hashlib.sha256).hexdigest()
            for version, key in versions.items()
        }
    except (ValueError, TypeError, binascii.Error) as exc:
        raise NotificationSecretError("通知密钥配置无效") from exc
    return digests[current], list(digests.values())


def decrypt_secret(ciphertext: str | None, version: str | int | None) -> str:
    if not ciphertext or version is None:
        raise NotificationSecretError("通知凭据不可用")
    try:
        return _fernet(str(version)).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise NotificationSecretError("通知凭据解密失败") from exc


def fingerprint(value: str) -> str:
    """仅用于已加密凭据的管理端指纹，不能暴露可离线猜测的无密钥口令摘要。"""
    digest, _ = operation_digests(value)
    return digest[:16]


def _credentials(connection) -> dict[str, str]:
    value = json.loads(decrypt_secret(connection.credential_ciphertext, connection.credential_key_version))
    if not isinstance(value, dict):
        raise NotificationSecretError("通知凭据格式无效")
    return value


def _smtp(connection, target, text: str, *, subject: str = "定时任务状态通知") -> DeliveryResult:
    config = json.loads(connection.config_json)
    creds = _credentials(connection)
    recipient = decrypt_secret(target.address_ciphertext, target.address_key_version)
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    # TLS 必须验证服务器证书及主机名；明文 SMTP 不提供降级路径。
    try:
        if config["tls_mode"] == "implicit":
            client = smtplib.SMTP_SSL(config["host"], config["port"], timeout=10, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(config["host"], config["port"], timeout=10)
    except (OSError, smtplib.SMTPException):
        return DeliveryResult("retry_wait", "smtp_connect")
    try:
        with client:
            if config["tls_mode"] == "starttls":
                client.starttls(context=ssl.create_default_context())
            client.login(config["username"], creds["password"])
            try:
                refused = client.send_message(message)
            except (OSError, smtplib.SMTPServerDisconnected, TimeoutError):
                # DATA 已发出时断线无法获知远端是否接收，禁止自动重试。
                return DeliveryResult("unknown", "smtp_outcome_unknown")
            if refused:
                return DeliveryResult("failed", "smtp_recipient_rejected")
            return DeliveryResult("accepted", "smtp_accepted", message["Message-ID"])
    except (ssl.SSLError, smtplib.SMTPAuthenticationError):
        return DeliveryResult("failed", "smtp_tls_or_auth")
    except (OSError, smtplib.SMTPException):
        return DeliveryResult("unknown", "smtp_outcome_unknown")


def _webhook(connection, text: str) -> DeliveryResult:
    credentials = _credentials(connection)
    token = credentials["access_token"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
        return DeliveryResult("failed", "invalid_webhook_token")
    if connection.kind == "dingtalk_group":
        secret = credentials["secret"]
        timestamp = str(int(time.time() * 1000))
        signature = base64.b64encode(hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()).decode()
        url = "https://oapi.dingtalk.com/robot/send?" + urlencode({"access_token": token, "timestamp": timestamp, "sign": signature})
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?" + urlencode({"key": token})
        payload = {"msgtype": "text", "text": {"content": text}}
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post(url, json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return DeliveryResult("retry_wait", "webhook_connect")
    except httpx.RequestError:
        return DeliveryResult("unknown", "webhook_outcome_unknown")
    if response.is_redirect:
        return DeliveryResult("failed", "webhook_redirect")
    if response.status_code in (408, 429) or response.status_code >= 500:
        return DeliveryResult("unknown", "webhook_http_uncertain")
    if response.status_code != 200:
        return DeliveryResult("failed", "webhook_http_rejected")
    try:
        body = response.json()
        code = body["errcode"]
        if not isinstance(code, int):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return DeliveryResult("unknown", "webhook_response_invalid")
    if code == 0:
        return DeliveryResult("accepted", "webhook_accepted")
    # 业务层拒绝是明确结果，不把限流/参数错误盲目重发。
    return DeliveryResult("failed", "webhook_business_rejected")


def send_notification(connection, target, text: str) -> DeliveryResult:
    """只接收受信任数据库记录，不从调用者输入构造任意 URL。"""
    if connection.status != "active" or target.status != "active" or target.connection_id != connection.id or target.verified_at is None:
        return DeliveryResult("failed", "target_inactive")
    if not isinstance(text, str) or len(text) > 4000:
        return DeliveryResult("failed", "content_invalid")
    try:
        if connection.kind == "smtp" and target.kind == "email":
            return _smtp(connection, target, text)
        if connection.kind in ("dingtalk_group", "wecom_group") and target.kind == connection.kind:
            return _webhook(connection, text)
        return DeliveryResult("failed", "channel_mismatch")
    except (NotificationSecretError, KeyError, ValueError, TypeError):
        return DeliveryResult("failed", "channel_configuration_invalid")


def send_verification_email(connection, target, code: str) -> DeliveryResult:
    """仅向待验证地址发送固定文案，不接受任意内容或外部收件人。"""
    if connection.kind != "smtp" or connection.status != "active" or target.kind != "email" or target.connection_id != connection.id:
        return DeliveryResult("failed", "verification_target_invalid")
    if not re.fullmatch(r"[0-9]{6}", code):
        return DeliveryResult("failed", "verification_code_invalid")
    try:
        return _smtp(connection, target, f"您的 Doc Nexus 通知邮箱验证码：{code}。10 分钟内有效；如果不是本人操作，请忽略。", subject="验证通知邮箱")
    except (NotificationSecretError, KeyError, ValueError, TypeError):
        return DeliveryResult("failed", "channel_configuration_invalid")
