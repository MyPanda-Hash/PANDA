from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.access import DocumentCategory, KnowledgeSpace, SpaceMember


def list_for_user(
    db: Session, user_id: str, is_admin: bool = False
) -> list[KnowledgeSpace]:
    # Keep archived spaces visible for the "已归档" filter; hide soft-deleted ones.
    visible = KnowledgeSpace.status != "deleted"
    if is_admin:
        return list(
            db.scalars(
                select(KnowledgeSpace)
                .where(visible)
                .order_by(KnowledgeSpace.updated_at.desc())
            )
        )
    member_space_ids = select(SpaceMember.space_id).where(SpaceMember.user_id == user_id)
    return list(
        db.scalars(
            select(KnowledgeSpace)
            .where(
                visible,
                or_(
                    KnowledgeSpace.owner_user_id == user_id,
                    KnowledgeSpace.id.in_(member_space_ids),
                ),
            )
            .order_by(KnowledgeSpace.updated_at.desc())
        )
    )


def update_space(
    space: KnowledgeSpace, *, name: str | None = None, description: str | None = None
) -> KnowledgeSpace:
    if name is not None:
        space.name = name.strip()
    if description is not None:
        space.description = description
    space.updated_at = now()
    return space


def soft_delete_space(space: KnowledgeSpace) -> KnowledgeSpace:
    space.status = "deleted"
    space.scope_version += 1
    space.updated_at = now()
    return space


_DEFAULT_CATEGORIES = [
    ("产品文档", 0),
    ("技术文档", 1),
    ("API文档", 2),
    ("设计文档", 3),
    ("其他", 4),
]


def create_space(
    db: Session, owner_user_id: str, name: str, description: str
) -> KnowledgeSpace:
    created = now()
    space = KnowledgeSpace(
        id=f"spc_{new_token(10)}",
        name=name,
        description=description,
        owner_user_id=owner_user_id,
        status="active",
        scope_version=1,
        created_at=created,
        updated_at=created,
    )
    db.add(space)
    db.flush()
    db.add(
        SpaceMember(
            id=f"mem_{new_token(10)}",
            space_id=space.id,
            user_id=owner_user_id,
            role="manager",
            created_at=created,
            updated_at=created,
        )
    )
    # Seed default document categories
    for cat_name, sort_order in _DEFAULT_CATEGORIES:
        db.add(
            DocumentCategory(
                id=f"cat_{new_token(10)}",
                space_id=space.id,
                name=cat_name,
                sort_order=sort_order,
                path=cat_name,
                created_at=created,
                updated_at=created,
            )
        )
    return space


_DEFAULT_SPACE_NAME = "默认资料空间"
_DEFAULT_SPACE_DESCRIPTION = "平台初始化时自动创建的资料空间。"


def ensure_default_space(db: Session, owner_user_id: str) -> KnowledgeSpace | None:
    if db.scalar(select(KnowledgeSpace.id).limit(1)) is not None:
        return None
    return create_space(
        db,
        owner_user_id=owner_user_id,
        name=_DEFAULT_SPACE_NAME,
        description=_DEFAULT_SPACE_DESCRIPTION,
    )


def add_member(db: Session, space_id: str, user_id: str, role: str) -> SpaceMember:
    member = db.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
        )
    )
    if member:
        member.role = role
        member.updated_at = now()
        return member
    member = SpaceMember(
        id=f"mem_{new_token(10)}",
        space_id=space_id,
        user_id=user_id,
        role=role,
        created_at=now(),
        updated_at=now(),
    )
    db.add(member)
    return member


def list_members(db: Session, space_id: str) -> list[SpaceMember]:
    return list(
        db.scalars(
            select(SpaceMember)
            .where(SpaceMember.space_id == space_id)
            .order_by(SpaceMember.created_at)
        )
    )


def list_categories(db: Session, space_id: str) -> list[DocumentCategory]:
    return list(
        db.scalars(
            select(DocumentCategory)
            .where(DocumentCategory.space_id == space_id)
            .order_by(DocumentCategory.sort_order, DocumentCategory.name)
        )
    )


def create_category(
    db: Session, space_id: str, name: str, parent_id: str | None, sort_order: int
) -> DocumentCategory:
    category = DocumentCategory(
        id=f"cat_{new_token(10)}",
        space_id=space_id,
        parent_id=parent_id,
        name=name,
        sort_order=sort_order,
        path=name,
        created_at=now(),
        updated_at=now(),
    )
    db.add(category)
    db.flush()
    return category


def get_category(
    db: Session, space_id: str, category_id: str
) -> DocumentCategory | None:
    return db.scalar(
        select(DocumentCategory).where(
            DocumentCategory.id == category_id,
            DocumentCategory.space_id == space_id,
        )
    )


def delete_category(db: Session, category: DocumentCategory) -> None:
    db.delete(category)
    db.flush()
