"""生产概况汇报：把「今天生产怎么样、有什么异常」做成可念的一段话。

与普通面板查询的区别：
- 它是**组合口径**（审批待办 / 超期工单 / 低库存 / 今日完工与报工），一句话回答要被问四五次的问题；
- 每一块都先过 MES 权限：没有该面板权限就跳过并在汇报里注明「你看不到 X」，不假装没数据；
- 数据只读，不改任何单据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from app.services.mes_client import MesClient, MesWatchError

LOW_STOCK_THRESHOLD = 500.0
DONE_STATUSES = {"已完工", "已完成", "已入库", "已审核完毕"}


@dataclass
class Briefing:
    lines: list[str] = field(default_factory=list)
    items: list[dict[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    ok: bool = True
    note: str = ""

    def speak(self) -> str:
        if self.note:
            return self.note
        if not self.items and not self.lines:
            return "今天没有查到需要汇报的生产情况。"
        head = "生产概况：" + "；".join(self.lines)
        if self.skipped:
            head += "。" + "、".join(self.skipped) + " 你没有权限，这部分跳过了"
        return head + "。"


def parse_qty(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


def overdue_orders(rows: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """超期未完工的加工单：预完工日早于今天，且状态不是完工。"""
    today = today or date.today()
    overdue = []
    for row in rows:
        status = str(row.get("单据状态") or "").strip()
        if status in DONE_STATUSES:
            continue
        due = parse_date(row.get("预完工日") or row.get("计划完工日") or row.get("交货日期"))
        if due and due < today:
            overdue.append(row)
    return overdue


def low_stock(rows: list[dict[str, Any]], threshold: float = LOW_STOCK_THRESHOLD) -> list[dict[str, Any]]:
    low = []
    for row in rows:
        quantity = parse_qty(row.get("现存量(主)") or row.get("现存量") or row.get("数量"))
        if quantity < threshold:
            low.append(row)
    return low


def pending_approvals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("单据状态") or "").strip() == "审批中"]


def build(
    *,
    fetch: Callable[[str], list[dict[str, Any]]],
    can: Callable[[str], bool],
    today: date | None = None,
) -> Briefing:
    """按权限逐块取数并拼成汇报。

    :param fetch: 面板编码 → 行（无权时由调用方返回空表并可记录跳过）
    :param can: 面板编码 → 是否有权
    """
    result = Briefing()
    today = today or date.today()

    def rows(panel: str, label: str) -> list[dict[str, Any]]:
        if not can(panel):
            result.skipped.append(label)
            return []
        try:
            return fetch(panel)
        except MesWatchError:
            result.ok = False
            result.skipped.append(f"{label}(查询失败)")
            return []

    orders = rows("MANU_ORDER", "生产加工单")
    overdue = overdue_orders(orders, today)
    if orders:
        doing = [r for r in orders if str(r.get("单据状态") or "") not in DONE_STATUSES]
        result.lines.append(f"加工单 {len(orders)} 张，在制 {len(doing)} 张")
        if overdue:
            result.lines.append(f"其中 {len(overdue)} 张已过预完工日")
            for row in overdue[:3]:
                result.items.append(
                    {
                        "title": str(row.get("单据编号") or row.get("编号") or ""),
                        "subtitle": f"预完工 {row.get('预完工日')}",
                        "status": "超期",
                        "extra": str(row.get("产品名称") or row.get("产品编码") or ""),
                    }
                )

    finishes = rows("FINISH_IN", "产成品入库单")
    today_finish = [
        r for r in finishes
        if parse_date(r.get("单据日期")) == today or str(r.get("单据日期") or "").startswith(today.isoformat())
    ]
    if finishes:
        result.lines.append(f"今日产成品入库 {len(today_finish)} 单")

    reports = rows("PROCESS_REPORT", "工序汇报单")
    today_reports = [
        r for r in reports
        if str(r.get("单据日期") or r.get("汇报日期") or "").startswith(today.isoformat())
    ]
    if reports:
        result.lines.append(f"今日工序汇报 {len(today_reports)} 单")

    stock = rows("STOCK_STATUS", "库存状况表")
    low = low_stock(stock)
    if stock:
        result.lines.append(f"低于 {int(LOW_STOCK_THRESHOLD)} 的物料 {len(low)} 项")
        for row in low[:3]:
            result.items.append(
                {
                    "title": str(row.get("存货编码") or row.get("存货") or ""),
                    "subtitle": str(row.get("仓库") or ""),
                    "status": "低库存",
                    "extra": str(row.get("现存量(主)") or row.get("现存量") or ""),
                }
            )

    approvals = rows("SO_ORDER", "销售订单")
    pending = pending_approvals(approvals)
    if approvals:
        result.lines.append(f"销售订单审批中 {len(pending)} 张")

    if not result.lines:
        result.ok = False
    return result


def for_user(user_id: str, *, today: date | None = None) -> "Briefing":
    """按某个人的 MES 权限生成汇报（主动推送与语音查询共用这一条路径）。

    权限拿不到时返回一条「需要绑定」的说明，而不是空汇报——空汇报会被误读成
    「今天一切正常」。
    """
    from app.services import mes_user_client

    try:
        client, _ = mes_user_client.client_for_user(user_id)
        access = mes_user_client.access_of(user_id, client)
    except mes_user_client.MesPermissionError as exc:
        return Briefing(lines=[], items=[], skipped=[], ok=False, note=str(exc))

    def can(panel: str) -> bool:
        return access.is_admin or panel in access.panels

    def fetch(panel: str) -> list[dict[str, Any]]:
        return client.query_panel(panel, page_size=100)

    return build(fetch=fetch, can=can, today=today)
