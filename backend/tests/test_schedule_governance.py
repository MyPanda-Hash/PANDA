"""定时任务治理闭环测试：预算硬停、用量入账、数量上限、稍后提醒边界与保留清理。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.models.auth import AuditEvent, User
from app.models.governance import BudgetReservation, BudgetRule, ModelPrice
from app.models.schedules import (
    Schedule,
    ScheduleDraft,
    ScheduleNotification,
    ScheduleOperation,
    ScheduleRun,
)
from app.repositories import usage as usage_repo
from app.schemas.schedules import SnoozeRequest
from app.services import schedule_retention
from app.services import schedules as schedule_service
from app.services.budget import (
    ScheduleBudgetExceeded,
    cumulative_cost,
    estimate_schedule_cost,
    reserve_schedule_budget,
)
from app.services.schedule_access import ScheduleAccessError
from app.services.schedule_executor import (
    _BLOCKING_ERROR_CODES,
    _recordable_usage,
    _reserve_schedule_budget,
    ScheduleExecutor,
)


# 测试夹具占位值，非真实凭据；通过变量传递以明确“哈希占位”语义。
_FIXTURE_HASH = "hashed"


@contextmanager
def _isolated_db(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    db_path = tmp_path / "schedule-governance.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        database.initialize_database()
        yield
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


def _user(db, user_id: str = "usr_governance") -> User:
    stamp = now()
    user = User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        display_name="治理用户",
        password_hash=_FIXTURE_HASH,
        role="user",
        status="active",
        first_login=False,
        created_at=stamp,
    )
    db.add(user)
    db.flush()
    return user


def _hard_rule(rule_id: str, scope: str, limit: str) -> BudgetRule:
    stamp = now()
    return BudgetRule(
        id=rule_id,
        scope=scope,
        cost_limit=limit,
        soft_threshold=0.8,
        hard_stop=True,
        is_active=True,
        created_at=stamp,
        updated_at=stamp,
    )


def _price(provider: str, model: str, *, input_: str, output: str) -> ModelPrice:
    stamp = now()
    return ModelPrice(
        id=f"prc_{provider}_{model}".replace("-", "_"),
        provider=provider,
        model=model,
        currency="CNY",
        input_per_million=input_,
        output_per_million=output,
        cache_read_per_million=None,
        cache_write_per_million=None,
        effective_at=stamp,
        superseded_at=None,
        created_at=stamp,
    )


def _skill_config(*, budget_tokens: int = 500_000) -> dict:
    return {
        "kind": "skill",
        "instruction": "汇总并生成结果",
        "budget_tokens": budget_tokens,
        "timeout_seconds": 600,
        "space_ids": [],
        "document_ids": [],
    }


def test_budget_exceeded_is_a_blocking_error_code() -> None:
    assert "budget_exceeded" in _BLOCKING_ERROR_CODES


def test_schedule_budget_rejection_raises_and_audits(tmp_path: Path) -> None:
    with _isolated_db(tmp_path):
        with transaction() as db:
            _user(db)
            db.add(_hard_rule("bud_schedule", "user", "1"))
        with pytest.raises(ScheduleAccessError) as rejected:
            _reserve_schedule_budget(
                user_id="usr_governance",
                operation_id="srn_budget_reject",
                provider="dashscope",
                model="qwen-plus",
                config=_skill_config(),
            )
        assert rejected.value.code == "budget_exceeded"
        with transaction() as db:
            events = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "schedule_budget_rejected"
                    )
                )
            )
        assert len(events) == 1
        assert events[0].actor_user_id == "usr_governance"
        assert events[0].target_id == "bud_schedule"
        assert events[0].result == "rejected"


def test_schedule_budget_rejection_missing_rule_is_not_blocking(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        with transaction() as db:
            _user(db)
        reservation = _reserve_schedule_budget(
            user_id="usr_governance",
            operation_id="srn_no_rule",
            provider="dashscope",
            model="qwen-plus",
            config=_skill_config(),
        )
        assert reservation is None


def test_schedule_budget_estimate_bounded_by_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _isolated_db(tmp_path):
        monkeypatch.setattr(settings, "schedule_max_cost_cny", Decimal("0.05"))
        with transaction() as db:
            db.add(_price("dashscope", "qwen-plus", input_="100", output="200"))
        with transaction() as db:
            priced = estimate_schedule_cost(
                db, provider="dashscope", model="qwen-plus", budget_tokens=500_000
            )
            unpriced = estimate_schedule_cost(
                db, provider="openai", model="gpt-x", budget_tokens=500_000
            )
            tiny = estimate_schedule_cost(
                db, provider="dashscope", model="qwen-plus", budget_tokens=10
            )
        assert priced == Decimal("0.05")
        assert unpriced == Decimal("0.05")
        # 10 tokens × (100+200)/1e6 ≈ 0.003，低于上限时应保留精确估算。
        assert tiny < Decimal("0.05")


def test_schedule_budget_reserve_settle_finalize_lifecycle(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        with transaction() as db:
            _user(db)
            db.add(_hard_rule("bud_high", "user", "100000"))
        handle = reserve_schedule_budget(
            user_id="usr_governance",
            operation_id="srn_lifecycle",
            provider="dashscope",
            model="qwen-plus",
            budget_tokens=1000,
            estimated_cost=Decimal("0.01"),
        )
        assert handle is not None
        with transaction() as db:
            row = db.get(BudgetReservation, handle.id)
            assert row is not None
            assert row.status == "reserved"
            assert Decimal(row.held_cost) == Decimal("0.01")

        executor = ScheduleExecutor(worker_id="gov-worker")
        executor._settle_budget_reservation(
            "srn_lifecycle",
            handle.id,
            usage={"cost": "0.004"},
            bill_committed=True,
            model_started=True,
        )
        with transaction() as db:
            row = db.get(BudgetReservation, handle.id)
            assert row is not None
            assert row.status == "accounted"
            assert Decimal(row.held_cost) == 0
            assert Decimal(str(row.actual_cost)) == Decimal("0.004")

        release_handle = reserve_schedule_budget(
            user_id="usr_governance",
            operation_id="srn_release",
            provider="dashscope",
            model="qwen-plus",
            budget_tokens=1000,
            estimated_cost=Decimal("0.01"),
        )
        assert release_handle is not None
        executor._settle_budget_reservation(
            "srn_release",
            release_handle.id,
            usage=None,
            bill_committed=False,
            model_started=False,
        )
        with transaction() as db:
            row = db.get(BudgetReservation, release_handle.id)
            assert row is not None
            assert row.status == "released"
            assert Decimal(row.held_cost) == 0


def test_recordable_usage_validates_and_prices(tmp_path: Path) -> None:
    with _isolated_db(tmp_path):
        with transaction() as db:
            db.add(_price("dashscope", "qwen-plus", input_="10", output="20"))
        token_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "input_uncached_tokens": 1000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 1500,
        }
        usage = _recordable_usage("dashscope", "qwen-plus", token_usage)
        assert usage is not None
        assert usage["provider"] == "dashscope"
        assert usage["model"] == "qwen-plus"
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 500
        assert Decimal(str(usage["cost"])) > 0
        assert _recordable_usage("dashscope", "qwen-plus", None) is None
        assert _recordable_usage("dashscope", "qwen-plus", {"total": 5}) is None


def test_schedule_usage_enters_ledger_and_cumulative_cost(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        stamp = now()
        with transaction() as db:
            user = _user(db)
            db.add(
                Schedule(
                    id="sch_ledger",
                    owner_user_id=user.id,
                    name="ledger",
                    kind="skill",
                    status="completed",
                    revision=1,
                    config_json=json.dumps(_skill_config()),
                    next_run_at=None,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            db.add(
                ScheduleRun(
                    id="srn_ledger",
                    schedule_id="sch_ledger",
                    owner_user_id=user.id,
                    trigger_kind="scheduled",
                    occurrence_key="2026-10-01T09:00Z",
                    scheduled_for=stamp,
                    config_json=json.dumps(_skill_config()),
                    schedule_revision=1,
                    status="completed",
                    status_revision=2,
                    usage_json=json.dumps(
                        {
                            "provider": "dashscope",
                            "model": "qwen-plus",
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "total_tokens": 1500,
                            "cost": "0.015",
                        }
                    ),
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        with transaction() as db:
            operations = [
                item
                for item in usage_repo._collect_operations(db, owner_user_id="usr_governance")
                if item.feature == "schedule"
            ]
            assert len(operations) == 1
            assert operations[0].id == "srn_ledger"
            assert operations[0].usage["model"] == "qwen-plus"
            total = cumulative_cost(db, scope="user", user_id="usr_governance")
            assert total >= Decimal("0.015")
            other = cumulative_cost(db, scope="user", user_id="usr_missing")
            assert other == 0


def test_snooze_request_rejects_beyond_31_days() -> None:
    request_id = "snooze-1"
    far = datetime.now(UTC) + timedelta(days=40)
    with pytest.raises(ValidationError):
        SnoozeRequest(request_id=request_id, until=far)
    naive = datetime.now() + timedelta(days=1)
    with pytest.raises(ValidationError):
        SnoozeRequest(request_id=request_id, until=naive)
    near = datetime.now(UTC) + timedelta(days=30)
    assert SnoozeRequest(request_id=request_id, until=near).until == near


def _reminder_config() -> dict:
    return {
        "name": "喝水提醒",
        "kind": "reminder",
        "instruction": "提醒我喝水",
        "timezone": "Asia/Shanghai",
        "rule": {"kind": "daily", "time": "09:30"},
        "space_ids": [],
        "document_ids": [],
        "notification": {
            "sound": False,
            "desktop": False,
            "on_success": False,
            "on_failure": True,
        },
    }


def test_confirm_draft_enforces_per_user_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "schedule_max_per_user", 1)
    with _isolated_db(tmp_path):
        with transaction() as db:
            user = _user(db)
            first = schedule_service.create_draft(db, user.id, _reminder_config(), "cap-1")
            first, _, _ = schedule_service.preview_draft(db, first, expected_revision=1)
            schedule = schedule_service.confirm_draft(
                db,
                first,
                expected_revision=first.revision,
                preview_token=first.preview_token,
                request_id="confirm-cap-1",
                enabled=True,
            )
            assert schedule.status == "enabled"

        with transaction() as db:
            second = schedule_service.create_draft(db, user.id, _reminder_config(), "cap-2")
            second, _, _ = schedule_service.preview_draft(db, second, expected_revision=1)
            with pytest.raises(schedule_service.ScheduleServiceError) as rejected:
                schedule_service.confirm_draft(
                    db,
                    second,
                    expected_revision=second.revision,
                    preview_token=second.preview_token,
                    request_id="confirm-cap-2",
                    enabled=True,
                )
            assert rejected.value.code == "schedule_limit_reached"

        # 归档后的计划不再占用配额。
        with transaction() as db:
            archived = db.get(Schedule, schedule.id)
            assert archived is not None
            archived.status = "archived"
        with transaction() as db:
            third = schedule_service.create_draft(db, user.id, _reminder_config(), "cap-3")
            third, _, _ = schedule_service.preview_draft(db, third, expected_revision=1)
            created = schedule_service.confirm_draft(
                db,
                third,
                expected_revision=third.revision,
                preview_token=third.preview_token,
                request_id="confirm-cap-3",
                enabled=True,
            )
            assert created.status == "enabled"


def test_retention_cleanup_removes_terminal_artifacts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(
        schedule_retention,
        "workspace_dir_for_agent",
        lambda runtime, agent, runtime_id: workspace_root / runtime_id,
    )
    monkeypatch.setattr(settings, "schedule_run_retention_days", 90)
    with _isolated_db(tmp_path):
        old = now() - timedelta(days=120)
        fresh = now() - timedelta(minutes=5)
        with transaction() as db:
            user = _user(db)
            db.add(
                Schedule(
                    id="sch_retention",
                    owner_user_id=user.id,
                    name="retention",
                    kind="skill",
                    status="completed",
                    revision=1,
                    config_json=json.dumps(_skill_config()),
                    next_run_at=None,
                    created_at=old,
                    updated_at=old,
                )
            )
            db.flush()
            for run_id, status, stamp, runtime_id in (
                ("srn_old_done", "completed", old, "pi-schedule-srn_old_done"),
                ("srn_old_skip", "skipped", old, None),
                ("srn_fresh", "completed", fresh, "pi-schedule-srn_fresh"),
                ("srn_awaiting", "awaiting_user", old, "pi-schedule-srn_awaiting"),
            ):
                db.add(
                    ScheduleRun(
                        id=run_id,
                        schedule_id="sch_retention",
                        owner_user_id=user.id,
                        trigger_kind="scheduled",
                        occurrence_key=f"{run_id}:occ",
                        scheduled_for=stamp,
                        config_json=json.dumps(_skill_config()),
                        schedule_revision=1,
                        status=status,
                        status_revision=2,
                        runtime_id=runtime_id,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            db.flush()
            db.add(
                ScheduleDraft(
                    id="sdr_expired",
                    owner_user_id=user.id,
                    request_id="retention-draft",
                    revision=1,
                    status="expired",
                    config_json=json.dumps(_reminder_config()),
                    expires_at=old,
                    operation="create",
                    created_at=old,
                    updated_at=old,
                )
            )
            db.add(
                ScheduleNotification(
                    id="snt_old",
                    owner_user_id=user.id,
                    event_id="schedule:srn_old_done:2:status",
                    sequence=1,
                    run_id="srn_old_done",
                    schedule_id="sch_retention",
                    status_revision=2,
                    event_kind="status",
                    status="completed",
                    title="定时任务已完成",
                    resource_path="/schedules/sch_retention/runs/srn_old_done",
                    created_at=old,
                    expires_at=old,
                )
            )
            db.add(
                ScheduleOperation(
                    id="sop_old",
                    owner_user_id=user.id,
                    resource_key="schedule:sch_retention:patch",
                    request_id="retention-op",
                    result_type="schedule",
                    result_id="sch_retention",
                    result_revision=1,
                    created_at=old,
                )
            )
        workspace_root.mkdir(parents=True, exist_ok=True)
        (workspace_root / "pi-schedule-srn_old_done").mkdir()
        (workspace_root / "pi-schedule-srn_fresh").mkdir()
        (workspace_root / "pi-schedule-srn_awaiting").mkdir()

        stats = schedule_retention.cleanup_once()

        assert stats["runs"] == 2
        assert stats["workspaces"] == 1
        assert stats["drafts"] == 1
        assert stats["operations"] == 1
        # 通知可能由运行删除的数据库级联先行清理，计数为 0 或 1 均正确。
        assert stats["notifications"] in (0, 1)
        assert not (workspace_root / "pi-schedule-srn_old_done").exists()
        assert (workspace_root / "pi-schedule-srn_fresh").exists()
        assert (workspace_root / "pi-schedule-srn_awaiting").exists()
        with transaction() as db:
            assert db.get(ScheduleRun, "srn_old_done") is None
            assert db.get(ScheduleRun, "srn_old_skip") is None
            assert db.get(ScheduleRun, "srn_fresh") is not None
            assert db.get(ScheduleRun, "srn_awaiting") is not None
            assert db.get(ScheduleDraft, "sdr_expired") is None
            assert db.get(ScheduleNotification, "snt_old") is None
            assert db.get(ScheduleOperation, "sop_old") is None


def test_schedule_budget_rejection_error_type_is_stable() -> None:
    exc = ScheduleBudgetExceeded("user")
    assert exc.code == "schedule_budget_exceeded"
    assert exc.scope == "user"
    assert not exc.unavailable
