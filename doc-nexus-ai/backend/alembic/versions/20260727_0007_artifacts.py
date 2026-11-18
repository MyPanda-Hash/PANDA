"""Create owner-scoped export artifact records.

Revision ID: 20260727_0007
Revises: 20260727_0006
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0007"
down_revision = "20260727_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False, server_default="writing_export"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('ready', 'expired', 'regenerating')", name="ck_artifacts_status"),
        sa.ForeignKeyConstraint(["operation_id"], ["writing_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["writing_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_owner_user_id", "artifacts", ["owner_user_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_operation_id", "artifacts", ["operation_id"])
    op.create_index("ix_artifacts_owner_created", "artifacts", ["owner_user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_owner_created", table_name="artifacts")
    op.drop_index("ix_artifacts_operation_id", table_name="artifacts")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_index("ix_artifacts_owner_user_id", table_name="artifacts")
    op.drop_table("artifacts")
