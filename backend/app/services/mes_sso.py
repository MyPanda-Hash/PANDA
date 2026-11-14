"""MES 单点登录票据：light-mes 签发，本服务验签后免密建立会话。

协议（两端必须一致，light-mes 侧见 AiSsoService）：

    ticket = base64url(payload_json) + "." + base64url(hmac_sha256(secret, base64url(payload_json)))
    payload = {"sub": 账号, "name": 姓名, "role": 角色码, "admin": 是否管理员,
               "iat": 签发秒, "exp": 过期秒, "jti": 一次性编号}

设计取舍：
  * 共享密钥走环境变量（DOC_NEXUS_MES_SSO_SECRET），留空即关闭该入口。
  * 票据只活 60 秒，且只经 HTTPS/内网跳转一次；未做「用过即失效」的
    服务端登记，因为那需要在 MES 侧维护共享状态。60 秒窗口是这里的
    已知残余风险，票据泄露也只能到此为止。
  * 不校验 iat 上限之外的时钟漂移，只用 exp + 5 秒容差。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings

#: 允许的时钟漂移（秒）：两端机器时间不完全同步时仍能验过
CLOCK_SKEW_SECONDS = 5


class MesSsoError(RuntimeError):
    """票据不合法或未启用单点登录。"""


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _secret() -> str:
    return (settings.mes_sso_secret or "").strip()


def enabled() -> bool:
    return bool(_secret())


def sign(payload: dict, secret: str | None = None) -> str:
    """按协议签发票据（light-mes 侧实现同一算法；这里保留一份用于测试与排查）。"""
    key = (secret if secret is not None else _secret()).encode("utf-8")
    body = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64encode(signature)


def verify_ticket(ticket: str) -> dict:
    """校验票据并返回身份。任何不合法情形都抛 MesSsoError。"""
    secret = _secret()
    if not secret:
        raise MesSsoError("未配置 DOC_NEXUS_MES_SSO_SECRET")

    parts = (ticket or "").strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise MesSsoError("票据格式错误")
    body, signature = parts

    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii", "ignore"), hashlib.sha256).digest()
    try:
        provided = _b64decode(signature)
    except Exception as exc:  # noqa: BLE001 - 任意解码错误都算票据不合法
        raise MesSsoError("票据签名格式错误") from exc
    if not hmac.compare_digest(expected, provided):
        raise MesSsoError("票据签名不匹配")

    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise MesSsoError("票据内容无法解析") from exc
    if not isinstance(payload, dict):
        raise MesSsoError("票据内容无法解析")

    username = str(payload.get("sub") or "").strip()
    if not username:
        raise MesSsoError("票据缺少账号")

    try:
        expires_at = float(payload.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise MesSsoError("票据缺少有效期") from exc
    if expires_at <= 0:
        raise MesSsoError("票据缺少有效期")
    if time.time() > expires_at + CLOCK_SKEW_SECONDS:
        raise MesSsoError("票据已过期")

    return {
        "username": username,
        "real_name": str(payload.get("name") or username).strip() or username,
        "role_code": str(payload.get("role") or "").strip(),
        "is_admin": bool(payload.get("admin")),
    }


__all__ = ["CLOCK_SKEW_SECONDS", "MesSsoError", "enabled", "sign", "verify_ticket"]
