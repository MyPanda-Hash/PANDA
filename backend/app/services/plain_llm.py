"""最小可用的模型调用层：解析端点 → 脱敏 → POST → 还原占位符。

为什么不直接用 ``qa_service``：那里没有走 ``data_guard``，会把原文发上云。
群聊助手要把聊天记录和文件正文送出去，必须经过脱敏，所以单独封一层。

约定：只支持 OpenAI 兼容的 ``chat_completions``。端点若是 Anthropic
``messages`` 协议会明确报错，而不是静默降级。
"""

from __future__ import annotations
import json

import logging
import os
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.services import data_guard
from app.services.model_endpoint_config import (
    PROTOCOL_CHAT_COMPLETIONS,
    resolve_endpoint_configuration,
)
from app.services.model_routing import resolve_model_endpoint

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 90.0


class LlmUnavailable(RuntimeError):
    """没有可用的模型端点。"""


class LlmCallFailed(RuntimeError):
    """端点存在但调用失败。"""


@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str
    endpoint_url: str
    masked: bool


def chat_completions_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return raw
    value = raw.split("?")[0].rstrip("/")
    for suffix in (
        "/chat/completions",
        "/completions",
        "/responses",
        "/embeddings",
        "/rerank",
        "/messages",
    ):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


def _resolve_secret(secret_ref: str | None) -> str | None:
    raw = (secret_ref or "").strip()
    if not raw:
        return None
    if raw.startswith("env://"):
        return os.environ.get(raw[6:])
    return raw


def resolve_chat_target(db: Session):
    target = resolve_model_endpoint(db, "chat")
    if target is None:
        raise LlmUnavailable("尚未配置对话模型端点")
    return target




def _mask_payload_keeping_tools(session, payload: dict) -> dict:
    """脱敏 payload，但保留 role=tool 的消息原样。

    工具结果要么是公开网页信息（联网检索），要么是已经按账号权限裁剪过的
    MES 数据（mes_* 工具），再套一层 PII/金额脱敏只会把可用信息毁掉。
    """
    masked = session.mask_payload(payload)
    if not isinstance(masked, dict):
        return masked
    original_messages = payload.get("messages") or []
    masked_messages = masked.get("messages") or []
    if len(original_messages) != len(masked_messages):
        return masked

    merged = []
    for raw, done in zip(original_messages, masked_messages):
        if isinstance(raw, dict) and str(raw.get("role")) == "tool":
            merged.append(raw)
        else:
            merged.append(done)
    masked["messages"] = merged
    return masked

