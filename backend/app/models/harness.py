"""Harness persistence models — SQLite/PG dual-compatible.

These tables persist Harness state across process restarts:
- ``harness_tasks``: task lifecycle + workspace binding
- ``harness_audit_events``: fine-grained audit trail (tool calls, policy, verification)
- ``harness_approvals``: pending/resolved approval requests
- ``harness_policy_rules``: user-defined policy rules (survive restart)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class HarnessTask(Base):
    """Persisted task record — survives process restarts."""

    __tablename__ = "harness_tasks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    project_id: Mapped[str] = mapped_column(String(128), default="default")
    objective: Mapped[str] = mapped_column(Text, default="")

    # Permissions (stored as JSON string for SQLite compat)
    permissions_json: Mapped[str] = mapped_column(
        Text, default='{"file_read":true,"file_write":true,"shell":true}'
    )
    # Limits
    max_steps: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=500000)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    max_cost: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Verification command profiles (IDs resolve to fixed argv server-side)
    verification_build_cmd: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_test_cmd: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_lint_cmd: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_build_command_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_test_command_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_lint_command_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_custom_command_ids: Mapped[str] = mapped_column(
        Text, default="[]"
    )
    verification_max_retries: Mapped[int] = mapped_column(Integer, default=3)

    # Workspace
    workspace_path: Mapped[str] = mapped_column(Text, default="")
    repository: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(32), index=True, default="created"
    )  # created | running | verifying | completed | failed

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HarnessAuditEvent(Base):
    """Fine-grained audit trail for Harness operations.

    Unified with the existing ``audit_events`` table concept, but with
    richer detail for tool calls, policy decisions, and verifications.
    """

    __tablename__ = "harness_audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(128), index=True, default=""
    )
    event_type: Mapped[str] = mapped_column(
        String(64), index=True
    )  # task_start, tool_call, policy_decision, verification, approval_*
    actor_id: Mapped[str] = mapped_column(String(64), index=True, default="")

    # Structured details as JSON string (SQLite + PG compatible)
    details_json: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )


class HarnessApproval(Base):
    """Pending or resolved approval requests."""

    __tablename__ = "harness_approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    rule_id: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(
        String(16), index=True, default="pending"
    )  # pending | approved | rejected | expired
    requester_id: Mapped[str] = mapped_column(String(64), default="")
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    ttl_seconds: Mapped[float] = mapped_column(Float, default=600.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HarnessPolicyRule(Base):
    """User-defined policy rules that survive restarts."""

    __tablename__ = "harness_policy_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_pattern: Mapped[str] = mapped_column(String(128))
    arg_pattern: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(
        String(16)
    )  # ALLOW | DENY | ASK | REWRITE
    reason: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
