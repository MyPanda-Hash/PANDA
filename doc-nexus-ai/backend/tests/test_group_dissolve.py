"""解散群聊：数据保留，素材还能用来提炼经验。

这条边界很容易写错：把「解散」做成硬删除，聊天记录没了，
AI 再想从中提炼项目经验就没有素材了。
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
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMessage
from app.services import group_chat

TZ = timezone.utc


@pytest.fixture
def group_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "dissolve.sqlite3"
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


def _user(db: Session, username: str, *, role: str = "user") -> User:
    user = User(
        id=f"usr_{username}", username=username, email=f"{username}@e.com",
        display_name=username, password_hash="x", role=role, status="active",
        first_login=False, created_at=datetime(2026, 5, 1, tzinfo=TZ),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def scene(group_db: Session):
    owner = _user(group_db, "owner")
    other = _user(group_db, "other")
    admin = _user(group_db, "root", role="admin")
    group = group_chat.create_group(group_db, owner=owner, name="包装改善", description="")
    group_chat.add_member(
        group_db, actor=owner, group_id=group.id, user_id=other.id, role="member"
    )
    group_chat.post_message(
        group_db, group=group, sender=owner, sender_kind="user", kind="text",
        content="木箱要加厚到 15mm",
    )
    group_db.commit()
    return {"owner": owner, "other": other, "admin": admin, "group": group}


def test_dissolve_keeps_messages_and_membership(group_db, scene):
    group = scene["group"]
    group_chat.archive_group(group_db, actor=scene["owner"], group_id=group.id)
    group_db.commit()

    group_db.expire_all()
    kept = group_db.get(ProjectGroup, group.id)
    assert kept is not None, "解散不能把群删掉"
    assert kept.status == "archived"
    messages = list(
        group_db.scalars(
            select(ProjectGroupMessage).where(ProjectGroupMessage.group_id == group.id)
        )
    )
    assert len(messages) == 1, "聊到记录要保留，否则没法提炼经验"


def test_dissolved_group_leaves_active_list(group_db, scene):
    group_chat.archive_group(group_db, actor=scene["owner"], group_id=scene["group"].id)
    group_db.commit()
    active = group_chat.list_groups(group_db, scene["owner"])
    everything = group_chat.list_groups(group_db, scene["owner"], include_archived=True)
    assert active == []
    assert [g.id for g in everything] == [scene["group"].id]


def test_member_cannot_dissolve(group_db, scene):
    with pytest.raises(group_chat.GroupPermissionDenied):
        group_chat.archive_group(group_db, actor=scene["other"], group_id=scene["group"].id)


def test_owner_can_dissolve(group_db, scene):
    group = group_chat.archive_group(
        group_db, actor=scene["owner"], group_id=scene["group"].id
    )
    assert group.status == "archived"


def test_system_admin_can_dissolve(group_db, scene):
    """运营要能收拾没人管的群，所以系统管理员也允许。"""
    group = group_chat.archive_group(
        group_db, actor=scene["admin"], group_id=scene["group"].id
    )
    assert group.status == "archived"


def test_dissolve_is_idempotent(group_db, scene):
    first = group_chat.archive_group(
        group_db, actor=scene["owner"], group_id=scene["group"].id
    )
    second = group_chat.archive_group(
        group_db, actor=scene["owner"], group_id=scene["group"].id
    )
    assert first.id == second.id
    assert second.status == "archived"


def test_todos_survive_dissolve(group_db, scene):
    group = scene["group"]
    group_db.add(
        GroupTodo(
            id="gtodo_keep", group_id=group.id, assignee_user_id=scene["owner"].id,
            origin="ai", title="改木箱规格", detail="", priority="normal",
            status="pending", topic="", refs_json="[]",
            created_at=datetime.now(TZ), updated_at=datetime.now(TZ),
        )
    )
    group_db.commit()
    group_chat.archive_group(group_db, actor=scene["owner"], group_id=group.id)
    group_db.commit()
    kept = group_db.get(GroupTodo, "gtodo_keep")
    assert kept is not None and kept.status == "pending"


def test_hard_delete_still_available_for_real_removal(group_db, scene):
    """解散是保留；真要清干净还留了 delete_group，别把两条路混了。"""
    group_chat.delete_group(group_db, actor=scene["owner"], group_id=scene["group"].id)
    group_db.commit()
    assert group_db.get(ProjectGroup, scene["group"].id) is None