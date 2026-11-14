"""Database-coordinated admission for Pi child processes.

Every API or writing worker process sharing the deployment database participates
in the same bounded queue. A dedicated guard row serializes limit decisions on
both SQLite and PostgreSQL; active executions are individual renewable leases.
This does not coordinate deployments that use different databases.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import logging
import os
import socket
import threading
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import as_utc, now
from app.models.execution_admission import (
    PiExecutionAdmissionGuard,
    PiExecutionAdmissionLease,
)

logger = logging.getLogger(__name__)

_GUARD_ID = 1
_LEASE_SECONDS = 3600
_HEARTBEAT_SECONDS = 10.0
_POLL_SECONDS = 0.25
_DB_CLEANUP_TIMEOUT_SECONDS = 5.0
_RECONCILE_SECONDS = 2.0
_DB_ATTEMPTS = 5

AdmissionCallback = Callable[[int], Any]
CancelCheck = Callable[[], Any]


class PiAdmissionError(RuntimeError):
    """Base class for stable admission failures."""

    code = "pi_execution_admission_unavailable"


class PiAdmissionQueueFull(PiAdmissionError):
    code = "pi_execution_queue_full"


class PiAdmissionQueueTimeout(PiAdmissionError):
    code = "pi_execution_queue_timeout"


class PiAdmissionCancelled(PiAdmissionError):
    code = "pi_execution_cancelled"


class PiAdmissionClosed(PiAdmissionError):
    code = "pi_execution_shutting_down"


class PiAdmissionLeaseLost(PiAdmissionError):
    code = "pi_execution_admission_lost"


@dataclass(frozen=True, slots=True)
class PiAdmissionLimits:
    max_concurrency: int
    max_per_user: int
    max_waiters: int
    queue_timeout_seconds: float

    @classmethod
    def from_settings(cls) -> PiAdmissionLimits:
        total = settings.pi_max_concurrency
        return cls(
            max_concurrency=total,
            max_per_user=min(total, settings.pi_max_concurrency_per_user),
            max_waiters=settings.pi_queue_max_waiters,
            queue_timeout_seconds=float(settings.pi_queue_timeout_seconds),
        )


@dataclass(frozen=True, slots=True)
class _AdmissionStatus:
    status: str
    position: int | None = None


class _AdmissionLeaseStore:
    """Small synchronous repository used through ``asyncio.to_thread``."""

    def _ensure_guard(self) -> None:
        db = SessionLocal()
        try:
            if db.get(PiExecutionAdmissionGuard, _GUARD_ID) is not None:
                return
            db.add(
                PiExecutionAdmissionGuard(
                    id=_GUARD_ID,
                    revision=0,
                    updated_at=now(),
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        finally:
            db.close()

    @staticmethod
    def _lock_guard(db: Session) -> None:
        stamp = now()
        locked = db.execute(
            update(PiExecutionAdmissionGuard)
            .where(PiExecutionAdmissionGuard.id == _GUARD_ID)
            .values(
                revision=PiExecutionAdmissionGuard.revision + 1,
                updated_at=stamp,
            )
        )
        if getattr(locked, "rowcount", 0) != 1:
            raise PiAdmissionError("Pi execution admission guard is unavailable")

    def _locked(self, operation: Callable[[Session], Any]) -> Any:
        self._ensure_guard()
        last_error: BaseException | None = None
        for attempt in range(_DB_ATTEMPTS):
            db = SessionLocal()
            try:
                self._lock_guard(db)
                result = operation(db)
                db.commit()
                return result
            except (PiAdmissionQueueFull, PiAdmissionQueueTimeout):
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                last_error = exc
            finally:
                db.close()
            if attempt + 1 < _DB_ATTEMPTS:
                import time

                time.sleep(0.02 * (attempt + 1))
        raise PiAdmissionError("Pi execution admission database is unavailable") from last_error

    @staticmethod
    def _prune(db: Session) -> None:
        stamp = now()
        db.execute(
            delete(PiExecutionAdmissionLease).where(
                or_(
                    (PiExecutionAdmissionLease.status == "waiting")
                    & (PiExecutionAdmissionLease.wait_expires_at <= stamp),
                    (PiExecutionAdmissionLease.status == "active")
                    & (PiExecutionAdmissionLease.lease_expires_at <= stamp),
                )
            )
        )

    @staticmethod
    def _active_rows(db: Session) -> list[PiExecutionAdmissionLease]:
        return list(
            db.scalars(
                select(PiExecutionAdmissionLease).where(
                    PiExecutionAdmissionLease.status == "active"
                )
            )
        )

    @classmethod
    def _promote(cls, db: Session, limits: PiAdmissionLimits) -> None:
        active = cls._active_rows(db)
        counts = Counter(item.user_key for item in active)
        available = limits.max_concurrency - len(active)
        if available <= 0:
            return
        waiting = list(
            db.scalars(
                select(PiExecutionAdmissionLease)
                .where(PiExecutionAdmissionLease.status == "waiting")
                .order_by(
                    PiExecutionAdmissionLease.queued_at,
                    PiExecutionAdmissionLease.id,
                )
            )
        )
        stamp = now()
        for item in waiting:
            if available <= 0:
                break
            if counts[item.user_key] >= limits.max_per_user:
                continue
            item.status = "active"
            item.lease_expires_at = stamp + timedelta(seconds=_LEASE_SECONDS)
            item.updated_at = stamp
            counts[item.user_key] += 1
            available -= 1

    @staticmethod
    def _status_in_transaction(
        db: Session, ticket_id: str
    ) -> _AdmissionStatus:
        ticket = db.get(PiExecutionAdmissionLease, ticket_id)
        if ticket is None:
            raise PiAdmissionQueueTimeout("Pi execution queue wait timed out")
        if ticket.status == "active":
            if (
                ticket.lease_expires_at is None
                or as_utc(ticket.lease_expires_at) <= now()
            ):
                raise PiAdmissionLeaseLost(
                    "Pi execution admission lease expired before execution"
                )
            return _AdmissionStatus("active")
        earlier = db.scalar(
            select(func.count(PiExecutionAdmissionLease.id)).where(
                PiExecutionAdmissionLease.status == "waiting",
                or_(
                    PiExecutionAdmissionLease.queued_at < ticket.queued_at,
                    (PiExecutionAdmissionLease.queued_at == ticket.queued_at)
                    & (PiExecutionAdmissionLease.id <= ticket.id),
                ),
            )
        )
        return _AdmissionStatus("waiting", int(earlier or 1))

    def enqueue(
        self,
        *,
        ticket_id: str,
        user_key: str,
        lease_owner: str,
        limits: PiAdmissionLimits,
    ) -> _AdmissionStatus:
        def operation(db: Session) -> _AdmissionStatus:
            self._prune(db)
            self._promote(db, limits)
            active = self._active_rows(db)
            waiting_count = int(
                db.scalar(
                    select(func.count(PiExecutionAdmissionLease.id)).where(
                        PiExecutionAdmissionLease.status == "waiting"
                    )
                )
                or 0
            )
            user_active = sum(1 for item in active if item.user_key == user_key)
            stamp = now()
            if (
                waiting_count == 0
                and len(active) < limits.max_concurrency
                and user_active < limits.max_per_user
            ):
                status = "active"
                lease_expires_at = stamp + timedelta(seconds=_LEASE_SECONDS)
            else:
                if waiting_count >= limits.max_waiters:
                    raise PiAdmissionQueueFull("Pi execution queue is full")
                status = "waiting"
                lease_expires_at = None
            db.add(
                PiExecutionAdmissionLease(
                    id=ticket_id,
                    user_key=user_key,
                    lease_owner=lease_owner,
                    status=status,
                    queued_at=stamp,
                    wait_expires_at=stamp
                    + timedelta(seconds=limits.queue_timeout_seconds),
                    lease_expires_at=lease_expires_at,
                    updated_at=stamp,
                )
            )
            db.flush()
            if status == "waiting":
                self._promote(db, limits)
            return self._status_in_transaction(db, ticket_id)

        return self._locked(operation)

    def status(self, *, ticket_id: str) -> _AdmissionStatus:
        db = SessionLocal()
        try:
            ticket = db.get(PiExecutionAdmissionLease, ticket_id)
            if ticket is None or (
                ticket.status == "waiting"
                and as_utc(ticket.wait_expires_at) <= now()
            ):
                raise PiAdmissionQueueTimeout("Pi execution queue wait timed out")
            return self._status_in_transaction(db, ticket_id)
        finally:
            db.close()

    def reconcile(self, *, limits: PiAdmissionLimits) -> None:
        def operation(db: Session) -> None:
            self._prune(db)
            self._promote(db, limits)

        self._locked(operation)

    def renew(self, *, ticket_id: str) -> bool:
        stamp = now()
        db = SessionLocal()
        try:
            renewed = db.execute(
                update(PiExecutionAdmissionLease)
                .where(
                    PiExecutionAdmissionLease.id == ticket_id,
                    PiExecutionAdmissionLease.status == "active",
                    PiExecutionAdmissionLease.lease_expires_at > stamp,
                )
                .values(
                    lease_expires_at=stamp + timedelta(seconds=_LEASE_SECONDS),
                    updated_at=stamp,
                )
            )
            db.commit()
            return getattr(renewed, "rowcount", 0) == 1
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def remove(self, *, ticket_id: str, limits: PiAdmissionLimits) -> None:
        def operation(db: Session) -> None:
            self._prune(db)
            db.execute(
                delete(PiExecutionAdmissionLease).where(
                    PiExecutionAdmissionLease.id == ticket_id
                )
            )
            self._promote(db, limits)

        self._locked(operation)


class PiAdmissionPermit:
    def __init__(
        self, controller: DatabasePiAdmissionController, ticket_id: str
    ) -> None:
        self._controller = controller
        self.ticket_id = ticket_id
        self._released = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._lost = asyncio.Event()

    def start(self) -> None:
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat(), name=f"pi-admission-{self.ticket_id[:8]}"
        )

    async def _heartbeat(self) -> None:
        loop = asyncio.get_running_loop()
        valid_until = loop.time() + _LEASE_SECONDS
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            while True:
                try:
                    renewed = await asyncio.to_thread(
                        self._controller._store.renew,
                        ticket_id=self.ticket_id,
                    )
                except Exception:
                    logger.warning("pi admission heartbeat failed", exc_info=True)
                    renewed = False
                if renewed:
                    valid_until = loop.time() + _LEASE_SECONDS
                    break
                if loop.time() >= valid_until:
                    self._lost.set()
                    return
                await asyncio.sleep(1.0)

    async def wait_lost(self) -> None:
        await self._lost.wait()

    def abandon(self) -> None:
        self._lost.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        heartbeat = self._heartbeat_task
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._controller._store.remove,
                    ticket_id=self.ticket_id,
                    limits=self._controller.limits,
                ),
                timeout=_DB_CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception:
            # A failed release is bounded by the renewable lease; do not hang
            # request or shutdown paths on a database outage.
            logger.warning("pi admission release deferred to lease expiry", exc_info=True)
        finally:
            self._controller._released(self.ticket_id)


class DatabasePiAdmissionController:
    def __init__(
        self,
        limits: PiAdmissionLimits | None = None,
        *,
        store: _AdmissionLeaseStore | None = None,
    ) -> None:
        self.limits = limits or PiAdmissionLimits.from_settings()
        self._store = store or _AdmissionLeaseStore()
        self._state_lock = threading.Lock()
        self._closed = False
        self._waiting: set[str] = set()
        self._permits: dict[str, PiAdmissionPermit] = {}

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise PiAdmissionClosed("Pi execution admission is shutting down")

    def _track_waiting(self, ticket_id: str) -> None:
        with self._state_lock:
            if self._closed:
                raise PiAdmissionClosed("Pi execution admission is shutting down")
            self._waiting.add(ticket_id)

    def _track_active(self, permit: PiAdmissionPermit) -> None:
        with self._state_lock:
            self._waiting.discard(permit.ticket_id)
            if self._closed:
                raise PiAdmissionClosed("Pi execution admission is shutting down")
            self._permits[permit.ticket_id] = permit

    def _released(self, ticket_id: str) -> None:
        with self._state_lock:
            self._waiting.discard(ticket_id)
            self._permits.pop(ticket_id, None)

    async def _remove_ticket(self, ticket_id: str) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._store.remove,
                    ticket_id=ticket_id,
                    limits=self.limits,
                ),
                timeout=_DB_CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("pi admission queued ticket cleanup failed", exc_info=True)
        finally:
            self._released(ticket_id)

    async def _cancel_requested(self, cancel_check: CancelCheck) -> None:
        while True:
            try:
                result = cancel_check()
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    return
            except Exception:
                logger.warning("pi admission cancel check failed", exc_info=True)
            await asyncio.sleep(_POLL_SECONDS)

    async def acquire(
        self,
        *,
        user_id: str,
        cancel_check: CancelCheck | None = None,
        on_waiting: AdmissionCallback | None = None,
    ) -> PiAdmissionPermit:
        self._ensure_open()
        ticket_id = uuid.uuid4().hex
        normalized_user = (user_id or "anonymous").strip() or "anonymous"
        user_key = hashlib.sha256(normalized_user.encode("utf-8")).hexdigest()
        lease_owner = f"{socket.gethostname()}:{os.getpid()}:{ticket_id}"
        enqueued = False
        cancel_task: asyncio.Task[None] | None = None
        try:
            state = await asyncio.to_thread(
                self._store.enqueue,
                ticket_id=ticket_id,
                user_key=user_key,
                lease_owner=lease_owner,
                limits=self.limits,
            )
            enqueued = True
            if state.status == "waiting":
                self._track_waiting(ticket_id)
                if on_waiting is not None:
                    callback_result = on_waiting(state.position or 1)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                if cancel_check is not None:
                    cancel_task = asyncio.create_task(
                        self._cancel_requested(cancel_check),
                        name=f"pi-admission-cancel-{ticket_id[:8]}",
                    )
                deadline = (
                    asyncio.get_running_loop().time()
                    + self.limits.queue_timeout_seconds
                )
                next_reconcile = asyncio.get_running_loop().time()
                while state.status != "active":
                    self._ensure_open()
                    current = asyncio.get_running_loop().time()
                    remaining = deadline - current
                    if remaining <= 0:
                        raise PiAdmissionQueueTimeout(
                            "Pi execution queue wait timed out"
                        )
                    sleeper = asyncio.create_task(
                        asyncio.sleep(min(_POLL_SECONDS, remaining))
                    )
                    waits: set[asyncio.Task[Any]] = {sleeper}
                    if cancel_task is not None:
                        waits.add(cancel_task)
                    done, _ = await asyncio.wait(
                        waits, return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_task is not None and cancel_task in done:
                        sleeper.cancel()
                        await asyncio.gather(sleeper, return_exceptions=True)
                        raise PiAdmissionCancelled(
                            "Pi turn cancelled while waiting for execution"
                        )
                    state = await asyncio.to_thread(
                        self._store.status,
                        ticket_id=ticket_id,
                    )
                    current = asyncio.get_running_loop().time()
                    if (
                        state.status == "waiting"
                        and (state.position or 1) <= self.limits.max_concurrency
                        and current >= next_reconcile
                    ):
                        await asyncio.to_thread(
                            self._store.reconcile,
                            limits=self.limits,
                        )
                        next_reconcile = current + _RECONCILE_SECONDS
                        state = await asyncio.to_thread(
                            self._store.status,
                            ticket_id=ticket_id,
                        )
            permit = PiAdmissionPermit(self, ticket_id)
            self._track_active(permit)
            permit.start()
            return permit
        except BaseException:
            if enqueued:
                cleanup = asyncio.create_task(self._remove_ticket(ticket_id))
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(cleanup)
            raise
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    async def close(self, timeout_seconds: float) -> None:
        deadline = (
            asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
        )
        with self._state_lock:
            self._closed = True
            waiting = tuple(self._waiting)
        if waiting:
            cleanup = asyncio.gather(
                *(self._remove_ticket(ticket_id) for ticket_id in waiting),
                return_exceptions=True,
            )
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=remaining)
        while True:
            with self._state_lock:
                permits = tuple(self._permits.values())
            if not permits:
                return
            if asyncio.get_running_loop().time() >= deadline:
                for permit in permits:
                    permit.abandon()
                return
            await asyncio.sleep(0.05)


_GLOBAL_LOCK = threading.Lock()
_GLOBAL_CONTROLLER: DatabasePiAdmissionController | None = None


def global_pi_admission() -> DatabasePiAdmissionController:
    global _GLOBAL_CONTROLLER
    with _GLOBAL_LOCK:
        if _GLOBAL_CONTROLLER is None:
            _GLOBAL_CONTROLLER = DatabasePiAdmissionController()
        return _GLOBAL_CONTROLLER


async def shutdown_pi_admission(timeout_seconds: float) -> None:
    global _GLOBAL_CONTROLLER
    with _GLOBAL_LOCK:
        controller = _GLOBAL_CONTROLLER
    if controller is None:
        return
    await controller.close(timeout_seconds)
    with _GLOBAL_LOCK:
        if _GLOBAL_CONTROLLER is controller:
            _GLOBAL_CONTROLLER = None
