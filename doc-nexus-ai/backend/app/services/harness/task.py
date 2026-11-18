"""AgentTask — Enterprise task contract for the Harness.

Every agent execution is wrapped in an AgentTask that carries the objective,
permissions, resource limits, and verification conditions.  The Harness uses
this contract to gate tool calls, enforce budgets, and drive the verification
loop — regardless of which runtime (Pi, Codex, …) executes the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Project context
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Identifies the project and workspace an AgentTask operates on."""

    project_id: str
    workspace: str = ""
    repository: str | None = None
    branch: str | None = None


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TaskPermissions:
    """Coarse-grained capability switches for an agent task.

    These are *switches* — ``shell=True`` merely allows the agent to invoke
    shell commands.  Whether a *specific* command is allowed, rewritten, or
    denied is decided by the PolicyEngine at execution time.
    """

    file_read: bool = True
    file_write: bool = True
    shell: bool = True
    git_commit: bool = False
    git_push: bool = False
    network: bool = False
    network_allowlist: tuple[str, ...] = ()
    public_web_read: bool = False
    install_packages: bool = False
    sandbox: bool = False
    approved_skill_execution: bool = False
    skill_install_request: bool = False
    workspace_file_upload: bool = False
    database_write: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_read": self.file_read,
            "file_write": self.file_write,
            "shell": self.shell,
            "git_commit": self.git_commit,
            "git_push": self.git_push,
            "network": self.network,
            "network_allowlist": list(self.network_allowlist),
            "public_web_read": self.public_web_read,
            "install_packages": self.install_packages,
            "sandbox": self.sandbox,
            "approved_skill_execution": self.approved_skill_execution,
            "skill_install_request": self.skill_install_request,
            "workspace_file_upload": self.workspace_file_upload,
            "database_write": self.database_write,
        }

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Quick check used by the Gateway before invoking the PolicyEngine."""
        _TOOL_TO_PERMISSION: dict[str, str] = {
            "file_read": "file_read",
            "file_write": "file_write",
            "write": "file_write",
            "edit": "file_write",
            "bash": "shell",
            "shell": "shell",
            "find": "file_read",
            "ls": "file_read",
            "grep": "file_read",
            "git_commit": "git_commit",
            "git_push": "git_push",
            "network": "network",
            "npm": "install_packages",
            "http_request": "network",
            "request_skill_install": "skill_install_request",
            "database_write": "database_write",
        }
        perm_attr = _TOOL_TO_PERMISSION.get(tool_name)
        if perm_attr is None:
            return True  # unknown tools pass to PolicyEngine for decision
        return getattr(self, perm_attr, True)


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TaskLimits:
    """Resource ceilings enforced by the Harness — not by the runtime.

    The Harness checks these *after every tool call* and terminates the task
    immediately when any limit is exceeded.
    """

    max_steps: int = 60
    max_tokens: int = 500_000
    timeout_seconds: int = 1800
    max_cost: Decimal | None = None
    max_file_edits: int = 50
    max_shell_commands: int = 30

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_cost": str(self.max_cost) if self.max_cost is not None else None,
            "max_file_edits": self.max_file_edits,
            "max_shell_commands": self.max_shell_commands,
        }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TaskVerification:
    """Commands the Harness runs after the agent reports completion.

    The agent saying "I'm done" is *not* sufficient — the Harness runs
    these commands independently and feeds failures back for retry.
    """

    build_cmd: str | None = None
    test_cmd: str | None = None
    lint_cmd: str | None = None
    build_command_id: str | None = None
    test_command_id: str | None = None
    lint_command_id: str | None = None
    custom_checks: list[str] = field(default_factory=list)
    custom_command_ids: list[str] = field(default_factory=list)
    max_retries: int = 3

    def as_dict(self) -> dict[str, Any]:
        return {
            "build_cmd": self.build_cmd,
            "test_cmd": self.test_cmd,
            "lint_cmd": self.lint_cmd,
            "build_command_id": self.build_command_id,
            "test_command_id": self.test_command_id,
            "lint_command_id": self.lint_command_id,
            "custom_checks": list(self.custom_checks),
            "custom_command_ids": list(self.custom_command_ids),
            "max_retries": self.max_retries,
        }

    @property
    def has_checks(self) -> bool:
        return bool(
            self.build_cmd
            or self.test_cmd
            or self.lint_cmd
            or self.build_command_id
            or self.test_command_id
            or self.lint_command_id
            or self.custom_checks
            or self.custom_command_ids
        )


# ---------------------------------------------------------------------------
# AgentTask — the core contract
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentTask:
    """Enterprise task contract submitted to the Harness.

    This is the single entry point for every agent execution.  The Harness
    uses it to:
    * create an isolated workspace (SandboxManager)
    * build the system prompt (ContextBuilder)
    * gate tool calls (ToolGateway → PolicyEngine)
    * enforce resource ceilings (TaskLimits)
    * drive the verification loop (VerificationEngine)
    """

    task_id: str
    tenant_id: str
    user_id: str
    project: ProjectContext
    objective: str
    permissions: TaskPermissions = field(default_factory=TaskPermissions)
    limits: TaskLimits = field(default_factory=TaskLimits)
    verification: TaskVerification = field(default_factory=TaskVerification)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- convenience -----------------------------------------------------------

    @property
    def workspace(self) -> Path:
        """Resolved workspace path (set by SandboxManager)."""
        return Path(self.project.workspace) if self.project.workspace else Path("")

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "project_id": self.project.project_id,
            "objective": self.objective,
            "permissions": self.permissions.as_dict(),
            "limits": self.limits.as_dict(),
            "verification": self.verification.as_dict(),
        }
