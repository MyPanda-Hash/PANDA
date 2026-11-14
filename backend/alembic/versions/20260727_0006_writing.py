"""Create owner-scoped writing projects, versions, claims, and operations.

Revision ID: 20260727_0006
Revises: 20260727_0005
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0006"
down_revision = "20260727_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("writing_projects", sa.Column("id", sa.String(64), nullable=False), sa.Column("owner_user_id", sa.String(64), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("selected_space_ids", sa.Text(), nullable=False), sa.Column("budget_limit", sa.String(32), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_writing_projects_owner_user_id", "writing_projects", ["owner_user_id"]); op.create_index("ix_writing_projects_owner_updated", "writing_projects", ["owner_user_id", "updated_at"])
    op.create_table("writing_outlines", sa.Column("id", sa.String(64), nullable=False), sa.Column("project_id", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("status in ('draft', 'confirmed')", name="ck_writing_outlines_status"), sa.ForeignKeyConstraint(["project_id"], ["writing_projects.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("project_id"))
    op.create_index("ix_writing_outlines_project_id", "writing_outlines", ["project_id"])
    op.create_table("writing_chapters", sa.Column("id", sa.String(64), nullable=False), sa.Column("outline_id", sa.String(64), nullable=False), sa.Column("position", sa.Integer(), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["outline_id"], ["writing_outlines.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_writing_chapters_outline_id", "writing_chapters", ["outline_id"]); op.create_index("ix_writing_chapters_outline_position", "writing_chapters", ["outline_id", "position"])
    op.create_table("writing_versions", sa.Column("id", sa.String(64), nullable=False), sa.Column("chapter_id", sa.String(64), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["chapter_id"], ["writing_chapters.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_writing_versions_chapter_id", "writing_versions", ["chapter_id"]); op.create_index("ix_writing_versions_chapter_created", "writing_versions", ["chapter_id", "created_at"])
    op.create_table("writing_claims", sa.Column("id", sa.String(64), nullable=False), sa.Column("project_id", sa.String(64), nullable=False), sa.Column("text", sa.Text(), nullable=False), sa.Column("support_state", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("support_state in ('supported', 'uncertain', 'unsupported')", name="ck_writing_claims_support_state"), sa.ForeignKeyConstraint(["project_id"], ["writing_projects.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_writing_claims_project_id", "writing_claims", ["project_id"])
    op.create_table("writing_operations", sa.Column("id", sa.String(64), nullable=False), sa.Column("project_id", sa.String(64), nullable=False), sa.Column("kind", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("usage", sa.Text(), nullable=False), sa.Column("result", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.CheckConstraint("status in ('queued', 'running', 'completed', 'failed', 'stopped')", name="ck_writing_operations_status"), sa.ForeignKeyConstraint(["project_id"], ["writing_projects.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_writing_operations_project_id", "writing_operations", ["project_id"]); op.create_index("ix_writing_operations_project_created", "writing_operations", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_writing_operations_project_created", table_name="writing_operations"); op.drop_index("ix_writing_operations_project_id", table_name="writing_operations"); op.drop_table("writing_operations")
    op.drop_index("ix_writing_claims_project_id", table_name="writing_claims"); op.drop_table("writing_claims")
    op.drop_index("ix_writing_versions_chapter_created", table_name="writing_versions"); op.drop_index("ix_writing_versions_chapter_id", table_name="writing_versions"); op.drop_table("writing_versions")
    op.drop_index("ix_writing_chapters_outline_position", table_name="writing_chapters"); op.drop_index("ix_writing_chapters_outline_id", table_name="writing_chapters"); op.drop_table("writing_chapters")
    op.drop_index("ix_writing_outlines_project_id", table_name="writing_outlines"); op.drop_table("writing_outlines")
    op.drop_index("ix_writing_projects_owner_updated", table_name="writing_projects"); op.drop_index("ix_writing_projects_owner_user_id", table_name="writing_projects"); op.drop_table("writing_projects")
