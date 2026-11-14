from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import as_utc, iso, now
from app.models.chat import ChatMessage, ChatSession
from app.models.schedules import Schedule, ScheduleDraft, ScheduleRun
from app.models.notification_delivery import (
    NotificationConnection,
    NotificationTarget,
    NotificationTargetGrant,
)
from app.repositories import schedules as repository
from app.schemas.schedules import ScheduleConfig
from app.services.model_routing import resolve_model_endpoint
from app.services.schedule_access import (
    ScheduleAccessError,
    can_access_schedule_run,
    resolve_schedule_skill,
    validate_schedule_scope,
)
from app.services.schedule_time import next_occurrence, preview_occurrences

_DRAFT_TTL = timedelta(days=7)
_PREVIEW_TTL = timedelta(minutes=30)
_ACTIVE_RUN_STATUSES = frozenset(
    {"queued", "running", "awaiting_user", "outcome_unknown"}
)


class ScheduleServiceError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_object(raw: str | None, *, field: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{field} 存储内容无效") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{field} 存储内容无效")
    return value


def _load_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _validate_config(config: dict) -> dict:
    try:
        return ScheduleConfig.model_validate(config).model_dump(mode="json")
    except ValidationError as exc:
        raise ScheduleServiceError(
            "config_invalid", "定时任务配置不完整或不合法", status_code=422
        ) from exc


def _validate_notification_targets(
    db: Session, owner_user_id: str, config: dict
) -> None:
    for rule in config.get("notification", {}).get("external", []):
        target = db.get(NotificationTarget, rule["target_id"])
        if (
            target is None
            or target.status != "active"
            or target.revision != rule["target_revision"]
            or target.verified_at is None
            or target.consent_at is None
        ):
            raise ScheduleServiceError(
                "notification_target_unavailable",
                "外部通知目标已变化或尚未验证，请重新选择并预览",
            )
        connection = db.get(NotificationConnection, target.connection_id)
        if connection is None or connection.status != "active":
            raise ScheduleServiceError(
                "notification_target_unavailable", "外部通知通道不可用"
            )
        if target.owner_user_id == owner_user_id:
            continue
        if target.owner_user_id is not None or db.get(
            NotificationTargetGrant, (target.id, owner_user_id)
        ) is None:
            raise ScheduleServiceError(
                "notification_target_unavailable", "外部通知目标不可用"
            )


