"""最小可用的模型调用层：解析端点 → 脱敏 → POST → 还原占位符。

为什么不直接用 ``qa_service``：那里没有走 ``data_guard``，会把原文发上云。
群聊助手要把聊天记录和文件正文送出去，必须经过脱敏，所以单独封一层。

约定：只支持 OpenAI 兼容的 ``chat_completions``。端点若是 Anthropic
``messages`` 协议会明确报错，而不是静默降级。
"""

from __future__ import annotations

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
