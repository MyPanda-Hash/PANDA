from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import ProjectGroupAttachment
from app.services import group_chat


@pytest.fixture
def group_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "group-chat.sqlite3"
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


def _user(db: Session, user_id: str, display_name: str, *, role: str = "user") -> User:
    stamp = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    user = User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        display_name=display_name,
        password_hash="unused",
        role=role,
        status="active",
        first_login=False,
        created_at=stamp,
    )
    db.add(user)
    db.flush()
    return user


def _setup(db: Session):
    owner = _user(db, "u_owner", "张组长")
    manager = _user(db, "u_manager", "李管理")
    member = _user(db, "u_member", "王组员")
    outsider = _user(db, "u_outsider", "赵路人")
    admin = _user(db, "u_admin", "系统管理员", role="admin")
    group = group_chat.create_group(
        db, owner=owner, name="A 型机架项目", description="机架试产"
    )
    group_chat.add_member(db, actor=owner, group_id=group.id, user_id=manager.id, role="manager")
    group_chat.add_member(db, actor=owner, group_id=group.id, user_id=member.id)
    db.commit()
    return group, owner, manager, member, outsider, admin


def test_create_group_makes_creator_owner_member(group_db: Session):
    group, owner, *_ = _setup(group_db)
    membership = group_chat.find_member(group_db, group.id, owner.id)
    assert membership is not None
    assert membership.role == "owner"
    assert group.status == "active"


def test_outsider_cannot_read_group_even_as_system_admin(group_db: Session):
    group, _, _, _, outsider, admin = _setup(group_db)
    with pytest.raises(group_chat.NotGroupMember):
        group_chat.require_member(group_db, group.id, outsider)
    with pytest.raises(group_chat.NotGroupMember):
        group_chat.require_member(group_db, group.id, admin)


def test_member_cannot_invite_but_manager_can(group_db: Session):
    group, owner, manager, member, outsider, _ = _setup(group_db)
    with pytest.raises(group_chat.GroupPermissionDenied):
        group_chat.add_member(
            group_db, actor=member, group_id=group.id, user_id=outsider.id
        )
    added = group_chat.add_member(
        group_db, actor=manager, group_id=group.id, user_id=outsider.id
    )
    assert added.role == "member"
    with pytest.raises(group_chat.InvalidGroupInput):
        group_chat.add_member(
            group_db, actor=manager, group_id=group.id, user_id=outsider.id
        )


def test_only_owner_can_appoint_manager(group_db: Session):
    group, owner, manager, member, outsider, _ = _setup(group_db)
    with pytest.raises(group_chat.GroupPermissionDenied):
        group_chat.add_member(
            group_db,
            actor=manager,
            group_id=group.id,
            user_id=outsider.id,
            role="manager",
        )
    assert (
        group_chat.add_member(
            group_db,
            actor=owner,
            group_id=group.id,
            user_id=outsider.id,
            role="manager",
        ).role
        == "manager"
    )


def test_owner_cannot_be_removed_and_member_can_leave(group_db: Session):
    group, owner, manager, member, _, _ = _setup(group_db)
    with pytest.raises(group_chat.GroupPermissionDenied):
        group_chat.remove_member(
            group_db, actor=owner, group_id=group.id, user_id=owner.id
        )
    group_chat.remove_member(group_db, actor=member, group_id=group.id, user_id=member.id)
    assert group_chat.find_member(group_db, group.id, member.id) is None


def test_archived_group_blocks_posting_but_keeps_history(group_db: Session):
    group, owner, _, member, _, _ = _setup(group_db)
    group_chat.post_message(
        group_db, group=group, sender=member, kind="text", content="第一版图纸已上传"
    )
    group_chat.archive_group(group_db, actor=owner, group_id=group.id)
    with pytest.raises(group_chat.InvalidGroupInput):
        group_chat.post_message(
            group_db, group=group, sender=member, kind="text", content="还能发吗"
        )
    assert len(group_chat.list_messages(group_db, group_id=group.id)) == 1


def test_message_needs_text_or_attachment(group_db: Session):
    group, _, _, member, _, _ = _setup(group_db)
    with pytest.raises(group_chat.InvalidGroupInput):
        group_chat.post_message(
            group_db, group=group, sender=member, kind="text", content="   "
        )


def test_document_message_links_attachment_to_message(group_db: Session):
    group, _, _, member, _, _ = _setup(group_db)
    attachment = ProjectGroupAttachment(
        id="gatt_1",
        group_id=group.id,
        kind="file",
        filename="工艺卡.xlsx",
        mime_type="application/vnd.ms-excel",
        size_bytes=2048,
        storage_path="/tmp/工艺卡.xlsx",
        extract_status="pending",
        created_at=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
    )
    group_db.add(attachment)
    group_db.flush()
    message = group_chat.post_message(
        group_db,
        group=group,
        sender=member,
        kind="file",
        content="请按这份工艺卡排产",
        attachments=[attachment],
    )
    assert attachment.message_id == message.id
    assert message.attachments_json == '["gatt_1"]'


def test_list_messages_after_cursor_only_returns_newer(group_db: Session):
    group, _, _, member, _, _ = _setup(group_db)
    base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    first = group_chat.post_message(
        group_db, group=group, sender=member, kind="text", content="一"
    )
    second = group_chat.post_message(
        group_db, group=group, sender=member, kind="text", content="二"
    )
    third = group_chat.post_message(
        group_db, group=group, sender=member, kind="text", content="三"
    )
    for offset, message in enumerate((first, second, third)):
        message.created_at = base + timedelta(minutes=offset)
    group_db.flush()

    newer = group_chat.list_messages(
        group_db, group_id=group.id, after_message_id=first.id
    )
    assert [item.content for item in newer] == ["二", "三"]

    older = group_chat.list_messages(
        group_db, group_id=group.id, before_message_id=third.id, limit=2
    )
    assert [item.content for item in older] == ["一", "二"]


def test_unread_count_skips_own_messages_and_honours_read_marker(group_db: Session):
    group, owner, _, member, _, _ = _setup(group_db)
    base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    messages = []
    for offset, (sender, text) in enumerate(
        ((owner, "周会改到周四"), (member, "收到"), (owner, "顺延一天"))
    ):
        message = group_chat.post_message(
            group_db, group=group, sender=sender, kind="text", content=text
        )
        message.created_at = base + timedelta(minutes=offset)
        messages.append(message)
    group_db.flush()

    membership = group_chat.find_member(group_db, group.id, member.id)
    assert group_chat.unread_count(group_db, group_id=group.id, member=membership) == 2

    group_chat.mark_read(group_db, member=membership, message_id=messages[2].id)
    assert group_chat.unread_count(group_db, group_id=group.id, member=membership) == 0


def test_list_groups_hides_archived_and_outsider_groups(group_db: Session):
    group, owner, _, member, outsider, _ = _setup(group_db)
    assert group_chat.list_groups(group_db, owner) == [group]
    assert group_chat.list_groups(group_db, outsider) == []
    group_chat.archive_group(group_db, actor=owner, group_id=group.id)
    assert group_chat.list_groups(group_db, owner) == []
    assert group_chat.list_groups(group_db, owner, include_archived=True) == [group]
