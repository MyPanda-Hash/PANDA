"""Independent, bounded durable personal-memory job consumer.

No Pi grant authorizes this worker. Each attempt rechecks the persistent owner,
consent, source, epoch and lease, then performs HTTP outside any DB transaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, transaction
from app.core.security import new_token
from app.models.user_memory import (
    UserMemoryEntry,
    UserMemoryEvidence,
    UserMemoryJob,
    UserMemoryOperation,
)
from app.repositories import user_memory as repo
from app.services.budget import (
    BudgetReservationHandle,
    MemoryBudgetExceeded,
    finalize_memory_budget,
    release_memory_budget,
    reserve_memory_budget,
    settle_memory_budget,
)
from app.services.pricing import fill_usage_cost, find_active_price
from app.services.user_memory import (
    MemoryPolicyError,
    apply_memory_policy,
    default_expiry,
    resolve_turn_source_text,
    validate_memory_proposal,
)
from app.services.user_memory_extractor import (
    ExtractionResult,
    MemoryExtractionError,
    PreparedExtraction,
    extract_memory,
    prepare_extraction,
    unknown_call,
)
from app.services.execution_resources import WorkerExecutionResources

logger = logging.getLogger(__name__)
# All dispatcher objects in a backend share the same in-flight boundary.
_PROCESS_GATE = threading.Lock()


@dataclass(frozen=True, slots=True)
class MemoryAttempt:
    job: UserMemoryJob
    operation_id: str
    source_text: str
    active_versions: dict[str, int]


class _LostAuthority(RuntimeError):
    pass


def _usage(call: dict[str, Any]) -> dict[str, Any]:
    return {**call, "calls": [dict(call)]}


def _priced_call(prepared: PreparedExtraction, call: dict[str, Any]) -> dict[str, Any]:
    result = dict(call)
    if result.get("usage_status") != "reported":
        return result
    with transaction() as db:
        price = find_active_price(
            db, provider=result.get("provider"), model=result.get("model")
        )
        if price is not None:
            fill_usage_cost(db, result)
            result["cost_status"] = "priced"
        else:
            # A token report without a price is not evidence that the call was free.
            result["cost"] = str(prepared.estimated_cost)
            result["cost_status"] = "reserved_unknown"
    return result


class UserMemoryDispatcher:
    def __init__(
        self, *, execution_resources: WorkerExecutionResources | None = None
    ) -> None:
        self.lease_owner = f"memory_{new_token(12)}"
        self._stop = threading.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup = 0.0
        self._execution_resources = execution_resources

    def start(self) -> asyncio.Task[None] | None:
        """Start one lifespan-owned loop; a closed dispatcher cannot restart."""
        if self._stop.is_set() or not settings.personal_memory_enabled:
            return None
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop(), name="personal-memory")
        return self._task

    async def close(self) -> None:
        """Stop claims now; a late HTTP worker can record cost but no memory."""
        self._stop.set()
        self._wake.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task), settings.personal_memory_shutdown_grace_seconds
            )
        except asyncio.TimeoutError:
            # Cancelling an asyncio wrapper does not stop a synchronous HTTP
            # thread. The stop fence prevents that thread from applying results.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run_once(self) -> bool:
        """Claim at most one source and perform one actual provider attempt."""
        if self._stop.is_set() or not settings.personal_memory_enabled:
            return False
        if self._execution_resources is not None:
            return await self._execution_resources.run_background(self._run_once)
        return await asyncio.to_thread(self._run_once)

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Deliberately omit exception strings/tracebacks: HTTP errors may
                # embed sensitive response data or credential-bearing URLs.
                logger.warning(
                    "memory_dispatcher.tick_failed worker=%s", self.lease_owner
                )
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(
                    self._wake.wait(), settings.personal_memory_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    def _run_once(self) -> bool:
        if not _PROCESS_GATE.acquire(blocking=False):
            return False
        try:
            if self._stop.is_set() or not settings.personal_memory_enabled:
                return False
            self._cleanup()
            with SessionLocal() as db:
                job = repo.claim_extraction_job(
                    db,
                    lease_owner=self.lease_owner,
                    lease_seconds=max(
                        120,
                        settings.personal_memory_lease_seconds,
                        int(3 * settings.personal_memory_extract_timeout_seconds + 30),
                    ),
                    max_attempts=settings.personal_memory_max_attempts,
                )
            if job is None:
                return False
            self._execute_job(job)
            return True
        finally:
            _PROCESS_GATE.release()

    def _cleanup(self, *, force: bool = False) -> None:
        stamp = time.monotonic()
        if (
            not force
            and stamp - self._last_cleanup
            < settings.personal_memory_cleanup_interval_seconds
        ):
            return
        with transaction() as db:
            repo.cleanup_memory_data(
                db,
                limit=settings.personal_memory_cleanup_batch_size,
                candidate_retention_days=settings.personal_memory_candidate_retention_days,
                terminal_retention_days=settings.personal_memory_terminal_retention_days,
                max_attempts=settings.personal_memory_max_attempts,
            )
        self._last_cleanup = stamp

    def _begin_attempt(self, job: UserMemoryJob) -> MemoryAttempt:
        if job.extractor_version != settings.personal_memory_extractor_version:
            raise MemoryExtractionError("memory_extractor_version_changed")
        with transaction() as db:
            if (
                self._stop.is_set()
                or not settings.personal_memory_enabled
                or not repo.job_is_still_authorized(db, job)
            ):
                raise _LostAuthority()
            turn = repo.get_owned_turn(
                db, owner_user_id=job.owner_user_id, turn_id=job.turn_id
            )
            if turn is None:
                raise _LostAuthority()
            source = resolve_turn_source_text(db, turn)
            if source is None or repo.source_hash(source) != job.source_hash:
                raise _LostAuthority()
            operation, replay = repo.claim_operation(
                db,
                owner_user_id=job.owner_user_id,
                kind="extract_attempt",
                request_id=f"{job.id}:{job.claim_generation}",
                payload_hash=repo.request_hash(
                    {
                        "job_id": job.id,
                        "source_hash": job.source_hash,
                        "generation": job.claim_generation,
                        "epoch": job.epoch,
                        "extractor_version": job.extractor_version,
                    }
                ),
                turn_id=job.turn_id,
                job_id=job.id,
            )
            if replay:
                # A generation never makes a second provider call, even if an
                # earlier process crashed between billing and result commit.
                raise _LostAuthority()
            active_versions = {
                entry.key: entry.version
                for entry in repo.list_active_entries(
                    db, owner_user_id=job.owner_user_id
                )
            }
            return MemoryAttempt(job, operation.id, source, active_versions)

    def _mark_provider_started(
        self, attempt: MemoryAttempt, call: dict[str, Any]
    ) -> None:
        with transaction() as db:
            if (
                self._stop.is_set()
                or not settings.personal_memory_enabled
                or not repo.job_is_still_authorized(db, attempt.job)
            ):
                raise _LostAuthority()
            operation = db.get(UserMemoryOperation, attempt.operation_id)
            if operation is None or operation.status != "pending":
                raise _LostAuthority()
            # Durable unknown-outcome accounting precedes transmission. A crash
            # cannot turn an unobservable paid attempt into zero cumulative cost.
            operation.usage_json = json.dumps(_usage(call), separators=(",", ":"))

    def _execute_job(self, job: UserMemoryJob) -> None:
        attempt: MemoryAttempt | None = None
        prepared: PreparedExtraction | None = None
        reservation: BudgetReservationHandle | None = None
        provider_started = False
        call: dict[str, Any] | None = None
        error_code: str | None = None
        retryable = False
        result: ExtractionResult | None = None
        try:
            attempt = self._begin_attempt(job)
            prepared = prepare_extraction(
                user_id=job.owner_user_id, source_text=attempt.source_text
            )
            reservation = reserve_memory_budget(
                user_id=job.owner_user_id,
                operation_id=attempt.operation_id,
                provider=prepared.endpoint.provider,
                model=prepared.endpoint.model,
                estimated_cost=prepared.estimated_cost,
            )
            self._mark_provider_started(attempt, unknown_call(prepared))
            if self._stop.is_set():
                raise _LostAuthority()
            provider_started = True
            result = extract_memory(prepared)
            call = result.call
        except _LostAuthority:
            error_code = "memory_source_invalid"
        except MemoryExtractionError as exc:
            error_code, retryable, call = exc.code, exc.retryable, exc.call
        except MemoryBudgetExceeded:
            error_code = "memory_budget_exceeded"
        except Exception:
            error_code = "memory_worker_failed"
        if provider_started and prepared is not None:
            call = call or unknown_call(prepared)
            try:
                call = _priced_call(prepared, call)
            except Exception:
                call["cost"] = str(prepared.estimated_cost)
                call["cost_status"] = "reserved_unknown"
                error_code = error_code or "memory_usage_unavailable"
            if error_code:
                call.update(status="failed", error_code=error_code)
        else:
            call = None
        # Provider cost survives revocation, source deletion and lease loss.
        # Only the memory result is fenced; erasing a bill would undercount spend.
        if reservation is not None:
            try:
                if call is None or call.get("cost_status") == "not_billed":
                    release_memory_budget(reservation.id)
                else:
                    settle_memory_budget(
                        reservation.id,
                        actual_cost=call.get("cost"),
                        uncertain=call.get("cost_status") == "reserved_unknown",
                    )
            except MemoryBudgetExceeded:
                error_code = error_code or "memory_budget_unavailable"
        committed = False
        if (
            attempt is not None
            and error_code is None
            and result is not None
            and call is not None
        ):
            try:
                self._apply_result(attempt, result, call)
                committed = True
            except _LostAuthority:
                error_code = "memory_source_invalid"
            except (MemoryPolicyError, repo.MemoryRepositoryError) as exc:
                error_code = exc.code
            except Exception:
                error_code = "memory_commit_failed"
        if not committed:
            self._finish_failure(
                job, attempt, call, error_code or "memory_worker_failed", retryable
            )
        if reservation is not None and call is not None:
            finalize_memory_budget(reservation.id)
        self._cleanup(force=True)
        logger.info(
            "memory_dispatcher.attempt owner=%s job=%s generation=%s status=%s error=%s",
            job.owner_user_id,
            job.id,
            job.claim_generation,
            "succeeded" if committed else "failed",
            error_code,
        )

    def _apply_result(
        self, attempt: MemoryAttempt, result: ExtractionResult, call: dict[str, Any]
    ) -> None:
        job = attempt.job
        with transaction() as db:
            if (
                self._stop.is_set()
                or not settings.personal_memory_enabled
                or not repo.job_is_still_authorized(db, job)
            ):
                raise _LostAuthority()
            turn = repo.get_owned_turn(
                db, owner_user_id=job.owner_user_id, turn_id=job.turn_id
            )
            if turn is None:
                raise _LostAuthority()
            state = repo.get_or_create_state(db, job.owner_user_id)
            operation = db.get(UserMemoryOperation, attempt.operation_id)
            if operation is None or operation.status != "pending":
                raise _LostAuthority()
            result_id: str | None = None
            for candidate in result.candidates:
                proposal = candidate.proposal(
                    attempt.active_versions.get(candidate.key)
                )
                value, quote = validate_memory_proposal(
                    db, owner_user_id=job.owner_user_id, turn=turn, proposal=proposal
                )
                # A terminal fact from this authenticated source cannot reappear
                # because a newer extractor version revisits the original turn.
                rejected = db.scalar(
                    select(UserMemoryEntry.id)
                    .join(
                        UserMemoryEvidence,
                        UserMemoryEvidence.memory_id == UserMemoryEntry.id,
                    )
                    .where(
                        UserMemoryEntry.owner_user_id == job.owner_user_id,
                        UserMemoryEntry.key == candidate.key,
                        UserMemoryEntry.status.in_(repo.TERMINAL_ENTRY_STATUSES),
                        UserMemoryEvidence.owner_user_id == job.owner_user_id,
                        UserMemoryEvidence.turn_id == job.turn_id,
                    )
                    .limit(1)
                )
                if rejected is not None:
                    continue
                active = repo.get_active_entry(
                    db, owner_user_id=job.owner_user_id, key=candidate.key
                )
                entry = repo.propose_entry(
                    db,
                    owner_user_id=job.owner_user_id,
                    key=candidate.key,
                    value=value,
                    origin="automatic",
                    turn_id=job.turn_id,
                    quote=quote,
                    quote_start=proposal.quote_start,
                    quote_end=proposal.quote_end,
                    expected_active_version=proposal.expected_active_version,
                    expires_at=default_expiry(candidate.key),
                    auto_confirm=apply_memory_policy(
                        state,
                        proposal=proposal,
                        has_conflicting_active=(
                            active is not None
                            or candidate.key in attempt.active_versions
                            or bool(candidate.conflict_note)
                        ),
                    ),
                )
                result_id = entry.id
            if self._stop.is_set() or not repo.finish_extraction_job(
                db,
                job_id=job.id,
                lease_owner=job.lease_owner or "",
                claim_generation=job.claim_generation,
                status="succeeded",
            ):
                raise _LostAuthority()
            repo.complete_operation(
                db,
                operation=operation,
                status="succeeded",
                result_memory_id=result_id,
                result_revision=state.revision,
                usage=_usage(call),
            )

    def _finish_failure(
        self,
        job: UserMemoryJob,
        attempt: MemoryAttempt | None,
        call: dict[str, Any] | None,
        error_code: str,
        retryable: bool,
    ) -> None:
        cancelled = error_code == "memory_source_invalid" or self._stop.is_set()
        if cancelled:
            retryable = False
        with transaction() as db:
            # A bill belongs to this generation, not to the next lease holder.
            # Updating its receipt does not confer authority to mutate memory.
            if attempt is not None:
                operation = db.scalar(
                    select(UserMemoryOperation)
                    .where(
                        UserMemoryOperation.id == attempt.operation_id,
                        UserMemoryOperation.owner_user_id == job.owner_user_id,
                    )
                    .with_for_update()
                )
                if operation is not None and (
                    operation.status == "pending"
                    or (
                        operation.status == "failed"
                        and operation.error_code == "memory_outcome_unknown"
                        and call is not None
                    )
                ):
                    if call is None:
                        operation.usage_json = "{}"
                    repo.complete_operation(
                        db,
                        operation=operation,
                        status="failed",
                        usage=_usage(call) if call is not None else None,
                        error_code=error_code,
                    )
            # False is expected after a new generation/revocation. The old
            # receipt above is still billable, but no stale job state is written.
            repo.finish_extraction_job(
                db,
                job_id=job.id,
                lease_owner=job.lease_owner or "",
                claim_generation=job.claim_generation,
                status="cancelled" if cancelled else "failed",
                error_code=error_code,
                retry_after_seconds=(
                    min(60, 5 * (2 ** max(0, job.attempts - 1)))
                    if retryable
                    and job.attempts < settings.personal_memory_max_attempts
                    else None
                ),
            )
