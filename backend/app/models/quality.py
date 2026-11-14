from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class RagEvaluationRun(Base):
    """Aggregate metadata for a reproducible retrieval evaluation run."""

    __tablename__ = "rag_evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'passed', 'failed', 'incomplete')",
            name="ck_rag_evaluation_runs_status",
        ),
        Index("ix_rag_evaluation_runs_completed", "completed_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(128), index=True)
    code_revision: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
