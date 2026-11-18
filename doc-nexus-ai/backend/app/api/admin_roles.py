from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import admin_user
from app.core.config import settings
from app.core.database import transaction
from app.core.security import new_token, now
from app.models.access import Permission, Role, RolePermission, UserRole
from app.models.auth import User
from app.repositories import audit, users
from app.schemas.access import (
    PermissionResponse,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
    UserRolesUpdateRequest,
)
from app.services import linkmind

router = APIRouter(prefix="/admin", tags=["admin-access"])


def _load_role(db, role_id: str) -> Role | None:
    return db.scalar(
        select(Role)
        .where(Role.id == role_id)
        .options(selectinload(Role.permissions).selectinload(RolePermission.permission))
    )


def role_response(role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        scope=role.scope,
        builtin=role.builtin,
        permissions=[binding.permission.code for binding in role.permissions],
    )


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(_: User = Depends(admin_user)) -> list[PermissionResponse]:
    with transaction() as db:
        return [
            PermissionResponse(
                code=item.code, name=item.name, description=item.description
            )
            for item in db.scalars(select(Permission).order_by(Permission.code))
        ]


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(_: User = Depends(admin_user)) -> list[RoleResponse]:
    with transaction() as db:
        items = db.scalars(
            select(Role)
            .order_by(Role.code)
            .options(
                selectinload(Role.permissions).selectinload(RolePermission.permission)
            )
        )
        return [role_response(item) for item in items]


@router.post("/roles", response_model=RoleResponse, status_code=201)
def create_role(
    payload: RoleCreateRequest, admin: User = Depends(admin_user)
) -> RoleResponse:
    with transaction() as db:
        if db.scalar(select(Role).where(Role.code == payload.code)):
            raise HTTPException(status_code=409, detail="角色编码已存在")
        role = Role(
            id=f"role_{new_token(10)}",
            code=payload.code,
            name=payload.name,
            description=payload.description,
            scope="system",
            builtin=False,
            created_at=now(),
        )
        db.add(role)
        db.flush()
        permissions = list(
            db.scalars(
                select(Permission).where(Permission.code.in_(payload.permission_codes))
            )
        )
        if len(permissions) != len(set(payload.permission_codes)):
            raise HTTPException(status_code=422, detail="包含不存在的权限编码")
        db.add_all(
            [
                RolePermission(role_id=role.id, permission_id=permission.id)
                for permission in permissions
            ]
        )
        db.flush()
        audit.record(db, admin.id, "role_created", role.id, "success")
        loaded = _load_role(db, role.id)
        assert loaded is not None
        return role_response(loaded)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str, payload: RoleUpdateRequest, admin: User = Depends(admin_user)
) -> RoleResponse:
    with transaction() as db:
        role = _load_role(db, role_id)
        if not role:
            raise HTTPException(status_code=404, detail="角色不存在")
        if role.builtin and payload.permission_codes is not None:
            raise HTTPException(status_code=409, detail="内置角色的权限绑定不可修改")
        if payload.name is not None:
            role.name = payload.name
        if payload.description is not None:
            role.description = payload.description
        if payload.permission_codes is not None:
            permissions = list(
                db.scalars(
                    select(Permission).where(
                        Permission.code.in_(payload.permission_codes)
                    )
                )
            )
            if len(permissions) != len(set(payload.permission_codes)):
                raise HTTPException(status_code=422, detail="包含不存在的权限编码")
            role.permissions.clear()
            role.permissions.extend(
                RolePermission(role_id=role.id, permission_id=permission.id)
                for permission in permissions
            )
        db.flush()
        audit.record(db, admin.id, "role_updated", role.id, "success")
        loaded = _load_role(db, role.id)
        assert loaded is not None
        return role_response(loaded)


@router.delete("/roles/{role_id}", status_code=204, response_model=None)
def delete_role(role_id: str, admin: User = Depends(admin_user)) -> None:
    with transaction() as db:
        role = db.get(Role, role_id)
        if not role:
            raise HTTPException(status_code=404, detail="角色不存在")
        if role.builtin:
            raise HTTPException(status_code=409, detail="内置角色不可删除")
        if db.scalar(select(UserRole).where(UserRole.role_id == role.id)):
            raise HTTPException(status_code=409, detail="角色仍被使用，不能删除")
        audit.record(db, admin.id, "role_deleted", role.id, "success")
        db.delete(role)


@router.get("/users/{user_id}/roles", response_model=list[RoleResponse])
def list_user_roles(user_id: str, _: User = Depends(admin_user)) -> list[RoleResponse]:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        role_ids = list(
            db.scalars(select(UserRole.role_id).where(UserRole.user_id == user_id))
        )
        if not role_ids:
            return []
        items = db.scalars(
            select(Role)
            .where(Role.id.in_(role_ids))
            .order_by(Role.code)
            .options(
                selectinload(Role.permissions).selectinload(RolePermission.permission)
            )
        )
        return [role_response(item) for item in items]


@router.put("/users/{user_id}/roles", response_model=list[RoleResponse])
def set_user_roles(
    user_id: str, payload: UserRolesUpdateRequest, admin: User = Depends(admin_user)
) -> list[RoleResponse]:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.status == "deleted":
            raise HTTPException(status_code=409, detail="已删除用户无法分配角色")
        unique_ids = list(dict.fromkeys(payload.role_ids))
        roles = (
            list(db.scalars(select(Role).where(Role.id.in_(unique_ids))))
            if unique_ids
            else []
        )
        if len(roles) != len(unique_ids):
            raise HTTPException(status_code=422, detail="包含不存在的角色")
        codes = {role.code for role in roles}
        if (
            users.is_bootstrap_admin(db, user, settings.bootstrap_username)
            and "system_admin" not in codes
        ):
            raise HTTPException(
                status_code=409, detail="系统引导管理员必须保留系统管理员角色"
            )
        existing = list(db.scalars(select(UserRole).where(UserRole.user_id == user_id)))
        for binding in existing:
            db.delete(binding)
        db.flush()
        db.add_all([UserRole(user_id=user_id, role_id=role.id) for role in roles])
        if "system_admin" in codes:
            user.role = "admin"
        elif (
            not users.is_bootstrap_admin(db, user, settings.bootstrap_username)
            and user.role == "admin"
        ):
            user.role = "user"
        db.flush()
        try:
            linkmind.sync_user(db, user)
        except linkmind.LinkMindError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        audit.record(db, admin.id, "user_roles_updated", user_id, "success")
        loaded = []
        for role in roles:
            item = _load_role(db, role.id)
            if item:
                loaded.append(role_response(item))
        return loaded
