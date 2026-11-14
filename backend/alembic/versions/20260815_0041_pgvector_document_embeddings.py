"""Persist local knowledge embeddings in PostgreSQL pgvector.

Revision ID: 20260815_0041
Revises: 20260814_0040
"""

from alembic import context, op

revision = "20260815_0041"
down_revision = "20260814_0040"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    if context.is_offline_mode():
        return True
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_embeddings (
            id VARCHAR(128) PRIMARY KEY,
            category VARCHAR(128) NOT NULL,
            file_id BIGINT NOT NULL,
            document TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_embeddings_category_file "
        "ON document_embeddings (category, file_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_embeddings_embedding_hnsw "
        "ON document_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    # Do not drop this table: CREATE TABLE IF NOT EXISTS may have reused an
    # older installation's data, and a rollback must never destroy embeddings.
    return
