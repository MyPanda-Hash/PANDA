"""一线员工看板：只回答「我手上还有什么要干」。

与车间主任现场看板的区别：
- 现场看板是**全车间**的在制与卡点（管理视角，要铺开）；
- 这里只挑**还没报工**的派工工序（执行视角，要能一眼看完、点进去就干）。

数据现实（2026-11-17 实测）：DISPATCH 表头没有「班组/工人」字段，
PROCESS_REPORT 的工人名称也普遍为空。所以这里不假装能精确到人：
- 能按姓名匹配到的报工算「我的产出」；
- 匹配不到就明确标注「未登记工人」，而不是把别人的产量算到你头上。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.services.production_briefing import parse_date
from app.services import mes_user_client

MAX_LIST = 8
MAX_DAYS_BACK = 7


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    detail = row.get("detail")
    items = detail.get("items") if isinstance(detail, dict) else None
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def _key(mo_no: str, process: str) -> str:
    return f"{mo_no.strip()}|{process.strip()}"


def build_mine(dispatches: list[dict[str, Any]], reports: list[dict[str, Any]],
               *, real_name: str = "", today: date | None = None) -> dict[str, Any]:
    """把派工与汇报折成「我的活」。纯函数，便于单测。"""
    today = today or date.today()
    since = today - timedelta(days=MAX_DAYS_BACK)

    # 已经报过的「工单|工序」：派工里这些不再算待办
    done_keys: dict[str, float] = {}
    for row in reports:
        mo_no = str(row.get("加工单号") or "")
        for it in _rows_of(row):
            key = _key(str(it.get("加工单号") or mo_no), str(it.get("工序名称") or ""))
            done_keys[key] = done_keys.get(key, 0.0) + _num(it.get("报工数量"))

    pending: list[dict[str, Any]] = []
    for row in dispatches:
        if str(row.get("单据状态") or "").strip() != "已审核":
            continue
        mo_no = str(row.get("加工单号") or row.get("编号") or "")
        for it in _rows_of(row):
            process = str(it.get("工序名称") or "")
            qty = _num(it.get("派工数量"))
            key = _key(str(it.get("加工单号") or mo_no), process)
            reported = done_keys.get(key, 0.0)
            # 派工量已报满就不出现
            if reported >= qty and qty > 0:
                continue
            pending.append({
                "docNo": str(row.get("编号") or row.get("单据编号") or ""),
                "mo": str(it.get("加工单号") or mo_no),
                "process": process,
                "product": str(row.get("产品名称") or ""),
                "workshop": str(row.get("生产车间") or ""),
                "qty": qty,
                "reported": reported,
                "dueDate": str(row.get("预完工日") or ""),
            })

    # 我自己这段时间报了多少：只认「工人名称 == 我」。
    # 没填工人的不能算成我的产出——那正是「产量算不到人头上」的病灶，
    # 计入反而掩盖问题；单独列出来让人看见。
    mine_reports: list[dict[str, Any]] = []
    unassigned_reports: list[dict[str, Any]] = []
    for row in reports:
        doc_date = parse_date(row.get("单据日期"))
        if not doc_date or doc_date < since:
            continue
        for it in _rows_of(row):
            worker = str(it.get("工人名称") or "").strip()
            item = {
                "docNo": str(row.get("单据编号") or ""),
                "process": str(it.get("工序名称") or ""),
                "qty": _num(it.get("报工数量")),
                "worker": worker,
                "day": doc_date.isoformat(),
                "mo": str(it.get("加工单号") or row.get("加工单号") or ""),
            }
            if worker and real_name and worker == real_name:
                mine_reports.append(item)
            elif not worker:
                unassigned_reports.append(item)

    today_qty = round(sum(r["qty"] for r in mine_reports if r["day"] == today.isoformat()), 2)
    week_qty = round(sum(r["qty"] for r in mine_reports), 2)

    bits = []
    if pending:
        bits.append(f"还有 {len(pending)} 道工序等你报工")
    else:
        bits.append("手上的派工都报完了")
    if today_qty:
        bits.append(f"今天已报 {today_qty} 件")
    if unassigned_reports:
        bits.append(f"另有 {len(unassigned_reports)} 条报工没填工人，产量算不到人头上")

    return {
        "day": today.isoformat(),
        "headline": "；".join(bits) + "。",
        "stats": [
            {"key": "pending", "label": "待报工工序", "value": str(len(pending)),
             "tone": "warn" if pending else "ok", "hint": "已派工、未报满"},
            {"key": "todayQty", "label": "今日已报", "value": f"{today_qty:g}",
             "tone": "ok" if today_qty else "muted", "hint": "件"},
            {"key": "weekQty", "label": f"{MAX_DAYS_BACK} 天累计", "value": f"{week_qty:g}",
             "tone": "muted", "hint": "件"},
            {"key": "unassigned", "label": "未登记工人", "value": str(len(unassigned_reports)),
             "tone": "warn" if unassigned_reports else "ok", "hint": "报工没填工人，产量算不到人"},
        ],
        "pending": pending[:MAX_LIST],
        "reports": mine_reports[:MAX_LIST],
        "unassignedReports": unassigned_reports[:MAX_LIST],
        "hidden": max(0, len(pending) - MAX_LIST),
        "unassigned": len(unassigned_reports),
    }


def for_user(user_id: str, *, real_name: str = "", today: date | None = None) -> dict[str, Any]:
    try:
        client, _ = mes_user_client.client_for_user(user_id)
    except mes_user_client.MesPermissionError as exc:
        return {"ok": False, "note": str(exc), "day": (today or date.today()).isoformat(),
                "headline": str(exc), "stats": [], "pending": [], "reports": [],
                "unassignedReports": [], "hidden": 0, "unassigned": 0}
    dispatches = client.query_panel("DISPATCH", page_size=200)
    reports = client.query_panel("PROCESS_REPORT", page_size=200)
    board = build_mine(dispatches, reports, real_name=real_name, today=today)
    board["ok"] = True
    board["generatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return board
