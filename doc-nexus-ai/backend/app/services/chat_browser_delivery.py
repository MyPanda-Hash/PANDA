"""网页严格原文交付：只认本轮受控工具回执，不交付模型自由文字。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.user_messages import redact_inline_secrets
from app.services.pi_runtime.events import _PERSIST_MAX_CONTENT_CHARS

SAFE_FAILURE = "网页原文读取未完成，未交付未经核验的模型回答。请重新读取网页。"
_SOURCE_KEYS = {"kind", "url", "title", "text", "truncated"}
_PROOF_DOMAIN = "doc-nexus:browser-source-delivery:v1"
_RECEIPT_LIMIT = 80000
# 与 browser_read_page.ts 一致：预留外层 JSON 和 64 位 HMAC，再反推 base64 字节预算。
_RECEIPT_OVERHEAD = len(json.dumps(
    {"kind": "browser_delivery_v1", "data": "", "proof": "0" * 64},
    separators=(",", ":"),
))
_SOURCE_BYTE_LIMIT = ((_RECEIPT_LIMIT - _RECEIPT_OVERHEAD) // 4) * 3


class BrowserSourceError(ValueError):
    code = "browser_source_unavailable"


def requested(body: Any) -> bool:
    lines = (body.message or "").strip().splitlines()
    return body.selected_skill == "agent-browser" or bool(
        lines and lines[0] == "/skill:agent-browser"
    )


def _http_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 8192:
        return False
    if any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
        )
    except ValueError:
        return False


def _source(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
        return None
    if (
        value.get("kind") != "browser_source_v1"
        or not _http_url(value.get("url"))
        or not isinstance(value.get("title"), str)
        or len(value["title"]) > 2048
        or not isinstance(value.get("text"), str)
        or not value["text"].strip()
        or len(value["text"]) > 20000
        or type(value.get("truncated")) is not bool
    ):
        return None
    # 平台既有凭据过滤仍生效；原文的空白、语言和标点不做模型改写。
    source = {
        key: redact_inline_secrets(item) if isinstance(item, str) else item
        for key, item in value.items()
    }
    encoded = json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _SOURCE_BYTE_LIMIT:
        source["truncated"] = True
        text = source["text"]
        low, high = 0, len(text)
        # Python 切片按 Unicode 字符截断；JSON 转义和多字节文字均计入最终预算。
        while low < high:
            middle = (low + high + 1) // 2
            source["text"] = text[:middle]
            size = len(json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if size <= _SOURCE_BYTE_LIMIT:
                low = middle
            else:
                high = middle - 1
        source["text"] = text[:low]
    return source


def _fenced(text: str) -> str:
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


def _render(sources: list[dict[str, Any]]) -> str:
    if not sources:
        raise BrowserSourceError(SAFE_FAILURE)
    blocks = ["网页原文（仅所读取内容；保留原语言，适用平台安全过滤）"]
    for source in sources:
        blocks.extend([
            "来源 URL：\n" + _fenced(source["url"]),
            "页面标题：\n" + _fenced(source["title"]),
            "原文：\n" + _fenced(source["text"]),
        ])
        if source["truncated"]:
            blocks.append("原文已达到读取长度上限，以上为已读取的截断内容。")
    rendered = "\n\n".join(blocks)
    if len(rendered) > _PERSIST_MAX_CONTENT_CHARS:
        raise BrowserSourceError("网页原文超过消息交付长度上限，请缩小读取范围。")
    return rendered


def safe_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """保留真实工具状态和安全错误，阻断所有自由文本、工具参数及 stdout。"""
    kind = event.get("type")
    if kind in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
        safe = {key: event[key] for key in ("type", "toolCallId", "toolName", "isError") if key in event}
        if kind == "tool_execution_end":
            safe["result"] = "工具执行失败。" if event.get("isError") else "工具执行完成。"
        return safe
    if kind == "extension_ui_request":
        # 审批仍需显示真实操作说明，不能让用户盲目授权。
        return event
    message = event.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant" and message.get("stopReason") == "error":
        return {"type": "message_end", "message": {
            key: message[key] for key in ("role", "stopReason", "errorMessage", "responseId") if key in message
        }}
    if kind in {"error", "extension_error", "runtime", "compaction_start", "compaction_end",
                "auto_retry_start", "auto_retry_end", "model_retry_wait", "model_retry_resumed", "model_retry_cancelled"}:
        return {key: event[key] for key in (
            "type", "code", "toolCallId", "toolName", "event", "status", "attempt", "retryAfterSeconds",
        ) if key in event}
    return None


def _proof(source: dict[str, Any], scope: tuple[str, str, str], call_id: str) -> str:
    # 独立用途域；永久回执不是登录令牌，无权限授予和短期过期语义。
    encoded = json.dumps(
        [_PROOF_DOMAIN, *scope, call_id, source], sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(settings.jwt_secret.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


class BrowserSourceTurn:
    def __init__(self) -> None:
        self._started: dict[str, bool] = {}
        self._seen: set[str] = set()
        self.sources: dict[str, dict[str, Any]] = {}
        self.model_activity_started = False

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        delta = event.get("assistantMessageEvent")
        if kind == "message_update" and isinstance(delta, dict):
            if delta.get("type") in {"toolcall_start", "toolcall_end"} or (
                delta.get("type") == "text_delta" and delta.get("delta")
            ):
                self.model_activity_started = True
        call_id = event.get("toolCallId")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 128:
            return
        if kind == "tool_execution_start":
            args = event.get("args")
            valid = (
                call_id not in self._seen and event.get("toolName") == "browser_read_page"
                and isinstance(args, dict) and set(args) <= {"url", "selector"}
                and _http_url(args.get("url"))
                and ("selector" not in args or isinstance(args["selector"], str))
            )
            self._seen.add(call_id)
            self._started[call_id] = valid
            self.sources.pop(call_id, None)
        elif kind == "tool_execution_end":
            valid_start = self._started.pop(call_id, False)
            if not valid_start or event.get("toolName") != "browser_read_page" or event.get("isError") is not False:
                return
            result = event.get("result")
            source = _source(result.get("details")) if isinstance(result, dict) else None
            if source is not None:
                self.sources[call_id] = source

    def render(self) -> str:
        return _render(list(self.sources.values()))

    def persist_receipts(self, parts: list[dict[str, Any]], scope: tuple[str, str, str]) -> None:
        """绕过通用工具输出的 4000 字展示截断，但仍遵守持久化 80000 字上限。"""
        stored = set()
        for part in parts:
            call_id = part.get("tool_call_id")
            if not isinstance(call_id, str):
                continue
            source = self.sources.get(call_id)
            if source is None or part.get("type") != "tool" or part.get("status") != "done":
                continue
            # 只编码已经过滤的原文，避免通用字符串脱敏破坏 JSON 转义或签名。
            data = base64.urlsafe_b64encode(
                json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
            receipt = json.dumps({
                "kind": "browser_delivery_v1", "data": data,
                "proof": _proof(source, scope, call_id),
            }, ensure_ascii=False, separators=(",", ":"))
            if len(receipt) > _RECEIPT_LIMIT:
                raise BrowserSourceError("网页回执超过消息保存长度上限。")
            part["output_text"] = receipt
            stored.add(call_id)
        if stored != set(self.sources):
            raise BrowserSourceError("网页回执超过消息执行记录上限。")


def replay(message: dict[str, Any], scope: tuple[str, str, str]) -> dict[str, Any]:
    """只重建本条助手消息的已签回执；客户端同步的伪造/跨消息回执无效。"""
    sources = []
    parts = []
    seen = set()
    for part in message.get("parts") or []:
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        if part.get("tool_name") != "browser_read_page" or part.get("status") != "done":
            continue
        call_id = part.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id or call_id in seen:
            raise BrowserSourceError(SAFE_FAILURE)
        try:
            receipt = json.loads(part.get("output_text") or "")
            if not isinstance(receipt, dict) or not isinstance(receipt.get("data"), str):
                raise BrowserSourceError(SAFE_FAILURE)
            if len(part["output_text"]) > _RECEIPT_LIMIT:
                raise BrowserSourceError(SAFE_FAILURE)
            source = _source(json.loads(base64.b64decode(
                receipt["data"], altchars=b"-_", validate=True,
            )))
        except (ValueError, TypeError, UnicodeError):
            raise BrowserSourceError(SAFE_FAILURE) from None
        if (
            source is None or receipt.get("kind") != "browser_delivery_v1"
            or not isinstance(receipt.get("proof"), str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt["proof"]) is None
            or not hmac.compare_digest(receipt["proof"], _proof(source, scope, call_id))
        ):
            raise BrowserSourceError(SAFE_FAILURE)
        seen.add(call_id)
        sources.append(source)
        parts.append({**part, "output_text": "工具执行完成。"})
    content = _render(sources)
    return {
        **message, "content": content, "thinking": None,
        "parts": [*parts, {"type": "content", "text": content}],
        "references": [], "citation_statuses": {}, "retrieval_summary": None,
        "retrieval_trace": None, "writing_project_id": None,
    }
