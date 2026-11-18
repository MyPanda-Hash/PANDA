"""Store structured presentation manifests with export artifacts.

Revision ID: 20260827_0061
Revises: 20260827_0060
"""

from alembic import op
import sqlalchemy as sa


revision = "20260827_0061"
down_revision = "20260827_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(
            sa.Column(
                "manifest_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("manifest_json")
