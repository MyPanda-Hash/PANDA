"""Store user API keys as plaintext values for self-service display.

Revision ID: 20260824_0051
Revises: 20260824_0050
"""

import hashlib
import secrets

from alembic import op
import sqlalchemy as sa


revision = "20260824_0051"
down_revision = "20260824_0050"
branch_labels = None
depends_on = None


def _new_api_key() -> str:
    return f"dnx_{secrets.token_urlsafe(32)}"


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "api_key" not in columns:
        op.add_column(
            "users",
            sa.Column("api_key", sa.String(length=128), nullable=True),
        )

    missing = bind.execute(
        sa.text("SELECT id FROM users WHERE api_key IS NULL OR api_key = ''")
    ).fetchall()
    for (user_id,) in missing:
        bind.execute(
            sa.text("UPDATE users SET api_key = :api_key WHERE id = :user_id"),
            {"api_key": _new_api_key(), "user_id": user_id},
        )

    if "api_key_hash" in columns:
        indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("users")
        }
        if "ix_users_api_key_hash" in indexes:
            op.drop_index("ix_users_api_key_hash", table_name="users")
        op.drop_column("users", "api_key_hash")

    op.create_index("ix_users_api_key", "users", ["api_key"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "api_key" not in columns:
        return
    if "api_key_hash" not in columns:
        op.add_column(
            "users",
            sa.Column("api_key_hash", sa.String(length=64), nullable=True),
        )

    rows = bind.execute(
        sa.text("SELECT id, api_key FROM users WHERE api_key IS NOT NULL")
    ).fetchall()
    for user_id, api_key in rows:
        bind.execute(
            sa.text("UPDATE users SET api_key_hash = :api_key_hash WHERE id = :user_id"),
            {
                "api_key_hash": hashlib.sha256(api_key.encode()).hexdigest(),
                "user_id": user_id,
            },
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("users")}
    if "ix_users_api_key" in indexes:
        op.drop_index("ix_users_api_key", table_name="users")
    op.drop_column("users", "api_key")
    op.create_index("ix_users_api_key_hash", "users", ["api_key_hash"], unique=True)
