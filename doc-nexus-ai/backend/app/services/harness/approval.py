"""ApprovalService — Human-in-the-loop approval for ASK decisions.

When the PolicyEngine returns an ASK decision, the ToolGateway creates an
approval request.  The ApprovalService manages the lifecycle of those
requests: creation, polling, approval, rejection, and expiry.

This module is *purely additive* — it does not modify any existing code.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(slots=True)
class ApprovalRequest:
    """A single approval request."""

    request_id: str
    task_id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    rule_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requester_id: str = ""
    approver_id: str | None = None
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    ttl_seconds: float = 600.0  # 10 minutes default
    notes: str = ""

    @property
    def is_expired(self) -> bool:
        return (
            self.status == ApprovalStatus.PENDING
            and (time.time() - self.created_at) > self.ttl_seconds
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "status": self.status.value,
            "requester_id": self.requester_id,
            "approver_id": self.approver_id,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ApprovalService:
    """Manages approval requests for ASK policy decisions.

    In-memory store for now; a future iteration will persist to the DB
    using the ``approval_requests`` table defined in the design doc.
    """

    _requests: dict[str, ApprovalRequest] = field(default_factory=dict)

    def create(
        self,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        *,
        rule_id: str = "",
        requester_id: str = "",
        ttl_seconds: float = 600.0,
    ) -> ApprovalRequest:
        """Create a new approval request."""
        request_id = f"apr-{uuid.uuid4().hex[:12]}"
        req = ApprovalRequest(
            request_id=request_id,
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
            reason=reason,
            rule_id=rule_id,
            requester_id=requester_id,
            ttl_seconds=ttl_seconds,
        )
        self._requests[request_id] = req
        logger.info(
            "Approval created: id=%s task=%s tool=%s reason=%s",
            request_id,
            task_id,
            tool_name,
            reason,
        )
        return req

    def get(self, request_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID."""
        req = self._requests.get(request_id)
        if req and req.is_expired:
            req.status = ApprovalStatus.EXPIRED
        return req

    def approve(
        self,
        request_id: str,
        approver_id: str = "",
        notes: str = "",
    ) -> ApprovalRequest | None:
        """Approve a pending request."""
        req = self._requests.get(request_id)
        if req is None:
            return None
        if req.is_expired:
            req.status = ApprovalStatus.EXPIRED
            return req
        if req.status != ApprovalStatus.PENDING:
            return req
        req.status = ApprovalStatus.APPROVED
        req.approver_id = approver_id
        req.resolved_at = time.time()
        req.notes = notes
        logger.info(
            "Approval granted: id=%s approver=%s",
            request_id,
            approver_id,
        )
        return req

    def reject(
        self,
        request_id: str,
        approver_id: str = "",
        notes: str = "",
    ) -> ApprovalRequest | None:
        """Reject a pending request."""
        req = self._requests.get(request_id)
        if req is None:
            return None
        if req.is_expired:
            req.status = ApprovalStatus.EXPIRED
            return req
        if req.status != ApprovalStatus.PENDING:
            return req
        req.status = ApprovalStatus.REJECTED
        req.approver_id = approver_id
        req.resolved_at = time.time()
        req.notes = notes
        logger.info(
            "Approval rejected: id=%s approver=%s reason=%s",
            request_id,
            approver_id,
            notes,
        )
        return req

    def list_pending(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """List all pending approval requests, optionally filtered by task."""
        # First, expire stale requests
        for req in self._requests.values():
            if req.is_expired:
                req.status = ApprovalStatus.EXPIRED

        results = []
        for req in self._requests.values():
            if req.status != ApprovalStatus.PENDING:
                continue
            if task_id and req.task_id != task_id:
                continue
            results.append(req.as_dict())
        return results

    def list_all(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """List all approval requests (any status)."""
        results = []
        for req in self._requests.values():
            if req.is_expired:
                req.status = ApprovalStatus.EXPIRED
            if task_id and req.task_id != task_id:
                continue
            results.append(req.as_dict())
        return results

    def count_pending(self) -> int:
        """Count pending requests."""
        for req in self._requests.values():
            if req.is_expired:
                req.status = ApprovalStatus.EXPIRED
        return sum(
            1 for r in self._requests.values() if r.status == ApprovalStatus.PENDING
        )

    def cleanup_expired(self) -> int:
        """Remove expired requests.  Returns count removed."""
        expired_ids = [
            rid
            for rid, req in self._requests.items()
            if req.status in (ApprovalStatus.EXPIRED, ApprovalStatus.REJECTED)
            and req.resolved_at is not None
            and (time.time() - req.resolved_at) > 3600  # keep for 1 hour after resolution
        ]
        for rid in expired_ids:
            del self._requests[rid]
        return len(expired_ids)
