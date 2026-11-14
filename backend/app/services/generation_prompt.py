from __future__ import annotations

from collections.abc import Iterable

from app.models.chat import ChatMessage

_MAX_CONTEXT_MESSAGES = 12
_MAX_CONTEXT_MESSAGE_CHARS = 800
_CONTEXT_ROLE_LABELS = {"user": "用户", "assistant": "助手"}


def _context_line(message: ChatMessage) -> str | None:
    label = _CONTEXT_ROLE_LABELS.get(message.role)
    content = (message.content or "").strip()
    if not label or not content:
        return None
    if len(content) > _MAX_CONTEXT_MESSAGE_CHARS:
        edge = (_MAX_CONTEXT_MESSAGE_CHARS - 3) // 2
        content = f"{content[:edge]}…{content[-edge:]}"
    return f"{label}：{content}"


def build_contextual_prompt(
    messages: Iterable[ChatMessage],
    prompt: str,
    *,
    generation_label: str,
    max_length: int,
    excluded_message_ids: frozenset[str] = frozenset(),
) -> str:
    normalized_prompt = (prompt or "").strip()
    context_lines = [
        line
        for message in messages
        if message.id not in excluded_message_ids
        for line in [_context_line(message)]
        if line
    ][-_MAX_CONTEXT_MESSAGES:]
    if not context_lines:
        return normalized_prompt

    header = (
        f"请根据下面的当前会话上下文完成本次{generation_label}生成请求。"
        "上下文是事实来源；请保持其中明确的主题、对象、时间、地点、数字和文字，"
        "不要凭空用默认值替换或改写这些信息。\n"
        "【当前会话上下文】\n"
    )
    request = f"【本次{generation_label}生成要求】\n{normalized_prompt}"
    context_budget = max_length - len(header) - len(request)
    if context_budget <= 0:
        return normalized_prompt

    selected: list[str] = []
    remaining = context_budget
    for line in reversed(context_lines):
        separator = 1 if selected else 0
        if len(line) + separator <= remaining:
            selected.append(line)
            remaining -= len(line) + separator
            continue
        if not selected and remaining > 0:
            selected.append(line[:remaining])
        break
    if not selected:
        return normalized_prompt
    selected.reverse()
    context = "\n".join(selected)
    return f"{header}{context}\n{request}"
