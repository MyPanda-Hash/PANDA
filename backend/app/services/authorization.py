from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.models.access import KnowledgeSpace, Permission, Role, RolePermission, SpaceMember, UserRole
from app.models.auth import User
from app.repositories.audit import record

SPACE_LEVEL = {"reader": 1, "contributor": 2, "manager": 3}


class PermissionService:
    """Clean, unified permission checking with audit logging."""

    @staticmethod
    def list_permission_codes(db: Session, user: User) -> list[str]:
        """Return effective system permission codes for UI gating (admin gets all)."""
        if user.role == "admin":
            return sorted(db.scalars(select(Permission.code)).all())
        rows = db.scalars(
            select(Permission.code)
            .join(RolePermission)
            .join(Role)
            .join(UserRole)
            .where(UserRole.user_id == user.id)
        ).all()
        return sorted(set(rows))

    @staticmethod
    def has_system_permission(db: Session, user: User, permission_code: str) -> bool:
        """Check if a user has a system-level permission (admin bypass)."""
        if user.role == "admin":
            return True
        query = (
            select(Permission.id)
            .join(RolePermission)
            .join(Role)
            .join(UserRole)
            .where(UserRole.user_id == user.id, Permission.code == permission_code)
        )
        return db.scalar(query) is not None

    @staticmethod
    def require_system_permission(db: Session, user: User, permission_code: str) -> None:
        """Require a system-level permission or raise 403. Audits on denial."""
        if not PermissionService.has_system_permission(db, user, permission_code):
            record(db, actor=user.id, event_type="permission_denied", target=permission_code, result="forbidden")
            raise HTTPException(status_code=403, detail="没有执行该操作的权限")

    @staticmethod
    def require_space_access(
        db: Session,
        user: User,
        space_id: str,
        minimum_role: str = "reader",
        *,
        allow_archived: bool = False,
    ) -> KnowledgeSpace:
        """Require the user to be a space member with at least `minimum_role`. Raises 404 on failure."""
        space = db.get(KnowledgeSpace, space_id)
        if not space or space.status == "deleted":
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        if space.status == "archived" and not allow_archived:
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        if user.role == "admin" or space.owner_user_id == user.id:
            return space
        member = PermissionService._space_member(db, user, space_id)
        if not member or SPACE_LEVEL.get(member.role, 0) < SPACE_LEVEL[minimum_role]:
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        return space

    @staticmethod
    def require_space_access_batch(
        db: Session,
        user: User,
        space_ids: list[str],
        minimum_role: str = "reader",
    ) -> list[str]:
        """Validate every space and return the canonical IDs that passed ACL."""
        return [
            PermissionService.require_space_access(
                db, user, space_id, minimum_role=minimum_role
            ).id
            for space_id in space_ids
        ]

    @staticmethod
    def has_space_member_role(
        db: Session, user: User, space_id: str, minimum_role: str = "reader"
    ) -> bool:
        """Check actual space membership; system-admin status never bypasses this.

        Space owners always count as managers, even if the membership row is missing.
        """
        space = db.get(KnowledgeSpace, space_id)
        if space and space.owner_user_id == user.id:
            return SPACE_LEVEL["manager"] >= SPACE_LEVEL[minimum_role]
        member = PermissionService._space_member(db, user, space_id)
        return bool(
            member
            and SPACE_LEVEL.get(member.role, 0) >= SPACE_LEVEL[minimum_role]
        )

    @staticmethod
    def require_space_member_access(
        db: Session,
        user: User,
        space_id: str,
        minimum_role: str = "reader",
        *,
        allow_archived: bool = False,
    ) -> KnowledgeSpace:
        """Require an actual membership role, including for platform administrators."""
        space = db.get(KnowledgeSpace, space_id)
        if not space or space.status == "deleted":
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        if space.status == "archived" and not allow_archived:
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        if not PermissionService.has_space_member_role(
            db, user, space_id, minimum_role
        ):
            record(db, actor=user.id, event_type="space_access_denied", target=space_id, result="not_found")
            raise HTTPException(status_code=404, detail="资源不存在")
        return space

    @staticmethod
    def check_space_owner(db: Session, space_id: str, user_id: str) -> bool:
        """Check if a user is the owner of a space."""
        space = db.get(KnowledgeSpace, space_id)
        return space is not None and space.owner_user_id == user_id

    @staticmethod
    def require_not_space_owner(db: Session, space_id: str, target_user_id: str, space: KnowledgeSpace | None = None) -> None:
        """Raise 409 if the target user is the space owner."""
        if space is None:
            space = db.get(KnowledgeSpace, space_id)
        if space and space.owner_user_id == target_user_id:
            raise HTTPException(status_code=409, detail="空间负责人不能移除，请先转移负责人")

    @staticmethod
    def require_owner_role_change_allowed(db: Session, space_id: str, target_user_id: str, new_role: str, space: KnowledgeSpace | None = None) -> None:
        """Raise 409 if the target user is the space owner and the new role is not 'manager'."""
        if space is None:
            space = db.get(KnowledgeSpace, space_id)
        if space and space.owner_user_id == target_user_id and new_role != "manager":
            raise HTTPException(status_code=409, detail="空间负责人必须保留 manager 角色")

    @staticmethod
    def _space_member(db: Session, user: User, space_id: str) -> SpaceMember | None:
        return db.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user.id
            )
        )


# --- Backward-compatible standalone wrappers ---

def has_system_permission(db: Session, user: User, permission_code: str) -> bool:
    return PermissionService.has_system_permission(db, user, permission_code)


def list_permission_codes(db: Session, user: User) -> list[str]:
    return PermissionService.list_permission_codes(db, user)


def space_member(db: Session, user: User, space_id: str) -> SpaceMember | None:
    return PermissionService._space_member(db, user, space_id)


def authorize(db: Session, user: User, action: str, *, space_id: str | None = None, minimum_role: str = "reader") -> KnowledgeSpace | None:
    if space_id is None:
        PermissionService.require_system_permission(db, user, action)
        return None
    return PermissionService.require_space_access(db, user, space_id, minimum_role=minimum_role)


def can_view_space(db: Session, user: User, space_id: str) -> KnowledgeSpace:
    return PermissionService.require_space_access(db, user, space_id, minimum_role="reader")
