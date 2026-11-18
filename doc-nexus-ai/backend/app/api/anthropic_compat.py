"""Anthropic Messages 兼容层：给 DeepSeek 官方 chat-demo 用。

为什么需要它：
- chat-demo（官方开源前端）用 **Anthropic Messages 协议**（POST /v1/messages + SSE 事件流），
  而系统里配置的对话端点是 **OpenAI 兼容**（/v1/chat/completions）。
- key 只配在「模型配置」里，绝不下发浏览器：所以由本端点做协议转换，
  浏览器 → 这里（同源 + MES 登录态）→ 用配置里的 key 调上游。

只实现对话所需的最小子集：请求 model/system/messages/max_tokens/temperature/stream，
响应 text 内容块；thinking 与工具调用不映射。
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.api.deps import _user_from_token
from fastapi import Cookie, HTTPException


def _user_from_api_key(
    x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """DeepSeek 官方 chat-demo 用 ``x-api-key`` 头带凭证（Anthropic 习惯）。

    我们把它当作 MES 的登录 token：key 的真实来源仍是后端「模型配置」，
    浏览器只负责证明「我是已登录的谁」。
    """
    token = (x_api_key or "").strip()
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    user = _user_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效")
    return user

from app.core.database import transaction
from app.models.auth import User
from app.repositories import system_config as config_repo
from app.services import ai_usage, chat_tools, plain_llm
import asyncio
import logging
import time

DEFAULT_ASSISTANT_NAME = "小智"


def _assistant_name(db) -> str:
    """助手名跟品牌配置走（基础设置里可改，默认小智），不要写死。"""
    try:
        stored = config_repo.get_all(db)
        name = str(stored.get("brand_assistant_name") or "").strip()
        return name or DEFAULT_ASSISTANT_NAME
    except Exception:  # noqa: BLE001 - 读不到品牌就用默认名，不影响对话
        return DEFAULT_ASSISTANT_NAME


def _identity_prompt(name: str) -> dict[str, str]:
    """身份与能力边界。

    两件事必须同时说清：
    1. **身份**：模型默认会自报 DeepSeek，这里框成系统里的助手名；
    2. **能力**：MES 工具只是「附加能力」，不是「能力边界」。
       之前只说身份，模型自己推理成「我只连着这套系统的数据」，
       连天气这种常识问题都拒答（2026-11-17 用户截图反馈）。
    """
    return {
        "role": "system",
        "content": (
            "你是「" + name + "」，一个通用 AI 助手，部署在这套生产管理系统里。"
            + chr(10) + chr(10) +
            "身份："
            + chr(10) +
            "- 回答时以「" + name + "」自称；不要提模型厂商、训练来源、底层模型或版本号。"
            + chr(10) +
            "- 用户问你是谁，就说是" + name + "，这套系统的 AI 助手。"
            + chr(10) + chr(10) +
            "能力边界（重要）："
            + chr(10) +
            "- 你可以正常回答**任何话题**：常识、天气、写作、翻译、计算、编程、学习方法等，都不需要调用工具。"
            + chr(10) +
            "- 工具（联网检索、查单据、库存、流程追踪、生成报表）都是**附加能力**，不是你能聊什么的限制。"
            + chr(10) +
            "- 涉及这套系统的业务数据时，才用 MES 工具查；并且只能拿到当前账号有权限的那部分。"
            + chr(10) +
            "- 需要实时信息或站外资料（新闻、政策、行业数据、公司情况、产品资料）时，**先调 web_search 联网检索**，"
            "不要凭记忆回答，也不要声称自己没有联网能力。"
            + chr(10) +
            "- 检索到结果后引用要点并注明来源；检索失败就说明失败原因，再给可执行的替代路径。"
            + chr(10) +
            "- **同一个问题最多检索两次**：拿到能回答的内容就直接作答，不要为求全反复查。"
            + chr(10) +
            "- 不确定的事说不确定，不要编造。"
        ),
    }

router = APIRouter(tags=["anthropic-compat"])


def _text_of(content: Any) -> str:
    """Anthropic 的 content 可能是字符串，也可能是 [{type:text,text}]。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


def _to_openai_messages(body: dict[str, Any]) -> list[dict[str, str]]:
    """只转换对话消息；身份说明统一由 _identity_prompt 负责。

    为什么不收 body.system：chat-demo 每次请求都固定带一句
    「该助手为DeepSeek，由深度求索公司创造。」（store.ts 里的 SYSTEM_PROMPT）。
    那条一旦进对话，模型就会拿它跟我们的身份约束对冲，
    甚至编出「上一轮对话里夹了一句…」这种用户根本没说过的话（2026-11-17 用户反馈）。
    """
    out: list[dict[str, str]] = []
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        text = _text_of(item.get("content"))
        if text:
            out.append({
                "role": "user" if role == "user" else "assistant",
                "content": text,
            })
    return out


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False)
    return ("event: " + event + chr(10) + "data: " + data + chr(10) + chr(10)).encode()


