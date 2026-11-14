from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.access import Permission, Role, RolePermission

SYSTEM_PERMISSIONS = [
    ("user:manage", "管理用户", "创建、邀请和停用系统用户"),
    ("system:overview:view", "查看系统概览", "查看系统用户、空间、文档和会话的聚合统计"),
    ("role:manage", "管理角色", "管理系统角色和权限绑定"),
    ("space:create", "创建空间", "创建知识空间"),
    ("space:manage_all", "管理全部空间", "管理不属于自己的空间"),
    ("usage:view_all", "查看全局用量", "查看授权范围外的聚合用量元数据"),
]
BUILTIN_ROLES = [
    ("system_admin", "系统管理员", "管理账号、角色、空间和系统配置"),
    ("employee", "员工", "使用聊天、写作和已获授权的资料，不附加系统管理权限"),
    ("auditor", "审计员", "查看审计事件和授权元数据"),
]
# 历史默认描述：只有当内置角色仍是这些默认值时才随新文案更新，
# 管理员自定义过的名称/描述不会被启动时的种子逻辑覆盖。
LEGACY_BUILTIN_ROLE_DESCRIPTIONS = {
    "auditor": {"查看治理事件和授权元数据"},
}


def seed_system_access(db: Session) -> None:
    permissions = {}
    for code, name, description in SYSTEM_PERMISSIONS:
        permission = db.scalar(select(Permission).where(Permission.code == code))
        if not permission:
            permission = Permission(id=f"perm_{new_token(10)}", code=code, name=name, description=description, created_at=now())
            db.add(permission)
        permissions[code] = permission
    db.flush()
    for code, name, description in BUILTIN_ROLES:
        role = db.scalar(select(Role).where(Role.code == code))
        if not role:
            role = Role(id=f"role_{new_token(10)}", code=code, name=name, description=description, scope="system", builtin=True, created_at=now())
            db.add(role)
            db.flush()
        if role.description in LEGACY_BUILTIN_ROLE_DESCRIPTIONS.get(code, set()):
            role.description = description
        if code == "system_admin":
            existing = {binding.permission_id for binding in role.permissions}
            for permission in permissions.values():
                if permission.id not in existing:
                    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()
