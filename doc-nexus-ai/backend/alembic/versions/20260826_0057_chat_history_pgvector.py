"""Add the PostgreSQL vector column and HNSW index for private history."""

from alembic import context, op
import sqlalchemy as sa

from app.core.config import settings


revision = "20260826_0057"
down_revision = "20260826_0056"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return not context.is_offline_mode() and op.get_bind().dialect.name == "postgresql"


def _dimensions() -> int:
    value = settings.embedding_dimensions
    if not isinstance(value, int) or not 1 <= value <= 2000:
        raise RuntimeError("DOC_NEXUS_EMBEDDING_DIMENSIONS must be between 1 and 2000")
    return value


def upgrade() -> None:
    if not _is_postgresql():
        return
    bind = op.get_bind()
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("chat_history_embeddings")
    }
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if "embedding" not in columns:
        op.execute(
            "ALTER TABLE chat_history_embeddings "
            "ADD COLUMN embedding vector"
        )
    dimensions = _dimensions()
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_history_embeddings_hnsw "
        "ON chat_history_embeddings USING hnsw "
        f"((embedding::vector({dimensions})) vector_cosine_ops) "
        f"WHERE embedding IS NOT NULL AND vector_dims(embedding) = {dimensions}"
    )


def downgrade() -> None:
    # Older vectors may be needed for recovery; do not drop the shared extension
    # or private embeddings as part of a downgrade.
    return
