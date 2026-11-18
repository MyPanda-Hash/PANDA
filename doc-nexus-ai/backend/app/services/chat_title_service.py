"""根据已持久化的对话文字生成会话标题。

调用方在后台执行，只短暂等待结果；明确空结果可有界重试。
成功标题只冻结一次，失败不影响聊天正文，也不冻结默认名称。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.services.data_guard import session_for_endpoint
from app.services.pi_local import (
    LocalPiEndpoint,
    resolve_local_pi_endpoint,
)
from app.services.anthropic_http import (
    anthropic_headers,
    anthropic_messages_url,
)
from app.services.model_endpoint_config import (
    ADAPTER_AZURE_OPENAI,
    PROTOCOL_MESSAGES,
    PROTOCOL_RESPONSES,
    normalize_azure_openai_base_url,
    resolve_endpoint_configuration,
)

logger = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 60
_SYSTEM_PROMPT = (
    "你是会话标题生成器。根据下面的对话,用一句简短的中文短语概括对话主题"
    "(<=30 字),只输出标题本身,不要引号、标点、解释或前缀。"
)


class ChatTitleSummarizeError(RuntimeError):
    """Raised when the title summarizer cannot produce a safe title."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _load_turns(*, messages: list[Any]) -> list[str]:
    """Build a compact alternating transcript for summarization.

    ``messages`` rows come from ``chat_repo`` ordered messages.  The
    summarizer only needs the conversation topic; keep the last ~8 turns and
    clip each turn so a long document dump cannot bloat the request.
    """
    turns: list[str] = []
    for item in messages:
        role = str(getattr(item, "role", "") or "")
        if role not in {"user", "assistant"}:
            continue
        text = str(getattr(item, "content", "") or "").strip()
        if not text:
            continue
        # Summaries only need the conversation topic; drop markdown/code and
        # keep each turn short so the title request stays fast even when the
        # session holds long document dumps.
        compact = " ".join(text.split())
        if len(compact) > 400:
            compact = f"{compact[:398]}…"
        turns.append(f"{role}: {compact}")
    # Newest last keeps the active topic at the end.
    return turns[-6:]


def _request_payload(
    endpoint: LocalPiEndpoint,
    *,
    transcript: str,
) -> tuple[str, dict[str, Any], str]:
    """Build the protocol-correct url/payload for the resolved endpoint.

    Returns ``(url, payload, protocol)``.
    """
    configuration = resolve_endpoint_configuration(
        endpoint.provider,
        endpoint.adapter,
        endpoint.api_protocol,
    )
    protocol = configuration.api_protocol
    base = normalize_azure_openai_base_url(
        endpoint.base_url, adapter=configuration.adapter
    )
    if protocol == PROTOCOL_RESPONSES:
        url = f"{base}/responses"
        payload: dict[str, Any] = {
            "model": endpoint.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": _SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": transcript}],
                },
            ],
            # Responses budgets include reasoning before the short visible title.
            "max_output_tokens": 4096,
            "stream": False,
            "store": False,
        }
    elif protocol == PROTOCOL_MESSAGES:
        url = anthropic_messages_url(base)
        payload = {
            "model": endpoint.model,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": transcript}],
            "max_tokens": 128,
        }
    else:
        url = f"{base}/chat/completions"
        payload = {
            "model": endpoint.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            "temperature": 0,
            # 预算包含推理 token；128 可能在可见标题生成前耗尽。
            "max_tokens": 4096,
            "stream": False,
        }
    if endpoint.adapter == ADAPTER_AZURE_OPENAI and endpoint.api_version:
        url = f"{url}?api-version={endpoint.api_version}"
    return url, payload, protocol


def _headers(endpoint: LocalPiEndpoint, protocol: str) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if not endpoint.api_key:
        return headers
    if endpoint.adapter == ADAPTER_AZURE_OPENAI:
        headers["api-key"] = endpoint.api_key
    elif protocol == PROTOCOL_MESSAGES:
        headers.update(anthropic_headers(endpoint.api_key, endpoint.base_url))
    else:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    return headers


