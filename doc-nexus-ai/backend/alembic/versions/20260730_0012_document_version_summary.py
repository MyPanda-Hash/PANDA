"""Add document_versions.summary for document processing brief.

Revision ID: 20260730_0012
Revises: 20260730_0011
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "20260730_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_versions", "summary")
