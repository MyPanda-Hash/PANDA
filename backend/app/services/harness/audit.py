"""HarnessAuditLogger — DB-backed audit trail for Harness operations.

Records task lifecycle, tool calls, policy decisions, verifications, and
approvals to the ``harness_audit_events`` table via the repository layer.

This replaces the previous in-memory implementation.  The API is unchanged
so callers need no modification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.database import transaction
from app.repositories import harness as harness_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HarnessAuditLogger:
    """DB-backed audit logger for Harness operations.

    Usage::

        audit = HarnessAuditLogger()
        audit.log_task_start("TASK-1", actor_id="admin")
        audit.log_tool_call("bash", {"command": "ls"}, "ALLOW", "TASK-1")
    """

    def log_task_start(self, task_id: str, *, actor_id: str = "", **kw: Any) -> None:
        self._record("task_start", task_id, actor_id=actor_id, details=kw)

    def log_task_complete(self, task_id: str, *, actor_id: str = "", **kw: Any) -> None:
        self._record("task_complete", task_id, actor_id=actor_id, details=kw)

    def log_task_failed(
        self, task_id: str, *, error: str = "", actor_id: str = ""
    ) -> None:
        self._record(
            "task_failed", task_id, actor_id=actor_id, details={"error": error}
        )

    def log_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision_kind: str,
        task_id: str,
        *,
        rule_id: str = "",
        actor_id: str = "",
    ) -> None:
        self._record(
            "tool_call",
            task_id,
            actor_id=actor_id,
            details={
                "tool": tool_name,
                "arguments": safe_tool_arguments(arguments, tool_name=tool_name),
                "decision": decision_kind,
                "rule_id": rule_id,
            },
        )

    def log_policy_decision(
        self,
        decision_kind: str,
        rule_id: str,
        task_id: str,
        *,
        reason: str = "",
        actor_id: str = "",
    ) -> None:
        self._record(
            "policy_decision",
            task_id,
            actor_id=actor_id,
            details={
                "decision": decision_kind,
                "rule_id": rule_id,
                "reason": reason,
            },
        )

    def log_verification(
        self,
        task_id: str,
        status: str,
        *,
        attempt: int = 0,
        steps: list[dict[str, Any]] | None = None,
        actor_id: str = "",
    ) -> None:
        self._record(
            "verification",
            task_id,
            actor_id=actor_id,
            details={
                "status": status,
                "attempt": attempt,
                "steps": steps or [],
            },
        )

    def log_approval_created(
        self,
        task_id: str,
        request_id: str,
        *,
        tool_name: str = "",
        reason: str = "",
        actor_id: str = "",
    ) -> None:
        self._record(
            "approval_created",
            task_id,
            actor_id=actor_id,
            details={
                "request_id": request_id,
                "tool": tool_name,
                "reason": reason,
            },
        )

    def log_approval_resolved(
        self,
        task_id: str,
        request_id: str,
        *,
        approved: bool,
        approver_id: str = "",
        actor_id: str = "",
    ) -> None:
        self._record(
            "approval_resolved",
            task_id,
            actor_id=actor_id or approver_id,
            details={
                "request_id": request_id,
                "approved": approved,
            },
        )

    def list_entries(
        self,
        task_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit entries from DB."""
        with transaction() as db:
            return harness_repo.list_audit_events(
                db, task_id=task_id, event_type=event_type, limit=limit
            )

    def count(self) -> int:
        with transaction() as db:
            return harness_repo.count_audit_events(db)

    # -- internals -------------------------------------------------------------

    def _record(
        self,
        event_type: str,
        task_id: str,
        *,
        actor_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            with transaction() as db:
                harness_repo.record_audit(
                    db,
                    event_type,
                    task_id,
                    actor_id=actor_id,
                    details=details,
                )
        except Exception:
            logger.exception(
                "Failed to write audit event: %s task=%s", event_type, task_id
            )


def safe_tool_arguments(
    arguments: dict[str, Any], *, tool_name: str = "", truncate: bool = True
) -> dict[str, Any]:
    """Keep private memory values out of logs and generic approval storage."""
    if tool_name.removeprefix("nexus_") in (
        "get_my_memory",
        "propose_my_memory",
        "propose_forget_my_memory",
    ):
        return {"argument_count": len(arguments)}
    if not truncate:
        return arguments
    safe = {}
    for k, v in arguments.items():
        sv = str(v)
        safe[k] = sv[:500] + "…" if len(sv) > 500 else sv
    return safe
