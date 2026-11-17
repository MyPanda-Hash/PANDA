"""车间现场看板：车间主任打开就该看到「今天现场什么情况、哪里卡住」。

与管理者简报的区别：
- 简报回答「要我定什么」（决策，一天几条）；
- 现场看板回答「现在产线怎么样」（进度，要铺开看在制工单与报工）。

取数全部来自 MES 面板：加工单（在制/逾期/齐套）、工序汇报（今日报工）。
不新增表，也不在 AI 侧复制业务规则 —— 只做聚合与排序。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.production_briefing import parse_date
from app.services import mes_user_client

IN_PROGRESS = ("已审核", "生产中")
STALL_DAYS = 2          # 开工超过几天还没报工，算「停滞」
MAX_LIST = 6


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _detail_of(row: dict[str, Any]) -> dict[str, Any]:
    """面板接口把明细放在 detail 里，不是顶层。"""
    detail = row.get("detail")
    return detail if isinstance(detail, dict) else {}


def _product_of(mo_row: dict[str, Any]) -> dict[str, Any]:
    """工单的第一个产品行：一张加工单通常只做一个产品。"""
    products = _detail_of(mo_row).get("products")
    if isinstance(products, list) and products and isinstance(products[0], dict):
        return products[0]
    return {}


def build_floor(mos: list[dict[str, Any]], reports: list[dict[str, Any]],
                today: date | None = None) -> dict[str, Any]:
    """把工单与报工折成现场看板。纯函数，便于单测。"""
    today = today or date.today()
    in_progress: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    stalled: list[dict[str, Any]] = []

    for row in mos:
        status = str(row.get("单据状态") or "").strip()
        if status not in IN_PROGRESS:
            continue
        product = _product_of(row)
        plan = _num(product.get("数量"))
        done = _num(product.get("累计汇报套数(工序单位)"))
        due = parse_date(row.get("预完工日"))
        start = parse_date(row.get("开工日期")) or parse_date(row.get("单据日期"))
        overdue_days = (today - due).days if due and due < today else 0
        idle_days = (today - start).days if start and start < today else 0
        item = {
            "docNo": str(row.get("单据编号") or row.get("编号") or ""),
            "product": str(product.get("产品名称") or product.get("产品编码") or ""),
            "workshop": str(row.get("生产车间") or ""),
            "planQty": plan,
            "doneQty": done,
            "progress": round(done / plan * 100, 1) if plan > 0 else 0.0,
            "dueDate": str(row.get("预完工日") or ""),
            "overdueDays": overdue_days,
            "idleDays": idle_days,
            "status": status,
        }
        in_progress.append(item)
        if overdue_days > 0:
            overdue.append(item)
        # 停滞：开工超过阈值天，一件都没报
        if done <= 0 and idle_days >= STALL_DAYS:
            stalled.append(item)

    # 进度低的排前面：主任最该先看没动的
    in_progress.sort(key=lambda x: (x["progress"], -x["overdueDays"]))

    today_reports: list[dict[str, Any]] = []
    for row in reports:
        doc_date = parse_date(row.get("单据日期"))
        if doc_date != today:
            continue
        items = _detail_of(row).get("items")
        rows = items if isinstance(items, list) else []
        if not rows:
            today_reports.append({
                "docNo": str(row.get("单据编号") or ""),
                "process": str(row.get("工序名称") or ""),
                "qty": 0.0,
                "worker": str(row.get("工人名称") or ""),
                "mo": str(row.get("加工单号") or ""),
            })
            continue
        for it in rows:
            if not isinstance(it, dict):
                continue
            today_reports.append({
                "docNo": str(row.get("单据编号") or ""),
                "process": str(it.get("工序名称") or ""),
                "qty": _num(it.get("报工数量")),
                "worker": str(it.get("工人名称") or ""),
                "mo": str(it.get("加工单号") or row.get("加工单号") or ""),
            })

    total_reported = round(sum(r["qty"] for r in today_reports), 2)

    headline_bits = [f"{len(in_progress)} 张工单在制"]
    if today_reports:
        headline_bits.append(f"今天已报 {len(today_reports)} 道工序、合计 {total_reported} 件")
    else:
        headline_bits.append("今天还没有报工")
    if overdue:
        headline_bits.append(f"{len(overdue)} 张已过预完工日")
    if stalled:
        headline_bits.append(f"{len(stalled)} 张开工 {STALL_DAYS} 天以上仍未报工")

    return {
        "day": today.isoformat(),
        "headline": "；".join(headline_bits) + "。",
        "stats": [
            {"key": "inProgress", "label": "在制工单", "value": str(len(in_progress)),
             "tone": "ok" if in_progress else "muted", "hint": "已审核 / 生产中"},
            {"key": "reports", "label": "今日报工工序", "value": str(len(today_reports)),
             "tone": "ok" if today_reports else "muted", "hint": f"合计 {total_reported} 件"},
            {"key": "stalled", "label": "停滞工单", "value": str(len(stalled)),
             "tone": "warn" if stalled else "ok", "hint": f"开工 ≥{STALL_DAYS} 天未报工"},
            {"key": "overdue", "label": "逾期未完工", "value": str(len(overdue)),
             "tone": "warn" if overdue else "ok", "hint": "已过预完工日"},
        ],
        "orders": in_progress[:MAX_LIST],
        "reports": today_reports[:MAX_LIST],
        "stalled": stalled[:MAX_LIST],
        # 逾期单独给一份：界面上「卡住了」和「逾期了」是两件事，各有各的处理动作
        "overdue": overdue[:MAX_LIST],
        "hiddenOrders": max(0, len(in_progress) - MAX_LIST),
    }


def for_user(user_id: str, *, today: date | None = None) -> dict[str, Any]:
    """按当前账号的 MES 权限取数；拿不到权限时给出说明而不是空看板。"""
    try:
        client, _ = mes_user_client.client_for_user(user_id)
    except mes_user_client.MesPermissionError as exc:
        return {"ok": False, "note": str(exc), "day": (today or date.today()).isoformat(),
                "headline": str(exc), "stats": [], "orders": [], "reports": [], "stalled": [],
                "hiddenOrders": 0}
    mos = client.query_panel("MANU_ORDER", page_size=200)
    reports = client.query_panel("PROCESS_REPORT", page_size=200)
    board = build_floor(mos, reports, today)
    board["ok"] = True
    board["generatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return board
