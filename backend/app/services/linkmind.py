from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, transaction
from app.models.access import Role, UserRole
from app.models.auth import LinkMindClientApiKey, User
from app.repositories import system_config

_SQLITE_BUSY_TIMEOUT_SECONDS = 30
_SQLITE_BUSY_RETRIES = 3
LINKMIND_ENDPOINT_SECRET_REF = "linkmind://user-client-api-key"
LINKMIND_BOX_INITIALIZED_CONFIG_KEY = "linkmind_box_initialized"
LINKMIND_BOX_INITIALIZATION_COMPLETE = "1"
LINKMIND_BOX_INITIALIZATION_PENDING = "pending"
LINKMIND_BOX_INITIALIZATION_STARTED = "started"

logger = logging.getLogger(__name__)

_SENSITIVE_LOG_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "tokenvalue",
}


def _safe_log_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if str(key).lower().replace("_", "") in _SENSITIVE_LOG_FIELDS
                else _safe_log_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_log_value(item) for item in value]
    return value


def _parse_created_at(value: object) -> int:
    if isinstance(value, (int, float)):
        try:
            return int(cast(int | float, value))
        except (TypeError, ValueError, OverflowError):
            return 0
    if not isinstance(value, str):
        return 0
    try:
        return int(value)
    except ValueError:
        try:
            return int(
                datetime.strptime(value, "%b %d, %Y %I:%M:%S %p")
                .replace(tzinfo=timezone.utc)
                .timestamp()
                * 1000
            )
        except ValueError:
            return 0


class LinkMindError(RuntimeError):
    def __init__(
        self,
        detail: str,
        status_code: int = 503,
        *,
        timed_out: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.timed_out = timed_out
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, slots=True)
class LinkMindKey:
    id: int
    user_id: str
    key_hint: str
    remark: str
    status: str
    created_at: int
    last_used_at: int | None
    revoked_at: int | None
    token: str | None = None


@dataclass(frozen=True, slots=True)
class LinkMindInitializationResult:
    deleted_token_statistics: int
    deleted_login_sessions: int
    initialized_at: int


def _parse_optional_created_at(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    parsed = _parse_created_at(value)
    if parsed <= 0:
        raise LinkMindError(f"LinkMind Key {field} 无效", status_code=502)
    return parsed


def enabled() -> bool:
    return settings.linkmind_enabled


def _database_path() -> Path:
    raw = settings.tokenlink_database_path.strip()
    if not raw:
        raise LinkMindError("TokenLink 中间库路径未配置")
    path = Path(raw)
    if not path.is_absolute():
        raise LinkMindError("TokenLink 中间库路径必须是绝对路径")
    if not path.parent.is_dir():
        raise LinkMindError("TokenLink 中间库挂载目录不存在")
    return path


def _directory_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=_SQLITE_BUSY_TIMEOUT_SECONDS)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _is_busy_database_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _with_directory_retry(operation):
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(_SQLITE_BUSY_RETRIES):
        connection: sqlite3.Connection | None = None
        try:
            connection = _directory_connection()
            with connection:
                return operation(connection)
        except sqlite3.OperationalError as exc:
            if not _is_busy_database_error(exc):
                raise LinkMindError("TokenLink 中间库操作失败") from exc
            last_error = exc
            if attempt + 1 < _SQLITE_BUSY_RETRIES:
                time.sleep(0.05 * (attempt + 1))
        finally:
            if connection is not None:
                connection.close()
    raise LinkMindError("TokenLink 中间库忙，请稍后重试") from last_error


