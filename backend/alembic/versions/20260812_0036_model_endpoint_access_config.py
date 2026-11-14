"""Add model endpoint adapter and API protocol metadata.

Revision ID: 20260812_0036
Revises: 20260812_0035
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260812_0036"
down_revision = "20260812_0035"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("model_endpoints")
    had_adapter = "adapter" in columns
    had_protocol = "api_protocol" in columns
    with op.batch_alter_table("model_endpoints") as batch:
        if not had_adapter:
            batch.add_column(
                sa.Column(
                    "adapter",
                    sa.String(64),
                    nullable=False,
                    server_default="openai_compatible",
                )
            )
        if not had_protocol:
            batch.add_column(
                sa.Column(
                    "api_protocol",
                    sa.String(32),
                    nullable=False,
                    server_default="chat_completions",
                )
            )

    if context.is_offline_mode() or (had_adapter and had_protocol):
        return

    bind = op.get_bind()
    if not had_adapter:
        bind.execute(
            sa.text(
                """
                UPDATE model_endpoints
                SET adapter = CASE
                    WHEN replace(lower(provider), '-', '_') IN ('openai', 'open_ai')
                        THEN 'openai'
                    WHEN replace(lower(provider), '-', '_') LIKE '%azure%'
                        THEN 'azure_openai'
                    ELSE 'openai_compatible'
                END
                """
            )
        )
    if not had_protocol:
        bind.execute(
            sa.text(
                """
                UPDATE model_endpoints
                SET api_protocol = CASE
                    WHEN adapter IN ('openai', 'azure_openai')
                        OR replace(lower(provider), '-', '_') IN (
                            'openai_responses', 'openai_compatible_responses'
                        )
                        THEN 'responses'
                    ELSE 'chat_completions'
                END
                """
            )
        )


def downgrade() -> None:
    columns = _column_names("model_endpoints")
    with op.batch_alter_table("model_endpoints") as batch:
        if "api_protocol" in columns:
            batch.drop_column("api_protocol")
        if "adapter" in columns:
            batch.drop_column("adapter")
