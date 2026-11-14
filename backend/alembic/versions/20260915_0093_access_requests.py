"""Access requests, plus the frozen citation set on writing publications.

``access_requests`` is approval context only — authorization keeps reading
``space_members`` / ``writing_project_members``, never this table.

``writing_project_publications.citation_refs_json`` is nullable on purpose:
NULL means "never recorded" (every pre-existing publication) and must stay
distinguishable from a recorded empty set. There is deliberately **no
backfill** — a set reconstructed from today's receipts can contain citations
retrieved *after* the snapshot was published, which would promote old,
never-verified markers into "trusted".
"""

from alembic import op
import sqlalchemy as sa

revision = "20260915_0093"
down_revision = "20260912_0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("target_type", sa.String(24), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("granted_role", sa.String(16), nullable=False),
        sa.Column(
            "requester_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("source_label", sa.String(255), nullable=False, server_default=""),
        sa.Column("source_refs_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("citation_refs_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("open_locator_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "decided_by_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_access_requests_target_type", "access_requests", ["target_type"]
    )
    op.create_index("ix_access_requests_target_id", "access_requests", ["target_id"])
    op.create_index(
        "ix_access_requests_requester_user_id", "access_requests", ["requester_user_id"]
    )
    op.create_index("ix_access_requests_status", "access_requests", ["status"])
    op.create_index(
        "ix_access_requests_status_target",
        "access_requests",
        ["status", "target_type", "target_id"],
    )
    op.create_index(
        "uq_access_requests_pending_target",
        "access_requests",
        ["target_type", "target_id", "requester_user_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.add_column(
        "writing_project_publications",
        sa.Column("citation_refs_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("writing_project_publications", "citation_refs_json")
    op.drop_index("uq_access_requests_pending_target", table_name="access_requests")
    for name in (
        "ix_access_requests_status_target",
        "ix_access_requests_status",
        "ix_access_requests_requester_user_id",
        "ix_access_requests_target_id",
        "ix_access_requests_target_type",
    ):
        op.drop_index(name, table_name="access_requests")
    op.drop_table("access_requests")
