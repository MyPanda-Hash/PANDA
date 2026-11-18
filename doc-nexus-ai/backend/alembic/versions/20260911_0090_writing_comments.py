"""Position-bound review comments and human-authored candidates.

Adds ``writing_comments`` and marks provenance on ``writing_candidates`` so a
person's suggestion and an agent's candidate share one lifecycle instead of two.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260911_0090"
down_revision = "20260910_0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_comments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("writing_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_id", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column(
            "author_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "anchor_version_id", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column("anchor_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "anchor_prefix_sha256", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column(
            "anchor_suffix_sha256", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column(
            "anchor_state", sa.String(16), nullable=False, server_default="exact"
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by_user_id", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('open', 'resolved', 'outdated')",
            name="ck_writing_comments_status",
        ),
        sa.CheckConstraint(
            "anchor_state in ('exact', 'shifted', 'lost', 'chapter')",
            name="ck_writing_comments_anchor_state",
        ),
    )
    op.create_index(
        "ix_writing_comments_project_id", "writing_comments", ["project_id"]
    )
    op.create_index(
        "ix_writing_comments_chapter_id", "writing_comments", ["chapter_id"]
    )
    op.create_index("ix_writing_comments_thread_id", "writing_comments", ["thread_id"])
    op.create_index(
        "ix_writing_comments_author_user_id", "writing_comments", ["author_user_id"]
    )
    op.create_index("ix_writing_comments_status", "writing_comments", ["status"])
    op.create_index(
        "ix_writing_comments_project_status",
        "writing_comments",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_writing_comments_chapter_status",
        "writing_comments",
        ["chapter_id", "status"],
    )

    op.add_column(
        "writing_candidates",
        sa.Column("origin", sa.String(16), nullable=False, server_default="agent"),
    )
    op.add_column(
        "writing_candidates", sa.Column("author_user_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "writing_candidates", sa.Column("comment_id", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("writing_candidates") as batch:
        for name in ("comment_id", "author_user_id", "origin"):
            batch.drop_column(name)
    for name in (
        "ix_writing_comments_chapter_status",
        "ix_writing_comments_project_status",
        "ix_writing_comments_status",
        "ix_writing_comments_author_user_id",
        "ix_writing_comments_thread_id",
        "ix_writing_comments_chapter_id",
        "ix_writing_comments_project_id",
    ):
        op.drop_index(name, table_name="writing_comments")
    op.drop_table("writing_comments")
