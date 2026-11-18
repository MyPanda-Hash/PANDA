"""Encrypted model grants and per-turn usage ledgers for Pi child processes."""

from __future__ import annotations

import base64
import binascii
import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.data_guard import MaskSession, session_for_endpoint
from app.services.model_endpoint_config import (
    ADAPTER_AZURE_OPENAI,
    PROTOCOL_CHAT_COMPLETIONS,
    PROTOCOL_MESSAGES,
    PROTOCOL_RESPONSES,
    normalize_azure_openai_base_url,
)
from app.services.pi_runtime.config import ResolvedPiModel


@dataclass(frozen=True, slots=True)
class ModelProxyGrant:
    token: str
    model: ResolvedPiModel
    expires_at: float | None
    max_tokens: int | None = None
    track_usage: bool = False
    require_verified_usage: bool = False


@dataclass(frozen=True, slots=True)
class PreparedModelProxyRequest:
    url: str
    query: tuple[tuple[str, str], ...]
    body: bytes
    #: 本次出网的脱敏会话；响应流要按它的映射把占位符换回真实值
    mask_session: MaskSession | None = None


_AAD = b"doc-nexus-pi-model-proxy-v1"
_MAX_REQUESTS = 64
# 启用用量核验的轮次由独立请求上限兜底；该上限只防止不消耗
# Token 的失控循环，不承担 Token 阈值阻断职责。
_MAX_BUDGETED_REQUESTS = 512
_MAX_CONCURRENT_REQUESTS = 2
_MAX_USAGE_FRAME_BYTES = 1_048_576
# 单个非流式 JSON 响应可能大于一帧 SSE。
_MAX_USAGE_BODY_BYTES = 8_388_608
# 无法核验用量的响应按保守值记账，避免未计量调用无限重试。
_MAX_UNMETERED_STREAK = 3
_UNMETERED_TOKEN_CHARGE = 65_536
_MAX_RETRY_AFTER_SECONDS = 3_600
_SSE_NEWLINE = re.compile(rb"[\r\n]")

_BUDGET_ERROR_MESSAGES = {
    "chat_token_budget_exceeded": (
        "本轮聊天累计输入与输出 Token 已达到预算，请发送新消息继续。"
    ),
    "chat_request_budget_exceeded": (
        "本轮聊天的模型调用次数已达到上限，请发送新消息继续。"
    ),
    "chat_token_usage_unavailable": (
        "模型未返回可核实的 Token 用量，本轮已停止继续调用，请发送新消息重试。"
    ),
}


class ModelProxyBudgetError(RuntimeError):
    """A nonretryable turn-budget admission failure, not an invalid credential."""

    def __init__(self, code: str):
        self.code = code
        message = _BUDGET_ERROR_MESSAGES.get(
            code, _BUDGET_ERROR_MESSAGES["chat_token_usage_unavailable"]
        )
        super().__init__(f"{message} ({code})")


def _token_count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("token counts must be nonnegative integers")
    return value


def _retry_after_seconds(
    value: object,
    *,
    now: datetime | None = None,
) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 128 or "\r" in text or "\n" in text:
        return None
    if text.isascii() and text.isdigit():
        seconds = int(text)
    else:
        try:
            target = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        seconds = math.ceil((target - current).total_seconds())
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _normalized_usage(usage: object) -> dict[str, int]:
    if not isinstance(usage, dict):
        raise ValueError("missing model usage")
    prompt = _token_count(usage["prompt_tokens"])
    completion = _token_count(usage["completion_tokens"])
    cache_read = _token_count(usage.get("cache_read_tokens", 0))
    cache_write = _token_count(usage.get("cache_write_tokens", 0))
    uncached = _token_count(
        usage.get("input_uncached_tokens", prompt - cache_read - cache_write)
    )
    if uncached + cache_read + cache_write != prompt:
        raise ValueError("inconsistent input token usage")
    total = prompt + completion
    if "total_tokens" in usage:
        # 某些供应商会另计 reasoning 或工具 Token；取较大总量，
        # 不因方言差异把可核验用量误判为未知。
        total = max(total, _token_count(usage["total_tokens"]))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "input_uncached_tokens": uncached,
    }


