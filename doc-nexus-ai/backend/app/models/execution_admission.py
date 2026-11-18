from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class PiExecutionAdmissionGuard(Base):
    """Single write-locked row serializing cross-process admission decisions."""

    __tablename__ = "pi_execution_admission_guard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PiExecutionAdmissionLease(Base):
    """One durable queued or active Pi execution ticket."""

    __tablename__ = "pi_execution_admission_leases"
    __table_args__ = (
        Index(
            "ix_pi_execution_admission_status_queued",
            "status",
            "queued_at",
        ),
        Index(
            "ix_pi_execution_admission_status_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_pi_execution_admission_user_status",
            "user_key",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_key: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wait_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
