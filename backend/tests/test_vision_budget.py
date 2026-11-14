from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import delete, select

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.models.governance import BudgetReservation, BudgetRule
from app.services.budget import (
    VisionBudgetExceeded,
    finalize_vision_budget,
    release_vision_budget,
    reserve_vision_budget,
    settle_vision_budget,
)


@pytest.mark.parametrize("scope", ["system", "user", "space"])
def test_vision_budget_reservation_is_atomic_per_scope(
    tmp_path: Path, scope: str
) -> None:
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / f"vision-budget-{scope}.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    database.reset_database(path)
    try:
        database.initialize_database()
        stamp = now()
        with transaction() as db:
            db.add(
                BudgetRule(
                    id=f"budget-{scope}",
                    scope=scope,
                    cost_limit="1",
                    soft_threshold=0.8,
                    hard_stop=True,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )

        barrier = Barrier(2)

        def attempt(index: int):
            barrier.wait(timeout=5)
            try:
                return reserve_vision_budget(
                    user_id="user-budget",
                    operation_id=f"operation-{index}",
                    provider="provider-budget",
                    model="model-budget",
                    estimated_cost=Decimal("1"),
                    space_ids=["space-budget"],
                )
            except VisionBudgetExceeded as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))

        handles = [
            item for item in results if not isinstance(item, VisionBudgetExceeded)
        ]
        assert len(handles) == 1, [
            repr(getattr(item, "__cause__", None)) for item in results
        ]
        with transaction() as db:
            rows = list(db.scalars(select(BudgetReservation)))
            assert len(rows) == 1
            assert rows[0].status == "reserved"
            assert rows[0].held_cost == "1"

        release_vision_budget(handles[0].id)
        replacement = reserve_vision_budget(
            user_id="user-budget",
            operation_id="operation-replacement",
            provider="provider-budget",
            model="model-budget",
            estimated_cost=Decimal("1"),
            space_ids=["space-budget"],
        )
        assert replacement is not None
        settle_vision_budget(replacement.id, actual_cost="0.25")
        with transaction() as db:
            settled = db.get(BudgetReservation, replacement.id)
            assert settled is not None
            assert settled.status == "settled"
            assert settled.actual_cost == "0.25"
            assert settled.held_cost == "0.25"
        finalize_vision_budget(replacement.id)
        with transaction() as db:
            accounted = db.get(BudgetReservation, replacement.id)
            assert accounted is not None
            assert accounted.status == "accounted"
            assert accounted.held_cost == "0"
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


@pytest.mark.skipif(
    not os.getenv("DOC_NEXUS_TEST_POSTGRES_URL"),
    reason="requires an isolated PostgreSQL migration database",
)
def test_postgres_vision_budget_reservation_serializes_workers(
    tmp_path: Path,
) -> None:
    """Two workers cannot reserve the same PostgreSQL hard-budget capacity."""
    old_path, old_url = settings.db_path, settings.database_url
    reset_path = tmp_path / "postgres-reset-sentinel.sqlite3"
    settings.database_url = os.environ["DOC_NEXUS_TEST_POSTGRES_URL"]
    database.reset_database(reset_path)
    try:
        stamp = now()
        with transaction() as db:
            db.execute(delete(BudgetReservation))
            db.execute(delete(BudgetRule))
            db.add(
                BudgetRule(
                    id="budget-postgres-system",
                    scope="system",
                    cost_limit="1",
                    soft_threshold=0.8,
                    hard_stop=True,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )

        barrier = Barrier(2)

        def attempt(index: int):
            barrier.wait(timeout=5)
            try:
                return reserve_vision_budget(
                    user_id="user-postgres-budget",
                    operation_id=f"postgres-operation-{index}",
                    provider="provider-budget",
                    model="model-budget",
                    estimated_cost=Decimal("1"),
                )
            except VisionBudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))

        handles = [item for item in results if item is not None]
        assert len(handles) == 1
        with transaction() as db:
            rows = list(db.scalars(select(BudgetReservation)))
            assert len(rows) == 1
            assert rows[0].status == "reserved"
    finally:
        try:
            with transaction() as db:
                db.execute(delete(BudgetReservation))
                db.execute(delete(BudgetRule))
        finally:
            database.reset_database(reset_path)
            settings.db_path, settings.database_url = old_path, old_url
