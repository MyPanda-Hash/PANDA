"""站内通知（气泡）：派活、催办、升级都要能弹到收件人面前。

重点是「只提醒一次」和「发不出去要说」——重复弹比不弹更烦人。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroupMessage
from app.services import inapp_notifications, group_chat, moss_voice, todo_followup

TZ = timezone.utc


@pytest.fixture
def notify_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "notify.sqlite3"
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
def crew(notify_db: Session):
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
    notify_db.add_all([admin, keeper])
    # 语音工具走自己的事务，账号必须已提交它才看得见
    notify_db.commit()
    yield {"admin": admin, "keeper": keeper}
    notify_db.rollback()


@pytest.fixture(autouse=True)
def write_on(monkeypatch):
    monkeypatch.setattr(settings, "moss_voice_write_enabled", True, raising=False)
    monkeypatch.setattr(settings, "moss_voice_write_limit", 5, raising=False)


# ---------------------------------------------------------------- 基础读写


def test_push_and_list(notify_db, crew):
    row = inapp_notifications.push(
        notify_db,
        user_id=crew["keeper"].id,
        kind="todo_assigned",
        title="补充包装木箱库存",
        body="来自管理员",
        ref_type="todo",
        ref_id="gtodo_x",
        actor_name="管理员",
    )
    assert row is not None
    items = inapp_notifications.pending(notify_db, crew["keeper"].id)
    assert [item.id for item in items] == [row.id]
    assert inapp_notifications.unread_count(notify_db, crew["keeper"].id) == 1


def test_dedupe_keeps_single_unread(notify_db, crew):
    for _ in range(3):
        inapp_notifications.push(
            notify_db,
            user_id=crew["keeper"].id,
            kind="todo_assigned",
            title="补充包装木箱库存",
            dedupe_key="todo_assigned:gtodo_x",
        )
    assert len(inapp_notifications.pending(notify_db, crew["keeper"].id)) == 1


def test_read_then_can_notify_again(notify_db, crew):
    inapp_notifications.push(
        notify_db, user_id=crew["keeper"].id, kind="todo_assigned",
        title="补充包装木箱库存", dedupe_key="todo_assigned:gtodo_x",
    )
    inapp_notifications.mark_read(notify_db, crew["keeper"].id)
    assert inapp_notifications.unread_count(notify_db, crew["keeper"].id) == 0
    again = inapp_notifications.push(
        notify_db, user_id=crew["keeper"].id, kind="todo_assigned",
        title="补充包装木箱库存", dedupe_key="todo_assigned:gtodo_x",
    )
    assert again is not None


def test_notifications_are_per_user(notify_db, crew):
    inapp_notifications.push(
        notify_db, user_id=crew["keeper"].id, kind="notice", title="给你"
    )
    assert inapp_notifications.pending(notify_db, crew["admin"].id) == []


def test_unknown_kind_is_rejected(notify_db, crew):
    assert inapp_notifications.push(
        notify_db, user_id=crew["keeper"].id, kind="whatever", title="x"
    ) is None


# ---------------------------------------------------------------- 派活联动


def test_voice_assign_pushes_bubble(notify_db, crew):
    outcome = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "补充包装木箱库存"}),
        crew["admin"].id,
    )
    assert outcome["meta"]["ok"] is True
    notify_db.commit()
    items = inapp_notifications.pending(notify_db, crew["keeper"].id)
    assert len(items) == 1
    assert items[0].kind == "todo_assigned"
    assert items[0].title == "补充包装木箱库存"
    assert items[0].actor_name == "管理员"
    assert items[0].ref_id == outcome["meta"]["todoId"]


def test_assign_echo_does_not_double_pop(notify_db, crew):
    payload = json.dumps({"person": "张仓管", "title": "补充包装木箱库存"})
    moss_voice.execute_tool("assign_todo", payload, crew["admin"].id)
    moss_voice.execute_tool("assign_todo", payload, crew["admin"].id)
    notify_db.commit()
    assert len(inapp_notifications.pending(notify_db, crew["keeper"].id)) == 1


def test_voice_notice_pushes_bubble(notify_db, crew):
    group = group_chat.create_group(
        notify_db, owner=crew["admin"], name="销售一部", description=""
    )
    group_chat.add_member(
        notify_db, actor=crew["admin"], group_id=group.id,
        user_id=crew["keeper"].id, role="member",
    )
    notify_db.commit()

    outcome = moss_voice.execute_tool(
        "send_notice",
        json.dumps({"person": "张仓管", "message": "明天中天来看厂"}),
        crew["admin"].id,
    )
    assert outcome["meta"]["ok"] is True, outcome["text"]
    notify_db.commit()
    items = inapp_notifications.pending(notify_db, crew["keeper"].id)
    assert len(items) == 1
    assert items[0].kind == "notice"
    assert "明天中天来看厂" in items[0].body


# ---------------------------------------------------------------- 催办联动


def _todo(db: Session, keeper: User, *, age_hours: float = 30) -> GroupTodo:
    moment = datetime.now(TZ)
    created = moment - timedelta(hours=age_hours)
    todo = GroupTodo(
        id="gtodo_follow1",
        group_id=None,
        assignee_user_id=keeper.id,
        origin="ai",
        title="补充包装木箱库存",
        detail="",
        priority="normal",
        status="pending",
        nudge_count=0,
        created_at=created,
        updated_at=created,
    )
    db.add(todo)
    db.flush()
    return todo


def _feed(db: Session, admin: User):
    group = group_chat.create_group(
        db, owner=admin, name=settings.mes_watch_group_name, description=""
    )
    db.commit()
    return group


def test_nudge_pushes_bubble(notify_db, crew):
    _feed(notify_db, crew["admin"])
    todo = _todo(notify_db, crew["keeper"])
    result = todo_followup.nudge(notify_db, todo=todo, by_label="管理员")
    assert result["ok"] is True
    items = inapp_notifications.pending(notify_db, crew["keeper"].id)
    assert len(items) == 1
    assert items[0].kind == "todo_nudged"
    assert "催办" in items[0].title


def test_each_nudge_pops_once(notify_db, crew, monkeypatch):
    """第二次催办是新的提醒，要能再弹一次。"""
    monkeypatch.setattr(settings, "todo_nudge_cooldown_hours", 0.0, raising=False)
    _feed(notify_db, crew["admin"])
    todo = _todo(notify_db, crew["keeper"])
    todo_followup.nudge(notify_db, todo=todo, by_label="AI")
    todo_followup.nudge(notify_db, todo=todo, by_label="AI")
    items = inapp_notifications.pending(notify_db, crew["keeper"].id)
    assert len(items) == 2


def test_escalation_pushes_bubble(notify_db, crew):
    _feed(notify_db, crew["admin"])
    todo = _todo(notify_db, crew["keeper"], age_hours=80)
    todo.created_by_user_id = crew["admin"].id
    todo.nudge_count = 3
    notify_db.commit()
    todo_followup.escalate(notify_db, todo=todo)
    items = inapp_notifications.pending(notify_db, crew["admin"].id)
    assert len(items) == 1
    assert items[0].kind == "todo_escalated"