"""数据看板的 AI 分析：拿看板上的真实指标，产出「增长点 + 建议」。

设计取舍：
- 输入只接受调用方传进来的指标字典，不自己再查一遍库 —— 分析对象必须与
  用户屏幕上看到的是同一份数，否则「AI 说的」和「界面显示的」会打架。
- 输出强制 JSON（summary / growth / advice），前端好渲染，也避免模型自由发挥成大段散文。
- 模型不可用时返回 available=false，看板据此显示降级文案，不阻塞页面。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是制造业 MES 的经营分析师。用户会给你一屏经营看板的真实指标（JSON）。
请据此做一次简洁的经营分析，只输出 JSON，结构为：
{"summary": "一段话结论，不超过 80 字",
 "growth": ["增长点，每条不超过 30 字", "..."],
 "advice": ["可执行建议，每条不超过 30 字", "..."]}

要求：
1. 只依据给定指标推断，不要编造没有出现的数字或业务；
2. growth 给 2-3 条，聚焦「哪里还有提升空间」；
3. advice 给 2-3 条，必须是下周就能动手的动作；
4. 指标里出现 0 或空值时，先考虑「是不是数据还没录」，再谈业务结论；
5. 中文，句子短，不用营销辞藻。"""

MAX_METRIC_CHARS = 4000


def _fallback(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "summary": "", "growth": [], "advice": []}


async def analyze(db: Session, metrics: dict[str, Any]) -> dict[str, Any]:
    """把看板指标交给模型做一次分析。失败一律降级，不抛错。"""
    if not metrics:
        return _fallback("没有可分析的指标")

    payload = json.dumps(metrics, ensure_ascii=False)
    if len(payload) > MAX_METRIC_CHARS:
        # 指标太多会挤掉输出预算：截断并说明，别让模型只回半句
        payload = payload[:MAX_METRIC_CHARS] + "…（已截断）"

    from app.services.plain_llm import LlmCallFailed, LlmUnavailable, complete_chat

    try:
        reply = await complete_chat(
            db,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            response_format_json=True,
            disable_thinking=True,
            max_tokens=1200,
            timeout_seconds=60,
        )
    except (LlmUnavailable, LlmCallFailed) as exc:
        logger.info("dashboard_analysis.unavailable", extra={"error": str(exc)})
        return _fallback(f"AI 分析暂不可用：{exc}")
    except Exception:  # noqa: BLE001
        logger.exception("dashboard_analysis.failed")
        return _fallback("AI 分析失败")

    text = (reply.text or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return _fallback("AI 返回的不是合法 JSON")

    def _list(key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:4]

    summary = str(data.get("summary") or "").strip()
    growth = _list("growth")
    advice = _list("advice")
    if not (summary or growth or advice):
        return _fallback("AI 没有给出有效结论")

    return {
        "available": True,
        "reason": "",
        "summary": summary,
        "growth": growth,
        "advice": advice,
        "generatedAt": None,
    }
