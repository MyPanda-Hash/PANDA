"""Create roles, permissions, knowledge spaces, members and categories.

Revision ID: 20260727_0002
Revises: 20260727_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("roles", sa.Column("id", sa.String(64), primary_key=True), sa.Column("code", sa.String(64), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.Text(), nullable=False, server_default=""), sa.Column("scope", sa.String(32), nullable=False, server_default="system"), sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_roles_code", "roles", ["code"], unique=True)
    op.create_table("permissions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("code", sa.String(100), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.Text(), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)
    op.create_table("role_permissions", sa.Column("role_id", sa.String(64), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True), sa.Column("permission_id", sa.String(64), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("user_roles", sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("role_id", sa.String(64), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("knowledge_spaces", sa.Column("id", sa.String(64), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("description", sa.Text(), nullable=False, server_default=""), sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("owner_user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False), sa.Column("scope_version", sa.Integer(), nullable=False, server_default="1"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_knowledge_spaces_name", "knowledge_spaces", ["name"])
    op.create_index("ix_knowledge_spaces_status", "knowledge_spaces", ["status"])
    op.create_index("ix_knowledge_spaces_owner_user_id", "knowledge_spaces", ["owner_user_id"])
    op.create_table("space_members", sa.Column("id", sa.String(64), primary_key=True), sa.Column("space_id", sa.String(64), sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("space_id", "user_id", name="uq_space_members_space_user"))
    op.create_index("ix_space_members_space_id", "space_members", ["space_id"])
    op.create_index("ix_space_members_user_id", "space_members", ["user_id"])
    op.create_table("document_categories", sa.Column("id", sa.String(64), primary_key=True), sa.Column("space_id", sa.String(64), sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), nullable=False), sa.Column("parent_id", sa.String(64), sa.ForeignKey("document_categories.id", ondelete="CASCADE")), sa.Column("name", sa.String(160), nullable=False), sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"), sa.Column("path", sa.Text(), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("space_id", "parent_id", "name", name="uq_category_parent_name"))
    op.create_index("ix_document_categories_space_id", "document_categories", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_document_categories_space_id", table_name="document_categories")
    op.drop_table("document_categories")
    op.drop_index("ix_space_members_user_id", table_name="space_members")
    op.drop_index("ix_space_members_space_id", table_name="space_members")
    op.drop_table("space_members")
    op.drop_index("ix_knowledge_spaces_owner_user_id", table_name="knowledge_spaces")
    op.drop_index("ix_knowledge_spaces_status", table_name="knowledge_spaces")
    op.drop_index("ix_knowledge_spaces_name", table_name="knowledge_spaces")
    op.drop_table("knowledge_spaces")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_index("ix_permissions_code", table_name="permissions")
    op.drop_table("permissions")
    op.drop_index("ix_roles_code", table_name="roles")
    op.drop_table("roles")
