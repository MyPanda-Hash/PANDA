"""Persist shared retrieval traces and aggregate RAG evaluation runs.

Revision ID: 20260830_0065
Revises: 20260830_0064
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260830_0065"
down_revision = "20260830_0064"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector | None:
    if context.is_offline_mode():
        return None
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    inspector = _inspector()
    return set() if inspector is None else set(inspector.get_table_names())


def _columns(table: str) -> set[str]:
    inspector = _inspector()
    if inspector is None or table not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("chat_messages")
    if context.is_offline_mode():
        op.add_column(
            "chat_messages", sa.Column("retrieval_trace_json", sa.Text(), nullable=True)
        )
    elif columns and "retrieval_trace_json" not in columns:
        op.add_column(
            "chat_messages", sa.Column("retrieval_trace_json", sa.Text(), nullable=True)
        )

    if "rag_evaluation_runs" not in _tables():
        op.create_table(
            "rag_evaluation_runs",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("dataset_version", sa.String(length=128), nullable=False),
            sa.Column("code_revision", sa.String(length=128), nullable=False),
            sa.Column("mode", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status in ('running', 'passed', 'failed', 'incomplete')",
                name="ck_rag_evaluation_runs_status",
            ),
        )
        op.create_index(
            "ix_rag_evaluation_runs_dataset_version",
            "rag_evaluation_runs",
            ["dataset_version"],
        )
        op.create_index(
            "ix_rag_evaluation_runs_status",
            "rag_evaluation_runs",
            ["status"],
        )
        op.create_index(
            "ix_rag_evaluation_runs_completed",
            "rag_evaluation_runs",
            ["completed_at"],
        )


def downgrade() -> None:
    tables = _tables()
    if "rag_evaluation_runs" in tables:
        for name in (
            "ix_rag_evaluation_runs_completed",
            "ix_rag_evaluation_runs_status",
            "ix_rag_evaluation_runs_dataset_version",
        ):
            op.drop_index(name, table_name="rag_evaluation_runs")
        op.drop_table("rag_evaluation_runs")
    if "retrieval_trace_json" in _columns("chat_messages"):
        op.drop_column("chat_messages", "retrieval_trace_json")
