from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from sqlalchemy import func, or_, select, text, update

from app.core.database import transaction
from app.core.security import now as utc_now
from app.models.schedules import Schedule, ScheduleRun, ScheduleRunAttempt
from app.models.auth import User
from app.repositories import schedules as schedule_repo
from app.services.schedule_executor import ClaimedScheduleRun, ScheduleExecutor
from app.services.schedule_time import latest_occurrence, next_occurrence

logger = logging.getLogger(__name__)
_ACTIVE_RUN_STATUSES = ("queued", "running", "awaiting_user", "outcome_unknown")


def default_schedule_worker_id() -> str:
    configured = os.getenv("DOC_NEXUS_SCHEDULE_WORKER_ID", "").strip()
    return configured or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _config(value: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("schedule config must be an object")
    return parsed


def _occurrence_key(value: datetime) -> str:
    return _aware_utc(value).isoformat(timespec="minutes").replace("+00:00", "Z")


def _run_id() -> str:
    return f"srn_{uuid4()}"


def _attempt_id() -> str:
    return f"sat_{uuid4()}"


class ScheduleDispatcher:
    """短事务派发与长执行分离的持久调度器。"""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        scan_interval_seconds: float = 1.0,
        lease_seconds: int = 90,
        execution_concurrency: int = 2,
        per_owner_concurrency: int = 1,
    ) -> None:
        self.worker_id = worker_id or default_schedule_worker_id()
        self.scan_interval_seconds = max(0.1, scan_interval_seconds)
        self.lease_seconds = max(30, lease_seconds)
        self.execution_concurrency = max(1, execution_concurrency)
        self.per_owner_concurrency = max(1, per_owner_concurrency)
        self.executor = ScheduleExecutor(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    def dispatch_due_once(self, *, at: datetime | None = None, limit: int = 50) -> int:
        stamp = _aware_utc(at or utc_now())
        dispatched = 0
        with transaction() as db:
            statement = (
                select(Schedule)
                .where(
                    Schedule.status == "enabled",
                    Schedule.next_run_at.is_not(None),
                    Schedule.next_run_at <= stamp,
                )
                .order_by(Schedule.next_run_at.asc(), Schedule.id.asc())
                .limit(max(1, limit))
            )
            if db.get_bind().dialect.name != "sqlite":
                statement = statement.with_for_update(of=Schedule, skip_locked=True)
            schedules = list(db.scalars(statement))
            for schedule in schedules:
                original_due = schedule.next_run_at
                if original_due is None:
                    continue
                due = _aware_utc(original_due)
                try:
                    config = _config(schedule.config_json)
                    rule = config["rule"]
                    timezone_name = str(config["timezone"])
                    latest, missed_count = latest_occurrence(
                        rule,
                        timezone_name,
                        due,
                        stamp,
                    )
                except (KeyError, TypeError, ValueError):
                    schedule.status = "blocked"
                    schedule.blocked_reason = "schedule_rule_invalid"
                    schedule.updated_at = stamp
                    continue
                selected = latest or due
                next_run_at = next_occurrence(rule, timezone_name, selected)
                update_values: dict = {
                    "next_run_at": next_run_at,
                    "updated_at": stamp,
                }
                lateness = max(0.0, (stamp - selected).total_seconds())
                window_seconds = (
                    max(0, int(config.get("late_window_minutes") or 0)) * 60
                )
                overlapping = db.scalar(
                    select(ScheduleRun.id)
                    .where(
                        ScheduleRun.schedule_id == schedule.id,
                        ScheduleRun.status.in_(_ACTIVE_RUN_STATUSES),
                    )
                    .limit(1)
                )
                status = "queued"
                error_code: str | None = None
                error_message: str | None = None
                if overlapping is not None:
                    status = "skipped"
                    error_code = "overlap_skipped"
                    error_message = "上一次运行尚未结束，本次已跳过"
                elif lateness > window_seconds:
                    status = "skipped"
                    error_code = "late_window_exceeded"
                    error_message = "已超过允许的补执行时间，本次已跳过"
                if schedule.kind == "reminder" and status == "queued":
                    status = "completed"
                if rule.get("kind") == "once" and status in {"completed", "skipped"}:
                    update_values.update(status="completed", blocked_reason=None)
                result = db.execute(
                    update(Schedule)
                    .where(
                        Schedule.id == schedule.id,
                        Schedule.status == "enabled",
                        Schedule.next_run_at == original_due,
                    )
                    .values(**update_values)
                )
                if not getattr(result, "rowcount", 0):
                    continue
                occurrence_key = _occurrence_key(selected)
                run = ScheduleRun(
                    id=_run_id(),
                    schedule_id=schedule.id,
                    owner_user_id=schedule.owner_user_id,
                    trigger_kind="scheduled",
                    occurrence_key=occurrence_key,
                    scheduled_for=selected,
                    config_json=schedule.config_json,
                    schedule_revision=schedule.revision,
                    status=status,
                    status_revision=1,
                    body=str(config.get("instruction") or "")
                    if schedule.kind == "reminder"
                    else None,
                    result_files_json="[]",
                    usage_json=None,
                    progress_json=json.dumps(
                        [
                            {
                                "message": (
                                    f"停机期间合并 {missed_count} 个触发，仅处理最近一次"
                                    if missed_count > 1
                                    else "按计划触发"
                                )
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    attempt_no=0,
                    claim_epoch=0,
                    next_attempt_at=stamp if status == "queued" else None,
                    started_at=stamp if status == "completed" else None,
                    completed_at=stamp if status in {"completed", "skipped"} else None,
                    created_at=stamp,
                    updated_at=stamp,
                    error_code=error_code,
                    error_message_safe=error_message,
                    retryable=False,
                    cancel_requested=False,
                )
                db.add(run)
                db.flush()
                if status in {"completed", "skipped"}:
                    schedule_repo.emit_notification_safely(db, run, now=stamp)
                dispatched += 1
        return dispatched

    def dispatch_queued_reminders_once(
        self, *, at: datetime | None = None, limit: int = 50
    ) -> int:
        stamp = _aware_utc(at or utc_now())
        completed = 0
        with transaction() as db:
            statement = (
                select(ScheduleRun)
                .where(
                    ScheduleRun.status == "queued",
                    ScheduleRun.cancel_requested.is_(False),
                    or_(
                        ScheduleRun.next_attempt_at.is_(None),
                        ScheduleRun.next_attempt_at <= stamp,
                    ),
                )
                .order_by(
                    ScheduleRun.next_attempt_at.asc(),
                    ScheduleRun.created_at.asc(),
                )
                .limit(max(1, limit))
            )
            if db.get_bind().dialect.name != "sqlite":
                statement = statement.with_for_update(of=ScheduleRun, skip_locked=True)
            for run in db.scalars(statement):
                try:
                    config = _config(run.config_json)
                except (TypeError, ValueError):
                    continue
                if config.get("kind") != "reminder":
                    continue
                result = db.execute(
                    update(ScheduleRun)
                    .where(
                        ScheduleRun.id == run.id,
                        ScheduleRun.status == "queued",
                        ScheduleRun.cancel_requested.is_(False),
                    )
                    .values(
                        status="completed",
                        status_revision=ScheduleRun.status_revision + 1,
                        body=str(config.get("instruction") or ""),
                        started_at=func.coalesce(ScheduleRun.started_at, stamp),
                        completed_at=stamp,
                        next_attempt_at=None,
                        updated_at=stamp,
                        retryable=False,
                        error_code=None,
                        error_message_safe=None,
                    )
                )
                if not getattr(result, "rowcount", 0):
                    continue
                db.flush()
                db.refresh(run)
                schedule_repo.emit_notification_safely(db, run, now=stamp)
                completed += 1
        return completed

    def recover_expired(self, *, at: datetime | None = None, limit: int = 100) -> int:
        stamp = _aware_utc(at or utc_now())
        recovered = 0
        with transaction() as db:
            statement = (
                select(ScheduleRun)
                .where(
                    ScheduleRun.status == "running",
                    ScheduleRun.lease_until.is_not(None),
                    ScheduleRun.lease_until < stamp,
                )
                .order_by(ScheduleRun.lease_until.asc())
                .limit(max(1, limit))
            )
            if db.get_bind().dialect.name != "sqlite":
                statement = statement.with_for_update(of=ScheduleRun, skip_locked=True)
            for run in db.scalars(statement):
                attempt = db.scalar(
                    select(ScheduleRunAttempt).where(
                        ScheduleRunAttempt.run_id == run.id,
                        ScheduleRunAttempt.attempt_no == run.attempt_no,
                        ScheduleRunAttempt.claim_epoch == run.claim_epoch,
                    )
                )
                phase = attempt.phase if attempt is not None else None
                try:
                    max_attempts = max(
                        1,
                        min(
                            3,
                            int(_config(run.config_json).get("max_attempts") or 1),
                        ),
                    )
                except (TypeError, ValueError):
                    max_attempts = 1
                safe_to_retry = (
                    phase in {"claimed", "preflight"} and run.attempt_no < max_attempts
                )
                run.status_revision += 1
                run.claim_epoch += 1
                run.lease_owner = None
                run.lease_until = None
                run.updated_at = stamp
                if attempt is not None:
                    attempt.completed_at = stamp
                if run.cancel_requested:
                    run.status = "cancelled"
                    run.completed_at = stamp
                    run.error_code = "cancelled"
                    run.error_message_safe = "执行已取消"
                    if attempt is not None:
                        attempt.status = "cancelled"
                        attempt.error_code = "cancelled"
                    schedule_repo.emit_notification_safely(db, run, now=stamp)
                elif safe_to_retry:
                    run.status = "queued"
                    run.next_attempt_at = stamp + timedelta(
                        seconds=min(60, 2**run.attempt_no)
                    )
                    run.error_code = "lease_expired_safe_retry"
                    run.error_message_safe = "执行资源中断，将在安全范围内重试"
                    run.retryable = True
                    if attempt is not None:
                        attempt.status = "failed"
                        attempt.error_code = "lease_expired_safe_retry"
                else:
                    run.status = "outcome_unknown"
                    run.completed_at = stamp
                    run.next_attempt_at = None
                    run.error_code = "lease_expired_outcome_unknown"
                    run.error_message_safe = (
                        "执行中断且外部状态无法确认，已暂停后续自动运行"
                    )
                    run.retryable = False
                    if attempt is not None:
                        attempt.status = "outcome_unknown"
                        attempt.error_code = run.error_code
                    db.execute(
                        update(Schedule)
                        .where(
                            Schedule.id == run.schedule_id,
                            Schedule.status != "archived",
                        )
                        .values(
                            status="blocked",
                            blocked_reason=run.error_code,
                            updated_at=stamp,
                        )
                    )
                    schedule_repo.emit_notification_safely(db, run, now=stamp)
                recovered += 1
        return recovered

    def _running_counts(self, db) -> tuple[int, dict[str, int]]:
        rows = db.execute(
            select(ScheduleRun.owner_user_id, func.count())
            .where(ScheduleRun.status == "running")
            .group_by(ScheduleRun.owner_user_id)
        ).all()
        owners = {str(owner): int(count) for owner, count in rows}
        return sum(owners.values()), owners

    def claim_next(self, *, at: datetime | None = None) -> ClaimedScheduleRun | None:
        stamp = _aware_utc(at or utc_now())
        with transaction() as db:
            dialect = db.get_bind().dialect.name
            if dialect == "postgresql":
                # 所有 PostgreSQL claim 先取同一事务级门闩，再核对系统与用户限额。
                db.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": 491291847731},
                )
            system_running, owner_running = self._running_counts(db)
            if system_running >= self.execution_concurrency:
                return None
            statement = (
                select(ScheduleRun)
                .where(
                    ScheduleRun.status == "queued",
                    ScheduleRun.cancel_requested.is_(False),
                    or_(
                        ScheduleRun.next_attempt_at.is_(None),
                        ScheduleRun.next_attempt_at <= stamp,
                    ),
                )
                .order_by(
                    ScheduleRun.next_attempt_at.asc(),
                    ScheduleRun.created_at.asc(),
                )
                .limit(20)
            )
            if dialect != "sqlite":
                statement = statement.with_for_update(of=ScheduleRun, skip_locked=True)
            candidates = list(db.scalars(statement))
            for candidate in candidates:
                try:
                    config = _config(candidate.config_json)
                    max_attempts = max(1, min(3, int(config.get("max_attempts") or 1)))
                except (TypeError, ValueError):
                    config = {}
                    max_attempts = 1
                if config.get("kind") == "reminder":
                    result = db.execute(
                        update(ScheduleRun)
                        .where(
                            ScheduleRun.id == candidate.id,
                            ScheduleRun.status == "queued",
                            ScheduleRun.cancel_requested.is_(False),
                        )
                        .values(
                            status="completed",
                            status_revision=ScheduleRun.status_revision + 1,
                            body=str(config.get("instruction") or ""),
                            started_at=func.coalesce(ScheduleRun.started_at, stamp),
                            completed_at=stamp,
                            next_attempt_at=None,
                            updated_at=stamp,
                            retryable=False,
                            error_code=None,
                            error_message_safe=None,
                        )
                    )
                    if getattr(result, "rowcount", 0):
                        db.flush()
                        db.refresh(candidate)
                        schedule_repo.emit_notification_safely(db, candidate, now=stamp)
                    continue
                if candidate.attempt_no >= max_attempts:
                    candidate.status = "failed"
                    candidate.status_revision += 1
                    candidate.completed_at = stamp
                    candidate.next_attempt_at = None
                    candidate.retryable = False
                    candidate.error_code = "attempts_exhausted"
                    candidate.error_message_safe = "已达到安全重试上限"
                    candidate.updated_at = stamp
                    schedule_repo.emit_notification_safely(db, candidate, now=stamp)
                    continue
                if dialect == "postgresql":
                    db.scalar(
                        select(User)
                        .where(User.id == candidate.owner_user_id)
                        .with_for_update(of=User)
                    )
                    system_running, owner_running = self._running_counts(db)
                    if system_running >= self.execution_concurrency:
                        return None
                if (
                    owner_running.get(candidate.owner_user_id, 0)
                    >= self.per_owner_concurrency
                ):
                    continue
                system_count = (
                    select(func.count())
                    .select_from(ScheduleRun)
                    .where(ScheduleRun.status == "running")
                    .scalar_subquery()
                )
                owner_count = (
                    select(func.count())
                    .select_from(ScheduleRun)
                    .where(
                        ScheduleRun.status == "running",
                        ScheduleRun.owner_user_id == candidate.owner_user_id,
                    )
                    .scalar_subquery()
                )
                new_epoch = candidate.claim_epoch + 1
                new_attempt = candidate.attempt_no + 1
                result = db.execute(
                    update(ScheduleRun)
                    .where(
                        ScheduleRun.id == candidate.id,
                        ScheduleRun.status == "queued",
                        ScheduleRun.claim_epoch == candidate.claim_epoch,
                        ScheduleRun.attempt_no == candidate.attempt_no,
                        ScheduleRun.cancel_requested.is_(False),
                        system_count < self.execution_concurrency,
                        owner_count < self.per_owner_concurrency,
                    )
                    .values(
                        status="running",
                        status_revision=ScheduleRun.status_revision + 1,
                        claim_epoch=new_epoch,
                        attempt_no=new_attempt,
                        lease_owner=self.worker_id,
                        lease_until=stamp + timedelta(seconds=self.lease_seconds),
                        next_attempt_at=None,
                        started_at=func.coalesce(ScheduleRun.started_at, stamp),
                        updated_at=stamp,
                        retryable=False,
                        error_code=None,
                        error_message_safe=None,
                    )
                )
                if not getattr(result, "rowcount", 0):
                    continue
                db.add(
                    ScheduleRunAttempt(
                        id=_attempt_id(),
                        run_id=candidate.id,
                        attempt_no=new_attempt,
                        claim_epoch=new_epoch,
                        lease_owner=self.worker_id,
                        started_at=stamp,
                        status="running",
                        phase="claimed",
                    )
                )
                db.flush()
                return ClaimedScheduleRun(
                    run_id=candidate.id,
                    claim_epoch=new_epoch,
                    attempt_no=new_attempt,
                    owner_user_id=candidate.owner_user_id,
                )
        return None

    def dispatch_notifications_once(
        self, *, at: datetime | None = None, limit: int = 50
    ) -> int:
        """仅补发待持久化的状态事件，不修改执行状态，也不重新调用模型。"""
        stamp = _aware_utc(at or utc_now())
        delivered = 0
        with transaction() as db:
            statement = (
                select(ScheduleRun)
                .where(
                    ScheduleRun.notification_revision < ScheduleRun.status_revision,
                    ScheduleRun.status.in_(
                        (
                            "completed",
                            "failed",
                            "cancelled",
                            "awaiting_user",
                            "outcome_unknown",
                            "skipped",
                        )
                    ),
                )
                .order_by(ScheduleRun.updated_at, ScheduleRun.id)
                .limit(max(1, limit))
            )
            if db.get_bind().dialect.name == "postgresql":
                statement = statement.with_for_update(of=ScheduleRun, skip_locked=True)
            for run in db.scalars(statement):
                if schedule_repo.emit_notification_safely(db, run, now=stamp):
                    delivered += 1
        return delivered

    async def run_dispatch_once(self) -> bool:
        recovered = await asyncio.to_thread(self.recover_expired)
        reminders = await asyncio.to_thread(self.dispatch_queued_reminders_once)
        dispatched = await asyncio.to_thread(self.dispatch_due_once)
        notified = await asyncio.to_thread(self.dispatch_notifications_once)
        return bool(recovered or reminders or dispatched or notified)

    async def run_execution_once(self) -> bool:
        claim = await asyncio.to_thread(self.claim_next)
        if claim is None:
            return False
        await self.executor.execute(claim)
        return True

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except TimeoutError:
            pass

    async def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("schedule dispatch scan failed")
                worked = False
            await self._sleep(0.1 if worked else self.scan_interval_seconds)

    async def _execution_loop(self, slot: int) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_execution_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("schedule execution consumer failed: slot=%s", slot)
                worked = False
            await self._sleep(0.1 if worked else 0.5)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await asyncio.to_thread(self.recover_expired)
        self._tasks.append(
            asyncio.create_task(self._dispatch_loop(), name="schedule-dispatcher")
        )
        self._tasks.extend(
            asyncio.create_task(
                self._execution_loop(slot), name=f"schedule-executor-{slot}"
            )
            for slot in range(self.execution_concurrency)
        )

    async def close(self) -> None:
        if not self._started:
            return
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False
