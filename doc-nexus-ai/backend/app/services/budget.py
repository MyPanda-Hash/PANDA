"""Durable cumulative hard-budget checks for provider-backed operations.

A provider call reserves its worst-case cost before any network request. Hard
rules and active reservations are read and updated in one serialized database
transaction: PostgreSQL locks the hard-rule rows; SQLite uses ``BEGIN
IMMEDIATE``. Reservations are settled or released by the caller and retain a
short conservative hold so persisted operation usage cannot create a gap in
which concurrent workers overspend the same budget.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_token, now
from app.models.governance import BudgetReservation, BudgetRule
from app.services.pricing import calculate_cost, find_active_price

_ACTIVE_RESERVATION_STATUSES = ("reserved", "settled")
_SETTLED_HOLD_SECONDS = 300


class VisionBudgetExceeded(RuntimeError):
    """Stable, privacy-safe cumulative budget refusal."""

    code = "vision_budget_exceeded"

    def __init__(self, scope: str = "system", *, unavailable: bool = False) -> None:
        self.scope = scope
        self.unavailable = unavailable
        super().__init__(
            "暂时无法确认视觉处理额度" if unavailable else "视觉处理已达到累计预算上限"
        )



class MemoryBudgetExceeded(RuntimeError):
    """Stable refusal for the same cumulative budgets used by other features."""

    code = "memory_budget_exceeded"

    def __init__(self, scope: str = "system", *, unavailable: bool = False) -> None:
        self.scope = scope
        self.unavailable = unavailable
        super().__init__(
            "暂时无法确认个人记忆处理额度"
            if unavailable
            else "个人记忆提炼已达到累计预算上限"
        )


class ScheduleBudgetExceeded(RuntimeError):
    """Stable refusal for unattended scheduled runs against cumulative budgets."""

    code = "schedule_budget_exceeded"

    def __init__(self, scope: str = "system", *, unavailable: bool = False) -> None:
        self.scope = scope
        self.unavailable = unavailable
        super().__init__(
            "暂时无法确认定时任务额度"
            if unavailable
            else "定时任务已达到累计预算上限"
        )

@dataclass(frozen=True, slots=True)
class BudgetUsage:
    scope: str
    current_cost: Decimal
    estimated_cost: Decimal
    limit: Decimal


@dataclass(frozen=True, slots=True)
class BudgetReservationHandle:
    id: str
    estimated_cost: Decimal
    operation_id: str = ""

def _decimal(value: object) -> Decimal:
    try:
        amount = Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return amount if amount.is_finite() and amount >= 0 else Decimal("0")


def _json_ids(value: object) -> frozenset[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return frozenset()
        raw = parsed if isinstance(parsed, list) else []
    return frozenset(
        item.strip()[:128]
        for item in (str(item or "") for item in raw)
        if item.strip()
    )


def _operation_space_ids(
    db: Session, feature: str, operation_id: str
) -> frozenset[str]:
    """Resolve durable space scope without exposing operation body content."""
    operation_id = str(operation_id or "").strip()
    if not operation_id:
        return frozenset()
    if feature == "chat":
        from app.models.chat import ChatMessage
        from app.models.chat_control import ChatControlOperation

        message = db.get(ChatMessage, operation_id)
        if message is not None:
            return _json_ids(getattr(message, "space_ids_json", "[]"))
        control = db.get(ChatControlOperation, operation_id)
        if control is not None:
            return frozenset()
    elif feature == "qa":
        from app.models.qa import QaTurn

        turn = db.get(QaTurn, operation_id)
        if turn is not None:
            return _json_ids(turn.selected_space_ids)
    elif feature == "writing":
        from app.models.writing import WritingOperation, WritingProject

        operation = db.get(WritingOperation, operation_id)
        if operation is not None:
            project = db.get(WritingProject, operation.project_id)
            if project is not None:
                return _json_ids(project.selected_space_ids)
    elif feature == "documents":
        from app.models.documents import Document, DocumentJob, DocumentVersion

        job = db.get(DocumentJob, operation_id)
        if job is not None:
            space_id = str(job.space_id or "").strip()
            return frozenset({space_id}) if space_id else frozenset()
        version = db.get(DocumentVersion, operation_id)
        if version is not None:
            document = db.get(Document, version.document_id)
            space_id = str(getattr(document, "space_id", "") or "").strip()
            return frozenset({space_id}) if space_id else frozenset()
    elif feature == "schedule":
        from app.models.schedules import ScheduleRun
        from app.services.schedule_access import schedule_scope_space_ids

        run = db.get(ScheduleRun, operation_id)
        if run is not None:
            try:
                config = json.loads(run.config_json)
            except (TypeError, ValueError):
                return frozenset()
            if isinstance(config, dict):
                return frozenset(schedule_scope_space_ids(db, config))
    return frozenset()


def _operation_space_ids_any(db: Session, operation_id: str) -> frozenset[str]:
    candidates = [str(operation_id or "").strip()]
    page_separator = ":page:"
    if page_separator in candidates[0]:
        candidates.append(candidates[0].split(page_separator, 1)[0])
    for candidate in candidates:
        for feature in ("chat", "qa", "writing", "documents"):
            spaces = _operation_space_ids(db, feature, candidate)
            if spaces:
                return spaces
    return frozenset()


def _operation_cost(usage: dict[str, Any]) -> Decimal:
    calls = usage.get("calls")
    if isinstance(calls, list) and calls:
        return sum(
            (_decimal(item.get("cost")) for item in calls if isinstance(item, dict)),
            Decimal("0"),
        )
    return _decimal(usage.get("cost"))


def cumulative_cost(
    db: Session,
    *,
    scope: str,
    user_id: str,
    space_ids: Iterable[str] = (),
) -> Decimal:
    """Return durable billable operation cost for one budget scope."""
    from app.repositories.usage import _collect_operations

    owner = user_id if scope == "user" else None
    requested_spaces = frozenset(
        item.strip()[:128]
        for item in (str(value or "") for value in space_ids)
        if item.strip()
    )
    total = Decimal("0")
    for operation in _collect_operations(db, owner_user_id=owner):
        if scope == "space":
            operation_spaces = _operation_space_ids(
                db, operation.feature, operation.id
            )
            if not requested_spaces.intersection(operation_spaces):
                continue
        total += _operation_cost(operation.usage)
    return total


def estimate_vision_cost(
    db: Session,
    *,
    provider: str,
    model: str,
) -> Decimal:
    """Estimate one worst-case call, bounded by the per-call cost ceiling."""
    per_call_cap = _decimal(getattr(settings, "vision_max_cost_cny", "1"))
    price = find_active_price(db, provider=provider, model=model)
    if price is None:
        return per_call_cap
    max_tokens = max(0, int(getattr(settings, "vision_max_total_tokens", 0) or 0))
    estimate = calculate_cost(
        input_tokens=max_tokens,
        output_tokens=max_tokens,
        input_uncached_tokens=max_tokens,
        input_per_million=price.input_per_million,
        output_per_million=price.output_per_million,
        cache_read_per_million=getattr(price, "cache_read_per_million", None),
        cache_write_per_million=getattr(price, "cache_write_per_million", None),
    )
    if estimate <= 0:
        return per_call_cap
    return min(per_call_cap, estimate)


def _begin_serialized(db: Session) -> str:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    else:
        db.begin()
    return dialect


def _locked_hard_rules(db: Session, dialect: str) -> list[BudgetRule]:
    statement = (
        select(BudgetRule)
        .where(BudgetRule.hard_stop.is_(True), BudgetRule.is_active.is_(True))
        .order_by(BudgetRule.scope, BudgetRule.updated_at.desc(), BudgetRule.id)
    )
    if dialect == "postgresql":
        statement = statement.with_for_update()
    return list(db.scalars(statement))


def _release_expired_holds(db: Session) -> None:
    stamp = now()
    db.execute(
        update(BudgetReservation)
        .where(
            BudgetReservation.status.in_(_ACTIVE_RESERVATION_STATUSES),
            BudgetReservation.hold_until <= stamp,
        )
        .values(
            status="released",
            held_cost="0",
            updated_at=stamp,
        )
    )


def _held_reservation_cost(
    db: Session,
    *,
    scope: str,
    user_id: str,
    space_ids: frozenset[str],
) -> Decimal:
    statement = select(BudgetReservation).where(
        BudgetReservation.status.in_(_ACTIVE_RESERVATION_STATUSES),
        BudgetReservation.hold_until > now(),
    )
    if scope == "user":
        statement = statement.where(BudgetReservation.owner_user_id == user_id)
    total = Decimal("0")
    for reservation in db.scalars(statement):
        if scope == "space" and not space_ids.intersection(
            _json_ids(reservation.space_ids_json)
        ):
            continue
        total += _decimal(reservation.held_cost)
    return total


def reserve_vision_budget(
    *,
    user_id: str,
    operation_id: str,
    provider: str,
    model: str,
    estimated_cost: Decimal | None = None,
    space_ids: Sequence[str] = (),
) -> BudgetReservationHandle | None:
    return _reserve_provider_budget(
        user_id=user_id,
        operation_id=operation_id,
        provider=provider,
        model=model,
        estimated_cost=estimated_cost,
        space_ids=space_ids,
        feature="vision",
        timeout_seconds=settings.vision_timeout_seconds,
        refusal=VisionBudgetExceeded,
    )


def reserve_memory_budget(
    *, user_id: str, operation_id: str, provider: str, model: str,
    estimated_cost: Decimal,
) -> BudgetReservationHandle | None:
    return _reserve_provider_budget(
        user_id=user_id,
        operation_id=operation_id,
        provider=provider,
        model=model,
        estimated_cost=estimated_cost,
        space_ids=(),
        feature="memory",
        timeout_seconds=settings.personal_memory_extract_timeout_seconds,
        refusal=MemoryBudgetExceeded,
    )


def reserve_schedule_budget(
    *,
    user_id: str,
    operation_id: str,
    provider: str,
    model: str,
    budget_tokens: int = 0,
    estimated_cost: Decimal | None = None,
    space_ids: Sequence[str] = (),
    timeout_seconds: float = 600.0,
) -> BudgetReservationHandle | None:
    if estimated_cost is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            estimated_cost = estimate_schedule_cost(
                db, provider=provider, model=model, budget_tokens=budget_tokens
            )
        finally:
            db.close()
    return _reserve_provider_budget(
        user_id=user_id,
        operation_id=operation_id,
        provider=provider,
        model=model,
        estimated_cost=estimated_cost,
        space_ids=space_ids,
        feature="schedule",
        timeout_seconds=timeout_seconds,
        refusal=ScheduleBudgetExceeded,
    )


def estimate_schedule_cost(
    db: Session, *, provider: str, model: str, budget_tokens: int
) -> Decimal:
    """Estimate one worst-case unattended run, bounded by the run cost ceiling."""
    per_call_cap = _decimal(getattr(settings, "schedule_max_cost_cny", "2"))
    price = find_active_price(db, provider=provider, model=model)
    if price is None:
        return per_call_cap
    tokens = max(0, int(budget_tokens or 0))
    estimate = calculate_cost(
        input_tokens=tokens,
        output_tokens=tokens,
        input_uncached_tokens=tokens,
        input_per_million=price.input_per_million,
        output_per_million=price.output_per_million,
        cache_read_per_million=getattr(price, "cache_read_per_million", None),
        cache_write_per_million=getattr(price, "cache_write_per_million", None),
    )
    if estimate <= 0:
        return per_call_cap
    return min(per_call_cap, estimate)


def _reserve_provider_budget(
    *, user_id: str, operation_id: str, provider: str, model: str,
    estimated_cost: Decimal | None, space_ids: Sequence[str], feature: str,
    timeout_seconds: float,
    refusal: (
        type[VisionBudgetExceeded]
        | type[MemoryBudgetExceeded]
        | type[ScheduleBudgetExceeded]
    ),
) -> BudgetReservationHandle | None:
    """Atomically reserve all matching system/user/space hard budgets."""
    from app.core.database import SessionLocal

    normalized_user_id = str(user_id or "").strip()
    normalized_operation_id = str(operation_id or "").strip()[:128]
    normalized_spaces = frozenset(
        item.strip()[:128]
        for item in (str(value or "") for value in space_ids)
        if item.strip()
    )
    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        rules = _locked_hard_rules(db, dialect)
        if not rules:
            db.commit()
            return None
        _release_expired_holds(db)
        scope_spaces = normalized_spaces or _operation_space_ids_any(
            db, normalized_operation_id
        )
        estimate = (
            _decimal(estimated_cost)
            if estimated_cost is not None
            else estimate_vision_cost(db, provider=provider, model=model)
        )
        for rule in rules:
            scope = str(rule.scope or "").strip().lower()
            if scope not in {"system", "user", "space"}:
                continue
            if scope == "space" and not scope_spaces:
                continue
            limit = _decimal(rule.cost_limit)
            current = cumulative_cost(
                db,
                scope=scope,
                user_id=normalized_user_id,
                space_ids=scope_spaces,
            ) + _held_reservation_cost(
                db,
                scope=scope,
                user_id=normalized_user_id,
                space_ids=scope_spaces,
            )
            if current >= limit or current + estimate > limit:
                from app.repositories import audit

                audit.record(
                    db,
                    normalized_user_id or None,
                    f"{feature}_budget_rejected",
                    str(getattr(rule, "id", "") or "")[:128] or None,
                    "rejected",
                )
                db.commit()
                raise refusal(scope)
        stamp = now()
        hold_seconds = max(
            _SETTLED_HOLD_SECONDS,
            int(float(timeout_seconds)) + 60,
        )
        reservation = BudgetReservation(
            id=f"bres_{new_token(10)}",
            operation_id=normalized_operation_id,
            owner_user_id=normalized_user_id,
            space_ids_json=json.dumps(
                sorted(scope_spaces), ensure_ascii=False, separators=(",", ":")
            ),
            estimated_cost=str(estimate),
            held_cost=str(estimate),
            actual_cost=None,
            status="reserved",
            created_at=stamp,
            updated_at=stamp,
            hold_until=stamp + timedelta(seconds=hold_seconds),
        )
        db.add(reservation)
        db.commit()
        return BudgetReservationHandle(
            reservation.id,
            estimate,
            normalized_operation_id,
        )
    except (VisionBudgetExceeded, MemoryBudgetExceeded, ScheduleBudgetExceeded):
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise refusal("system", unavailable=True) from exc
    finally:
        db.close()


def settle_vision_budget(
    reservation_id: str,
    *,
    actual_cost: Decimal | str | None = None,
    uncertain: bool = False,
) -> None:
    _settle_provider_budget(
        reservation_id, actual_cost=actual_cost, uncertain=uncertain,
        refusal=VisionBudgetExceeded,
    )


def settle_memory_budget(
    reservation_id: str, *, actual_cost: Decimal | str | None = None,
    uncertain: bool = False,
) -> None:
    _settle_provider_budget(
        reservation_id, actual_cost=actual_cost, uncertain=uncertain,
        refusal=MemoryBudgetExceeded,
    )


def _settle_provider_budget(
    reservation_id: str, *, actual_cost: Decimal | str | None,
    uncertain: bool,
    refusal: (
        type[VisionBudgetExceeded]
        | type[MemoryBudgetExceeded]
        | type[ScheduleBudgetExceeded]
    ),
) -> None:
    """Settle a reservation; unknown provider outcomes retain the estimate."""
    if not reservation_id:
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        statement = select(BudgetReservation).where(
            BudgetReservation.id == reservation_id
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        reservation = db.scalar(statement)
        if reservation is None or reservation.status == "released":
            db.commit()
            return
        amount = (
            _decimal(reservation.estimated_cost)
            if uncertain or actual_cost is None
            else _decimal(actual_cost)
        )
        stamp = now()
        reservation.actual_cost = None if uncertain else str(amount)
        reservation.held_cost = str(amount)
        reservation.status = "settled"
        reservation.updated_at = stamp
        reservation.hold_until = stamp + timedelta(seconds=_SETTLED_HOLD_SECONDS)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise refusal("system", unavailable=True) from exc
    finally:
        db.close()


def settle_schedule_budget(
    reservation_id: str,
    *,
    actual_cost: Decimal | str | None = None,
    uncertain: bool = False,
) -> None:
    _settle_provider_budget(
        reservation_id, actual_cost=actual_cost, uncertain=uncertain,
        refusal=ScheduleBudgetExceeded,
    )


def release_vision_budget(reservation_id: str) -> None:
    _release_provider_budget(reservation_id, refusal=VisionBudgetExceeded)


def release_memory_budget(reservation_id: str) -> None:
    _release_provider_budget(reservation_id, refusal=MemoryBudgetExceeded)


def _release_provider_budget(
    reservation_id: str, *,
    refusal: (
        type[VisionBudgetExceeded]
        | type[MemoryBudgetExceeded]
        | type[ScheduleBudgetExceeded]
    ),
) -> None:
    """Release a reservation after a definite non-billable failure."""
    if not reservation_id:
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        statement = select(BudgetReservation).where(
            BudgetReservation.id == reservation_id
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        reservation = db.scalar(statement)
        if reservation is not None:
            stamp = now()
            reservation.status = "released"
            reservation.held_cost = "0"
            reservation.updated_at = stamp
            reservation.hold_until = stamp
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise refusal("system", unavailable=True) from exc
    finally:
        db.close()


def release_schedule_budget(reservation_id: str) -> None:
    _release_provider_budget(reservation_id, refusal=ScheduleBudgetExceeded)


def finalize_vision_budget(reservation_id: str) -> None:
    _finalize_provider_budget(reservation_id, refusal=VisionBudgetExceeded)


def finalize_memory_budget(reservation_id: str) -> None:
    _finalize_provider_budget(reservation_id, refusal=MemoryBudgetExceeded)


def _finalize_provider_budget(
    reservation_id: str, *,
    refusal: (
        type[VisionBudgetExceeded]
        | type[MemoryBudgetExceeded]
        | type[ScheduleBudgetExceeded]
    ),
) -> None:
    """Stop holding a settled reservation once its durable bill is committed.

    A settled reservation remains held briefly so a provider result cannot
    race a durable usage write.  The persistence boundary calls this method
    after that usage is committed; the durable operation is then the sole
    budget accounting source.
    """
    if not reservation_id:
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        statement = select(BudgetReservation).where(
            BudgetReservation.id == reservation_id
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        reservation = db.scalar(statement)
        if reservation is None or reservation.status in {"released", "accounted"}:
            db.commit()
            return
        # Never clear a live reservation before the provider result has been
        # settled.  Unknown/in-flight outcomes must remain conservatively held.
        if reservation.status != "settled":
            db.commit()
            return
        stamp = now()
        reservation.status = "accounted"
        reservation.held_cost = "0"
        reservation.updated_at = stamp
        reservation.hold_until = stamp
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise refusal("system", unavailable=True) from exc
    finally:
        db.close()


def finalize_vision_budget_for_operation(operation_id: str) -> None:
    """Finalize every settled vision hold linked to one durable operation."""
    normalized_operation_id = str(operation_id or "").strip()[:128]
    if not normalized_operation_id:
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        statement = select(BudgetReservation).where(
            BudgetReservation.operation_id == normalized_operation_id,
            BudgetReservation.status == "settled",
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        reservations = list(db.scalars(statement))
        stamp = now()
        for reservation in reservations:
            reservation.status = "accounted"
            reservation.held_cost = "0"
            reservation.updated_at = stamp
            reservation.hold_until = stamp
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise VisionBudgetExceeded("system", unavailable=True) from exc
    finally:
        db.close()



def finalize_schedule_budget_for_operation(operation_id: str) -> None:
    """Finalize every settled schedule hold linked to one durable run bill."""
    _finalize_provider_budget_for_operation(
        operation_id, refusal=ScheduleBudgetExceeded
    )


def _finalize_provider_budget_for_operation(
    operation_id: str,
    *,
    refusal: (
        type[VisionBudgetExceeded]
        | type[MemoryBudgetExceeded]
        | type[ScheduleBudgetExceeded]
    ),
) -> None:
    normalized_operation_id = str(operation_id or "").strip()[:128]
    if not normalized_operation_id:
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        dialect = _begin_serialized(db)
        statement = select(BudgetReservation).where(
            BudgetReservation.operation_id == normalized_operation_id,
            BudgetReservation.status == "settled",
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        reservations = list(db.scalars(statement))
        stamp = now()
        for reservation in reservations:
            reservation.status = "accounted"
            reservation.held_cost = "0"
            reservation.updated_at = stamp
            reservation.hold_until = stamp
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise refusal("system", unavailable=True) from exc
    finally:
        db.close()


# Backward-compatible name for existing callers; the returned handle must be
# settled or released by provider code.
def preflight_vision_budget(**kwargs: Any) -> BudgetReservationHandle | None:
    kwargs.pop("db", None)
    return reserve_vision_budget(**kwargs)
