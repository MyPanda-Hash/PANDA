"""周期报表入口：把已存在的日报/周报/月报挂到简报上。

为什么要单独一层：
- 报表生成逻辑早在 assistant_reports 里（含模型解读与投递个人对话），
  这里不重写，只负责「当前周期是哪一份、生成过没有、点了怎么生成」。
- 简报是每天第一眼看的地方，报表放这里比让人自己想「上周的周报在哪」更合理。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.chat import ChatSession
from app.services import assistant_reports

KINDS = ("daily", "weekly", "monthly")
LABELS = {"daily": "日报", "weekly": "周报", "monthly": "月报"}


def _title_of(kind: str, today: date) -> str:
    """与 assistant_reports._report_title 保持一致的标题（用于判断是否生成过）。

    注意日报取的是**周期起点**（昨天），不是今天：报表覆盖「昨天到现在」，
    标题按起点命名。这里若用 today 拼，生成完简报仍会显示「未生成」（踩过）。
    """
    if kind == "daily":
        return f"MES 日报 {(today - timedelta(days=1)).isoformat()}"
    if kind == "weekly":
        monday = today - timedelta(days=today.weekday())
        return f"MES 周报 {monday.isoformat()} 周"
    return f"MES 月报 {today.strftime('%Y-%m')}"


def current_reports(db: Session, *, user: User, today: date | None = None) -> list[dict[str, Any]]:
    """当前周期的三份报表：标题 + 是否已生成 + 对应会话 id。"""
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for kind in KINDS:
        title = _title_of(kind, today)
        session = db.scalar(
            select(ChatSession)
            .where(ChatSession.owner_user_id == user.id, ChatSession.title == title)
            .order_by(ChatSession.updated_at.desc())
        )
        out.append({
            "kind": kind,
            "label": LABELS[kind],
            "title": title,
            "generated": session is not None,
            "sessionId": session.id if session else "",
            "updatedAt": session.updated_at.isoformat() if session and session.updated_at else "",
        })
    return out


def generate_sync(db: Session, *, user: User, kind: str, anchor: date | None = None) -> dict[str, Any]:
    """同步版：只出数字报表，不调模型加解读。

    对话工具（execute_capability）是同步调用，不能 await 模型；而且模型后续
    本来就会自己总结，这里再加一段解读是重复。[同步/异步] 两条路共用同一套取数与渲染。
    """
    if kind not in KINDS:
        raise ValueError(f"不支持的报表类型：{kind}")
    ctx = assistant_reports.build_context(db, kind=kind, anchor=anchor)
    body = assistant_reports.render_report(ctx)
    title = assistant_reports._report_title(kind, ctx)
    session = assistant_reports.deliver_to_chat(db, user=user, title=title, content=body)
    return {
        "kind": kind,
        "label": LABELS[kind],
        "title": title,
        "content": body,
        "sessionId": session.id if session else "",
        "hasInsight": False,
    }


async def generate(db: Session, *, user: User, kind: str, anchor: date | None = None) -> dict[str, Any]:
    """异步版（页面按钮用）：数字 + 模型解读。"""
    if kind not in KINDS:
        raise ValueError(f"不支持的报表类型：{kind}")
    result = await assistant_reports.generate_report_async(db, kind=kind, anchor=anchor, deliver=True)
    return {
        "kind": kind,
        "label": LABELS[kind],
        "title": result.get("title", ""),
        "content": result.get("content", ""),
        "sessionId": result.get("session_id") or "",
        "hasInsight": bool(result.get("has_insight")),
    }
