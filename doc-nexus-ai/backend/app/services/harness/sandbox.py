"""SandboxManager — Isolated workspace lifecycle for agent tasks.

Creates per-task workspaces under a configurable base directory, clones
repositories, and cleans up after the task completes.  The Pi runtime (or
any other agent engine) only sees the workspace path — it never has access
to the host filesystem beyond it.

This module is *purely additive* — existing code paths that already create
workspaces via ``workspace_dir_for_agent`` continue to work unchanged.
The SandboxManager is used by the Harness orchestrator for new AgentTask
flows.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.harness.task import AgentTask

logger = logging.getLogger(__name__)


class SandboxError(RuntimeError):
    """Base error for sandbox operations."""


class WorkspaceCreateError(SandboxError):
    """Failed to create workspace."""


class CloneError(SandboxError):
    """Git clone failed."""


class WorkspaceNotFoundError(SandboxError):
    """Requested workspace does not exist."""


@dataclass(slots=True)
class WorkspaceInfo:
    """Metadata about a created workspace."""

    task_id: str
    path: Path
    repository: str | None = None
    branch: str | None = None
    cloned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "path": str(self.path),
            "repository": self.repository,
            "branch": self.branch,
            "cloned": self.cloned,
        }


@dataclass(slots=True)
class SandboxManager:
    """Manages isolated workspaces for agent tasks.

    Usage::

        mgr = SandboxManager(base_dir=Path("/workspaces"))
        info = mgr.create_workspace(task)
        # ... run agent with cwd=info.path ...
        mgr.destroy_workspace(task.task_id)
    """

    base_dir: Path = field(default_factory=lambda: Path("/workspaces"))

    # -- public API ------------------------------------------------------------

    def create_workspace(self, task: AgentTask, *, clone: bool = True) -> WorkspaceInfo:
        """Create an isolated workspace for *task*.

        1. Creates ``<base_dir>/<task_id>/repo`` directory.
        2. If *task.project.repository* is set and *clone* is True, clones it.
        3. Updates ``task.project.workspace`` to the resolved path.

        Returns a :class:`WorkspaceInfo` describing the created workspace.
        """
        workspace_root = self._workspace_root(task.task_id)
        repo_dir = workspace_root / "repo"

        try:
            repo_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceCreateError(
                f"Cannot create workspace {repo_dir}: {exc}"
            ) from exc

        cloned = False
        repository = task.project.repository
        branch = task.project.branch

        if clone and repository:
            self._clone_repository(repository, repo_dir, branch)
            cloned = True

        # Wire the resolved path back into the task so the runtime uses it.
        task.project = type(task.project)(
            project_id=task.project.project_id,
            workspace=str(repo_dir),
            repository=task.project.repository,
            branch=task.project.branch,
        )

        logger.info(
            "Workspace created: task_id=%s path=%s cloned=%s",
            task.task_id,
            repo_dir,
            cloned,
        )

        return WorkspaceInfo(
            task_id=task.task_id,
            path=repo_dir,
            repository=repository,
            branch=branch,
            cloned=cloned,
        )

    def destroy_workspace(self, task_id: str) -> None:
        """Remove the workspace for *task_id*.

        Safe to call multiple times — silently ignores missing directories.
        """
        workspace_root = self._workspace_root(task_id)
        if not workspace_root.exists():
            logger.debug("Workspace already gone: %s", workspace_root)
            return

        shutil.rmtree(workspace_root, ignore_errors=False)
        logger.info("Workspace destroyed: task_id=%s", task_id)

    def get_workspace(self, task_id: str) -> Path:
        """Return the repo directory for *task_id*.

        Raises :class:`WorkspaceNotFoundError` if it does not exist.
        """
        repo_dir = self._workspace_root(task_id) / "repo"
        if not repo_dir.is_dir():
            raise WorkspaceNotFoundError(
                f"Workspace not found for task {task_id}: {repo_dir}"
            )
        return repo_dir

    def list_active(self) -> list[dict[str, Any]]:
        """List all workspaces currently on disk."""
        result: list[dict[str, Any]] = []
        if not self.base_dir.is_dir():
            return result
        for child in sorted(self.base_dir.iterdir()):
            repo = child / "repo"
            if child.is_dir() and repo.is_dir():
                result.append({
                    "task_id": child.name,
                    "path": str(repo),
                })
        return result

    # -- internals -------------------------------------------------------------

    def _workspace_root(self, task_id: str) -> Path:
        """Top-level directory for a task (contains ``repo/``)."""
        safe_id = _safe_path_segment(task_id)
        return self.base_dir / safe_id

    def _clone_repository(
        self,
        repository: str,
        target: Path,
        branch: str | None,
    ) -> None:
        """Shallow-clone *repository* into *target*."""
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [repository, str(target)]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError as exc:
            raise CloneError("git is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise CloneError(f"Clone timed out: {repository}") from exc

        if result.returncode != 0:
            raise CloneError(
                f"git clone failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        logger.info("Cloned %s → %s (branch=%s)", repository, target, branch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re


def _safe_path_segment(value: str) -> str:
    """Sanitize a user-supplied string for safe use as a path segment."""
    return _re.sub(r"[^A-Za-z0-9._-]", "_", (value or "").strip())[:128] or "unknown"