class ModelProxyUsage:
    """Extract one response's cumulative usage without retaining its transcript."""

    def __init__(self, api_protocol: str):
        self._protocol = api_protocol
        self._mode = ""
        self._line_limit = _MAX_USAGE_FRAME_BYTES
        self._line = bytearray()
        self._data = bytearray()
        self._event_type = ""
        self._after_cr = False
        self._overflow = False
        self._skip_event = False
        self._usage_skipped = False
        self._invalid = False
        self._complete = False
        self._finished = False
        self._prompt: int | None = None
        self._completion: int | None = None
        self._cache_read = 0
        self._cache_write = 0
        self._reported_total = 0

    @property
    def complete(self) -> bool:
        return self._complete

    def feed(self, chunk: bytes) -> None:
        if not chunk or self._complete or self._finished:
            return
        start = 0
        if not self._mode:
            while start < len(chunk) and chunk[start] in b" \t\r\n":
                start += 1
            if start == len(chunk):
                return
            self._mode = "json" if chunk[start] in b"{[" else "sse"
            self._line_limit = (
                _MAX_USAGE_BODY_BYTES if self._mode == "json"
                else _MAX_USAGE_FRAME_BYTES
            )
        view = memoryview(chunk)
        if self._mode == "json":
            self._append_line(view[start:])
            return
        for match in _SSE_NEWLINE.finditer(chunk, start):
            end = match.start()
            if self._after_cr and end == start and chunk[end] == 10:
                self._after_cr = False
                start = end + 1
                continue
            self._append_line(view[start:end])
            self._consume_line()
            self._after_cr = chunk[end] == 13
            start = end + 1
            if self._complete:
                return
        if start < len(chunk):
            self._after_cr = False
            self._append_line(view[start:])

    def _append_line(self, fragment: memoryview) -> None:
        if self._overflow:
            return
        if len(self._line) + len(fragment) > self._line_limit:
            self._line.clear()
            self._overflow = True
            self._skip_event = True
            self._usage_skipped = True
            return
        self._line.extend(fragment)

    def _consume_line(self) -> None:
        if self._overflow:
            self._overflow = False
            self._line.clear()
            return
        if not self._line:
            self._consume_event()
        elif self._skip_event:
            self._line.clear()
            return
        elif self._line.startswith(b"data:"):
            offset = 6 if self._line[5:6] == b" " else 5
            size = len(self._line) - offset
            if len(self._data) + size + 1 > _MAX_USAGE_FRAME_BYTES:
                self._skip_event = True
                self._usage_skipped = True
                self._data.clear()
            else:
                if self._data:
                    self._data.extend(b"\n")
                self._data.extend(memoryview(self._line)[offset:])
        elif self._line.startswith(b"event:"):
            # Only protocol event names matter; arbitrary event labels stay bounded.
            self._event_type = self._line[6:134].strip().decode("ascii", "replace")
        self._line.clear()

    def _consume_event(self) -> None:
        if not self._skip_event:
            if self._data == b"[DONE]" and self._protocol == PROTOCOL_CHAT_COMPLETIONS:
                self._complete = True
            elif self._data:
                self._consume_json(self._data, self._event_type)
        self._skip_event = False
        self._data.clear()
        self._event_type = ""

    def _consume_json(self, raw: bytearray, event_type: str = "") -> None:
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._invalid = True
            return
        if not isinstance(payload, dict):
            self._invalid = True
            return
        event_type = payload.get("type") or event_type
        if self._protocol == PROTOCOL_CHAT_COMPLETIONS:
            usage = payload.get("usage")
            input_key, output_key = "prompt_tokens", "completion_tokens"
        elif self._protocol == PROTOCOL_RESPONSES:
            if event_type in (
                "response.completed", "response.incomplete", "response.failed"
            ):
                self._complete = True
            response = payload.get("response", payload)
            usage = response.get("usage") if isinstance(response, dict) else None
            input_key, output_key = "input_tokens", "output_tokens"
        elif self._protocol == PROTOCOL_MESSAGES:
            if event_type == "message_stop":
                self._complete = True
            message = payload.get("message", payload)
            usage = message.get("usage") if isinstance(message, dict) else None
            input_key, output_key = "input_tokens", "output_tokens"
        else:
            self._invalid = True
            return
        if usage is None:
            return
        if not isinstance(usage, dict):
            self._invalid = True
            return
        try:
            if self._protocol == PROTOCOL_MESSAGES and event_type == "message_delta":
                output = _token_count(usage[output_key])
                self._completion = max(self._completion or 0, output)
                if self._prompt is not None:
                    self._usage_skipped = False
                return
            prompt = _token_count(usage[input_key])
            output = _token_count(usage[output_key])
            cache_write = 0
            if self._protocol == PROTOCOL_MESSAGES:
                cache_read = _token_count(usage.get("cache_read_input_tokens", 0))
                cache_write = _token_count(usage.get("cache_creation_input_tokens", 0))
                prompt += cache_read + cache_write
            else:
                details = usage.get(f"{input_key}_details")
                if details is None:
                    details = {}
                if not isinstance(details, dict):
                    raise ValueError("invalid cached token usage")
                cache_read = _token_count(details.get("cached_tokens", 0))
            if cache_read + cache_write > prompt:
                raise ValueError("cached usage exceeds total input usage")
            reported_total = usage.get("total_tokens")
            if reported_total is not None:
                self._reported_total = max(
                    self._reported_total, _token_count(reported_total)
                )
            self._prompt = max(self._prompt or 0, prompt)
            self._completion = max(self._completion or 0, output)
            self._cache_read = max(self._cache_read, cache_read)
            self._cache_write = max(self._cache_write, cache_write)
            self._usage_skipped = False
        except (ValueError, TypeError, KeyError):
            self._invalid = True

    def finish(self, *, stream_ended: bool = False) -> dict[str, int] | None:
        """Close the response; ``stream_ended`` marks a cleanly drained body.

        Providers are inconsistent about terminal events (``[DONE]`` is
        optional in several OpenAI-compatible gateways), so a body the proxy
        drained to completion counts as terminated even without one. A body cut
        short by cancellation or a transport error never reaches this path with
        ``stream_ended`` set.
        """
        if not self._finished:
            self._finished = True
            if self._mode == "json" and not self._overflow:
                self._consume_json(self._line)
            elif stream_ended and self._mode == "sse" and not self._complete:
                # A body ending without a trailing blank line still holds one
                # complete frame; drain it before deciding the usage is absent.
                if self._line:
                    self._consume_line()
                self._consume_event()
            self._line.clear()
            self._data.clear()
        if (
            self._invalid
            or self._usage_skipped
            or (self._mode != "json" and not (self._complete or stream_ended))
            or self._prompt is None
            or self._completion is None
        ):
            return None
        try:
            return _normalized_usage({
                "prompt_tokens": self._prompt,
                "completion_tokens": self._completion,
                "cache_read_tokens": self._cache_read,
                "cache_write_tokens": self._cache_write,
                # Kept so a provider that bills a bucket outside both sums is
                # charged for it; _normalized_usage takes the larger figure.
                "total_tokens": max(
                    self._reported_total, self._prompt + self._completion
                ),
            })
        except ValueError:
            return None


