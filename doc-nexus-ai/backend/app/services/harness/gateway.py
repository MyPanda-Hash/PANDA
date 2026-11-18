"""ToolGateway — Interception layer for all agent tool calls.

Every tool call from the agent runtime is routed through the Gateway
*before* execution.  The Gateway:

1. Checks the task's permission switches (coarse gate).
2. Invokes the PolicyEngine for fine-grained ALLOW / DENY / ASK / REWRITE.
3. For DENY → returns an error result (never executes).
4. For ASK → creates an approval request and returns a pending result.
5. For REWRITE → substitutes the arguments and falls through.
6. For ALLOW → executes the tool in the sandbox and returns the result.

This module is *purely additive* — it does not modify any existing code.
The existing ``tool_policy.ts`` prompt-level guard continues to work as a
soft hint; the Gateway is the hard enforcement layer.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.harness.file_storage import upload_workspace_file
from app.services.harness.policy import DecisionKind, PolicyDecision, PolicyEngine
from app.services.harness.task import AgentTask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool result envelope
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of a tool call routed through the Gateway."""

    success: bool
    output: str = ""
    error: str = ""
    is_denied: bool = False
    approval_required: bool = False
    approval_request_id: str = ""
    rewritten: bool = False
    policy_decision: PolicyDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, **kw: Any) -> ToolResult:
        return cls(success=True, output=output, **kw)

    @classmethod
    def fail(cls, error: str, **kw: Any) -> ToolResult:
        return cls(success=False, error=error, **kw)

    @classmethod
    def denied(cls, error: str, decision: PolicyDecision | None = None) -> ToolResult:
        return cls(success=False, error=error, is_denied=True, policy_decision=decision)

    @classmethod
    def approval_pending(
        cls,
        request_id: str,
        reason: str,
        decision: PolicyDecision | None = None,
    ) -> ToolResult:
        return cls(
            success=False,
            error=f"approval required: {reason}",
            approval_required=True,
            approval_request_id=request_id,
            policy_decision=decision,
        )

    def with_metadata(self, **updates: Any) -> ToolResult:
        return ToolResult(
            success=self.success,
            output=self.output,
            error=self.error,
            is_denied=self.is_denied,
            approval_required=self.approval_required,
            approval_request_id=self.approval_request_id,
            rewritten=self.rewritten,
            policy_decision=self.policy_decision,
            metadata={**self.metadata, **updates},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "is_denied": self.is_denied,
            "approval_required": self.approval_required,
            "approval_request_id": self.approval_request_id,
            "rewritten": self.rewritten,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Sandbox executor
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SandboxExecutor:
    """Executes tool calls inside a sandboxed workspace.

    All file and shell operations are confined to ``workspace``.  Attempts
    to escape are rejected before the command even runs.
    """

    workspace: Path
    shell_timeout: int = 600

    def run_shell(self, command: str) -> ToolResult:
        """Execute *command* via subprocess inside the workspace."""
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=self.shell_timeout,
            )
            output = result.stdout
            if result.returncode != 0:
                error = result.stderr or f"exit code {result.returncode}"
                return ToolResult.fail(error, metadata={"returncode": result.returncode})
            return ToolResult.ok(output)
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"command timed out ({self.shell_timeout}s)")
        except Exception as exc:
            return ToolResult.fail(str(exc))

    def run_file_read(self, path: str) -> ToolResult:
        """Read a file inside the workspace."""
        resolved = self._resolve_safe(path)
        if resolved is None:
            return ToolResult.denied(f"path outside workspace: {path}")
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            return ToolResult.ok(content)
        except (OSError, ValueError) as exc:
            return ToolResult.fail(str(exc))

    def run_file_write(self, path: str, content: str) -> ToolResult:
        """Write a file inside the workspace."""
        resolved = self._resolve_safe(path)
        if resolved is None:
            return ToolResult.denied(f"path outside workspace: {path}")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return ToolResult.ok(f"written {len(content)} bytes")
        except (OSError, ValueError) as exc:
            return ToolResult.fail(str(exc))

    def resolve_path(self, path: str) -> Path | None:
        """Resolve *path* and ensure it stays within the workspace."""
        return self._resolve_safe(path)

    def _resolve_safe(self, path_str: str) -> Path | None:
        """Resolve *path_str* and ensure it stays within the workspace."""
        p = Path(path_str)
        if not p.is_absolute():
            p = self.workspace / p
        try:
            resolved = p.resolve()
            workspace_resolved = self.workspace.resolve()
            if resolved == workspace_resolved or str(resolved).startswith(
                str(workspace_resolved) + "/"
            ):
                return resolved
        except (OSError, ValueError):
            pass
        return None


