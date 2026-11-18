"""语音助手的「派活」能力：把通知和待办落到具体人身上。

边界：
- 只写 AI 自己的东西（待办、群消息），不碰 MES 单据；
- 姓名/岗位解析不出来或有歧义时**绝不猜**，把候选返回让模型去问；
- 非管理员只能给「自己和同群的人」派活，防止随便给不认识的人派单。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroupMessage
from app.services import group_chat, moss_voice


@pytest.fixture
def voice_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "voice-write.sqlite3"
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


def _user(db: Session, username: str, display: str, *, role: str = "user") -> User:
    user = User(
        id=f"usr_{username}",
        username=username,
        email=f"{username}@example.com",
        display_name=display,
        password_hash="unused",
        role=role,
        status="active",
        first_login=False,
        created_at=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def people(voice_db: Session):
    admin = _user(voice_db, "admin", "管理员", role="admin")
    sales = _user(voice_db, "sales", "王销售")
    keeper = _user(voice_db, "keeper", "张仓管")
    outsider = _user(voice_db, "qc2", "孙质检")
    _user(voice_db, "planner", "李计划")
    voice_db.commit()
    return {"admin": admin, "sales": sales, "keeper": keeper, "outsider": outsider}


@pytest.fixture
def write_on(monkeypatch):
    monkeypatch.setattr(settings, "moss_voice_enabled", True, raising=False)
    monkeypatch.setattr(settings, "moss_voice_write_enabled", True, raising=False)
    monkeypatch.setattr(settings, "moss_voice_write_limit", 5, raising=False)


def _todos(db: Session) -> list[GroupTodo]:
    db.expire_all()
    return list(db.scalars(select(GroupTodo)))


# ---------------------------------------------------------------- 工具表


def test_write_tools_declared_as_effects():
    tools = {tool["name"]: tool for tool in moss_voice.tool_definitions()}
    for name in ("assign_todo", "send_notice", "nudge_todo"):
        assert name in tools, f"缺少写工具 {name}"
        assert tools[name]["execution_mode"] == "async_effect"
        assert tools[name]["read_only"] is False
    # 读工具必须保持只读
    for name, tool in tools.items():
        if name not in ("assign_todo", "send_notice", "nudge_todo"):
            assert tool["read_only"] is True


def test_write_tools_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "moss_voice_write_enabled", False, raising=False)
    names = [tool["name"] for tool in moss_voice.tool_definitions()]
    assert "assign_todo" not in names
    assert "send_notice" not in names


# ---------------------------------------------------------------- 解析人


def test_resolve_exact_display_name(voice_db, people):
    matches = moss_voice.resolve_people(voice_db, people["admin"], "王销售")
    assert [user.username for user in matches] == ["sales"]


def test_resolve_by_job_word(voice_db, people):
    """「让仓管补一下」这种说法要能对上张仓管。"""
    matches = moss_voice.resolve_people(voice_db, people["admin"], "仓管")
    assert [user.username for user in matches] == ["keeper"]


def test_resolve_empty_means_self(voice_db, people):
    matches = moss_voice.resolve_people(voice_db, people["sales"], "")
    assert [user.username for user in matches] == ["sales"]


def test_resolve_unknown_returns_nothing(voice_db, people):
    assert moss_voice.resolve_people(voice_db, people["admin"], "李四") == []


def test_resolve_ambiguous_returns_all(voice_db, people):
    _user(voice_db, "sales2", "李销售")
    voice_db.commit()
    matches = moss_voice.resolve_people(voice_db, people["admin"], "销售")
    assert {user.username for user in matches} == {"sales", "sales2"}


def test_non_admin_only_sees_co_members(voice_db, people):
    """普通员工不能给不认识的人派活。"""
    owner = people["sales"]
    group = group_chat.create_group(voice_db, owner=owner, name="销售一部", description="")
    group_chat.add_member(
        voice_db, actor=owner, group_id=group.id, user_id=people["keeper"].id, role="member"
    )
    voice_db.commit()

    assert moss_voice.resolve_people(voice_db, owner, "张仓管")
    # 孙质检不在同一个群，普通员工解析不到
    assert moss_voice.resolve_people(voice_db, owner, "孙质检") == []
    # 管理员可以看到所有人
    assert moss_voice.resolve_people(voice_db, people["admin"], "孙质检")


# ---------------------------------------------------------------- 派待办


def test_assign_todo_creates_for_target(voice_db, people, write_on):
    outcome = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "补充包装木箱库存", "priority": "high"}),
        people["admin"].id,
    )
    assert outcome["meta"]["ok"] is True
    assert outcome["meta"]["person"] == "张仓管"
    todos = _todos(voice_db)
    assert len(todos) == 1
    assert todos[0].assignee_user_id == people["keeper"].id
    assert todos[0].title == "补充包装木箱库存"
    assert todos[0].priority == "high"
    assert todos[0].created_by_user_id == people["admin"].id
    assert "语音" in (todos[0].detail or "")


def test_assign_todo_ambiguous_does_not_guess(voice_db, people, write_on):
    _user(voice_db, "sales2", "李销售")
    voice_db.commit()
    outcome = moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "销售", "title": "对接中天"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert "王销售" in outcome["text"] and "李销售" in outcome["text"]
    assert _todos(voice_db) == []


def test_assign_todo_unknown_person_lists_names(voice_db, people, write_on):
    outcome = moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "李四", "title": "随便"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert "王销售" in outcome["text"]
    assert _todos(voice_db) == []


def test_assign_todo_rejects_empty_title(voice_db, people, write_on):
    outcome = moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "张仓管", "title": "  "}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert _todos(voice_db) == []


def test_assign_todo_dedupes_open_todo(voice_db, people, write_on):
    payload = json.dumps({"person": "张仓管", "title": "补充包装木箱库存"})
    first = moss_voice.execute_tool("assign_todo", payload, people["admin"].id)
    second = moss_voice.execute_tool("assign_todo", payload, people["admin"].id)
    assert first["meta"]["ok"] is True
    assert second["meta"]["ok"] is True
    assert second["meta"]["duplicated"] is True
    assert len(_todos(voice_db)) == 1


def test_assign_todo_respects_visibility(voice_db, people, write_on):
    """普通员工给群外的人派活要被拒，而不是悄悄建一条。"""
    outcome = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "孙质检", "title": "来查一下"}),
        people["sales"].id,
    )
    assert outcome["meta"]["ok"] is False
    assert _todos(voice_db) == []


def test_assign_todo_refused_when_disabled(voice_db, people, monkeypatch):
    monkeypatch.setattr(settings, "moss_voice_write_enabled", False, raising=False)
    outcome = moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "张仓管", "title": "x"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert _todos(voice_db) == []


# ---------------------------------------------------------------- 发通知


def test_send_notice_posts_into_shared_group(voice_db, people, write_on):
    owner = people["admin"]
    group = group_chat.create_group(voice_db, owner=owner, name="销售一部", description="")
    group_chat.add_member(
        voice_db, actor=owner, group_id=group.id, user_id=people["sales"].id, role="member"
    )
    voice_db.commit()

    outcome = moss_voice.execute_tool(
        "send_notice",
        json.dumps({"person": "王销售", "message": "中天那边明天要来看厂，准备一下"}),
        people["admin"].id,
    )
    assert outcome["meta"]["ok"] is True
    assert outcome["meta"]["groupId"] == group.id

    voice_db.expire_all()
    messages = list(voice_db.scalars(select(ProjectGroupMessage)))
    assert len(messages) == 1
    assert "@sales" in messages[0].content
    assert "明天要来看厂" in messages[0].content
    assert messages[0].sender_kind == "ai"


def test_send_notice_without_shared_group_fails_loudly(voice_db, people, write_on):
    """没有共同群聊就不发——宁可让模型说明，也不要假装通知到了。"""
    outcome = moss_voice.execute_tool(
        "send_notice",
        json.dumps({"person": "孙质检", "message": "来一下"}),
        people["admin"].id,
    )
    assert outcome["meta"]["ok"] is False
    assert "群" in outcome["text"]
    voice_db.expire_all()
    assert list(voice_db.scalars(select(ProjectGroupMessage))) == []


def test_send_notice_rejects_empty_message(voice_db, people, write_on):
    outcome = moss_voice.execute_tool(
        "send_notice", json.dumps({"person": "王销售", "message": " "}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False

# ---------------------------------------------------------------- 我的待办


def test_list_my_todos_renders_existing_rows(voice_db, people, write_on):
    """回归：待办渲染取错了键名，会让整个工具抛 KeyError 并被当成超时。"""
    moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "补充包装木箱库存", "priority": "high"}),
        people["admin"].id,
    )
    outcome = moss_voice.execute_tool("list_my_todos", "{}", people["keeper"].id)
    assert outcome["meta"]["ok"] is True
    assert outcome["meta"]["count"] == 1
    assert "补充包装木箱库存" in outcome["text"]
    assert "high" in outcome["text"]


def test_list_my_todos_empty(voice_db, people):
    outcome = moss_voice.execute_tool("list_my_todos", "{}", people["sales"].id)
    assert outcome["meta"]["ok"] is True
    assert "没有" in outcome["text"]


# ---------------------------------------------------------------- 待办状态回流


def test_query_todo_status_for_self(voice_db, people, write_on):
    moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "张仓管", "title": "补充包装木箱库存"}),
        people["admin"].id,
    )
    outcome = moss_voice.execute_tool("query_todo_status", "{}", people["keeper"].id)
    assert outcome["meta"]["ok"] is True
    assert "补充包装木箱库存" in outcome["text"]
    assert outcome["meta"]["count"] == 1


def test_query_todo_status_unknown_person(voice_db, people):
    outcome = moss_voice.execute_tool(
        "query_todo_status", json.dumps({"person": "李四"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert "王销售" in outcome["text"]


def test_nudge_requires_a_single_target(voice_db, people, write_on):
    """名下有多条时先问哪一条，不要挨个催。"""
    for title in ("补充包装木箱库存", "清点纸箱库存"):
        moss_voice.execute_tool(
            "assign_todo", json.dumps({"person": "张仓管", "title": title}), people["admin"].id
        )
    outcome = moss_voice.execute_tool(
        "nudge_todo", json.dumps({"person": "张仓管"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is False
    assert outcome["meta"]["kind"] == "ambiguous_todo"
    voice_db.expire_all()
    assert list(voice_db.scalars(select(ProjectGroupMessage))) == []


def test_nudge_posts_and_counts(voice_db, people, write_on):
    moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "张仓管", "title": "补充包装木箱库存"}),
        people["admin"].id,
    )
    outcome = moss_voice.execute_tool(
        "nudge_todo", json.dumps({"person": "张仓管"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is True
    assert outcome["meta"]["kind"] == "nudge"
    assert outcome["meta"]["person"] == "张仓管"

    voice_db.expire_all()
    messages = list(voice_db.scalars(select(ProjectGroupMessage)))
    assert any("@keeper" in row.content for row in messages)
    todo = voice_db.scalars(select(GroupTodo)).first()
    assert todo.nudge_count == 1


def test_nudge_respects_cooldown(voice_db, people, write_on):
    moss_voice.execute_tool(
        "assign_todo", json.dumps({"person": "张仓管", "title": "补充包装木箱库存"}),
        people["admin"].id,
    )
    first = moss_voice.execute_tool(
        "nudge_todo", json.dumps({"person": "张仓管"}), people["admin"].id
    )
    second = moss_voice.execute_tool(
        "nudge_todo", json.dumps({"person": "张仓管"}), people["admin"].id
    )
    assert first["meta"]["ok"] is True
    assert second["meta"]["ok"] is False
    assert "刚催过" in second["text"]


def test_nudge_nothing_to_do(voice_db, people, write_on):
    outcome = moss_voice.execute_tool(
        "nudge_todo", json.dumps({"person": "张仓管"}), people["admin"].id
    )
    assert outcome["meta"]["ok"] is True
    assert "没有" in outcome["text"]


# ---------------------------------------------------------------- 同主题合并


def test_assign_merges_details_of_the_same_event(voice_db, people, write_on):
    """现场问题：一件事的不同细节被拆成了 6 条待办。"""
    first = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "核对明天中天客户的接待准备"}),
        people["admin"].id,
    )
    second = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "确认中天客户的接送车辆"}),
        people["admin"].id,
    )
    third = moss_voice.execute_tool(
        "assign_todo",
        json.dumps({"person": "张仓管", "title": "安排中天客户午餐与伴手礼"}),
        people["admin"].id,
    )
    assert first["meta"]["merged"] is False
    assert second["meta"]["merged"] is True
    assert third["meta"]["merged"] is True
    assert second["meta"]["todoId"] == first["meta"]["todoId"]

    todos = _todos(voice_db)
    assert len(todos) == 1
    assert "接送车辆" in todos[0].detail
    assert "午餐" in todos[0].detail


def test_assign_keeps_different_business_separate(voice_db, people, write_on):
    """同一客户、不同性质的事（接待 vs 发货 vs 回款）不能并成一条。"""
    for title in (
        "核对中天客户的接待准备",
        "确认中天精工订单明天发货",
        "中天精工本月回款对账",
    ):
        moss_voice.execute_tool(
            "assign_todo", json.dumps({"person": "张仓管", "title": title}), people["admin"].id
        )
    assert len(_todos(voice_db)) == 3
