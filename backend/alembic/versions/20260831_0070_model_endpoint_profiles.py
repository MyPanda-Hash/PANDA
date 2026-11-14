"""Normalize concrete models and their capabilities under model endpoints.

Revision ID: 20260831_0070
Revises: 20260830_0069
Create Date: 2026-08-31
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from alembic import context, op
import sqlalchemy as sa


revision = "20260831_0070"
down_revision = "20260830_0069"
branch_labels = None
depends_on = None


profiles_table = sa.table(
    "model_endpoint_models",
    sa.column("id", sa.String(length=64)),
    sa.column("endpoint_id", sa.String(length=64)),
    sa.column("model", sa.String(length=160)),
    sa.column("status", sa.String(length=32)),
    sa.column("is_default", sa.Boolean()),
    sa.column("sort_order", sa.Integer()),
    sa.column("max_context", sa.Integer()),
    sa.column("supports_image_input", sa.Boolean()),
    sa.column("supports_structured_output", sa.Boolean()),
    sa.column("supports_streaming_usage", sa.Boolean()),
    sa.column("supports_cache_pricing", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _model_names(primary: object, extras: object) -> list[str]:
    names: list[str] = []
    for value in [primary, *str(extras or "").split(",")]:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _timestamp(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _profile_id(endpoint_id: str, model: str) -> str:
    digest = hashlib.sha256(f"{endpoint_id}\0{model}".encode("utf-8")).hexdigest()[:32]
    return f"mepm_{digest}"


def upgrade() -> None:
    op.create_table(
        "model_endpoint_models",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("endpoint_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_context", sa.Integer(), nullable=False, server_default="128000"),
        sa.Column(
            "supports_image_input",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "supports_structured_output",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "supports_streaming_usage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "supports_cache_pricing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["endpoint_id"], ["model_endpoints.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "endpoint_id",
            "model",
            name="uq_model_endpoint_models_endpoint_model",
        ),
    )
    op.create_index(
        "ix_model_endpoint_models_endpoint_id",
        "model_endpoint_models",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_endpoint_models_model",
        "model_endpoint_models",
        ["model"],
        unique=False,
    )
    op.create_index(
        "ix_model_endpoint_models_status",
        "model_endpoint_models",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_model_endpoint_models_is_default",
        "model_endpoint_models",
        ["is_default"],
        unique=False,
    )

    if context.is_offline_mode():
        return

    bind = op.get_bind()
    endpoints = bind.execute(
        sa.text(
            """
            SELECT id, model, extra_models, max_context,
                   supports_vision, supports_structured_output,
                   supports_streaming_usage, supports_cache_pricing,
                   created_at, updated_at
            FROM model_endpoints
            """
        )
    ).mappings()
    rows: list[dict[str, object]] = []
    fallback_timestamp = datetime.now(timezone.utc)
    for endpoint in endpoints:
        endpoint_id = str(endpoint["id"])
        names = _model_names(endpoint["model"], endpoint["extra_models"])
        for index, model in enumerate(names):
            rows.append(
                {
                    "id": _profile_id(endpoint_id, model),
                    "endpoint_id": endpoint_id,
                    "model": model,
                    "status": "active",
                    "is_default": index == 0,
                    "sort_order": index * 10,
                    "max_context": int(endpoint["max_context"] or 128000),
                    "supports_image_input": bool(endpoint["supports_vision"])
                    if index == 0
                    else False,
                    "supports_structured_output": bool(
                        endpoint["supports_structured_output"]
                    ),
                    "supports_streaming_usage": bool(
                        endpoint["supports_streaming_usage"]
                    ),
                    "supports_cache_pricing": bool(
                        endpoint["supports_cache_pricing"]
                    ),
                    "created_at": _timestamp(
                        endpoint["created_at"], fallback_timestamp
                    ),
                    "updated_at": _timestamp(
                        endpoint["updated_at"], fallback_timestamp
                    ),
                }
            )
    if rows:
        op.bulk_insert(profiles_table, rows)


def downgrade() -> None:
    op.drop_index(
        "ix_model_endpoint_models_is_default", table_name="model_endpoint_models"
    )
    op.drop_index(
        "ix_model_endpoint_models_status", table_name="model_endpoint_models"
    )
    op.drop_index(
        "ix_model_endpoint_models_model", table_name="model_endpoint_models"
    )
    op.drop_index(
        "ix_model_endpoint_models_endpoint_id", table_name="model_endpoint_models"
    )
    op.drop_table("model_endpoint_models")
