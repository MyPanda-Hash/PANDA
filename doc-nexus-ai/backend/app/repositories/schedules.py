from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.security import now as utc_now
from app.models.notification_delivery import (
    NotificationConnection,
    NotificationDelivery,
    NotificationPreference,
    NotificationTarget,
    NotificationTargetGrant,
)
from app.models.schedules import (
    Schedule,
    ScheduleDraft,
    ScheduleNotification,
    ScheduleNotificationCursor,
    ScheduleOperation,
    ScheduleRun,
    ScheduleRunAttempt,
)

logger = logging.getLogger(__name__)


def owned_draft(db: Session, draft_id: str, owner_user_id: str) -> ScheduleDraft | None:
    return db.scalar(
        select(ScheduleDraft).where(
            ScheduleDraft.id == draft_id,
            ScheduleDraft.owner_user_id == owner_user_id,
        )
    )


def owned_schedule(
    db: Session, schedule_id: str, owner_user_id: str
) -> Schedule | None:
    return db.scalar(
        select(Schedule).where(
            Schedule.id == schedule_id,
            Schedule.owner_user_id == owner_user_id,
        )
    )


def owned_run(
    db: Session, schedule_id: str, run_id: str, owner_user_id: str
) -> ScheduleRun | None:
    return db.scalar(
        select(ScheduleRun).where(
            ScheduleRun.id == run_id,
            ScheduleRun.schedule_id == schedule_id,
            ScheduleRun.owner_user_id == owner_user_id,
        )
    )


def latest_run(db: Session, schedule_id: str) -> ScheduleRun | None:
    return db.scalar(
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id)
        .order_by(desc(ScheduleRun.created_at), desc(ScheduleRun.id))
        .limit(1)
    )


def pending_snooze_run(db: Session, schedule_id: str) -> ScheduleRun | None:
    """尚未触发的一次性稍后提醒；single-flight 保证同一计划最多一条排队记录。"""
    return db.scalar(
        select(ScheduleRun).where(
            ScheduleRun.schedule_id == schedule_id,
            ScheduleRun.trigger_kind == "snooze",
            ScheduleRun.status == "queued",
        )
    )


def attempts_for_run(db: Session, run_id: str) -> list[ScheduleRunAttempt]:
    return list(
        db.scalars(
            select(ScheduleRunAttempt)
            .where(ScheduleRunAttempt.run_id == run_id)
            .order_by(ScheduleRunAttempt.attempt_no)
        )
    )


def operation_result(
    db: Session, owner_user_id: str, resource_key: str, request_id: str
) -> ScheduleOperation | None:
    return db.scalar(
        select(ScheduleOperation).where(
            ScheduleOperation.owner_user_id == owner_user_id,
            ScheduleOperation.resource_key == resource_key,
            ScheduleOperation.request_id == request_id,
        )
    )


def record_operation(
    db: Session,
    *,
    owner_user_id: str,
    resource_key: str,
    request_id: str,
    result_type: str,
    result_id: str,
    result_revision: int | None,
    created_at: datetime | None = None,
) -> ScheduleOperation:
    row = ScheduleOperation(
        id=f"sop_{uuid4()}",
        owner_user_id=owner_user_id,
        resource_key=resource_key,
        request_id=request_id,
        result_type=result_type,
        result_id=result_id,
        result_revision=result_revision,
        created_at=created_at or utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _lock_notification_cursor(db: Session, owner_user_id: str) -> None:
    values = {"owner_user_id": owner_user_id, "last_sequence": 0}
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(ScheduleNotificationCursor).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["owner_user_id"])
    elif dialect == "sqlite":
        statement = sqlite_insert(ScheduleNotificationCursor).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["owner_user_id"])
    else:
        existing = db.get(ScheduleNotificationCursor, owner_user_id)
        if existing is None:
            db.add(ScheduleNotificationCursor(**values))
            db.flush()
        statement = None
    if statement is not None:
        # SQLite 的写语句取得写锁；PG 再显式锁定 owner 计数行。
        db.execute(statement)
    if dialect == "postgresql":
        db.scalar(
            select(ScheduleNotificationCursor)
            .where(ScheduleNotificationCursor.owner_user_id == owner_user_id)
            .with_for_update()
        )


def _notification_sequence(db: Session, owner_user_id: str) -> int:
    sequence = db.scalar(
        update(ScheduleNotificationCursor)
        .where(ScheduleNotificationCursor.owner_user_id == owner_user_id)
        .values(last_sequence=ScheduleNotificationCursor.last_sequence + 1)
        .returning(ScheduleNotificationCursor.last_sequence)
    )
    if sequence is None:
        raise RuntimeError("通知游标分配失败")
    return int(sequence)

