"""Repair schema objects missing from historically stamped databases.

Revision ID: 20260731_0013r
Revises: 20260731_0012
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260731_0013r"
down_revision = "20260731_0019"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def upgrade() -> None:
    operation_columns = _column_names("writing_operations")
    if "result" not in operation_columns:
        with op.batch_alter_table("writing_operations") as batch:
            batch.add_column(
                sa.Column(
                    "result",
                    sa.Text(),
                    nullable=False,
                    server_default="{}",
                )
            )

    version_columns = _column_names("document_versions")
    with op.batch_alter_table("document_versions") as batch:
        if "file_path" not in version_columns:
            batch.add_column(sa.Column("file_path", sa.Text(), nullable=True))
        if "ext_task_id" not in version_columns:
            batch.add_column(
                sa.Column("ext_task_id", sa.String(128), nullable=True)
            )
        if "chunk_count" not in version_columns:
            batch.add_column(
                sa.Column(
                    "chunk_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )

    if "system_config" not in _table_names():
        op.create_table(
            "system_config",
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "description",
                sa.String(256),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
        )


def downgrade() -> None:
    # Repair revisions do not own objects created by historical migrations.
    # Removing them here would destroy unrelated schema and data.
    pass
