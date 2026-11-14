"""AI 助手的主动监控：读 MES → 判定待处理事实 → 派给对的人 → 群内通知。

设计原则：
- **只发现，不代劳**：不在 MES 里点审批、不改单据，动作仍由人在 MES 完成。
- **事实优先**：每条事件都能指回具体单据编号，不做猜测式提醒。
- **去重靠条件**：同一事实只留一条事件；条件消失（例如生产工单建好了）自动 resolved。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_token, now
from app.core.timeutil import as_utc
from app.models.assistant_watch import AssistantEvent, WatchScanRun
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMessage
from app.services import group_chat

logger = logging.getLogger(__name__)

# 面板 → 默认处理角色（MES 用户名）。可在 scan() 调用时覆盖。
DEFAULT_PANEL_OWNERS: dict[str, list[str]] = {
    "SO_ORDER": ["planner", "worker"],
    "MANU_ORDER": ["worker", "planner"],
    "STOCK_STATUS": ["keeper", "planner"],
    "PU_ORDER": ["keeper"],
    "ARRIVAL_IN": ["qc"],
    "FINISH_INSPECT": ["qc"],
}

# 临期判定窗口在 rule_thresholds（MES_RULE_DUE_SOON_DAYS）


@dataclass
class Candidate:
    """一条待处理事实。"""

    kind: str
    dedupe_key: str
    title: str
    detail: str
    suggested_action: str
    targets: list[str] = field(default_factory=list)
    severity: str = "info"
    panel_code: str | None = None
    doc_no: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # True=审批人来自 MES 开放接口（精确到人），解析不到账号就不要乱派
    exact: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "panel_code": self.panel_code,
            "doc_no": self.doc_no,
            **self.payload,
        }


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------


def evaluate_approvals(
    pending: list[tuple[str, dict[str, Any]]],
) -> list[Candidate]:
    """把「审批人 → 他的待审单据」翻译成事件，精确到人。

    ``pending`` 的每一项是 ``(approver_username, 单据元数据)``，数据来自 MES 的
    开放审批接口；这里不猜审批人，也不读单据正文。
    """
    out: list[Candidate] = []
    for approver, row in pending:
        panel = str(row.get("panelCode") or "").strip()
        form_no = str(row.get("formNo") or "").strip()
        if not approver or not form_no:
            continue
        panel_name = str(row.get("panelName") or panel)
        submitter = str(row.get("submitter") or "未知")
        submitted_at = str(row.get("submittedAt") or "")
        out.append(
            Candidate(
                kind="pending_approval",
                dedupe_key=f"pending_approval:{approver}:{panel}:{form_no}",
                title=f"{panel_name} {form_no} 待 {approver} 审批",
                detail=f"提交人：{submitter}" + (f"；提交时间：{submitted_at}" if submitted_at else ""),
                suggested_action="在 MES 打开该单据完成审批或退回",
                targets=[approver],
                severity="warning",
                panel_code=panel,
                doc_no=form_no,
                payload={
                    "approver": approver,
                    "submitter": submitter,
                    "panelName": panel_name,
                },
                exact=True,
            )
        )
    return out


def evaluate(
    snapshot: dict[str, list[dict[str, Any]]],
    *,
    today: date | None = None,
    panel_owners: dict[str, list[str]] | None = None,
) -> list[Candidate]:
    """把 MES 快照翻译成待处理事实。纯函数，方便单测。"""
    today = today or now().date()
    owners = panel_owners or DEFAULT_PANEL_OWNERS
    out: list[Candidate] = []

    so_rows = snapshot.get("SO_ORDER") or []
    manu_rows = snapshot.get("MANU_ORDER") or []
    covered = {
        _text(row, "销售订单号")
        for row in manu_rows
        if _text(row, "销售订单号")
    }

    # 规则 1：销售订单已审核，但还没有生产工单 → 计划/车间要安排生产
    for row in so_rows:
        doc_no = _text(row, "单据编号")
        if not doc_no or _text(row, "单据状态") != "已审核":
            continue
        # 审批还没过的单子先别催生产，否则会跟「待审批」提醒撞在一起
        if _text(row, "审批状态") in ("待审批", "待审核"):
            continue
        if doc_no in covered:
            continue
        customer = _text(row, "客户") or "未填客户"
        salesman = _text(row, "业务员")
        out.append(
            Candidate(
                kind="so_unstarted",
                dedupe_key=f"so_unstarted:{doc_no}",
                title=f"销售订单 {doc_no} 已审核，尚未安排生产",
                detail=f"客户：{customer}" + (f"；业务员：{salesman}" if salesman else ""),
                suggested_action="按这张销售订单生成生产工单并排产",
                targets=owners.get("SO_ORDER", ["planner"]),
                severity="warning",
                panel_code="SO_ORDER",
                doc_no=doc_no,
                payload={"customer": customer, "salesman": salesman},
            )
        )

    # 规则 2：处于待审批状态的单据 → 提醒对应审批角色
    # （配了 MES 开放审批接口就不用这条：那边能精确到人，见 evaluate_approvals）
    for panel, rows in ({} if settings.mes_open_api_enabled else snapshot).items():
        for row in rows:
            doc_no = _text(row, "单据编号")
            approval = _text(row, "审批状态", "审核状态")
            if not doc_no or approval not in ("待审批", "待审核"):
                continue
            out.append(
                Candidate(
                    kind="pending_approval",
                    dedupe_key=f"pending_approval:{panel}:{doc_no}",
                    title=f"{panel} {doc_no} 待你审批",
                    detail=f"当前审批状态：{approval}",
                    suggested_action="在 MES 里审批或退回，避免流程卡住",
                    targets=owners.get(panel, ["admin"]),
                    severity="warning",
                    panel_code=panel,
                    doc_no=doc_no,
                )
            )

    # 规则 3：销售订单还是草稿 → 提醒业务员补提交
    for row in so_rows:
        doc_no = _text(row, "单据编号")
        if not doc_no or _text(row, "单据状态") != "草稿":
            continue
        salesman = _text(row, "业务员")
        out.append(
            Candidate(
                kind="so_draft",
                dedupe_key=f"so_draft:{doc_no}",
                title=f"销售订单 {doc_no} 仍是草稿",
                detail=f"客户：{_text(row, '客户') or '未填'}",
                suggested_action="确认信息后提交审核，否则不会进入排产",
                targets=[salesman] if salesman else owners.get("SO_ORDER", ["planner"]),
                severity="info",
                panel_code="SO_ORDER",
                doc_no=doc_no,
                # 草稿是业务员自己的事；对不上账号就只记录、不乱派给计划员
                exact=bool(salesman),
            )
        )

    # 规则 4：生产工单临期未完工 → 车间盯进度
    from app.services.rule_thresholds import current as _rules

    due_limit = today + timedelta(days=_rules().due_soon_days)
    for row in manu_rows:
        doc_no = _text(row, "单据编号", "编号")
        status = _text(row, "单据状态")
        if not doc_no or status in ("已完工", "已关闭", "草稿"):
            continue
        due = _parse_date(_text(row, "预完工日", "完工日期"))
        if due is None:
            continue
        if due < today:
            severity, lead = "critical", f"已逾期 {today - due} 天"
        elif due <= due_limit:
            severity, lead = "warning", f"还有 {(due - today).days} 天到期"
        else:
            continue
        out.append(
            Candidate(
                kind="manu_due",
                dedupe_key=f"manu_due:{doc_no}:{due.isoformat()}",
                title=f"生产工单 {doc_no} {lead}",
                detail=f"状态：{status}；预完工：{due.isoformat()}",
                suggested_action="确认物料与工序进度，必要时调整排产",
                targets=owners.get("MANU_ORDER", ["worker"]),
                severity=severity,
                panel_code="MANU_ORDER",
                doc_no=doc_no,
                payload={"due": due.isoformat(), "status": status},
            )
        )

    # 生产执行层：单据状态看不出「哪道工序停了」，这一层补上
    out.extend(_production_problems(snapshot, owners, today))
    out.extend(_manu_missing_material(snapshot, owners, today))
    out.extend(_so_shipping_overdue(snapshot, owners, today))
    out.extend(_stock_low(snapshot, owners))
    out.extend(_purchase_followups(snapshot, owners, today))
    out.extend(_inspection_overdue(snapshot, owners, today))
    out.extend(_manu_without_report(snapshot, owners, today, report_rows=snapshot.get("PROCESS_REPORT") or []))

    # 把客户/供应商挂到事件上：跨单归并要用，但聚簇时不该再去查一遍 MES
    _attach_entities(out, snapshot)
    return out


def _attach_entities(candidates: list[Candidate], snapshot: dict[str, list[dict[str, Any]]]) -> None:
    """给事件补上「这张单属于哪个客户 / 哪个供应商」。

    只在这一处做映射，后面所有跨实体归并都读 payload，不用重复查接口。
    """
    so_customer = {
        _text(row, "单据编号"): _text(row, "客户")
        for row in snapshot.get("SO_ORDER") or []
        if _text(row, "单据编号")
    }
    manu_customer = {}
    for row in snapshot.get("MANU_ORDER") or []:
        no = _text(row, "编号", "单据编号")
        if no:
            manu_customer[no] = so_customer.get(_text(row, "销售订单号"), "")
    supplier_of = {}
    for panel in ("PU_ORDER", "ARRIVAL_IN"):
        for row in snapshot.get(panel) or []:
            no = _text(row, "单据编号")
            if no:
                supplier_of[no] = _text(row, "供应商")

    for candidate in candidates:
        if not candidate.doc_no:
            continue
        if "customer" not in candidate.payload:
            customer = so_customer.get(candidate.doc_no) or manu_customer.get(candidate.doc_no) or ""
            if customer:
                candidate.payload["customer"] = customer
        if "supplier" not in candidate.payload:
            supplier = supplier_of.get(candidate.doc_no) or ""
            if supplier:
                candidate.payload["supplier"] = supplier

    # 同一家公司既是客户又是供应商（买卖对开）：两端都挂着待办时，
    # 分开催会出现「一边催交期、一边承诺交期」，必须合起来看
    customers = {c.payload.get("customer") for c in candidates if c.payload.get("customer")}
    suppliers = {c.payload.get("supplier") for c in candidates if c.payload.get("supplier")}
    for name in customers & suppliers:
        for candidate in candidates:
            if candidate.payload.get("customer") == name or candidate.payload.get("supplier") == name:
                candidate.payload["dual_role"] = name



def _production_problems(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
) -> list[Candidate]:
    """工序级问题转成待处理事实。

    详情里带上影响面和处置步骤——「哪道工序停了」只是事实，
    「停几天、卡多少件、先做什么」才是能直接执行的东西。
    """
    from app.services import production_insight

    out: list[Candidate] = []
    for problem in production_insight.analyze(snapshot, today=today, owners=owners):
        detail_lines = [problem.detail]
        if problem.impact:
            detail_lines.append(f"影响：{problem.impact}")
        if problem.steps:
            detail_lines.append(
                "处置：" + "；".join(
                    f"{index}) {step}" for index, step in enumerate(problem.steps[:4], start=1)
                )
            )
        out.append(
            Candidate(
                kind=problem.kind,
                dedupe_key=problem.dedupe_key,
                title=problem.title,
                detail="\n".join(line for line in detail_lines if line),
                suggested_action=problem.suggested_action,
                targets=problem.targets or owners.get(problem.panel_code or "", ["worker"]),
                severity=problem.severity,
                panel_code=problem.panel_code,
                doc_no=problem.doc_no,
                payload=problem.as_payload(),
            )
        )
    return out

def _manu_missing_material(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
) -> list[Candidate]:
    """已开工的工单有领料申请但没实际出库 → 可能用"黑料"生产，账实不符。"""
    issued = {
        _text(row, "加工单号")
        for row in snapshot.get("MATERIAL_OUT") or []
        if _text(row, "单据状态") == "已审核"
    }
    requested = {
        _text(row, "加工单号")
        for row in snapshot.get("MATERIAL_REQ") or []
        if _text(row, "单据状态") == "已审核"
    }
    out: list[Candidate] = []
    for row in snapshot.get("MANU_ORDER") or []:
        doc_no = _text(row, "编号", "单据编号")
        status = _text(row, "单据状态")
        if not doc_no or status not in ("已审核", "生产中"):
            continue
        if doc_no not in requested or doc_no in issued:
            continue
        started = _parse_date(_text(row, "开工日期", "预开工日"))
        waiting = (today - started).days if started else 0
        if waiting < 2:
            continue
        out.append(
            Candidate(
                kind="manu_missing_material",
                dedupe_key=f"manu_missing_material:{doc_no}",
                title=f"生产工单 {doc_no} 已开工 {waiting} 天但材料未出库",
                detail=f"状态：{status}；开工：{started.isoformat() if started else '未填'}；"
                       f"已有领料申请但没出库记录",
                suggested_action="确认是否漏出库，否则材料账与实物会对不上",
                targets=owners.get("MANU_ORDER", ["keeper"]),
                severity="critical",
                panel_code="MANU_ORDER",
                doc_no=doc_no,
                payload={"waitingDays": waiting},
            )
        )
    return out


def _so_shipping_overdue(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
) -> list[Candidate]:
    """销售订单下单后 N 天仍未出库。MES 没有「交货日期」字段，只能按单据日期推算。"""
    days = max(1, int(settings.mes_watch_so_ship_days or 7))
    shipped = {
        _text(row, "销售订单号") or _text(row, "来源单号")
        for row in snapshot.get("SALE_OUT") or []
        if _text(row, "单据状态") == "已审核"
    }
    covered = {
        _text(row, "销售订单号")
        for row in snapshot.get("MANU_ORDER") or []
        if _text(row, "销售订单号")
    }
    out: list[Candidate] = []
    for row in snapshot.get("SO_ORDER") or []:
        doc_no = _text(row, "单据编号")
        if not doc_no or _text(row, "单据状态") != "已审核":
            continue
        if doc_no in shipped:
            continue
        # 还没排产的，由 so_unstarted 负责；这里只管「已排产但迟迟没出库」
        if doc_no not in covered:
            continue
        ordered = _parse_date(_text(row, "单据日期"))
        if ordered is None or ordered > today - timedelta(days=days):
            continue
        waiting = (today - ordered).days
        salesman = _text(row, "业务员")
        out.append(
            Candidate(
                kind="so_shipping_overdue",
                dedupe_key=f"so_shipping_overdue:{doc_no}",
                title=f"销售订单 {doc_no} 已 {waiting} 天仍未出库",
                detail=f"下单 {ordered.isoformat()}；客户：{_text(row, '客户') or '未填'}",
                suggested_action="确认是否已发货、是否需要补出库单",
                targets=[salesman] if salesman else owners.get("SO_ORDER", ["planner"]),
                severity="critical" if waiting >= days * 2 else "warning",
                panel_code="SO_ORDER",
                doc_no=doc_no,
                exact=bool(salesman),
                payload={"waitingDays": waiting},
            )
        )
    return out


def _threshold_map() -> dict[str, float]:
    raw = (settings.mes_watch_low_stock_items or "").strip()
    out: dict[str, float] = {}
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        code, _, value = chunk.partition(":")
        try:
            out[code.strip()] = float(value)
        except ValueError:
            continue
    return out


def _stock_low(
    snapshot: dict[str, list[dict[str, Any]]], owners: dict[str, list[str]]
) -> list[Candidate]:
    """库存低于阈值。MES 没有安全库存字段，阈值来自配置（全局 + 按物料覆盖）。"""
    default = float(settings.mes_watch_low_stock_threshold or 0)
    overrides = _threshold_map()
    if default <= 0 and not overrides:
        return []
    out: list[Candidate] = []
    for row in snapshot.get("STOCK_STATUS") or []:
        code = _text(row, "存货编码")
        if not code:
            continue
        limit = overrides.get(code, default)
        if limit <= 0:
            continue
        try:
            qty = float(row.get("数量") or 0)
        except (TypeError, ValueError):
            continue
        if qty > limit:
            continue
        warehouse = _text(row, "仓库")
        out.append(
            Candidate(
                kind="stock_low",
                dedupe_key=f"stock_low:{code}:{_text(row, '仓库编码')}",
                title=f"{_text(row, '存货', code)} 库存 {qty:g}，低于下限 {limit:g}",
                detail=f"仓库：{warehouse or '未填'}；存货编码：{code}",
                suggested_action="确认是否需要补采购或调拨",
                targets=owners.get("STOCK_STATUS", ["keeper"]),
                severity="warning",
                panel_code="STOCK_STATUS",
                doc_no=code,
                payload={"quantity": qty, "threshold": limit, "warehouse": warehouse},
            )
        )
    return out


def _purchase_followups(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
) -> list[Candidate]:
    """采购：草稿催提交；已审核未发货且交期临近催货。"""
    out: list[Candidate] = []
    lead = max(0, int(settings.mes_watch_purchase_lead_days or 3))
    for row in snapshot.get("PU_ORDER") or []:
        doc_no = _text(row, "单据编号", "编号")
        if not doc_no:
            continue
        status = _text(row, "单据状态")
        supplier = _text(row, "供应商") or "未填供应商"
        if status == "草稿":
            maker = _text(row, "制单人")
            out.append(
                Candidate(
                    kind="pu_draft",
                    dedupe_key=f"pu_draft:{doc_no}",
                    title=f"采购订单 {doc_no} 仍是草稿",
                    detail=f"供应商：{supplier}",
                    suggested_action="确认后提交审批，否则不会进入采购流程",
                    targets=[maker] if maker else owners.get("PU_ORDER", ["keeper"]),
                    severity="info",
                    panel_code="PU_ORDER",
                    doc_no=doc_no,
                    exact=bool(maker),
                )
            )
            continue
        if status != "已审核" or _text(row, "发货状态") != "未发货":
            continue
        due = _parse_date(_text(row, "交货日期"))
        if due is None or due > today + timedelta(days=lead):
            continue
        late = due < today
        out.append(
            Candidate(
                kind="pu_unshipped",
                dedupe_key=f"pu_unshipped:{doc_no}:{due.isoformat()}",
                title=f"采购订单 {doc_no} " + ("已过交期仍未发货" if late else "交期临近仍未发货"),
                detail=f"供应商：{supplier}；交货日期：{due.isoformat()}",
                suggested_action="联系供应商确认发货，必要时调整计划",
                targets=owners.get("PU_ORDER", ["keeper"]),
                severity="critical" if late else "warning",
                panel_code="PU_ORDER",
                doc_no=doc_no,
                payload={"due": due.isoformat()},
            )
        )
    return out


def _inspection_overdue(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
) -> list[Candidate]:
    """来料到货未检、以及完工报检迟迟没有结果。"""
    days = max(0, int(settings.mes_watch_inspect_overdue_days or 2))
    out: list[Candidate] = []

    for row in snapshot.get("ARRIVAL_IN") or []:
        doc_no = _text(row, "单据编号", "编号")
        if not doc_no or _text(row, "单据状态") != "已审核":
            continue
        if _text(row, "检验状态") != "未检":
            continue
        arrived = _parse_date(_text(row, "到货日期", "单据日期"))
        if arrived is None or arrived > today - timedelta(days=days):
            continue
        out.append(
            Candidate(
                kind="arrival_uninspected",
                dedupe_key=f"arrival_uninspected:{doc_no}",
                title=f"到货单 {doc_no} 已 {days} 天以上未检验",
                detail=f"到货日期：{arrived.isoformat()}；供应商：{_text(row, '供应商') or '未填'}",
                suggested_action="安排来料检验，避免影响入库和排产",
                targets=owners.get("ARRIVAL_IN", ["qc"]),
                severity="warning",
                panel_code="ARRIVAL_IN",
                doc_no=doc_no,
                payload={"arrivedAt": arrived.isoformat()},
            )
        )

    for row in snapshot.get("FINISH_INSPECT") or []:
        doc_no = _text(row, "单据编号", "编号")
        downstream = _text(row, "下游状态")
        if not doc_no or downstream in ("已审批", "已完成", "已检验"):
            continue
        submitted = _parse_date(_text(row, "单据日期", "提交时间"))
        if submitted is None or submitted > today - timedelta(days=days):
            continue
        out.append(
            Candidate(
                kind="finish_inspect_overdue",
                dedupe_key=f"finish_inspect_overdue:{doc_no}",
                title=f"完工报检 {doc_no} 超过 {days} 天没有检验结果",
                detail=f"单据日期：{submitted.isoformat()}；下游状态：{downstream or '未开始'}",
                suggested_action="安排完工检验，否则成品无法入库",
                targets=owners.get("FINISH_INSPECT", ["qc"]),
                severity="warning",
                panel_code="FINISH_INSPECT",
                doc_no=doc_no,
                payload={"submittedAt": submitted.isoformat()},
            )
        )
    return out


def _manu_without_report(
    snapshot: dict[str, list[dict[str, Any]]],
    owners: dict[str, list[str]],
    today: date,
    *,
    report_rows: list[dict[str, Any]],
) -> list[Candidate]:
    """已开工的工单连续 N 天没有报工。"""
    days = max(1, int(settings.mes_watch_no_report_days or 3))
    last_report: dict[str, date] = {}
    for row in report_rows:
        key = _text(row, "加工单号")
        reported = _parse_date(_text(row, "单据日期"))
        if not key or reported is None:
            continue
        if key not in last_report or reported > last_report[key]:
            last_report[key] = reported

    out: list[Candidate] = []
    for row in snapshot.get("MANU_ORDER") or []:
        doc_no = _text(row, "编号", "单据编号")
        status = _text(row, "单据状态")
        if not doc_no or status not in ("已审核", "生产中"):
            continue
        started = _parse_date(_text(row, "开工日期", "预开工日"))
        if started is None:
            continue
        reference = last_report.get(doc_no, started)
        if reference > today - timedelta(days=days):
            continue
        idle = (today - reference).days
        out.append(
            Candidate(
                kind="manu_no_report",
                dedupe_key=f"manu_no_report:{doc_no}:{reference.isoformat()}",
                title=f"生产工单 {doc_no} 已 {idle} 天没有报工",
                detail=f"状态：{status}；开工：{started.isoformat()}；最近报工：{last_report.get(doc_no, '从未')}",
                suggested_action="确认是否停产、待料或漏报工",
                targets=owners.get("MANU_ORDER", ["worker"]),
                severity="warning",
                panel_code="MANU_ORDER",
                doc_no=doc_no,
                payload={"idleDays": idle},
            )
        )
    return out


# ---------------------------------------------------------------------------
# 落地：事件 + 待办 + 群通知
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def _resolve_users(db: Session, usernames: list[str]) -> list[User]:
    wanted = [name.strip() for name in usernames if name and name.strip()]
    if not wanted:
        return []
    rows = db.scalars(select(User).where(User.username.in_(wanted))).all()
    by_name = {row.username: row for row in rows}
    found = [by_name[name] for name in wanted if name in by_name]
    if found:
        return found
    # 用户名没匹配上时，按显示名兜底（MES 的「业务员」存的是姓名）
    display = db.scalars(select(User).where(User.display_name.in_(wanted))).all()
    return list(display)


def ensure_feed_group(db: Session) -> ProjectGroup | None:
    """AI 调度台：主动通知落在这个群里，不存在就建一个。"""
    name = (settings.mes_watch_group_name or "").strip()
    if not name:
        return None
    existing = db.scalar(
        select(ProjectGroup).where(
            ProjectGroup.name == name, ProjectGroup.status == "active"
        )
    )
    if existing is not None:
        return existing
    admin = db.scalar(select(User).where(User.username == settings.bootstrap_username))
    if admin is None:
        admin = db.scalar(select(User).order_by(User.created_at))
    if admin is None:
        return None
    group = group_chat.create_group(
        db,
        owner=admin,
        name=name,
        description="AI 助手主动派发的 MES 待处理事项，自动维护，无需手工拉人。",
    )
    for user in db.scalars(select(User).where(User.username.in_(
        ["sales", "planner", "keeper", "worker", "qc"]
    ))):
        try:
            group_chat.add_member(
                db, actor=admin, group_id=group.id, user_id=user.id, role="member"
            )
        except group_chat.GroupChatError:
            continue
    logger.info("mes_watch.feed_group_created", extra={"group_id": group.id})
    return group


def scan(
    db: Session,
    *,
    client: Any | None = None,
    today: date | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """跑一轮：读 MES → 更新事件 → 派待办 → 群里通知。"""
    if client is None:
        from app.services.mes_client import MesClient

        client = MesClient()
    started = time.monotonic()
    run = WatchScanRun(
        id=_new_id("wrun"),
        trigger="manual",
        status="running",
        started_at=now(),
    )
    db.add(run)
    db.flush()
    snapshot = client.fetch_snapshot()
    candidates = evaluate(snapshot, today=today)

    # 审批待办：优先走 MES 开放接口（能精确到人）；接口未启用时退回面板字段推断
    approval_candidates: list[Candidate] = []
    if settings.mes_open_api_enabled:
        try:
            pairs: list[tuple[str, dict[str, Any]]] = []
            for approver in client.approvers():
                username = str(approver.get("username") or "").strip()
                if not username:
                    continue
                for row in client.pending_approvals(username):
                    pairs.append((username, row))
            approval_candidates = evaluate_approvals(pairs)
        except Exception as exc:  # noqa: BLE001 - 接口异常不应让整轮扫描失败
            logger.warning("mes_watch.approval_source_failed", extra={"error": str(exc)})
    if approval_candidates:
        # 有了精确数据，就不要再用面板字段猜审批人，避免同一单据两条事件
        candidates = [c for c in candidates if c.kind != "pending_approval"]
        candidates.extend(approval_candidates)

    stamp = now()
    quiet = _in_quiet_hours(stamp)
    current_keys = {item.dedupe_key for item in candidates}
    existing = {
        row.dedupe_key: row
        for row in db.scalars(
            select(AssistantEvent).where(
                AssistantEvent.dedupe_key.in_(current_keys | set())
            )
        )
    } if current_keys else {}

    # 条件消失的事件 → resolved
    resolved = 0
    for row in db.scalars(
        select(AssistantEvent).where(AssistantEvent.status.in_(("open", "handled")))
    ):
        if row.dedupe_key not in current_keys:
            row.status = "resolved"
            row.resolved_at = stamp
            row.updated_at = stamp
            resolved += 1
            _close_todos_for_event(db, row.id, stamp)

    created: list[AssistantEvent] = []
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.dedupe_key in existing:
            record = existing[candidate.dedupe_key]
            record.title = candidate.title
            record.detail = candidate.detail
            record.suggested_action = candidate.suggested_action
            # 影响面和处置步骤会随天数变化（停 2 天和停 4 天的建议不一样），
            # 不刷新就等于一直挂着第一天的旧方案
            record.payload_json = json.dumps(candidate.as_payload(), ensure_ascii=False)
            record.updated_at = stamp
            # 拖久了自动升级：最早发现是 info/warning，超过阈值提到 critical
            age_hours = (
                (stamp - _aware(record.first_seen_at)).total_seconds() / 3600.0
                if record.first_seen_at
                else 0.0
            )
            escalated = age_hours >= float(settings.mes_watch_escalate_hours or 24)
            record.severity = "critical" if escalated else candidate.severity
            if escalated and record.original_severity in ("info", "warning"):
                record.original_severity = candidate.severity
            if record.status == "resolved":
                # 关掉又回来：这不是新问题，是同一件事复发。
                # 记一次复发，并在标题上标出来——反复出现说明根因没动，
                # 只当成"又发现一条"会让人每天重复处理同一件事。
                record.status = "open"
                record.resolved_at = None
                record.recurrence = int(record.recurrence or 0) + 1
                record.last_reopened_at = stamp
                if record.recurrence >= 2:
                    candidate.title = f"{candidate.title}（第 {record.recurrence + 1} 次复发）"
                    record.title = candidate.title
                # 严重度不在这里改：上面刚按「拖了多久」判过升级，
                # 这里再赋一次会把 critical 打回 warning
            # 目标人可能之前没匹配上（比如业务员姓名改了账号），每次扫描重算一次，
            # 补上待办，避免事件一直悬着没人管。
            users = _resolve_users(db, candidate.targets)
            if not users and candidate.panel_code and not candidate.exact:
                users = _resolve_users(
                    db, DEFAULT_PANEL_OWNERS.get(candidate.panel_code, [])
                )
            if users:
                record.target_usernames_json = json.dumps(
                    [user.username for user in users], ensure_ascii=False
                )
                record.target_user_ids_json = json.dumps(
                    [user.id for user in users], ensure_ascii=False
                )
                record.notifications = max(record.notifications, len(users))
                if notify:
                    for user in users:
                        bucket = grouped.setdefault(
                            (user.id, candidate.kind), {"user": user, "events": []}
                        )
                        bucket["events"].append(record)
            continue

        users = _resolve_users(db, candidate.targets)
        if not users and candidate.panel_code and not candidate.exact:
            # 指定的人对不上账号（比如业务员姓名和账号不同），退回该面板的默认处理角色
            users = _resolve_users(
                db, DEFAULT_PANEL_OWNERS.get(candidate.panel_code, [])
            )
            if users:
                candidate.targets = [user.username for user in users]
        record = AssistantEvent(
            id=_new_id("aev"),
            dedupe_key=candidate.dedupe_key,
            kind=candidate.kind,
            panel_code=candidate.panel_code,
            doc_no=candidate.doc_no,
            title=candidate.title,
            detail=candidate.detail,
            suggested_action=candidate.suggested_action,
            severity=candidate.severity,
            status="open",
            target_usernames_json=json.dumps(candidate.targets, ensure_ascii=False),
            target_user_ids_json=json.dumps([u.id for u in users], ensure_ascii=False),
            payload_json=json.dumps(candidate.as_payload(), ensure_ascii=False),
            notifications=len(users),
            reminders=0,
            last_notified_at=stamp if users else None,
            first_seen_at=stamp,
            original_severity=candidate.severity,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(record)
        created.append(record)

        if users:
            for user in users:
                bucket = grouped.setdefault(
                    (user.id, candidate.kind), {"user": user, "events": []}
                )
                bucket["events"].append(record)

    if notify and grouped and not quiet:
        _deliver_grouped(db, grouped=grouped, stamp=stamp)

    db.flush()

    if notify and created and not quiet:
        _announce(db, created=created)

    # 反复提醒：还开着、派过但没人处理、到点了就再喊一次
    reminded = 0
    if notify and not quiet:
        fresh_ids = {item.id for item in created}
        reminded = _send_reminders(db, stamp=stamp, skip_ids=fresh_ids)

    summary = {
        "evaluated": len(candidates),
        "created": len(created),
        "resolved": resolved,
        "open": len(candidates),
        "reminded": reminded,
        "quiet": 1 if quiet else 0,
    }
    _finish_run(db, run, summary, snapshot=snapshot, started=started)
    return summary


KIND_LABELS = {
    "so_unstarted": "销售订单未排产",
    "so_draft": "销售订单草稿",
    "so_shipping_overdue": "销售订单超期未出库",
    "manu_due": "生产工单临期",
    "manu_no_report": "生产工单无报工",
    "stock_low": "库存低于下限",
    "pu_draft": "采购订单草稿",
    "pu_unshipped": "采购订单未发货",
    "arrival_uninspected": "到货未检验",
    "finish_inspect_overdue": "完工报检无结果",
    "pending_approval": "待审批",
    "manu_missing_material": "生产工单材料未出库",
}

KIND_SUMMARY = {
    "so_unstarted": "张销售订单待安排生产",
    "so_draft": "张销售订单仍是草稿",
    "pending_approval": "张单据待你审批",
    "manu_due": "个生产工单临期",
    "stock_low": "项存货低于下限",
    "pu_draft": "张采购订单仍是草稿",
    "pu_unshipped": "张采购订单未发货",
    "arrival_uninspected": "张到货单待检验",
    "finish_inspect_overdue": "张完工报检没有结果",
    "manu_no_report": "个生产工单没有报工",
    "manu_missing_material": "个生产工单材料未出库",
    "process_stalled": "道工序停滞未报工",
    "quality_rate": "道工序不合格率偏高",
    "rework_pending": "个生产工单有返修待处理",
}


def _finish_run(
    db: Session,
    run: WatchScanRun,
    summary: dict[str, Any],
    *,
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
    started: float | None = None,
    error: str = "",
) -> None:
    """补齐本轮巡查的运行记录。"""
    run.finished_at = now()
    run.duration_ms = int(((time.monotonic() - started) if started else 0) * 1000)
    run.panels_fetched = len(snapshot or {})
    run.events_evaluated = summary.get("evaluated", 0)
    run.events_created = summary.get("created", 0)
    run.events_resolved = summary.get("resolved", 0)
    run.reminders_sent = summary.get("reminded", 0)
    run.status = "failed" if error else "ok"
    run.error = error[:1000]
    db.flush()


def record_failed_run(db: Session, *, error: str, started: float | None = None,
                      trigger: str = "timer") -> WatchScanRun:
    """扫描整体失败时也要留痕，否则图表上会像「这段时间没数据」。"""
    run = WatchScanRun(
        id=_new_id("wrun"),
        trigger=trigger,
        status="failed",
        started_at=now(),
    )
    db.add(run)
    db.flush()
    _finish_run(db, run, {}, started=started, error=error)
    return run


def scan_health(db: Session, *, hours: int = 24) -> dict[str, Any]:
    """巡查健康度：最近跑了多少轮、成功率、平均耗时、距上次多久。"""
    since = now() - timedelta(hours=max(1, hours))
    rows = list(
        db.scalars(
            select(WatchScanRun)
            .where(WatchScanRun.started_at >= since)
            .order_by(WatchScanRun.started_at.desc())
        )
    )
    if not rows:
        return {
            "hours": hours,
            "runs": 0,
            "ok": 0,
            "failed": 0,
            "success_rate": None,
            "avg_duration_ms": None,
            "last_run_at": None,
            "last_ok_at": None,
            "seconds_since_last": None,
            "interval_seconds": float(settings.mes_watch_interval_seconds or 0),
            "stale": True,
            "last_error": "",
        }

    ok = [r for r in rows if r.status == "ok"]
    failed = [r for r in rows if r.status == "failed"]
    durations = [r.duration_ms for r in ok if r.duration_ms]
    last = rows[0]
    last_ok = ok[0] if ok else None
    seconds_since = (
        (now() - _aware(last.started_at)).total_seconds() if last.started_at else None
    )
    interval = float(settings.mes_watch_interval_seconds or 0)
    # 超过 2 个周期没跑，就认为巡查停了
    stale = bool(seconds_since is not None and interval and seconds_since > interval * 2)

    return {
        "hours": hours,
        "runs": len(rows),
        "ok": len(ok),
        "failed": len(failed),
        "success_rate": round(len(ok) / len(rows), 3) if rows else None,
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "last_run_at": (as_utc(last.started_at).isoformat() if last.started_at else None),
        "last_ok_at": (as_utc(last_ok.started_at).isoformat() if last_ok else None),
        "seconds_since_last": round(seconds_since) if seconds_since is not None else None,
        "interval_seconds": interval,
        "stale": stale,
        "last_error": (last.error or "") if last.status == "failed" else "",
    }


def response_metrics(db: Session, *, days: int = 7) -> dict[str, Any]:
    """响应时效：发现→认领、发现→关闭各花了多久。"""
    since = now() - timedelta(days=max(1, days))
    rows = list(
        db.scalars(
            select(AssistantEvent).where(
                AssistantEvent.first_seen_at >= since,
                AssistantEvent.archived_at.is_(None),
            )
        )
    )
    ack_hours: list[float] = []
    close_hours: list[float] = []
    over_24 = 0
    for row in rows:
        discovered = _aware(row.first_seen_at or row.created_at)
        if row.acked_by_user_id:
            # 认领时间没有单独字段，用 updated_at 近似（认领时会刷新）
            acked = _aware(row.updated_at)
            if acked >= discovered:
                ack_hours.append((acked - discovered).total_seconds() / 3600.0)
        if row.status == "resolved" and row.resolved_at:
            close_hours.append(
                (_aware(row.resolved_at) - discovered).total_seconds() / 3600.0
            )
        if row.status == "open" and (now() - discovered).total_seconds() > 86400:
            over_24 += 1

    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    def pct(values: list[float], limit: float) -> float | None:
        if not values:
            return None
        return round(sum(1 for v in values if v <= limit) / len(values), 2)

    return {
        "days": days,
        "events": len(rows),
        "acked": len(ack_hours),
        "closed": len(close_hours),
        "avg_ack_hours": avg(ack_hours),
        "avg_close_hours": avg(close_hours),
        "ack_within_4h": pct(ack_hours, 4),
        "ack_within_24h": pct(ack_hours, 24),
        "open_over_24h": over_24,
    }


def _aware(value: datetime | None) -> datetime:
    """SQLite 读回的时间没有时区，统一按 UTC 处理。"""
    if value is None:
        return now()
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _in_quiet_hours(stamp: datetime) -> bool:
    """配置成 "8-18" 时，只在这两个整点之间通知。空值=全天可通知。"""
    raw = (settings.mes_watch_quiet_hours or "").strip()
    if not raw or "-" not in raw:
        return False
    try:
        start_text, end_text = raw.split("-", 1)
        start, end = int(start_text), int(end_text)
    except ValueError:
        return False
    # 按本地时区判断：配置写的是"几点到几点"，人的直觉是本地时间
    hour = _aware(stamp).astimezone().hour
    if start >= 24 or end >= 24:
        # 24 表示到当天结束，"0-24" 即全天静默
        return start <= hour < 24
    if start <= end:
        return not (start <= hour < end)
    # 跨夜，例如 22-6
    return not (hour >= start or hour < end)


def _send_reminders(db: Session, *, stamp, skip_ids: set[str] | None = None) -> int:
    """未处理的事件按间隔重发，最多 N 次。间隔设 0 表示每轮都提醒（测试用）。"""
    raw_hours = settings.mes_watch_remind_hours
    every = float(4.0 if raw_hours is None else raw_hours) * 3600
    limit = max(0, int(settings.mes_watch_max_reminders or 3))
    if limit == 0:
        return 0
    rows = list(
        db.scalars(
            select(AssistantEvent).where(
                # 只有「未处理」才催；handled 表示人已经认领，不该再打扰
                AssistantEvent.status == "open",
                AssistantEvent.reminders < limit,
            )
        )
    )
    skip = skip_ids or set()
    due: list[AssistantEvent] = []
    for row in rows:
        if row.id in skip:      # 本轮刚建的事件不重复催
            continue
        last = _aware(row.last_notified_at or row.first_seen_at)
        if (stamp - last).total_seconds() >= every:
            due.append(row)
    if not due:
        return 0

    group = ensure_feed_group(db)
    if group is None:
        return 0
    # 按人汇总：一个人一行，别在群里反复 @ 同一个人
    by_person: dict[str, list[AssistantEvent]] = {}
    for row in due:
        for username in json.loads(row.target_usernames_json or "[]"):
            by_person.setdefault(username, []).append(row)
        row.reminders += 1
        row.last_notified_at = stamp
        row.updated_at = stamp
        row.notifications += 1
        _touch_todo(db, row.id, stamp)

    lines = ["这些还没处理，再提醒一次："]
    for username, items in by_person.items():
        if len(items) == 1:
            lines.append(f"- @{username}：{items[0].title}")
        else:
            docs = "、".join(item.doc_no for item in items if item.doc_no)
            lines.append(f"- @{username}：{len(items)} 件事未处理（{docs}）")
    # 没人认领的事件单独列出来
    unassigned = [row for row in due if not json.loads(row.target_usernames_json or "[]")]
    for row in unassigned:
        lines.append(f"- {row.title}（暂未指定负责人）")
    group_chat.post_message(
        db, group=group, sender=None, sender_kind="ai", kind="ai",
        content="\n".join(lines),
        parts=[{"type": "assistant_reminder", "count": len(due)}],
    )
    db.flush()
    return len(due)


def _touch_todo(db: Session, event_id: str, stamp) -> None:
    """提醒时刷新对应待办的时间，让它浮到列表前面。"""
    for todo in db.scalars(
        select(GroupTodo).where(
            GroupTodo.status.in_(("pending", "accepted", "doing")),
            GroupTodo.assistant_event_ids_json.contains(event_id),
        )
    ):
        todo.updated_at = stamp


def _summary_title(kind: str, items: list[AssistantEvent]) -> str:
    if len(items) == 1:
        return items[0].title
    suffix = KIND_SUMMARY.get(kind)
    if suffix:
        return f"{len(items)} {suffix}"
    return f"{len(items)} 项待处理：{items[0].title}"


def _deliver_grouped(
    db: Session,
    *,
    grouped: dict[tuple[str, str], dict[str, Any]],
    stamp,
) -> None:
    """同一个人 + 同一类事情合成一条待办，避免一次扫描给某人刷出 N 条。"""
    group = ensure_feed_group(db)
    for (user_id, kind), bucket in grouped.items():
        items: list[AssistantEvent] = bucket["events"]
        user: User = bucket["user"]
        title = _summary_title(kind, items)
        exists = db.scalar(
            select(GroupTodo).where(
                GroupTodo.assignee_user_id == user_id,
                GroupTodo.title == title[:255],
                GroupTodo.status.in_(("pending", "accepted", "doing")),
            )
        )
        if exists is not None:
            merged = set(json.loads(exists.assistant_event_ids_json or "[]"))
            merged.update(item.id for item in items)
            exists.assistant_event_ids_json = json.dumps(sorted(merged), ensure_ascii=False)
            continue
        details = _detail_of(items).split("\n") if items else []
        db.add(
            GroupTodo(
                id=_new_id("gtodo"),
                group_id=group.id if group is not None else None,
                assignee_user_id=user_id,
                origin="ai",
                title=title[:255],
                refs_json=_refs_of(items),
                detail="\n".join(details)[:4000],
                priority=(
                    "urgent" if any(i.severity == "critical" for i in items)
                    else "high" if any(i.severity == "warning" for i in items)
                    else "normal"
                ),
                status="pending",
                assistant_event_ids_json=json.dumps([item.id for item in items], ensure_ascii=False),
                created_at=stamp,
                updated_at=stamp,
            )
        )
    db.flush()


def _close_todos_for_event(db: Session, event_id: str, stamp) -> None:
    """业务状态变了，就把这条事件从相关待办里摘掉。

    汇总待办可能关联多张单据：只处理掉其中一张时，待办要按剩余单据重算标题和明细，
    全部处理完才真正关闭——否则一张单处理完会把另外几张一起关掉。
    """
    for todo in db.scalars(
        select(GroupTodo).where(
            GroupTodo.status.in_(("pending", "accepted", "doing")),
            GroupTodo.assistant_event_ids_json.contains(event_id),
        )
    ):
        ids = [item for item in json.loads(todo.assistant_event_ids_json or "[]") if item != event_id]
        if ids:
            events = list(db.scalars(select(AssistantEvent).where(AssistantEvent.id.in_(ids))))
            if events:
                todo.assistant_event_ids_json = json.dumps(ids, ensure_ascii=False)
                todo.title = _summary_title(events[0].kind, events)[:255]
                todo.detail = _detail_of(events)
                todo.updated_at = stamp
                continue
        todo.status = "done"
        todo.completed_at = stamp
        todo.updated_at = stamp
        note = "（MES 状态已变化，AI 自动关闭）"
        if note not in todo.detail:
            todo.detail = (todo.detail + "\n" + note).strip()
    db.flush()


def _refs_of(items: list[AssistantEvent]) -> str:
    """把待办关联的 MES 单据记下来，前端据此生成跳转链接。"""
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not item.doc_no:
            continue
        panel = item.panel_code or ""
        key = (panel, item.doc_no)
        if key in seen:
            continue
        seen.add(key)
        try:
            payload = json.loads(item.payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        refs.append(
            {
                "panel": panel,
                "docNo": item.doc_no,
                "label": str(payload.get("panelName") or panel or ""),
            }
        )
    return json.dumps(refs[:20], ensure_ascii=False)


def _detail_of(items: list[AssistantEvent]) -> str:
    lines: list[str] = []
    seen_actions: set[str] = set()
    for item in items:
        # 标题里已经有单号时不要在后面再补一遍
        needs_doc = item.doc_no and item.doc_no not in item.title
        doc = f"（{item.doc_no}）" if needs_doc else ""
        lines.append(f"· {item.title}{doc}")
        # 工序级问题带着影响面和处置步骤，直接排进待办，人不用再点开 MES 才看得懂
        try:
            payload = json.loads(item.payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        impact = str(payload.get("impact") or "").strip()
        if impact:
            lines.append(f"  影响：{impact}")
        steps = [str(step) for step in (payload.get("steps") or []) if str(step).strip()]
        if steps:
            lines.append("  处置：" + "；".join(f"{i}) {step}" for i, step in enumerate(steps[:4], 1)))
        action = (item.suggested_action or "").strip()
        # 同一批单据的建议往往一模一样，重复三遍只让人以为有三条不同的建议
        if action and action not in seen_actions:
            seen_actions.add(action)
            lines.append(f"  建议：{action}")
    return "\n".join(lines)[:4000]


def _deliver(db: Session, *, record: AssistantEvent, users: list[User], stamp) -> None:
    """给每个目标人建一条待办，并挂到 AI 调度台群。"""
    group = ensure_feed_group(db)
    for user in users:
        exists = db.scalar(
            select(GroupTodo).where(
                GroupTodo.assignee_user_id == user.id,
                GroupTodo.title == record.title,
                GroupTodo.status.in_(("pending", "accepted", "doing")),
            )
        )
        if exists is not None:
            continue
        todo = GroupTodo(
            id=_new_id("gtodo"),
            group_id=group.id if group is not None else None,
            assignee_user_id=user.id,
            origin="ai",
            title=record.title[:255],
            refs_json=_refs_of([record]),
            detail=(record.detail + f"\n建议：{record.suggested_action}").strip(),
            priority="high" if record.severity in ("warning", "critical") else "normal",
            status="pending",
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(todo)
        try:
            from app.services import inapp_notifications

            inapp_notifications.push(
                db,
                user_id=user.id,
                kind="todo_assigned",
                title=todo.title,
                body=record.detail,
                ref_type="todo",
                ref_id=todo.id,
                group_id=todo.group_id,
                actor_name="小缔",
                dedupe_key=f"todo_assigned:{todo.id}",
            )
        except Exception:  # noqa: BLE001 - 弹窗失败不能拖垮扫描
            logger.exception("inapp push failed for %s", todo.id)
    db.flush()


def _announce(db: Session, *, created: list[AssistantEvent]) -> None:
    group = ensure_feed_group(db)
    if group is None:
        return
    lines = ["我在 MES 里发现了这些待办："]
    # 一个人一行：同一人的不同类别在行内分段，避免同一个人反复出现
    by_person: dict[str, list[AssistantEvent]] = {}
    for record in created:
        for username in json.loads(record.target_usernames_json or "[]"):
            by_person.setdefault(username, []).append(record)
    for username, records in by_person.items():
        by_kind: dict[str, list[AssistantEvent]] = {}
        for record in records:
            by_kind.setdefault(record.kind, []).append(record)
        segments: list[str] = []
        for kind, items in by_kind.items():
            docs = "、".join(item.doc_no for item in items if item.doc_no)
            if len(items) == 1:
                segments.append(items[0].title)
            else:
                suffix = KIND_SUMMARY.get(kind, "项待处理")
                segments.append(f"{len(items)} {suffix}（{docs}）")
        lines.append(f"- @{username}：" + "；".join(segments))
    group_chat.post_message(
        db,
        group=group,
        sender=None,
        sender_kind="ai",
        kind="ai",
        content="\n".join(lines),
        parts=[
            {
                "type": "assistant_watch",
                "events": [
                    {"id": item.id, "kind": item.kind, "severity": item.severity}
                    for item in created
                ],
            }
        ],
    )


# ---------------------------------------------------------------------------
# 查询与确认
# ---------------------------------------------------------------------------


# 认领人筛选项
ACK_FILTERS = ("all", "any", "none", "me", "others")


def list_events(
    db: Session,
    *,
    user: User,
    status: str = "open",
    limit: int = 50,
    acked: str = "any",
    assignee: str = "",
) -> list[AssistantEvent]:
    """列事件。

    ``acked``：认领状态筛选 ——
      * ``any``    不筛（默认）
      * ``none``   只看还没人认领的
      * ``me``     只看我认领的
      * ``others`` 只看别人认领的
      * ``all``    同 any，保留兼容
    ``assignee``：按「派给谁」筛，传用户名；``me`` 表示只看派给我的。
    """
    stmt = select(AssistantEvent).where(AssistantEvent.archived_at.is_(None))
    if status and status != "all":
        stmt = stmt.where(AssistantEvent.status == status)

    mode = (acked or "any").lower()
    if mode == "none":
        stmt = stmt.where(AssistantEvent.acked_by_user_id.is_(None))
    elif mode == "me":
        stmt = stmt.where(AssistantEvent.acked_by_user_id == user.id)
    elif mode == "others":
        stmt = stmt.where(
            AssistantEvent.acked_by_user_id.is_not(None),
            AssistantEvent.acked_by_user_id != user.id,
        )

    # 可见范围：管理员看全部；普通成员只看派给自己的
    rows = list(db.scalars(stmt.order_by(AssistantEvent.created_at.desc())))
    visible: list[AssistantEvent] = []
    for row in rows:
        targets = json.loads(row.target_user_ids_json or "[]")
        if user.role == "admin" or user.id in targets:
            visible.append(row)

    wanted = (assignee or "").strip()
    if wanted:
        if wanted == "me":
            visible = [
                row for row in visible if user.id in json.loads(row.target_user_ids_json or "[]")
            ]
        else:
            usernames_by_id = _username_map(db, visible)
            visible = [
                row
                for row in visible
                if wanted
                in {
                    usernames_by_id.get(uid, "")
                    for uid in json.loads(row.target_user_ids_json or "[]")
                }
            ]

    return visible[: max(1, min(limit, 200))]


def _username_map(db: Session, events: list[AssistantEvent]) -> dict[str, str]:
    ids = {
        uid
        for row in events
        for uid in json.loads(row.target_user_ids_json or "[]")
        if uid
    }
    if not ids:
        return {}
    rows = db.scalars(select(User).where(User.id.in_(ids)))
    return {user.id: user.username for user in rows}


def ack_facets(
    db: Session, *, user: User, status: str = "open", acked: str = "any"
) -> dict[str, Any]:
    """筛选面板需要的计数。

    ``by_assignee`` 按**当前认领筛选**统计，这样点了「未认领」后
    按人计数和列表条数一致，不会出现「显示 5 条实际出来 4 条」。
    """
    rows = list_events(db, user=user, status=status, limit=200)
    scoped = list_events(db, user=user, status=status, limit=200, acked=acked)
    usernames = _username_map(db, rows)
    by_assignee: dict[str, int] = {}
    mine = none = others = 0
    for row in rows:
        if row.acked_by_user_id is None:
            none += 1
        elif row.acked_by_user_id == user.id:
            mine += 1
        else:
            others += 1
    for row in scoped:
        for uid in json.loads(row.target_user_ids_json or "[]"):
            name = usernames.get(uid)
            if name:
                by_assignee[name] = by_assignee.get(name, 0) + 1
    return {
        "total": len(rows),
        "unacked": none,
        "acked_by_me": mine,
        "acked_by_others": others,
        "by_assignee": dict(sorted(by_assignee.items(), key=lambda kv: -kv[1])),
    }


def ack_event(
    db: Session, *, event: AssistantEvent, user: User, note: str = ""
) -> AssistantEvent:
    """认领：标记 handled，可留一句处理说明。"""
    event.status = "handled"
    event.acked_by_user_id = user.id
    text = (note or "").strip()[:1000]
    if text:
        event.note = text
    event.updated_at = now()
    db.flush()
    return event


def reopen_event(db: Session, *, event: AssistantEvent, user: User) -> AssistantEvent:
    """撤回认领：条件还在就重新打开，避免误点后没人管。"""
    event.status = "open"
    event.acked_by_user_id = None
    event.updated_at = now()
    db.flush()
    return event


# ---------------------------------------------------------------------------
# 3. 归档清理
# ---------------------------------------------------------------------------


def archive_old_events(db: Session, *, older_than_days: int | None = None) -> int:
    """把早已处理完的事件挪进归档，只保留最近 N 天供统计。

    不删数据（可能还要回溯），只打 archived_at 标记；统计和列表默认排除归档。
    """
    days = int(
        older_than_days
        if older_than_days is not None
        else (settings.mes_watch_archive_days or 30)
    )
    if days <= 0:
        return 0
    cutoff = now() - timedelta(days=days)
    rows = list(
        db.scalars(
            select(AssistantEvent).where(
                AssistantEvent.archived_at.is_(None),
                AssistantEvent.status.in_(("resolved", "handled")),
            )
        )
    )
    archived = 0
    for row in rows:
        anchor = _aware(row.resolved_at or row.updated_at or row.created_at)
        if anchor < cutoff:
            row.archived_at = now()
            archived += 1
    db.flush()
    return archived


# ---------------------------------------------------------------------------
# 4. 周期汇总
# ---------------------------------------------------------------------------


def build_digest(db: Session, *, user: User, days: int = 1) -> dict[str, Any]:
    """一段时间内的进展汇总：处理了多少、还剩多少、按人/类型拆开。"""
    since = now() - timedelta(days=max(1, days))
    events = list(db.scalars(select(AssistantEvent)))
    in_window = [e for e in events if _aware(e.first_seen_at or e.created_at) >= since]
    created = len(in_window)
    resolved = sum(1 for e in in_window if _aware(e.resolved_at or e.updated_at) >= since
                   and e.status in ("resolved", "handled"))
    open_now = [e for e in events if e.status == "open" and e.archived_at is None]
    stale = sum(1 for e in open_now if (now() - _aware(e.first_seen_at or e.created_at)).days >= 1)

    by_kind: dict[str, int] = {}
    for event in in_window:
        by_kind[event.kind] = by_kind.get(event.kind, 0) + 1

    # 按人统计「还压着多少」
    by_person: dict[str, int] = {}
    for event in open_now:
        for username in json.loads(event.target_usernames_json or "[]"):
            by_person[username] = by_person.get(username, 0) + 1

    return {
        "days": days,
        "created": created,
        "resolved": resolved,
        "open": len(open_now),
        "stale": stale,
        "by_kind": by_kind,
        "by_person": dict(sorted(by_person.items(), key=lambda kv: -kv[1])),
    }


def post_digest(db: Session, *, days: int = 1, force: bool = False) -> str | None:
    """把汇总发到 AI 调度台群里；没有内容就不发。

    ``force=False`` 时同一天只发一次，避免手动重复触发刷屏。
    """
    group = ensure_feed_group(db)
    if group is None:
        return None
    if not force:
        today_start = now() - timedelta(days=1)
        recent = db.scalar(
            select(ProjectGroupMessage.id).where(
                ProjectGroupMessage.group_id == group.id,
                ProjectGroupMessage.content.like("%MES 待办汇总%"),
                ProjectGroupMessage.created_at >= today_start,
                ProjectGroupMessage.deleted_at.is_(None),
            )
        )
        if recent is not None:
            return None
    admin = db.scalar(select(User).where(User.username == settings.bootstrap_username))
    if admin is None:
        return None
    digest = build_digest(db, user=admin, days=days)
    if not (digest["created"] or digest["open"]):
        return None

    label = "今天" if days <= 1 else f"最近 {days} 天"
    lines = [f"{label}的 MES 待办汇总："]
    lines.append(
        f"- 新发现 {digest['created']} 条，处理 {digest['resolved']} 条，"
        f"还剩 {digest['open']} 条未处理"
        + (f"（其中 {digest['stale']} 条已超过 1 天）" if digest["stale"] else "")
    )
    if digest["by_person"]:
        detail = "、".join(f"@{k} {v} 条" for k, v in list(digest["by_person"].items())[:8])
        lines.append(f"- 待处理分布：{detail}")
    if digest["by_kind"]:
        kinds = "、".join(f"{KIND_LABELS.get(k, k)} {v}" for k, v in
                          sorted(digest["by_kind"].items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"- 主要类型：{kinds}")
    content = "\n".join(lines)
    group_chat.post_message(
        db, group=group, sender=None, sender_kind="ai", kind="ai",
        content=content, parts=[{"type": "assistant_digest", "days": days}],
    )
    db.flush()
    return content


def today_plan(db: Session, *, user: User, limit: int = 3) -> dict[str, Any]:
    """今天先干什么：把派给我的未处理事件排成一份可执行的计划。

    排序逻辑在 ``production_insight.plan_day``：拖久的中等问题会压过
    刚出现的严重问题——已经在损失的事情，比还来得及的事情更该先动。
    """
    from app.services import production_insight

    rows = [
        row
        for row in list_events(db, user=user, status="open", limit=200)
        if _targets_user(row, user)
    ]
    events: list[dict[str, Any]] = []
    stamp = now()
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        seen = _aware(row.first_seen_at or row.created_at or stamp) or stamp
        events.append(
            {
                "event_id": row.id,
                "kind": row.kind,
                "title": row.title,
                # 用「刚发现时的严重度」排序：拖久了会被扫描抬成 critical，
                # 全抬成 critical 之后排序就退化成「谁更老」，等于没有优先级
                "severity": row.original_severity or row.severity,
                "age_hours": max(0.0, (stamp - seen).total_seconds() / 3600.0),
                "impact": str(payload.get("impact") or ""),
                "steps": [str(step) for step in (payload.get("steps") or []) if str(step).strip()],
                "doc_no": row.doc_no or "",
                "suggested_action": row.suggested_action or "",
            }
        )
    plan = production_insight.plan_day(events, limit=limit, kind_labels=KIND_SUMMARY)
    plan["total"] = len(events)
    return plan


def _targets_user(row: AssistantEvent, user: User) -> bool:
    """派给我的、或者还没指定人的，都算我的；派给别人就不掺和。"""
    try:
        names = json.loads(row.target_usernames_json or "[]")
    except (TypeError, ValueError):
        names = []
    if not names:
        return True
    return user.username in names


def post_morning_plan(db: Session) -> dict[str, Any]:
    """早间主动推送：给每个人推他自己今天的计划，群里发一条总览。

    为什么按人推：同一个「3 张单据待审批」，对审批人是今天的头等事，
    对车间就是噪音。计划必须落到人头上，否则等于群发公告。
    """
    from app.services import inapp_notifications, production_briefing, production_insight

    stamp = now()
    users = list(db.scalars(select(User)))
    pushed = 0
    overall: list[dict[str, Any]] = []
    for user in users:
        plan = today_plan(db, user=user)
        # 生产概况按这个人自己的 MES 权限取数：报表里不会出现他看不到的板块
        try:
            briefing = production_briefing.for_user(user.id)
            # 只有真的取到数才写进推送：没绑定 MES 的人不该在每条待办前面看到一句
            # 「请先去绑定」，那是干扰；绑定提示改由界面上的入口承担
            briefing_text = briefing.speak() if briefing.ok else ""
        except Exception:  # noqa: BLE001 - 汇报失败不能挡住待办推送
            logger.exception("morning briefing failed", extra={"user_id": user.id})
            briefing_text = ""
        if not plan["top"] and not briefing_text:
            continue
        if plan["top"]:
            overall.extend(plan["top"])
        summary = (briefing_text + "\n\n" + plan["summary"]) if briefing_text else plan["summary"]
        inapp_notifications.push(
            db,
            user_id=user.id,
            kind="notice",
            title=(
                f"早间汇报：{len(plan['top'])} 件待处理"
                if plan["top"]
                else "早间汇报：生产概况"
            ),
            body=summary[:1500],
            ref_type="plan",
            ref_id=stamp.date().isoformat(),
            # 一天一条，重复调用不会叠加
            dedupe_key=f"daily_plan:{stamp.date().isoformat()}",
        )
        pushed += 1

    group = ensure_feed_group(db)
    posted = ""
    # 重启会重置内存里「今天推过没」的标记，所以群里这条要按库里查一次，
    # 否则同一天重启几次就有几条计划
    already = None
    if group is not None:
        already = db.scalar(
            select(ProjectGroupMessage.id).where(
                ProjectGroupMessage.group_id == group.id,
                ProjectGroupMessage.content.like("%今天要处理的事%"),
                ProjectGroupMessage.created_at >= stamp - timedelta(hours=20),
                ProjectGroupMessage.deleted_at.is_(None),
            )
        )
    if group is not None and overall and already is None:
        merged = production_insight.plan_day(
            [{**item, "kind": item.get("kind", "")} for item in overall],
            limit=5,
            kind_labels=KIND_SUMMARY,
        )
        posted = merged["summary"]
        db.add(
            ProjectGroupMessage(
                id=_new_id("gmsg"),
                group_id=group.id,
                sender_user_id=None,
                sender_kind="ai",
                kind="ai",
                content="今天要处理的事（按优先级）：\n\n" + posted,
                created_at=stamp,
            )
        )
    db.flush()
    return {"pushed": pushed, "posted": bool(posted)}
