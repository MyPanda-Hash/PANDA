"""简报看板：把「今天有什么要管理者知道、要管理者定」做成结构化数据。

与 production_briefing 的区别：
- 那个产出一段可念的话（给语音、给通知）；
- 这个产出带层级的结构（结论 / 四个数字 / 三条待拍板），给工作台直接渲染。

两者共用同一批数据源（待办管线 + MES 指标），不各自查询一遍。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services import group_assistant, mes_user_client, period_reports

logger = logging.getLogger(__name__)

# 需要管理者拍板的优先级：只有这两档算「要他定」，普通待办是执行层的事
DECISION_PRIORITIES = ("urgent", "high")
DECISION_STATUSES = ("pending", "accepted", "doing")
MAX_DECISIONS = 3

PRIORITY_LABEL = {"urgent": "紧急", "high": "高", "normal": "普通", "low": "低"}

# 待办 refs 里的面板 → 工作台可跳转的面板路径
PANEL_PATH = {
    "SO_ORDER": "/panelx/list/SO_ORDER",
    "PU_ORDER": "/panelx/list/PU_ORDER",
    "MANU_ORDER": "/panelx/list/MANU_ORDER",
    "PU_INVOICE": "/panelx/list/PU_INVOICE",
    "SALE_INV": "/panelx/list/SALE_INV",
    "SALE_OUT": "/panelx/list/SALE_OUT",
}


def _rate_text(metric: dict[str, Any] | None) -> str:
    """按期率的展示文案：没样本时说「暂无样本」，不说 0%（0% 会被读成全都不按期）。"""
    if not metric or not metric.get("hasData"):
        return "暂无样本"
    return f"{metric.get('rate')}%"


def _rate_hint(metric: dict[str, Any] | None) -> str:
    if not metric or not metric.get("hasData"):
        return "还没有已交付/已完工的单据"
    return f"{metric.get('onTime')}/{metric.get('total')} 单按期"


def _first_ref(todo: Any) -> dict[str, Any] | None:
    try:
        refs = json.loads(getattr(todo, "refs_json", "") or "[]")
    except ValueError:
        return None
    for ref in refs if isinstance(refs, list) else []:
        if isinstance(ref, dict) and ref.get("panel"):
            return ref
    return None


def _decision_of(todo: Any, index: int) -> dict[str, Any]:
    ref = _first_ref(todo)
    panel = (ref or {}).get("panel") or ""
    doc_no = (ref or {}).get("docNo") or ""
    label = (ref or {}).get("label") or ""
    path = PANEL_PATH.get(panel)
    title = getattr(todo, "title", "") or ""
    advice = (getattr(todo, "detail", "") or "").strip().split("\n")[0]
    # 待办 detail 的第一行常常就是标题本身（还带「· 」这类前缀），
    # 去前缀后若与标题重合就整行不显示 —— 重复一遍只占地方，不传递信息。
    normalized = advice.lstrip("·-•* 　").strip()
    if normalized and title and (
        normalized == title
        or title.startswith(normalized[:20])
        or normalized.startswith(title[:20])
    ):
        advice = ""
    else:
        advice = normalized
    return {
        "id": getattr(todo, "id", f"d{index}"),
        "title": title or "待处理事项",
        # 为什么现在要你看：优先级 + 关联单据，管理者据此判断急不急
        "reason": " · ".join(
            [p for p in [PRIORITY_LABEL.get(getattr(todo, "priority", ""), ""), label or doc_no] if p]
        ),
        "advice": advice[:120],
        "actionLabel": "去处理" if path else "看详情",
        "actionPath": f"#{path}" if path else "",
    }


def _headline(stats: dict[str, Any], decisions: list[dict[str, Any]]) -> str:
    """一句话说清今天：先讲要你定的事，再讲两个按期率。"""
    bits: list[str] = []
    if stats["decisions"]:
        bits.append(f"有 {stats['decisions']} 件事等你拍板")
    if stats["overdue"]:
        bits.append(f"{stats['overdue']} 张加工单已过预完工日仍未完工")
    if not bits:
        bits.append("今天没有需要你决策的异常")

    order = stats.get("_order")
    manu = stats.get("_manu")
    if (order and order.get("hasData")) or (manu and manu.get("hasData")):
        tail = []
        if order and order.get("hasData"):
            tail.append(f"订单按期交付 {order.get('rate')}%")
        if manu and manu.get("hasData"):
            tail.append(f"工单按期完工 {manu.get('rate')}%")
        bits.append("、".join(tail))
    else:
        bits.append("按期率暂无可比样本（还没有已交付或已完工的单据）")

    return "；".join(bits) + "。"


def _reports_of(db: Session, user_id: str) -> list[dict[str, Any]]:
    """当前周期的报表清单；用户不存在时返回空表（简报其余部分照常）。"""
    from app.models.auth import User

    user = db.get(User, user_id)
    if user is None:
        return []
    try:
        return period_reports.current_reports(db, user=user)
    except Exception:
        # 报表只是简报的附加信息，取不到不能拖垮整张简报
        logger.exception("period reports failed")
        return []


def for_user(db: Session, *, user_id: str, factory_code: str = "", role: str = "manager",
             period: str = "") -> dict[str, Any]:
    """生成简报看板数据。

    :param role: manager / supervisor / staff，决定「需要你决定」的取数范围
    :param period: 指标月份 yyyy-MM，空表示全部
    """
    todos = group_assistant.list_my_todos(
        db, user_id=user_id, status=None, limit=200, factory_code=factory_code
    )
    open_todos = [t for t in todos if (getattr(t, "status", "") or "") in DECISION_STATUSES]
    decision_todos = [t for t in open_todos if (getattr(t, "priority", "") or "") in DECISION_PRIORITIES]
    decision_todos.sort(key=lambda t: (t.priority != "urgent", getattr(t, "created_at", datetime.now(timezone.utc))))
    # 同一件事会因「第 N 次复发」生成多条待办，按标题归一后只留最靠前的一条
    seen_titles: set[str] = set()
    unique_decisions: list[Any] = []
    for todo in decision_todos:
        key = (getattr(todo, "title", "") or "").split("（第")[0].strip()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique_decisions.append(todo)
    picked = unique_decisions[:MAX_DECISIONS]

    order_metric: dict[str, Any] | None = None
    manu_metric: dict[str, Any] | None = None
    overdue_count = 0
    client = None
    try:
        client, _ = mes_user_client.client_for_user(user_id)
        metrics = client.dashboard("on-time", {"period": period} if period else None) or {}
        order_metric = metrics.get("order")
        manu_metric = metrics.get("manuOrder")
    except Exception:
        # 指标拿不到不该拖垮整张简报：数字位显示「暂无样本」，其余照常
        order_metric = None
        manu_metric = None

    # 逾期按 MES 加工单算（预完工日 < 今天且未完工），不靠待办标题去猜 ——
    # 标题是 AI 措辞，措辞一改数字就变，不能当口径。
    if client is not None:
        try:
            from app.services.production_briefing import DONE_STATUSES, overdue_orders

            mo_rows = client.query_panel("MANU_ORDER", page_size=200)
            overdue_count = len([
                row for row in overdue_orders(mo_rows, date.today())
                if str(row.get("单据状态") or "") not in DONE_STATUSES
            ])
        except Exception:
            overdue_count = 0

    stats = {
        "decisions": len(unique_decisions),
        "overdue": overdue_count,
        "aiFindings": len([t for t in open_todos if getattr(t, "origin", "") == "ai"]),
        "_order": order_metric,
        "_manu": manu_metric,
    }

    stat_cards = [
        {"key": "decisions", "label": "需要你拍板", "value": str(stats["decisions"]), "tone": "warn",
         "hint": f"另有 {max(0, len(open_todos) - len(picked))} 条执行层待办"},
        {"key": "overdue", "label": "逾期未完成", "value": str(stats["overdue"]), "tone": "warn" if stats["overdue"] else "ok",
         "hint": "已过预完工日"},
        {"key": "orderRate", "label": "订单按期交付率", "value": _rate_text(order_metric),
         "tone": "ok" if (order_metric or {}).get("hasData") else "muted", "hint": _rate_hint(order_metric)},
        {"key": "moRate", "label": "工单按期完工率", "value": _rate_text(manu_metric),
         "tone": "ok" if (manu_metric or {}).get("hasData") else "muted", "hint": _rate_hint(manu_metric)},
    ]

    return {
        "role": role,
        "period": period or "全部",
        "headline": _headline(stats, picked),
        "stats": stat_cards,
        "decisions": [_decision_of(t, i) for i, t in enumerate(picked)],
        # 「其余」指执行层待办（不需要管理者拍板），不是被截断的决策项
        "hidden": max(0, len(open_todos) - len(picked)),
        # 周期报表：日报/周报/月报各一份，已生成的给会话 id，没生成的给生成入口
        "reports": _reports_of(db, user_id),
        "metrics": {"order": order_metric, "manuOrder": manu_metric},
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "day": date.today().isoformat(),
    }