def _extract_text(body: dict[str, Any], protocol: str) -> str | None:
    if protocol == PROTOCOL_RESPONSES:
        output = body.get("output")
        if isinstance(output, list):
            chunks: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            chunks.append(part["text"])
            if chunks:
                return "".join(chunks).strip()
        if isinstance(body.get("output_text"), str):
            return str(body["output_text"]).strip()
        return None
    if protocol == PROTOCOL_MESSAGES:
        content_blocks = body.get("content")
        if isinstance(content_blocks, list):
            chunks = [
                str(block["text"])
                for block in content_blocks
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if chunks:
                return "".join(chunks).strip()
        return None
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                chunks = [
                    str(item.get("text"))
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if chunks:
                    return "".join(chunks).strip()
    return None


def _clean_title(raw: str | None) -> str | None:
    """Normalize an LLM-produced title into a bounded display title."""
    if not raw:
        return None
    value = " ".join((raw or "").replace("\n", " ").split())
    # Strip common quoting/wrapping the model may add.
    value = value.strip("“”\"'《》「」[]()（） ")
    value = " ".join(value.split())
    if not value:
        return None
    value = value[:_TITLE_MAX_CHARS]
    return value.strip() or None


def _empty_response_diagnostics(body: dict[str, Any], protocol: str) -> dict[str, Any]:
    """仅保留白名单结构和计数，不记录正文、推理内容或任意上游字符串。"""
    if protocol == PROTOCOL_RESPONSES:
        content = body.get("output")
        incomplete = body.get("incomplete_details")
        reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
        reasoning = isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "reasoning"
            for item in content
        )
    elif protocol == PROTOCOL_MESSAGES:
        content = body.get("content")
        reason = body.get("stop_reason")
        reasoning = isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") in ("thinking", "redacted_thinking")
            for item in content
        )
    else:
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        choice = first if isinstance(first, dict) else {}
        candidate = choice.get("message")
        message = candidate if isinstance(candidate, dict) else {}
        content = message.get("content")
        reason = choice.get("finish_reason")
        reasoning = bool(message.get("reasoning_content"))
    known_reasons = (
        "stop", "length", "tool_calls", "function_call", "content_filter",
        "max_output_tokens", "max_tokens", "end_turn", "stop_sequence",
        "refusal", "pause_turn",
    )
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    counts = {
        key: value
        for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens")
        if type(value := usage.get(key)) is int and 0 <= value < 10**12
    }
    for key in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(key)
        value = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if type(value) is int and 0 <= value < 10**12:
            counts["reasoning_tokens"] = value
    status = body.get("status")
    return {
        "content_type": type(content).__name__,
        "finish_reason": reason if reason in known_reasons else "unknown",
        "status": status if status in ("completed", "incomplete", "failed") else "unknown",
        "reasoning_present": reasoning,
        "usage": counts,
    }


def summarize_session_title(
    *,
    user_id: str,
    messages: list[Any],
    model: str | None = None,
    timeout_seconds: float = 12.0,
) -> str:
    """Summarize a chat transcript into a bounded session title.

    Raises :class:`ChatTitleSummarizeError` when the endpoint is unavailable,
    the provider returns nothing parseable, or the output cannot be bounded.
    """
    turns = _load_turns(messages=messages)
    if not turns:
        raise ChatTitleSummarizeError("no transcript to summarize")
    transcript = "对话内容：\n" + "\n".join(turns)
    try:
        endpoint: LocalPiEndpoint = resolve_local_pi_endpoint(
            model,
            capability="chat",
            user_id=user_id,
        )
    except Exception as exc:
        raise ChatTitleSummarizeError("chat endpoint unavailable") from exc
    # 标题也要脱敏：transcript 是对话原文，不能原样送到云端
    mask_session = session_for_endpoint(endpoint.base_url)
    if mask_session.enabled:
        transcript = mask_session.mask_text(transcript)
    try:
        url, payload, protocol = _request_payload(
            endpoint,
            transcript=transcript,
        )
    except (ValueError, TypeError) as exc:
        raise ChatTitleSummarizeError("chat endpoint configuration invalid") from exc

    headers = _headers(endpoint, protocol)
    try:
        response = httpx.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "chat_title.summarize_failed status=%s error=%s",
            getattr(getattr(exc, "response", None), "status_code", None),
            type(exc).__name__,
        )
        raise ChatTitleSummarizeError("chat title endpoint request failed") from exc

    try:
        body = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ChatTitleSummarizeError(
            "chat title endpoint returned invalid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise ChatTitleSummarizeError("chat title endpoint returned invalid body")
    raw = _extract_text(body, protocol)
    title = _clean_title(raw)
    if not title:
        logger.warning(
            "chat_title.empty_response provider=%s model=%s protocol=%s "
            "text_chars=%s diagnostics=%s",
            endpoint.provider,
            endpoint.model,
            protocol,
            len(raw or ""),
            json.dumps(_empty_response_diagnostics(body, protocol), ensure_ascii=True),
        )
        raise ChatTitleSummarizeError(
            "chat title endpoint returned empty title", retryable=True
        )
    return title
