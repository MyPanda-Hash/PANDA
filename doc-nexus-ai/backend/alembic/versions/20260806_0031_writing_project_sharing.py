"""Add shared writing project snapshots.

Revision ID: 20260806_0031
Revises: 20260806_0030
"""

import sqlalchemy as sa
from alembic import op

revision = "20260806_0031"
down_revision = "20260806_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_project_publications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_project_id", sa.String(length=64), nullable=False),
        sa.Column("source_owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("shared_by_user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "share_scope",
            sa.String(length=32),
            nullable=False,
            server_default="all_internal",
        ),
        sa.Column("allow_remix", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_project_id"], ["writing_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_writing_publications_status_published",
        "writing_project_publications",
        ["status", "published_at"],
    )
    op.create_index(
        "ix_writing_publications_source_project",
        "writing_project_publications",
        ["source_project_id", "version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_publications_source_project",
        table_name="writing_project_publications",
    )
    op.drop_index(
        "ix_writing_publications_status_published",
        table_name="writing_project_publications",
    )
    op.drop_table("writing_project_publications")
