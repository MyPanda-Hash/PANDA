"""Add selected_document_ids to writing projects.

Revision ID: 20260806_0030
Revises: 20260806_0029
"""

import sqlalchemy as sa
from alembic import op

revision = "20260806_0030"
down_revision = "20260806_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "writing_projects",
        sa.Column(
            "selected_document_ids",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("writing_projects", "selected_document_ids")