def _error_frame(message: str, kind: str = "api_error", *, stream: bool = True):
    # 非流式调用方只认 JSON，给它吐 SSE 会得到一段解不开的文本（2026-11-17 踩过）。
    if not stream:
        return {"type": "error", "error": {"type": kind, "message": message}}
    payload = _sse("error", {"type": "error", "error": {"type": kind, "message": message}})
    return StreamingResponse(iter([payload]), media_type="text/event-stream")


@router.post("/anthropic/v1/messages")
async def anthropic_messages(
    body: dict[str, Any],
    user: User = Depends(_user_from_api_key),
    anthropic_version: Annotated[str | None, Header(alias="anthropic-version")] = None,
    # chat-demo 用 x-api-key 带凭证；这里显式声明，供工具循环按同一身份取数
    x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
):
    """Anthropic Messages → OpenAI 兼容调用 → 再转回 Anthropic 事件流。"""
    messages = _to_openai_messages(body)
    if not messages:
        return _error_frame("messages 不能为空", "invalid_request_error")

    model_hint = str(body.get("model") or "")
    max_tokens = int(body.get("max_tokens") or 2048)
    temperature = float(body.get("temperature") if body.get("temperature") is not None else 0.6)
    want_stream = bool(body.get("stream", True))
    message_id = "msg_" + uuid.uuid4().hex[:24]

    # 端点解析与助手名只在开头取一次；流式响应期间不再占用数据库连接
    with transaction() as db:
        assistant_name = _assistant_name(db)
        # 额度校验放在最前面：超额就直接拒，不浪费一次模型调用
        allowed, quota_message, _ = ai_usage.check_quota(db, str(getattr(user, "id", "")))
    if not allowed:
        return _error_frame(quota_message, "permission_error", stream=want_stream)

    try:
        with transaction() as db:
            target = plain_llm.resolve_chat_target(db)
    except Exception as exc:  # noqa: BLE001 - 未配置端点时要给可读提示
        return _error_frame(str(exc), stream=want_stream)

    # 身份提示词放最前：模型默认会自称 DeepSeek，必须先把身份框住
    messages = [_identity_prompt(assistant_name)] + [
        m for m in messages if m.get("role") != "system"
    ] + [m for m in messages if m.get("role") == "system"]

    # 服务端工具循环：chat-demo 是纯浏览器客户端，不会自己执行工具，
    # 所以工具调用一律在这里完成，对前端完全透明（它只看到普通文本回复）。
    _, final_text, turn_stats = await _run_with_tools(
        target,
        messages,
        token=(x_api_key or "").strip(),
        user_id=str(getattr(user, "id", "")),
        max_tokens=max_tokens,
        temperature=temperature,
    )

    # 记一条用量流水。记账失败不能影响回答本身——用户不该因为统计出错而看不到回复。
    try:
        with transaction() as db:
            ai_usage.record_usage(
                db,
                user_id=str(getattr(user, "id", "")),
                username=str(getattr(user, "username", "") or ""),
                real_name=str(getattr(user, "display_name", "") or ""),
                model=str(getattr(target, "model", "") or ""),
                provider=str(getattr(getattr(target, "endpoint", None), "provider", "") or ""),
                input_tokens=int(turn_stats.get("tokens_in") or 0),
                output_tokens=int(turn_stats.get("tokens_out") or 0),
                cache_read_tokens=int(turn_stats.get("tokens_cache_read") or 0),
                rounds=int(turn_stats.get("rounds") or 0),
                duration_ms=int(turn_stats.get("total_ms") or 0),
                session_id=str(body.get("session_id") or ""),
                source="chat",
            )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("ai_usage record failed", exc_info=True)

    async def events() -> AsyncIterator[bytes]:
        yield _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model_hint or "configured",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                },
            },
        })
        yield _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        emitted = 0
        for delta in _chunks(final_text):
            if not delta:
                continue
            emitted += len(delta)
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": delta},
            })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {
                # 前端 isUsage 要求这四个字段都存在且是数字，缺一个整段 usage 会被丢弃
                # （2026-11-17 踩过：只给 output_tokens，结果 timing 永远拿不到）。
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": max(1, emitted // 2),
                # timing 是自定义扩展：isUsage 只校验必需字段、不裁剪多余键，能原样拿到。
                # server_tool_use 是协议原生字段，顺手把检索次数填上。
                "timing": turn_stats,
                "server_tool_use": {
                    "web_search_requests": turn_stats.get("searches", 0),
                    "tool_search": 0,
                },
            },
        })
        yield _sse("message_stop", {"type": "message_stop"})

    if not want_stream:
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model_hint or "configured",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": max(1, len(final_text) // 2),
                "timing": turn_stats,
            },
        }

    return StreamingResponse(events(), media_type="text/event-stream")


