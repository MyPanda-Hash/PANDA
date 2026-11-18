"""Allow nullable space_id for temporary chat attachments.

Revision ID: 20260813_0038
Revises: 20260813_0037

Some environments already applied 20260813_0037 before space_id was relaxed.
This follow-up migration is idempotent and safe to re-run.
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260813_0038"
down_revision = "20260813_0037"
branch_labels = None
depends_on = None


def _column_nullable(table_name: str, column_name: str) -> bool | None:
    if context.is_offline_mode():
        return None
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    for item in inspector.get_columns(table_name):
        if item["name"] == column_name:
            return bool(item.get("nullable"))
    return None


def upgrade() -> None:
    docs_nullable = _column_nullable("documents", "space_id")
    if docs_nullable is False:
        with op.batch_alter_table("documents") as batch:
            batch.alter_column(
                "space_id", existing_type=sa.String(length=64), nullable=True
            )

    jobs_nullable = _column_nullable("document_jobs", "space_id")
    if jobs_nullable is False:
        with op.batch_alter_table("document_jobs") as batch:
            batch.alter_column(
                "space_id", existing_type=sa.String(length=64), nullable=True
            )


def downgrade() -> None:
    bind = op.get_bind()
    if context.is_offline_mode():
        return
    orphan_docs = bind.execute(
        sa.text("SELECT COUNT(*) FROM documents WHERE space_id IS NULL")
    ).scalar()
    orphan_jobs = bind.execute(
        sa.text("SELECT COUNT(*) FROM document_jobs WHERE space_id IS NULL")
    ).scalar()
    if not orphan_docs and _column_nullable("documents", "space_id") is True:
        with op.batch_alter_table("documents") as batch:
            batch.alter_column(
                "space_id", existing_type=sa.String(length=64), nullable=False
            )
    if not orphan_jobs and _column_nullable("document_jobs", "space_id") is True:
        with op.batch_alter_table("document_jobs") as batch:
            batch.alter_column(
                "space_id", existing_type=sa.String(length=64), nullable=False
            )
