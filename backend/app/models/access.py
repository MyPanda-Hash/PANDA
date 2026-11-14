from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(32), default="system")
    builtin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    permissions: Mapped[list[RolePermission]] = relationship(back_populates="role", cascade="all, delete-orphan")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    roles: Mapped[list[RolePermission]] = relationship(back_populates="permission", cascade="all, delete-orphan")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship(back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class KnowledgeSpace(Base):
    __tablename__ = "knowledge_spaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scope_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    members: Mapped[list[SpaceMember]] = relationship(back_populates="space", cascade="all, delete-orphan")
    categories: Mapped[list[DocumentCategory]] = relationship(back_populates="space", cascade="all, delete-orphan")


class SpaceMember(Base):
    __tablename__ = "space_members"
    __table_args__ = (UniqueConstraint("space_id", "user_id", name="uq_space_members_space_user"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    space: Mapped[KnowledgeSpace] = relationship(back_populates="members")


class DocumentCategory(Base):
    __tablename__ = "document_categories"
    __table_args__ = (UniqueConstraint("space_id", "parent_id", "name", name="uq_category_parent_name"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("document_categories.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    space: Mapped[KnowledgeSpace] = relationship(back_populates="categories")


class AccessRequest(Base):
    """One access request against one target, per requester.

    The request row is approval context only: authorization always reads the
    real grant (``SpaceMember`` / ``WritingProjectMember``), never this table.
    ``target_type`` is not a response projection — it decides both who may
    approve and which grant is written.

    The JSON columns share one envelope (``{"items": [...], "truncated":
    bool}``, see ``app.services.access_requests``); the ``source_*`` triple
    records the *first* anchor (the list headline) while ``source_refs_json``
    is the authoritative set of anchors this request covers, so cross-anchor
    merges cannot leave the approver reading one anchor and approving another.
    """

    __tablename__ = "access_requests"
    __table_args__ = (
        Index(
            "uq_access_requests_pending_target",
            "target_type",
            "target_id",
            "requester_user_id",
            unique=True,
            sqlite_where=column("status", String) == "pending",
            postgresql_where=column("status", String) == "pending",
        ),
        Index(
            "ix_access_requests_status_target",
            "status",
            "target_type",
            "target_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(24), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    granted_role: Mapped[str] = mapped_column(String(16))
    requester_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(64))
    source_label: Mapped[str] = mapped_column(String(255), default="")
    source_refs_json: Mapped[str] = mapped_column(Text, default="")
    citation_refs_json: Mapped[str] = mapped_column(Text, default="")
    open_locator_json: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