def _chunks(text: str, size: int = 24):
    """把最终文本切块吐出，前端仍能感到「在逐字输出」，但不需要上游真流式。"""
    value = text or ""
    for index in range(0, len(value), size):
        yield value[index:index + size]


def _openai_tools() -> list[dict]:
    """Anthropic 的 input_schema → OpenAI 的 function.parameters。"""
    out = []
    for schema in chat_tools.schemas():
        out.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


async def _run_one_tool(call: dict, *, token: str, user_id: str):
    """解析并执行单个 tool_call，返回 (name, args, result)。"""
    fn = call.get("function") or {}
    name = str(fn.get("name") or "")
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (ValueError, TypeError):
        args = {}
    # 同步工具丢线程池：这样同一轮的多个检索能并行，不必一个个等
    result = await asyncio.to_thread(
        chat_tools.run_tool, name, args, token=token, user_id=user_id
    )
    return name, args, result

async def _run_with_tools(
    target,
    messages: list[dict],
    *,
    token: str,
    user_id: str,
    max_tokens: int,
    temperature: float,
    # 4 轮实测能把一次问答拖到 50 秒（模型本身 1.6s/轮，时间都耗在等检索）。
    # 3 轮足够「查一次 → 按需补一次 → 作答」，再多是模型在自我重复。
    max_rounds: int = 3,
):
    """跑工具循环：模型要调工具就执行，把结果喂回去，直到它给出最终回答。

    返回 (工具轨迹, 最终文本, 耗时统计)。工具轨迹只用于日志排障，不回给前端；
    耗时统计随 SSE 的 usage 下发，前端在回答末尾做成可展开的小字（隐式显示）。
    """
    tools = _openai_tools()
    started = time.perf_counter()
    model_ms = 0.0
    tool_ms = 0.0
    searches = 0
    rounds = 0
    # token 累加：一次问答可能多轮模型调用，全都要算进这个账号的本月成本
    tokens_in = 0
    tokens_out = 0
    tokens_cache_read = 0
    trace: list[str] = []
    convo = list(messages)

    def _stats() -> dict:
        return {
            "total_ms": int((time.perf_counter() - started) * 1000),
            "model_ms": int(model_ms),
            "tool_ms": int(tool_ms),
            "rounds": rounds,
            "searches": searches,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_cache_read": tokens_cache_read,
        }

    for _ in range(max_rounds):
        rounds += 1
        _t0 = time.perf_counter()
        try:
            text, calls, call_usage = await plain_llm.complete_with_tools(
                target,
                convo,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 - 上游失败要变成可读文本
            return trace, "模型调用失败：" + str(exc), _stats()
        finally:
            model_ms += (time.perf_counter() - _t0) * 1000
        tokens_in += int((call_usage or {}).get("input_tokens") or 0)
        tokens_out += int((call_usage or {}).get("output_tokens") or 0)
        tokens_cache_read += int((call_usage or {}).get("cache_read_tokens") or 0)

        if not calls:
            return trace, text, _stats()

        # 把模型这一轮的 tool_calls 记进对话，再执行
        convo.append({"role": "assistant", "content": text or "", "tool_calls": calls})

        # 同一轮的多个工具并行执行（串行时两个检索要 7.2s，模型本身只要 1.6s）
        _t1 = time.perf_counter()
        outcomes = await asyncio.gather(
            *[_run_one_tool(call, token=token, user_id=user_id) for call in calls],
            return_exceptions=True,
        )
        tool_ms += (time.perf_counter() - _t1) * 1000
        for call, outcome in zip(calls, outcomes):
            if isinstance(outcome, Exception):
                result = "工具执行失败：" + str(outcome)
                name = str(((call.get("function") or {}).get("name") or "unknown"))
                args = {}
            else:
                name, args, result = outcome
            if name == "web_search":
                searches += 1
            trace.append(name + "(" + str(args)[:80] + ")")
            convo.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": result,
            })

    # 工具轮数用完还没收敛：给一句兜底，不要把半成品当答案
    return trace, "我需要更多信息才能继续。可以再补充一下单号或客户名吗？", _stats()
