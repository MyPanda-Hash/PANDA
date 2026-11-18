"""待办归类：同一件事的不同细节要并成一条。

现实里踩到的：对「中天客户来访」一件事，语音里分几回说了接送、样品、午餐、
参观路线、接待准备，结果冒出 6 条待办，看着像 6 件事。
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
from app.models.group_chat import GroupTodo
from app.services import todo_topics

TZ = timezone.utc
KNOWN = ("中天精工", "华东热处理厂", "王销售", "张仓管")


@pytest.fixture
def topic_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "todo-topics.sqlite3"
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
def crew(topic_db: Session):
    people = []
    for username, display in (("sales", "王销售"), ("keeper", "张仓管")):
        user = User(
            id=f"usr_{username}", username=username, email=f"{username}@e.com",
            display_name=display, password_hash="x", role="user", status="active",
            first_login=False, created_at=datetime(2026, 5, 1, tzinfo=TZ),
        )
        topic_db.add(user)
        people.append(user)
    topic_db.commit()
    return {"sales": people[0], "keeper": people[1]}


RECEPTION_TITLES = (
    "确认中天客户的接送车辆",
    "准备中天客户的样品清单",
    "安排中天客户午餐与伴手礼",
    "把中天客户的样品提前摆到会议室",
    "确认中天客户来访的产线参观路线",
    "核对明天中天客户的接待准备",
)


# ---------------------------------------------------------------- 主题键


def test_same_event_different_details_share_topic():
    """这一组就是现场那 6 条，必须归成一件事。"""
    keys = {todo_topics.topic_of(title, "", known=KNOWN).key for title in RECEPTION_TITLES}
    assert len(keys) == 1, f"应归为一类，实际：{keys}"
    assert "" not in keys


def test_different_intent_on_same_customer_is_separate():
    reception = todo_topics.topic_of("安排中天客户来访接待", "", known=KNOWN).key
    delivery = todo_topics.topic_of("确认并安排中天精工订单明天发货", "", known=KNOWN).key
    finance = todo_topics.topic_of("中天精工本月回款对账", "", known=KNOWN).key
    assert reception != delivery != finance
    assert reception.endswith("事务接待")
    assert delivery.endswith("业务交付")
    assert finance.endswith("财务")


def test_unknown_target_and_category_does_not_merge():
    """说不清是什么事，就别合并——宁可分开，也不能把无关的事捆一起。"""
    assert todo_topics.topic_of("整理一下资料", "", known=KNOWN).key == ""


def test_doc_number_becomes_topic():
    key = todo_topics.topic_of("核查 SO-20261112-003 的审核人", "", known=KNOWN).key
    assert key.startswith("SO-20261112-003#")
    other = todo_topics.topic_of("核查 SO-20261112-004 的审核人", "", known=KNOWN).key
    assert key != other


def test_short_name_is_expanded_to_known_customer():
    """「中天客户」要归到「中天精工」，否则同一个客户会被拆成两类。"""
    assert todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN).entity == "中天精工"


# ---------------------------------------------------------------- 合并落库


def _age(db: Session, todo: GroupTodo, days: float) -> None:
    moment = datetime.now(TZ) - timedelta(days=days)
    todo.created_at = moment
    todo.updated_at = moment
    db.commit()


def test_merge_or_create_collapses_same_topic(topic_db, crew):
    first, merged_first = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0],
        topic=todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN),
    )
    assert merged_first is False
    topic_db.commit()

    second, merged_second = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[1],
        topic=todo_topics.topic_of(RECEPTION_TITLES[1], "", known=KNOWN),
    )
    topic_db.commit()

    assert merged_second is True
    assert second.id == first.id
    assert len(list(topic_db.scalars(select(GroupTodo)))) == 1
    assert RECEPTION_TITLES[1] in second.detail


def test_merge_appends_each_detail_once(topic_db, crew):
    topic = todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN)
    todo, _ = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0], topic=topic
    )
    topic_db.commit()
    for title in RECEPTION_TITLES[1:]:
        todo_topics.merge_or_create(
            topic_db, assignee_id=crew["sales"].id, title=title, topic=topic
        )
        topic_db.commit()
    rows = list(topic_db.scalars(select(GroupTodo)))
    assert len(rows) == 1
    for title in RECEPTION_TITLES[1:]:
        assert rows[0].detail.count(title) == 1


def test_merge_respects_assignee(topic_db, crew):
    topic = todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN)
    todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0], topic=topic
    )
    todo_topics.merge_or_create(
        topic_db, assignee_id=crew["keeper"].id, title=RECEPTION_TITLES[1], topic=topic
    )
    topic_db.commit()
    assert len(list(topic_db.scalars(select(GroupTodo)))) == 2


def test_merge_respects_window(topic_db, crew):
    topic = todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN)
    old, _ = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0], topic=topic
    )
    topic_db.commit()
    _age(topic_db, old, 30)
    _, merged = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[1], topic=topic
    )
    topic_db.commit()
    assert merged is False
    assert len(list(topic_db.scalars(select(GroupTodo)))) == 2


def test_merge_keeps_highest_priority(topic_db, crew):
    topic = todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN)
    todo, _ = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0],
        priority="low", topic=topic,
    )
    topic_db.commit()
    todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[1],
        priority="urgent", topic=topic,
    )
    topic_db.commit()
    assert todo.priority == "urgent"


def test_closed_todo_is_not_merged_into(topic_db, crew):
    topic = todo_topics.topic_of(RECEPTION_TITLES[0], "", known=KNOWN)
    done, _ = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[0], topic=topic
    )
    done.status = "done"
    topic_db.commit()
    _, merged = todo_topics.merge_or_create(
        topic_db, assignee_id=crew["sales"].id, title=RECEPTION_TITLES[1], topic=topic
    )
    topic_db.commit()
    assert merged is False


# ---------------------------------------------------------------- 存量整理


def _legacy_rows(db: Session, assignee_id: str, count: int) -> None:
    """模拟改造前的存量：没有 topic、彼此独立的三条待办。"""
    stamp = datetime.now(TZ)
    for index, title in enumerate(RECEPTION_TITLES[:count]):
        db.add(
            GroupTodo(
                id=f"gtodo_legacy{index}",
                group_id=None,
                assignee_user_id=assignee_id,
                origin="ai",
                title=title,
                detail="",
                priority="normal",
                status="pending",
                topic="",
                created_at=stamp,
                updated_at=stamp,
            )
        )
    db.commit()


def test_merge_existing_dry_run_does_not_touch(topic_db, crew):
    _legacy_rows(topic_db, crew["sales"].id, 3)
    report = todo_topics.merge_existing(topic_db, dry_run=True)
    assert report
    assert len(list(topic_db.scalars(select(GroupTodo)))) == 3


def test_merge_existing_collapses_and_keeps_text(topic_db, crew):
    _legacy_rows(topic_db, crew["sales"].id, 3)
    todo_topics.merge_existing(topic_db, dry_run=False)
    rows = list(
        topic_db.scalars(select(GroupTodo).where(GroupTodo.status != "dismissed"))
    )
    assert len(rows) == 1
    for title in RECEPTION_TITLES[1:3]:
        assert title in rows[0].detail