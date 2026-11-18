from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class PiAgentError(RuntimeError):
    """A Pi turn failed.

    ``code`` is a stable machine-readable reason and ``public_message`` is the
    end-user wording for it; callers that surface failures to a person must use
    ``public_message`` instead of the technical ``str(exc)``.

    ``max_attempts`` bounds how many dispatch attempts this failure class
    justifies when the caller's default is higher: a deterministic post-generation
    contract violation is worth one targeted regeneration, not the full retry
    budget of an unattended worker.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        public_message: str = "",
        max_attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = public_message
        self.max_attempts = max_attempts


class PiAgentCancelledError(PiAgentError):
    """The caller requested cancellation of an active Pi turn."""


@dataclass(frozen=True)
class PiAgentResult:
    payload: dict[str, Any]
    session_id: str
    model: str
    run_time_ms: int
    token_usage: dict[str, int]
    trace_steps: int
    cost: str | None = None


@dataclass(frozen=True)
class ResolvedPiAgentEndpoint:
    base_url: str
    api_key: str
    agent_id: str
    session_type: str
    require_published: bool
    source: Literal["pi", "chat"]


def resolve_pi_agent_endpoint() -> ResolvedPiAgentEndpoint:
    """Resolve the Pi agent endpoint from system_config or env settings.

    Resolution order:
    1. system_config (admin settings page)
    2. DOC_NEXUS_PI_AGENT_* settings/env
    """
    from app.services.writing_runtime_config import (
        load_writing_overrides,
        resolve_pi_agent_api_key,
        resolve_pi_agent_id,
        resolve_pi_agent_url,
    )

    stored = load_writing_overrides()
    pi_url = resolve_pi_agent_url(stored)
    pi_key = resolve_pi_agent_api_key(stored)
    pi_id = resolve_pi_agent_id(stored)
    if not (pi_url and pi_key and pi_id):
        raise PiAgentError(
            "Pi Agent 未配置：请在管理后台设置 Pi Agent URL、API Key 和 Agent ID"
        )
    return ResolvedPiAgentEndpoint(
        base_url=pi_url.rstrip("/"),
        api_key=pi_key,
        agent_id=pi_id,
        session_type="api_chat",
        require_published=settings.pi_agent_require_published,
        source="pi",
    )


def _repair_unescaped_json_quotes(text: str) -> str:
    """Escape quote characters that models place inside JSON string values."""
    repaired: list[str] = []
    in_string = False
    string_is_key = False
    containers: list[str] = []
    last_token = ""
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            repaired.append(char)
            if char == '"':
                string_is_key = bool(
                    containers and containers[-1] == "{" and last_token in ("{", ",")
                )
                in_string = True
            elif char in "{[":
                containers.append(char)
            elif char in "}]" and containers:
                containers.pop()
            if not char.isspace():
                last_token = char
        elif char == "\\":
            repaired.append(char)
            if index + 1 < len(text):
                index += 1
                repaired.append(text[index])
        elif char == '"':
            next_index = index + 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
            next_char = text[next_index] if next_index < len(text) else ""
            closes = (
                next_char == ":" if string_is_key
                else bool(next_char) and next_char in ",]}"
            )
            if closes:
                repaired.append(char)
                in_string = False
            else:
                repaired.append('\\"')
        elif ord(char) < 0x20:
            repaired.append(f"\\u{ord(char):04x}")
        else:
            repaired.append(char)
        index += 1
    return "".join(repaired)


def parse_json_reply(reply: str) -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```"):
        first_line_end = text.find("\n")
        if first_line_end >= 0:
            text = text[first_line_end + 1 :]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as initial_error:
        start = text.find("{")
        if start < 0:
            raise PiAgentError(
                "Pi Agent did not return a JSON object"
            ) from initial_error
        candidate = text[start:]
        try:
            # Models sometimes add a short explanation after the object. Parse
            # the first complete object instead of extending to the last brace.
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError as raw_error:
            try:
                # Some model replies contain unescaped quotes inside Markdown
                # string values, such as: "项目为"2026年..."。".
                value, _ = json.JSONDecoder().raw_decode(
                    _repair_unescaped_json_quotes(candidate)
                )
            except json.JSONDecodeError:
                raise PiAgentError("Pi Agent returned invalid JSON") from raw_error
    if not isinstance(value, dict):
        raise PiAgentError("Pi Agent JSON response must be an object")
    return value


class PiAgentClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        agent_id: str | None = None,
        session_type: str | None = None,
        require_published: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        if base_url is not None and api_key is not None and agent_id is not None:
            self.base_url = base_url.rstrip("/")
            self.api_key = api_key
            self.agent_id = agent_id
            self.session_type = session_type if session_type is not None else "api_chat"
            self.require_published = (
                require_published
                if require_published is not None
                else settings.pi_agent_require_published
            )
            self.source: Literal["pi", "chat"] = "pi"
        else:
            resolved = resolve_pi_agent_endpoint()
            self.base_url = (base_url or resolved.base_url).rstrip("/")
            self.api_key = api_key if api_key is not None else resolved.api_key
            self.agent_id = agent_id or resolved.agent_id
            self.session_type = (
                session_type if session_type is not None else resolved.session_type
            )
            self.require_published = (
                require_published
                if require_published is not None
                else resolved.require_published
            )
            self.source = resolved.source
        self.transport = transport

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        capability_token: str | None = None,
    ) -> PiAgentResult:
        if not self.base_url:
            raise PiAgentError("DOC_NEXUS_PI_AGENT_URL is not configured")
        if not self.api_key:
            raise PiAgentError("DOC_NEXUS_PI_AGENT_API_KEY is not configured")
        if not self.agent_id:
            raise PiAgentError(
                "DOC_NEXUS_PI_AGENT_ID or DOC_NEXUS_AGENT_CHAT_AGENT_ID is not configured"
            )
        resume_id = (session_id or "").strip()
        payload: dict[str, Any] = {
            "message": message,
            "new_session": not bool(resume_id),
            "require_published": self.require_published,
            "stream": False,
            "session_type": self.session_type,
        }
        if resume_id:
            payload["session_id"] = resume_id
        key = (idempotency_key or "").strip()
        capability = (capability_token or "").strip()
        if capability:
            # Opaque Nexus grant forwarded to the Pi child runtime.
            payload["nexus_capability_token"] = capability
        if key:
            payload["idempotency_key"] = key
        t0 = time.monotonic()
        logger.info(
            "pi_agent.request.start",
            extra={
                "agent_id": self.agent_id,
                "session_id": resume_id or "(new)",
                "span": "pi_agent.chat",
            },
        )
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(settings.pi_agent_timeout_seconds),
                transport=self.transport,
            ) as client:
                response = client.post(
                    f"/api/agents/{self.agent_id}/chat",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.RequestError as exc:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.error(
                "pi_agent.request.failed",
                extra={
                    "agent_id": self.agent_id,
                    "session_id": resume_id or "(new)",
                    "duration_ms": duration_ms,
                    "error": str(exc),
                    "span": "pi_agent.chat",
                },
            )
            raise PiAgentError("Pi Agent 暂时不可用") from exc
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        if response.status_code >= 400:
            logger.warning(
                "pi_agent.request.failed",
                extra={
                    "agent_id": self.agent_id,
                    "session_id": resume_id or "(new)",
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "span": "pi_agent.chat",
                },
            )
            raise PiAgentError(f"Pi Agent 请求失败（HTTP {response.status_code}）")
        body = response.json()
        if not isinstance(body, dict):
            raise PiAgentError("Pi Agent returned an invalid response")
        reply = body.get("reply")
        if not isinstance(reply, str):
            raise PiAgentError("Pi Agent response has no reply")
        usage = body.get("token_usage")
        if not isinstance(usage, dict):
            usage = {}
        from app.services.usage_normalize import normalize_usage_buckets

        buckets = normalize_usage_buckets(usage)
        trace = body.get("execution_trace")
        raw_cost = body.get("cost")
        try:
            run_time_ms = int(body.get("run_time_ms") or 0)
        except (TypeError, ValueError) as exc:
            raise PiAgentError("Pi Agent response has invalid run_time_ms") from exc
        model_name = str(body.get("effective_model") or "")
        logger.info(
            "pi_agent.request.end",
            extra={
                "agent_id": self.agent_id,
                "session_id": str(body.get("session_id") or ""),
                "model": model_name,
                "run_time_ms": run_time_ms,
                "trace_steps": len(trace) if isinstance(trace, list) else 0,
                "duration_ms": duration_ms,
                "span": "pi_agent.chat",
            },
        )
        return PiAgentResult(
            payload=parse_json_reply(reply),
            session_id=str(body.get("session_id") or ""),
            model=model_name,
            run_time_ms=run_time_ms,
            token_usage={
                "prompt_tokens": buckets["input_tokens"],
                "completion_tokens": buckets["output_tokens"],
                "total_tokens": buckets["total_tokens"],
                "input_uncached_tokens": buckets["input_uncached_tokens"],
                "cache_read_tokens": buckets["cache_read_tokens"],
                "cache_write_tokens": buckets["cache_write_tokens"],
            },
            trace_steps=len(trace) if isinstance(trace, list) else 0,
            cost=str(raw_cost) if raw_cost not in (None, "") else None,
        )
