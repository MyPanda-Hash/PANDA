"""项目群聊的领域逻辑：建组、加人、发消息、已读。

这里只放权限与不变式，不碰 HTTP 层；路由在 ``app/api/group_chat.py``，
AI 分析在 ``app/services/group_assistant.py``。

角色模型（三档，数字越大权限越高）::

    owner   组长，建组人，唯一，可转让、可解散、可管任何人
    manager 管理员，可邀请/移除普通成员
    member  普通成员，可读可发消息
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.auth import User
from app.models.group_chat import (
    GROUP_ROLES,
    MESSAGE_KINDS,
    GroupTodo,
    ProjectGroup,
    ProjectGroupAttachment,
    ProjectGroupMember,
    ProjectGroupMessage,
)

ROLE_WEIGHT = {"member": 1, "manager": 2, "owner": 3}
GROUP_NAME_MAX = 160


class GroupChatError(Exception):
    """群聊领域错误的基类，路由层统一翻译成 4xx。"""


class GroupNotFound(GroupChatError):
    pass


class NotGroupMember(GroupChatError):
    pass


class GroupPermissionDenied(GroupChatError):
    pass


class InvalidGroupInput(GroupChatError):
    pass


def _require_text(value: str | None, field: str, *, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidGroupInput(f"{field}不能为空")
    if len(text) > max_length:
        raise InvalidGroupInput(f"{field}不能超过 {max_length} 个字符")
    return text


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def create_group(
    db: Session,
    *,
    owner: User,
    name: str,
    description: str = "",
    mes_project_code: str | None = None,
    knowledge_space_id: str | None = None,
) -> ProjectGroup:
    """建组，建组人自动成为 owner 成员。"""
    stamp = now()
    group = ProjectGroup(
        id=_new_id("grp"),
        name=_require_text(name, "群名", max_length=GROUP_NAME_MAX),
        description=(description or "").strip(),
        owner_user_id=owner.id,
        status="active",
        mes_project_code=(mes_project_code or "").strip() or None,
        knowledge_space_id=knowledge_space_id,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(group)
    db.flush()
    db.add(
        ProjectGroupMember(
            id=_new_id("gpm"),
            group_id=group.id,
            user_id=owner.id,
            role="owner",
            invited_by_user_id=None,
            joined_at=stamp,
        )
    )
    db.flush()
    return group


def get_group(db: Session, group_id: str) -> ProjectGroup:
    group = db.get(ProjectGroup, group_id)
    if group is None:
        raise GroupNotFound("项目群不存在")
    return group


def find_member(
    db: Session, group_id: str, user_id: str
) -> ProjectGroupMember | None:
    return db.scalar(
        select(ProjectGroupMember).where(
            ProjectGroupMember.group_id == group_id,
            ProjectGroupMember.user_id == user_id,
        )
    )


def require_member(
    db: Session, group_id: str, user: User
) -> tuple[ProjectGroup, ProjectGroupMember]:
    """返回 (群, 成员记录)；非成员直接拒绝，管理员也不绕过。

    群是成员制，不是全局可见：即便 ``user.role == "admin"``，不加入也读不到
    聊天内容，避免后台管理员静默翻阅项目讨论。
    """
    group = get_group(db, group_id)
    if group.status == "archived":
        # 归档群仍可读，只是不能发言，交由调用方判断。
        member = find_member(db, group_id, user.id)
        if member is None:
            raise NotGroupMember("不是该项目群成员")
        return group, member
    member = find_member(db, group_id, user.id)
    if member is None:
        raise NotGroupMember("不是该项目群成员")
    return group, member


def _require_role(
    member: ProjectGroupMember, minimum: str, action: str
) -> None:
    if ROLE_WEIGHT.get(member.role, 0) < ROLE_WEIGHT[minimum]:
        raise GroupPermissionDenied(f"只有{'组长' if minimum == 'owner' else '组长或管理员'}可以{action}")


def require_manager(
    db: Session, group_id: str, user: User
) -> tuple[ProjectGroup, ProjectGroupMember]:
    group, member = require_member(db, group_id, user)
    _require_role(member, "manager", "执行该操作")
    return group, member


def require_owner(
    db: Session, group_id: str, user: User
) -> tuple[ProjectGroup, ProjectGroupMember]:
    group, member = require_member(db, group_id, user)
    _require_role(member, "owner", "执行该操作")
    return group, member


def list_groups(db: Session, user: User, *, include_archived: bool = False) -> list[ProjectGroup]:
    member_group_ids = select(ProjectGroupMember.group_id).where(
        ProjectGroupMember.user_id == user.id
    )
    stmt = select(ProjectGroup).where(ProjectGroup.id.in_(member_group_ids))
    if not include_archived:
        stmt = stmt.where(ProjectGroup.status == "active")
    return list(db.scalars(stmt.order_by(ProjectGroup.updated_at.desc())))


def add_member(
    db: Session,
    *,
    actor: User,
    group_id: str,
    user_id: str,
    role: str = "member",
) -> ProjectGroupMember:
    if role not in GROUP_ROLES or role == "owner":
        raise InvalidGroupInput("只能邀请为管理员或普通成员")
    group, actor_member = require_manager(db, group_id, actor)
    if group.status == "archived":
        raise InvalidGroupInput("群已归档，不能再加人")
    if find_member(db, group_id, user_id) is not None:
        raise InvalidGroupInput("该成员已在群里")
    if db.get(User, user_id) is None:
        raise InvalidGroupInput("被邀请人不存在")
    if role == "manager":
        # 管理员只有组长能任命，避免管理员互相提权。
        if actor_member.role != "owner":
            raise GroupPermissionDenied("只有组长可以任命管理员")
    member = ProjectGroupMember(
        id=_new_id("gpm"),
        group_id=group_id,
        user_id=user_id,
        role=role,
        invited_by_user_id=actor.id,
        joined_at=now(),
    )
    db.add(member)
    group.updated_at = now()
    db.flush()
    return member


def remove_member(
    db: Session, *, actor: User, group_id: str, user_id: str
) -> None:
    group, actor_member = require_member(db, group_id, actor)
    target = find_member(db, group_id, user_id)
    if target is None:
        raise InvalidGroupInput("该成员不在群里")
    if target.role == "owner":
        raise GroupPermissionDenied("不能移除组长，请先转让")
    if actor.id != user_id:
        # 只有组长能移除管理员；管理员只能移除普通成员。
        if actor_member.role == "manager" and target.role == "manager":
            raise GroupPermissionDenied("管理员之间不能互相移除")
        _require_role(actor_member, "manager", "移除成员")
    db.delete(target)
    group.updated_at = now()
    db.flush()


def delete_group(db: Session, *, actor: User, group_id: str) -> None:
    """硬删除：群、成员、消息、附件、AI 记录、待办一起没。

    组长可以解散自己的群；系统管理员也可以（运营要能收拾没人管的群）。
    其他人一律拒绝。
    """
    if (actor.role or "").strip() == "admin":
        group = get_group(db, group_id)
    else:
        group, _ = require_owner(db, group_id, actor)
    db.delete(group)
    db.flush()


def archive_group(db: Session, *, actor: User, group_id: str) -> ProjectGroup:
    """解散（= 归档）：群从列表里消失，但消息、附件、待办全部保留。

    保留是有意的：解散之后 AI 还要从这里提炼项目经验，
    硬删除会把素材一起删掉。真删除仍走 ``delete_group``。
    组长或系统管理员可执行。
    """
    if (actor.role or "").strip() == "admin":
        group = get_group(db, group_id)
    else:
        group, _ = require_owner(db, group_id, actor)
    if group.status == "archived":
        return group
    group.status = "archived"
    group.updated_at = now()
    db.flush()
    return group


def post_message(
    db: Session,
    *,
    group: ProjectGroup,
    sender: User | None,
    kind: str,
    content: str = "",
    sender_kind: str = "user",
    parts: list[dict] | None = None,
    attachments: list[ProjectGroupAttachment] | None = None,
    reply_to_message_id: str | None = None,
) -> ProjectGroupMessage:
    """落一条消息。``sender`` 为 None 时表示 AI 或系统消息。"""
    if kind not in MESSAGE_KINDS:
        raise InvalidGroupInput(f"不支持的消息类型：{kind}")
    if group.status == "archived":
        raise InvalidGroupInput("群已归档，不能发消息")
    text = (content or "").strip()
    files = attachments or []
    if not text and not files:
        raise InvalidGroupInput("消息内容为空")
    if sender_kind == "user" and sender is None:
        raise InvalidGroupInput("缺少发送人")

    stamp = now()
    message = ProjectGroupMessage(
        id=_new_id("gmsg"),
        group_id=group.id,
        sender_user_id=sender.id if sender is not None else None,
        sender_kind=sender_kind,
        kind=kind,
        content=text,
        parts_json=json.dumps(parts, ensure_ascii=False) if parts else None,
        attachments_json=json.dumps(
            [item.id for item in files], ensure_ascii=False
        ),
        reply_to_message_id=reply_to_message_id,
        created_at=stamp,
    )
    db.add(message)
    db.flush()
    for item in files:
        item.message_id = message.id
    group.updated_at = stamp
    db.flush()
    return message


def list_messages(
    db: Session,
    *,
    group_id: str,
    after_message_id: str | None = None,
    before_message_id: str | None = None,
    limit: int = 50,
) -> list[ProjectGroupMessage]:
    """按时间正序返回，``after_message_id`` 用于增量拉取新消息。"""
    limit = max(1, min(limit, 200))
    stmt = select(ProjectGroupMessage).where(
        ProjectGroupMessage.group_id == group_id,
        ProjectGroupMessage.deleted_at.is_(None),
    )
    if after_message_id:
        anchor = db.get(ProjectGroupMessage, after_message_id)
        if anchor is not None:
            stmt = stmt.where(ProjectGroupMessage.created_at > anchor.created_at)
    if before_message_id:
        anchor = db.get(ProjectGroupMessage, before_message_id)
        if anchor is not None:
            stmt = stmt.where(ProjectGroupMessage.created_at < anchor.created_at)
            return list(
                db.scalars(
                    stmt.order_by(ProjectGroupMessage.created_at.desc()).limit(limit)
                )
            )[::-1]
    return list(
        db.scalars(stmt.order_by(ProjectGroupMessage.created_at).limit(limit))
    )


def unread_count(db: Session, *, group_id: str, member: ProjectGroupMember) -> int:
    stmt = select(func.count(ProjectGroupMessage.id)).where(
        ProjectGroupMessage.group_id == group_id,
        ProjectGroupMessage.deleted_at.is_(None),
        ProjectGroupMessage.sender_user_id != member.user_id,
    )
    if member.last_read_message_id:
        anchor = db.get(ProjectGroupMessage, member.last_read_message_id)
        if anchor is not None:
            stmt = stmt.where(ProjectGroupMessage.created_at > anchor.created_at)
    return int(db.scalar(stmt) or 0)


def mark_read(
    db: Session, *, member: ProjectGroupMember, message_id: str
) -> None:
    member.last_read_message_id = message_id
    db.flush()


def group_member_users(db: Session, group_id: str) -> list[tuple[ProjectGroupMember, User]]:
    """群成员及其用户档案，供前端展示与 AI 指派姓名匹配。"""
    rows = db.execute(
        select(ProjectGroupMember, User)
        .join(User, User.id == ProjectGroupMember.user_id)
        .where(ProjectGroupMember.group_id == group_id)
    ).all()
    return [(member, user) for member, user in rows]


def todo_counts(db: Session, *, user_id: str) -> dict[str, int]:
    rows = db.execute(
        select(GroupTodo.status, func.count(GroupTodo.id))
        .where(GroupTodo.assignee_user_id == user_id)
        .group_by(GroupTodo.status)
    ).all()
    return {status: int(count) for status, count in rows}
