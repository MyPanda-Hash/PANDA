"""报表的模型解读：把已算准的数字交给模型，请它写「分析 + 建议」。

硬性原则：
1. **数字由代码算**，模型只负责归因、排序、给建议，不许自己编数字。
2. 输出必须能核对：要求模型引用上下文里出现过的事实；编造的数字会被校验剔除。
3. 模型不可用就退回纯模板报表，不报错、不留空。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位制造业 MES 运营顾问，为工厂管理层写经营分析。

规则：
1. 只能使用我给出的数字和事实，**绝对不要编造任何数字、客户名、单号**。
2. 先给结论，再给依据，最后给可执行建议。
3. 建议必须落到「谁、做什么、什么时候」，不要写"加强管理"这类空话。
4. 输出一个 JSON 对象，不要代码块标记、不要解释。

输出格式：
{
  "summary": "两三句话总结这段时期最关键的问题",
  "highlights": ["最重要的 2-3 个发现，每条不超过 40 字"],
  "advice": [
    {"target": "角色或岗位", "action": "具体动作", "reason": "为什么", "urgency": "high|normal"}
  ],
  "risks": ["如果不处理会怎样，1-2 条"]
}"""


@dataclass
class ReportInsight:
    summary: str = ""
    highlights: list[str] = None
    advice: list[dict[str, str]] = None
    risks: list[str] = None

    def __post_init__(self) -> None:
        self.highlights = self.highlights or []
        self.advice = self.advice or []
        self.risks = self.risks or []


def build_prompt(ctx: Any) -> str:
    """把 ReportContext 转成模型可读的事实清单。"""
    start = ctx.period_start.astimezone().strftime("%Y-%m-%d %H:%M")
    end = ctx.period_end.astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"报表类型：{ctx.period_label}",
        f"统计区间：{start} ~ {end}",
        "",
        "【确定的事实】",
        f"- 新发现待办 {ctx.created} 条",
        f"- 已处理 {ctx.resolved} 条",
        f"- 当前积压 {ctx.open_now} 条" + (f"，其中 {ctx.stale} 条超过 24 小时" if ctx.stale else ""),
    ]
    if ctx.avg_ack_hours is not None:
        lines.append(f"- 平均认领耗时 {ctx.avg_ack_hours} 小时")
    if ctx.ack_within_24h is not None:
        lines.append(f"- 24 小时内认领比例 {round(ctx.ack_within_24h * 100)}%")
    if ctx.scans:
        lines.append(
            f"- 巡查 {ctx.scans} 轮，失败 {ctx.scan_failures} 次"
            + (f"，平均耗时 {ctx.avg_scan_ms} ms" if ctx.avg_scan_ms else "")
        )
    if ctx.missing_owners:
        lines.append(f"- 有 {ctx.missing_owners} 条待办没有明确负责人")

    if ctx.by_kind:
        lines.append("")
        lines.append("【新增按类型】")
        for kind, count in sorted(ctx.by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_kind_label(kind)}: {count} 条")

    if ctx.resolved_by_kind:
        lines.append("")
        lines.append("【已处理按类型】")
        for kind, count in sorted(ctx.resolved_by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_kind_label(kind)}: {count} 条")

    if ctx.by_person:
        lines.append("")
        lines.append("【积压按人】")
        for name, count in list(ctx.by_person.items())[:10]:
            lines.append(f"- {name}: {count} 条")

    if ctx.top_items:
        lines.append("")
        lines.append("【重点关注的具体事项】")
        for item in ctx.top_items:
            lines.append(f"- [{item['severity']}] {item['title']}")

    lines.append("")
    lines.append("请基于以上事实输出 JSON。")
    return "\n".join(lines)


def _kind_label(kind: str) -> str:
    from app.services.assistant_watch import KIND_LABELS

    return KIND_LABELS.get(kind, kind)


def parse_insight(raw: str) -> ReportInsight:
    """解析模型输出；容忍代码块围栏与多余文字。"""
    text = (raw or "").strip()
    if not text:
        return ReportInsight()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ReportInsight()
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return ReportInsight()
    if not isinstance(data, dict):
        return ReportInsight()

    advice: list[dict[str, str]] = []
    for item in data.get("advice") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        urgency = str(item.get("urgency") or "normal").lower()
        advice.append(
            {
                "target": str(item.get("target") or "").strip(),
                "action": action[:120],
                "reason": str(item.get("reason") or "").strip()[:120],
                "urgency": urgency if urgency in ("high", "normal") else "normal",
            }
        )

    return ReportInsight(
        summary=str(data.get("summary") or "").strip(),
        highlights=[str(x).strip() for x in (data.get("highlights") or []) if str(x).strip()][:3],
        advice=advice[:5],
        risks=[str(x).strip() for x in (data.get("risks") or []) if str(x).strip()][:2],
    )


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# 单号形如 SO-2026-08-0002 / DH-20261109-001 / MO-x-1：其中数字属于编号，不参与数值校验
_DOC_NO_RE = re.compile(r"\b[A-Za-z]{2,6}[-_]?\d{4,}(?:[-_]\d+)*\b")
# 「今日内 / 11-13 前 / 明天」这类时间承诺：模型容易编未来日期，
# 直接把这些表述替换成"尽快"，保留建议本身而不是整条丢弃。
_DATE_PHRASE_RE = re.compile(
    r"(?:今日内|今天内|明日内|明天内|本[周月]内|尽快|"
    r"(?<![\d-])\d{1,2}[-/月]\d{1,2}[日]?(?:前|之前)?)"
)


