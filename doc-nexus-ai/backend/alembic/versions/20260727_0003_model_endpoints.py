"""Create model endpoint configuration table.

Revision ID: 20260727_0003
Revises: 20260727_0002
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_endpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("endpoint_url", sa.String(length=500), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column("secret_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("allowed_data_level", sa.String(length=32), nullable=False, server_default="internal"),
        sa.Column("max_context", sa.Integer(), nullable=False, server_default="128000"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="30000"),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("supports_streaming_usage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_cache_pricing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "model", "capability", "region", name="uq_model_endpoint_route"),
    )
    op.create_index("ix_model_endpoints_provider", "model_endpoints", ["provider"])
    op.create_index("ix_model_endpoints_model", "model_endpoints", ["model"])
    op.create_index("ix_model_endpoints_capability", "model_endpoints", ["capability"])
    op.create_index("ix_model_endpoints_status", "model_endpoints", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_endpoints_status", table_name="model_endpoints")
    op.drop_index("ix_model_endpoints_capability", table_name="model_endpoints")
    op.drop_index("ix_model_endpoints_model", table_name="model_endpoints")
    op.drop_index("ix_model_endpoints_provider", table_name="model_endpoints")
    op.drop_table("model_endpoints")