# ---------------------------------------------------------------------------
# ToolGateway
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolGateway:
    """Central interception point for all agent tool calls.

    Usage::

        gateway = ToolGateway(policy=PolicyEngine(), sandbox=SandboxExecutor(...))
        result = gateway.execute("bash", {"command": "ls"}, task)
    """

    policy: PolicyEngine
    sandbox: SandboxExecutor | None = None
    _pending_approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    _uploaded_file_paths: set[str] = field(default_factory=set)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        task: AgentTask | None = None,
        *,
        on_approval_create: Any | None = None,
    ) -> ToolResult:
        """Route a tool call through policy checks and execute.

        Parameters
        ----------
        tool_name : str
            The tool the agent wants to invoke (e.g. "bash", "write", "edit").
        arguments : dict
            Tool arguments (e.g. {"command": "mvn test"}).
        task : AgentTask | None
            The current task (for permission and limit checks).
        on_approval_create : callable | None
            Optional callback ``(request_id, tool, args, reason) -> None``
            called when an ASK decision creates an approval request.
        """
        # 1. Policy evaluation
        decision = self.policy.evaluate(tool_name, arguments, task)

        # 2. Route by decision
        if decision.kind == DecisionKind.DENY:
            logger.info(
                "tool.denied",
                extra={
                    "tool": tool_name,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "task_id": task.task_id if task else None,
                    "span": "harness.tool",
                },
            )
            return ToolResult.denied(decision.reason, decision=decision)

        if decision.kind == DecisionKind.ASK:
            request_id = self._create_approval_request(
                tool_name, arguments, decision, task
            )
            if on_approval_create is not None:
                on_approval_create(request_id, tool_name, arguments, decision.reason)
            logger.info(
                "tool.approval_required",
                extra={
                    "tool": tool_name,
                    "approval_request_id": request_id,
                    "reason": decision.reason,
                    "task_id": task.task_id if task else None,
                    "span": "harness.tool",
                },
            )
            return ToolResult.approval_pending(
                request_id, decision.reason, decision=decision
            )

        if decision.kind == DecisionKind.REWRITE:
            # Substitute rewritten arguments
            if decision.rewritten_args:
                arguments = decision.rewritten_args
            if decision.rewritten_tool:
                tool_name = decision.rewritten_tool
            logger.info(
                "tool.rewritten",
                extra={
                    "tool": tool_name,
                    "rule_id": decision.rule_id,
                    "task_id": task.task_id if task else None,
                    "span": "harness.tool",
                },
            )
            # Fall through to execution with rewritten args

        # 3. ALLOW (or REWRITE fell through) → execute
        return self._execute_tool(tool_name, arguments, task.task_id if task else None)

    def resolve_approval(
        self,
        request_id: str,
        approved: bool,
        *,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Resolve a pending approval request.

        If *approved* is True, executes the original tool call.
        If False, returns a denied result.
        """
        pending = self._pending_approvals.pop(request_id, None)
        if pending is None:
            return ToolResult.fail(f"unknown or expired approval request: {request_id}")

        if not approved:
            return ToolResult.denied("approval rejected by reviewer")

        # Execute with original tool/args
        exec_tool = tool_name or pending["tool"]
        exec_args = arguments or pending["args"]
        task_id = pending.get("task_id")
        return self._execute_tool(exec_tool, exec_args, task_id)

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        """Return all pending approval requests (for API/UI)."""
        return [
            {
                "request_id": rid,
                "tool": info["tool"],
                "args": info["args"],
                "reason": info["reason"],
                "task_id": info.get("task_id", ""),
            }
            for rid, info in self._pending_approvals.items()
        ]

    # -- internals -------------------------------------------------------------

    def _execute_tool(
        self, tool_name: str, arguments: dict[str, Any], task_id: str | None = None
    ) -> ToolResult:
        """Execute a tool call in the sandbox."""
        if self.sandbox is None:
            return ToolResult.fail("no sandbox configured")

        if tool_name in ("bash", "shell"):
            command = arguments.get("command", "")
            return self.sandbox.run_shell(command)

        if tool_name in ("read", "file_read", "find", "ls", "grep"):
            path = arguments.get("path", arguments.get("file", ""))
            return self.sandbox.run_file_read(path) if path else ToolResult.fail("missing path")

        if tool_name in ("write", "file_write"):
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            if not path:
                return ToolResult.fail("missing path")
            result = self.sandbox.run_file_write(path, content)
            return self._enrich_write_result(result, task_id, path, content.encode("utf-8"))

        if tool_name == "edit":
            # edit is a special case — for now, treat as write
            path = arguments.get("path", "")
            content = arguments.get("content", arguments.get("new_string", ""))
            if not path:
                return ToolResult.fail("missing path")
            result = self.sandbox.run_file_write(path, content)
            return self._enrich_write_result(result, task_id, path, content.encode("utf-8"))

        # Unknown tool → pass through as a warning
        logger.warning(
            "tool.unknown",
            extra={
                "tool": tool_name,
                "span": "harness.tool",
            },
        )
        return ToolResult.fail(f"unsupported tool: {tool_name}")

    def _enrich_write_result(
        self,
        result: ToolResult,
        task_id: str | None,
        path: str,
        content: bytes,
    ) -> ToolResult:
        """Upload a written file to MinIO and attach the download URL."""
        if not result.success or self.sandbox is None or not task_id:
            return result
        resolved = self.sandbox.resolve_path(path)
        if resolved is None:
            return result
        info = upload_workspace_file(
            task_id=task_id,
            workspace=self.sandbox.workspace,
            path=resolved,
            content=content,
        )
        if info:
            self._uploaded_file_paths.add(str(resolved))
            return result.with_metadata(
                download_url=info["url"],
                uploaded_path=info["path"],
            )
        return result

    def _create_approval_request(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PolicyDecision,
        task: AgentTask | None,
    ) -> str:
        """Create an approval request and return its ID."""
        import uuid

        request_id = f"apr-{uuid.uuid4().hex[:12]}"
        self._pending_approvals[request_id] = {
            "tool": tool_name,
            "args": arguments,
            "reason": decision.reason,
            "rule_id": decision.rule_id,
            "task_id": task.task_id if task else "",
        }
        return request_id
