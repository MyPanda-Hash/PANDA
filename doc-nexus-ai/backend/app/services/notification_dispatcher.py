from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.core.database import transaction
from app.core.security import now as utc_now
from app.models.auth import User
from app.models.notification_delivery import (
    NotificationConnection,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationPreference,
    NotificationTarget,
    NotificationTargetGrant,
)
from app.models.schedules import ScheduleNotification, ScheduleRun
from app.services.notification_channels import send_notification

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3
_LEASE_SECONDS = 120


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_code(value: str | None) -> str:
    # 外部响应和异常不能成为持久化日志中的密钥或地址。
    return value if value and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", value) else "provider_error"


class NotificationDispatcher:
    """将一次外部请求锁定为单一租约；不确定结果绝不自动重发。"""

    def __init__(self, *, scan_interval_seconds: float = 2.0) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self.scan_interval_seconds = max(0.1, scan_interval_seconds)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self, run_background) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(run_background), name="notification-delivery-worker")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self, run_background) -> None:
        while not self._stop.is_set():
            try:
                worked = await run_background(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notification delivery worker failed")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(self._stop.wait(), self.scan_interval_seconds)
                except asyncio.TimeoutError:
                    pass

    def _claim(self, stamp: datetime) -> tuple[str, int] | None:
        with transaction() as db:
            # sending 表示请求可能已发出；租约失效后只能人工核对，不能二次投递。
            stale = list(db.scalars(select(NotificationDelivery).where(
                NotificationDelivery.status == "sending",
                NotificationDelivery.lease_until <= stamp,
            ).limit(50)))
            for row in stale:
                result = db.execute(update(NotificationDelivery).where(
                    NotificationDelivery.id == row.id,
                    NotificationDelivery.status == "sending",
                    NotificationDelivery.lease_generation == row.lease_generation,
                    NotificationDelivery.lease_until <= stamp,
                ).values(status="unknown", last_error_code="lease_expired", lease_owner=None,
                         lease_until=None, next_attempt_at=None, updated_at=stamp), execution_options={"synchronize_session": False})
                if getattr(result, "rowcount", 0):
                    attempt = db.scalar(select(NotificationDeliveryAttempt).where(
                        NotificationDeliveryAttempt.delivery_id == row.id,
                        NotificationDeliveryAttempt.attempt_no == row.attempts,
                    ))
                    if attempt is not None:
                        attempt.status = "unknown"
                        attempt.error_code = "lease_expired"
            candidates = list(db.scalars(select(NotificationDelivery).where(
                NotificationDelivery.status.in_(("pending", "retry_wait")),
                NotificationDelivery.next_attempt_at <= stamp,
            ).order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id).limit(20)))
            for row in candidates:
                if row.attempts >= _MAX_ATTEMPTS:
                    db.execute(update(NotificationDelivery).where(
                        NotificationDelivery.id == row.id,
                        NotificationDelivery.status.in_(("pending", "retry_wait")),
                    ).values(status="failed", last_error_code="attempt_limit", next_attempt_at=None, updated_at=stamp), execution_options={"synchronize_session": False})
                    continue
                generation = row.lease_generation + 1
                attempt_no = row.attempts + 1
                result = db.execute(update(NotificationDelivery).where(
                    NotificationDelivery.id == row.id,
                    NotificationDelivery.status.in_(("pending", "retry_wait")),
                    NotificationDelivery.lease_generation == row.lease_generation,
                    NotificationDelivery.next_attempt_at <= stamp,
                ).values(status="sending", lease_owner=self.worker_id,
                         lease_generation=generation, lease_until=stamp + timedelta(seconds=_LEASE_SECONDS),
                         attempts=attempt_no, updated_at=stamp), execution_options={"synchronize_session": False})
                if getattr(result, "rowcount", 0):
                    db.add(NotificationDeliveryAttempt(
                        id=f"nda_{uuid4()}", delivery_id=row.id, attempt_no=attempt_no,
                        status="sending", created_at=stamp,
                    ))
                    return row.id, generation
        return None

    def _preflight(self, delivery_id: str, generation: int, stamp: datetime):
        with transaction() as db:
            row = db.get(NotificationDelivery, delivery_id)
            if row is None or row.status != "sending" or row.lease_owner != self.worker_id or row.lease_generation != generation:
                return None
            if row.lease_until is None or _utc(row.lease_until) <= stamp:
                return None
            event = db.get(ScheduleNotification, row.event_id)
            run = db.get(ScheduleRun, event.run_id) if event is not None else None
            target = db.get(NotificationTarget, row.target_id)
            connection = db.get(NotificationConnection, target.connection_id) if target is not None else None
            owner = db.get(User, row.owner_user_id)
            preference = db.get(NotificationPreference, row.owner_user_id)
            authorized = (target is not None and (
                (target.kind == "email" and target.owner_user_id == row.owner_user_id and connection is not None and connection.kind == "smtp")
                or (target.kind != "email" and target.owner_user_id is None
                    and connection is not None and connection.kind == target.kind
                    and db.get(NotificationTargetGrant, (target.id, row.owner_user_id)) is not None)
            ))
            valid = (
                event is not None and run is not None and run.owner_user_id == row.owner_user_id
                and event.status_revision == row.event_status_revision
                and run.status_revision == row.event_status_revision and run.status == event.status
                and run.handled_at is None
                and target is not None and target.revision == row.target_revision
                and target.status == "active" and target.verified_at is not None and target.consent_at is not None
                and connection is not None and connection.status == "active"
                and owner is not None and owner.status == "active"
                and preference is not None and preference.external_enabled
                and authorized and _utc(row.expires_at) > stamp
            )
            if not valid:
                db.execute(update(NotificationDelivery).where(
                    NotificationDelivery.id == delivery_id, NotificationDelivery.status == "sending",
                    NotificationDelivery.lease_generation == generation, NotificationDelivery.lease_owner == self.worker_id,
                ).values(status="suppressed", last_error_code="policy_revoked",
                         next_attempt_at=None, lease_owner=None, lease_until=None, updated_at=stamp), execution_options={"synchronize_session": False})
                attempt = db.scalar(select(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.delivery_id == delivery_id,
                    NotificationDeliveryAttempt.attempt_no == row.attempts,
                ))
                if attempt is not None:
                    attempt.status = "suppressed"
                    attempt.error_code = "policy_revoked"
                return None
            # Expire ORM values before leaving the transaction; immutable detached copies are safer.
            db.expunge(target)
            db.expunge(connection)
            return connection, target, row.content_text

    def _finish(self, delivery_id: str, generation: int, status: str,
                code: str, message_id: str | None, stamp: datetime) -> None:
        with transaction() as db:
            row = db.scalar(select(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == "sending",
                NotificationDelivery.lease_owner == self.worker_id,
                NotificationDelivery.lease_generation == generation,
                NotificationDelivery.lease_until > stamp,
            ))
            if row is None:
                return
            retry = status == "retry_wait" and row.attempts < _MAX_ATTEMPTS and _utc(row.expires_at) > stamp
            final_status = "retry_wait" if retry else ("failed" if status == "retry_wait" else status)
            next_attempt = stamp + timedelta(seconds=30 * 2 ** (row.attempts - 1)) if retry else None
            result = db.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == "sending",
                NotificationDelivery.lease_owner == self.worker_id,
                NotificationDelivery.lease_generation == generation,
                NotificationDelivery.lease_until > stamp,
            ).values(status=final_status, last_error_code=_safe_code(code),
                     provider_message_id=message_id if message_id and len(message_id) <= 255 else None,
                     next_attempt_at=next_attempt, lease_owner=None, lease_until=None, updated_at=stamp), execution_options={"synchronize_session": False})
            if getattr(result, "rowcount", 0):
                attempt = db.scalar(select(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.delivery_id == delivery_id,
                    NotificationDeliveryAttempt.attempt_no == row.attempts,
                ))
                if attempt is not None:
                    attempt.status = final_status
                    attempt.error_code = _safe_code(code)
                    attempt.provider_message_id = message_id if message_id and len(message_id) <= 255 else None

    def run_once(self) -> bool:
        stamp = utc_now()
        claimed = self._claim(stamp)
        if claimed is None:
            return False
        delivery_id, generation = claimed
        prepared = self._preflight(delivery_id, generation, utc_now())
        if prepared is None:
            return True
        connection, target, text = prepared
        try:
            result = send_notification(connection, target, text)
            status = result.status if result.status in {"accepted", "retry_wait", "unknown", "failed"} else "unknown"
            code = result.code
            message_id = result.provider_message_id
        except Exception:
            # 请求可能已抵达提供商，异常不能被当成可安全重试。
            logger.warning("notification provider outcome unknown: delivery=%s", delivery_id)
            status, code, message_id = "unknown", "request_outcome_unknown", None
        self._finish(delivery_id, generation, status, code, message_id, utc_now())
        return True