def _key(master: str) -> bytes:
    if not master:
        raise ValueError("model proxy requires Harness gateway secret")
    return hashlib.sha256(_AAD + master.encode()).digest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _marker_path(grant_root: Path, token: str) -> Path:
    return grant_root / f"{hashlib.sha256(token.encode()).hexdigest()}.json"


def _decode_grant(token: str, master: str) -> ModelProxyGrant | None:
    try:
        encrypted = _decode(token)
        if len(encrypted) <= 12:
            return None
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        raw = AESGCM(_key(master)).decrypt(nonce, ciphertext, _AAD)
        payload = json.loads(raw)
        raw_expires_at = payload["expires_at"]
        expires_at = None if raw_expires_at is None else float(raw_expires_at)
        if expires_at is not None and not math.isfinite(expires_at):
            return None
        model = ResolvedPiModel(**payload["model"])
        max_tokens = payload.get("max_tokens")
        if max_tokens is not None:
            max_tokens = _token_count(max_tokens)
        track_usage = payload.get("track_usage", max_tokens is not None)
        require_verified_usage = payload.get("require_verified_usage", False)
        if type(track_usage) is not bool or type(require_verified_usage) is not bool:
            return None
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        binascii.Error,
        InvalidTag,
    ):
        return None
    return ModelProxyGrant(
        token=token,
        model=model,
        expires_at=expires_at,
        max_tokens=max_tokens,
        track_usage=track_usage,
        require_verified_usage=require_verified_usage,
    )


