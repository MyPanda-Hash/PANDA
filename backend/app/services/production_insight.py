"""生产执行层的问题发现：从“单据状态”下沉到“工序进度”。

现有的 ``assistant_watch.evaluate`` 看的是单据级事实（已审核未排产、逾期未发货），
它能告诉人「这张单有问题」，但答不上「问题出在哪道工序、还剩多少、今天还能不能救」。

这一层补的就是后半句。数据来源是 MES 已经在给的嵌套结构：

- ``MANU_ORDER.detail.processes[]``：每道工序的计划数量、工序完工状态、待返修数量、关键工序标记
- ``PROCESS_REPORT.detail.items[]``：每张汇报单的工序名称、计划数量、报工数量、合格/不合格数量

纯函数，不碰数据库、不调模型，方便单测，也方便以后接更多面板。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.rule_thresholds import current as _rules


# 判定阈值统一在 app/services/rule_thresholds.py，可用环境变量覆盖。
# 这里保留 SPEC_EPSILON，它是浮点比较的精度，不是业务阈值。
SPEC_EPSILON = 1e-6


@dataclass
class Problem:
    """一条生产问题。带上影响面和处置步骤，直接能变成待办。"""

    kind: str
    dedupe_key: str
    title: str
    detail: str
    suggested_action: str
    severity: str = "warning"
    doc_no: str | None = None
    panel_code: str | None = "MANU_ORDER"
    targets: list[str] = field(default_factory=list)
    #: 影响面，例如「影响 3 件交期」「占用精整车间 2 天」
    impact: str = ""
    #: 处置步骤，按顺序给，最多 4 条
    steps: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "doc_no": self.doc_no,
            "panel_code": self.panel_code,
            "impact": self.impact,
            "steps": self.steps,
        }


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _number(value: Any) -> float:
    try:
        return float(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _processes_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    detail = row.get("detail")
    if not isinstance(detail, dict):
        return []
    items = detail.get("processes")
    return [item for item in items or [] if isinstance(item, dict)]


def _reports_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    detail = row.get("detail")
    if not isinstance(detail, dict):
        return []
    items = detail.get("items")
    return [item for item in items or [] if isinstance(item, dict)]


def _is_done(process: dict[str, Any]) -> bool:
    state = _text(process, "工序完工状态", "完工状态")
    return state in ("完工", "已完成", "已完工")


def analyze(
    snapshot: dict[str, list[dict[str, Any]]],
    *,
    today: date,
    owners: dict[str, list[str]] | None = None,
) -> list[Problem]:
    """把 MES 快照里的工序级数据翻译成生产问题。"""
    problems: list[Problem] = []
    problems.extend(_stalled_processes(snapshot, today, owners))
    problems.extend(_quality_issues(snapshot, today, owners))
    problems.extend(_rework_pending(snapshot, today, owners))
    problems.extend(_delivery_risk(snapshot, today, owners))
    problems.extend(_process_trends(snapshot, today, owners))
    problems.extend(_material_mismatch(snapshot, today, owners))
    return problems


def _owners(owners: dict[str, list[str]] | None, panel: str, today: date) -> list[str]:
    if owners and panel in owners:
        return list(owners[panel])
    return ["worker", "planner"]


def _dispatched(snapshot: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    """每张加工单已经排产的工序集合。"""
    out: dict[str, set[str]] = {}
    for row in snapshot.get("DISPATCH") or []:
        manu_no = _text(row, "加工单号")
        if not manu_no or _text(row, "单据状态") in ("作废", "草稿"):
            continue
        for item in _reports_of(row):
            name = _text(item, "工序名称")
            if name:
                out.setdefault(manu_no, set()).add(name)
    return out


def _reported_types(snapshot: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, str]]:
    """每张加工单的工序 → 汇报状态：``done``（有量）/ ``empty``（单建了没量）。"""
    out: dict[str, dict[str, str]] = {}
    for row in snapshot.get("PROCESS_REPORT") or []:
        manu_no = _text(row, "加工单号")
        if _text(row, "单据状态") not in ("已审核", "已提交", "审核中"):
            continue
        for item in _reports_of(row):
            name = _text(item, "工序名称")
            if not manu_no or not name:
                continue
            bucket = out.setdefault(manu_no, {})
            if _number(item.get("报工数量")) > 0:
                bucket[name] = "done"
            else:
                bucket.setdefault(name, "empty")
    return out


def _stalled_processes(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """开工多天、工序还没完工 → 车间停在那里了。

    这是「生产中的问题」最典型的一种：单子开局了，但某道工序卡住，
    单据状态还显示「生产中」，只看单据永远发现不了。

    再往下分三种，处置方式完全不同：

    - **汇报单都没建**：卡在建单环节，后面几道工序根本排不上（工序是串行的）；
    - **建了但没数量**：人干了没录，账实会不符；
    - **报了但没完工**：那是产能/进度问题，不算停滞。
    """
    rules = _rules()
    # 每张加工单已报工的工序名集合
    reported: dict[str, set[str]] = {}
    for row in snapshot.get("PROCESS_REPORT") or []:
        manu_no = _text(row, "加工单号")
        if _text(row, "单据状态") not in ("已审核", "已提交", "审核中"):
            continue
        for item in _reports_of(row):
            name = _text(item, "工序名称")
            if manu_no and name and _number(item.get("报工数量")) > 0:
                reported.setdefault(manu_no, set()).add(name)
    reported_state = _reported_types(snapshot)
    dispatched = _dispatched(snapshot)

    out: list[Problem] = []
    for row in snapshot.get("MANU_ORDER") or []:
        manu_no = _text(row, "编号", "单据编号")
        status = _text(row, "单据状态")
        if not manu_no or status in ("已完工", "已关闭", "作废", "草稿"):
            continue
        started = _parse_date(_text(row, "开工日期", "预开工日"))
        if started is None:
            continue
        waited = (today - started).days
        # 门槛就是「从第几天开始报」，往上每翻一倍升一级
        if waited < rules.stall_days:
            continue
        done_processes: list[str] = []
        stuck: list[dict[str, Any]] = []
        for process in _processes_of(row):
            name = _text(process, "工序名称")
            if not name:
                continue
            if _is_done(process):
                done_processes.append(name)
                continue
            stuck.append(process)
        if not stuck:
            continue
        # 一道工序都没报过工，才是真停了；报过但没完工属于「落后」而不是「停滞」
        silent = [p for p in stuck if _text(p, "工序名称") not in reported.get(manu_no, set())]
        if not silent:
            continue
        names = "、".join(_text(p, "工序名称") for p in silent[:3])
        planned = sum(_number(p.get("计划数量")) for p in silent)
        # 表头常常没有产品名（在产品页签里），退到第一道工序的工序行上取
        product = _text(row, "产品名称", "产品")
        if not product:
            for process in _processes_of(row):
                if _text(process, "产品名称"):
                    product = _text(process, "产品名称")
                    break
        customer = _text(row, "客户", "生产订单客户")
        due = _text(row, "预完工日")
        progress = f"{len(done_processes)}/{len(done_processes) + len(stuck)}"

        # 分三种情况：汇报单没建 / 建了没数量 / 真有量但没完工
        # 判据要落在**第一道卡住工序**上，而不是「哪类情况存在」：
        # 按工序顺序，最前面那道才是真正把后面全堵住的那道
        states = reported_state.get(manu_no, {})
        blocking = _text(silent[0], "工序名称")
        blocking_state = states.get(blocking, "missing")

        # 工序是串行的：前面那道没报工，后面几道就排不上
        order = {_text(p, "工序名称"): index for index, p in enumerate(_processes_of(row))}
        first_block_at = min(
            (order.get(_text(p, "工序名称"), 999) for p in silent), default=999
        )
        blocked = [
            name
            for name, index in order.items()
            if index > first_block_at
            and name not in dispatched.get(manu_no, set())
            and name not in done_processes
        ]

        if blocking_state == "missing":
            action = f"先把{blocking}的工序汇报单建出来，否则后面几道工序排不上"
            steps = [
                f"{blocking}的工序汇报单还没建，不是没做——先建单",
                "汇报单建好后再排下一道工序",
                "车间如果已经做了，按实际数量补录",
            ]
            headline = f"生产加工单 {manu_no} 卡在{blocking}：汇报单还没建"
        elif blocking_state == "empty":
            action = f"{blocking}的汇报单建了但没填数量，补录后流程才能往下走"
            steps = [
                f"查{blocking}的汇报单为什么没有数量",
                "按实际报工数量补录并审核",
                "确认没有漏报的工序",
            ]
            headline = f"生产加工单 {manu_no} 的{blocking}汇报单没有数量"
        else:
            action = f"到车间确认{names}为什么没动，定下恢复时间"
            steps = [
                f"问车间：{names}是缺人、缺料还是设备问题",
                "确认恢复时间，超过 1 天就调整排产",
                "恢复后补报工，避免账实不符",
            ]
            headline = f"生产加工单 {manu_no} 的{names}停滞 {waited} 天未报工"

        detail_parts = [
            f"产品：{product or '未填'}" + (f"；客户：{customer}" if customer else ""),
            f"开工 {started.isoformat()}，已过 {waited} 天",
            f"停滞工序：{names}（计划 {planned:g} 件，无报工）",
            f"工序进度：{progress} 道已完成",
        ]
        if blocked:
            detail_parts.append(f"因此排不上：{'、'.join(blocked[:4])}（共 {len(blocked)} 道）")
        if due:
            detail_parts.append(f"预完工：{due}")

        impact_bits = [f"{names} 停滞 {waited} 天未报工"]
        if blocked:
            impact_bits.append(f"{len(blocked)} 道后续工序排不上")
        if due:
            impact_bits.append(f"预完工日 {due}")
        if planned:
            impact_bits.append(f"{planned:g} 件未产出")

        out.append(
            Problem(
                kind="process_stalled",
                dedupe_key=f"process_stalled:{manu_no}:{names}",
                title=headline,
                detail="\n".join(detail_parts),
                suggested_action=action,
                # 阶梯升级：1 天只是苗头，2 天要动手，4 天已经是事故
                severity=(
                    "critical" if waited >= rules.stall_days * 4
                    else "warning" if waited >= rules.stall_days * 2
                    else "info"
                ),
                doc_no=manu_no,
                targets=_owners(owners, "MANU_ORDER", today),
                impact="；".join(impact_bits),
                steps=steps,
            )
        )
    return out


def _quality_issues(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """某道工序不合格率偏高 → 先查这道工序，别等到成品检验才发现。"""
    rules = _rules()
    out: list[Problem] = []
    for row in snapshot.get("PROCESS_REPORT") or []:
        if _text(row, "单据状态") not in ("已审核", "已提交", "审核中"):
            continue
        manu_no = _text(row, "加工单号")
        doc_no = _text(row, "编号", "单据编号")
        for item in _reports_of(row):
            reported = _number(item.get("报工数量"))
            bad = _number(item.get("不合格数量"))
            if reported < rules.quality_min_sample or bad <= 0:
                continue
            rate = bad / reported
            if rate < rules.quality_rate:
                continue
            name = _text(item, "工序名称") or "未命名工序"
            product = _text(item, "产品名称")
            out.append(
                Problem(
                    kind="quality_rate",
                    dedupe_key=f"quality_rate:{manu_no}:{name}",
                    title=f"{name}工序不合格 {bad:g}/{reported:g} 件（{rate:.0%}）",
                    detail="\n".join([
                        f"加工单：{manu_no}" + (f"；产品：{product}" if product else ""),
                        f"汇报单：{doc_no}；工序：{name}",
                        f"报工 {reported:g} 件，合格 {_number(item.get('合格数量')):g} 件，不合格 {bad:g} 件",
                    ]),
                    suggested_action=f"查{name}工序的人、机、料，必要时停线复核",
                    severity=(
                        "critical" if rate >= rules.quality_critical_rate else "warning"
                    ),
                    doc_no=manu_no,
                    targets=["qc"] + _owners(owners, "MANU_ORDER", today)[:1],
                    impact=f"{bad:g} 件不合格，返工或报废都会拖交期",
                    steps=[
                        f"调{name}工序的检验记录，确认不良类型是否集中",
                        "确认这批要用还是返修，返修走返修单",
                        "同一工序连续出现就查设备或工装",
                    ],
                )
            )
    return out


def _rework_pending(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """有返修量挂在工序上 → 数字对不上之前先处理掉。"""
    out: list[Problem] = []
    for row in snapshot.get("MANU_ORDER") or []:
        manu_no = _text(row, "编号", "单据编号")
        status = _text(row, "单据状态")
        if not manu_no or status in ("已关闭", "作废", "草稿"):
            continue
        pending: list[str] = []
        total = 0.0
        for process in _processes_of(row):
            qty = _number(process.get("待返修数量-本序发现")) + _number(
                process.get("待返修数量-他序发现")
            )
            if qty > 0:
                pending.append(_text(process, "工序名称") or "未命名工序")
                total += qty
        if not pending:
            continue
        names = "、".join(pending[:3])
        out.append(
            Problem(
                kind="rework_pending",
                dedupe_key=f"rework_pending:{manu_no}",
                title=f"生产加工单 {manu_no} 有 {total:g} 件待返修（{names}）",
                detail="\n".join([
                    f"产品：{_text(row, '产品名称') or '未填'}；状态：{status}",
                    f"待返修工序：{names}，合计 {total:g} 件",
                ]),
                suggested_action="安排返修并跟踪合格数量，返修完再报工",
                severity="warning",
                doc_no=manu_no,
                targets=_owners(owners, "MANU_ORDER", today),
                impact=f"{total:g} 件成品卡在返修，不能算完工",
                steps=[
                    "确认返修责任：本序发现还是他序发现",
                    "开返修单并约定完成时间",
                    "返修合格后补报工，避免产量对不上",
                ],
            )
        )
    return out


def rank(problems: list[Problem]) -> list[Problem]:
    """按「该先处理哪件」排序：严重度 → 影响件数 → 单号。

    主动推送和今日计划都用这一个顺序，避免两个地方各排一套。
    """
    weight = {"critical": 0, "warning": 1, "info": 2}

    def key(problem: Problem) -> tuple[int, int, str]:
        return (
            weight.get(problem.severity, 3),
            0 if problem.impact else 1,
            problem.doc_no or "",
        )

    return sorted(problems, key=key)


def daily_plan(problems: list[Problem], *, limit: int = 3) -> dict[str, Any]:
    """今日计划：先干什么，后干什么。

    事件多的时候人会挑简单的做，这正是拖延的来源。这里按严重度排好，
    明确给出「今天必须处理的前 N 件」，其余归到「其余」里，不混在一起。
    """
    ordered = rank(problems)
    top = ordered[:limit]
    rest = ordered[limit:]
    return {
        "top": top,
        "rest": rest,
        "summary": _plan_summary(top, rest),
    }


def _plan_summary(top: list[Problem], rest: list[Problem]) -> str:
    if not top:
        return "今天没有发现需要干预的生产问题。"
    lines = [f"今天优先处理这 {len(top)} 件："]
    for index, problem in enumerate(top, start=1):
        lines.append(f"{index}. {problem.title}")
        if problem.impact:
            lines.append(f"   影响：{problem.impact}")
        for step_index, step in enumerate(problem.steps[:3], start=1):
            lines.append(f"   {step_index}) {step}")
    if rest:
        lines.append(f"其余 {len(rest)} 件排在后面，处理完上面再看。")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# 计划层：把一堆事件排成「今天先干什么」
# ---------------------------------------------------------------------------

# 「拖了多久算压着」由 rule_thresholds 统一管（MES_RULE_STALE_HOURS）


def _merge_same_doc(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一张单上的多个问题合成一条。

    「这张单待审批」+「这张单要排产」对经办人其实是同一件事：这张单卡住了。
    分成两条会让人来回切换，而且处理掉一条时另一条还在提醒。
    """
    buckets: dict[str, dict[str, Any]] = {}
    plain: list[dict[str, Any]] = []
    for event in ordered:
        doc = str(event.get("doc_no") or "").strip()
        if not doc:
            plain.append(event)
            continue
        entry = buckets.get(doc)
        if entry is None:
            buckets[doc] = {**event, "doc_count": 1, "doc_members": [event]}
            continue
        entry["doc_count"] += 1
        entry["doc_members"].append(event)
        if not entry.get("impact") and event.get("impact"):
            entry["impact"] = event["impact"]
        if not entry.get("steps") and event.get("steps"):
            entry["steps"] = event["steps"]
        entry["age_hours"] = max(
            float(entry.get("age_hours") or 0), float(event.get("age_hours") or 0)
        )
    merged = [*buckets.values(), *plain]
    for entry in merged:
        if entry.get("doc_count", 1) > 1:
            entry["title"] = f"单据 {entry.get('doc_no')}：{entry['doc_count']} 个问题卡在一起"
    return merged

