"""Repair chat auto-DOCX identity after the historical 0082 collision.

Revision ID: 20260904_0085
Revises: 20260904_0084
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision = "20260904_0085"
down_revision = "20260904_0084"
branch_labels = None
depends_on = None


_TABLE = "writing_projects"
_SESSION_COLUMN = "source_chat_session_id"
_MESSAGE_COLUMN = "source_chat_assistant_message_id"
_UNIQUE_CONSTRAINT = "uq_writing_projects_chat_auto_export"
_SESSION_INDEX = "ix_writing_projects_source_chat_session_id"


def _inspector():
    if context.is_offline_mode():
        return None
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    if inspector is None or _TABLE not in inspector.get_table_names():
        return

    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    target_unique_signature = (
        "owner_user_id",
        _SESSION_COLUMN,
        _MESSAGE_COLUMN,
    )
    indexes = inspector.get_indexes(_TABLE)
    unique_signatures = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(_TABLE)
    }
    unique_signatures.update(
        tuple(item.get("column_names") or ()) for item in indexes if item.get("unique")
    )
    index_signatures = {tuple(item.get("column_names") or ()) for item in indexes}

    missing_session_column = _SESSION_COLUMN not in columns
    missing_message_column = _MESSAGE_COLUMN not in columns
    missing_constraint = target_unique_signature not in unique_signatures
    if missing_session_column or missing_message_column or missing_constraint:
        with op.batch_alter_table(_TABLE) as batch:
            if missing_session_column:
                batch.add_column(
                    sa.Column(_SESSION_COLUMN, sa.String(64), nullable=True)
                )
            if missing_message_column:
                batch.add_column(
                    sa.Column(_MESSAGE_COLUMN, sa.String(64), nullable=True)
                )
            if missing_constraint:
                batch.create_unique_constraint(
                    _UNIQUE_CONSTRAINT,
                    list(target_unique_signature),
                )

    if (_SESSION_COLUMN,) not in index_signatures:
        op.create_index(_SESSION_INDEX, _TABLE, [_SESSION_COLUMN], unique=False)


def downgrade() -> None:
    # Repair revisions do not own columns or indexes declared by revision 0082.
    pass
