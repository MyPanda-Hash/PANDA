"""Durable per-employee personal memory.

Revision ID: 20260909_0088
Revises: 20260908_0087

Two constraints on ``user_memory_entries`` and ``user_memory_turns`` look
similar but do different jobs, and neither substitutes for the other:

* ``uq_..._owner_id`` is a non-partial UNIQUE over ``(owner_user_id, id)``. It
  exists solely so the composite foreign keys on evidence and jobs have a
  unique target, which is what makes the database itself reject attaching one
  employee's fact to another employee's source.
* ``uq_user_memory_entries_active_key`` is a partial unique index allowing at
  most one ``status='active'`` row per ``(owner_user_id, key)``.

``user_memory_operations`` deliberately has no foreign key on its
``turn_id``/``job_id``/``memory_id`` columns: a receipt must outlive the row it
touched so a replayed request reports the current revoked state instead of
resurrecting a deleted value.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0088"
down_revision = "20260908_0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_states",
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "use_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "learning_mode",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "automatic_source_cutoff_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_user_id"),
    )

    op.create_table(
        "user_memory_turns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("request_key", sa.String(length=255), nullable=False),
        sa.Column("runtime_session_id", sa.String(length=128), nullable=True),
        sa.Column("chat_session_id", sa.String(length=64), nullable=True),
        sa.Column("chat_message_id", sa.String(length=64), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_text_safe", sa.Text(), nullable=True),
        sa.Column("source_text_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="live"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["chat_message_id"], ["chat_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "channel",
            "request_key",
            name="uq_user_memory_turns_owner_channel_request",
        ),
        # Composite foreign-key target for evidence and jobs. Non-partial.
        sa.UniqueConstraint(
            "owner_user_id", "id", name="uq_user_memory_turns_owner_id"
        ),
    )
    op.create_index(
        "ix_user_memory_turns_owner_user_id", "user_memory_turns", ["owner_user_id"]
    )
    op.create_index(
        "ix_user_memory_turns_owner_created",
        "user_memory_turns",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_user_memory_turns_status_expiry",
        "user_memory_turns",
        ["status", "source_text_expires_at"],
    )
    op.create_index(
        "ix_user_memory_turns_chat_message", "user_memory_turns", ["chat_message_id"]
    )

    op.create_table(
        "user_memory_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=True),
        sa.Column("value_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="candidate"
        ),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expected_active_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "forgotten_source_cutoff_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Composite foreign-key target for evidence. Non-partial, all statuses.
        sa.UniqueConstraint(
            "owner_user_id", "id", name="uq_user_memory_entries_owner_id"
        ),
    )
    op.create_index(
        "ix_user_memory_entries_owner_user_id",
        "user_memory_entries",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_user_memory_entries_owner_status",
        "user_memory_entries",
        ["owner_user_id", "status"],
    )
    op.create_index(
        "ix_user_memory_entries_status_expires",
        "user_memory_entries",
        ["status", "expires_at"],
    )
    # One active fact per key, expressed identically on PostgreSQL and SQLite.
    op.create_index(
        "uq_user_memory_entries_active_key",
        "user_memory_entries",
        ["owner_user_id", "key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "user_memory_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("quote_start", sa.Integer(), nullable=True),
        sa.Column("quote_end", sa.Integer(), nullable=True),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Both keys carry the owner, so a cross-owner link is rejected by the
        # database and not only by application code.
        sa.ForeignKeyConstraint(
            ["owner_user_id", "memory_id"],
            ["user_memory_entries.owner_user_id", "user_memory_entries.id"],
            name="fk_user_memory_evidence_entry",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "turn_id"],
            ["user_memory_turns.owner_user_id", "user_memory_turns.id"],
            name="fk_user_memory_evidence_turn",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "memory_id",
            "turn_id",
            name="uq_user_memory_evidence_memory_turn",
        ),
    )
    op.create_index(
        "ix_user_memory_evidence_owner_user_id",
        "user_memory_evidence",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_user_memory_evidence_turn",
        "user_memory_evidence",
        ["owner_user_id", "turn_id"],
    )

    op.create_table(
        "user_memory_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claim_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_safe", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "turn_id"],
            ["user_memory_turns.owner_user_id", "user_memory_turns.id"],
            name="fk_user_memory_jobs_turn",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "turn_id",
            "source_hash",
            "extractor_version",
            name="uq_user_memory_jobs_source_version",
        ),
    )
    op.create_index(
        "ix_user_memory_jobs_owner_user_id", "user_memory_jobs", ["owner_user_id"]
    )
    op.create_index(
        "ix_user_memory_jobs_claimable",
        "user_memory_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_user_memory_jobs_owner_status",
        "user_memory_jobs",
        ["owner_user_id", "status"],
    )

    op.create_table(
        "user_memory_operations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        # Soft references on purpose: a receipt outlives what it touched.
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("memory_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("result_memory_id", sa.String(length=64), nullable=True),
        sa.Column("result_revision", sa.Integer(), nullable=True),
        sa.Column("usage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "kind",
            "request_id",
            name="uq_user_memory_operations_request",
        ),
    )
    op.create_index(
        "ix_user_memory_operations_owner_user_id",
        "user_memory_operations",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_user_memory_operations_owner_created",
        "user_memory_operations",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_memory_operations_owner_created", table_name="user_memory_operations"
    )
    op.drop_index(
        "ix_user_memory_operations_owner_user_id", table_name="user_memory_operations"
    )
    op.drop_table("user_memory_operations")

    op.drop_index("ix_user_memory_jobs_owner_status", table_name="user_memory_jobs")
    op.drop_index("ix_user_memory_jobs_claimable", table_name="user_memory_jobs")
    op.drop_index("ix_user_memory_jobs_owner_user_id", table_name="user_memory_jobs")
    op.drop_table("user_memory_jobs")

    op.drop_index(
        "ix_user_memory_evidence_turn", table_name="user_memory_evidence"
    )
    op.drop_index(
        "ix_user_memory_evidence_owner_user_id", table_name="user_memory_evidence"
    )
    op.drop_table("user_memory_evidence")

    op.drop_index(
        "uq_user_memory_entries_active_key", table_name="user_memory_entries"
    )
    op.drop_index(
        "ix_user_memory_entries_status_expires", table_name="user_memory_entries"
    )
    op.drop_index(
        "ix_user_memory_entries_owner_status", table_name="user_memory_entries"
    )
    op.drop_index(
        "ix_user_memory_entries_owner_user_id", table_name="user_memory_entries"
    )
    op.drop_table("user_memory_entries")

    op.drop_index("ix_user_memory_turns_chat_message", table_name="user_memory_turns")
    op.drop_index("ix_user_memory_turns_status_expiry", table_name="user_memory_turns")
    op.drop_index("ix_user_memory_turns_owner_created", table_name="user_memory_turns")
    op.drop_index("ix_user_memory_turns_owner_user_id", table_name="user_memory_turns")
    op.drop_table("user_memory_turns")

    op.drop_table("user_memory_states")