def _merge_same_kind(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同种事件合成一条，保留最严重那条的处置步骤和影响面。"""
    buckets: dict[str, dict[str, Any]] = {}
    for event in ordered:
        kind = str(event.get("kind") or event.get("title") or "")
        entry = buckets.get(kind)
        if entry is None:
            buckets[kind] = {**event, "count": 1, "members": [event]}
            continue
        entry["count"] += 1
        entry["members"].append(event)
        # 保留影响面最完整的那条（通常有 steps/impact 的更有信息量）
        if not entry.get("impact") and event.get("impact"):
            entry["impact"] = event["impact"]
        if not entry.get("steps") and event.get("steps"):
            entry["steps"] = event["steps"]
    merged = list(buckets.values())
    for entry in merged:
        if entry["count"] > 1:
            suffix = entry.get("kind_label") or ""
            base = str(entry.get("title") or "")
            entry["title"] = (
                f"{entry['count']} {suffix}" if suffix else f"{entry['count']} 项同类：{base}"
            )
            entry["age_hours"] = max(float(m.get("age_hours") or 0) for m in entry["members"])
    return merged

def plan_day(
    events: list[dict[str, Any]],
    *,
    limit: int = 3,
    kind_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把事件列表压成一份能执行的今日计划。

    ``events`` 每项：``{kind, title, severity, age_hours, impact, steps, doc_no}``。

    排序不是简单的严重度优先：**拖了很久的中等问题，优先级要压过刚出现的
    严重问题**——后者还在窗口期内，前者已经在损失了。这就是
    「先做最该做的」而不是「先做最吓人的」。
    """
    rules = _rules()
    if not events:
        return {
            "top": [],
            "rest": [],
            "summary": "今天没有待处理的生产事件。",
            "counts": {"critical": 0, "warning": 0, "info": 0},
        }

    def score(event: dict[str, Any]) -> tuple[float, str]:
        severity = str(event.get("severity") or "info")
        base = {"critical": 300.0, "warning": 200.0, "info": 100.0}.get(severity, 0.0)
        age = float(event.get("age_hours") or 0.0)
        # 每拖一天加 60 分，封顶 180：再久也不该压过一个当天的 critical
        stale_hours = rules.stale_hours
        age_bonus = min(age / stale_hours * 60.0, 180.0)
        stale = 40.0 if age >= stale_hours else 0.0
        return (-(base + age_bonus + stale), str(event.get("title") or ""))

    ordered = sorted(events, key=score)
    # 同类事件合成一条：三张单都「未安排生产」是一个动作，不是三个动作。
    # 计划里堆三条同义句只会让真正的那件事被淹掉。
    ordered = _merge_same_doc(ordered)
    if kind_labels:
        for event in ordered:
            label = kind_labels.get(str(event.get("kind") or ""))
            if label:
                event["kind_label"] = label
    merged = _merge_same_kind(ordered)
    top = merged[:limit]
    rest = merged[limit:]
    counts = {"critical": 0, "warning": 0, "info": 0}
    for event in events:
        key = str(event.get("severity") or "info")
        if key in counts:
            counts[key] += 1

    # 「组」和「条」分开说：一组可能是好几条同类事件，
    # 混着说会让人对不上今日计划和画像里的数字
    lines = [f"今天优先处理这 {len(top)} 组（共 {len(events)} 条事件）："]
    for index, event in enumerate(top, start=1):
        lines.append(f"{index}. {event.get('title')}")
        impact = str(event.get("impact") or "").strip()
        if impact:
            lines.append(f"   影响：{impact}")
        steps = [str(step) for step in (event.get("steps") or []) if str(step).strip()]
        for step_index, step in enumerate(steps[:3], start=1):
            lines.append(f"   {step_index}) {step}")
        age = float(event.get("age_hours") or 0.0)
        if age >= rules.stale_hours:
            lines.append(f"   已经拖了 {age / 24:.0f} 天，今天不定就继续拖")
    if rest:
        lines.append(
            f"其余 {len(rest)} 组排在后面（{counts['critical']} 紧急 / {counts['warning']} 需处理）。"
        )
    return {
        "top": top,
        "rest": rest,
        "summary": "\n".join(lines),
        "counts": counts,
    }


def _delivery_risk(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """交期还来不来得及：按「时间过了多少 / 工序做完多少」比。

    这是计划岗每天真正要判断的事——不是「有没有逾期」，而是「照这个速度
    交期还保不保得住」。逾期时才报警已经太晚了。
    """
    out: list[Problem] = []
    for row in snapshot.get("MANU_ORDER") or []:
        manu_no = _text(row, "编号", "单据编号")
        status = _text(row, "单据状态")
        if not manu_no or status in ("已完工", "已关闭", "作废", "草稿"):
            continue
        due = _parse_date(_text(row, "预完工日"))
        if due is None:
            # 加工单常常不填预完工日，退到销售订单的预计交货日期：
            # 没有交期的工单没法判「来不来得及」，这条规则就该看最原始的那个交期
            so_no = _text(row, "销售订单号")
            for so in snapshot.get("SO_ORDER") or []:
                if so_no and _text(so, "单据编号") == so_no:
                    due = _parse_date(_text(so, "预计交货日期", "交货日期"))
                    break
        start = _parse_date(_text(row, "开工日期", "预开工日"))
        if due is None or start is None:
            continue
        processes = [p for p in _processes_of(row) if _text(p, "工序名称")]
        if not processes:
            continue
        total = len(processes)
        done = sum(1 for p in processes if _is_done(p))
        if done >= total:
            continue
        left_days = (due - today).days
        span = max(1, (due - start).days)
        elapsed = max(0, (today - start).days)
        expected = min(1.0, elapsed / span)
        actual = done / total
        behind = round((expected - actual) * total)

        product = _text(row, "产品名称", "产品")
        if not product:
            for process in processes:
                if _text(process, "产品名称"):
                    product = _text(process, "产品名称")
                    break

        if left_days < 0:
            out.append(
                Problem(
                    kind="delivery_risk",
                    dedupe_key=f"delivery_risk:{manu_no}:late",
                    title=f"生产加工单 {manu_no} 已过交期 {-left_days} 天仍未完工",
                    detail="\n".join([
                        f"产品：{product or '未填'}；交期：{due.isoformat()}",
                        f"工序进度：{done}/{total} 完成",
                        f"当前状态：{status}",
                    ]),
                    suggested_action="确认剩余工序要几天，给客户一个准信或改交期",
                    severity="critical",
                    doc_no=manu_no,
                    targets=_owners(owners, "MANU_ORDER", today),
                    impact=f"已经逾期 {-left_days} 天",
                    steps=[
                        "问车间剩余工序需要几天",
                        "算出卖货日期，同步给销售",
                        "确实赶不上就改交期，别让客户等空信",
                    ],
                )
            )
            continue

        if behind >= 1 and left_days <= max(3, total):
            out.append(
                Problem(
                    kind="delivery_risk",
                    dedupe_key=f"delivery_risk:{manu_no}:tight",
                    title=f"生产加工单 {manu_no} 按现进度赶不上 {due.isoformat()}",
                    detail="\n".join([
                        f"产品：{product or '未填'}；还剩 {left_days} 天",
                        f"工序进度：{done}/{total} 完成，按时间应到 {expected:.0%}，实际 {actual:.0%}",
                        f"落后约 {behind} 道工序",
                    ]),
                    suggested_action="把剩余工序并线或加人，今天定下能不能保住交期",
                    severity="warning",
                    doc_no=manu_no,
                    targets=_owners(owners, "MANU_ORDER", today),
                    impact=f"落后 {behind} 道工序，只剩 {left_days} 天",
                    steps=[
                        "确认剩余工序能不能并行",
                        "不能并行就加人加设备",
                        "今天就给销售一个明确答复",
                    ],
                )
            )
    return out


def _process_trends(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """跨单看同一道工序：反复不合格说明是设备或工艺问题，不是这一批料的问题。

    单看一张汇报单，某个工序不合格率 6% 也许只是运气；但三道不同的单都这样，
    就该去查工装和参数了。
    """
    rules = _rules()
    stats: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("PROCESS_REPORT") or []:
        if _text(row, "单据状态") not in ("已审核", "已提交", "审核中"):
            continue
        manu_no = _text(row, "加工单号")
        for item in _reports_of(row):
            name = _text(item, "工序名称")
            reported = _number(item.get("报工数量"))
            bad = _number(item.get("不合格数量"))
            if not name or reported <= 0:
                continue
            bucket = stats.setdefault(name, {"reported": 0.0, "bad": 0.0, "orders": set()})
            bucket["reported"] += reported
            bucket["bad"] += bad
            if manu_no:
                bucket["orders"].add(manu_no)

    out: list[Problem] = []
    for name, bucket in stats.items():
        reported = bucket["reported"]
        bad = bucket["bad"]
        orders = bucket["orders"]
        if reported < rules.trend_min_sample or len(orders) < rules.trend_min_orders or bad <= 0:
            continue
        rate = bad / reported
        if rate < rules.trend_rate:
            continue
        out.append(
            Problem(
                kind="process_trend",
                dedupe_key=f"process_trend:{name}",
                title=f"{name}工序跨 {len(orders)} 张单不合格率 {rate:.0%}（{bad:g}/{reported:g} 件）",
                detail="\n".join([
                    f"涉及加工单：{'、'.join(sorted(orders)[:5])}",
                    f"汇总：报工 {reported:g} 件，不合格 {bad:g} 件",
                    "多张单都偏高，通常不是来料问题，是这道工序本身",
                ]),
                suggested_action=f"查{name}工序的设备、工装与参数，别只挑单件返修",
                severity="critical" if rate >= rules.trend_rate * 2 else "warning",
                doc_no=None,
                panel_code="MANU_ORDER",
                targets=["qc"] + _owners(owners, "MANU_ORDER", today)[:1],
                impact=f"涉及 {len(orders)} 张单，返修和报废都在累积",
                steps=[
                    f"看{name}工序最近三批的检验记录，找共同点",
                    "查设备/工装是否需要保养或校准",
                    "必要时改工艺参数并小批验证",
                ],
            )
        )
    return out


def _material_mismatch(
    snapshot: dict[str, list[dict[str, Any]]],
    today: date,
    owners: dict[str, list[str]] | None,
) -> list[Problem]:
    """领料发不出：要的规格仓库里没有，或者有但在别的仓。

    这是「车间看起来没干」最隐蔽的一种原因——单子排了、人也到位了，
    领料单却发不出料。查半天现场，问题其实在 BOM 或仓库上。

    数据来自领料申请明细（材料编码、规格型号、仓库、数量）对库存结存。
    """
    stock: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("STOCK_STATUS") or []:
        code = _text(row, "存货编码")
        if code:
            stock.setdefault(code, []).append(row)

    status_of = {
        _text(row, "编号", "单据编号"): _text(row, "单据状态")
        for row in snapshot.get("MANU_ORDER") or []
    }

    # 同一物料+规格+仓库可能有多张领料单，合成一条，别一张单报一次
    grouped: dict[str, dict[str, Any]] = {}
    # 已经开过领料单的（工单, 物料, 规格），不要再从材料明细里算一遍
    covered: set[tuple[str, str, str]] = set()
    for row in snapshot.get("MATERIAL_REQ") or []:
        if _text(row, "单据状态") not in ("已审核", "已提交"):
            continue
        for item in _reports_of(row):
            code = _text(item, "材料编码")
            spec = _text(item, "规格型号")
            warehouse = _text(item, "仓库")
            manu_no = _text(item, "加工单号") or _text(row, "加工单号")
            need = _number(item.get("数量"))
            issued = _number(item.get("累计领料数量"))
            if not code or need <= 0 or issued >= need:
                continue
            # _stock_problem 返回的是元组，永远为真——必须看第一项，别写成 if not ...
            if _stock_problem(stock.get(code, []), spec, warehouse, need)[0] == "ok":
                continue
            covered.add((manu_no, code, spec))
            key = f"material_spec:{code}:{spec}:{warehouse}"
            bucket = grouped.setdefault(
                key,
                {"code": code, "spec": spec, "warehouse": warehouse, "need": 0.0,
                 "orders": [], "name": _text(item, "材料名称")},
            )
            bucket["need"] += need
            if manu_no and manu_no not in bucket["orders"]:
                bucket["orders"].append(manu_no)

    # 还没开领料单的工单：材料需求在「工单材料明细」里，倒冲领料尤其常见。
    # 不从这里补，这类单子缺料永远发现不了。
    for row in snapshot.get("MANU_ORDER_MATERIAL_DETAIL") or []:
        manu_no = _text(row, "单据编号")
        status = _text(row, "单据状态")
        if not manu_no or status in ("已完工", "已中止", "已关闭", "作废", "草稿"):
            continue
        code = _text(row, "材料编码")
        spec = _text(row, "规格型号")
        warehouse = _text(row, "预出仓库")
        need = _number(row.get("需用数量"))
        if not code or need <= 0 or (manu_no, code, spec) in covered:
            continue
        if _stock_problem(stock.get(code, []), spec, warehouse, need)[0] == "ok":
            continue
        key = f"material_spec:{code}:{spec}:{warehouse}"
        bucket = grouped.setdefault(
            key,
            {"code": code, "spec": spec, "warehouse": warehouse, "need": 0.0,
             "orders": [], "name": _text(row, "材料名称")},
        )
        bucket["need"] += need
        if manu_no not in bucket["orders"]:
            bucket["orders"].append(manu_no)

    out: list[Problem] = []
    for key, bucket in grouped.items():
        rows = stock.get(bucket["code"], [])
        kind, available = _stock_problem(rows, bucket["spec"], bucket["warehouse"], bucket["need"])
        orders = bucket["orders"]
        unfinished = [o for o in orders if status_of.get(o) not in ("已完工", "已关闭", "作废")]
        spec_list = "、".join(
            f"{_text(r, '规格型号') or '无规格'}@{_text(r, '仓库')}（{_number(r.get('数量')):g}）"
            for r in available[:3]
        )
        # 能救急的仓：只说「哪个仓有多少」，别再重复规格
        where = "、".join(
            f"{_text(r, '仓库')}（{_number(r.get('数量')):g}）" for r in available[:3]
        )

        if kind == "spec_missing":
            headline = f"要 {bucket['name']} {bucket['spec']}，库存里没有这个规格"
            action = f"确认 {bucket['name']} 是不是该用 {bucket['spec']}，再决定补料还是改 BOM"
            steps = [
                f"先确认 BOM：这张单真的要 {bucket['spec']} 吗",
                f"现有规格：{spec_list or '无'}，能不能替代",
                "确实要用就走采购或调拨，别让领料单一直挂着",
            ]
        else:
            headline = (
                f"要 {bucket['name']} {bucket['spec']}，{bucket['warehouse']}没有，货在别的仓"
            )
            action = f"从{where}调拨到{bucket['warehouse']}，领料就能发"
            steps = [
                f"{bucket['warehouse']}只有别的规格，先确认能不能调拨",
                f"可调拨的：{where or '无'}",
                "调拨单建好再发料，别先用后补",
            ]

        out.append(
            Problem(
                kind="material_spec",
                dedupe_key=key,
                title=headline,
                detail="\n".join([
                    f"物料：{bucket['code']} {bucket['name']}，规格 {bucket['spec']}，"
                    f"预出仓库 {bucket['warehouse']}",
                    f"需求合计 {bucket['need']:g}",
                    f"涉及加工单：{'、'.join(orders[:4])}",
                    f"库存里同物料共 {len(rows)} 行：{spec_list or '无'}",
                ]),
                suggested_action=action,
                # 未完工的工单现在就卡着；已完工的说明当初用了黑料 / BOM 有错
                severity="critical" if unfinished else "info",
                doc_no=orders[0] if orders else None,
                targets=_owners(owners, "MANU_ORDER", today)[:1] + ["keeper"],
                impact=(
                    f"{len(unfinished)} 张未完工工单领不到料"
                    if unfinished
                    else f"{len(orders)} 张已完工工单当初领不到料，账实可能不符"
                ),
                steps=steps,
            )
        )
    return out


def _stock_problem(
    rows: list[dict[str, Any]], spec: str, warehouse: str, need: float
) -> tuple[str, list[dict[str, Any]]]:
    """判断这条需求在库存里能不能满足，返回 (问题类型, 可参考的库存行)。

    类型：``ok`` / ``spec_missing``（没这个规格）/ ``wrong_warehouse``（规格有，仓不对）
    """
    same_spec = [r for r in rows if _text(r, "规格型号") == spec]
    if not same_spec:
        return ("spec_missing" if rows else "spec_missing", rows)
    in_warehouse = [
        r for r in same_spec
        if _text(r, "仓库") == warehouse and _number(r.get("数量")) + SPEC_EPSILON >= need
    ]
    if in_warehouse:
        return ("ok", same_spec)
    elsewhere = [r for r in same_spec if _number(r.get("数量")) + SPEC_EPSILON >= need]
    if elsewhere:
        return ("wrong_warehouse", elsewhere)
    return ("wrong_warehouse", same_spec)
