"""Harness repository — DB-backed CRUD for all Harness entities.

All functions accept an optional ``db`` parameter.  When omitted, the
caller wraps the call in ``transaction()``.  This matches the existing
pattern in ``repositories/audit.py`` and ``repositories/usage.py``.

SQLite + PostgreSQL dual-compatible via SQLAlchemy 2 Core / ORM.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.security import as_utc, new_token, now
from app.models.harness import (
    HarnessApproval,
    HarnessAuditEvent,
    HarnessPolicyRule,
    HarnessTask,
)
from app.services.harness.policy import DecisionKind, PolicyRule
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskLimits,
    TaskPermissions,
    TaskVerification,
)


def _now() -> datetime:
    # Naive UTC — SQLite round-trips naive datetimes faithfully,
    # but timezone-aware ones lose tzinfo and break timestamp math.
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


def create_task(
    db: Session, task: AgentTask, *, workspace_path: str = ""
) -> HarnessTask:
    """Persist a new Harness task."""
    now = _now()
    row = HarnessTask(
        id=task.task_id,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        project_id=task.project.project_id,
        objective=task.objective,
        permissions_json=json.dumps(task.permissions.as_dict(), ensure_ascii=False),
        max_steps=task.limits.max_steps,
        max_tokens=task.limits.max_tokens,
        timeout_seconds=task.limits.timeout_seconds,
        max_cost=str(task.limits.max_cost) if task.limits.max_cost else None,
        verification_build_cmd=task.verification.build_cmd,
        verification_test_cmd=task.verification.test_cmd,
        verification_lint_cmd=task.verification.lint_cmd,
        verification_build_command_id=task.verification.build_command_id,
        verification_test_command_id=task.verification.test_command_id,
        verification_lint_command_id=task.verification.lint_command_id,
        verification_custom_command_ids=json.dumps(
            task.verification.custom_command_ids, ensure_ascii=False
        ),
        verification_max_retries=task.verification.max_retries,
        workspace_path=workspace_path or str(task.workspace),
        repository=task.project.repository,
        branch=task.project.branch,
        status="created",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def get_task(db: Session, task_id: str) -> HarnessTask | None:
    return db.get(HarnessTask, task_id)


def update_task_workspace(db: Session, task_id: str, workspace_path: str) -> bool:
    """Persist the canonical workspace path for an existing task."""
    row = db.get(HarnessTask, task_id)
    if row is None:
        return False
    row.workspace_path = workspace_path
    row.updated_at = _now()
    db.flush()
    return True


def ensure_task_permission(
    db: Session, task_id: str, permission: str, *, enabled: bool = True
) -> bool:
    """Add a permission to an existing task without changing other switches."""
    row = db.get(HarnessTask, task_id)
    if row is None:
        return False
    try:
        permissions = json.loads(row.permissions_json or "{}")
    except json.JSONDecodeError:
        permissions = {}
    if permissions.get(permission) is enabled:
        return False
    permissions[permission] = enabled
    row.permissions_json = json.dumps(permissions, ensure_ascii=False)
    row.updated_at = _now()
    db.flush()
    return True


def list_tasks(
    db: Session, *, status: str | None = None, limit: int = 100
) -> list[HarnessTask]:
    stmt = select(HarnessTask).order_by(HarnessTask.created_at.desc())
    if status:
        stmt = stmt.where(HarnessTask.status == status)
    stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def update_task_status(
    db: Session, task_id: str, status: str, *, completed: bool = False
) -> None:
    now = _now()
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if completed:
        values["completed_at"] = now
    db.execute(update(HarnessTask).where(HarnessTask.id == task_id).values(**values))
    db.flush()


def delete_task(db: Session, task_id: str) -> bool:
    row = db.get(HarnessTask, task_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def row_to_agent_task(row: HarnessTask) -> AgentTask:
    """Convert a DB row back to an AgentTask domain object."""
    perms_dict = json.loads(row.permissions_json) if row.permissions_json else {}
    perms_dict["network_allowlist"] = tuple(perms_dict.get("network_allowlist", ()))
    return AgentTask(
        task_id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        project=ProjectContext(
            project_id=row.project_id,
            workspace=row.workspace_path,
            repository=row.repository,
            branch=row.branch,
        ),
        objective=row.objective,
        permissions=TaskPermissions(**perms_dict),
        limits=TaskLimits(
            max_steps=row.max_steps,
            max_tokens=row.max_tokens,
            timeout_seconds=row.timeout_seconds,
            max_cost=Decimal(row.max_cost) if row.max_cost else None,
        ),
        verification=TaskVerification(
            build_cmd=row.verification_build_cmd,
            test_cmd=row.verification_test_cmd,
            lint_cmd=row.verification_lint_cmd,
            build_command_id=row.verification_build_command_id,
            test_command_id=row.verification_test_command_id,
            lint_command_id=row.verification_lint_command_id,
            custom_command_ids=(
                json.loads(row.verification_custom_command_ids)
                if row.verification_custom_command_ids
                else []
            ),
            max_retries=row.verification_max_retries,
        ),
    )


# ---------------------------------------------------------------------------
# Audit CRUD
# ---------------------------------------------------------------------------


def record_audit(
    db: Session,
    event_type: str,
    task_id: str,
    *,
    actor_id: str = "",
    details: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> str:
    event_id = event_id or f"haud_{new_token(10)}"
    if db.get(HarnessAuditEvent, event_id) is not None:
        return event_id
    row = HarnessAuditEvent(
        id=event_id,
        task_id=task_id,
        event_type=event_type,
        actor_id=actor_id,
        details_json=json.dumps(details or {}, ensure_ascii=False, default=str),
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return event_id


def list_audit_events(
    db: Session,
    *,
    task_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = select(HarnessAuditEvent).order_by(HarnessAuditEvent.created_at.desc())
    if task_id:
        stmt = stmt.where(HarnessAuditEvent.task_id == task_id)
    if event_type:
        stmt = stmt.where(HarnessAuditEvent.event_type == event_type)
    stmt = stmt.limit(limit)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "event_type": r.event_type,
            "actor_id": r.actor_id,
            "details": json.loads(r.details_json) if r.details_json else {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def count_audit_events(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(HarnessAuditEvent)) or 0


# ---------------------------------------------------------------------------
# Approval CRUD
# ---------------------------------------------------------------------------


def create_approval(
    db: Session,
    request_id: str,
    task_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
    *,
    rule_id: str = "",
    requester_id: str = "",
    ttl_seconds: float = 600.0,
) -> HarnessApproval:
    row = HarnessApproval(
        id=request_id,
        task_id=task_id,
        tool_name=tool_name,
        arguments_json=json.dumps(arguments, ensure_ascii=False, default=str),
        reason=reason,
        rule_id=rule_id,
        status="pending",
        requester_id=requester_id,
        ttl_seconds=ttl_seconds,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def get_approval(db: Session, request_id: str) -> HarnessApproval | None:
    row = db.get(HarnessApproval, request_id)
    if row and row.status == "pending" and _is_expired(row):
        row.status = "expired"
        db.flush()
    return row


def resolve_approval(
    db: Session,
    request_id: str,
    *,
    approved: bool,
    approver_id: str = "",
    notes: str = "",
) -> HarnessApproval | None:
    row = db.get(HarnessApproval, request_id)
    if row is None:
        return None
    if _is_expired(row):
        row.status = "expired"
    if row.status != "pending":
        db.flush()
        return row
    row.status = "approved" if approved else "rejected"
    row.approver_id = approver_id
    row.notes = notes
    row.resolved_at = _now()
    db.flush()
    return row


def list_approvals(
    db: Session, *, task_id: str | None = None, pending_only: bool = True
) -> list[dict[str, Any]]:
    stmt = select(HarnessApproval).order_by(HarnessApproval.created_at.desc())
    if task_id:
        stmt = stmt.where(HarnessApproval.task_id == task_id)
    rows = db.scalars(stmt).all()
    results = []
    for r in rows:
        if _is_expired(r) and r.status == "pending":
            r.status = "expired"
        if pending_only and r.status != "pending":
            continue
        results.append(_approval_to_dict(r))
    if pending_only:
        db.flush()  # persist any expiry updates
    return results


def count_pending_approvals(db: Session) -> int:
    # Expire stale ones first
    stale = db.scalars(
        select(HarnessApproval).where(
            HarnessApproval.status == "pending",
        )
    ).all()
    for r in stale:
        if _is_expired(r):
            r.status = "expired"
    db.flush()
    return (
        db.scalar(
            select(func.count()).select_from(
                select(HarnessApproval)
                .where(HarnessApproval.status == "pending")
                .subquery()
            )
        )
        or 0
    )


def _is_expired(row: HarnessApproval) -> bool:
    if row.status != "pending":
        return False
    created = row.created_at
    if created is None:
        return True
    # Normalize DB timestamps (SQLite naive UTC, PostgreSQL aware) before age math.
    age = (now() - as_utc(created)).total_seconds()
    if age < 0 or age > 86400 * 365:
        return False
    return age > row.ttl_seconds


def _approval_to_dict(r: HarnessApproval) -> dict[str, Any]:
    return {
        "request_id": r.id,
        "task_id": r.task_id,
        "tool_name": r.tool_name,
        "arguments": json.loads(r.arguments_json) if r.arguments_json else {},
        "reason": r.reason,
        "rule_id": r.rule_id,
        "status": r.status,
        "requester_id": r.requester_id,
        "approver_id": r.approver_id,
        "notes": r.notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    }


# ---------------------------------------------------------------------------
# Policy Rule CRUD
# ---------------------------------------------------------------------------


def upsert_policy_rule(db: Session, rule: PolicyRule) -> HarnessPolicyRule:
    existing = db.get(HarnessPolicyRule, rule.id)
    now = _now()
    if existing:
        existing.tool_pattern = rule.tool_pattern
        existing.arg_pattern = rule.arg_pattern
        existing.decision = rule.decision.value
        existing.reason = rule.reason
        existing.priority = rule.priority
        existing.enabled = rule.enabled
        existing.updated_at = now
        db.flush()
        return existing
    row = HarnessPolicyRule(
        id=rule.id,
        tool_pattern=rule.tool_pattern,
        arg_pattern=rule.arg_pattern,
        decision=rule.decision.value,
        reason=rule.reason,
        priority=rule.priority,
        enabled=rule.enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def delete_policy_rule(db: Session, rule_id: str) -> bool:
    row = db.get(HarnessPolicyRule, rule_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def list_policy_rules(db: Session) -> list[PolicyRule]:
    rows = db.scalars(
        select(HarnessPolicyRule).order_by(HarnessPolicyRule.priority.desc())
    ).all()
    return [
        PolicyRule(
            id=r.id,
            tool_pattern=r.tool_pattern,
            arg_pattern=r.arg_pattern,
            decision=DecisionKind(r.decision),
            reason=r.reason,
            priority=r.priority,
            enabled=r.enabled,
        )
        for r in rows
    ]


def load_persisted_rules(db: Session) -> list[PolicyRule]:
    """Load user-defined rules from DB, returning empty list if none exist."""
    return list_policy_rules(db)
