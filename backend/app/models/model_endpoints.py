from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class ModelEndpoint(Base):
    __tablename__ = "model_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "capability",
            "region",
            name="uq_model_endpoint_route",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(
        String(64), default="openai_compatible", index=True
    )
    api_protocol: Mapped[str] = mapped_column(
        String(32), default="chat_completions", index=True
    )
    model: Mapped[str] = mapped_column(String(160), index=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    endpoint_url: Mapped[str] = mapped_column(String(500))
    region: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    allowed_data_level: Mapped[str] = mapped_column(String(32), default="internal")
    max_context: Mapped[int] = mapped_column(Integer, default=128000)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=30000)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=8)
    supports_streaming_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_cache_pricing: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_models: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    api_version: Mapped[str] = mapped_column(String(64), default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    models: Mapped[list["ModelEndpointModel"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ModelEndpointModel.sort_order",
    )


class ModelEndpointModel(Base):
    __tablename__ = "model_endpoint_models"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "model",
            name="uq_model_endpoint_models_endpoint_model",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("model_endpoints.id", ondelete="CASCADE"),
        index=True,
    )
    model: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    max_context: Mapped[int] = mapped_column(Integer, default=128000)
    supports_image_input: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_cache_pricing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    endpoint: Mapped[ModelEndpoint] = relationship(back_populates="models")