def _validate_source(
    db: Session,
    owner_user_id: str,
    source_session_id: str | None,
    source_message_id: str | None,
) -> None:
    if source_session_id is None and source_message_id is None:
        return
    if not source_session_id or not source_message_id:
        raise ScheduleServiceError("source_invalid", "来源会话无效", status_code=422)
    session = db.scalar(
        select(ChatSession.id).where(
            ChatSession.id == source_session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    message = db.scalar(
        select(ChatMessage.id).where(
            ChatMessage.id == source_message_id,
            ChatMessage.session_id == source_session_id,
            ChatMessage.owner_user_id == owner_user_id,
        )
    )
    if session is None or message is None:
        raise ScheduleServiceError("source_unavailable", "来源会话已不可用")


def _skill_request_pending(config: dict, skill_request_id: str | None) -> bool:
    """聊天里明确申请了新能力：能力批准并绑定前停在等待状态，不按提示词直接执行。"""
    return (
        bool(skill_request_id)
        and config.get("kind") == "skill"
        and not config.get("skill_id")
    )


def _resolve_model(db: Session, config: dict) -> dict:
    if config["kind"] == "reminder":
        return config
    requested = str(config.get("model") or "").strip() or None
    target = resolve_model_endpoint(db, "chat", requested_model=requested)
    if target is None or (requested is not None and target.profile.model != requested):
        raise ScheduleServiceError("model_unavailable", "所选模型当前不可用")
    resolved = dict(config)
    resolved["model"] = target.profile.model
    return resolved


def _validate_target(
    db: Session,
    owner_user_id: str,
    operation: str,
    target_schedule_id: str | None,
    target_revision: int | None,
) -> None:
    if operation == "create":
        if target_schedule_id is not None or target_revision is not None:
            raise ScheduleServiceError(
                "target_invalid", "创建草案不能指定目标计划", status_code=422
            )
        return
    if not target_schedule_id or target_revision is None:
        raise ScheduleServiceError(
            "target_invalid", "管理草案缺少目标计划及版本", status_code=422
        )
    schedule = repository.owned_schedule(db, target_schedule_id, owner_user_id)
    if schedule is None:
        raise ScheduleServiceError("not_found", "定时任务不存在", status_code=404)
    if schedule.revision != target_revision:
        raise ScheduleServiceError(
            "revision_conflict", "目标任务已更新，请重新生成修改草案"
        )


def create_draft(
    db: Session,
    owner_user_id: str,
    config: dict,
    request_id: str,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
    operation: str = "create",
    target_schedule_id: str | None = None,
    target_revision: int | None = None,
    *,
    skill_request_id: str | None = None,
) -> ScheduleDraft:
    """建立持久草案；同 owner/request 仅能代表完全相同的请求。"""
    request_id = request_id.strip()
    if not request_id or len(request_id) > 128:
        raise ScheduleServiceError("request_invalid", "请求标识无效", status_code=422)
    canonical = _validate_config(config)
    _validate_source(db, owner_user_id, source_session_id, source_message_id)
    _validate_target(db, owner_user_id, operation, target_schedule_id, target_revision)
    stopping = operation in {"pause", "archive"}
    # 停用不读取资料或执行能力；依赖失效时仍应允许 owner 停止自己的任务。
    validate_schedule_scope(db, owner_user_id, {} if stopping else canonical)
    if not stopping:
        _validate_notification_targets(db, owner_user_id, canonical)
    status = "draft"
    blocked_reason = None
    if canonical["kind"] == "skill" and not stopping:
        if _skill_request_pending(canonical, skill_request_id):
            status = "waiting_skill"
            blocked_reason = "skill_required"
        else:
            try:
                canonical = resolve_schedule_skill(canonical)
                canonical = _resolve_model(db, canonical)
            except (ScheduleAccessError, ScheduleServiceError) as exc:
                if (
                    exc.code not in {"skill_required", "model_unavailable"}
                    and not skill_request_id
                ):
                    raise
                status = "waiting_skill"
                blocked_reason = exc.code
    existing = db.scalar(
        select(ScheduleDraft).where(
            ScheduleDraft.owner_user_id == owner_user_id,
            ScheduleDraft.request_id == request_id,
        )
    )
    if existing is not None:
        same = (
            existing.config_json == _dump(canonical)
            and existing.source_session_id == source_session_id
            and existing.source_message_id == source_message_id
            and existing.operation == operation
            and existing.target_schedule_id == target_schedule_id
            and existing.target_revision == target_revision
            and existing.skill_request_id == skill_request_id
        )
        if not same:
            raise ScheduleServiceError("request_conflict", "请求标识已用于其他草案")
        return existing
    timestamp = now()
    draft = ScheduleDraft(
        id=f"sdr_{uuid4()}",
        owner_user_id=owner_user_id,
        source_session_id=source_session_id,
        source_message_id=source_message_id,
        request_id=request_id,
        revision=1,
        status=status,
        config_json=_dump(canonical),
        skill_request_id=skill_request_id,
        expires_at=timestamp + _DRAFT_TTL,
        operation=operation,
        target_schedule_id=target_schedule_id,
        target_revision=target_revision,
        blocked_reason=blocked_reason,
        created_at=timestamp,
        updated_at=timestamp,
    )
    try:
        with db.begin_nested():
            db.add(draft)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(ScheduleDraft).where(
                ScheduleDraft.owner_user_id == owner_user_id,
                ScheduleDraft.request_id == request_id,
            )
        )
        if existing is None:
            raise
        same = (
            existing.config_json == _dump(canonical)
            and existing.source_session_id == source_session_id
            and existing.source_message_id == source_message_id
            and existing.operation == operation
            and existing.target_schedule_id == target_schedule_id
            and existing.target_revision == target_revision
            and existing.skill_request_id == skill_request_id
        )
        if not same:
            raise ScheduleServiceError("request_conflict", "请求标识已用于其他草案")
        return existing
    return draft


def attach_skill_request(
    db: Session,
    draft: ScheduleDraft,
    *,
    skill_request_id: str,
    expected_revision: int,
) -> ScheduleDraft:
    if draft.revision != expected_revision or draft.status in {
        "confirmed",
        "cancelled",
        "expired",
    }:
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    if not skill_request_id or len(skill_request_id) > 64:
        raise ScheduleServiceError(
            "skill_request_invalid", "能力申请标识无效", status_code=422
        )
    changed = db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.owner_user_id == draft.owner_user_id,
            ScheduleDraft.revision == expected_revision,
        )
        .values(
            skill_request_id=skill_request_id,
            status="waiting_skill",
            blocked_reason="skill_unavailable",
            preview_token=None,
            preview_expires_at=None,
            revision=expected_revision + 1,
            updated_at=now(),
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    db.flush()
    refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("草案更新后不存在")
    return refreshed


def refresh_draft_dependency(
    db: Session, draft: ScheduleDraft, *, candidate_config: dict | None = None
) -> ScheduleDraft:
    if draft.status != "waiting_skill":
        return draft
    observed_revision = draft.revision
    observed_config_json = draft.config_json
    config = (
        candidate_config
        if candidate_config is not None
        else _load_object(observed_config_json, field="草案配置")
    )
    try:
        if _skill_request_pending(config, draft.skill_request_id):
            raise ScheduleAccessError("skill_required", "能力尚未批准并绑定")
        resolved = _resolve_model(db, resolve_schedule_skill(config))
        validate_schedule_scope(db, draft.owner_user_id, resolved)
    except (ScheduleAccessError, ScheduleServiceError) as exc:
        db.execute(
            update(ScheduleDraft)
            .where(
                ScheduleDraft.id == draft.id,
                ScheduleDraft.owner_user_id == draft.owner_user_id,
                ScheduleDraft.status == "waiting_skill",
                ScheduleDraft.revision == observed_revision,
                ScheduleDraft.config_json == observed_config_json,
            )
            .values(blocked_reason=exc.code)
            .execution_options(synchronize_session=False)
        )
        refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
        if refreshed is None:
            raise RuntimeError("草案刷新后不存在")
        return refreshed
    db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.owner_user_id == draft.owner_user_id,
            ScheduleDraft.status == "waiting_skill",
            ScheduleDraft.revision == observed_revision,
            ScheduleDraft.config_json == observed_config_json,
        )
        .values(
            config_json=_dump(resolved),
            status="draft",
            blocked_reason=None,
            preview_token=None,
            preview_expires_at=None,
            revision=observed_revision + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("草案刷新后不存在")
    return refreshed


def cancel_draft(
    db: Session,
    draft: ScheduleDraft,
    *,
    expected_revision: int,
    request_id: str,
) -> ScheduleDraft:
    resource_key = f"draft:{draft.id}:cancel"
    replay = repository.operation_result(
        db, draft.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        return draft
    if draft.status == "confirmed" or draft.schedule_id is not None:
        raise ScheduleServiceError(
            "state_conflict", "草案已确认，不能用取消草案来停用计划"
        )
    if draft.status == "cancelled":
        raise ScheduleServiceError("state_conflict", "草案已经取消")
    if draft.revision != expected_revision:
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    timestamp = now()
    changed = db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.owner_user_id == draft.owner_user_id,
            ScheduleDraft.revision == expected_revision,
            ScheduleDraft.schedule_id.is_(None),
            ScheduleDraft.status.not_in(("confirmed", "cancelled")),
        )
        .values(
            status="cancelled",
            blocked_reason=None,
            preview_token=None,
            preview_expires_at=None,
            revision=expected_revision + 1,
            updated_at=timestamp,
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        replay = repository.operation_result(
            db, draft.owner_user_id, resource_key, request_id
        )
        if replay is None:
            raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
        refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
        if refreshed is None:
            raise RuntimeError("草案取消后不存在")
        if refreshed.status != "cancelled":
            raise ScheduleServiceError(
                "state_conflict", "草案已确认，不能用取消草案来停用计划"
            )
        return refreshed
    refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("草案取消后不存在")
    if refreshed.status != "cancelled":
        raise ScheduleServiceError(
            "state_conflict", "草案已确认，不能用取消草案来停用计划"
        )
    repository.record_operation(
        db,
        owner_user_id=draft.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="draft",
        result_id=draft.id,
        result_revision=refreshed.revision,
    )
    return refreshed


def update_draft(
    db: Session,
    draft: ScheduleDraft,
    *,
    expected_revision: int,
    config: dict,
    request_id: str,
) -> ScheduleDraft:
    resource_key = f"draft:{draft.id}:patch"
    replay = repository.operation_result(
        db, draft.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        if replay.result_revision != draft.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且草案后来又有更新"
            )
        return draft
    if draft.revision != expected_revision or draft.status in {
        "confirmed",
        "cancelled",
        "expired",
    }:
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    canonical = _validate_config(config)
    stopping = draft.operation in {"pause", "archive"}
    validate_schedule_scope(db, draft.owner_user_id, {} if stopping else canonical)
    if not stopping:
        _validate_notification_targets(db, draft.owner_user_id, canonical)
    status = "draft"
    blocked_reason = None
    if canonical["kind"] == "skill" and not stopping:
        if _skill_request_pending(canonical, draft.skill_request_id):
            status = "waiting_skill"
            blocked_reason = "skill_required"
        else:
            try:
                canonical = _resolve_model(db, resolve_schedule_skill(canonical))
            except (ScheduleAccessError, ScheduleServiceError) as exc:
                if (
                    exc.code not in {"skill_required", "model_unavailable"}
                    and not draft.skill_request_id
                ):
                    raise
                status = "waiting_skill"
                blocked_reason = exc.code
    changed = db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.revision == expected_revision,
        )
        .values(
            config_json=_dump(canonical),
            status=status,
            blocked_reason=blocked_reason,
            preview_token=None,
            preview_expires_at=None,
            revision=expected_revision + 1,
            updated_at=now(),
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        replay = repository.operation_result(
            db, draft.owner_user_id, resource_key, request_id
        )
        if replay is None:
            raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
        current = db.get(ScheduleDraft, draft.id, populate_existing=True)
        if current is None or replay.result_revision != current.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且草案后来又有更新"
            )
        return current
    refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("草案更新后不存在")
    draft = refreshed
    repository.record_operation(
        db,
        owner_user_id=draft.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="draft",
        result_id=draft.id,
        result_revision=draft.revision,
    )
    return draft


def _preview_fingerprint(draft: ScheduleDraft, config: dict) -> str:
    payload = {
        "draft_id": draft.id,
        "revision": draft.revision,
        "owner_user_id": draft.owner_user_id,
        "config": config,
        "operation": draft.operation,
        "target_schedule_id": draft.target_schedule_id,
        "target_revision": draft.target_revision,
        "expires_at": iso(draft.expires_at),
    }
    return hashlib.sha256(_dump(payload).encode()).hexdigest()


def _future_occurrence(config: dict, timestamp: datetime) -> datetime:
    occurrence = next_occurrence(config["rule"], config["timezone"], timestamp)
    if occurrence is None:
        raise ScheduleServiceError(
            "schedule_time_passed",
            "指定时间已过去或没有可用触发时间，请修改时间后重新预览",
            status_code=422,
        )
    return occurrence


def preview_draft(
    db: Session, draft: ScheduleDraft, *, expected_revision: int
) -> tuple[ScheduleDraft, list[datetime], list[str]]:
    timestamp = now()
    if draft.status in {"confirmed", "cancelled", "expired"}:
        raise ScheduleServiceError("state_conflict", "当前草案不能重新预览")
    if draft.revision != expected_revision:
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    observed_status = draft.status
    observed_config_json = draft.config_json
    if as_utc(draft.expires_at) <= timestamp:
        expired = db.execute(
            update(ScheduleDraft)
            .where(
                ScheduleDraft.id == draft.id,
                ScheduleDraft.owner_user_id == draft.owner_user_id,
                ScheduleDraft.status == observed_status,
                ScheduleDraft.revision == expected_revision,
                ScheduleDraft.config_json == observed_config_json,
            )
            .values(
                status="expired",
                preview_token=None,
                preview_expires_at=None,
                updated_at=timestamp,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(expired, "rowcount", 0) != 1:
            raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
        db.get(ScheduleDraft, draft.id, populate_existing=True)
        raise ScheduleServiceError("draft_expired", "草案已过期，请重新创建")
    _validate_target(
        db,
        draft.owner_user_id,
        draft.operation,
        draft.target_schedule_id,
        draft.target_revision,
    )
    _validate_source(
        db,
        draft.owner_user_id,
        draft.source_session_id,
        draft.source_message_id,
    )
    config = _load_object(observed_config_json, field="草案配置")
    stopping = draft.operation in {"pause", "archive"}
    requires_future = draft.operation in {"create", "update", "resume"}
    blockers: list[str] = []
    try:
        validate_schedule_scope(db, draft.owner_user_id, {} if stopping else config)
        if not stopping:
            _validate_notification_targets(db, draft.owner_user_id, config)
        if not stopping:
            # 申请中的能力未绑定前不放行：缺能力在这里只是等待，不是提示词任务的失败。
            if _skill_request_pending(config, draft.skill_request_id):
                blockers.append("skill_required")
            else:
                config = resolve_schedule_skill(config)
                config = _resolve_model(db, config)
    except (ScheduleAccessError, ScheduleServiceError) as exc:
        blockers.append(exc.code)
    if blockers:
        values = {
            "status": "waiting_skill",
            "blocked_reason": blockers[0],
            "preview_token": None,
            "preview_expires_at": None,
            "updated_at": timestamp,
        }
        occurrences: list[datetime] = []
    else:
        occurrences = (
            preview_occurrences(config["rule"], config["timezone"], timestamp, count=3)
            if requires_future
            else []
        )
        if not occurrences and requires_future:
            _future_occurrence(config, timestamp)
        values = {
            "config_json": _dump(config),
            "status": "ready",
            "blocked_reason": None,
            "preview_token": _preview_fingerprint(draft, config),
            "preview_expires_at": timestamp + _PREVIEW_TTL,
            "updated_at": timestamp,
        }
    changed = db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.owner_user_id == draft.owner_user_id,
            ScheduleDraft.status == observed_status,
            ScheduleDraft.revision == expected_revision,
            ScheduleDraft.config_json == observed_config_json,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if getattr(changed, "rowcount", 0) != 1:
        db.get(ScheduleDraft, draft.id, populate_existing=True)
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    refreshed = db.get(ScheduleDraft, draft.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("草案预览后不存在")
    return refreshed, occurrences, blockers


def confirm_draft(
    db: Session,
    draft: ScheduleDraft,
    *,
    expected_revision: int,
    preview_token: str | None,
    request_id: str,
    enabled: bool,
) -> Schedule:
    if draft.schedule_id:
        schedule = repository.owned_schedule(db, draft.schedule_id, draft.owner_user_id)
        if schedule is None:
            raise ScheduleServiceError("not_found", "定时任务不存在", status_code=404)
        return schedule
    timestamp = now()
    if draft.revision != expected_revision or draft.status != "ready":
        raise ScheduleServiceError("revision_conflict", "草案需要重新预览")
    if as_utc(draft.expires_at) <= timestamp:
        raise ScheduleServiceError("draft_expired", "草案已过期，请重新创建")
    if (
        not draft.preview_token
        or not preview_token
        or draft.preview_token != preview_token
        or draft.preview_expires_at is None
        or as_utc(draft.preview_expires_at) <= timestamp
    ):
        raise ScheduleServiceError("preview_expired", "预览已失效，请重新预览")
    _validate_source(
        db,
        draft.owner_user_id,
        draft.source_session_id,
        draft.source_message_id,
    )
    config = _load_object(draft.config_json, field="草案配置")
    stopping = draft.operation in {"pause", "archive"}
    if not stopping:
        config = _resolve_model(db, resolve_schedule_skill(config))
    validate_schedule_scope(db, draft.owner_user_id, {} if stopping else config)
    if not stopping:
        _validate_notification_targets(db, draft.owner_user_id, config)
    if _preview_fingerprint(draft, config) != preview_token:
        raise ScheduleServiceError(
            "preview_changed", "能力或资料状态已改变，请重新预览"
        )
    if draft.operation != "create":
        return _confirm_management_draft(
            db, draft, config=config, request_id=request_id, enabled=enabled
        )
    limit = int(settings.schedule_max_per_user)
    existing_count = db.scalar(
        select(func.count())
        .select_from(Schedule)
        .where(
            Schedule.owner_user_id == draft.owner_user_id,
            Schedule.status != "archived",
        )
    )
    if existing_count is not None and int(existing_count) >= limit:
        raise ScheduleServiceError(
            "schedule_limit_reached",
            f"定时任务数量已达上限（最多 {limit} 个），请先归档不再使用的任务",
            status_code=422,
        )
    schedule_id = f"sch_{uuid4()}"
    next_run_at = _future_occurrence(config, timestamp)
    status = "enabled" if enabled else "paused"
    schedule = Schedule(
        id=schedule_id,
        owner_user_id=draft.owner_user_id,
        name=config["name"],
        kind=config["kind"],
        status=status,
        revision=1,
        config_json=_dump(config),
        next_run_at=next_run_at if enabled else None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    # 先建立被引用行，避免 SQLite 的立即外键检查拒绝草案绑定。
    db.add(schedule)
    db.flush()
    claimed = db.execute(
        update(ScheduleDraft)
        .where(
            ScheduleDraft.id == draft.id,
            ScheduleDraft.owner_user_id == draft.owner_user_id,
            ScheduleDraft.revision == expected_revision,
            ScheduleDraft.status == "ready",
            ScheduleDraft.schedule_id.is_(None),
        )
        .values(
            status="confirmed",
            schedule_id=schedule_id,
            preview_token=None,
            preview_expires_at=None,
            updated_at=timestamp,
        )
    )
    if getattr(claimed, "rowcount", 0) != 1:
        db.delete(schedule)
        db.flush()
        current = db.get(ScheduleDraft, draft.id, populate_existing=True)
        if current is not None and current.schedule_id:
            existing = repository.owned_schedule(
                db, current.schedule_id, draft.owner_user_id
            )
            if existing is not None:
                return existing
        raise ScheduleServiceError("revision_conflict", "草案已更新，请刷新后重试")
    # 条件 UPDATE 已写入绑定，刷新旧 identity-map 对象，保证同事务后续重放也可回读。
    db.refresh(draft)
    repository.record_operation(
        db,
        owner_user_id=draft.owner_user_id,
        resource_key=f"draft:{draft.id}:confirm",
        request_id=request_id,
        result_type="schedule",
        result_id=schedule.id,
        result_revision=schedule.revision,
    )
    return schedule


def _confirm_management_draft(
    db: Session,
    draft: ScheduleDraft,
    *,
    config: dict,
    request_id: str,
    enabled: bool,
) -> Schedule:
    del enabled
    schedule = repository.owned_schedule(
        db, str(draft.target_schedule_id), draft.owner_user_id
    )
    if schedule is None:
        raise ScheduleServiceError("not_found", "定时任务不存在", status_code=404)
    if draft.target_revision != schedule.revision:
        raise ScheduleServiceError(
            "revision_conflict", "目标任务已更新，请重新生成修改草案"
        )
    if draft.operation == "update":
        schedule = update_schedule(
            db,
            schedule,
            expected_revision=schedule.revision,
            config=config,
            request_id=request_id,
            allow_external_change=True,
        )
    elif draft.operation == "pause":
        schedule = transition_schedule(
            db, schedule, "pause", schedule.revision, request_id
        )
    elif draft.operation == "resume":
        schedule = transition_schedule(
            db, schedule, "resume", schedule.revision, request_id
        )
    elif draft.operation == "archive":
        schedule = transition_schedule(
            db, schedule, "archive", schedule.revision, request_id
        )
    elif draft.operation == "run_now":
        run_now(
            db, schedule, expected_revision=schedule.revision, request_id=request_id
        )
    draft.status = "confirmed"
    draft.schedule_id = schedule.id
    draft.preview_token = None
    draft.preview_expires_at = None
    draft.updated_at = now()
    db.flush()
    return schedule


def update_schedule(
    db: Session,
    schedule: Schedule,
    *,
    expected_revision: int,
    config: dict,
    request_id: str,
    allow_external_change: bool = False,
) -> Schedule:
    resource_key = f"schedule:{schedule.id}:patch"
    replay = repository.operation_result(
        db, schedule.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        if replay.result_revision != schedule.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且任务后来又有更新"
            )
        return schedule
    if schedule.revision != expected_revision or schedule.status == "archived":
        raise ScheduleServiceError("revision_conflict", "任务已更新，请刷新后重试")
    canonical = _resolve_model(db, resolve_schedule_skill(_validate_config(config)))
    if not allow_external_change:
        current_external = (
            _load_object(schedule.config_json, field="任务配置")
            .get("notification", {})
            .get("external", [])
        )
        next_external = canonical["notification"]["external"]
        if current_external != next_external:
            raise ScheduleServiceError(
                "notification_preview_required",
                "外部通知目标变更需要先预览并确认草案",
            )
    validate_schedule_scope(db, schedule.owner_user_id, canonical)
    _validate_notification_targets(db, schedule.owner_user_id, canonical)
    timestamp = now()
    next_run_at = (
        _future_occurrence(canonical, timestamp)
        if schedule.status == "enabled"
        else None
    )
    changed = db.execute(
        update(Schedule)
        .where(Schedule.id == schedule.id, Schedule.revision == expected_revision)
        .values(
            name=canonical["name"],
            kind=canonical["kind"],
            config_json=_dump(canonical),
            next_run_at=next_run_at,
            revision=expected_revision + 1,
            updated_at=timestamp,
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        replay = repository.operation_result(
            db, schedule.owner_user_id, resource_key, request_id
        )
        if replay is None:
            raise ScheduleServiceError("revision_conflict", "任务已更新，请刷新后重试")
        current = db.get(Schedule, schedule.id, populate_existing=True)
        if current is None or replay.result_revision != current.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且任务后来又有更新"
            )
        return current
    db.execute(
        update(ScheduleRun)
        .where(
            ScheduleRun.schedule_id == schedule.id,
            ScheduleRun.status == "queued",
            ScheduleRun.schedule_revision == expected_revision,
        )
        .values(
            status="skipped",
            status_revision=ScheduleRun.status_revision + 1,
            completed_at=timestamp,
            updated_at=timestamp,
            error_code="schedule_updated",
            error_message_safe="任务设置已更新，本次尚未开始的执行已跳过。",
        )
    )
    refreshed = db.get(Schedule, schedule.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("任务更新后不存在")
    schedule = refreshed
    repository.record_operation(
        db,
        owner_user_id=schedule.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="schedule",
        result_id=schedule.id,
        result_revision=schedule.revision,
    )
    return schedule


def transition_schedule(
    db: Session,
    schedule: Schedule,
    action: str,
    expected_revision: int,
    request_id: str,
) -> Schedule:
    resource_key = f"schedule:{schedule.id}:{action}"
    replay = repository.operation_result(
        db, schedule.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        if replay.result_revision != schedule.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且任务后来又有更新"
            )
        return schedule
    if schedule.revision != expected_revision:
        raise ScheduleServiceError("revision_conflict", "任务已更新，请刷新后重试")
    transitions = {
        "pause": ({"enabled", "blocked"}, "paused"),
        "resume": ({"paused", "blocked"}, "enabled"),
        "archive": ({"enabled", "paused", "blocked", "completed"}, "archived"),
    }
    allowed, target = transitions[action]
    if schedule.status not in allowed:
        raise ScheduleServiceError("state_conflict", "当前状态不能执行此操作")
    timestamp = now()
    config = _load_object(schedule.config_json, field="任务配置")
    if action == "resume":
        validate_schedule_scope(db, schedule.owner_user_id, config)
        config = _resolve_model(db, resolve_schedule_skill(config))
        next_run_at = _future_occurrence(config, timestamp)
    elif action in {"pause", "archive"}:
        next_run_at = None
    else:
        next_run_at = schedule.next_run_at
    changed = db.execute(
        update(Schedule)
        .where(Schedule.id == schedule.id, Schedule.revision == expected_revision)
        .values(
            status=target,
            next_run_at=next_run_at,
            blocked_reason=None,
            revision=expected_revision + 1,
            updated_at=timestamp,
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        replay = repository.operation_result(
            db, schedule.owner_user_id, resource_key, request_id
        )
        if replay is None:
            raise ScheduleServiceError("revision_conflict", "任务已更新，请刷新后重试")
        current = db.get(Schedule, schedule.id, populate_existing=True)
        if current is None or replay.result_revision != current.revision:
            raise ScheduleServiceError(
                "request_conflict", "请求已处理且任务后来又有更新"
            )
        return current
    refreshed = db.get(Schedule, schedule.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("任务更新后不存在")
    schedule = refreshed
    repository.record_operation(
        db,
        owner_user_id=schedule.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="schedule",
        result_id=schedule.id,
        result_revision=schedule.revision,
    )
    return schedule


def run_now(
    db: Session,
    schedule: Schedule,
    *,
    expected_revision: int,
    request_id: str,
) -> ScheduleRun:
    resource_key = f"schedule:{schedule.id}:run-now"
    replay = repository.operation_result(
        db, schedule.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        run = db.get(ScheduleRun, replay.result_id)
        if run is None:
            raise RuntimeError("幂等执行记录不存在")
        return run
    # 条件空写在 SQLite 上取得数据库写锁，在 PostgreSQL 上锁住目标行；
    # PATCH 因而要么看见本次 queued run 并将其跳过，要么先提交后使这里版本冲突。
    locked = db.execute(
        update(Schedule)
        .where(
            Schedule.id == schedule.id,
            Schedule.owner_user_id == schedule.owner_user_id,
            Schedule.revision == expected_revision,
        )
        .values(revision=Schedule.revision)
        .execution_options(synchronize_session=False)
    )
    if getattr(locked, "rowcount", 0) != 1:
        replay = repository.operation_result(
            db, schedule.owner_user_id, resource_key, request_id
        )
        if replay is not None:
            run = db.get(ScheduleRun, replay.result_id)
            if run is not None:
                return run
        raise ScheduleServiceError("revision_conflict", "任务已更新，请刷新后重试")
    refreshed = db.get(Schedule, schedule.id, populate_existing=True)
    if refreshed is None:
        raise ScheduleServiceError("not_found", "定时任务不存在", status_code=404)
    # 同一 request_id 可能在等待数据库锁期间由另一请求完成；重放仍优先于状态判断。
    replay = repository.operation_result(
        db, schedule.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        run = db.get(ScheduleRun, replay.result_id)
        if run is None:
            raise RuntimeError("幂等执行记录不存在")
        return run
    schedule = refreshed
    if schedule.status not in {"enabled", "paused", "completed"}:
        raise ScheduleServiceError("state_conflict", "当前状态不能立即执行")
    active = db.scalar(
        select(ScheduleRun.id)
        .where(
            ScheduleRun.schedule_id == schedule.id,
            ScheduleRun.status.in_(_ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    if active is not None:
        raise ScheduleServiceError("overlap_blocked", "已有一次执行尚未结束")
    config = _resolve_model(
        db, resolve_schedule_skill(_load_object(schedule.config_json, field="任务配置"))
    )
    validate_schedule_scope(db, schedule.owner_user_id, config)
    timestamp = now()
    run = ScheduleRun(
        id=f"srn_{uuid4()}",
        schedule_id=schedule.id,
        owner_user_id=schedule.owner_user_id,
        trigger_kind="manual",
        occurrence_key=f"manual:{request_id}",
        scheduled_for=timestamp,
        config_json=_dump(config),
        schedule_revision=schedule.revision,
        status="queued",
        status_revision=1,
        created_at=timestamp,
        updated_at=timestamp,
        next_attempt_at=timestamp,
    )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
            repository.record_operation(
                db,
                owner_user_id=schedule.owner_user_id,
                resource_key=resource_key,
                request_id=request_id,
                result_type="run",
                result_id=run.id,
                result_revision=run.status_revision,
            )
    except IntegrityError:
        replay = repository.operation_result(
            db, schedule.owner_user_id, resource_key, request_id
        )
        if replay is not None:
            existing = db.get(ScheduleRun, replay.result_id)
            if existing is not None:
                return existing
        raise ScheduleServiceError("overlap_blocked", "已有一次执行尚未结束") from None
    return run


def cancel_run(db: Session, run: ScheduleRun, *, request_id: str) -> ScheduleRun:
    resource_key = f"run:{run.id}:cancel"
    replay = repository.operation_result(
        db, run.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        refreshed = db.get(ScheduleRun, run.id, populate_existing=True)
        if refreshed is None:
            raise RuntimeError("执行记录不存在")
        return refreshed
    timestamp = now()
    observed_status = run.status
    observed_revision = run.status_revision
    if observed_status in {"queued", "awaiting_user", "outcome_unknown"}:
        values = {
            "status": "cancelled",
            "status_revision": ScheduleRun.status_revision + 1,
            "cancel_requested": True,
            "completed_at": timestamp,
            "updated_at": timestamp,
            "retryable": False,
        }
    elif observed_status == "running":
        values = {"cancel_requested": True, "updated_at": timestamp}
    elif observed_status == "cancelled":
        values = None
    else:
        raise ScheduleServiceError("state_conflict", "当前执行不能取消")
    if values is not None:
        changed = db.execute(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run.id,
                ScheduleRun.owner_user_id == run.owner_user_id,
                ScheduleRun.status == observed_status,
                ScheduleRun.status_revision == observed_revision,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if getattr(changed, "rowcount", 0) != 1:
            replay = repository.operation_result(
                db, run.owner_user_id, resource_key, request_id
            )
            if replay is None:
                raise ScheduleServiceError(
                    "state_conflict", "执行状态已更新，请刷新后重试"
                )
    refreshed = db.get(ScheduleRun, run.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("执行记录不存在")
    run = refreshed
    repository.record_operation(
        db,
        owner_user_id=run.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="run",
        result_id=run.id,
        result_revision=run.status_revision,
    )
    return run


def retry_run(db: Session, run: ScheduleRun, *, request_id: str) -> ScheduleRun:
    resource_key = f"run:{run.id}:retry"
    replay = repository.operation_result(
        db, run.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        return run
    if run.status != "failed" or not run.retryable:
        raise ScheduleServiceError("retry_unsafe", "本次执行不能安全重试")
    config = _load_object(run.config_json, field="执行配置")
    if run.attempt_no >= int(config.get("max_attempts") or 1):
        raise ScheduleServiceError("attempts_exhausted", "本次执行已达到重试上限")
    validate_schedule_scope(db, run.owner_user_id, config)
    resolve_schedule_skill(config)
    timestamp = now()
    run.status = "queued"
    run.status_revision += 1
    run.next_attempt_at = timestamp
    run.completed_at = None
    run.error_code = None
    run.error_message_safe = None
    run.retryable = False
    run.cancel_requested = False
    run.lease_owner = None
    run.lease_until = None
    run.updated_at = timestamp
    db.flush()
    repository.record_operation(
        db,
        owner_user_id=run.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="run",
        result_id=run.id,
        result_revision=run.status_revision,
    )
    return run


def handle_run(db: Session, run: ScheduleRun, *, request_id: str) -> ScheduleRun:
    resource_key = f"run:{run.id}:handle"
    replay = repository.operation_result(
        db, run.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        return run
    config = _load_object(run.config_json, field="执行配置")
    if config.get("kind") != "reminder" or run.status != "completed":
        raise ScheduleServiceError("state_conflict", "只有已触发的提醒可以标记处理")
    if run.handled_at is None:
        handled_at = now()
        changed = db.execute(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run.id,
                ScheduleRun.status == "completed",
                ScheduleRun.handled_at.is_(None),
                ScheduleRun.status_revision == run.status_revision,
            )
            .values(
                handled_at=handled_at,
                status_revision=ScheduleRun.status_revision + 1,
                updated_at=handled_at,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise ScheduleServiceError("state_conflict", "提醒状态已变化，请刷新后重试")
        db.refresh(run)
    if run.snoozed_until is None:
        from app.repositories.tasks import acknowledge_tasks

        acknowledge_tasks(
            db, owner_user_id=run.owner_user_id, task_ids=[f"schedule:{run.id}"]
        )
    repository.record_operation(
        db,
        owner_user_id=run.owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type="run",
        result_id=run.id,
        result_revision=run.status_revision,
    )
    return run


def snooze_run(
    db: Session, run: ScheduleRun, *, request_id: str, until: datetime
) -> ScheduleRun:
    resource_key = f"run:{run.id}:snooze"
    replay = repository.operation_result(
        db, run.owner_user_id, resource_key, request_id
    )
    if replay is not None:
        child = db.get(ScheduleRun, replay.result_id)
        if child is None:
            raise RuntimeError("稍后提醒记录不存在")
        return child
    config = _load_object(run.config_json, field="执行配置")
    if (
        config.get("kind") != "reminder"
        or run.status != "completed"
        or run.handled_at
        or run.snoozed_until
    ):
        raise ScheduleServiceError("state_conflict", "当前提醒不能稍后处理")
    if until.tzinfo is None or as_utc(until) <= now():
        raise ScheduleServiceError(
            "snooze_time_invalid", "稍后提醒时间必须晚于当前时间", status_code=422
        )
    timestamp = now()
    child = ScheduleRun(
        id=f"srn_{uuid4()}",
        schedule_id=run.schedule_id,
        owner_user_id=run.owner_user_id,
        trigger_kind="snooze",
        occurrence_key=f"snooze:{run.id}:{request_id}",
        scheduled_for=as_utc(until),
        config_json=run.config_json,
        schedule_revision=run.schedule_revision,
        status="queued",
        status_revision=1,
        parent_run_id=run.id,
        next_attempt_at=as_utc(until),
        created_at=timestamp,
        updated_at=timestamp,
    )
    claimed_parent = False
    try:
        with db.begin_nested():
            claimed = db.execute(
                update(ScheduleRun)
                .where(
                    ScheduleRun.id == run.id,
                    ScheduleRun.owner_user_id == run.owner_user_id,
                    ScheduleRun.status == "completed",
                    ScheduleRun.status_revision == run.status_revision,
                    ScheduleRun.handled_at.is_(None),
                    ScheduleRun.snoozed_until.is_(None),
                )
                .values(
                    snoozed_until=as_utc(until),
                    handled_at=timestamp,
                    status_revision=ScheduleRun.status_revision + 1,
                    updated_at=timestamp,
                )
                .execution_options(synchronize_session=False)
            )
            claimed_parent = getattr(claimed, "rowcount", 0) == 1
            if claimed_parent:
                db.add(child)
                db.flush()
                repository.record_operation(
                    db,
                    owner_user_id=run.owner_user_id,
                    resource_key=resource_key,
                    request_id=request_id,
                    result_type="run",
                    result_id=child.id,
                    result_revision=child.status_revision,
                )
    except IntegrityError:
        replay = repository.operation_result(
            db, run.owner_user_id, resource_key, request_id
        )
        if replay is not None:
            existing = db.get(ScheduleRun, replay.result_id)
            if existing is not None:
                return existing
        raise ScheduleServiceError("state_conflict", "当前提醒不能稍后处理") from None
    if not claimed_parent:
        replay = repository.operation_result(
            db, run.owner_user_id, resource_key, request_id
        )
        if replay is not None:
            existing = db.get(ScheduleRun, replay.result_id)
            if existing is not None:
                return existing
        raise ScheduleServiceError("state_conflict", "当前提醒不能稍后处理")
    refreshed = db.get(ScheduleRun, run.id, populate_existing=True)
    if refreshed is None:
        raise RuntimeError("原提醒记录不存在")
    return child


def draft_response(db: Session, draft: ScheduleDraft) -> dict:
    if draft.status == "waiting_skill":
        draft = refresh_draft_dependency(db, draft)
    return {
        "id": draft.id,
        "revision": draft.revision,
        "status": draft.status,
        "config": _load_object(draft.config_json, field="草案配置"),
        "source_session_id": draft.source_session_id,
        "source_message_id": draft.source_message_id,
        "skill_request_id": draft.skill_request_id,
        "schedule_id": draft.schedule_id,
        "expires_at": iso(draft.expires_at),
        "blocked_reason": draft.blocked_reason,
        "operation": draft.operation,
        "target_schedule_id": draft.target_schedule_id,
        "target_revision": draft.target_revision,
    }


def run_response(db: Session, run: ScheduleRun, owner_user_id: str) -> dict:
    if not can_access_schedule_run(db, run, owner_user_id):
        raise ScheduleServiceError("not_found", "执行记录不存在", status_code=404)
    schedule = repository.owned_schedule(db, run.schedule_id, owner_user_id)
    if schedule is None:
        raise ScheduleServiceError("not_found", "执行记录不存在", status_code=404)
    attempts = repository.attempts_for_run(db, run.id)
    return {
        "id": run.id,
        "schedule_id": run.schedule_id,
        "kind": schedule.kind,
        "status": run.status,
        "status_revision": run.status_revision,
        "scheduled_for": iso(run.scheduled_for),
        "started_at": iso(run.started_at) if run.started_at else None,
        "completed_at": iso(run.completed_at) if run.completed_at else None,
        "created_at": iso(run.created_at),
        "updated_at": iso(run.updated_at),
        "body": run.body,
        "files": _load_list(run.result_files_json),
        "usage": _load_object(run.usage_json, field="用量") if run.usage_json else None,
        "progress": _load_list(run.progress_json),
        "error_code": run.error_code,
        "error_message_safe": run.error_message_safe,
        "retryable": run.retryable,
        "handled_at": iso(run.handled_at) if run.handled_at else None,
        "snoozed_until": iso(run.snoozed_until) if run.snoozed_until else None,
        "resource_path": f"/schedules/{run.schedule_id}/runs/{run.id}",
        "attempts": [
            {
                "attempt_no": item.attempt_no,
                "status": item.status,
                "started_at": iso(item.started_at),
                "completed_at": iso(item.completed_at) if item.completed_at else None,
                "error_code": item.error_code,
            }
            for item in attempts
        ],
        "config": _load_object(run.config_json, field="执行配置"),
    }


def schedule_response(db: Session, schedule: Schedule, owner_user_id: str) -> dict:
    if schedule.owner_user_id != owner_user_id:
        raise ScheduleServiceError("not_found", "定时任务不存在", status_code=404)
    recent = repository.latest_run(db, schedule.id)
    latest_response = None
    if recent is not None:
        try:
            latest_response = run_response(db, recent, owner_user_id)
        except ScheduleServiceError as exc:
            if exc.code != "not_found":
                raise
    # 稍后提醒是一次性排队运行。排队期间 single-flight 会把它当作活动运行，周期时间
    # 即便更早到点也只会被判为重叠跳过，所以下一次真正会执行的就是这次提醒。
    pending_snooze = repository.pending_snooze_run(db, schedule.id)
    pending_snooze_until = (
        as_utc(pending_snooze.scheduled_for) if pending_snooze is not None else None
    )
    next_run_at = (
        pending_snooze_until
        if pending_snooze_until is not None
        else schedule.next_run_at
    )
    return {
        "id": schedule.id,
        "name": schedule.name,
        "kind": schedule.kind,
        "status": schedule.status,
        "revision": schedule.revision,
        "config": _load_object(schedule.config_json, field="任务配置"),
        "next_run_at": iso(next_run_at) if next_run_at else None,
        "pending_snooze_until": (
            iso(pending_snooze_until) if pending_snooze_until else None
        ),
        "blocked_reason": schedule.blocked_reason,
        "created_at": iso(schedule.created_at),
        "updated_at": iso(schedule.updated_at),
        "latest_run": latest_response,
        "resource_path": f"/schedules/{schedule.id}",
    }


def list_drafts(
    db: Session, owner_user_id: str, *, session_id: str | None = None
) -> list[ScheduleDraft]:
    statement = select(ScheduleDraft).where(
        ScheduleDraft.owner_user_id == owner_user_id
    )
    if session_id is not None:
        statement = statement.where(ScheduleDraft.source_session_id == session_id)
    return list(
        db.scalars(
            statement.order_by(desc(ScheduleDraft.created_at), desc(ScheduleDraft.id))
        )
    )
