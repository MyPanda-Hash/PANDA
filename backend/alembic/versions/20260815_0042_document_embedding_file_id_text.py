"""Store document embedding file ids as text.

Revision ID: 20260815_0042
Revises: 20260815_0041
"""

from alembic import context, op

revision = "20260815_0042"
down_revision = "20260815_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not context.is_offline_mode() and op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "ALTER TABLE document_embeddings ALTER COLUMN file_id "
        "TYPE VARCHAR(128) USING file_id::VARCHAR"
    )


def downgrade() -> None:
    # Stable knowledge ids are allowed to be textual. Casting back to BIGINT
    # can fail or discard valid identifiers, so rollback must preserve data.
    return