def verify_insight(insight: ReportInsight, ctx: Any) -> ReportInsight:
    """剔除编造的数字。

    把上下文里出现过的所有数字收集成集合；模型给出的数字若不在集合里，
    视为编造，丢弃该条（宁缺勿错）。

    注意：单号里的数字（SO-2026-08-0002）先剔掉再校验，否则单号会被误判成
    编造数字——这些单号本来就出现在事实清单里，属于合法引用。
    """
    prompt = build_prompt(ctx)
    allowed = set(_NUMBER_RE.findall(_DOC_NO_RE.sub(" ", prompt)))
    allowed_docs = set(_DOC_NO_RE.findall(prompt))

    dropped_numbers: list[str] = []

    def _clean(text: str) -> str | None:
        original = text
        # 先把单号抠出来占位，避免「SO-2026-08-0002」里的日期被当成时间承诺
        placeholders: dict[str, str] = {}

        def _stash(match: re.Match[str]) -> str:
            token = f"\x00{len(placeholders)}\x00"
            placeholders[token] = match.group(0)
            return token

        guarded = _DOC_NO_RE.sub(_stash, text)
        # 时间承诺里的日期换成"尽快"，避免模型编未来日期导致整条被丢
        normalized = _DATE_PHRASE_RE.sub("尽快", guarded)
        for token, doc in placeholders.items():
            normalized = normalized.replace(token, doc)
        normalized = re.sub(r"尽快\s*前", "尽快", normalized).strip()
        # 单号必须原样出现在事实清单里，不能自己编单号
        for doc in _DOC_NO_RE.findall(normalized):
            if doc not in allowed_docs:
                dropped_numbers.append(doc)
                return None
        stripped = _DOC_NO_RE.sub(" ", normalized)
        for number in _NUMBER_RE.findall(stripped):
            if number not in allowed:
                dropped_numbers.append(number)
                return None
        # 保留改写前的语气，但把日期换成"尽快"
        return normalized if normalized != original else original

    summary = _clean(insight.summary)
    highlights = [x for x in (_clean(h) for h in insight.highlights) if x]
    risks = [x for x in (_clean(r) for r in insight.risks) if x]
    advice: list[dict[str, str]] = []
    for item in insight.advice:
        cleaned_action = _clean(item["action"])
        if cleaned_action is None:
            continue
        cleaned_reason = _clean(item["reason"]) or ""
        advice.append({**item, "action": cleaned_action, "reason": cleaned_reason})

    dropped = (
        (1 if summary is None and insight.summary else 0)
        + (len(insight.highlights) - len(highlights))
        + (len(insight.advice) - len(advice))
    )
    if dropped:
        # 记下具体是哪个数字被拦，便于区分「模型编造」和「我们的白名单漏了」
        logger.warning(
            "report_insight.dropped_unverified",
            extra={"dropped": dropped, "values": dropped_numbers[:5]},
        )

    return ReportInsight(
        summary=summary or "",
        highlights=highlights,
        advice=advice,
        risks=risks,
    )


def render_insight(insight: ReportInsight) -> str:
    """把解读渲染成 Markdown 段落，追加在报表数字之后。"""
    blocks: list[str] = []
    if insight.summary:
        blocks.append("### 分析")
        blocks.append(insight.summary)
        if insight.highlights:
            blocks.append("")
            for item in insight.highlights:
                blocks.append(f"- {item}")

    if insight.advice:
        blocks.append("")
        blocks.append("### 建议")
        for index, item in enumerate(insight.advice, 1):
            who = f"**{item['target']}**：" if item["target"] else ""
            flag = "⚠️ " if item["urgency"] == "high" else ""
            line = f"{index}. {flag}{who}{item['action']}"
            if item["reason"]:
                line += f"（{item['reason']}）"
            blocks.append(line)

    if insight.risks:
        blocks.append("")
        blocks.append("### 风险")
        for item in insight.risks:
            blocks.append(f"- {item}")

    return "\n".join(blocks).strip()


async def generate_insight(ctx: Any, db: Any = None) -> str:
    """调模型生成解读段落；失败返回空串（调用方退回纯数字报表）。"""
    from app.services.plain_llm import LlmCallFailed, LlmUnavailable, complete_chat

    try:
        reply = await complete_chat(
            db,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(ctx)},
            ],
            response_format_json=True,
            disable_thinking=True,
            max_tokens=1200,
        )
    except (LlmUnavailable, LlmCallFailed) as exc:
        logger.info("report_insight.unavailable", extra={"error": str(exc)})
        return ""
    except Exception:
        logger.exception("report_insight.failed")
        return ""

    insight = verify_insight(parse_insight(reply.text), ctx)
    if not (insight.summary or insight.advice):
        return ""
    return render_insight(insight)