def issue_model_proxy_grant(
    model: ResolvedPiModel,
    proxy_root: str,
    master: str,
    grant_root: Path,
    *,
    hard_lifetime_seconds: int | None,
    lease_seconds: int,
    max_tokens: int | None = None,
    initial_tokens: int = 0,
    track_usage: bool = False,
    require_verified_usage: bool = False,
) -> tuple[ModelProxyGrant, ResolvedPiModel]:
    """签发短租约凭据，并独立配置用量核验与可选 Token 硬上限。"""
    if type(require_verified_usage) is not bool:
        raise ValueError("require_verified_usage must be a boolean")
    if max_tokens is not None:
        max_tokens = _token_count(max_tokens)
        track_usage = True
    if require_verified_usage:
        track_usage = True
    issued_at = time.time()
    expires_at = (
        None
        if hard_lifetime_seconds is None
        else issued_at + max(60, int(hard_lifetime_seconds))
    )
    lease_lifetime = max(60, int(lease_seconds))
    lease_expires_at = issued_at + lease_lifetime
    if expires_at is not None:
        lease_expires_at = min(expires_at, lease_expires_at)
    payload = json.dumps(
        {
            "model": asdict(model),
            "expires_at": expires_at,
            "max_tokens": max_tokens,
            "track_usage": track_usage,
            "require_verified_usage": require_verified_usage,
        },
        separators=(",", ":"),
    ).encode()
    nonce = os.urandom(12)
    token = _encode(nonce + AESGCM(_key(master)).encrypt(nonce, payload, _AAD))
    grant = ModelProxyGrant(
        token=token,
        model=model,
        expires_at=expires_at,
        max_tokens=max_tokens,
        track_usage=track_usage,
        require_verified_usage=require_verified_usage,
    )
    grant_root.mkdir(parents=True, exist_ok=True)
    grant_root.chmod(0o700)
    marker = _marker_path(grant_root, token)
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "expires_at": expires_at,
                "lease_expires_at": lease_expires_at,
                "lease_seconds": lease_lifetime,
                "requests": 0,
                "active": 0,
                "initial_tokens": initial_tokens,
                "usage_unavailable": False,
                "require_verified_usage": require_verified_usage,
                "unmetered_streak": 0,
                "unmetered_responses": 0,
                "metered_responses": 0,
                "retry_after_seconds": None,
                **_normalized_usage({"prompt_tokens": 0, "completion_tokens": 0}),
            },
            stream,
            separators=(",", ":"),
        )
    proxied = replace(
        model,
        base_url=f"{proxy_root.rstrip('/')}/admin/harness/model-proxy/{token}",
        api_key=token,
    )
    return grant, proxied


def resolve_model_proxy_grant(
    token: str,
    supplied_secret: str,
    master: str,
    grant_root: Path,
    *,
    now: float | None = None,
) -> ModelProxyGrant | None:
    """Require a live activity lease and, when present, a live fixed hard cap."""
    if not token or not secrets_compare(token, supplied_secret):
        return None
    grant = _decode_grant(token, master)
    if grant is None:
        return None
    current_time = time.time() if now is None else now
    marker = _marker_path(grant_root, token)
    if grant.expires_at is not None and grant.expires_at <= current_time:
        marker.unlink(missing_ok=True)
        return None
    try:
        with marker.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            state = json.load(stream)
            if state["expires_at"] != grant.expires_at:
                return None
            lease_expires_at = float(state["lease_expires_at"])
            if not math.isfinite(lease_expires_at):
                return None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if lease_expires_at <= current_time:
        marker.unlink(missing_ok=True)
        return None
    return grant


