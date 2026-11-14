from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.api.schedules import cancel_schedule_run

from app.core import database
from app.core.config import settings
from app.core.security import now
from app.models.auth import User
from app.models.schedules import (
    Schedule,
    ScheduleNotification,
    ScheduleRun,
    ScheduleRunAttempt,
)
from app.services.schedule_dispatcher import ScheduleDispatcher
from app.main import app
from app.repositories import harness as harness_repo
from app.repositories import schedules as schedule_repo
from app.schemas.schedules import RunActionRequest
from app.services.pi_runtime.skills import SkillNotFoundError, prepare_agent_skills
from app.services.schedule_executor import (
    ScheduleExecutor,
    _ExecutionCapture,
    _ensure_restricted_harness_task,
)
from tests._auth import bootstrap_admin_response


@contextmanager
def _isolated_db(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    db_path = tmp_path / "schedule-execution.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        database.initialize_database()
        yield
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


def _user(db, user_id: str = "usr_schedule") -> User:
    stamp = now()
    user = User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        display_name="Schedule User",
        password_hash="hashed",
        role="user",
        status="active",
        first_login=False,
        created_at=stamp,
    )
    db.add(user)
    db.flush()
    return user


def _config(*, kind: str) -> dict:
    stamp = now() - timedelta(minutes=1)
    config = {
        "name": "每分钟提醒" if kind == "reminder" else "定时分析",
        "kind": kind,
        "instruction": "整理已授权资料并生成新的结果",
        "timezone": "UTC",
        "rule": {
            "kind": "once",
            "date": stamp.date().isoformat(),
            "time": stamp.strftime("%H:%M"),
        },
        "space_ids": [],
        "document_ids": [],
        "late_window_minutes": 15 if kind == "reminder" else 60,
        "budget_tokens": 500_000,
        "timeout_seconds": 600,
        "max_attempts": 3,
        "notification": {
            "sound": False,
            "desktop": False,
            "on_success": False,
            "on_failure": True,
        },
    }
    if kind == "skill":
        config.update(
            skill_id="approved-skill",
            skill_version="1.0.0",
            skill_digest="a" * 64,
            model="bound-model",
        )
    return config


