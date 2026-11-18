"""Create immutable model price versions and budget rules.

Revision ID: 20260803_0027
Revises: 20260802_0026
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260803_0027"
down_revision = "20260802_0026"
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
    columns = _column_names("model_prices")
    with op.batch_alter_table("model_prices") as batch:
        if "cache_read_per_million" not in columns:
            batch.add_column(sa.Column("cache_read_per_million", sa.String(32), nullable=True))
        if "cache_write_per_million" not in columns:
            batch.add_column(sa.Column("cache_write_per_million", sa.String(32), nullable=True))
    # Existing rows: default cache rates to input rate so historical pricing stays conservative.
    op.execute(
        sa.text(
            "UPDATE model_prices SET cache_read_per_million = input_per_million "
            "WHERE cache_read_per_million IS NULL OR cache_read_per_million = ''"
        )
    )
    op.execute(
        sa.text(
            "UPDATE model_prices SET cache_write_per_million = input_per_million "
            "WHERE cache_write_per_million IS NULL OR cache_write_per_million = ''"
        )
    )


def downgrade() -> None:
    columns = _column_names("model_prices")
    with op.batch_alter_table("model_prices") as batch:
        if "cache_write_per_million" in columns:
            batch.drop_column("cache_write_per_million")
        if "cache_read_per_million" in columns:
            batch.drop_column("cache_read_per_million")
