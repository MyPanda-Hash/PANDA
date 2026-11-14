"""Add durable external-object cleanup tombstones.

Revision ID: 20260904_0081
Revises: 20260904_0080
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0081"
down_revision = "20260904_0080"
branch_labels = None
depends_on = None


_TABLE = "external_object_cleanup_outbox"
_INDEXES = [
    ("ix_external_object_cleanup_outbox_status", ["status"]),
    ("ix_external_object_cleanup_outbox_next_attempt_at", ["next_attempt_at"]),
    ("ix_external_object_cleanup_ready", ["status", "next_attempt_at"]),
]


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    # Guard so a later re-stamp that re-runs this migration does not hard-fail on
    # an already-present table or index.
    if not insp.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("uri", sa.Text(), nullable=False),
            sa.Column(
                "resource_kind",
                sa.String(length=64),
                nullable=False,
                server_default="minio_object",
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claim_token", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("uri", name="uq_external_object_cleanup_uri"),
            sa.UniqueConstraint("claim_token", name="uq_external_object_cleanup_claim"),
            sa.CheckConstraint(
                "status in ('pending', 'processing', 'succeeded')",
                name="ck_external_object_cleanup_status",
            ),
        )
    for index_name, columns in _INDEXES:
        if not _has_index(_TABLE, index_name):
            op.create_index(index_name, _TABLE, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(_TABLE):
        return
    for index_name, _columns in reversed(_INDEXES):
        if _has_index(_TABLE, index_name):
            op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
