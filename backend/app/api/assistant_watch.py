"""AI 主动监控的接口：事件列表、确认、手动触发扫描。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from datetime import datetime, timezone
from urllib.parse import quote

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.config import settings
from app.core.database import transaction
from app.core.timeutil import as_utc
from app.models.assistant_watch import AssistantEvent
from app.models.auth import User
from app.models.group_chat import GroupTodo
from app.services import assistant_watch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant-watch"])


class AckRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class DigestResponse(BaseModel):
    days: int
    created: int
    resolved: int
    open: int
    stale: int
    by_kind: dict[str, int] = {}
    by_person: dict[str, int] = {}


class EventResponse(BaseModel):
    id: str
    kind: str
    severity: str
    status: str
    title: str
    detail: str
    suggested_action: str
    panel_code: str | None = None
    doc_no: str | None = None
    targets: list[str] = []
    created_at: str
    resolved_at: str | None = None
    age_hours: float = 0.0
    reminders: int = 0
    escalated: bool = False
    monday_url: str | None = None
    note: str = ""
    acked_by: str | None = None


class ScanResponse(BaseModel):
    evaluated: int
    created: int
    resolved: int
    open: int
    reminded: int = 0
    quiet: int = 0


class StatusResponse(BaseModel):
    enabled: bool
    interval_seconds: float
    open_for_me: int
    open_total: int
    feed_group: str


def _age_hours(event: AssistantEvent) -> float:
    if not event.first_seen_at:
        return 0.0
    seen = event.first_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - seen).total_seconds() / 3600.0)


def _acked_name(db, event: AssistantEvent) -> str | None:
    if not event.acked_by_user_id:
        return None
    user = db.get(User, event.acked_by_user_id)
    if user is None:
        return None
    return user.display_name or user.username


def _serialize_with_user(db, event: AssistantEvent) -> EventResponse:
    response = _serialize(event)
    response.acked_by = _acked_name(db, event)
    return response


def _serialize(event: AssistantEvent, *, mine: str | None = None) -> EventResponse:
    return EventResponse(
        id=event.id,
        kind=event.kind,
        severity=event.severity,
        status=event.status,
        title=event.title,
        detail=event.detail,
        suggested_action=event.suggested_action,
        panel_code=event.panel_code,
        doc_no=event.doc_no,
        targets=json.loads(event.target_usernames_json or "[]"),
        created_at=(as_utc(event.created_at).isoformat() if event.created_at else ""),
        resolved_at=(as_utc(event.resolved_at).isoformat() if event.resolved_at else None),
        age_hours=round(_age_hours(event), 1),
        reminders=event.reminders,
        escalated=bool(
            event.original_severity in ("info", "warning") and event.severity == "critical"
        ),
        # 有面板和单号就给个直达链接：MES 是 hash 路由，表单页为
        # /#/panelx/form/<panelCode>?code=<单据号>（见 light-mes router/index.js）
        note=event.note or "",
        acked_by=None,   # 由调用方按需填充（见 _serialize_with_user）
        monday_url=(
            f"{(settings.mes_watch_ui_url or settings.mes_watch_base_url).rstrip('/')}"
            f"/#/panelx/form/{event.panel_code}"
            f"?code={quote(str(event.doc_no))}"
            if event.panel_code and event.doc_no
            else None
        ),
    )


@router.get("/events", response_model=list[EventResponse])
def list_events(
    user: User = Depends(current_user_from_bearer_or_cookie),
    status: str = Query(default="open", max_length=16),
    limit: int = Query(default=50, ge=1, le=200),
    acked: str = Query(
        default="any", pattern="^(any|all|none|me|others)$", description="认领筛选"
    ),
    assignee: str = Query(default="", max_length=64, description="来源筛选：用户名或 me"),
) -> list[EventResponse]:
    with transaction() as db:
        return [
            _serialize_with_user(db, item)
            for item in assistant_watch.list_events(
                db,
                user=user,
                status=status,
                limit=limit,
                acked=acked,
                assignee=assignee,
            )
        ]


@router.post("/reports/{kind}", response_model=dict)
async def generate_report(
    kind: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    deliver: bool = Query(default=True),
    insight: bool = Query(default=True, description="是否附模型解读"),
) -> dict:
    """立即生成一份报表（daily/weekly/monthly），默认发到管理员个人聊天。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以生成报表")
    from app.services import assistant_reports

    if kind not in assistant_reports.REPORT_KINDS:
        raise HTTPException(status_code=422, detail=f"不支持的报表类型：{kind}")
    with transaction() as db:
        try:
            return await assistant_reports.generate_report_async(
                db, kind=kind, deliver=deliver, with_insight=insight
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("assistant report failed", exc_info=True)
            raise HTTPException(status_code=500, detail=f"生成报表失败：{exc}") from exc


@router.get("/reports/preview", response_model=dict)
def preview_report(
    user: User = Depends(current_user_from_bearer_or_cookie),
    kind: str = Query(default="daily", max_length=16),
) -> dict:
    """预览报表内容，不投递。"""
    from app.services import assistant_reports

    if kind not in assistant_reports.REPORT_KINDS:
        raise HTTPException(status_code=422, detail=f"不支持的报表类型：{kind}")
    with transaction() as db:
        result = assistant_reports.generate_report(db, kind=kind, deliver=False)
        return {"kind": kind, "title": result["title"], "content": result["content"]}


@router.get("/health", response_model=dict)
def health(
    user: User = Depends(current_user_from_bearer_or_cookie),
    hours: int = Query(default=24, ge=1, le=720),
) -> dict:
    """巡查健康度：多久跑一次、成功率、平均耗时、是否停摆。"""
    with transaction() as db:
        return assistant_watch.scan_health(db, hours=hours)


@router.get("/sla", response_model=dict)
def sla(
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """响应时效：发现到认领、发现到关闭的耗时。"""
    with transaction() as db:
        return assistant_watch.response_metrics(db, days=days)


@router.get("/facets", response_model=dict)
def facets(
    user: User = Depends(current_user_from_bearer_or_cookie),
    status: str = Query(default="open", max_length=16),
    acked: str = Query(default="any", pattern="^(any|all|none|me|others)$"),
) -> dict:
    """筛选面板的计数：未认领/我认领的/别人认领的 + 按人分布。"""
    with transaction() as db:
        return assistant_watch.ack_facets(db, user=user, status=status, acked=acked)


@router.post("/events/{event_id}:ack", response_model=EventResponse)
def ack_event(
    event_id: str,
    payload: AckRequest | None = None,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> EventResponse:
    with transaction() as db:
        event = db.get(AssistantEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        targets = json.loads(event.target_user_ids_json or "[]")
        if user.role != "admin" and user.id not in targets:
            raise HTTPException(status_code=403, detail="这条事件不是派给你的")
        assistant_watch.ack_event(
            db, event=event, user=user, note=(payload.note if payload else "")
        )
        return _serialize_with_user(db, event)


@router.post("/events/{event_id}:reopen", response_model=EventResponse)
def reopen_event(
    event_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> EventResponse:
    with transaction() as db:
        event = db.get(AssistantEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        targets = json.loads(event.target_user_ids_json or "[]")
        if user.role != "admin" and user.id not in targets:
            raise HTTPException(status_code=403, detail="这条事件不是派给你的")
        assistant_watch.reopen_event(db, event=event, user=user)
        return _serialize_with_user(db, event)


@router.get("/digest", response_model=DigestResponse)
def digest(
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=1, ge=1, le=30),
) -> DigestResponse:
    with transaction() as db:
        return DigestResponse(**assistant_watch.build_digest(db, user=user, days=days))


@router.post("/digest:post", response_model=dict)
def post_digest(
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=1, ge=1, le=30),
    force: bool = Query(default=False),
) -> dict:
    """把汇总发到 AI 调度台（管理员）。默认同一天只发一次，force=true 强制再发。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以发布汇总")
    with transaction() as db:
        content = assistant_watch.post_digest(db, days=days, force=force)
        return {
            "posted": bool(content),
            "content": content or "",
            "skipped": "今天已经发过汇总" if not content else "",
        }


@router.post("/archive", response_model=dict)
def archive(
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=0, ge=0, le=365),
) -> dict:
    """把处理完很久的事件归档（不删数据）。days=0 用配置值。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以归档")
    with transaction() as db:
        count = assistant_watch.archive_old_events(
            db, older_than_days=(days or None)
        )
        return {"archived": count}


@router.get("/status", response_model=StatusResponse)
def status(user: User = Depends(current_user_from_bearer_or_cookie)) -> StatusResponse:
    with transaction() as db:
        mine = assistant_watch.list_events(db, user=user, status="open", limit=200)
        total = (
            db.scalar(
                select(func.count(AssistantEvent.id)).where(
                    AssistantEvent.status == "open"
                )
            )
            or 0
        )
        return StatusResponse(
            enabled=settings.mes_watch_enabled,
            interval_seconds=settings.mes_watch_interval_seconds,
            open_for_me=len(mine),
            open_total=int(total),
            feed_group=settings.mes_watch_group_name,
        )


@router.get("/stats", response_model=dict)
def stats(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """按类型/严重度/时长汇总，给「今天该关心什么」一个概览。"""
    with transaction() as db:
        rows = assistant_watch.list_events(db, user=user, status="open", limit=200)
        by_kind: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        stale = 0
        for row in rows:
            by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
            by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
            if _age_hours(row) >= 24:
                stale += 1
        return {
            "open": len(rows),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "stale_over_24h": stale,
            "enabled": settings.mes_watch_enabled,
        }


@router.post("/scan", response_model=ScanResponse)
def scan_now(
    user: User = Depends(current_user_from_bearer_or_cookie),
    background: BackgroundTasks = None,
    wait: bool = Query(default=True),
) -> ScanResponse:
    """手动跑一轮（管理员）；``wait=false`` 时后台执行。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以手动触发扫描")

    def _run() -> dict:
        with transaction() as db:
            return assistant_watch.scan(db)

    if not wait:
        background.add_task(_run)
        return ScanResponse(evaluated=0, created=0, resolved=0, open=0)
    try:
        result = _run()
    except Exception as exc:  # noqa: BLE001 - MES 不可达要让调用方看到
        logger.warning("assistant_watch.scan_failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"扫描失败：{exc}") from exc
    return ScanResponse(**result)


@router.get("/todos", response_model=list[dict])
def my_todos(user: User = Depends(current_user_from_bearer_or_cookie)) -> list[dict]:
    """AI 派发的待办（按人）；前端弹窗用。"""
    with transaction() as db:
        rows = db.scalars(
            select(GroupTodo)
            .where(
                GroupTodo.assignee_user_id == user.id,
                GroupTodo.origin == "ai",
                GroupTodo.status.in_(("pending", "accepted", "doing")),
            )
            .order_by(GroupTodo.created_at.desc())
            .limit(50)
        )
        return [
            {
                "id": row.id,
                "title": row.title,
                "detail": row.detail,
                "priority": row.priority,
                "status": row.status,
                "group_id": row.group_id,
                "created_at": (as_utc(row.created_at).isoformat() if row.created_at else ""),
            }
            for row in rows
        ]
