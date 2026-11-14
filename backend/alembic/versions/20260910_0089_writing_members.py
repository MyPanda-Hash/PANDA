"""Writing project collaborators and chapter concurrency columns.

Adds ``writing_project_members`` (the project owner stays implicit via
``writing_projects.owner_user_id`` and is never materialised, so existing
projects need no backfill) plus the optimistic-concurrency and advisory-lock
columns on ``writing_chapters``.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260910_0089"
down_revision = "68cf522fd227"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_project_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("writing_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("invited_by_user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id", "user_id", name="uq_writing_member_project_user"
        ),
        sa.CheckConstraint(
            "role in ('owner', 'editor', 'commenter', 'viewer')",
            name="ck_writing_members_role",
        ),
        sa.CheckConstraint(
            "status in ('active', 'removed')", name="ck_writing_members_status"
        ),
    )
    op.create_index(
        "ix_writing_project_members_project_id",
        "writing_project_members",
        ["project_id"],
    )
    op.create_index(
        "ix_writing_project_members_user_id",
        "writing_project_members",
        ["user_id"],
    )
    op.create_index(
        "ix_writing_project_members_status",
        "writing_project_members",
        ["status"],
    )
    op.create_index(
        "ix_writing_members_user_status",
        "writing_project_members",
        ["user_id", "status"],
    )

    # Optimistic concurrency + advisory edit lock for multi-editor chapters.
    op.add_column(
        "writing_chapters",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "writing_chapters",
        sa.Column("last_editor_user_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "writing_chapters",
        sa.Column("edit_lock_user_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "writing_chapters",
        sa.Column("edit_lock_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("writing_chapters") as batch:
        for name in (
            "edit_lock_expires_at",
            "edit_lock_user_id",
            "last_editor_user_id",
            "revision",
        ):
            batch.drop_column(name)
    op.drop_index(
        "ix_writing_members_user_status", table_name="writing_project_members"
    )
    op.drop_index(
        "ix_writing_project_members_status", table_name="writing_project_members"
    )
    op.drop_index(
        "ix_writing_project_members_user_id", table_name="writing_project_members"
    )
    op.drop_index(
        "ix_writing_project_members_project_id", table_name="writing_project_members"
    )
    op.drop_table("writing_project_members")
