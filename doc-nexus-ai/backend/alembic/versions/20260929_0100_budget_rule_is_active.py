"""Budget rule enable/disable flag and one-rule-per-scope uniqueness."""

import sqlalchemy as sa

from alembic import op

revision = "20260929_0100"
down_revision = "20260925_0099"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("budget_rules", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    # Collapse historical duplicate rules per scope: keep the most recently
    # updated row so admins keep whatever they last touched.
    op.execute(
        """
        DELETE FROM budget_rules
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY scope ORDER BY updated_at DESC, id
                ) AS rn
                FROM budget_rules
            ) ranked
            WHERE rn = 1
        )
        """
    )
    op.drop_index("ix_budget_rules_scope", table_name="budget_rules")
    op.create_index("uq_budget_rules_scope", "budget_rules", ["scope"], unique=True)

def downgrade() -> None:
    op.drop_index("uq_budget_rules_scope", table_name="budget_rules")
    op.create_index("ix_budget_rules_scope", "budget_rules", ["scope"])
    with op.batch_alter_table("budget_rules") as batch:
        batch.drop_column("is_active")