def renew_model_proxy_grant(
    token: str,
    grant_root: Path,
    *,
    now: float | None = None,
) -> bool:
    """Extend a live short lease without moving an optional fixed hard cap."""
    marker = _marker_path(grant_root, token)
    current_time = time.time() if now is None else now
    try:
        with marker.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            state = json.load(stream)
            raw_expires_at = state["expires_at"]
            expires_at = None if raw_expires_at is None else float(raw_expires_at)
            lease_expires_at = float(state["lease_expires_at"])
            lease_seconds = max(60, int(state["lease_seconds"]))
            if (
                (
                    expires_at is not None
                    and (
                        not math.isfinite(expires_at)
                        or expires_at <= current_time
                    )
                )
                or not math.isfinite(lease_expires_at)
                or lease_expires_at <= current_time
            ):
                return False
            renewed_until = current_time + lease_seconds
            if expires_at is not None:
                renewed_until = min(expires_at, renewed_until)
            if renewed_until > lease_expires_at:
                state["lease_expires_at"] = renewed_until
                stream.seek(0)
                json.dump(state, stream, separators=(",", ":"))
                stream.truncate()
    except (OSError, ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
        return False
    return True


def acquire_model_proxy_grant(
    token: str,
    supplied_secret: str,
    master: str,
    grant_root: Path,
) -> ModelProxyGrant | None:
    """跨 Backend 进程预留一个受请求次数和计量策略约束的上游调用。"""
    grant = resolve_model_proxy_grant(token, supplied_secret, master, grant_root)
    if grant is None:
        return None
    marker = _marker_path(grant_root, token)
    try:
        with marker.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return None
            state = json.load(stream)
            current_time = time.time()
            lease_expires_at = float(state["lease_expires_at"])
            if (
                state["expires_at"] != grant.expires_at
                or (
                    grant.expires_at is not None
                    and grant.expires_at <= current_time
                )
                or not math.isfinite(lease_expires_at)
                or lease_expires_at <= current_time
            ):
                return None
            request_ceiling = (
                _MAX_BUDGETED_REQUESTS if grant.track_usage else _MAX_REQUESTS
            )
            if grant.track_usage:
                try:
                    usage = _normalized_usage(state)
                    initial_tokens = _token_count(state["initial_tokens"])
                    unmetered_streak = _token_count(
                        state.get("unmetered_streak", 0)
                    )
                    if (
                        state["usage_unavailable"] is not False
                        or state.get("require_verified_usage", False)
                        is not grant.require_verified_usage
                    ):
                        raise ValueError("model usage ledger is unusable")
                except (ValueError, TypeError, KeyError):
                    raise ModelProxyBudgetError(
                        "chat_token_usage_unavailable"
                    ) from None
                # 普通聊天允许短暂缺失后保守重试；严格执行首次缺失即永久阻断。
                if (
                    grant.require_verified_usage
                    and unmetered_streak > 0
                ) or unmetered_streak >= _MAX_UNMETERED_STREAK:
                    raise ModelProxyBudgetError("chat_token_usage_unavailable")
                if (
                    grant.max_tokens is not None
                    and initial_tokens + usage["total_tokens"] >= grant.max_tokens
                ):
                    raise ModelProxyBudgetError("chat_token_budget_exceeded")
                if int(state.get("requests", 0)) >= request_ceiling:
                    raise ModelProxyBudgetError("chat_request_budget_exceeded")
            if (
                int(state.get("requests", 0)) >= request_ceiling
                or int(state.get("active", 0)) >= _MAX_CONCURRENT_REQUESTS
            ):
                return None
            state["requests"] = int(state.get("requests", 0)) + 1
            state["active"] = int(state.get("active", 0)) + 1
            stream.seek(0)
            json.dump(state, stream, separators=(",", ":"))
            stream.truncate()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return grant


def record_model_proxy_usage(
    token: str, grant_root: Path, usage: dict[str, int] | None
) -> None:
    """在 grant 文件锁内累计一个 HTTP 响应；调用方必须且只记一次。"""
    marker = _marker_path(grant_root, token)
    try:
        normalized = _normalized_usage(usage)
    except (ValueError, TypeError, KeyError):
        normalized = None
    try:
        with marker.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return
            state = json.load(stream)
            if not isinstance(state, dict):
                raise ValueError("model grant ledger is not an object")
            try:
                accumulated = _normalized_usage(state)
                require_verified_usage = state["require_verified_usage"]
                if type(require_verified_usage) is not bool:
                    raise ValueError("invalid strict usage flag")
            except (ValueError, TypeError, KeyError):
                accumulated = None
                require_verified_usage = False
            if accumulated is None:
                # 账本本身不可读时，任何累计值都不再可信。
                state["usage_unavailable"] = True
            elif normalized is None and require_verified_usage:
                # 无人值守执行必须保留已知累计值，但首次未知即永久关闭后续调用。
                state.update(accumulated)
                state["usage_unavailable"] = True
                state["unmetered_streak"] = (
                    int(state.get("unmetered_streak", 0) or 0) + 1
                )
                state["unmetered_responses"] = (
                    int(state.get("unmetered_responses", 0) or 0) + 1
                )
            elif normalized is None:
                # 普通聊天仍按保守上限记账，再允许调用方重试。
                state.update(accumulated)
                state["completion_tokens"] = (
                    accumulated["completion_tokens"] + _UNMETERED_TOKEN_CHARGE
                )
                state["total_tokens"] = (
                    accumulated["total_tokens"] + _UNMETERED_TOKEN_CHARGE
                )
                state["unmetered_streak"] = (
                    int(state.get("unmetered_streak", 0) or 0) + 1
                )
                state["unmetered_responses"] = (
                    int(state.get("unmetered_responses", 0) or 0) + 1
                )
            else:
                for key, value in normalized.items():
                    state[key] = accumulated[key] + value
                state["metered_responses"] = (
                    int(state.get("metered_responses", 0) or 0) + 1
                )
                state["unmetered_streak"] = 0
            stream.seek(0)
            json.dump(state, stream, separators=(",", ":"))
            stream.truncate()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A failed debit must not leave a previously valid budget usable.
        marker.unlink(missing_ok=True)


def read_model_proxy_usage(token: str, grant_root: Path) -> dict[str, int] | None:
    """Return known proxy-only totals, even if a later response lacked usage."""
    try:
        with _marker_path(grant_root, token).open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return None
            state = json.load(stream)
            if (
                isinstance(state, dict)
                and state.get("require_verified_usage") is True
                and _token_count(state.get("metered_responses", 0)) == 0
            ):
                # 初始化账本的零不是供应商报告；严格执行不能把未知用量补成零。
                return None
            return _normalized_usage(state)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def model_proxy_usage_is_verified(token: str, grant_root: Path) -> bool:
    """严格读取代理账本；明确零用量有效，缺失、损坏或未知响应均失败。"""
    try:
        with _marker_path(grant_root, token).open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return False
            state = json.load(stream)
            if not isinstance(state, dict):
                return False
            metered_responses = _token_count(state.get("metered_responses", 0))
            return (
                state.get("require_verified_usage") is True
                and state.get("usage_unavailable") is False
                and metered_responses > 0
                and _normalized_usage(state) is not None
            )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def model_proxy_usage_is_unavailable(token: str, grant_root: Path) -> bool:
    """读取严格账本中的永久未知事实；损坏或丢失同样按未知处理。"""
    try:
        with _marker_path(grant_root, token).open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return True
            state = json.load(stream)
            return (
                not isinstance(state, dict)
                or state.get("require_verified_usage") is not True
                or state.get("usage_unavailable") is not False
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True


def record_model_proxy_retry_after(
    token: str,
    grant_root: Path,
    value: object,
) -> None:
    """保存最近一次上游错误响应中经过校验的 Retry-After。"""
    retry_after_seconds = _retry_after_seconds(value)
    marker = _marker_path(grant_root, token)
    try:
        with marker.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return
            state = json.load(stream)
            if not isinstance(state, dict):
                raise ValueError("model grant ledger is not an object")
            state["retry_after_seconds"] = retry_after_seconds
            stream.seek(0)
            json.dump(state, stream, separators=(",", ":"))
            stream.truncate()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        marker.unlink(missing_ok=True)


def read_model_proxy_retry_after(token: str, grant_root: Path) -> int | None:
    """读取最近一次上游错误响应的安全等待秒数。"""
    try:
        with _marker_path(grant_root, token).open(
            "r", encoding="utf-8"
        ) as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            if os.fstat(stream.fileno()).st_nlink == 0:
                return None
            state = json.load(stream)
            value = state.get("retry_after_seconds")
            if type(value) is not int or not (
                1 <= value <= _MAX_RETRY_AFTER_SECONDS
            ):
                return None
            return value
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def release_model_proxy_grant(token: str, grant_root: Path) -> None:
    marker = _marker_path(grant_root, token)
    try:
        with marker.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            state = json.load(stream)
            state["active"] = max(0, int(state.get("active", 0)) - 1)
            stream.seek(0)
            json.dump(state, stream, separators=(",", ":"))
            stream.truncate()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def revoke_model_proxy_grant(token: str, grant_root: Path) -> None:
    _marker_path(grant_root, token).unlink(missing_ok=True)


def prepare_model_proxy_request(
    model: ResolvedPiModel,
    upstream_path: str,
    query: list[tuple[str, str]],
    body: bytes,
    *,
    require_usage: bool = False,
) -> PreparedModelProxyRequest:
    """Bind an untrusted request to the grant's protocol, model, and endpoint."""
    expected_path = (
        "responses"
        if model.api_protocol == PROTOCOL_RESPONSES
        else "v1/messages"
        if model.api_protocol == PROTOCOL_MESSAGES
        else "chat/completions"
        if model.api_protocol == PROTOCOL_CHAT_COMPLETIONS
        else ""
    )
    if upstream_path.strip("/") != expected_path:
        raise ValueError("model grant does not allow this provider endpoint")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("model request body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("model request body must be a JSON object")

    azure = model.adapter == ADAPTER_AZURE_OPENAI
    if azure:
        api_version = model.azure_api_version or "v1"
        if any(key != "api-version" or value != api_version for key, value in query):
            raise ValueError("model grant does not allow these query parameters")
        forwarded_query = (("api-version", api_version),)
        payload["model"] = model.azure_deployment or model.model
    else:
        if query:
            raise ValueError("model grant does not allow query parameters")
        forwarded_query = ()
        payload["model"] = model.model

    if expected_path == "v1/messages":
        # Anthropic Messages: keep stream/max_tokens; model already rewritten.
        if not isinstance(payload.get("max_tokens"), int) or not (
            1 <= payload["max_tokens"] <= 32_768
        ):
            payload["max_tokens"] = 32_768
    else:
        payload["stream"] = True
        if expected_path == "responses":
            payload["store"] = False
            payload["background"] = False
            if "max_output_tokens" in payload:
                payload["max_output_tokens"] = min(
                    32_768, max(16, int(payload["max_output_tokens"]))
                )
        else:
            if require_usage:
                options = payload.get("stream_options")
                payload["stream_options"] = {
                    **(options if isinstance(options, dict) else {}),
                    "include_usage": True,
                }
            for key in ("max_tokens", "max_completion_tokens"):
                if key in payload:
                    payload[key] = min(32_768, max(1, int(payload[key])))

    # 出网前统一脱敏：端点是公网云模型时，payload 里的 PII/敏感字段换占位符。
    # 本机/内网端点 session.enabled=False，行为完全不变。
    mask_session = session_for_endpoint(model.base_url)
    if mask_session.enabled:
        payload = mask_session.mask_payload(payload)

    base_url = normalize_azure_openai_base_url(model.base_url, adapter=model.adapter)
    url = f"{base_url.rstrip('/')}/{quote(expected_path, safe='/')}"
    return PreparedModelProxyRequest(
        url=url,
        query=forwarded_query,
        body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        mask_session=mask_session if mask_session.enabled else None,
    )




def secrets_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )


@contextlib.contextmanager
def model_for_child_process(
    model: ResolvedPiModel,
    proxy_root: str,
    master: str,
    grant_root: Path,
    *,
    enabled: bool,
    hard_lifetime_seconds: int | None,
    lease_seconds: int,
    max_tokens: int | None = None,
    initial_tokens: int = 0,
    track_usage: bool = False,
    require_verified_usage: bool = False,
):
    """为子进程提供短租约模型凭据，并按需核验用量或设置硬上限。"""
    if not enabled:
        yield model
        return
    if not proxy_root:
        raise ValueError("sandbox model proxy requires Harness gateway URL")
    grant, proxied = issue_model_proxy_grant(
        model,
        proxy_root,
        master,
        grant_root,
        hard_lifetime_seconds=hard_lifetime_seconds,
        lease_seconds=lease_seconds,
        max_tokens=max_tokens,
        initial_tokens=initial_tokens,
        track_usage=track_usage,
        require_verified_usage=require_verified_usage,
    )
    try:
        yield proxied
    finally:
        revoke_model_proxy_grant(grant.token, grant_root)