def _enqueue_external_deliveries(
    db: Session, run: ScheduleRun, event: ScheduleNotification,
    notification: dict, schedule_kind: str
) -> None:
    # 仅使用运行时已保存的白名单，绝不把指令、正文或结果拼进外发内容。
    if run.handled_at is not None:
        return
    if run.status not in {"completed", "failed", "awaiting_user", "outcome_unknown"}:
        return
    preference = db.get(NotificationPreference, run.owner_user_id)
    if preference is None or not preference.external_enabled:
        return
    external = notification.get("external")
    if not isinstance(external, list):
        return
    labels = {
        "completed": "已完成",
        "failed": "执行失败",
        "awaiting_user": "等待用户处理",
        "outcome_unknown": "执行结果待核实",
    }
    text_content = f"定时任务状态：{labels[run.status]}。请登录系统查看结果。"
    for policy in external:
        if not isinstance(policy, dict) or policy.get("content_mode") != "status_only":
            continue
        if run.status not in policy.get("events", ()):
            continue
        target_id = policy.get("target_id")
        target_revision = policy.get("target_revision")
        if not isinstance(target_id, str) or type(target_revision) is not int:
            continue
        target = db.get(NotificationTarget, target_id)
        if (
            target is None
            or target.revision != target_revision
            or target.status != "active"
            or target.verified_at is None
            or target.consent_at is None
        ):
            continue
        connection = db.get(NotificationConnection, target.connection_id)
        if connection is None or connection.status != "active":
            continue
        if target.kind == "email":
            authorized = target.owner_user_id == run.owner_user_id and connection.kind == "smtp"
        else:
            authorized = (
                target.owner_user_id is None
                and target.kind == connection.kind
                and db.get(NotificationTargetGrant, (target.id, run.owner_user_id)) is not None
            )
        if not authorized:
            continue
        values = dict(
            id=f"ndl_{uuid4()}",
            event_id=event.id,
            owner_user_id=run.owner_user_id,
            target_id=target.id,
            target_revision=target.revision,
            target_label_snapshot=target.label,
            event_status_revision=run.status_revision,
            content_text=text_content,
            status="pending",
            next_attempt_at=event.created_at,
            lease_generation=0,
            attempts=0,
            expires_at=event.created_at + (
                timedelta(minutes=15) if schedule_kind == "reminder" and run.status == "completed"
                else timedelta(hours=24)
            ),
            created_at=event.created_at,
            updated_at=event.created_at,
        )
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(NotificationDelivery).values(**values)
        elif dialect == "postgresql":
            statement = pg_insert(NotificationDelivery).values(**values)
        else:
            raise RuntimeError("unsupported notification delivery dialect")
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=["event_id", "target_id"]
            )
        )


def emit_notification(
    db: Session, run: ScheduleRun, *, now: datetime | None = None
) -> ScheduleNotification | None:
    """在结果事务内幂等写入安全通知，并按 owner 串行分配游标。"""
    terminal_statuses = {
        "completed",
        "failed",
        "cancelled",
        "awaiting_user",
        "outcome_unknown",
        "skipped",
    }
    if run.status not in terminal_statuses:
        return None
    # 先按 owner 串行，再查幂等记录；并发提交不会产生可跳过的序号。
    _lock_notification_cursor(db, run.owner_user_id)
    existing = db.scalar(
        select(ScheduleNotification).where(
            ScheduleNotification.run_id == run.id,
            ScheduleNotification.status_revision == run.status_revision,
            ScheduleNotification.event_kind == "status",
        )
    )
    if existing is not None:
        run.notification_revision = run.status_revision
        return existing

    schedule = db.get(Schedule, run.schedule_id)
    if schedule is None or schedule.owner_user_id != run.owner_user_id:
        raise ValueError("运行记录未绑定有效计划")
    try:
        config = json.loads(run.config_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("运行配置无效") from exc
    notification = config.get("notification")
    if not isinstance(notification, dict):
        notification = {}
    is_success = run.status == "completed"
    announce = run.handled_at is None and (
        schedule.kind == "reminder"
        or (is_success and bool(notification.get("on_success")))
        or (not is_success and bool(notification.get("on_failure", True)))
    )
    if schedule.kind == "reminder" and is_success:
        title = "提醒已触发"
    elif is_success:
        title = "定时任务已完成"
    elif run.status in {"awaiting_user", "outcome_unknown"}:
        title = "定时任务需要处理"
    elif run.status == "failed":
        title = "定时任务执行失败"
    else:
        title = "定时任务状态已更新"
    created_at = now or utc_now()
    sequence = _notification_sequence(db, run.owner_user_id)
    event = ScheduleNotification(
        id=f"snt_{uuid4()}",
        owner_user_id=run.owner_user_id,
        event_id=f"schedule:{run.id}:{run.status_revision}:status",
        sequence=sequence,
        run_id=run.id,
        schedule_id=run.schedule_id,
        status_revision=run.status_revision,
        event_kind="status",
        status=run.status,
        title=title,
        resource_path=f"/schedules/{run.schedule_id}/runs/{run.id}",
        created_at=created_at,
        expires_at=(run.completed_at or run.updated_at) + timedelta(days=7),
        announce=announce,
        sound=announce and bool(notification.get("sound")),
        desktop=announce and bool(notification.get("desktop")),
    )
    run.notification_revision = run.status_revision
    db.add(event)
    db.flush()
    _enqueue_external_deliveries(db, run, event, notification, schedule.kind)
    db.flush()
    return event


def emit_notification_safely(
    db: Session, run: ScheduleRun, *, now: datetime | None = None
) -> bool:
    """通知写入失败时仅回滚保存点，不影响同事务中的业务状态。"""
    try:
        with db.begin_nested():
            emit_notification(db, run, now=now)
        return True
    except Exception:
        logger.exception("schedule notification persistence failed: run=%s", run.id)
        return False


def notification_query(owner_user_id: str, after_sequence: int) -> Select:
    return (
        select(ScheduleNotification)
        .where(
            ScheduleNotification.owner_user_id == owner_user_id,
            ScheduleNotification.sequence > after_sequence,
        )
        .order_by(ScheduleNotification.sequence)
    )


def run_response(db: Session, run: ScheduleRun, owner_user_id: str) -> dict:
    """兼容运行器/投影调用的单一安全响应入口。"""
    from app.services.schedules import run_response as build_run_response

    return build_run_response(db, run, owner_user_id)
