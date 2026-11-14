"""定时报表：日报 / 周报 / 月报，发到管理员个人聊天框。

为什么发到个人聊天而不是群：
- 报表是管理视角，含全局数字，不该淹没在群里
- 个人聊天里能追溯、能引用、能被 AI 继续追问

内容由「事实统计 + 文字结论」组成：先算准数字，再让模型写一段解读；
模型不可用时**只发数字**，不编造。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import now
from app.core.timeutil import as_utc
from app.models.assistant_watch import AssistantEvent, WatchScanRun
from app.models.auth import User
from app.models.chat import ChatSession
from app.repositories import chat as chat_repo

logger = logging.getLogger(__name__)

REPORT_KINDS = ("daily", "weekly", "monthly")

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
}


@dataclass
class ReportContext:
    """一份报表的全部素材：先算数，再说人话。"""

    kind: str
    period_start: datetime
    period_end: datetime
    created: int = 0
    resolved: int = 0
    open_now: int = 0
    stale: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    by_person: dict[str, int] = field(default_factory=dict)
    resolved_by_kind: dict[str, int] = field(default_factory=dict)
    avg_ack_hours: float | None = None
    avg_close_hours: float | None = None
    ack_within_24h: float | None = None
    scans: int = 0
    scan_failures: int = 0
    avg_scan_ms: int | None = None
    top_items: list[dict[str, Any]] = field(default_factory=list)
    missing_owners: int = 0

    @property
    def period_label(self) -> str:
        return {
            "daily": "日报",
            "weekly": "周报",
            "monthly": "月报",
        }.get(self.kind, "报表")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _period_for(kind: str, *, anchor: date | None = None) -> tuple[datetime, datetime]:
    """按报表类型算时间窗（本地日界）。

    日报是「从昨天这个时刻到现在」，不是「昨天 00:00 到今天 00:00」——
    后者会把今天 0 点之后新发现的事情漏掉。周报/月报用完整自然周期。
    """
    current = now()
    local_now = current.astimezone()
    today = anchor or local_now.date()

    if anchor is not None:
        # 显式指定日期时按自然日整段算（补生成历史报表用）
        if kind == "daily":
            start_day = today - timedelta(days=1)
            end_day = today
        elif kind == "weekly":
            monday = today - timedelta(days=today.weekday())
            start_day = monday - timedelta(days=7)
            end_day = monday
        else:
            first = today.replace(day=1)
            start_day = (first - timedelta(days=1)).replace(day=1)
            end_day = first
        start = datetime.combine(start_day, datetime.min.time()).astimezone()
        end = datetime.combine(end_day, datetime.min.time()).astimezone()
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    if kind == "daily":
        return current - timedelta(days=1), current
    if kind == "weekly":
        monday = today - timedelta(days=today.weekday())
        start = datetime.combine(monday, datetime.min.time()).astimezone()
        return start.astimezone(timezone.utc), current
    first = today.replace(day=1)
    start = datetime.combine(first, datetime.min.time()).astimezone()
    return start.astimezone(timezone.utc), current


def build_context(db: Session, *, kind: str, anchor: date | None = None) -> ReportContext:
    """把报表需要的数字一次算齐。"""
    start, end = _period_for(kind, anchor=anchor)
    events = list(db.scalars(select(AssistantEvent)))
    in_window: list[AssistantEvent] = []
    for event in events:
        seen = _aware(event.first_seen_at or event.created_at)
        if seen and start <= seen < end:
            in_window.append(event)

    open_now = [e for e in events if e.status == "open" and e.archived_at is None]
    stale = 0
    for event in open_now:
        seen = _aware(event.first_seen_at or event.created_at)
        if seen and (now() - seen).total_seconds() > 86400:
            stale += 1

    ctx = ReportContext(
        kind=kind,
        period_start=start,
        period_end=end,
        created=len(in_window),
        open_now=len(open_now),
        stale=stale,
    )

    # 期间内被解决/认领的数量
    for event in in_window:
        resolved_at = _aware(event.resolved_at)
        if event.status == "resolved" and resolved_at and start <= resolved_at < end:
            ctx.resolved += 1
            ctx.resolved_by_kind[event.kind] = ctx.resolved_by_kind.get(event.kind, 0) + 1

    for event in in_window:
        ctx.by_kind[event.kind] = ctx.by_kind.get(event.kind, 0) + 1

    usernames = _usernames(db, open_now)
    for event in open_now:
        if not json.loads(event.target_usernames_json or "[]"):
            ctx.missing_owners += 1
        for uid in json.loads(event.target_user_ids_json or "[]"):
            name = usernames.get(uid)
            if name:
                ctx.by_person[name] = ctx.by_person.get(name, 0) + 1

    # 响应时效
    ack_hours: list[float] = []
    close_hours: list[float] = []
    for event in in_window:
        discovered = _aware(event.first_seen_at or event.created_at)
        if not discovered:
            continue
        if event.acked_by_user_id:
            acked = _aware(event.updated_at)
            if acked and acked >= discovered:
                ack_hours.append((acked - discovered).total_seconds() / 3600)
        resolved_at = _aware(event.resolved_at)
        if event.status == "resolved" and resolved_at:
            close_hours.append((resolved_at - discovered).total_seconds() / 3600)
    if ack_hours:
        ctx.avg_ack_hours = round(sum(ack_hours) / len(ack_hours), 1)
        ctx.ack_within_24h = round(
            sum(1 for h in ack_hours if h <= 24) / len(ack_hours), 2
        )
    if close_hours:
        ctx.avg_close_hours = round(sum(close_hours) / len(close_hours), 1)

    # 巡查健康
    runs = [
        r
        for r in db.scalars(select(WatchScanRun).where(WatchScanRun.started_at >= start))
        if _aware(r.started_at) and _aware(r.started_at) < end
    ]
    ctx.scans = len(runs)
    ctx.scan_failures = sum(1 for r in runs if r.status == "failed")
    durations = [r.duration_ms for r in runs if r.status == "ok" and r.duration_ms]
    if durations:
        ctx.avg_scan_ms = int(sum(durations) / len(durations))

    # Top 待办：按严重度 + 时长排序，给管理层看最该关注的
    ranked = sorted(
        open_now,
        key=lambda e: (
            0 if e.severity == "critical" else 1 if e.severity == "warning" else 2,
            -(_aware(e.first_seen_at or e.created_at) or now()).timestamp(),
        ),
    )
    ctx.top_items = [
        {
            "title": e.title,
            "severity": e.severity,
            "kind": e.kind,
            "doc_no": e.doc_no or "",
        }
        for e in ranked[:8]
    ]
    return ctx


def _usernames(db: Session, events: list[AssistantEvent]) -> dict[str, str]:
    ids = {
        uid
        for event in events
        for uid in json.loads(event.target_user_ids_json or "[]")
        if uid
    }
    if not ids:
        return {}
    rows = db.scalars(select(User).where(User.id.in_(ids)))
    return {user.id: user.display_name or user.username for user in rows}


def render_report(ctx: ReportContext) -> str:
    """把数字排成一份可读的 Markdown 报表（不依赖模型）。"""
    start = as_utc(ctx.period_start).astimezone().strftime("%m-%d")
    end = as_utc(ctx.period_end).astimezone().strftime("%m-%d")
    lines = [f"## MES {ctx.period_label}（{start} ~ {end}）", ""]

    lines.append("### 概览")
    lines.append(
        f"- 新发现 **{ctx.created}** 条待办，处理 **{ctx.resolved}** 条，"
        f"当前积压 **{ctx.open_now}** 条"
        + (f"（其中 {ctx.stale} 条超过 24 小时）" if ctx.stale else "")
    )
    if ctx.avg_ack_hours is not None:
        ratio = (
            f"，{round(ctx.ack_within_24h * 100)}% 在 24 小时内认领"
            if ctx.ack_within_24h is not None
            else ""
        )
        lines.append(f"- 平均认领耗时 **{ctx.avg_ack_hours}** 小时{ratio}")
    if ctx.avg_close_hours is not None:
        lines.append(f"- 平均关闭耗时 **{ctx.avg_close_hours}** 小时")
    if ctx.scans:
        failed = f"，失败 {ctx.scan_failures} 次" if ctx.scan_failures else ""
        speed = f"，平均 {ctx.avg_scan_ms} ms" if ctx.avg_scan_ms else ""
        lines.append(f"- 巡查 {ctx.scans} 轮{failed}{speed}")
    lines.append("")

    if ctx.by_kind:
        lines.append("### 新增分布")
        for kind, count in sorted(ctx.by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {KIND_LABELS.get(kind, kind)}：{count} 条")
        lines.append("")

    if ctx.resolved_by_kind:
        lines.append("### 已处理")
        for kind, count in sorted(ctx.resolved_by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {KIND_LABELS.get(kind, kind)}：{count} 条")
        lines.append("")

    if ctx.by_person:
        lines.append("### 待办分布")
        detail = "、".join(f"{name} {count} 条" for name, count in list(ctx.by_person.items())[:10])
        lines.append(f"- {detail}")
        if ctx.missing_owners:
            lines.append(f"- 其中 {ctx.missing_owners} 条**没有明确负责人**")
        lines.append("")

    if ctx.top_items:
        lines.append("### 重点关注")
        for index, item in enumerate(ctx.top_items, 1):
            flag = "🔥 " if item["severity"] == "critical" else ""
            lines.append(f"{index}. {flag}{item['title']}")
        lines.append("")

    if not ctx.created and not ctx.open_now:
        lines.append("这段时间系统里没有待处理事项。")
    return "\n".join(lines).strip()


def _report_title(kind: str, ctx: ReportContext) -> str:
    start = as_utc(ctx.period_start).astimezone()
    if kind == "daily":
        return f"MES 日报 {start.strftime('%Y-%m-%d')}"
    if kind == "weekly":
        return f"MES 周报 {start.strftime('%Y-%m-%d')} 周"
    return f"MES 月报 {start.strftime('%Y-%m')}"


def admin_user(db: Session) -> User | None:
    user = db.scalar(select(User).where(User.username == settings.bootstrap_username))
    if user is not None:
        return user
    return db.scalar(select(User).where(User.role == "admin").order_by(User.created_at))


def deliver_to_chat(
    db: Session, *, user: User, title: str, content: str
) -> ChatSession:
    """把报表作为一条 AI 消息发进该用户的个人聊天。

    同一标题（同一天/同一周）的会话复用，避免刷出一堆一次性会话。
    """
    session = db.scalar(
        select(ChatSession)
        .where(ChatSession.owner_user_id == user.id, ChatSession.title == title)
        .order_by(ChatSession.updated_at.desc())
    )
    if session is None:
        session = chat_repo.create_session(
            db, owner_user_id=user.id, title=title
        )
    chat_repo.append_messages(
        db,
        session,
        [{"role": "assistant", "content": content}],
    )
    return session


async def generate_report_async(
    db: Session,
    *,
    kind: str,
    anchor: date | None = None,
    deliver: bool = True,
    with_insight: bool = True,
) -> dict[str, Any]:
    """生成报表并附上模型解读。

    数字始终由 :func:`render_report` 出；模型只追加「分析 / 建议 / 风险」段。
    模型不可用时安静退化，报表照常发出。
    """
    if kind not in REPORT_KINDS:
        raise ValueError(f"不支持的报表类型：{kind}")
    ctx = build_context(db, kind=kind, anchor=anchor)
    body = render_report(ctx)

    if with_insight:
        try:
            from app.services import assistant_report_insight

            section = await assistant_report_insight.generate_insight(ctx, db)
            if section:
                body = f"{body}\n\n{section}"
        except Exception:
            logger.exception("report insight failed")

    title = _report_title(kind, ctx)
    session = None
    if deliver:
        user = admin_user(db)
        if user is not None:
            session = deliver_to_chat(db, user=user, title=title, content=body)
    return {
        "kind": kind,
        "title": title,
        "content": body,
        "created": ctx.created,
        "resolved": ctx.resolved,
        "open": ctx.open_now,
        "session_id": session.id if session else None,
        "has_insight": "### 分析" in body,
    }


def generate_report(
    db: Session,
    *,
    kind: str,
    anchor: date | None = None,
    deliver: bool = True,
    explain: Any | None = None,
) -> dict[str, Any]:
    """同步版：只出数字，不调模型（供测试与纯数字场景）。"""
    if kind not in REPORT_KINDS:
        raise ValueError(f"不支持的报表类型：{kind}")
    ctx = build_context(db, kind=kind, anchor=anchor)
    body = render_report(ctx)

    title = _report_title(kind, ctx)
    session = None
    if deliver:
        user = admin_user(db)
        if user is not None:
            session = deliver_to_chat(db, user=user, title=title, content=body)

    return {
        "kind": kind,
        "title": title,
        "content": body,
        "created": ctx.created,
        "resolved": ctx.resolved,
        "open": ctx.open_now,
        "session_id": session.id if session else None,
    }
