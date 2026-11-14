"""Create immutable model price versions and budget rules.

Revision ID: 20260727_0008
Revises: 20260727_0007
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0008"
down_revision = "20260727_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("model_prices", sa.Column("id", sa.String(64), nullable=False), sa.Column("provider", sa.String(120), nullable=False), sa.Column("model", sa.String(255), nullable=False), sa.Column("currency", sa.String(8), nullable=False), sa.Column("input_per_million", sa.String(32), nullable=False), sa.Column("output_per_million", sa.String(32), nullable=False), sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False), sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_model_prices_provider", "model_prices", ["provider"]); op.create_index("ix_model_prices_model", "model_prices", ["model"]); op.create_index("ix_model_prices_effective_at", "model_prices", ["effective_at"]); op.create_index("ix_model_prices_superseded_at", "model_prices", ["superseded_at"])
    op.create_table("budget_rules", sa.Column("id", sa.String(64), nullable=False), sa.Column("scope", sa.String(32), nullable=False), sa.Column("cost_limit", sa.String(32), nullable=False), sa.Column("soft_threshold", sa.Float(), nullable=False), sa.Column("hard_stop", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_budget_rules_scope", "budget_rules", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_budget_rules_scope", table_name="budget_rules"); op.drop_table("budget_rules")
    op.drop_index("ix_model_prices_superseded_at", table_name="model_prices"); op.drop_index("ix_model_prices_effective_at", table_name="model_prices"); op.drop_index("ix_model_prices_model", table_name="model_prices"); op.drop_index("ix_model_prices_provider", table_name="model_prices"); op.drop_table("model_prices")
