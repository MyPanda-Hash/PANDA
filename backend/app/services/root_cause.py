"""根因归并：同一张单上的多个问题串成一条，反复复发的标出来。

为什么需要它：现在的问题是「一张单卡住」会被拆成好几条事件——
审批没过、排产没做、工序停了，每条都独立提醒一次。人看到的是三件事，
实际是一件事卡在那儿。更糟的是这件事关掉又回来，又重新报一遍。

这个模块做两件事：

1. **同一实体的事件聚成一簇**，给出「这张单到底卡在哪」的一句话判断；
2. **复发识别**：关掉又回来的事件带``recurrence`` 计数，簇里只要有一条
   复发 ≥2 次，就说明根因没动，光处理表面会一直回来。

只读、不写库，也不调模型。归并结果给今日计划和周回顾用。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assistant_watch import AssistantEvent
from app.services.rule_thresholds import current as _rules

#: 一条事件被当成「反复复发」的门槛
RECUR_SEVERE = 2


@dataclass
class RootCause:
    """一张单（或一个实体）上的一组问题。"""

    key: str
    label: str
    #: doc=同一张单；supplier / customer / approver=跨单的业务链
    kind: str = "doc"
    events: list[AssistantEvent] = field(default_factory=list)
    recurrence: int = 0
    kinds: list[str] = field(default_factory=list)
    severity: str = "info"
    verdict: str = ""
    steps: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "count": self.count,
            "kinds": self.kinds,
            "recurrence": self.recurrence,
            "severity": self.severity,
            "verdict": self.verdict,
            "steps": self.steps,
            "titles": [event.title for event in self.events[:5]],
        }

    def render(self) -> str:
        head = {
            "doc": f"{self.label}：{self.count} 个问题",
            "supplier": self.label,
            "customer": self.label,
            "approver": self.label,
        }.get(self.kind, f"{self.label}：{self.count} 个问题")
        lines = [head]
        if self.recurrence >= RECUR_SEVERE:
            lines.append(f"  其中一件事已经复发 {self.recurrence} 次，根因没解决")
        for index, event in enumerate(self.events[:5], start=1):
            lines.append(f"  {index}. {event.title}")
        if self.verdict:
            lines.append(f"  判断：{self.verdict}")
        if self.steps:
            lines.append("  处置：" + "；".join(
                f"{i}) {step}" for i, step in enumerate(self.steps[:3], start=1)
            ))
        return "\n".join(lines)


def _payload(event: AssistantEvent) -> dict[str, Any]:
    try:
        data = json.loads(event.payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _key_of(event: AssistantEvent) -> str:
    if event.doc_no:
        return f"doc:{event.doc_no}"
    return f"kind:{event.kind}"


def _label_of(events: list[AssistantEvent]) -> str:
    doc_no = next((e.doc_no for e in events if e.doc_no), "")
    if doc_no:
        # 面板名在 payload 里，能取到就用「其他入库单 OTHE-…」这种完整叫法
        for event in events:
            name = str(_payload(event).get("panelName") or "").strip()
            if name:
                return f"{name} {doc_no}"
        return f"单据 {doc_no}"
    from app.services.assistant_watch import KIND_SUMMARY

    return KIND_SUMMARY.get(events[0].kind, events[0].kind)


def cluster(events: list[AssistantEvent]) -> list[RootCause]:
    """把事件按实体聚簇。同一张单上的所有问题算一簇。"""
    buckets: dict[str, list[AssistantEvent]] = defaultdict(list)
    for event in events:
        if event.archived_at is not None:
            continue
        buckets[_key_of(event)].append(event)

    out: list[RootCause] = []
    for key, items in buckets.items():
        if len(items) < 2 and max((item.recurrence or 0) for item in items) < RECUR_SEVERE:
            # 单个、没复发的问题不需要归并，原样交给上层
            continue
        out.append(_build(key, items))
    out.sort(key=lambda item: (-item.recurrence, -item.count))
    return out


def _build(key: str, items: list[AssistantEvent]) -> RootCause:
    weight = {"critical": 3, "warning": 2, "info": 1}
    severity = max(items, key=lambda e: weight.get(e.severity, 0)).severity
    kinds: list[str] = []
    for event in items:
        if event.kind not in kinds:
            kinds.append(event.kind)
    recurrence = max((item.recurrence or 0) for item in items)

    steps: list[str] = []
    for event in items:
        for step in _payload(event).get("steps") or []:
            text = str(step).strip()
            if text and text not in steps:
                steps.append(text)

    cause = RootCause(
        key=key,
        label=_label_of(items),
        events=sorted(items, key=lambda e: (-weight.get(e.severity, 0), e.title)),
        recurrence=recurrence,
        kinds=kinds,
        severity=severity,
        steps=steps[:4],
    )
    cause.verdict = _verdict(cause)
    return cause


def _verdict(cause: RootCause) -> str:
    """一句话说清「这些其实是同一件事」。"""
    if cause.recurrence >= RECUR_SEVERE:
        return (
            f"这件事已经第 {cause.recurrence + 1} 次出现，处理一次不算完，"
            "要找到为什么会回来"
        )
    # 同一张单卡在多个审批环节：这不是重复，是必须全过才放行
    if cause.kinds == ["pending_approval"] and cause.count >= 2:
        approvers = []
        for event in cause.events:
            name = str(_payload(event).get("approver") or "").strip()
            if name and name not in approvers:
                approvers.append(name)
        who = "、".join(approvers) if approvers else f"{cause.count} 个环节"
        return f"卡在审批上（{who}），都过了才能往下走，别分几次催"
    if len(cause.kinds) >= 3:
        return "这些问题成串出现，通常卡在最前面那一环，后面都是连带"
    if len(cause.kinds) == 2:
        return "两个问题大概率同源，先处理更靠前的那个"
    return ""


def analyze(db: Session, *, limit: int | None = None) -> list[RootCause]:
    """读库里的未处理事件，给出根因簇。"""
    events = list(
        db.scalars(
            select(AssistantEvent).where(
                AssistantEvent.status == "open",
                AssistantEvent.archived_at.is_(None),
            )
        )
    )
    causes = cluster(events)
    causes.extend(cross_entity(events))
    causes.sort(key=lambda item: (-item.recurrence, -item.count))
    return causes[:limit] if limit else causes


def render_all(causes: list[RootCause]) -> str:
    if not causes:
        return "没有发现需要归并的问题：每件事都只出现一次，或者都已经处理掉了。"
    doc_causes = [c for c in causes if c.kind == "doc"]
    chain_causes = [c for c in causes if c.kind in ("supplier", "customer", "approver", "chain")]
    lines: list[str] = []
    if doc_causes:
        lines.append(f"### 一张单上的多个问题（{len(doc_causes)} 组）")
        for index, cause in enumerate(doc_causes, start=1):
            lines.append(f"{index}. {cause.render()}")
            lines.append("")
    if chain_causes:
        lines.append("### 跨单的共同根因（不是同一张单，但很可能是同一件事）")
        for index, cause in enumerate(chain_causes, start=1):
            # 同类簇的处置步骤基本一样，只在最后统一印一次，别每条重复三行
            lines.append(f"{index}. " + "\n   ".join(
                line for line in cause.render().split("\n") if "处置：" not in line
            ))
            lines.append("")
        lines.append("怎么处理：")
        for kind, title in (
            ("supplier", "供应侧"),
            ("customer", "交付侧"),
            ("approver", "流程侧"),
        ):
            sample = next((c for c in chain_causes if c.kind == kind and c.steps), None)
            if sample is not None:
                steps = "；".join(
                    f"{i}) {step}" for i, step in enumerate(sample.steps[:3], start=1)
                )
                lines.append(f"- {title}：{steps}")
    return "\n".join(lines).strip()

# ---------------------------------------------------------------------------
# 跨实体关联：两个问题没有共同单号，但可能是同一条业务链断了
# ---------------------------------------------------------------------------

# 门槛统一在 rule_thresholds（MES_RULE_SUPPLIER_MIN_EVENTS 等）


def _entity(event: AssistantEvent, name: str) -> str:
    return str(_payload(event).get(name) or "").strip()


def cross_entity(events: list[AssistantEvent]) -> list[RootCause]:
    """按供应商 / 客户 / 审批人找跨单的共同根因。

    只收「跨了多张单」的簇：一张单上的问题已经按单号聚过了，
    这里再报一遍等于说话说两遍。
    """
    out: list[RootCause] = []
    out.extend(_buy_sell_same_entity(events))
    out.extend(_supplier_chains(events))
    out.extend(_customer_chains(events))
    out.extend(_approver_bottlenecks(events))
    return out


def _buy_sell_same_entity(events: list[AssistantEvent]) -> list[RootCause]:
    """同一家公司既是供应商又是客户，且两端都有待办。

    这种单子分开处理一定会打架：采购那边在催交期，销售那边在给承诺，
    实际是同一批货的两端。AI 跑一遍就能发现，不该靠人去对名字。
    """
    buckets: dict[str, list[AssistantEvent]] = defaultdict(list)
    for event in events:
        name = str(_payload(event).get("dual_role") or "").strip()
        if name:
            buckets[name].append(event)

    out: list[RootCause] = []
    for name, items in buckets.items():
        buys = [e for e in items if _entity(e, "supplier")]
        sells = [e for e in items if _entity(e, "customer")]
        if not buys or not sells:
            continue
        cause = RootCause(
            key=f"dual:{name}",
            label=f"{name}（买卖同源：既是供应商又是客户）",
            kind="chain",
            events=sorted(items, key=lambda e: e.title),
            recurrence=max((item.recurrence or 0) for item in items),
            kinds=sorted({item.kind for item in items}),
            severity="warning",
            steps=[
                f"把{name}的采购单与销售单放在一起看，确认是不是同一批货",
                "同一批货就只承诺一次，别一边催交期一边答应客户",
                "确认不了就问业务：是直发、转手，还是单据填错了",
            ],
        )
        cause.verdict = (
            f"{name}既供货给我们、又向我们买货，两端各挂着 "
            f"{len(buys)} 条和 {len(sells)} 条待办，很可能是同一批货的两头"
        )
        out.append(cause)
    return out


def _supplier_chains(events: list[AssistantEvent]) -> list[RootCause]:
    """R1：同一供应商的采购逾期 + 到货没检，是「这条线断了」，不是两件事。"""
    buckets: dict[str, list[AssistantEvent]] = defaultdict(list)
    for event in events:
        supplier = _entity(event, "supplier")
        if supplier:
            buckets[supplier].append(event)

    rules = _rules()
    out: list[RootCause] = []
    for supplier, items in buckets.items():
        kinds = {item.kind for item in items}
        if len(items) < rules.supplier_min_events or len(kinds) < 2:
            continue
        # 每条单据只算一次，避免同一张单的两条事件把数量灌水
        docs = {item.doc_no or item.id for item in items}
        label = f"供应商 {supplier}（{len(docs)} 张单）"
        cause = RootCause(
            key=f"supplier:{supplier}",
            label=label,
            kind="supplier",
            events=sorted(items, key=lambda e: e.title),
            recurrence=max((item.recurrence or 0) for item in items),
            kinds=sorted(kinds),
            severity="critical" if len(docs) >= 3 else "warning",
            steps=[
                f"找{supplier}要一个明确的交期承诺",
                "已到货的先安排检验放行，别压在待检区",
                "影响生产的料，评估换供应商或调拨",
            ],
        )
        cause.verdict = (
            f"{supplier}这条线断了：{len(items)} 条问题挂在 {len(docs)} 张单上，"
            "料到了也用不上，先解决供应端"
        )
        out.append(cause)
    return out


def _customer_chains(events: list[AssistantEvent]) -> list[RootCause]:
    """R2：同一客户的几张单卡在不同环节，通常是交付承诺的问题，不是单据的问题。"""
    buckets: dict[str, list[AssistantEvent]] = defaultdict(list)
    for event in events:
        customer = _entity(event, "customer")
        if customer:
            buckets[customer].append(event)

    rules = _rules()
    out: list[RootCause] = []
    for customer, items in buckets.items():
        docs = {item.doc_no for item in items if item.doc_no}
        if len(docs) < rules.customer_min_docs:
            continue
        cause = RootCause(
            key=f"customer:{customer}",
            label=f"客户 {customer}（{len(docs)} 张单）",
            kind="customer",
            events=sorted(items, key=lambda e: e.title),
            recurrence=max((item.recurrence or 0) for item in items),
            kinds=sorted({item.kind for item in items}),
            severity="critical" if len(docs) >= 3 else "warning",
            steps=[
                f"把{customer}名下未完成的单拉成一张表，按交期排",
                "统一对外承诺一次，避免每张单单独回话",
                "有赶不上的提前告知，别等客户来问",
            ],
        )
        cause.verdict = (
            f"{customer}有 {len(docs)} 张单卡在不同环节，"
            "按单处理会一直在救火，按客户整体排一次更省事"
        )
        out.append(cause)
    return out


def _approver_bottlenecks(events: list[AssistantEvent]) -> list[RootCause]:
    """R5：同一个审批人压着多张单——这是人的瓶颈，不是单据的问题。"""
    buckets: dict[str, list[AssistantEvent]] = defaultdict(list)
    for event in events:
        if event.kind != "pending_approval":
            continue
        approver = _entity(event, "approver")
        if approver:
            buckets[approver].append(event)

    rules = _rules()
    out: list[RootCause] = []
    for approver, items in buckets.items():
        if len(items) < rules.approver_min_events:
            continue
        docs = {item.doc_no or item.id for item in items}
        cause = RootCause(
            key=f"approver:{approver}",
            label=f"审批人 {approver}（{len(docs)} 张单待审）",
            kind="approver",
            events=sorted(items, key=lambda e: e.title),
            recurrence=max((item.recurrence or 0) for item in items),
            kinds=["pending_approval"],
            severity="critical" if len(docs) >= 5 else "warning",
            steps=[
                f"{approver}一次审完这一批，别一张一张点",
                "长期压着就加签或临时授权，别让人卡住整条流程",
                "看看是不是审批规则配得太细，同类单不该逐张过",
            ],
        )
        cause.verdict = (
            f"{len(docs)} 张单都压在 {approver} 一个人身上，"
            "这是流程瓶颈，催单据没用，要动审批安排"
        )
        out.append(cause)
    return out
