from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import timedelta
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import schedules as schedules_api
from app.api.deps import current_user
from app.core.security import iso, now
from app.models.auth import User
from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
from app.models.notification_delivery import (
    NotificationConnection,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationPreference,
    NotificationTarget,
    NotificationTargetGrant,
    NotificationVerificationChallenge,
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
from app.models.user_closure import TaskAcknowledgement
from app.repositories import schedules as repository
from app.repositories import tasks as task_repository
from app.schemas.schedules import DraftCreateRequest, ScheduleConfig
from app.services import schedules as service


def _config(name: str = "喝水提醒") -> dict:
    return {
        "name": name,
        "kind": "reminder",
        "instruction": "提醒我喝水",
        "timezone": "Asia/Shanghai",
        "rule": {"kind": "daily", "time": "09:30"},
        "space_ids": [],
        "document_ids": [],
        "notification": {
            "sound": True,
            "desktop": False,
            "on_success": False,
            "on_failure": True,
        },
    }


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        User.__table__,
        ModelEndpoint.__table__,
        ModelEndpointModel.__table__,
        TaskAcknowledgement.__table__,
        Schedule.__table__,
        ScheduleDraft.__table__,
        ScheduleRun.__table__,
        ScheduleRunAttempt.__table__,
        ScheduleNotificationCursor.__table__,
        ScheduleNotification.__table__,
        NotificationConnection.__table__,
        NotificationTarget.__table__,
        NotificationTargetGrant.__table__,
        NotificationPreference.__table__,
        NotificationVerificationChallenge.__table__,
        NotificationDelivery.__table__,
        NotificationDeliveryAttempt.__table__,
        ScheduleOperation.__table__,
    ):
        cast(Table, table).create(engine)
    session = Session(engine, expire_on_commit=False)
    timestamp = now()
    session.add_all(
        [
            User(
                id="usr_owner",
                username="schedule-owner",
                display_name="任务所有者",
                password_hash="unused",
                role="user",
                status="active",
                first_login=False,
                created_at=timestamp,
            ),
            User(
                id="usr_other",
                username="schedule-other",
                display_name="其他用户",
                password_hash="unused",
                role="user",
                status="active",
                first_login=False,
                created_at=timestamp,
            ),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_schedule_list_search_filters_before_paging_and_preserves_owner(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = now()
    rows = [
        ("other", "usr_other", "月报", "其他人的月报", "reminder", "enabled"),
        ("noise", "usr_owner", "不相关", "普通提醒", "reminder", "enabled"),
        ("paused", "usr_owner", "月报暂停", "暂停提醒", "reminder", "paused"),
        ("skill", "usr_owner", "月报任务", "任务说明", "skill", "enabled"),
        ("name", "usr_owner", "月报 Monthly", "普通提醒", "reminder", "enabled"),
        ("description", "usr_owner", "财务提醒", "整理月报 Monthly", "reminder", "enabled"),
        ("literal", "usr_owner", r"进度100%_完成\归档", '核对"凭证"\n第二行', "reminder", "enabled"),
        ("lookalike", "usr_owner", "进度100XX完成归档", "检查凭证", "reminder", "enabled"),
        ("literal-description", "usr_owner", "字符提醒", r"比例50%_和路径\目录", "reminder", "enabled"),
    ]
    for index, (key, owner, name, instruction, kind, status) in enumerate(rows):
        config = _config(name)
        config.update(kind=kind, instruction=instruction)
        db.add(
            Schedule(
                id=f"search-{key}",
                owner_user_id=owner,
                name=name,
                kind=kind,
                status=status,
                revision=1,
                config_json=json.dumps(config),
                next_run_at=None,
                created_at=stamp,
                updated_at=stamp - timedelta(seconds=index),
            )
        )
    db.commit()
    owner = db.get(User, "usr_owner")
    assert owner is not None
    app = FastAPI()
    app.include_router(schedules_api.router)
    app.dependency_overrides[current_user] = lambda: owner
    monkeypatch.setattr(schedules_api, "transaction", lambda: nullcontext(db))
    with TestClient(app) as client:
        filters = {"query": "  月报  ", "kind": "reminder", "status": "enabled", "limit": 1}
        first = client.get("/schedules", params=filters)
        assert first.status_code == 200
        assert [item["id"] for item in first.json()["items"]] == ["search-name"]
        assert first.json()["next_offset"] == 1
        second = client.get("/schedules", params={**filters, "offset": 1})
        assert second.status_code == 200
        assert [item["id"] for item in second.json()["items"]] == ["search-description"]
        assert second.json()["next_offset"] is None

        for query, expected in [
            ("月报", ["search-paused", "search-skill", "search-name", "search-description"]),
            ("mOnThLy", ["search-name", "search-description"]),
            ("%", ["search-literal", "search-literal-description"]),
            ("_", ["search-literal", "search-literal-description"]),
            ("\\", ["search-literal", "search-literal-description"]),
            ("100%_", ["search-literal"]),
            ('"凭证"\n第二行', ["search-literal"]),
            ("asia/shanghai", []),
            ("不存在", []),
        ]:
            response = client.get("/schedules", params={"query": query})
            assert response.status_code == 200
            assert [item["id"] for item in response.json()["items"]] == expected
            assert response.json()["next_offset"] is None

        unfiltered = client.get("/schedules")
        blank = client.get("/schedules", params={"query": " \t "})
        assert blank.status_code == unfiltered.status_code == 200
        assert blank.json() == unfiltered.json()
        assert len(blank.json()["items"]) == len(rows) - 1


def test_strict_schema_rejects_owner_and_allows_unbound_skill_draft() -> None:
    payload = {"config": _config(), "request_id": "req-1", "owner_user_id": "forged"}
    with pytest.raises(ValidationError):
        DraftCreateRequest.model_validate(payload)

    skill = _config("日报")
    skill.update(kind="skill", instruction="汇总日报")
    validated = ScheduleConfig.model_validate(skill)
    assert validated.skill_id is None
    assert validated.late_window_minutes == 60


def test_once_schedule_cannot_be_activated_after_preview_time_passes(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = now()
    due = (stamp + timedelta(minutes=2)).replace(second=0, microsecond=0)
    config = _config()
    config.update(
        timezone="UTC",
        rule={
            "kind": "once",
            "date": due.date().isoformat(),
            "time": due.strftime("%H:%M"),
        },
    )
    monkeypatch.setattr(service, "now", lambda: stamp)
    draft = service.create_draft(db, "usr_owner", config, "clock-crossing")
    draft, _, _ = service.preview_draft(db, draft, expected_revision=1)
    token = draft.preview_token
    monkeypatch.setattr(service, "now", lambda: due + timedelta(seconds=1))
    with pytest.raises(service.ScheduleServiceError) as rejected:
        service.confirm_draft(
            db,
            draft,
            expected_revision=1,
            preview_token=token,
            request_id="confirm-clock-crossing",
            enabled=True,
        )
    assert rejected.value.code == "schedule_time_passed"
    assert db.query(Schedule).count() == 0
    with pytest.raises(service.ScheduleServiceError) as preview_rejected:
        service.preview_draft(db, draft, expected_revision=1)
    assert preview_rejected.value.code == "schedule_time_passed"


def test_confirm_is_durable_and_idempotent_after_response_loss(db: Session) -> None:
    draft = service.create_draft(db, "usr_owner", _config(), "create-1")
    draft, occurrences, blockers = service.preview_draft(db, draft, expected_revision=1)
    assert blockers == []
    assert len(occurrences) == 3
    preview_token = draft.preview_token
    assert preview_token

    schedule = service.confirm_draft(
        db,
        draft,
        expected_revision=1,
        preview_token=preview_token,
        request_id="confirm-1",
        enabled=True,
    )
    db.commit()

    persisted_draft = db.get(ScheduleDraft, draft.id)
    assert persisted_draft is not None
    replay = service.confirm_draft(
        db,
        persisted_draft,
        expected_revision=1,
        preview_token=preview_token,
        request_id="confirm-1",
        enabled=True,
    )
    assert replay.id == schedule.id
    assert db.query(Schedule).count() == 1
    assert repository.owned_schedule(db, schedule.id, "usr_other") is None


def test_stale_revision_cannot_modify_schedule_or_overwrite_run_snapshot(
    db: Session,
) -> None:
    timestamp = now()
    canonical = ScheduleConfig.model_validate(_config()).model_dump(mode="json")
    schedule = Schedule(
        id="sch_revision",
        owner_user_id="usr_owner",
        name=canonical["name"],
        kind="reminder",
        status="enabled",
        revision=2,
        config_json=json.dumps(canonical),
        next_run_at=timestamp + timedelta(hours=1),
        created_at=timestamp,
        updated_at=timestamp,
    )
    snapshot = dict(canonical)
    snapshot["instruction"] = "旧执行快照"
    run = ScheduleRun(
        id="srn_snapshot",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="manual",
        occurrence_key="manual:old",
        scheduled_for=timestamp,
        config_json=json.dumps(snapshot),
        schedule_revision=1,
        status="running",
        status_revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add_all([schedule, run])
    db.flush()

    with pytest.raises(service.ScheduleServiceError) as captured:
        service.update_schedule(
            db,
            schedule,
            expected_revision=1,
            config=_config("新名称"),
            request_id="patch-stale",
        )
    assert captured.value.code == "revision_conflict"
    assert json.loads(run.config_json)["instruction"] == "旧执行快照"


def test_management_preview_rejects_a_changed_target_revision(db: Session) -> None:
    draft = service.create_draft(db, "usr_owner", _config(), "target-original")
    draft, _, _ = service.preview_draft(db, draft, expected_revision=1)
    schedule = service.confirm_draft(
        db,
        draft,
        expected_revision=1,
        preview_token=draft.preview_token,
        request_id="target-original-confirm",
        enabled=True,
    )
    change = service.create_draft(
        db,
        "usr_owner",
        _config("聊天修改"),
        "target-change",
        operation="update",
        target_schedule_id=schedule.id,
        target_revision=1,
    )
    service.update_schedule(
        db,
        schedule,
        expected_revision=1,
        config=_config("页面已修改"),
        request_id="target-concurrent-update",
    )

    with pytest.raises(service.ScheduleServiceError) as rejected:
        service.preview_draft(db, change, expected_revision=change.revision)
    assert rejected.value.code == "revision_conflict"


@pytest.mark.parametrize(
    "operation, expected_status", [("pause", "paused"), ("archive", "archived")]
)
def test_stopping_owned_task_does_not_require_execution_dependencies(
    db: Session, monkeypatch: pytest.MonkeyPatch, operation: str, expected_status: str
) -> None:
    stamp = now()
    config = _config("已失效的执行能力")
    config.update(
        kind="skill",
        timezone="UTC",
        skill_id="unavailable-skill",
        skill_version="1.0.0",
        skill_digest="a" * 64,
        model="unavailable-model",
        rule={
            "kind": "once",
            "date": (stamp - timedelta(days=1)).date().isoformat(),
            "time": "09:00",
        },
    )
    canonical = ScheduleConfig.model_validate(config).model_dump(mode="json")
    schedule = Schedule(
        id="sch_unavailable_control",
        owner_user_id="usr_owner",
        name=canonical["name"],
        kind="skill",
        status="blocked",
        revision=1,
        config_json=json.dumps(canonical),
        next_run_at=None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(schedule)
    db.flush()

    def unavailable(_config: dict) -> dict:
        raise service.ScheduleAccessError("skill_unavailable", "能力已停用")

    monkeypatch.setattr(service, "resolve_schedule_skill", unavailable)
    owner = db.get(User, "usr_owner")
    assert owner is not None
    owner.status = "disabled"
    db.flush()
    with pytest.raises(service.ScheduleAccessError) as inactive:
        service.create_draft(
            db,
            owner.id,
            canonical,
            "disabled-control",
            operation=operation,
            target_schedule_id=schedule.id,
            target_revision=1,
        )
    assert inactive.value.code == "owner_unavailable"
    owner.status = "active"
    db.flush()

    draft = service.create_draft(
        db,
        owner.id,
        canonical,
        "stop-unavailable",
        operation=operation,
        target_schedule_id=schedule.id,
        target_revision=1,
    )
    draft, occurrences, blockers = service.preview_draft(
        db, draft, expected_revision=draft.revision
    )
    assert occurrences == [] and blockers == []
    result = service.confirm_draft(
        db,
        draft,
        expected_revision=draft.revision,
        preview_token=draft.preview_token,
        request_id="stop-unavailable-confirm",
        enabled=True,
    )
    assert result.status == expected_status
    assert db.query(ScheduleRun).count() == 0


def test_cancel_draft_is_versioned_idempotent_and_cannot_cancel_confirmed(
    db: Session,
) -> None:
    draft = service.create_draft(db, "usr_owner", _config(), "cancel-create")
    cancelled = service.cancel_draft(
        db, draft, expected_revision=1, request_id="cancel-request"
    )
    assert cancelled.status == "cancelled"
    assert cancelled.revision == 2
    replay = service.cancel_draft(
        db, cancelled, expected_revision=1, request_id="cancel-request"
    )
    assert replay.id == cancelled.id

    ready = service.create_draft(db, "usr_owner", _config("确认后"), "confirmed-create")
    ready, _, _ = service.preview_draft(db, ready, expected_revision=1)
    schedule = service.confirm_draft(
        db,
        ready,
        expected_revision=1,
        preview_token=ready.preview_token,
        request_id="confirmed-request",
        enabled=True,
    )
    with pytest.raises(service.ScheduleServiceError) as captured:
        service.cancel_draft(
            db, ready, expected_revision=1, request_id="cancel-confirmed"
        )
    assert captured.value.code == "state_conflict"
    assert db.get(Schedule, schedule.id) is not None


def test_notification_sequence_is_idempotent_safe_and_does_not_touch_run_time(
    db: Session,
) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(_config()).model_dump(mode="json")
    schedule = Schedule(
        id="sch_notify",
        owner_user_id="usr_owner",
        name="不可进入锁屏的敏感标题",
        kind="reminder",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    first = ScheduleRun(
        id="srn_notify_1",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="scheduled",
        occurrence_key="2026-09-24T01:30:00Z",
        scheduled_for=timestamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status="completed",
        status_revision=2,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )
    second = ScheduleRun(
        id="srn_notify_2",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="scheduled",
        occurrence_key="2026-09-25T01:30:00Z",
        scheduled_for=timestamp + timedelta(days=1),
        config_json=json.dumps(config),
        schedule_revision=1,
        status="completed",
        status_revision=2,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )
    db.add_all([schedule, first, second])
    db.flush()
    original_updated_at = first.updated_at

    event = repository.emit_notification(db, first)
    replay = repository.emit_notification(db, first)
    following = repository.emit_notification(db, second)
    db.flush()
    assert event is not None
    assert following is not None

    assert event is replay
    assert event.title == "提醒已触发"
    assert "敏感标题" not in event.title
    assert (event.sequence, following.sequence) == (1, 2)
    assert first.updated_at == original_updated_at
    assert db.query(ScheduleNotification).count() == 2


def test_completed_skill_with_success_notification_disabled_persists_silent_event(
    db: Session,
) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(
        {
            **_config("静默日报"),
            "kind": "skill",
            "skill_id": "approved-skill",
            "model": "approved-model",
            "notification": {
                "sound": False,
                "desktop": False,
                "on_success": False,
                "on_failure": True,
            },
        }
    ).model_dump(mode="json")
    schedule = Schedule(
        id="sch_silent",
        owner_user_id="usr_owner",
        name="静默日报",
        kind="skill",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    run = ScheduleRun(
        id="srn_silent",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="manual",
        occurrence_key="manual:silent",
        scheduled_for=timestamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status="completed",
        status_revision=2,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )
    db.add_all([schedule, run])
    db.flush()

    event = repository.emit_notification(db, run)
    assert event is not None
    assert event.announce is False
    assert event.sound is False
    assert event.desktop is False
    db.commit()
    persisted = db.get(ScheduleRun, run.id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert db.query(ScheduleNotification).count() == 1


def test_external_delivery_is_once_per_terminal_event_and_not_recreated_when_handled(
    db: Session,
) -> None:
    stamp = now()
    db.add_all(
        [
            NotificationConnection(
                id="nco_email",
                name="邮件",
                kind="smtp",
                config_json="{}",
                credential_ciphertext="encrypted",
                credential_key_version="v1",
                credential_fingerprint="fingerprint",
                status="active",
                revision=1,
                created_at=stamp,
                updated_at=stamp,
            ),
            NotificationPreference(
                user_id="usr_owner", external_enabled=True, updated_at=stamp
            ),
        ]
    )
    db.flush()
    db.add(
        NotificationTarget(
            id="ntg_email",
            connection_id="nco_email",
            kind="email",
            owner_user_id="usr_owner",
            label="我的邮箱",
            address_ciphertext="encrypted",
            address_mask="m***@example.com",
            address_key_version="v1",
            verified_at=stamp,
            consent_at=stamp,
            status="active",
            revision=1,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    config = ScheduleConfig.model_validate(
        {
            **_config(),
            "notification": {
                "on_success": False,
                "external": [
                    {
                        "target_id": "ntg_email",
                        "target_revision": 1,
                        "events": ["completed"],
                        "content_mode": "status_only",
                    }
                ],
            },
        }
    ).model_dump(mode="json")
    schedule = Schedule(
        id="sch_external",
        owner_user_id="usr_owner",
        name="机密标题",
        kind="reminder",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        created_at=stamp,
        updated_at=stamp,
    )
    run = ScheduleRun(
        id="srn_external",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="scheduled",
        occurrence_key="external:once",
        scheduled_for=stamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status="completed",
        status_revision=2,
        created_at=stamp,
        updated_at=stamp,
        completed_at=stamp,
    )
    db.add_all([schedule, run])
    db.flush()
    repository.emit_notification(db, run)
    repository.emit_notification(db, run)
    deliveries = db.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].status == "pending"
    assert "机密标题" not in deliveries[0].content_text
    service.handle_run(db, run, request_id="handle-external")
    repository.emit_notification(db, run)
    assert db.query(NotificationDelivery).count() == 1
    with pytest.raises(service.ScheduleServiceError) as bypass:
        service.update_schedule(
            db,
            schedule,
            expected_revision=schedule.revision,
            config=_config(),
            request_id="bypass-external-preview",
        )
    assert bypass.value.code == "notification_preview_required"
    draft = service.create_draft(
        db, "usr_owner", config, "external-preview-revoked"
    )
    previewed, _, _ = service.preview_draft(
        db, draft, expected_revision=draft.revision
    )
    assert previewed.preview_token
    target = db.get(NotificationTarget, "ntg_email")
    assert target is not None
    target.status = "disabled"
    target.revision += 1
    db.flush()
    with pytest.raises(service.ScheduleServiceError) as revoked:
        service.confirm_draft(
            db,
            previewed,
            expected_revision=previewed.revision,
            preview_token=previewed.preview_token,
            request_id="confirm-revoked",
            enabled=True,
        )
    assert revoked.value.code == "notification_target_unavailable"


def test_retry_refuses_unknown_or_non_retryable_outcome(db: Session) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(_config()).model_dump(mode="json")
    schedule = Schedule(
        id="sch_retry",
        owner_user_id="usr_owner",
        name="重试边界",
        kind="reminder",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    run = ScheduleRun(
        id="srn_retry",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="manual",
        occurrence_key="manual:retry",
        scheduled_for=timestamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status="outcome_unknown",
        status_revision=3,
        retryable=True,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add_all([schedule, run])
    db.flush()

    with pytest.raises(service.ScheduleServiceError) as captured:
        service.retry_run(db, run, request_id="unsafe-retry")
    assert captured.value.code == "retry_unsafe"
    assert run.status == "outcome_unknown"


def test_cancelled_and_confirmed_drafts_cannot_be_previewed_again(db: Session) -> None:
    cancelled = service.create_draft(db, "usr_owner", _config(), "preview-cancelled")
    cancelled = service.cancel_draft(
        db, cancelled, expected_revision=1, request_id="preview-cancel-request"
    )
    with pytest.raises(service.ScheduleServiceError) as cancelled_error:
        service.preview_draft(db, cancelled, expected_revision=cancelled.revision)
    assert cancelled_error.value.code == "state_conflict"
    assert db.get(ScheduleDraft, cancelled.id).status == "cancelled"

    confirmed = service.create_draft(
        db, "usr_owner", _config("不可复活的确认草案"), "preview-confirmed"
    )
    confirmed, _, _ = service.preview_draft(db, confirmed, expected_revision=1)
    service.confirm_draft(
        db,
        confirmed,
        expected_revision=1,
        preview_token=confirmed.preview_token,
        request_id="preview-confirm-request",
        enabled=True,
    )
    with pytest.raises(service.ScheduleServiceError) as confirmed_error:
        service.preview_draft(db, confirmed, expected_revision=confirmed.revision)
    assert confirmed_error.value.code == "state_conflict"
    assert db.get(ScheduleDraft, confirmed.id).status == "confirmed"


def test_stale_session_cannot_enqueue_run_from_replaced_schedule_revision(
    db: Session,
) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(_config("旧配置")).model_dump(mode="json")
    schedule = Schedule(
        id="sch_stale_run_now",
        owner_user_id="usr_owner",
        name=config["name"],
        kind="reminder",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=timestamp + timedelta(hours=1),
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(schedule)
    db.commit()

    stale_db = Session(db.get_bind(), expire_on_commit=False)
    try:
        stale_schedule = stale_db.get(Schedule, schedule.id)
        assert stale_schedule is not None
        stale_db.commit()
        current = db.get(Schedule, schedule.id, populate_existing=True)
        assert current is not None
        service.update_schedule(
            db,
            current,
            expected_revision=1,
            config=_config("新配置"),
            request_id="replace-before-run",
        )
        db.commit()

        with pytest.raises(service.ScheduleServiceError) as captured:
            service.run_now(
                stale_db,
                stale_schedule,
                expected_revision=1,
                request_id="stale-manual-run",
            )
        assert captured.value.code == "revision_conflict"
        assert stale_db.query(ScheduleRun).count() == 0
    finally:
        stale_db.close()


def test_dependency_refresh_cannot_overwrite_concurrent_draft_patch(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_config = {
        **_config("等待能力"),
        "kind": "skill",
        "instruction": "汇总日报",
    }
    draft = service.create_draft(
        db,
        "usr_owner",
        skill_config,
        "refresh-stale",
        skill_request_id="skr_refresh",
    )
    assert draft.status == "waiting_skill"
    db.commit()

    stale_db = Session(db.get_bind(), expire_on_commit=False)
    try:
        stale_draft = stale_db.get(ScheduleDraft, draft.id)
        assert stale_draft is not None
        stale_db.commit()
        current = db.get(ScheduleDraft, draft.id, populate_existing=True)
        assert current is not None
        service.update_draft(
            db,
            current,
            expected_revision=1,
            config=_config("用户刚刚修改"),
            request_id="patch-during-refresh",
        )
        db.commit()

        monkeypatch.setattr(service, "resolve_schedule_skill", lambda config: config)
        monkeypatch.setattr(service, "_resolve_model", lambda _db, config: config)
        refreshed = service.refresh_draft_dependency(stale_db, stale_draft)
        assert refreshed.revision == 2
        assert refreshed.status == "draft"
        assert json.loads(refreshed.config_json)["name"] == "用户刚刚修改"
    finally:
        stale_db.close()


def test_preview_cas_cannot_restore_config_from_a_stale_session(db: Session) -> None:
    draft = service.create_draft(
        db, "usr_owner", _config("预览旧配置"), "preview-stale"
    )
    db.commit()

    stale_db = Session(db.get_bind(), expire_on_commit=False)
    try:
        stale_draft = stale_db.get(ScheduleDraft, draft.id)
        assert stale_draft is not None
        stale_db.commit()
        current = db.get(ScheduleDraft, draft.id, populate_existing=True)
        assert current is not None
        service.update_draft(
            db,
            current,
            expected_revision=1,
            config=_config("预览新配置"),
            request_id="patch-before-preview",
        )
        db.commit()

        with pytest.raises(service.ScheduleServiceError) as captured:
            service.preview_draft(stale_db, stale_draft, expected_revision=1)
        assert captured.value.code == "revision_conflict"
        persisted = stale_db.get(ScheduleDraft, draft.id, populate_existing=True)
        assert persisted is not None
        assert persisted.revision == 2
        assert json.loads(persisted.config_json)["name"] == "预览新配置"
    finally:
        stale_db.close()


@pytest.mark.parametrize("blocked_status", ["awaiting_user", "outcome_unknown"])
def test_owner_can_end_blocked_run_then_resume_and_run_again(
    db: Session, blocked_status: str
) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(_config("阻断恢复")).model_dump(mode="json")
    schedule = Schedule(
        id=f"sch_resolve_{blocked_status}",
        owner_user_id="usr_owner",
        name=config["name"],
        kind="reminder",
        status="blocked",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        blocked_reason=blocked_status,
        created_at=timestamp,
        updated_at=timestamp,
    )
    run = ScheduleRun(
        id=f"srn_resolve_{blocked_status}",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="manual",
        occurrence_key=f"manual:{blocked_status}",
        scheduled_for=timestamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status=blocked_status,
        status_revision=3,
        body="已经保存的部分正文",
        result_files_json='[{"name":"partial.txt","href":"/files/partial"}]',
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add_all([schedule, run])
    db.flush()

    ended = service.cancel_run(db, run, request_id=f"end-{blocked_status}")
    assert ended.status == "cancelled"
    assert ended.body == "已经保存的部分正文"
    assert json.loads(ended.result_files_json)[0]["name"] == "partial.txt"
    resumed = service.transition_schedule(
        db,
        schedule,
        "resume",
        expected_revision=1,
        request_id=f"resume-{blocked_status}",
    )
    replacement = service.run_now(
        db,
        resumed,
        expected_revision=resumed.revision,
        request_id=f"run-after-{blocked_status}",
    )
    assert replacement.status == "queued"
    assert replacement.id != ended.id


def test_snooze_replay_is_single_child_and_parent_leaves_task_center(
    db: Session,
) -> None:
    timestamp = now()
    config = ScheduleConfig.model_validate(_config("稍后提醒")).model_dump(mode="json")
    schedule = Schedule(
        id="sch_snooze_once",
        owner_user_id="usr_owner",
        name=config["name"],
        kind="reminder",
        status="enabled",
        revision=1,
        config_json=json.dumps(config),
        next_run_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    parent = ScheduleRun(
        id="srn_snooze_once",
        schedule_id=schedule.id,
        owner_user_id="usr_owner",
        trigger_kind="scheduled",
        occurrence_key="2026-09-24T09:30:00Z",
        scheduled_for=timestamp,
        config_json=json.dumps(config),
        schedule_revision=1,
        status="completed",
        status_revision=2,
        completed_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add_all([schedule, parent])
    db.flush()
    task_key = f"schedule:{parent.id}"
    task_repository.acknowledge_tasks(
        db, owner_user_id="usr_owner", task_ids=[task_key]
    )
    read_reminder = task_repository.task_by_key(
        db, owner_user_id="usr_owner", task_key=task_key
    )
    assert read_reminder is not None and not read_reminder.unread
    assert parent.handled_at is None
    assert task_repository.is_attention_task(read_reminder)
    db.commit()
    stale_db = Session(db.get_bind(), expire_on_commit=False)
    try:
        stale_parent = stale_db.get(ScheduleRun, parent.id)
        assert stale_parent is not None
        stale_db.commit()
        parent = db.get(ScheduleRun, parent.id, populate_existing=True)
        assert parent is not None
        until = timestamp + timedelta(hours=1)

        child = service.snooze_run(db, parent, request_id="snooze-once", until=until)
        replay = service.snooze_run(db, parent, request_id="snooze-once", until=until)
        assert replay.id == child.id
        # 计划没有下一次周期时间时，“下次执行”就是待触发的稍后提醒。
        summary = service.schedule_response(db, schedule, "usr_owner")
        assert summary["next_run_at"] == iso(until)
        assert summary["pending_snooze_until"] == iso(until)
        # 周期时间更早也只会在到点被判为重叠跳过，因此仍显示这次稍后提醒。
        schedule.next_run_at = until - timedelta(hours=1)
        db.flush()
        assert service.schedule_response(db, schedule, "usr_owner")["next_run_at"] == iso(
            until
        )
        schedule.next_run_at = until + timedelta(hours=2)
        db.flush()
        assert service.schedule_response(db, schedule, "usr_owner")["next_run_at"] == iso(
            until
        )
        schedule.next_run_at = None
        db.flush()
        with pytest.raises(service.ScheduleServiceError) as captured:
            service.snooze_run(
                stale_db,
                stale_parent,
                request_id="snooze-again",
                until=until + timedelta(hours=1),
            )
        assert captured.value.code == "state_conflict"
        with pytest.raises(service.ScheduleServiceError) as handled_conflict:
            service.handle_run(stale_db, stale_parent, request_id="late-handle")
        assert handled_conflict.value.code == "state_conflict"
        assert (
            db.query(ScheduleRun).filter(ScheduleRun.parent_run_id == parent.id).count()
            == 1
        )
        assert parent.handled_at is not None
        assert (
            task_repository.task_by_key(
                db, owner_user_id="usr_owner", task_key=f"schedule:{parent.id}"
            )
            is None
        )
        child.status = "completed"
        child.completed_at = timestamp
        child.status_revision += 1
        db.flush()
        service.handle_run(db, child, request_id="handle-child")
        event = repository.emit_notification(db, child)
        assert event is not None and not event.announce
        handled_reminder = task_repository.task_by_key(
            db, owner_user_id="usr_owner", task_key=f"schedule:{child.id}"
        )
        assert handled_reminder is not None and not handled_reminder.unread
        assert not task_repository.is_attention_task(handled_reminder)
        # 稍后提醒触发并处理后，列表不再声明有待触发的执行。
        summary = service.schedule_response(db, schedule, "usr_owner")
        assert summary["pending_snooze_until"] is None
        assert summary["next_run_at"] is None
    finally:
        stale_db.close()
