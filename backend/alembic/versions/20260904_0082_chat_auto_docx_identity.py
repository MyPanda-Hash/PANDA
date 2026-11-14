"""Add stable identity for chat auto-DOCX projects.

Revision ID: 20260904_0082
Revises: 20260904_0081
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0082"
down_revision = "20260904_0081"
branch_labels = None
depends_on = None


_TABLE = "writing_projects"
_UQ_NAME = "uq_writing_projects_chat_auto_export"
_IX_NAME = "ix_writing_projects_source_chat_session_id"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def _has_unique_constraint(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(uc["name"] == name for uc in insp.get_unique_constraints(table))


def upgrade() -> None:
    # Guard every DDL op independently so a later re-stamp that re-runs this
    # migration on a partially-migrated database does not hard-fail.
    with op.batch_alter_table(_TABLE) as batch:
        if not _has_column(_TABLE, "source_chat_session_id"):
            batch.add_column(
                sa.Column("source_chat_session_id", sa.String(length=64), nullable=True)
            )
        if not _has_column(_TABLE, "source_chat_assistant_message_id"):
            batch.add_column(
                sa.Column(
                    "source_chat_assistant_message_id",
                    sa.String(length=64),
                    nullable=True,
                )
            )
        if not _has_unique_constraint(_TABLE, _UQ_NAME):
            batch.create_unique_constraint(
                _UQ_NAME,
                [
                    "owner_user_id",
                    "source_chat_session_id",
                    "source_chat_assistant_message_id",
                ],
            )
        if not _has_index(_TABLE, _IX_NAME):
            batch.create_index(
                _IX_NAME,
                ["source_chat_session_id"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        if _has_index(_TABLE, _IX_NAME):
            batch.drop_index(_IX_NAME)
        if _has_unique_constraint(_TABLE, _UQ_NAME):
            batch.drop_constraint(_UQ_NAME, type_="unique")
        if _has_column(_TABLE, "source_chat_assistant_message_id"):
            batch.drop_column("source_chat_assistant_message_id")
        if _has_column(_TABLE, "source_chat_session_id"):
            batch.drop_column("source_chat_session_id")
