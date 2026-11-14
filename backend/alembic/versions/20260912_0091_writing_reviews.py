"""Approval rounds and issued-document state for writing projects.

``review_required`` defaults to false so existing single-author projects keep
exporting exactly as they do today; the gate only applies once a project opts in.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260912_0091"
down_revision = "20260911_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("writing_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(64), nullable=False),
        sa.Column("reviewer_user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "open_comment_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_writing_reviews_status",
        ),
    )
    op.create_index("ix_writing_reviews_project_id", "writing_reviews", ["project_id"])
    op.create_index(
        "ix_writing_reviews_reviewer_user_id", "writing_reviews", ["reviewer_user_id"]
    )
    op.create_index("ix_writing_reviews_status", "writing_reviews", ["status"])
    op.create_index(
        "ix_writing_reviews_project_round", "writing_reviews", ["project_id", "round"]
    )

    op.add_column(
        "writing_projects",
        sa.Column("doc_status", sa.String(16), nullable=False, server_default="draft"),
    )
    op.add_column(
        "writing_projects",
        sa.Column(
            "review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "writing_projects",
        sa.Column("approved_review_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "writing_projects",
        sa.Column("approved_content_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "writing_projects", sa.Column("doc_number", sa.String(64), nullable=True)
    )
    op.add_column(
        "writing_projects",
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "writing_projects",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("writing_projects") as batch:
        for name in (
            "valid_until",
            "issued_at",
            "doc_number",
            "approved_content_hash",
            "approved_review_id",
            "review_required",
            "doc_status",
        ):
            batch.drop_column(name)
    for name in (
        "ix_writing_reviews_project_round",
        "ix_writing_reviews_status",
        "ix_writing_reviews_reviewer_user_id",
        "ix_writing_reviews_project_id",
    ):
        op.drop_index(name, table_name="writing_reviews")
    op.drop_table("writing_reviews")