async def complete_with_tools(
    target,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
):
    """一次非流式调用，支持 OpenAI tools；返回 (文本, 工具调用列表, token 用量)。

    Anthropic 的 tool_use 与 OpenAI 的 tool_calls 结构不同，转换放在调用方，
    这里只做「把 tools 交给上游、把 tool_calls 取回来」这一件事。
    """
    request_url = chat_completions_url(target.endpoint_url)
    payload: dict = {
        "model": target.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    session = data_guard.session_for_endpoint(target.endpoint_url)
    # 工具结果**不做脱敏**：
    # - 联网检索拿到的是公开网页摘要，本来就不含本系统的业务数据；
    # - 脱敏规则里的 MONEY（「2.68万亿」「180元」）会把公开数据误伤成〔MONEY_1〕，
    #   模型只能回「金额被脱敏」，等于白检索（2026-11-17 实测）。
    # 系统内部数据走的是 MES 工具，那条链路自己按账号权限裁剪，不依赖这里。
    payload = _mask_payload_keeping_tools(session, payload)

    headers = {"Content-Type": "application/json"}
    secret = _resolve_secret(getattr(target.endpoint, "secret_ref", None))
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    endpoint_timeout = (getattr(target.endpoint, "timeout_ms", 0) or 0) / 1000.0
    timeout = timeout_seconds or endpoint_timeout or 120.0

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(request_url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise LlmCallFailed(
                f"上游返回 {response.status_code}：{response.text[:300]}"
            )
        body = response.json()

    choices = body.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = str(message.get("content") or "")
    calls = message.get("tool_calls") or []
    # 把上游的真实 usage 带出去：以前这里直接丢了，导致「AI 用量」无从统计
    # （input_tokens 全是 0，算不出钱）。字段名按 OpenAI 兼容格式。
    usage = body.get("usage") or {}
    return text, calls, {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_tokens": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
    }

async def stream_chat_with_target(
    target,
    messages: list[dict],
    *,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
):
    """与 stream_chat 相同的流式调用，但用已解析好的端点。

    流式响应可能持续几十秒；数据库会话不应被它一直占着，
    所以调用方先用 transaction() 解析出 target，再把它传进来。
    """
    request_url = chat_completions_url(target.endpoint_url)
    payload = {
        "model": target.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    session = data_guard.session_for_endpoint(target.endpoint_url)
    payload = session.mask_payload(payload)

    headers = {"Content-Type": "application/json"}
    secret = _resolve_secret(getattr(target.endpoint, "secret_ref", None))
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    endpoint_timeout = (getattr(target.endpoint, "timeout_ms", 0) or 0) / 1000.0
    timeout = timeout_seconds or endpoint_timeout or 120.0

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", request_url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "ignore")[:400]
                raise LlmCallFailed(f"上游返回 {response.status_code}：{detail}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {})
                    piece = delta.get("content")
                    if piece:
                        yield piece


async def stream_chat(
    db: Session,
    messages: list[dict],
    *,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
):
    """流式调用对话端点，逐段产出文本增量。

    与 complete_chat 共用同一套端点解析与密钥读取；区别只在于上游开 stream=True，
    并把 OpenAI 的 SSE 增量拆出来。给 Anthropic 兼容层用。
    """
    target = resolve_chat_target(db)
    request_url = chat_completions_url(target.endpoint_url)
    payload = {
        "model": target.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    session = data_guard.session_for_endpoint(target.endpoint_url)
    payload = session.mask_payload(payload)

    headers = {"Content-Type": "application/json"}
    secret = _resolve_secret(getattr(target.endpoint, "secret_ref", None))
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    endpoint_timeout = (getattr(target.endpoint, "timeout_ms", 0) or 0) / 1000.0
    timeout = timeout_seconds or endpoint_timeout or 120.0

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", request_url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "ignore")[:400]
                raise LlmCallFailed(f"上游返回 {response.status_code}：{detail}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {})
                    piece = delta.get("content")
                    if piece:
                        yield piece


async def complete_chat(
    db: Session,
    messages: list[dict],
    *,
    user_id: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
    response_format_json: bool = False,
    disable_thinking: bool = False,
) -> LlmReply:
    """发一次非流式对话请求，返回已还原占位符的文本。"""
    target = resolve_chat_target(db)
    configuration = resolve_endpoint_configuration(
        getattr(target.endpoint, "provider", ""),
        getattr(target.endpoint, "adapter", ""),
        getattr(target.endpoint, "api_protocol", ""),
    )
    if configuration.api_protocol != PROTOCOL_CHAT_COMPLETIONS:
        raise LlmCallFailed(
            f"当前端点协议为 {configuration.api_protocol}，群聊助手只支持 chat_completions"
        )

    request_url = chat_completions_url(target.endpoint_url)
    payload: dict = {
        "model": target.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format_json:
        # DeepSeek 文档：设成 json_object 后模型保证输出合法 JSON；
        # 识别不出 JSON 时会把非法片段替换成空格（表现为空串），所以要配合下面的空内容检查。
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking and "deepseek" in (getattr(target.endpoint, "provider", "") or "").lower():
        # 思考模式会把 max_tokens 全烧在推理上，正式答案变空。
        payload["thinking"] = {"type": "disabled"}

    # 云端端点才需要脱敏；本地回环端点原样发送。
    session = data_guard.session_for_endpoint(target.endpoint_url)
    payload = session.mask_payload(payload)

    headers = {"Content-Type": "application/json"}
    secret = _resolve_secret(getattr(target.endpoint, "secret_ref", None))
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    # 端点自己配了超时就用它（deepseek 思考模式可能跑很久，默认 30s 不够）
    endpoint_timeout = (getattr(target.endpoint, "timeout_ms", 0) or 0) / 1000.0
    timeout = timeout_seconds or endpoint_timeout or DEFAULT_TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(request_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "group_assistant.llm.http_error",
            extra={"status": exc.response.status_code, "url": request_url.split("?")[0]},
        )
        raise LlmCallFailed(f"模型端点返回 {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise LlmCallFailed(f"模型调用失败：{exc}") from exc

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmCallFailed("模型返回结构不符合 OpenAI 兼容格式") from exc
    if not isinstance(text, str) or not text.strip():
        raise LlmCallFailed(
            "模型返回内容为空"
            + ("（思考模式可能耗尽了 max_tokens）" if not disable_thinking else "")
        )

    return LlmReply(
        text=session.restore_text(text),
        model=target.model,
        endpoint_url=target.endpoint_url,
        masked=session.masked,
    )
