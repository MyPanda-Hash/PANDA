from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.api.deps import current_user
from app.core.database import transaction
from app.core.security import now
from app.models.access import DocumentCategory, KnowledgeSpace, SpaceMember
from app.models.auth import User
from app.models.documents import Document, DocumentVersion
from app.repositories import audit, documents, spaces, users
from app.schemas.spaces import (
    CategoryCreateRequest,
    CategoryResponse,
    DirectoryUserListResponse,
    DirectoryUserResponse,
    MemberBulkAddRequest,
    MemberBulkAddResponse,
    MemberBulkRemoveResponse,
    MemberCreateRequest,
    MemberResponse,
    MemberUpdateRequest,
    SpaceCreateRequest,
    SpaceResponse,
    SpaceUpdateRequest,
    WorkspaceResponse,
)
from app.services.auth import public_user_with_permissions
from app.services.authorization import PermissionService

router = APIRouter(tags=["workspace"])


def member_response(db, member: SpaceMember) -> MemberResponse:
    profile = users.get_by_id(db, member.user_id)
    return MemberResponse(
        user_id=member.user_id,
        role=member.role,
        username=profile.username if profile else None,
        display_name=profile.display_name if profile else None,
        email=profile.email if profile else None,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def space_response(db, space: KnowledgeSpace, user_id: str) -> SpaceResponse:
    member = db.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space.id, SpaceMember.user_id == user_id
        )
    )
    owner = users.get_by_id(db, space.owner_user_id)
    document_count = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.space_id == space.id,
                Document.status == "active",
            )
        )
        or 0
    )
    queryable_count = (
        db.scalar(
            select(func.count(Document.id))
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                Document.space_id == space.id,
                Document.status == "active",
                DocumentVersion.processing_stage == "ready",
                DocumentVersion.queryable.is_(True),
                DocumentVersion.is_current_published.is_(True),
            )
        )
        or 0
    )
    return SpaceResponse(
        id=space.id,
        name=space.name,
        description=space.description,
        status=space.status,
        owner_user_id=space.owner_user_id,
        owner_display_name=owner.display_name if owner else None,
        owner_username=owner.username if owner else None,
        scope_version=space.scope_version,
        member_role=(
            "manager"
            if space.owner_user_id == user_id
            else (member.role if member else None)
        ),
        document_count=document_count,
        queryable_document_count=queryable_count,
        updated_at=space.updated_at,
    )


@router.get("/workspace", response_model=WorkspaceResponse)
def workspace(user: User = Depends(current_user)) -> WorkspaceResponse:
    with transaction() as db:
        items = spaces.list_for_user(db, user.id, user.role == "admin")[:5]
        running_tasks = [
            documents.task_snapshot(
                db, job, user_id=user.id, is_admin=user.role == "admin"
            )
            for job in documents.list_active_jobs_for_user(
                db, user_id=user.id, is_admin=user.role == "admin"
            )
        ]
        return WorkspaceResponse(
            user=public_user_with_permissions(db, user).model_dump(),
            recent_spaces=[space_response(db, item, user.id) for item in items],
            running_tasks=running_tasks,
            usage={"period": "current_month", "cost": "0", "budget_percent": 0},
        )


@router.get("/knowledge-spaces", response_model=list[SpaceResponse])
def list_spaces(user: User = Depends(current_user)) -> list[SpaceResponse]:
    with transaction() as db:
        return [
            space_response(db, item, user.id)
            for item in spaces.list_for_user(db, user.id, user.role == "admin")
        ]


@router.post("/knowledge-spaces", response_model=SpaceResponse, status_code=201)
def create_space(
    payload: SpaceCreateRequest, user: User = Depends(current_user)
) -> SpaceResponse:
    with transaction() as db:
        PermissionService.require_system_permission(db, user, "space:create")
        space = spaces.create_space(db, user.id, payload.name, payload.description)
        audit.record(db, user.id, "space_created", space.id, "success")
        return space_response(db, space, user.id)


@router.patch("/knowledge-spaces/{space_id}", response_model=SpaceResponse)
def update_space(
    space_id: str, payload: SpaceUpdateRequest, user: User = Depends(current_user)
) -> SpaceResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager", allow_archived=True
        )
        if space.status in ("archived", "deleted"):
            raise HTTPException(status_code=409, detail="已归档或已删除的空间不可修改")
        if payload.name is None and payload.description is None:
            raise HTTPException(status_code=422, detail="请至少提供名称或说明")
        spaces.update_space(space, name=payload.name, description=payload.description)
        audit.record(db, user.id, "space_updated", space.id, "success")
        return space_response(db, space, user.id)


