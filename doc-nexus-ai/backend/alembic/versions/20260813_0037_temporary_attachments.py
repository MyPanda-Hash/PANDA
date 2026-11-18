"""Track temporary chat attachments and their cleanup lifecycle.

Revision ID: 20260813_0037
Revises: 20260812_0036
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260813_0037"
down_revision = "20260812_0036"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("documents")
    with op.batch_alter_table("documents") as batch:
        if "is_temporary" not in columns:
            batch.add_column(
                sa.Column(
                    "is_temporary",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "expires_at" not in columns:
            batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True)))
        if "temporary_cleanup_at" not in columns:
            batch.add_column(
                sa.Column("temporary_cleanup_at", sa.DateTime(timezone=True))
            )
        # Temporary chat attachments are intentionally space-less.
        if "space_id" in columns:
            batch.alter_column("space_id", existing_type=sa.String(length=64), nullable=True)

    job_columns = _column_names("document_jobs")
    if "space_id" in job_columns:
        with op.batch_alter_table("document_jobs") as batch:
            batch.alter_column("space_id", existing_type=sa.String(length=64), nullable=True)

    if context.is_offline_mode():
        return

    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("documents")}
    if "ix_documents_temporary_expires_at" not in indexes:
        op.create_index(
            "ix_documents_temporary_expires_at",
            "documents",
            ["is_temporary", "expires_at", "temporary_cleanup_at"],
        )


def downgrade() -> None:
    if not context.is_offline_mode():
        inspector = sa.inspect(op.get_bind())
        indexes = {item["name"] for item in inspector.get_indexes("documents")}
        if "ix_documents_temporary_expires_at" in indexes:
            op.drop_index("ix_documents_temporary_expires_at", table_name="documents")

    # Only tighten space_id when no temporary/orphan rows remain.
    bind = op.get_bind()
    if not context.is_offline_mode():
        orphan_docs = bind.execute(
            sa.text("SELECT COUNT(*) FROM documents WHERE space_id IS NULL")
        ).scalar()
        orphan_jobs = bind.execute(
            sa.text("SELECT COUNT(*) FROM document_jobs WHERE space_id IS NULL")
        ).scalar()
        if not orphan_docs:
            with op.batch_alter_table("documents") as batch:
                batch.alter_column(
                    "space_id", existing_type=sa.String(length=64), nullable=False
                )
        if not orphan_jobs:
            with op.batch_alter_table("document_jobs") as batch:
                batch.alter_column(
                    "space_id", existing_type=sa.String(length=64), nullable=False
                )

    columns = _column_names("documents")
    with op.batch_alter_table("documents") as batch:
        if "temporary_cleanup_at" in columns:
            batch.drop_column("temporary_cleanup_at")
        if "expires_at" in columns:
            batch.drop_column("expires_at")
        if "is_temporary" in columns:
            batch.drop_column("is_temporary")
