"""待办状态回流：算状态、催办、升级。

关键约束：天数与「卡住」判定全部由代码算，模型只负责念；催办必须落得下去，
发不出去要如实报失败。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMessage
from app.services import group_chat, todo_followup

TZ = timezone.utc


@pytest.fixture
def follow_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "todo-followup.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


@pytest.fixture
def crew(follow_db: Session):
    admin = User(
        id="usr_admin", username="admin", email="admin@example.com", display_name="管理员",
        password_hash="x", role="admin", status="active", first_login=False,
        created_at=datetime(2026, 5, 1, tzinfo=TZ),
    )
    keeper = User(
        id="usr_keeper", username="keeper", email="keeper@example.com", display_name="张仓管",
        password_hash="x", role="user", status="active", first_login=False,
        created_at=datetime(2026, 5, 1, tzinfo=TZ),
    )
    follow_db.add_all([admin, keeper])
    follow_db.flush()
    yield {"admin": admin, "keeper": keeper}
    follow_db.rollback()


@pytest.fixture
def feed(follow_db: Session, crew):
    """待办通知要落到群里，没有群就发不出去。"""
    group = group_chat.create_group(
        follow_db, owner=crew["admin"], name=settings.mes_watch_group_name, description=""
    )
    group_chat.add_member(
        follow_db, actor=crew["admin"], group_id=group.id, user_id=crew["keeper"].id, role="member"
    )
    follow_db.flush()
    return group


def _todo(
    db: Session,
    *,
    keeper: User,
    title: str = "补充包装木箱库存",
    status: str = "pending",
    age_hours: float = 30,
    due_hours: float | None = None,
    nudges: int = 0,
) -> GroupTodo:
    moment = datetime.now(TZ)
    created = moment - timedelta(hours=age_hours)
    todo = GroupTodo(
        id=f"gtodo_{title[:6]}{status}{int(age_hours)}",
        group_id=None,
        assignee_user_id=keeper.id,
        origin="ai",
        title=title,
        detail="",
        priority="normal",
        status=status,
        due_at=(moment + timedelta(hours=due_hours)) if due_hours is not None else None,
        nudge_count=nudges,
        created_at=created,
        updated_at=created,
    )
    db.add(todo)
    db.flush()
    return todo


# ---------------------------------------------------------------- 状态判定


def test_pending_beyond_threshold_is_idle(follow_db, crew):
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    info = todo_followup.classify(todo)
    assert info["state"] == "idle"
    assert info["idleHours"] >= 24


def test_fresh_pending_is_not_idle(follow_db, crew):
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=2)
    assert todo_followup.classify(todo)["state"] == "fresh"


def test_past_due_is_overdue(follow_db, crew):
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=5, due_hours=-3)
    info = todo_followup.classify(todo)
    assert info["state"] == "overdue"
    assert info["overdueHours"] > 0


def test_doing_stuck_after_threshold(follow_db, crew):
    todo = _todo(follow_db, keeper=crew["keeper"], status="doing", age_hours=72)
    assert todo_followup.classify(todo)["state"] == "stuck"


def test_done_todos_are_not_open(follow_db, crew, feed):
    _todo(follow_db, keeper=crew["keeper"], status="done", age_hours=100)
    assert todo_followup.open_todos(follow_db) == []


# ---------------------------------------------------------------- 催办


def test_nudge_posts_message_and_counts(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 12.0, raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    result = todo_followup.nudge(follow_db, todo=todo, by_label="管理员")
    assert result["ok"] is True
    assert todo.nudge_count == 1
    assert todo.nudged_at is not None

    follow_db.expire_all()
    messages = list(follow_db.scalars(select(ProjectGroupMessage)))
    assert len(messages) == 1
    assert "@keeper" in messages[0].content
    assert "补充包装木箱库存" in messages[0].content


def test_nudge_respects_cooldown(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 12.0, raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    assert todo_followup.nudge(follow_db, todo=todo)["ok"] is True
    second = todo_followup.nudge(follow_db, todo=todo)
    assert second["ok"] is False
    assert "刚催过" in second["reason"]
    follow_db.expire_all()
    assert len(list(follow_db.scalars(select(ProjectGroupMessage)))) == 1


def test_nudge_without_group_fails_loudly(follow_db, crew, monkeypatch):
    """没有通知群（群名没配）时不能假装催到了。"""
    monkeypatch.setattr(settings, "mes_watch_group_name", "", raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    result = todo_followup.nudge(follow_db, todo=todo)
    assert result["ok"] is False
    assert todo.nudge_count == 0


# ---------------------------------------------------------------- 升级


def test_sweep_escalates_after_max_nudges(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_followup_enabled", True, raising=False)
    monkeypatch.setattr(settings, "todo_max_nudges", 3, raising=False)
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 0.5, raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=80, nudges=3)
    todo.created_by_user_id = crew["admin"].id
    follow_db.flush()

    summary = todo_followup.sweep(follow_db)
    assert summary["escalated"] == 1
    assert todo.escalated_at is not None
    follow_db.expire_all()
    messages = list(follow_db.scalars(select(ProjectGroupMessage)))
    assert any("卡住了" in row.content for row in messages)


def test_sweep_nudges_idle_todo(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_followup_enabled", True, raising=False)
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 12.0, raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    summary = todo_followup.sweep(follow_db)
    assert summary["nudged"] == 1
    assert todo.nudge_count == 1


def test_sweep_is_idempotent_within_cooldown(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_followup_enabled", True, raising=False)
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 12.0, raising=False)
    _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    assert todo_followup.sweep(follow_db)["nudged"] == 1
    assert todo_followup.sweep(follow_db)["nudged"] == 0


def test_sweep_skips_when_disabled(follow_db, crew, feed, monkeypatch):
    monkeypatch.setattr(settings, "todo_followup_enabled", False, raising=False)
    _todo(follow_db, keeper=crew["keeper"], age_hours=30)
    summary = todo_followup.sweep(follow_db)
    assert summary["nudged"] == 0
    assert summary["checked"] == 0


# ---------------------------------------------------------------- 概览


def test_overview_text_mentions_person_and_state(follow_db, crew):
    _todo(follow_db, keeper=crew["keeper"], title="补充包装木箱库存", age_hours=30)
    overview = todo_followup.status_overview(follow_db, assignee=crew["keeper"])
    assert overview["count"] == 1
    assert "补充包装木箱库存" in overview["text"]
    assert "还没开始" in overview["text"]
    assert "卡住" in overview["text"]


def test_overview_empty(follow_db, crew):
    overview = todo_followup.status_overview(follow_db, assignee=crew["keeper"])
    assert overview["count"] == 0
    assert "没有" in overview["text"]

def test_nudge_does_not_reset_idle_clock(follow_db, crew, feed, monkeypatch):
    """回归：拿 updated_at 当「多久没动」会被自己催一次就清零。

    实测踩过：催完之后状态从「还没开始」变回「刚派下去」。
    """
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 12.0, raising=False)
    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=48)
    assert todo_followup.classify(todo)["state"] == "idle"

    assert todo_followup.nudge(follow_db, todo=todo)["ok"] is True
    after = todo_followup.classify(todo)
    assert after["state"] == "idle", "催办不应把「还没开始」冲掉"
    assert after["idleHours"] >= 24


def test_human_action_resets_idle_clock(follow_db, crew):
    """人在群里点了「开始做」，就该重新计时。"""
    from app.services import group_assistant

    todo = _todo(follow_db, keeper=crew["keeper"], age_hours=48)
    assert todo_followup.classify(todo)["state"] == "idle"
    group_assistant.set_todo_status(
        follow_db, todo=todo, actor=crew["keeper"], status="doing"
    )
    info = todo_followup.classify(todo)
    assert info["state"] == "doing"
    assert info["idleHours"] < 1