def _schedule(db, *, kind: str = "reminder") -> Schedule:
    stamp = now()
    user = db.get(User, "usr_schedule") or _user(db)
    config = _config(kind=kind)
    due = stamp - timedelta(minutes=1)
    row = Schedule(
        id=f"sch_{kind}",
        owner_user_id=user.id,
        name=config["name"],
        kind=kind,
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=due,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(row)
    db.flush()
    return row


def test_prompt_only_skill_draft_previews_and_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import schedules

    model_requests = []

    def resolve_model(db, purpose, *, requested_model):
        model_requests.append(requested_model)
        return SimpleNamespace(profile=SimpleNamespace(model="resolved-model"))

    monkeypatch.setattr(schedules, "resolve_model_endpoint", resolve_model)
    config = _config(kind="skill")
    for key in ("skill_id", "skill_version", "skill_digest", "model"):
        config.pop(key)
    future = now() + timedelta(days=1)
    config["rule"] = {
        "kind": "once",
        "date": future.date().isoformat(),
        "time": future.strftime("%H:%M"),
    }
    with _isolated_db(tmp_path):
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            created = client.post(
                "/schedule-drafts",
                headers=headers,
                json={"config": config, "request_id": "prompt-only-draft"},
            )
            assert created.status_code == 200, created.text
            draft = created.json()
            assert draft["config"]["skill_id"] is None
            assert draft["config"]["model"] == "resolved-model"
            preview = client.post(
                f"/schedule-drafts/{draft['id']}/preview",
                headers=headers,
                json={"expected_revision": draft["revision"]},
            )
            assert preview.status_code == 200, preview.text
            payload = preview.json()
            assert "skill_required" not in payload["blockers"]
            assert payload["blockers"] == []
            assert payload["draft"]["config"]["skill_id"] is None
            assert payload["draft"]["config"]["model"] == "resolved-model"
            confirmed = client.post(
                f"/schedule-drafts/{draft['id']}/confirm",
                headers=headers,
                json={
                    "expected_revision": draft["revision"],
                    "preview_token": payload["preview_token"],
                    "request_id": "prompt-only-confirm",
                    "enabled": True,
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["config"]["skill_id"] is None
            assert confirmed.json()["config"]["model"] == "resolved-model"
            with database.transaction() as db:
                schedule = db.get(Schedule, confirmed.json()["id"])
                persisted = json.loads(schedule.config_json)
                assert persisted["skill_id"] is None
                assert persisted["model"] == "resolved-model"
    assert model_requests == [None, "resolved-model", "resolved-model"]


@pytest.mark.parametrize("with_skill", [False, True], ids=["prompt-only", "bound-skill"])
def test_executor_passes_optional_skill_binding_to_pi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_skill: bool
) -> None:
    from app.services import schedule_access, schedule_executor

    captured = []
    token_usage = {"total_tokens": 12}
    files = [{"name": "result.txt", "href": "/files/result.txt"}]

    async def run_pi(self, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            reply="本次执行结果", generated_files=files, token_usage=token_usage
        )

    monkeypatch.setattr(
        schedule_access, "list_schedule_skills",
        lambda: [{"id": "approved-skill", "version": "1.0.0", "digest": "a" * 64}],
    )
    monkeypatch.setattr(
        schedule_executor.PiRuntimeSettings, "from_env",
        lambda: SimpleNamespace(execution_mode="sandbox", root=tmp_path / "pi"),
    )
    monkeypatch.setattr(schedule_executor, "create_capability_token", lambda **kwargs: "test-capability")
    monkeypatch.setattr(
        schedule_executor, "_resolved_pi_model",
        lambda *args: (SimpleNamespace(model="bound-model"), "test-provider"),
    )
    monkeypatch.setattr(schedule_executor, "_reserve_schedule_budget", lambda **kwargs: None)
    monkeypatch.setattr(schedule_executor, "_recordable_usage", lambda *args: token_usage)
    monkeypatch.setattr(schedule_executor.PiRpcRunner, "run", run_pi)
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            config = json.loads(schedule.config_json)
            if not with_skill:
                for key in ("skill_id", "skill_version", "skill_digest"):
                    config[key] = None
                schedule.config_json = json.dumps(config)
        dispatcher = ScheduleDispatcher(worker_id="worker-optional-skill")
        assert dispatcher.dispatch_due_once() == 1
        claim = dispatcher.claim_next()
        assert claim is not None
        asyncio.run(ScheduleExecutor(worker_id="worker-optional-skill").execute(claim))
        assert len(captured) == 1
        # 无绑定时 Pi 不得发布或加载任何已启用 Skill（include_enabled 与 --skill 都由 enable_skills 决定）。
        assert captured[0]["agent"].pi_config.enable_skills is with_skill
        # 无人值守沙箱的工具白名单与绑定无关，始终收敛。
        assert captured[0]["agent"].pi_config.tools == ("read", "write", "edit", "grep", "find")
        if with_skill:
            assert captured[0]["prompt"] == f"/skill:approved-skill\n{config['instruction']}"
            assert captured[0]["required_skill"] == ("approved-skill", "1.0.0", "a" * 64)
        else:
            assert captured[0]["prompt"] == config["instruction"]
            assert captured[0]["required_skill"] is None
        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            assert run.status == "completed", run.error_code
            assert run.body == "本次执行结果"
            assert json.loads(run.result_files_json) == files
            assert json.loads(run.usage_json) == token_usage


def test_due_reminder_is_committed_once_without_pi_execution(tmp_path: Path) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db)
            schedule_id = schedule.id

        dispatcher = ScheduleDispatcher(worker_id="worker-a")
        assert dispatcher.dispatch_due_once() == 1
        assert dispatcher.dispatch_due_once() == 0

        with database.transaction() as db:
            runs = list(
                db.query(ScheduleRun).filter(ScheduleRun.schedule_id == schedule_id)
            )
            assert len(runs) == 1
            assert runs[0].status == "completed"
            assert runs[0].attempt_no == 0
            assert runs[0].runtime_id is None
            assert runs[0].body == "整理已授权资料并生成新的结果"


def test_snoozed_reminder_is_delivered_without_claiming_pi(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db)
            stamp = now()
            run = ScheduleRun(
                id="srn_snoozed_reminder",
                schedule_id=schedule.id,
                owner_user_id=schedule.owner_user_id,
                trigger_kind="snooze",
                occurrence_key="snooze:parent:request",
                scheduled_for=stamp - timedelta(seconds=1),
                config_json=schedule.config_json,
                schedule_revision=schedule.revision,
                status="queued",
                status_revision=1,
                result_files_json="[]",
                progress_json="[]",
                next_attempt_at=stamp - timedelta(seconds=1),
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(run)

        dispatcher = ScheduleDispatcher(worker_id="worker-a")
        assert dispatcher.dispatch_queued_reminders_once() == 1
        assert dispatcher.claim_next() is None

        with database.transaction() as db:
            run = db.get(ScheduleRun, "srn_snoozed_reminder")
            assert run is not None
            assert run.status == "completed"
            assert run.attempt_no == 0
            assert run.runtime_id is None
            assert run.body == "整理已授权资料并生成新的结果"


def test_claim_epoch_fences_a_late_worker_write(tmp_path: Path) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            stamp = now()
            run = ScheduleRun(
                id="srn_fenced",
                schedule_id=schedule.id,
                owner_user_id=schedule.owner_user_id,
                trigger_kind="manual",
                occurrence_key="request-1",
                scheduled_for=stamp,
                config_json=schedule.config_json,
                schedule_revision=1,
                status="queued",
                status_revision=1,
                result_files_json="[]",
                progress_json="[]",
                next_attempt_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(run)

        dispatcher = ScheduleDispatcher(worker_id="worker-old")
        claim = dispatcher.claim_next()
        assert claim is not None
        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            assert run is not None
            run.claim_epoch += 1
            run.lease_owner = "worker-new"

        executor = ScheduleExecutor(worker_id="worker-old")
        assert not executor._finalize(
            claim,
            status="completed",
            capture=_ExecutionCapture(body="stale result"),
        )
        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            assert run is not None
            assert run.body is None
            assert run.status == "running"


@pytest.mark.parametrize(
    ("trigger_kind", "edited", "expected_status"),
    [
        ("manual", False, "enabled"),
        ("scheduled", True, "enabled"),
        ("scheduled", False, "completed"),
    ],
)
def test_one_time_completion_preserves_manual_and_newer_schedule(
    tmp_path: Path, trigger_kind: str, edited: bool, expected_status: str
) -> None:
    with _isolated_db(tmp_path):
        stamp = now()
        future = stamp + timedelta(days=1)
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            config = json.loads(schedule.config_json)
            if trigger_kind == "manual":
                config["rule"] = {
                    "kind": "once",
                    "date": future.date().isoformat(),
                    "time": future.strftime("%H:%M"),
                }
                schedule.config_json = json.dumps(config)
            schedule.next_run_at = future if trigger_kind == "manual" else None
            db.add(
                ScheduleRun(
                    id="srn_once_lifecycle",
                    schedule_id=schedule.id,
                    owner_user_id=schedule.owner_user_id,
                    trigger_kind=trigger_kind,
                    occurrence_key="once-lifecycle",
                    scheduled_for=stamp,
                    config_json=schedule.config_json,
                    schedule_revision=1,
                    status="queued",
                    status_revision=1,
                    next_attempt_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        claim = ScheduleDispatcher(worker_id="once-worker").claim_next()
        assert claim is not None
        if edited:
            with database.transaction() as db:
                schedule = db.get(Schedule, "sch_skill")
                config = json.loads(schedule.config_json)
                config["rule"] = {"kind": "daily", "time": "09:00"}
                schedule.config_json = json.dumps(config)
                schedule.revision = 2
                schedule.next_run_at = future
        assert ScheduleExecutor(worker_id="once-worker")._finalize(
            claim, status="completed", capture=_ExecutionCapture(body="本次结果")
        )
        with database.transaction() as db:
            schedule = db.get(Schedule, "sch_skill")
            assert schedule.status == expected_status
            if expected_status == "enabled":
                assert schedule.next_run_at.replace(tzinfo=future.tzinfo) == future
            else:
                assert schedule.next_run_at is None
            assert db.get(ScheduleRun, claim.run_id).body == "本次结果"


@pytest.mark.parametrize(
    ("plan_status", "plan_revision", "run_status", "error_code", "expected_status"),
    [
        ("archived", 2, "completed", None, "archived"),
        ("archived", 2, "outcome_unknown", None, "archived"),
        ("enabled", 2, "failed", "scope_unavailable", "enabled"),
        ("enabled", 1, "failed", "scope_unavailable", "blocked"),
        ("enabled", 2, "outcome_unknown", None, "blocked"),
        ("enabled", 2, "awaiting_user", None, "blocked"),
        ("enabled", 2, "failed", "usage_unknown", "blocked"),
    ],
)
def test_terminal_result_preserves_schedule_control_state(
    tmp_path: Path,
    plan_status: str,
    plan_revision: int,
    run_status: str,
    error_code: str | None,
    expected_status: str,
) -> None:
    with _isolated_db(tmp_path):
        stamp = now()
        future = stamp + timedelta(days=1)
        expected_next = (
            future if plan_status == "enabled" and plan_revision > 1 else None
        )
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            db.add(
                ScheduleRun(
                    id="srn_archived_inflight",
                    schedule_id=schedule.id,
                    owner_user_id=schedule.owner_user_id,
                    trigger_kind="scheduled",
                    occurrence_key="archived-inflight",
                    scheduled_for=stamp,
                    config_json=schedule.config_json,
                    schedule_revision=1,
                    status="queued",
                    status_revision=1,
                    next_attempt_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        claim = ScheduleDispatcher(worker_id="archive-worker").claim_next()
        assert claim is not None
        with database.transaction() as db:
            schedule = db.get(Schedule, "sch_skill")
            schedule.status = plan_status
            schedule.revision = plan_revision
            schedule.next_run_at = expected_next
            if expected_next is not None:
                config = json.loads(schedule.config_json)
                config["rule"] = {"kind": "daily", "time": "09:00"}
                schedule.config_json = json.dumps(config)
        assert ScheduleExecutor(worker_id="archive-worker")._finalize(
            claim,
            status=run_status,
            capture=_ExecutionCapture(body="旧执行已产生的结果"),
            error_code=error_code,
        )
        with database.transaction() as db:
            schedule = db.get(Schedule, "sch_skill")
            assert schedule.status == expected_status
            if expected_next is None:
                assert schedule.next_run_at is None
            else:
                assert (
                    schedule.next_run_at.replace(tzinfo=future.tzinfo) == expected_next
                )
            run = db.get(ScheduleRun, claim.run_id)
            assert run.status == run_status
            assert run.body == "旧执行已产生的结果"


def test_notification_failure_does_not_rollback_completed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            stamp = now()
            db.add(
                ScheduleRun(
                    id="srn_notification_failure",
                    schedule_id=schedule.id,
                    owner_user_id=schedule.owner_user_id,
                    trigger_kind="manual",
                    occurrence_key="request-notification-failure",
                    scheduled_for=stamp,
                    config_json=schedule.config_json,
                    schedule_revision=1,
                    status="queued",
                    status_revision=1,
                    result_files_json="[]",
                    progress_json="[]",
                    next_attempt_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )

        claim = ScheduleDispatcher(worker_id="worker-a").claim_next()
        assert claim is not None

        emit_notification = schedule_repo.emit_notification

        def fail_notification(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("notification unavailable")

        monkeypatch.setattr(schedule_repo, "emit_notification", fail_notification)
        assert ScheduleExecutor(worker_id="worker-a")._finalize(
            claim,
            status="completed",
            capture=_ExecutionCapture(body="persisted result"),
        )

        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            assert run is not None
            assert run.status == "completed"
            assert run.body == "persisted result"
            assert run.notification_revision < run.status_revision

        monkeypatch.setattr(schedule_repo, "emit_notification", emit_notification)
        dispatcher = ScheduleDispatcher(worker_id="notification-recovery")
        assert dispatcher.dispatch_notifications_once() == 1
        assert dispatcher.dispatch_notifications_once() == 0
        assert dispatcher.claim_next() is None
        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            assert run.status == "completed"
            assert run.body == "persisted result"
            assert run.attempt_no == 1
            assert run.notification_revision == run.status_revision


def test_cancel_commits_when_notification_fails_after_write_and_dispatcher_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            user = db.get(User, schedule.owner_user_id)
            assert user is not None
            stamp = now()
            run = ScheduleRun(
                id="srn_cancel_notification_failure",
                schedule_id=schedule.id,
                owner_user_id=schedule.owner_user_id,
                trigger_kind="manual",
                occurrence_key="request-cancel-notification-failure",
                scheduled_for=stamp,
                config_json=schedule.config_json,
                schedule_revision=1,
                status="queued",
                status_revision=1,
                result_files_json="[]",
                progress_json="[]",
                next_attempt_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(run)
            db.flush()
            schedule_id = schedule.id
            run_id = run.id

        emit_notification = schedule_repo.emit_notification

        def fail_after_notification_write(
            db: Any, run: ScheduleRun, **kwargs: Any
        ) -> None:
            event = emit_notification(db, run, **kwargs)
            assert event is not None
            raise RuntimeError("notification unavailable after write")

        monkeypatch.setattr(
            schedule_repo, "emit_notification", fail_after_notification_write
        )
        response = cancel_schedule_run(
            schedule_id,
            run_id,
            RunActionRequest(request_id="cancel-notification-failure"),
            user,
        )
        assert response.status == "cancelled"

        with database.transaction() as db:
            persisted = db.get(ScheduleRun, run_id)
            assert persisted is not None
            assert persisted.status == "cancelled"
            assert persisted.cancel_requested is True
            assert persisted.notification_revision < persisted.status_revision
            assert (
                db.query(ScheduleNotification)
                .filter(ScheduleNotification.run_id == run_id)
                .count()
                == 0
            )

        monkeypatch.setattr(schedule_repo, "emit_notification", emit_notification)
        dispatcher = ScheduleDispatcher(worker_id="cancel-notification-recovery")
        assert dispatcher.dispatch_notifications_once() == 1
        assert dispatcher.dispatch_notifications_once() == 0
        assert dispatcher.claim_next() is None

        with database.transaction() as db:
            persisted = db.get(ScheduleRun, run_id)
            assert persisted is not None
            assert persisted.status == "cancelled"
            assert persisted.cancel_requested is True
            assert persisted.attempt_no == 0
            assert persisted.notification_revision == persisted.status_revision
            notifications = list(
                db.query(ScheduleNotification).filter(
                    ScheduleNotification.run_id == run_id
                )
            )
            assert len(notifications) == 1
            assert notifications[0].status == "cancelled"


def test_pre_model_failure_requeues_with_bounded_backoff(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            stamp = now()
            db.add(
                ScheduleRun(
                    id="srn_preflight_retry",
                    schedule_id=schedule.id,
                    owner_user_id=schedule.owner_user_id,
                    trigger_kind="manual",
                    occurrence_key="request-preflight-retry",
                    scheduled_for=stamp,
                    config_json=schedule.config_json,
                    schedule_revision=1,
                    status="queued",
                    status_revision=1,
                    result_files_json="[]",
                    progress_json="[]",
                    next_attempt_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )

        dispatcher = ScheduleDispatcher(worker_id="worker-a")
        claim = dispatcher.claim_next()
        assert claim is not None
        executor = ScheduleExecutor(worker_id="worker-a")
        assert executor._retry_pre_model_failure(
            claim,
            capture=_ExecutionCapture(),
            error_message="执行前安全校验暂时无法完成",
        )

        with database.transaction() as db:
            run = db.get(ScheduleRun, claim.run_id)
            attempt = (
                db.query(ScheduleRunAttempt)
                .filter(
                    ScheduleRunAttempt.run_id == claim.run_id,
                    ScheduleRunAttempt.attempt_no == claim.attempt_no,
                    ScheduleRunAttempt.claim_epoch == claim.claim_epoch,
                )
                .one()
            )
            assert run is not None
            assert run.status == "queued"
            assert run.retryable
            assert run.lease_owner is None
            assert run.next_attempt_at is not None
            assert run.next_attempt_at > run.updated_at
            assert attempt.status == "failed"
            assert attempt.error_code == "preflight_failed"


@pytest.mark.parametrize(
    ("initial_status", "revision", "expected_status"),
    [("enabled", 1, "blocked"), ("enabled", 2, "blocked"), ("archived", 2, "archived")],
)
def test_expired_model_attempt_becomes_unknown_and_is_not_requeued(
    tmp_path: Path, initial_status: str, revision: int, expected_status: str
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            stamp = now()
            run = ScheduleRun(
                id="srn_unknown",
                schedule_id=schedule.id,
                owner_user_id=schedule.owner_user_id,
                trigger_kind="scheduled",
                occurrence_key="2026-09-24T00:00Z",
                scheduled_for=stamp,
                config_json=schedule.config_json,
                schedule_revision=1,
                status="running",
                status_revision=2,
                result_files_json="[]",
                progress_json="[]",
                attempt_no=1,
                claim_epoch=4,
                lease_owner="dead-worker",
                lease_until=stamp - timedelta(seconds=1),
                started_at=stamp - timedelta(minutes=2),
                created_at=stamp - timedelta(minutes=2),
                updated_at=stamp - timedelta(minutes=2),
            )
            db.add(run)
            db.add(
                ScheduleRunAttempt(
                    id="sat_unknown",
                    run_id=run.id,
                    attempt_no=1,
                    claim_epoch=4,
                    lease_owner="dead-worker",
                    started_at=run.started_at,
                    status="running",
                    phase="model_started",
                )
            )
            schedule.status = initial_status
            schedule.revision = revision
            if initial_status == "archived":
                schedule.next_run_at = None
            elif revision > 1:
                future = stamp + timedelta(days=1)
                config = json.loads(schedule.config_json)
                config["rule"] = {
                    "kind": "once",
                    "date": future.date().isoformat(),
                    "time": future.strftime("%H:%M"),
                }
                schedule.config_json = json.dumps(config)
                schedule.next_run_at = future

        dispatcher = ScheduleDispatcher(worker_id="recovery-worker")
        assert dispatcher.recover_expired() == 1
        assert dispatcher.claim_next() is None

        with database.transaction() as db:
            run = db.get(ScheduleRun, "srn_unknown")
            schedule = db.get(Schedule, "sch_skill")
            assert run is not None and schedule is not None
            assert run.status == "outcome_unknown"
            assert run.error_code == "lease_expired_outcome_unknown"
            assert run.lease_owner is None
            assert schedule.status == expected_status


def test_pre_model_expiry_is_the_only_automatic_retry_boundary(tmp_path: Path) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            schedule = _schedule(db, kind="skill")
            stamp = now()
            run = ScheduleRun(
                id="srn_safe_retry",
                schedule_id=schedule.id,
                owner_user_id=schedule.owner_user_id,
                trigger_kind="manual",
                occurrence_key="request-safe",
                scheduled_for=stamp,
                config_json=schedule.config_json,
                schedule_revision=1,
                status="running",
                status_revision=2,
                result_files_json="[]",
                progress_json="[]",
                attempt_no=1,
                claim_epoch=1,
                lease_owner="dead-worker",
                lease_until=stamp - timedelta(seconds=1),
                started_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(run)
            db.add(
                ScheduleRunAttempt(
                    id="sat_safe_retry",
                    run_id=run.id,
                    attempt_no=1,
                    claim_epoch=1,
                    lease_owner="dead-worker",
                    started_at=stamp,
                    status="running",
                    phase="preflight",
                )
            )

        dispatcher = ScheduleDispatcher(worker_id="recovery-worker")
        assert dispatcher.recover_expired() == 1
        with database.transaction() as db:
            run = db.get(ScheduleRun, "srn_safe_retry")
            assert run is not None
            assert run.status == "queued"
            assert run.retryable
            assert run.error_code == "lease_expired_safe_retry"


def test_required_skill_snapshot_rejects_missing_approved_digest(
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(root=tmp_path / "pi")
    agent = SimpleNamespace(tenant_id="_system", id="doc-nexus-pi")
    workspace = (
        runtime.root / "workspaces" / "_system" / "doc-nexus-pi" / "pi-schedule-run"
    )

    with pytest.raises(SkillNotFoundError):
        prepare_agent_skills(
            agent,
            workspace,
            runtime,
            required_skill=("approved-skill", "1.0.0", "a" * 64),
        )


def test_schedule_harness_task_disables_shell_network_and_install(
    tmp_path: Path,
) -> None:
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            _user(db)
        runtime = cast(Any, SimpleNamespace(root=tmp_path / "pi-runtime"))
        agent = SimpleNamespace(tenant_id="_system", id="doc-nexus-pi")
        workspace = _ensure_restricted_harness_task(
            runtime,
            runtime_id="pi-schedule-srn_restricted",
            owner_user_id="usr_schedule",
            instruction="只读分析并生成新成果",
            budget_tokens=1000,
            timeout_seconds=60,
            agent=agent,
        )
        assert workspace.is_dir()
        with database.transaction() as db:
            row = harness_repo.get_task(db, "pi-schedule-srn_restricted")
            assert row is not None
            permissions = json.loads(row.permissions_json)
            assert permissions["file_read"]
            assert permissions["file_write"]
            assert permissions["sandbox"]
            assert permissions["approved_skill_execution"]
            assert not permissions["shell"]
            assert not permissions["network"]
            assert not permissions["install_packages"]
            assert not permissions["skill_install_request"]
            assert not permissions["database_write"]


@pytest.mark.parametrize("expand_shell", [False, True])
def test_persisted_schedule_binding_is_reusable_without_permission_expansion(
    tmp_path: Path, expand_shell: bool
) -> None:
    from app.services.schedule_access import ScheduleAccessError

    with _isolated_db(tmp_path):
        with database.transaction() as db:
            _user(db)
        runtime = cast(Any, SimpleNamespace(root=tmp_path / "pi-runtime"))
        binding: dict[str, Any] = {
            "runtime_id": "pi-schedule-srn_recovery",
            "owner_user_id": "usr_schedule",
            "instruction": "只读分析并生成新成果",
            "budget_tokens": 1000,
            "timeout_seconds": 60,
            "agent": SimpleNamespace(tenant_id="_system", id="doc-nexus-pi"),
        }
        workspace = _ensure_restricted_harness_task(runtime, **binding)
        if expand_shell:
            with database.transaction() as db:
                row = harness_repo.get_task(db, binding["runtime_id"])
                assert row is not None
                permissions = json.loads(row.permissions_json)
                permissions["shell"] = True
                row.permissions_json = json.dumps(permissions)
            with pytest.raises(ScheduleAccessError) as rejected:
                _ensure_restricted_harness_task(runtime, **binding)
            assert rejected.value.code == "runtime_binding_invalid"
        else:
            assert _ensure_restricted_harness_task(runtime, **binding) == workspace


def test_notification_stream_flags_only_unhandled_reminders(tmp_path: Path) -> None:
    """站内提醒是否常驻，取决于“提醒已触发但未处理”，而不是所有完成事件。"""
    with _isolated_db(tmp_path):
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            owner_id = login["user"]["id"]
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            baseline = client.get("/schedules/notifications", headers=headers).json()["cursor"]
            stamp = now()
            reminder_config = _config(kind="reminder")
            skill_config = _config(kind="skill")
            skill_config["notification"] = {
                "sound": False,
                "desktop": False,
                "on_success": True,
                "on_failure": True,
            }
            cases = (
                ("sch_notify_reminder", "reminder", reminder_config),
                ("sch_notify_skill", "skill", skill_config),
            )
            with database.transaction() as db:
                for index, (schedule_id, kind, config) in enumerate(cases):
                    db.add(
                        Schedule(
                            id=schedule_id,
                            owner_user_id=owner_id,
                            name=config["name"],
                            kind=kind,
                            status="enabled",
                            revision=1,
                            config_json=json.dumps(config),
                            next_run_at=None,
                            created_at=stamp,
                            updated_at=stamp,
                        )
                    )
                    db.flush()
                    run = ScheduleRun(
                        id=f"srn_notify_{index}",
                        schedule_id=schedule_id,
                        owner_user_id=owner_id,
                        trigger_kind="scheduled",
                        occurrence_key=f"2026-09-24T13:0{index}:00Z",
                        scheduled_for=stamp,
                        config_json=json.dumps(config),
                        schedule_revision=1,
                        status="completed",
                        status_revision=1,
                        created_at=stamp,
                        updated_at=stamp,
                        completed_at=stamp,
                    )
                    db.add(run)
                    db.flush()
                    assert schedule_repo.emit_notification(db, run) is not None
            payload = client.get(
                "/schedules/notifications", params={"cursor": baseline}, headers=headers
            ).json()
            items = {item["schedule_id"]: item for item in payload["items"]}
            assert items["sch_notify_reminder"]["requires_action"] is True
            # 完成的定时任务即使要求播报，也不需要用户确认。
            assert items["sch_notify_skill"]["requires_action"] is False


def test_task_center_keeps_only_newest_pending_reminder(tmp_path: Path) -> None:
    """周期提醒只有最新一条未处理记录保持「提醒待处理」，更早的降级为可读结果。"""
    with _isolated_db(tmp_path):
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            owner_id = login["user"]["id"]
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            stamp = now()
            config = _config(kind="reminder")
            runs = (
                ("srn_periodic_old", stamp - timedelta(days=1)),
                ("srn_periodic_new", stamp),
            )
            with database.transaction() as db:
                db.add(
                    Schedule(
                        id="sch_periodic",
                        owner_user_id=owner_id,
                        name="收衣服提醒",
                        kind="reminder",
                        status="enabled",
                        revision=1,
                        config_json=json.dumps(config),
                        next_run_at=None,
                        created_at=stamp - timedelta(days=1),
                        updated_at=stamp - timedelta(days=1),
                    )
                )
                db.flush()
                for run_id, scheduled_for in runs:
                    db.add(
                        ScheduleRun(
                            id=run_id,
                            schedule_id="sch_periodic",
                            owner_user_id=owner_id,
                            trigger_kind="scheduled",
                            occurrence_key=scheduled_for.isoformat(),
                            scheduled_for=scheduled_for,
                            config_json=json.dumps(config),
                            schedule_revision=1,
                            status="completed",
                            status_revision=1,
                            created_at=scheduled_for,
                            updated_at=scheduled_for,
                            completed_at=scheduled_for,
                        )
                    )
                db.flush()

            def attention_ids() -> dict[str, dict[str, Any]]:
                payload = client.get(
                    "/tasks", params={"view": "attention"}, headers=headers
                ).json()
                assert payload["next_offset"] is None
                return {item["id"]: item for item in payload["items"]}

            items = attention_ids()
            newest = items["schedule:srn_periodic_new"]
            older = items["schedule:srn_periodic_old"]
            # 最新一条是待处理提醒，并带出本次触发的原定时间，便于区分每一次触发。
            assert newest["requires_action"] is True
            assert newest["status"] == "completed"
            assert datetime.fromisoformat(
                newest["scheduled_for"].replace("Z", "+00:00")
            ) == runs[1][1]
            # 更早的未处理提醒不再占用「提醒待处理」，改为未读结果留在需要关注。
            assert older["requires_action"] is False
            assert older["unread"] is True
            assert datetime.fromisoformat(
                older["scheduled_for"].replace("Z", "+00:00")
            ) == runs[0][1]

            acknowledged = client.post(
                "/tasks/acknowledgements",
                json={"task_ids": ["schedule:srn_periodic_old"]},
                headers=headers,
            )
            assert acknowledged.status_code == 200
            assert "schedule:srn_periodic_old" not in attention_ids()
            assert "schedule:srn_periodic_new" in attention_ids()

            handled = client.post(
                "/schedules/sch_periodic/runs/srn_periodic_new/handle",
                json={"request_id": "req-handle-periodic"},
                headers=headers,
            )
            assert handled.status_code == 200
            assert handled.json()["handled_at"]
            after_handle = attention_ids()
            assert "schedule:srn_periodic_new" not in after_handle
            # 处理最新一条后，已读的旧条目不能被重新提升为「提醒待处理」。
            assert "schedule:srn_periodic_old" not in after_handle
            history = client.get(
                "/tasks", params={"view": "history"}, headers=headers
            ).json()
            assert {"schedule:srn_periodic_new", "schedule:srn_periodic_old"} <= {
                item["id"] for item in history["items"]
            }


def test_read_pending_reminder_stays_until_handled(tmp_path: Path) -> None:
    """已读不代替处理：最新一条未处理提醒即使已读，也继续显示「提醒待处理」。"""
    with _isolated_db(tmp_path):
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            owner_id = login["user"]["id"]
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            stamp = now()
            config = _config(kind="reminder")
            with database.transaction() as db:
                db.add(
                    Schedule(
                        id="sch_single_reminder",
                        owner_user_id=owner_id,
                        name="收衣服提醒",
                        kind="reminder",
                        status="enabled",
                        revision=1,
                        config_json=json.dumps(config),
                        next_run_at=None,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.flush()
                db.add(
                    ScheduleRun(
                        id="srn_single",
                        schedule_id="sch_single_reminder",
                        owner_user_id=owner_id,
                        trigger_kind="scheduled",
                        occurrence_key=stamp.isoformat(),
                        scheduled_for=stamp,
                        config_json=json.dumps(config),
                        schedule_revision=1,
                        status="completed",
                        status_revision=1,
                        created_at=stamp,
                        updated_at=stamp,
                        completed_at=stamp,
                    )
                )
                db.flush()
            acknowledged = client.post(
                "/tasks/acknowledgements",
                json={"task_ids": ["schedule:srn_single"]},
                headers=headers,
            )
            assert acknowledged.status_code == 200
            items = client.get(
                "/tasks", params={"view": "attention"}, headers=headers
            ).json()["items"]
            pending = next(item for item in items if item["id"] == "schedule:srn_single")
            assert pending["unread"] is False
            assert pending["requires_action"] is True


def test_group_target_requires_explicit_grant_and_never_exposes_robot_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """机器人凭据不回显；创建群目标不会自动授予配置它的管理员。"""
    from cryptography.fernet import Fernet

    monkeypatch.setattr(
        settings, "notification_fernet_keys",
        json.dumps({"1": Fernet.generate_key().decode("ascii")}),
    )
    monkeypatch.setattr(settings, "notification_fernet_current_version", "1")
    with _isolated_db(tmp_path):
        with TestClient(app) as client:
            login = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            credential = {"access_token": "a" * 32, "secret": "private-robot-secret"}
            created = client.post(
                "/admin/notification-connections",
                headers=headers,
                json={
                    "request_id": "conn-initial",
                    "name": "测试钉钉群",
                    "kind": "dingtalk_group",
                    "config": {},
                    "credential": credential,
                },
            )
            assert created.status_code == 201, created.text
            connection_id = created.json()["id"]
            assert credential["secret"] not in created.text
            assert credential["access_token"] not in created.text
            assert credential["secret"] not in client.get(
                "/admin/notification-connections", headers=headers
            ).text
            target = client.post(
                "/admin/notification-targets",
                headers=headers,
                json={
                    "request_id": "group-initial",
                    "connection_id": connection_id,
                    "label": "已同意接收的测试群",
                    "consent": True,
                },
            )
            assert target.status_code == 201, target.text
            target_id = target.json()["id"]
            assert target_id not in {
                item["id"]
                for item in client.get("/notification-targets", headers=headers).json()["items"]
            }
            grant = client.post(
                f"/admin/notification-targets/{target_id}/grants",
                headers=headers,
                json={"request_id": "grant-admin", "user_id": login["user"]["id"]},
            )
            assert grant.status_code == 200, grant.text
            visible = client.get("/notification-targets", headers=headers)
            assert visible.status_code == 200
            assert {item["id"] for item in visible.json()["items"]} == {target_id}
            enabled = client.patch(
                "/me/notification-preferences",
                headers=headers,
                json={"request_id": "allow-external", "external_enabled": True},
            )
            assert enabled.status_code == 200, enabled.text
            config = _config(kind="reminder")
            config["notification"]["external"] = [{
                "target_id": target_id,
                "target_revision": grant.json()["revision"],
                "events": ["completed"],
                "content_mode": "status_only",
            }]
            stamp = now()
            with database.transaction() as db:
                schedule = Schedule(
                    id="sch_external_unknown", owner_user_id=login["user"]["id"],
                    name="不可外泄的任务名", kind="reminder", status="enabled",
                    revision=1, config_json=json.dumps(config), next_run_at=None,
                    created_at=stamp, updated_at=stamp,
                )
                run = ScheduleRun(
                    id="srn_external_unknown", schedule_id=schedule.id,
                    owner_user_id=login["user"]["id"], trigger_kind="scheduled",
                    occurrence_key="external-unknown", scheduled_for=stamp,
                    config_json=json.dumps(config), schedule_revision=1,
                    status="completed", status_revision=1, completed_at=stamp,
                    created_at=stamp, updated_at=stamp,
                )
                db.add_all((schedule, run))
                db.flush()
                assert schedule_repo.emit_notification(db, run) is not None
            from app.services.notification_channels import DeliveryResult
            from app.services.notification_dispatcher import NotificationDispatcher

            sent: list[str] = []
            def uncertain_send(_connection, _target, text: str) -> DeliveryResult:
                sent.append(text)
                return DeliveryResult("unknown", "webhook_outcome_unknown")

            monkeypatch.setattr(
                "app.services.notification_dispatcher.send_notification", uncertain_send
            )
            dispatcher = NotificationDispatcher()
            assert dispatcher.run_once() is True
            assert dispatcher.run_once() is False
            assert len(sent) == 1
            assert "不可外泄" not in sent[0]
            receipt = client.get(
                "/schedules/sch_external_unknown/runs/srn_external_unknown/notifications",
                headers=headers,
            )
            assert receipt.status_code == 200, receipt.text
            assert [item["status"] for item in receipt.json()["items"]] == ["unknown"]
            revoked = client.request(
                "DELETE",
                f"/admin/notification-targets/{target_id}/grants/{login['user']['id']}",
                headers=headers,
                json={"request_id": "revoke-admin"},
            )
            assert revoked.status_code == 200, revoked.text
            assert client.get("/notification-targets", headers=headers).json()["items"] == []
