"""Persist bounded PDF vision page counts and provider-call usage."""

from alembic import context, op
import sqlalchemy as sa


revision = "20260830_0068"
down_revision = "20260830_0067"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {str(item["name"]) for item in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _columns("document_versions")
    if context.is_offline_mode() or (existing and "vision_page_count" not in existing):
        op.add_column(
            "document_versions",
            sa.Column("vision_page_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if context.is_offline_mode() or (existing and "vision_usage_json" not in existing):
        op.add_column(
            "document_versions",
            sa.Column(
                "vision_usage_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    existing = _columns("document_versions")
    if context.is_offline_mode() or "vision_usage_json" in existing:
        op.drop_column("document_versions", "vision_usage_json")
    if context.is_offline_mode() or "vision_page_count" in existing:
        op.drop_column("document_versions", "vision_page_count")