@router.delete("/knowledge-spaces/{space_id}", status_code=204, response_model=None)
def delete_space(space_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager", allow_archived=True
        )
        if space.status == "deleted":
            raise HTTPException(status_code=404, detail="知识空间不存在")
        # Only owner or system admin can permanently soft-delete the space.
        if space.owner_user_id != user.id and user.role != "admin":
            raise HTTPException(
                status_code=403, detail="仅空间拥有者或系统管理员可删除知识空间"
            )
        spaces.soft_delete_space(space)
        audit.record(db, user.id, "space_deleted", space.id, "success")


@router.post("/knowledge-spaces/{space_id}:archive", response_model=SpaceResponse)
def archive_space(space_id: str, user: User = Depends(current_user)) -> SpaceResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager", allow_archived=True
        )
        if space.status == "deleted":
            raise HTTPException(status_code=404, detail="知识空间不存在")
        if space.status != "archived":
            space.status = "archived"
            space.scope_version += 1
            space.updated_at = now()
            audit.record(db, user.id, "space_archived", space.id, "success")
        return space_response(db, space, user.id)


@router.post("/knowledge-spaces/{space_id}:restore", response_model=SpaceResponse)
def restore_space(space_id: str, user: User = Depends(current_user)) -> SpaceResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager", allow_archived=True
        )
        if space.status == "deleted":
            raise HTTPException(status_code=404, detail="知识空间不存在")
        if space.status == "archived":
            space.status = "active"
            space.scope_version += 1
            space.updated_at = now()
            audit.record(db, user.id, "space_restored", space.id, "success")
        return space_response(db, space, user.id)


@router.get("/knowledge-spaces/{space_id}", response_model=SpaceResponse)
def get_space(space_id: str, user: User = Depends(current_user)) -> SpaceResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader", allow_archived=True
        )
        return space_response(db, space, user.id)


@router.get(
    "/knowledge-spaces/{space_id}/directory-users",
    response_model=DirectoryUserListResponse,
)
def list_directory_users(
    space_id: str,
    q: str = "",
    limit: int = 200,
    user: User = Depends(current_user),
) -> DirectoryUserListResponse:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        member_ids = {item.user_id for item in spaces.list_members(db, space_id)}
        total_active_users = (
            db.scalar(select(func.count(User.id)).where(User.status == "active")) or 0
        )
        return DirectoryUserListResponse(
            items=[
                DirectoryUserResponse(
                    id=item.id,
                    username=item.username,
                    display_name=item.display_name,
                    email=item.email,
                    already_member=item.id in member_ids,
                )
                for item in users.list_directory(db, q, limit)
            ],
            total_active_users=total_active_users,
        )


@router.get("/knowledge-spaces/{space_id}/members", response_model=list[MemberResponse])
def list_members(
    space_id: str, user: User = Depends(current_user)
) -> list[MemberResponse]:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )
        return [member_response(db, item) for item in spaces.list_members(db, space_id)]


@router.post(
    "/knowledge-spaces/{space_id}/members",
    response_model=MemberResponse,
    status_code=201,
)
def add_member(
    space_id: str, payload: MemberCreateRequest, user: User = Depends(current_user)
) -> MemberResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        identifier = (payload.identifier or payload.user_id or "").strip()
        if not identifier:
            raise HTTPException(status_code=422, detail="请提供用户 ID、用户名或邮箱")
        target = users.resolve_by_identifier(db, identifier)
        if not target:
            raise HTTPException(
                status_code=404, detail="用户不存在，请确认用户名或邮箱是否正确"
            )
        if target.status != "active":
            raise HTTPException(
                status_code=409, detail="该账户当前不可用，无法加入空间"
            )
        member = spaces.add_member(db, space_id, target.id, payload.role)
        space.scope_version += 1
        space.updated_at = now()
        audit.record(
            db, user.id, "space_member_added", f"{space_id}:{target.id}", "success"
        )
        return member_response(db, member)


