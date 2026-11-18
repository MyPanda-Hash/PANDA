from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ModelPrice(Base):
    __tablename__ = "model_prices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    model: Mapped[str] = mapped_column(String(255), index=True)
    currency: Mapped[str] = mapped_column(String(8))
    input_per_million: Mapped[str] = mapped_column(String(32))
    output_per_million: Mapped[str] = mapped_column(String(32))
    cache_read_per_million: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cache_write_per_million: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetRule(Base):
    __tablename__ = "budget_rules"
    __table_args__ = (Index("uq_budget_rules_scope", "scope", unique=True),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32))
    cost_limit: Mapped[str] = mapped_column(String(32))
    soft_threshold: Mapped[float] = mapped_column()
    hard_stop: Mapped[bool] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        Index(
            "ix_budget_reservations_status_hold_until",
            "status",
            "hold_until",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    space_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    estimated_cost: Mapped[str] = mapped_column(String(32))
    held_cost: Mapped[str] = mapped_column(String(32))
    actual_cost: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hold_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
