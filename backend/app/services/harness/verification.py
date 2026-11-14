"""VerificationEngine — Independent completion verification for agent tasks.

When an agent reports "I'm done", the Harness does NOT trust it.  The
VerificationEngine runs the task's configured build/test/lint commands
independently and feeds failures back for retry.

This module is *purely additive* — it does not modify any existing code.
The existing ``writing_dispatcher.py`` verification steps continue to work
unchanged; the VerificationEngine generalises the pattern for any task.
"""

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.services.harness.command_registry import (
    CommandNotRegisteredError,
    VerificationCommandRegistry,
)
from app.services.harness.task import AgentTask, TaskVerification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification status
# ---------------------------------------------------------------------------

class VerificationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Single step result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class VerificationStep:
    """Result of a single verification command (build, test, lint, or custom)."""

    name: str
    command: str
    status: VerificationStatus = VerificationStatus.PENDING
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status == VerificationStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.status == VerificationStatus.FAILED

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "status": self.status.value,
            "return_code": self.return_code,
            "stdout": self.stdout[-2000:] if self.stdout else "",  # truncate
            "stderr": self.stderr[-2000:] if self.stderr else "",
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Overall verification result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class VerificationResult:
    """Aggregate result of all verification steps for a task."""

    task_id: str
    status: VerificationStatus = VerificationStatus.PENDING
    steps: list[VerificationStep] = field(default_factory=list)
    attempt: int = 0
    max_retries: int = 0
    total_duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status == VerificationStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.status == VerificationStatus.FAILED

    @property
    def has_more_retries(self) -> bool:
        return self.attempt < self.max_retries

    @property
    def failure_summary(self) -> str:
        """Human-readable summary of failed steps (for feeding back to agent)."""
        failed = [s for s in self.steps if s.failed]
        if not failed:
            return ""
        lines = ["Verification failed:"]
        for s in failed:
            lines.append(f"  [{s.name}] {s.command}")
            if s.stderr:
                lines.append(f"    stderr: {s.stderr[-500:]}")
            elif s.stdout:
                lines.append(f"    output: {s.stdout[-500:]}")
            lines.append(f"    exit code: {s.return_code}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "has_more_retries": self.has_more_retries,
            "total_duration_ms": self.total_duration_ms,
            "failure_summary": self.failure_summary,
            "steps": [s.as_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------
# VerificationEngine
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class VerificationEngine:
    """Runs verification through fixed command profiles where configured."""

    default_timeout: int = 300
    command_registry: VerificationCommandRegistry = field(
        default_factory=VerificationCommandRegistry
    )
    async def verify(self, task: AgentTask) -> VerificationResult:
        """Run configured command profiles and legacy checks for *task*."""
        verification = task.verification
        workspace = task.workspace

        result = VerificationResult(
            task_id=task.task_id,
            max_retries=verification.max_retries,
        )

        if not verification.has_checks:
            result.status = VerificationStatus.SKIPPED
            return result

        result.status = VerificationStatus.RUNNING
        total_start = time.monotonic()
        steps_config: list[tuple[str, str | None, str | None]] = [
            ("build", verification.build_cmd, verification.build_command_id),
            ("test", verification.test_cmd, verification.test_command_id),
            ("lint", verification.lint_cmd, verification.lint_command_id),
        ]
        for i, custom_cmd in enumerate(verification.custom_checks):
            steps_config.append((f"custom-{i}", custom_cmd, None))
        for i, command_id in enumerate(verification.custom_command_ids):
            steps_config.append((f"custom-command-{i}", None, command_id))

        for step_name, command, command_id in steps_config:
            if not command and not command_id:
                continue
            step = await self._run_step(
                step_name,
                command or command_id or "",
                workspace,
                command_id=command_id,
            )
            result.steps.append(step)

            if step.failed:
                result.status = VerificationStatus.FAILED
                break
        else:
            result.status = VerificationStatus.PASSED

        result.total_duration_ms = int((time.monotonic() - total_start) * 1000)
        logger.info(
            "Verification %s: task=%s status=%s steps=%d duration=%dms",
            "passed" if result.passed else "failed",
            task.task_id,
            result.status.value,
            len(result.steps),
            result.total_duration_ms,
        )
        return result

    async def verify_with_retry(
        self,
        task: AgentTask,
        *,
        on_failure: Any | None = None,
    ) -> VerificationResult:
        """Run verification with automatic retry loop.

        Parameters
        ----------
        task : AgentTask
            The task to verify.
        on_failure : async callable | None
            Optional callback ``(result: VerificationResult) -> None``
            called after each failure.  Use this to feed the failure
            summary back to the agent before retrying.

        Returns
        -------
        VerificationResult
            The final result (passed, or failed after all retries).
        """
        verification = task.verification
        result = VerificationResult(
            task_id=task.task_id,
            max_retries=verification.max_retries,
        )

        for attempt in range(verification.max_retries + 1):
            result = await self.verify(task)
            result.attempt = attempt
            result.max_retries = verification.max_retries

            if result.passed:
                return result

            if not result.has_more_retries:
                logger.warning(
                    "Verification failed after %d attempts: task=%s",
                    attempt + 1,
                    task.task_id,
                )
                return result

            # Notify about failure before retry
            if on_failure is not None:
                await on_failure(result)

            logger.info(
                "Verification retry %d/%d: task=%s",
                attempt + 1,
                verification.max_retries,
                task.task_id,
            )

        return result

    # -- internals -------------------------------------------------------------

    async def _run_step(
        self,
        name: str,
        command: str,
        workspace: Path,
        *,
        command_id: str | None = None,
    ) -> VerificationStep:
        """Run a fixed command profile or a legacy command string."""
        step = VerificationStep(name=name, command=command)
        step.status = VerificationStatus.RUNNING
        start = time.monotonic()

        try:
            if command_id:
                profile = self.command_registry.resolve(command_id)
                proc = await asyncio.create_subprocess_exec(
                    *profile.argv,
                    cwd=str(workspace) if workspace and workspace.exists() else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(workspace) if workspace and workspace.exists() else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.default_timeout,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    proc.kill()
                await proc.wait()
                step.status = VerificationStatus.FAILED
                step.stderr = f"timed out after {self.default_timeout}s"
                step.duration_ms = int((time.monotonic() - start) * 1000)
                return step

            step.stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            step.stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            step.return_code = proc.returncode
            step.duration_ms = int((time.monotonic() - start) * 1000)
            step.status = (
                VerificationStatus.PASSED
                if proc.returncode == 0
                else VerificationStatus.FAILED
            )

        except (FileNotFoundError, CommandNotRegisteredError) as exc:
            step.status = VerificationStatus.FAILED
            step.stderr = f"command not found or not registered: {exc}"
            step.duration_ms = int((time.monotonic() - start) * 1000)
        except Exception as exc:
            step.status = VerificationStatus.FAILED
            step.stderr = str(exc)
            step.duration_ms = int((time.monotonic() - start) * 1000)

        return step
