"""Add TokenLink login state and encrypted local LinkMind User Key cache.
Revision ID: 20260824_0052
Revises: 20260824_0051
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0052"
down_revision = "20260824_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    user_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "linkmind_login_status" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "linkmind_login_status",
                sa.String(length=16),
                nullable=False,
                server_default="logged_out",
            ),
        )
    op.create_table(
        "linkmind_client_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=128), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_linkmind_client_api_keys_user_id",
        "linkmind_client_api_keys",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_linkmind_client_api_keys_user_id",
        table_name="linkmind_client_api_keys",
    )
    op.drop_table("linkmind_client_api_keys")
    op.drop_column("users", "linkmind_login_status")