@router.post(
    "/knowledge-spaces/{space_id}/members:all", response_model=MemberBulkAddResponse
)
def add_all_members(
    space_id: str, payload: MemberBulkAddRequest, user: User = Depends(current_user)
) -> MemberBulkAddResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        active_users = list(
            db.scalars(
                select(User)
                .where(User.status == "active")
                .order_by(User.created_at, User.id)
            )
        )
        existing_members = {
            member.user_id: member for member in spaces.list_members(db, space_id)
        }
        added_count = 0
        existing_count = 0
        for target in active_users:
            if target.id in existing_members:
                existing_count += 1
                continue
            existing_members[target.id] = spaces.add_member(
                db, space_id, target.id, payload.role
            )
            added_count += 1
        if added_count:
            space.scope_version += 1
            space.updated_at = now()
            audit.record(db, user.id, "space_members_bulk_added", space.id, "success")
        db.flush()
        return MemberBulkAddResponse(
            items=[
                member_response(db, item) for item in spaces.list_members(db, space_id)
            ],
            added_count=added_count,
            existing_count=existing_count,
        )


@router.delete(
    "/knowledge-spaces/{space_id}/members:all",
    response_model=MemberBulkRemoveResponse,
)
def remove_all_members_except_owner(
    space_id: str, user: User = Depends(current_user)
) -> MemberBulkRemoveResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        members = spaces.list_members(db, space_id)
        removed_count = 0
        for member in members:
            if member.user_id == space.owner_user_id:
                continue
            db.delete(member)
            removed_count += 1
        if removed_count:
            space.scope_version += 1
            space.updated_at = now()
            audit.record(
                db, user.id, "space_members_bulk_removed", space.id, "success"
            )
        db.flush()
        return MemberBulkRemoveResponse(
            items=[
                member_response(db, item) for item in spaces.list_members(db, space_id)
            ],
            removed_count=removed_count,
        )


@router.get(
    "/knowledge-spaces/{space_id}/categories", response_model=list[CategoryResponse]
)
def list_categories(
    space_id: str, user: User = Depends(current_user)
) -> list[CategoryResponse]:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader", allow_archived=True
        )
        return [
            CategoryResponse(
                id=item.id,
                space_id=item.space_id,
                parent_id=item.parent_id,
                name=item.name,
                sort_order=item.sort_order,
                path=item.path,
            )
            for item in spaces.list_categories(db, space_id)
        ]


@router.post(
    "/knowledge-spaces/{space_id}/categories",
    response_model=CategoryResponse,
    status_code=201,
)
def add_category(
    space_id: str, payload: CategoryCreateRequest, user: User = Depends(current_user)
) -> CategoryResponse:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        if payload.parent_id and not db.scalar(
            select(DocumentCategory).where(
                DocumentCategory.id == payload.parent_id,
                DocumentCategory.space_id == space_id,
            )
        ):
            raise HTTPException(status_code=404, detail="父级分类不存在")
        category = spaces.create_category(
            db, space_id, payload.name, payload.parent_id, payload.sort_order
        )
        return CategoryResponse(
            id=category.id,
            space_id=category.space_id,
            parent_id=category.parent_id,
            name=category.name,
            sort_order=category.sort_order,
            path=category.path,
        )


@router.delete(
    "/knowledge-spaces/{space_id}/categories/{category_id}",
    status_code=204,
    response_model=None,
)
def remove_category(
    space_id: str, category_id: str, user: User = Depends(current_user)
) -> None:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        category = spaces.get_category(db, space_id, category_id)
        if not category:
            raise HTTPException(status_code=404, detail="文档分类不存在")
        spaces.delete_category(db, category)


@router.patch(
    "/knowledge-spaces/{space_id}/members/{user_id}", response_model=MemberResponse
)
def update_member(
    space_id: str,
    user_id: str,
    payload: MemberUpdateRequest,
    user: User = Depends(current_user),
) -> MemberResponse:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        member = db.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
            )
        )
        if not member:
            raise HTTPException(status_code=404, detail="成员不存在")
        PermissionService.require_owner_role_change_allowed(
            db, space_id, user_id, payload.role, space=space
        )
        member.role = payload.role
        member.updated_at = now()
        space.scope_version += 1
        space.updated_at = now()
        return member_response(db, member)


@router.delete(
    "/knowledge-spaces/{space_id}/members/{user_id}",
    status_code=204,
    response_model=None,
)
def remove_member(
    space_id: str, user_id: str, user: User = Depends(current_user)
) -> None:
    with transaction() as db:
        space = PermissionService.require_space_access(
            db, user, space_id, minimum_role="manager"
        )
        member = db.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
            )
        )
        if not member:
            raise HTTPException(status_code=404, detail="成员不存在")
        PermissionService.require_not_space_owner(db, space_id, user_id, space=space)
        db.delete(member)
        space.scope_version += 1
        space.updated_at = now()
