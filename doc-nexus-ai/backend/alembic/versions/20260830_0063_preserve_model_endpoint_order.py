"""Preserve the configured chat endpoint when adding picker ordering.

Revision ID: 20260830_0063
Revises: 20260829_0062
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260830_0063"
down_revision = "20260829_0062"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_model_endpoints_sort_order"


def upgrade() -> None:
    if context.is_offline_mode():
        op.create_index(_INDEX_NAME, "model_endpoints", ["sort_order"])
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "model_endpoints" not in inspector.get_table_names():
        return
    if _INDEX_NAME not in {item["name"] for item in inspector.get_indexes("model_endpoints")}:
        op.create_index(_INDEX_NAME, "model_endpoints", ["sort_order"])
    if "system_config" not in inspector.get_table_names():
        return

    rows = list(
        bind.execute(
            sa.text(
                "select id, capability from model_endpoints "
                "order by provider, model, capability, region, id"
            )
        ).mappings()
    )
    if not rows or bind.scalar(
        sa.text("select 1 from model_endpoints where sort_order <> 0 limit 1")
    ):
        return
    configured_id = bind.scalar(
        sa.text("select value from system_config where key = 'pi_chat_model'")
    )
    if not configured_id or not any(
        row["id"] == configured_id and row["capability"] == "chat" for row in rows
    ):
        return

    ordered_ids = [configured_id, *[row["id"] for row in rows if row["id"] != configured_id]]
    for position, endpoint_id in enumerate(ordered_ids):
        bind.execute(
            sa.text("update model_endpoints set sort_order = :sort_order where id = :id"),
            {"id": endpoint_id, "sort_order": position * 10},
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index(_INDEX_NAME, table_name="model_endpoints")
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "model_endpoints" in inspector.get_table_names() and _INDEX_NAME in {
        item["name"] for item in inspector.get_indexes("model_endpoints")
    }:
        op.drop_index(_INDEX_NAME, table_name="model_endpoints")
