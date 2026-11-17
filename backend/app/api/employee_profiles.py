"""员工职能卡片与项目经验（仅管理员）。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.core.timeutil import as_utc
from app.models.auth import User
from app.models.employee_profile import EmployeeProfile
from app.services import employee_profiles as profiles
from app.services import session_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/employees", tags=["employee-profiles"])


class ProfileResponse(BaseModel):
    id: str
    user_id: str
    display_name: str | None = None
    username: str | None = None
    period_start: str
    period_end: str
    chat_sessions: int
    chat_messages: int
    group_messages: int
    todo_assigned: int
    todo_completed: int
    events_acked: int
    actions_total: int
    active_days: int
    avg_response_hours: float | None = None
    summary: str
    strengths: list[str] = []
    risks: list[str] = []
    focus_areas: list[str] = []
    capability_tags: list[str] = []
    activity_digest: str = ""
    generated_at: str
    generated_by: str


class ExperienceResponse(BaseModel):
    id: str
    title: str
    topic: str = ""
    status: str = "unverified"
    reusable: bool = False
    not_applicable: str = ""
    source_count: int = 1
    problem: str
    resolution: str
    lesson: str
    tags: list[str] = []
    group_id: str | None = None
    source_ref: str | None = None
    contributors: list[str] = []
    created_at: str


def _json_raw(raw: str | None) -> list:
    """保留原始结构（含 dict），给需要对象数组的字段用。"""
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def _serialize(db, profile: EmployeeProfile) -> ProfileResponse:
    user = db.get(User, profile.user_id)
    return ProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        display_name=user.display_name if user else None,
        username=user.username if user else None,
        period_start=as_utc(profile.period_start).isoformat() if profile.period_start else "",
        period_end=as_utc(profile.period_end).isoformat() if profile.period_end else "",
        chat_sessions=profile.chat_sessions,
        chat_messages=profile.chat_messages,
        group_messages=profile.group_messages,
        todo_assigned=profile.todo_assigned,
        todo_completed=profile.todo_completed,
        events_acked=profile.events_acked,
        actions_total=profile.actions_total,
        active_days=profile.active_days,
        avg_response_hours=profile.avg_response_hours,
        summary=profile.summary,
        strengths=_json_list(profile.strengths),
        risks=_json_list(profile.risks),
        focus_areas=_json_list(profile.focus_areas),
        capability_tags=_json_list(profile.capability_tags),
        activity_digest=profile.activity_digest,
        generated_at=as_utc(profile.generated_at).isoformat() if profile.generated_at else "",
        generated_by=profile.generated_by,
    )


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以查看员工画像")


@router.get("", response_model=list[ProfileResponse])
def list_profiles(
    user: User = Depends(current_user_from_bearer_or_cookie),
    user_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ProfileResponse]:
    _require_admin(user)
    with transaction() as db:
        rows = profiles.list_profiles(db, actor=user, user_id=user_id, limit=limit)
        return [_serialize(db, row) for row in rows]


@router.post("/experiences:extract", response_model=dict)
async def extract_experiences(
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=7, ge=1, le=60),
    with_model: bool = Query(default=True),
) -> dict:
    """手动触发一次经验沉淀（默认覆盖最近 7 天）。"""
    _require_admin(user)
    from datetime import datetime, timedelta, timezone

    with transaction() as db:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        try:
            created = await profiles.extract_from_groups(
                db, period_start=start, period_end=end, with_model=with_model
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("experience extraction failed")
            raise HTTPException(status_code=500, detail=f"沉淀失败：{exc}") from exc
        return {
            "created": len(created),
            "titles": [row.title for row in created],
        }


class ExperienceStatusRequest(BaseModel):
    status: str = Field(pattern="^(unverified|verified|stale)$")
    reusable: bool | None = None
    not_applicable: str | None = Field(default=None, max_length=1000)


@router.patch("/experiences/{experience_id}", response_model=ExperienceResponse)
def update_experience(
    experience_id: str,
    payload: ExperienceStatusRequest,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> ExperienceResponse:
    """确认经验证据：标已验证 / 标记可复用 / 补适用范围。"""
    _require_admin(user)
    with transaction() as db:
        try:
            row = profiles.set_experience_status(
                db,
                experience_id=experience_id,
                status=payload.status,
                reusable=payload.reusable,
                not_applicable=payload.not_applicable,
            )
        except profiles.ProfileError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ExperienceResponse(
            id=row.id, title=row.title, topic=row.topic or "",
            status=row.status, reusable=row.reusable,
            not_applicable=row.not_applicable or "", source_count=row.source_count,
            problem=row.problem, resolution=row.resolution, lesson=row.lesson,
            tags=_json_list(row.tags), group_id=row.group_id, source_ref=row.source_ref,
            contributors=_json_list(row.contributors),
            created_at=as_utc(row.created_at).isoformat() if row.created_at else "",
        )


@router.get("/promotions", response_model=list[dict])
def promotion_candidates(
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> list[dict]:
    """哪些 topic 已经攒够证据、可以晋升成 SOP。"""
    _require_admin(user)
    with transaction() as db:
        return profiles.list_promotion_candidates(db)


@router.get("/duplicates", response_model=list[dict])
def duplicates(
    user: User = Depends(current_user_from_bearer_or_cookie),
    threshold: float = Query(default=0.55, ge=0.0, le=1.0),
) -> list[dict]:
    """找出可能重复的经验条目。"""
    _require_admin(user)
    with transaction() as db:
        return profiles.find_duplicates(db, threshold=threshold)


@router.get("/experiences", response_model=list[ExperienceResponse])
def list_experiences(
    user: User = Depends(current_user_from_bearer_or_cookie),
    keyword: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ExperienceResponse]:
    _require_admin(user)
    with transaction() as db:
        rows = profiles.list_experiences(db, actor=user, keyword=keyword, limit=limit)
        return [
            ExperienceResponse(
                id=row.id,
                title=row.title,
                topic=row.topic or "",
                status=row.status,
                reusable=row.reusable,
                not_applicable=row.not_applicable or "",
                source_count=row.source_count,
                problem=row.problem,
                resolution=row.resolution,
                lesson=row.lesson,
                tags=_json_list(row.tags),
                group_id=row.group_id,
                source_ref=row.source_ref,
                contributors=_json_list(row.contributors),
                created_at=as_utc(row.created_at).isoformat() if row.created_at else "",
            )
            for row in rows
        ]


@router.post("/{target_user_id}:distill", response_model=ProfileResponse)
async def distill(
    target_user_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    with_model: bool = Query(default=True),
):
    """手动为一个员工生成本周画像。"""
    _require_admin(user)
    with transaction() as db:
        target = db.get(User, target_user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        profile = await profiles.distill_user_async(
            db, user=target, with_model=with_model
        )
        return _serialize(db, profile)


@router.post(":distill-all", response_model=list[ProfileResponse])
async def distill_all(
    user: User = Depends(current_user_from_bearer_or_cookie),
    with_model: bool = Query(default=True),
) -> list[ProfileResponse]:
    """为所有活跃用户生成画像（批量）。"""
    _require_admin(user)
    with transaction() as db:
        targets = [
            row
            for row in db.scalars(select(User).where(User.status == "active"))
        ]
        out: list[ProfileResponse] = []
        for target in targets:
            try:
                profile = await profiles.distill_user_async(
                    db, user=target, with_model=with_model
                )
                out.append(_serialize(db, profile))
            except Exception:
                logger.exception("distill failed", extra={"user_id": target.id})
        return out



# ---------------------------------------------------------------- 会话记忆


class MemoryHighlight(BaseModel):
    title: str = ""
    detail: str = ""
    session_id: str = ""


class SessionMemoryResponse(BaseModel):
    id: str
    user_id: str
    display_name: str | None = None
    period_start: str
    period_end: str
    headline: str = ""
    topic: str = ""
    highlights: list[MemoryHighlight] = []
    lessons: list[str] = []
    tools: list[str] = []
    session_ids: list[str] = []
    session_count: int = 0
    message_count: int = 0
    confidence: float = 0.0
    generated_by: str = ""
    created_at: str


def _serialize_memory(db, row) -> SessionMemoryResponse:
    owner = db.get(User, row.user_id)
    raw_highlights = _json_raw(row.highlights)
    highlights = [
        MemoryHighlight(
            title=str(item.get("title") or ""),
            detail=str(item.get("detail") or ""),
            session_id=str(item.get("session_id") or ""),
        )
        for item in raw_highlights
        if isinstance(item, dict)
    ]
    return SessionMemoryResponse(
        id=row.id,
        user_id=row.user_id,
        display_name=(owner.display_name or owner.username) if owner else None,
        period_start=as_utc(row.period_start).isoformat() if row.period_start else "",
        period_end=as_utc(row.period_end).isoformat() if row.period_end else "",
        headline=row.headline or "",
        topic=row.topic or "",
        highlights=highlights,
        lessons=_json_list(row.lessons),
        tools=_json_list(row.tools),
        session_ids=_json_list(row.session_ids),
        session_count=row.session_count,
        message_count=row.message_count,
        confidence=float(row.confidence or 0.0),
        generated_by=row.generated_by or "",
        created_at=as_utc(row.created_at).isoformat() if row.created_at else "",
    )


@router.get("/{target_user_id}/memories", response_model=list[SessionMemoryResponse])
def list_user_memories(
    target_user_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    limit: int = Query(default=12, ge=1, le=50),
) -> list[SessionMemoryResponse]:
    """某位员工的 AI 会话记忆（按时间倒序）。"""
    _require_admin(user)
    with transaction() as db:
        rows = session_memory.list_memories(db, user_id=target_user_id, limit=limit)
        return [_serialize_memory(db, row) for row in rows]


@router.post("/{target_user_id}:distill-sessions", response_model=SessionMemoryResponse)
async def distill_user_sessions(
    target_user_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    days: int = Query(default=7, ge=1, le=90),
    with_model: bool = Query(default=True),
) -> SessionMemoryResponse:
    """把某人最近 N 天的 AI 会话蒸成一条记忆（同周期覆盖写）。"""
    _require_admin(user)
    with transaction() as db:
        target = db.get(User, target_user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        row = await session_memory.distill_sessions(
            db, user=target, days=days, with_model=with_model
        )
        return _serialize_memory(db, row)