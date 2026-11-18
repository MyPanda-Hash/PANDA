from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Literal, Never

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import JSON, cast, desc, func, or_, select

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import transaction
from app.core.security import as_utc, iso, now
from app.models.auth import User
from app.models.notification_delivery import NotificationDelivery, NotificationTarget
from app.models.schedules import (
    Schedule,
    ScheduleDraft,
    ScheduleNotification,
    ScheduleNotificationCursor,
    ScheduleRun,
)
from app.repositories import schedules as repository
from app.schemas.schedules import (
    CapabilityListDTO,
    DraftConfirmRequest,
    DeliveryListDTO,
    DraftCreateRequest,
    DraftDTO,
    DraftListDTO,
    DraftPatchRequest,
    DraftPreviewDTO,
    DraftPreviewRequest,
    NotificationListDTO,
    RunActionRequest,
    RunDTO,
    RunListDTO,
    ScheduleActionRequest,
    ScheduleDTO,
    ScheduleListDTO,
    SchedulePatchRequest,
    SnoozeRequest,
)
from app.services import schedules as service
from app.services.schedule_access import ScheduleAccessError, list_schedule_skills

router = APIRouter(tags=["schedules"])


def _raise_service_error(exc: Exception) -> Never:
    if isinstance(exc, service.ScheduleServiceError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    if isinstance(exc, ScheduleAccessError):
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    raise exc


def _owned_draft(db, draft_id: str, owner_user_id: str) -> ScheduleDraft:
    draft = repository.owned_draft(db, draft_id, owner_user_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="草案不存在")
    return draft


def _owned_schedule(db, schedule_id: str, owner_user_id: str) -> Schedule:
    schedule = repository.owned_schedule(db, schedule_id, owner_user_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return schedule


def _owned_run(db, schedule_id: str, run_id: str, owner_user_id: str) -> ScheduleRun:
    run = repository.owned_run(db, schedule_id, run_id, owner_user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return run


def _cursor_signature(owner_user_id: str, sequence: int) -> str:
    payload = f"{owner_user_id}:{sequence}".encode()
    return hmac.new(settings.jwt_secret.encode(), payload, hashlib.sha256).hexdigest()[
        :32
    ]


def _encode_cursor(owner_user_id: str, sequence: int) -> str:
    value = json.dumps(
        {"s": sequence, "m": _cursor_signature(owner_user_id, sequence)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(owner_user_id: str, cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        sequence = int(value["s"])
        signature = str(value["m"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="通知游标无效") from exc
    if sequence < 0 or not hmac.compare_digest(
        signature, _cursor_signature(owner_user_id, sequence)
    ):
        raise HTTPException(status_code=422, detail="通知游标无效")
    return sequence


@router.post("/schedule-drafts", response_model=DraftDTO)
def create_schedule_draft(
    payload: DraftCreateRequest, user: User = Depends(current_user)
) -> DraftDTO:
    with transaction() as db:
        try:
            draft = service.create_draft(
                db,
                user.id,
                payload.config.model_dump(mode="json"),
                payload.request_id,
                payload.source_session_id,
                payload.source_message_id,
                payload.operation,
                payload.target_schedule_id,
                payload.target_revision,
            )
            return DraftDTO.model_validate(service.draft_response(db, draft))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.get("/schedule-drafts", response_model=DraftListDTO)
def list_schedule_drafts(
    session_id: str | None = Query(default=None, min_length=1, max_length=64),
    user: User = Depends(current_user),
) -> DraftListDTO:
    with transaction() as db:
        drafts = service.list_drafts(db, user.id, session_id=session_id)
        return DraftListDTO.model_validate(
            {"items": [service.draft_response(db, item) for item in drafts]}
        )


@router.get("/schedule-drafts/{draft_id}", response_model=DraftDTO)
def get_schedule_draft(draft_id: str, user: User = Depends(current_user)) -> DraftDTO:
    with transaction() as db:
        draft = _owned_draft(db, draft_id, user.id)
        return DraftDTO.model_validate(service.draft_response(db, draft))


@router.patch("/schedule-drafts/{draft_id}", response_model=DraftDTO)
def patch_schedule_draft(
    draft_id: str,
    payload: DraftPatchRequest,
    user: User = Depends(current_user),
) -> DraftDTO:
    with transaction() as db:
        try:
            draft = service.update_draft(
                db,
                _owned_draft(db, draft_id, user.id),
                expected_revision=payload.expected_revision,
                config=payload.config.model_dump(mode="json"),
                request_id=payload.request_id,
            )
            return DraftDTO.model_validate(service.draft_response(db, draft))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.post("/schedule-drafts/{draft_id}/cancel", response_model=DraftDTO)
def cancel_schedule_draft(
    draft_id: str,
    payload: ScheduleActionRequest,
    user: User = Depends(current_user),
) -> DraftDTO:
    with transaction() as db:
        try:
            draft = service.cancel_draft(
                db,
                _owned_draft(db, draft_id, user.id),
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
            )
            return DraftDTO.model_validate(service.draft_response(db, draft))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.post("/schedule-drafts/{draft_id}/preview", response_model=DraftPreviewDTO)
def preview_schedule_draft(
    draft_id: str,
    payload: DraftPreviewRequest,
    user: User = Depends(current_user),
) -> DraftPreviewDTO:
    with transaction() as db:
        try:
            draft, occurrences, blockers = service.preview_draft(
                db,
                _owned_draft(db, draft_id, user.id),
                expected_revision=payload.expected_revision,
            )
            return DraftPreviewDTO.model_validate(
                {
                    "draft": service.draft_response(db, draft),
                    "next_runs": [iso(item) for item in occurrences],
                    "preview_token": draft.preview_token,
                    "blockers": blockers,
                }
            )
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.post("/schedule-drafts/{draft_id}/confirm", response_model=ScheduleDTO)
def confirm_schedule_draft(
    draft_id: str,
    payload: DraftConfirmRequest,
    user: User = Depends(current_user),
) -> ScheduleDTO:
    with transaction() as db:
        try:
            schedule = service.confirm_draft(
                db,
                _owned_draft(db, draft_id, user.id),
                expected_revision=payload.expected_revision,
                preview_token=payload.preview_token,
                request_id=payload.request_id,
                enabled=payload.enabled,
            )
            return ScheduleDTO.model_validate(
                service.schedule_response(db, schedule, user.id)
            )
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


# 静态路径必须位于 /schedules/{schedule_id} 之前。
@router.get("/schedules/capabilities", response_model=CapabilityListDTO)
def schedule_capabilities(user: User = Depends(current_user)) -> CapabilityListDTO:
    del user
    return CapabilityListDTO.model_validate({"items": list_schedule_skills()})


@router.get("/schedules/notifications", response_model=NotificationListDTO)
def schedule_notifications(
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
) -> NotificationListDTO:
    with transaction() as db:
        if cursor is None:
            current = db.get(ScheduleNotificationCursor, user.id)
            sequence = current.last_sequence if current else 0
            return NotificationListDTO(
                items=[], cursor=_encode_cursor(user.id, sequence), has_more=False
            )
        sequence = _decode_cursor(user.id, cursor)
        rows = list(
            db.scalars(
                repository.notification_query(user.id, sequence).limit(limit + 1)
            )
        )
        scanned = rows[:limit]
        next_sequence = scanned[-1].sequence if scanned else sequence
        current_time = now()
        visible: list[dict] = []
        for item in scanned:
            run = db.get(ScheduleRun, item.run_id)
            if run is None or run.handled_at is not None:
                continue
            if as_utc(item.expires_at) <= current_time:
                continue
            schedule = db.get(Schedule, item.schedule_id)
            visible.append(
                {
                    "event_id": item.event_id,
                    "run_id": item.run_id,
                    "schedule_id": item.schedule_id,
                    "status": item.status,
                    "title": item.title,
                    "resource_path": item.resource_path,
                    "created_at": iso(item.created_at),
                    "expires_at": iso(item.expires_at),
                    "announce": item.announce,
                    "sound": item.sound,
                    "desktop": item.desktop,
                    # handled_at 已在上方过滤，因此这里等价于任务中心的
                    # requires_action（提醒完成但未处理）。
                    "requires_action": (
                        schedule is not None
                        and schedule.kind == "reminder"
                        and item.status == "completed"
                    ),
                }
            )
        return NotificationListDTO.model_validate(
            {
                "items": visible,
                "cursor": _encode_cursor(user.id, next_sequence),
                "has_more": len(rows) > limit,
            }
        )


@router.get("/schedules", response_model=ScheduleListDTO)
def list_schedules(
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=100),
    kind: Literal["reminder", "skill"] | None = Query(default=None),
    status: Literal["enabled", "paused", "blocked", "completed", "archived"]
    | None = Query(default=None),
    query: str | None = Query(default=None),
    user: User = Depends(current_user),
) -> ScheduleListDTO:
    with transaction() as db:
        statement = select(Schedule).where(Schedule.owner_user_id == user.id)
        if kind:
            statement = statement.where(Schedule.kind == kind)
        if status:
            statement = statement.where(Schedule.status == status)
        needle = (query or "").strip()
        if needle:
            # 配置存为 Text：按数据库方言提取提醒内容，避免匹配配置中的其他字段。
            instruction = (
                func.json_extract(Schedule.config_json, "$.instruction")
                if db.get_bind().dialect.name == "sqlite"
                else cast(Schedule.config_json, JSON)["instruction"].as_string()
            )
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    Schedule.name.ilike(pattern, escape="\\"),
                    instruction.ilike(pattern, escape="\\"),
                )
            )
        rows = list(
            db.scalars(
                statement.order_by(desc(Schedule.updated_at), desc(Schedule.id))
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return ScheduleListDTO.model_validate(
            {
                "items": [
                    service.schedule_response(db, item, user.id)
                    for item in rows[:limit]
                ],
                "next_offset": offset + limit if len(rows) > limit else None,
            }
        )


@router.get("/schedules/{schedule_id}", response_model=ScheduleDTO)
def get_schedule(schedule_id: str, user: User = Depends(current_user)) -> ScheduleDTO:
    with transaction() as db:
        return ScheduleDTO.model_validate(
            service.schedule_response(
                db, _owned_schedule(db, schedule_id, user.id), user.id
            )
        )


@router.patch("/schedules/{schedule_id}", response_model=ScheduleDTO)
def patch_schedule(
    schedule_id: str,
    payload: SchedulePatchRequest,
    user: User = Depends(current_user),
) -> ScheduleDTO:
    with transaction() as db:
        try:
            schedule = service.update_schedule(
                db,
                _owned_schedule(db, schedule_id, user.id),
                expected_revision=payload.expected_revision,
                config=payload.config.model_dump(mode="json"),
                request_id=payload.request_id,
            )
            return ScheduleDTO.model_validate(
                service.schedule_response(db, schedule, user.id)
            )
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


def _schedule_transition(
    schedule_id: str, action: str, payload: ScheduleActionRequest, user: User
) -> ScheduleDTO:
    with transaction() as db:
        try:
            schedule = service.transition_schedule(
                db,
                _owned_schedule(db, schedule_id, user.id),
                action,
                payload.expected_revision,
                payload.request_id,
            )
            return ScheduleDTO.model_validate(
                service.schedule_response(db, schedule, user.id)
            )
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.post("/schedules/{schedule_id}/pause", response_model=ScheduleDTO)
def pause_schedule(
    schedule_id: str,
    payload: ScheduleActionRequest,
    user: User = Depends(current_user),
) -> ScheduleDTO:
    return _schedule_transition(schedule_id, "pause", payload, user)


@router.post("/schedules/{schedule_id}/resume", response_model=ScheduleDTO)
def resume_schedule(
    schedule_id: str,
    payload: ScheduleActionRequest,
    user: User = Depends(current_user),
) -> ScheduleDTO:
    return _schedule_transition(schedule_id, "resume", payload, user)


@router.post("/schedules/{schedule_id}/archive", response_model=ScheduleDTO)
def archive_schedule(
    schedule_id: str,
    payload: ScheduleActionRequest,
    user: User = Depends(current_user),
) -> ScheduleDTO:
    return _schedule_transition(schedule_id, "archive", payload, user)


@router.post("/schedules/{schedule_id}/run-now", response_model=RunDTO)
def run_schedule_now(
    schedule_id: str,
    payload: ScheduleActionRequest,
    user: User = Depends(current_user),
) -> RunDTO:
    with transaction() as db:
        try:
            run = service.run_now(
                db,
                _owned_schedule(db, schedule_id, user.id),
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
            )
            return RunDTO.model_validate(service.run_response(db, run, user.id))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.get("/schedules/{schedule_id}/runs", response_model=RunListDTO)
def list_schedule_runs(
    schedule_id: str,
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
) -> RunListDTO:
    with transaction() as db:
        _owned_schedule(db, schedule_id, user.id)
        rows = list(
            db.scalars(
                select(ScheduleRun)
                .where(
                    ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.owner_user_id == user.id,
                )
                .order_by(desc(ScheduleRun.created_at), desc(ScheduleRun.id))
                .offset(offset)
                .limit(limit + 1)
            )
        )
        visible = []
        for item in rows[:limit]:
            try:
                visible.append(service.run_response(db, item, user.id))
            except service.ScheduleServiceError as exc:
                if exc.code != "not_found":
                    _raise_service_error(exc)
        return RunListDTO.model_validate(
            {
                "items": visible,
                "next_offset": offset + limit if len(rows) > limit else None,
            }
        )


@router.get("/schedules/{schedule_id}/runs/{run_id}", response_model=RunDTO)
def get_schedule_run(
    schedule_id: str, run_id: str, user: User = Depends(current_user)
) -> RunDTO:
    with transaction() as db:
        try:
            return RunDTO.model_validate(
                service.run_response(
                    db, _owned_run(db, schedule_id, run_id, user.id), user.id
                )
            )
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.get(
    "/schedules/{schedule_id}/runs/{run_id}/notifications",
    response_model=DeliveryListDTO,
)
def list_run_notification_deliveries(
    schedule_id: str, run_id: str, user: User = Depends(current_user)
) -> DeliveryListDTO:
    with transaction() as db:
        run = _owned_run(db, schedule_id, run_id, user.id)
        try:
            service.run_response(db, run, user.id)
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)
        rows = db.execute(
            select(NotificationDelivery, NotificationTarget)
            .join(ScheduleNotification, NotificationDelivery.event_id == ScheduleNotification.id)
            .outerjoin(NotificationTarget, NotificationDelivery.target_id == NotificationTarget.id)
            .where(
                ScheduleNotification.run_id == run_id,
                NotificationDelivery.owner_user_id == user.id,
            )
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        ).all()
        return DeliveryListDTO.model_validate(
            {
                "items": [
                    {
                        "id": delivery.id,
                        "target_label": delivery.target_label_snapshot,
                        "channel": target.kind if target else "unknown",
                        "status": delivery.status,
                        "created_at": iso(delivery.created_at),
                        "updated_at": iso(delivery.updated_at),
                        "last_error_code": delivery.last_error_code,
                    }
                    for delivery, target in rows
                ]
            }
        )


def _run_action(
    schedule_id: str,
    run_id: str,
    action: Literal["cancel", "retry", "handle"],
    payload: RunActionRequest,
    user: User,
) -> RunDTO:
    with transaction() as db:
        run = _owned_run(db, schedule_id, run_id, user.id)
        try:
            if action == "cancel":
                run = service.cancel_run(db, run, request_id=payload.request_id)
                if run.status == "cancelled":
                    repository.emit_notification_safely(db, run)
            elif action == "retry":
                run = service.retry_run(db, run, request_id=payload.request_id)
            else:
                run = service.handle_run(db, run, request_id=payload.request_id)
            return RunDTO.model_validate(service.run_response(db, run, user.id))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)


@router.post("/schedules/{schedule_id}/runs/{run_id}/cancel", response_model=RunDTO)
def cancel_schedule_run(
    schedule_id: str,
    run_id: str,
    payload: RunActionRequest,
    user: User = Depends(current_user),
) -> RunDTO:
    return _run_action(schedule_id, run_id, "cancel", payload, user)


@router.post("/schedules/{schedule_id}/runs/{run_id}/retry", response_model=RunDTO)
def retry_schedule_run(
    schedule_id: str,
    run_id: str,
    payload: RunActionRequest,
    user: User = Depends(current_user),
) -> RunDTO:
    return _run_action(schedule_id, run_id, "retry", payload, user)


@router.post("/schedules/{schedule_id}/runs/{run_id}/handle", response_model=RunDTO)
def handle_schedule_run(
    schedule_id: str,
    run_id: str,
    payload: RunActionRequest,
    user: User = Depends(current_user),
) -> RunDTO:
    return _run_action(schedule_id, run_id, "handle", payload, user)


@router.post("/schedules/{schedule_id}/runs/{run_id}/snooze", response_model=RunDTO)
def snooze_schedule_run(
    schedule_id: str,
    run_id: str,
    payload: SnoozeRequest,
    user: User = Depends(current_user),
) -> RunDTO:
    with transaction() as db:
        try:
            child = service.snooze_run(
                db,
                _owned_run(db, schedule_id, run_id, user.id),
                request_id=payload.request_id,
                until=payload.until,
            )
            return RunDTO.model_validate(service.run_response(db, child, user.id))
        except (service.ScheduleServiceError, ScheduleAccessError) as exc:
            _raise_service_error(exc)