def _ensure_users_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT NOT NULL PRIMARY KEY,
            role TEXT NOT NULL,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            user_login_status TEXT NOT NULL
        )
        """
    )
    # 存量中间库升级：翠花用户资料携带 display_name，LinkMind 侧展示用户时优先使用显示名。
    existing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "display_name" not in existing_columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )


def _role_code(db: Session, user: User) -> str:
    codes = set(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        )
    )
    if "system_admin" in codes:
        return "system_admin"
    if user.role == "admin" or "admin" in codes:
        return "admin"
    return "user"


def sync_user(db: Session, user: User) -> None:
    """Synchronize Cuihua-owned users into the shared TokenLink directory."""
    if not enabled():
        return
    # LinkMind only checks that the shared users.id exists for local User Keys.
    # Login state is mirrored for compatibility, but it is not model admission.
    raw_path = settings.tokenlink_database_path.strip()
    if (
        not raw_path
        or not Path(raw_path).is_absolute()
        or not Path(raw_path).parent.is_dir()
    ):
        logger.warning(
            "TokenLink directory unavailable, skipping user sync user_id=%s path=%s",
            user.id,
            raw_path or "<unset>",
        )
        return
    login_status = (
        "logged_out" if user.status != "active" else user.linkmind_login_status
    )
    if login_status not in {"logged_in", "logged_out"}:
        raise LinkMindError("TokenLink 登录状态无效")
    payload = {
        "role": _role_code(db, user),
        "username": user.username,
        "display_name": (user.display_name or "").strip(),
        "password_hash": user.password_hash,
        "user_login_status": login_status,
    }

    def upsert(connection: sqlite3.Connection) -> None:
        _ensure_users_schema(connection)
        connection.execute(
            """
            INSERT INTO users (id, role, username, display_name, password_hash, user_login_status)
            VALUES (:id, :role, :username, :display_name, :password_hash, :user_login_status)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                username = excluded.username,
                display_name = excluded.display_name,
                password_hash = excluded.password_hash,
                user_login_status = excluded.user_login_status
            """,
            {"id": user.id, **payload},
        )

    _with_directory_retry(upsert)


def sync_user_by_id(user_id: str) -> None:
    if not enabled():
        return
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise LinkMindError("待同步的用户不存在", status_code=404)
        sync_user(db, user)


class LinkMindClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.linkmind_base_url).strip().rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.linkmind_timeout_seconds
        self.transport = transport
        if not self.base_url:
            raise LinkMindError("LinkMind 服务地址未配置")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        success_outcome_unknown: bool = False,
    ) -> Any:
        request_url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        safe_url = request_url.split("?")[0]
        logger.info(
            "linkmind.request.start",
            extra={
                "http_method": method,
                "url": safe_url,
                "span": "linkmind.api",
            },
        )
        t0 = time.monotonic()
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.error(
                "linkmind.request.failed",
                extra={
                    "http_method": method,
                    "url": safe_url,
                    "duration_ms": duration_ms,
                    "error": f"timeout: {exc}",
                    "span": "linkmind.api",
                },
            )
            raise LinkMindError(
                "LinkMind 服务请求超时",
                timed_out=True,
                outcome_unknown=True,
            ) from exc
        except httpx.RequestError as exc:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.error(
                "linkmind.request.failed",
                extra={
                    "http_method": method,
                    "url": safe_url,
                    "duration_ms": duration_ms,
                    "error": f"connection_error: {exc}",
                    "span": "linkmind.api",
                },
            )
            raise LinkMindError(
                "LinkMind 服务不可达",
                outcome_unknown=True,
            ) from exc

        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        if response.status_code == 204:
            logger.info(
                "linkmind.request.end",
                extra={
                    "http_method": method,
                    "url": safe_url,
                    "status": 204,
                    "duration_ms": duration_ms,
                    "span": "linkmind.api",
                },
            )
            return None
        try:
            body = response.json()
        except ValueError:
            body = None

        log_level = "warning" if not response.is_success else "info"
        log_data: dict[str, Any] = {
            "http_method": method,
            "url": safe_url,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "span": "linkmind.api",
        }
        if not response.is_success:
            log_data["error_code"] = (
                body.get("code") if isinstance(body, dict) else None
            )
            log_data["error_msg"] = body.get("msg") if isinstance(body, dict) else None
        getattr(logger, log_level)("linkmind.request.end", extra=log_data)

        if not response.is_success:
            if response.status_code in {400, 422}:
                raise LinkMindError("LinkMind 请求参数无效", status_code=422)
            if response.status_code == 404:
                raise LinkMindError("LinkMind 资源不存在", status_code=404)
            if response.status_code == 409:
                raise LinkMindError("LinkMind Key 状态冲突", status_code=409)
            if response.status_code == 410:
                raise LinkMindError("LinkMind 旧接口已移除", status_code=410)
            if response.status_code in {401, 403}:
                raise LinkMindError("LinkMind 管理接口拒绝请求", status_code=502)
            if response.status_code in {502, 503, 504}:
                raise LinkMindError("LinkMind 上游服务暂不可用", status_code=502)
            raise LinkMindError("LinkMind 服务请求失败", status_code=502)
        if body is None:
            return None
        if not isinstance(body, dict) or body.get("status") != "success":
            raise LinkMindError(
                "LinkMind 返回数据无效",
                status_code=502,
                outcome_unknown=success_outcome_unknown,
            )
        return body.get("data")

    @staticmethod
    def _key_from_data(
        data: object,
        *,
        user_id: str,
        fallback: LinkMindKey | None = None,
    ) -> LinkMindKey:
        if not isinstance(data, dict):
            raise LinkMindError("LinkMind Key 数据无效", status_code=502)
        try:
            key_id = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LinkMindError("LinkMind Key 数据不完整", status_code=502) from exc
        raw_status = data.get("status")
        status = (
            str(
                raw_status
                if raw_status is not None
                else (fallback.status if fallback is not None else "")
            )
            .strip()
            .lower()
        )
        if status not in {"active", "revoked"}:
            raise LinkMindError("LinkMind Key 状态无效", status_code=502)
        token_value = data.get("apiKey") or data.get("token")
        token = (
            str(token_value)
            if token_value is not None
            else (fallback.token if fallback is not None else None)
        )
        key_hint = str(
            data.get("keyHint")
            or (fallback.key_hint if fallback is not None else "")
            or (f"{token[:8]}...{token[-4:]}" if token else "")
        )
        created_at = (
            _parse_created_at(data.get("createdAt"))
            if data.get("createdAt") is not None
            else (fallback.created_at if fallback is not None else 0)
        )
        last_used_at = (
            _parse_optional_created_at(data.get("lastUsedAt"), field="lastUsedAt")
            if data.get("lastUsedAt") is not None
            else (fallback.last_used_at if fallback is not None else None)
        )
        revoked_at = (
            _parse_optional_created_at(data.get("revokedAt"), field="revokedAt")
            if data.get("revokedAt") is not None
            else (fallback.revoked_at if fallback is not None else None)
        )
        return LinkMindKey(
            id=key_id,
            user_id=str(data.get("userId") or user_id),
            key_hint=key_hint,
            remark=str(
                data.get("remark")
                if data.get("remark") is not None
                else (fallback.remark if fallback is not None else "")
            ),
            status=status,
            created_at=created_at,
            last_used_at=last_used_at,
            revoked_at=revoked_at,
            token=token,
        )

    def create_key(self, user_id: str, *, remark: str) -> LinkMindKey:
        data = self._request(
            "POST",
            f"/v1/integration/users/{quote(user_id, safe='')}/user-api-keys",
            json={"remark": remark},
        )
        return self._key_from_data(data, user_id=user_id)

    def initialize_box(self, actor_id: str) -> LinkMindInitializationResult:
        data = self._request(
            "POST",
            "/v1/integration/initialize",
            json={"confirmation": "RESET_USAGE_AND_SESSIONS"},
            headers={"X-Cuihua-Actor-Id": actor_id},
            success_outcome_unknown=True,
        )
        if not isinstance(data, dict):
            raise LinkMindError(
                "LinkMind 初始化结果无效",
                status_code=502,
                outcome_unknown=True,
            )
        values = (
            data.get("deletedTokenStatistics"),
            data.get("deletedLoginSessions"),
            data.get("initializedAt"),
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise LinkMindError(
                "LinkMind 初始化结果无效",
                status_code=502,
                outcome_unknown=True,
            )
        return LinkMindInitializationResult(
            deleted_token_statistics=values[0],
            deleted_login_sessions=values[1],
            initialized_at=values[2],
        )

    def list_keys(self, user_id: str) -> list[LinkMindKey]:
        page = 1
        page_size = 100
        keys: list[LinkMindKey] = []
        while True:
            data = self._request(
                "GET",
                f"/v1/integration/users/{quote(user_id, safe='')}/user-api-keys",
                params={"status": "all", "page": page, "pageSize": page_size},
            )
            if not isinstance(data, dict):
                raise LinkMindError("LinkMind Key 列表无效", status_code=502)
            items = data.get("items")
            if not isinstance(items, list):
                raise LinkMindError("LinkMind Key 列表无效", status_code=502)
            keys.extend(self._key_from_data(item, user_id=user_id) for item in items)
            try:
                total = int(data.get("total", len(keys)))
            except (TypeError, ValueError) as exc:
                raise LinkMindError("LinkMind Key 总数无效", status_code=502) from exc
            if not items or len(keys) >= total or len(items) < page_size:
                return keys
            page += 1

    def update_key(
        self,
        user_id: str,
        key_id: int,
        *,
        remark: str,
        existing: LinkMindKey | None = None,
    ) -> LinkMindKey:
        data = self._request(
            "PATCH",
            f"/v1/integration/users/{quote(user_id, safe='')}/user-api-keys/{key_id}",
            json={"remark": remark},
        )
        return self._key_from_data(data, user_id=user_id, fallback=existing)

    def revoke_key(
        self,
        user_id: str,
        key_id: int,
        *,
        existing: LinkMindKey | None = None,
    ) -> LinkMindKey:
        data = self._request(
            "POST",
            f"/v1/integration/users/{quote(user_id, safe='')}/user-api-keys/{key_id}/revoke",
        )
        if data is None and existing is not None:
            return replace(existing, status="revoked")
        return self._key_from_data(data, user_id=user_id, fallback=existing)

    def token_usage(
        self, user_id: str, endpoint: str, **params: object
    ) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/v1/integration/users/{quote(user_id, safe='')}/token-usage/{endpoint}",
            params={key: value for key, value in params.items() if value is not None},
        )
        if not isinstance(data, dict):
            raise LinkMindError("LinkMind 用量数据无效", status_code=502)
        return data


def get_client() -> LinkMindClient:
    if not enabled():
        raise LinkMindError("LinkMind 集成未启用")
    return LinkMindClient()


def initialize_box(actor_id: str) -> LinkMindInitializationResult:
    result = get_client().initialize_box(actor_id)
    logger.info(
        "LinkMind box initialized actor_id=%s deleted_token_statistics=%s "
        "deleted_login_sessions=%s initialized_at=%s",
        actor_id,
        result.deleted_token_statistics,
        result.deleted_login_sessions,
        result.initialized_at,
    )
    return result


def initialize_box_once(
    actor_id: str,
) -> LinkMindInitializationResult | None:
    with transaction() as db:
        state = system_config.get_all(db).get(LINKMIND_BOX_INITIALIZED_CONFIG_KEY)
        if state == LINKMIND_BOX_INITIALIZATION_COMPLETE:
            return None
        if state == LINKMIND_BOX_INITIALIZATION_STARTED:
            raise LinkMindError(
                "LinkMind 初始化结果未知，已禁止自动重试；请先检查 LinkMind 状态",
                status_code=503,
                outcome_unknown=True,
            )
        if not settings.linkmind_initialize_box_on_setup:
            logger.info("复用既有 LinkMind：跳过 setup 的统计/会话重置，保持原初始化记录")
            return None
        system_config.upsert(
            db,
            LINKMIND_BOX_INITIALIZED_CONFIG_KEY,
            LINKMIND_BOX_INITIALIZATION_STARTED,
            "LinkMind 盒子初始化状态",
        )

    try:
        result = initialize_box(actor_id)
    except LinkMindError as exc:
        if not exc.outcome_unknown:
            with transaction() as db:
                system_config.upsert(
                    db,
                    LINKMIND_BOX_INITIALIZED_CONFIG_KEY,
                    LINKMIND_BOX_INITIALIZATION_PENDING,
                    "LinkMind 盒子初始化状态",
                )
        raise

    with transaction() as db:
        system_config.upsert(
            db,
            LINKMIND_BOX_INITIALIZED_CONFIG_KEY,
            LINKMIND_BOX_INITIALIZATION_COMPLETE,
            "LinkMind 盒子初始化完成标记",
        )
    return result


def _fernet() -> Fernet:
    raw = settings.linkmind_key_encryption_key.strip()
    if not raw:
        raise LinkMindError("LinkMind 客户端 Key 加密密钥未配置", status_code=502)
    try:
        return Fernet(raw.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise LinkMindError(
            "LinkMind 客户端 Key 加密密钥无效", status_code=502
        ) from exc


def _timestamp_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _persist_key(
    key: LinkMindKey,
    *,
    token: str | None,
    db: Session | None = None,
) -> None:
    if db is None:
        with transaction() as session:
            _persist_key(key, token=token, db=session)
        return
    encrypted_token = (
        _fernet().encrypt(token.encode("utf-8")).decode("ascii")
        if token is not None
        else None
    )
    stored = db.get(LinkMindClientApiKey, key.id)
    if stored is None:
        if key.created_at <= 0:
            raise LinkMindError("LinkMind Key 缺少创建时间", status_code=502)
        stored = LinkMindClientApiKey(
            id=key.id,
            user_id=key.user_id,
            token_prefix=key.key_hint,
            remark=key.remark,
            enabled=key.status == "active",
            created_at=_timestamp_to_datetime(key.created_at),
            expires_at=None,
            encrypted_token=encrypted_token,
        )
        db.add(stored)
        return
    if key.key_hint:
        stored.token_prefix = key.key_hint
    if key.remark:
        stored.remark = key.remark
    stored.enabled = key.status == "active"
    if key.created_at > 0:
        stored.created_at = _timestamp_to_datetime(key.created_at)
    stored.expires_at = None
    if encrypted_token is not None:
        stored.encrypted_token = encrypted_token


def _default_key_remark(user_id: str, db: Session | None = None) -> str:
    def resolve(session: Session) -> str:
        user = session.get(User, user_id)
        if user is None:
            raise LinkMindError("待创建 LinkMind Key 的用户不存在", status_code=404)
        return f"{user.username}-default"

    if db is None:
        with SessionLocal() as session:
            return resolve(session)
    return resolve(db)


def create_key(
    user_id: str,
    *,
    remark: str | None = None,
    db: Session | None = None,
) -> LinkMindKey:
    remark = remark or _default_key_remark(user_id, db=db)
    client = get_client()
    key = client.create_key(user_id, remark=remark)
    key = replace(key, user_id=user_id)
    if not key.token:
        with contextlib.suppress(LinkMindError):
            client.revoke_key(user_id, key.id, existing=key)
        raise LinkMindError("LinkMind 创建 Key 未返回明文，已尝试吊销", status_code=502)
    try:
        if db is None:
            _persist_key(key, token=key.token)
        else:
            _persist_key(key, token=key.token, db=db)
    except Exception as exc:
        with contextlib.suppress(LinkMindError):
            client.revoke_key(user_id, key.id, existing=key)
        if isinstance(exc, LinkMindError):
            raise
        raise LinkMindError("LinkMind Key 无法安全保存，已尝试吊销") from exc
    return key


def _decrypt_stored_token(stored: LinkMindClientApiKey) -> str:
    if not stored.encrypted_token:
        raise LinkMindError("LinkMind Key 没有可用的加密凭据", status_code=503)
    try:
        return _fernet().decrypt(stored.encrypted_token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise LinkMindError("已保存的 LinkMind Key 无法解密", status_code=503) from exc


def get_user_key(user_id: str, *, db: Session | None = None) -> str | None:
    def find(session: Session) -> LinkMindClientApiKey | None:
        return session.scalar(
            select(LinkMindClientApiKey)
            .where(
                LinkMindClientApiKey.user_id == user_id,
                LinkMindClientApiKey.enabled.is_(True),
                LinkMindClientApiKey.encrypted_token.is_not(None),
            )
            .order_by(LinkMindClientApiKey.created_at.desc())
        )

    if db is None:
        with SessionLocal() as session:
            stored = find(session)
            return _decrypt_stored_token(stored) if stored else None
    stored = find(db)
    return _decrypt_stored_token(stored) if stored else None


def ensure_user_key(user_id: str, *, db: Session | None = None) -> str:
    if not enabled():
        logger.info("LinkMind user key skipped user_id=%s reason=disabled", user_id)
        raise LinkMindError("LinkMind 集成未启用", status_code=503)
    existing = get_user_key(user_id, db=db)
    logger.info(
        "LinkMind user key lookup user_id=%s stored_key=%s",
        user_id,
        bool(existing),
    )
    if existing:
        return existing
    logger.info("LinkMind user key create requested user_id=%s", user_id)
    key = create_key(user_id, db=db)
    if not key.token:
        raise LinkMindError("LinkMind 创建 Key 未返回明文", status_code=502)
    logger.info(
        "LinkMind user key persisted user_id=%s key_id=%s key_hint=%s",
        user_id,
        key.id,
        key.key_hint,
    )
    return key.token


def list_keys(user_id: str) -> list[tuple[LinkMindKey, bool]]:
    keys = [replace(key, user_id=user_id) for key in get_client().list_keys(user_id)]
    with transaction() as db:
        for key in keys:
            _persist_key(key, token=None, db=db)
        stored = {
            row.id: row.encrypted_token is not None
            for row in db.scalars(
                select(LinkMindClientApiKey).where(
                    LinkMindClientApiKey.user_id == user_id
                )
            )
        }
    return [(key, stored.get(key.id, False)) for key in keys]


def update_key(user_id: str, key_id: int, *, remark: str) -> LinkMindKey:
    client = get_client()
    existing = next(
        (key for key in client.list_keys(user_id) if key.id == key_id),
        None,
    )
    if existing is None:
        raise LinkMindError("LinkMind Key 不存在", status_code=404)
    key = client.update_key(
        user_id,
        key_id,
        remark=remark,
        existing=existing,
    )
    key = replace(key, user_id=user_id)
    _persist_key(key, token=None)
    return key


def revoke_key(user_id: str, key_id: int) -> LinkMindKey:
    client = get_client()
    existing = next(
        (key for key in client.list_keys(user_id) if key.id == key_id),
        None,
    )
    if existing is None:
        raise LinkMindError("LinkMind Key 不存在", status_code=404)
    key = client.revoke_key(user_id, key_id, existing=existing)
    key = replace(key, user_id=user_id)
    _persist_key(key, token=None)
    return key


def disable_all_keys(user_id: str) -> None:
    client = get_client()
    for key in client.list_keys(user_id):
        if key.status == "active":
            updated = client.revoke_key(user_id, key.id, existing=key)
            _persist_key(replace(updated, user_id=user_id), token=None)


def revoke_all_keys(user_id: str) -> None:
    # The new LinkMind contract never physically deletes User Keys. Preserve
    # history by revoking every active key and retaining local cache metadata.
    disable_all_keys(user_id)


def reveal_key(user_id: str, key_id: int) -> str:
    with SessionLocal() as db:
        stored = db.get(LinkMindClientApiKey, key_id)
        if stored is None or stored.user_id != user_id or not stored.encrypted_token:
            raise LinkMindError("该 LinkMind Key 没有可显示的明文", status_code=404)
        encrypted_token = stored.encrypted_token
    try:
        return _fernet().decrypt(encrypted_token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise LinkMindError("已保存的 LinkMind Key 无法解密", status_code=503) from exc


def key_payload(key: LinkMindKey, *, can_reveal: bool = False) -> dict[str, Any]:
    return {
        "id": key.id,
        "user_id": key.user_id,
        "key_hint": key.key_hint,
        "remark": key.remark,
        "status": key.status,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "revoked_at": key.revoked_at,
        "can_reveal": can_reveal,
    }


def delete_shared_user(user_id: str) -> None:
    if not enabled():
        return

    def delete(connection: sqlite3.Connection) -> None:
        _ensure_users_schema(connection)
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    _with_directory_retry(delete)


def sso_url(db: Session, user: User) -> str | None:
    if not enabled() or _role_code(db, user) != "system_admin":
        return None
    if user.first_login:
        return None
    base_url = (settings.linkmind_public_url or settings.linkmind_base_url).strip()
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/sso/login?userId={quote(user.id, safe='')}"
