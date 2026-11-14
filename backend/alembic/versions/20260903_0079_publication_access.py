"""Add publication scope metadata and writing citation control.

Revision ID: 20260903_0079
Revises: 20260903_0078
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_0079"
down_revision = "20260903_0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_session_publications") as batch:
        batch.add_column(
            sa.Column(
                "space_ids_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
    with op.batch_alter_table("writing_project_publications") as batch:
        batch.add_column(
            sa.Column(
                "space_ids_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.add_column(
            sa.Column(
                "allow_cite",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("writing_project_publications") as batch:
        batch.drop_column("allow_cite")
        batch.drop_column("space_ids_json")
    with op.batch_alter_table("chat_session_publications") as batch:
        batch.drop_column("space_ids_json")
