"""Create SQLite document_embeddings for sqlite-vec cosine search.

Revision ID: 20260820_0044
Revises: 20260815_0043
"""

from alembic import context, op

revision = "20260820_0044"
down_revision = "20260815_0043"
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    if context.is_offline_mode():
        return False
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if not _is_sqlite():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_embeddings (
            id VARCHAR(128) PRIMARY KEY,
            category VARCHAR(128) NOT NULL,
            file_id VARCHAR(128) NOT NULL,
            document TEXT NOT NULL,
            embedding BLOB NOT NULL,
            metadata JSON NOT NULL DEFAULT '{}',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_embeddings_category_file "
        "ON document_embeddings (category, file_id)"
    )


def downgrade() -> None:
    return
