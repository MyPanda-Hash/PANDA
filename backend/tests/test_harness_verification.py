"""Tests for VerificationEngine."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.harness.command_registry import (
    CommandProfile,
    VerificationCommandRegistry,
)
from app.services.harness.task import AgentTask, ProjectContext, TaskVerification
from app.services.harness.verification import (
    VerificationEngine,
    VerificationResult,
    VerificationStatus,
    VerificationStep,
)


# ============================================================================
# Helpers
# ============================================================================


def _task(
    tmp_path: Path | None = None,
    *,
    build_cmd: str | None = None,
    test_cmd: str | None = None,
    lint_cmd: str | None = None,
    custom_checks: list[str] | None = None,
    max_retries: int = 3,
) -> AgentTask:
    ws = str(tmp_path) if tmp_path else "/tmp/ws"
    return AgentTask(
        task_id="T1",
        tenant_id="ten",
        user_id="usr",
        project=ProjectContext(project_id="p1", workspace=ws),
        objective="test",
        verification=TaskVerification(
            build_cmd=build_cmd,
            test_cmd=test_cmd,
            lint_cmd=lint_cmd,
            custom_checks=custom_checks or [],
            max_retries=max_retries,
        ),
    )


# ============================================================================
# VerificationStep
# ============================================================================


class TestVerificationStep:
    def test_passed_property(self):
        step = VerificationStep(
            name="build", command="mvn package", status=VerificationStatus.PASSED
        )
        assert step.passed is True
        assert step.failed is False

    def test_failed_property(self):
        step = VerificationStep(
            name="test", command="mvn test", status=VerificationStatus.FAILED
        )
        assert step.failed is True
        assert step.passed is False

    def test_as_dict_truncates_output(self):
        step = VerificationStep(
            name="test",
            command="cmd",
            status=VerificationStatus.PASSED,
            stdout="x" * 5000,
        )
        d = step.as_dict()
        assert len(d["stdout"]) == 2000


# ============================================================================
# VerificationResult
# ============================================================================


class TestVerificationResult:
    def test_failure_summary_empty_when_passed(self):
        result = VerificationResult(task_id="T1", status=VerificationStatus.PASSED)
        assert result.failure_summary == ""

    def test_failure_summary_with_failed_steps(self):
        result = VerificationResult(
            task_id="T1",
            status=VerificationStatus.FAILED,
            steps=[
                VerificationStep(
                    name="build",
                    command="mvn package",
                    status=VerificationStatus.PASSED,
                ),
                VerificationStep(
                    name="test",
                    command="mvn test",
                    status=VerificationStatus.FAILED,
                    stderr="testLogin failed",
                    return_code=1,
                ),
            ],
        )
        summary = result.failure_summary
        assert "Verification failed" in summary
        assert "test" in summary
        assert "testLogin failed" in summary

    def test_has_more_retries(self):
        result = VerificationResult(
            task_id="T1", attempt=0, max_retries=3
        )
        assert result.has_more_retries is True

        result2 = VerificationResult(
            task_id="T1", attempt=3, max_retries=3
        )
        assert result2.has_more_retries is False

    def test_as_dict(self):
        result = VerificationResult(
            task_id="T1",
            status=VerificationStatus.PASSED,
            attempt=1,
            max_retries=3,
            total_duration_ms=500,
        )
        d = result.as_dict()
        assert d["task_id"] == "T1"
        assert d["status"] == "passed"
        assert d["attempt"] == 1


# ============================================================================
# VerificationEngine
# ============================================================================


class TestVerificationEngine:
    @pytest.mark.asyncio
    async def test_no_checks_returns_skipped(self, tmp_path):
        task = _task(tmp_path)
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.status == VerificationStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_passing_build(self, tmp_path):
        task = _task(tmp_path, build_cmd="echo build-ok")
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.passed is True
        assert len(result.steps) == 1
        assert result.steps[0].passed is True
        assert "build-ok" in result.steps[0].stdout
    @pytest.mark.asyncio
    async def test_registered_command_uses_fixed_argv(self, tmp_path):
        task = _task(tmp_path)
        task.verification.build_command_id = "unit"
        engine = VerificationEngine(
            command_registry=VerificationCommandRegistry(
                profiles={
                    "unit": CommandProfile("unit", ("/bin/echo", "build-ok")),
                }
            )
        )
        result = await engine.verify(task)
        assert result.passed is True
        assert result.steps[0].stdout.strip() == "build-ok"

    @pytest.mark.asyncio
    async def test_failing_build(self, tmp_path):
        task = _task(tmp_path, build_cmd="echo fail && exit 1")
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.failed is True
        assert len(result.steps) == 1
        assert result.steps[0].failed is True
        assert result.steps[0].return_code == 1

    @pytest.mark.asyncio
    async def test_build_passes_test_fails(self, tmp_path):
        task = _task(
            tmp_path,
            build_cmd="echo build-ok",
            test_cmd="echo test-fail && exit 1",
        )
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.failed is True
        assert len(result.steps) == 2
        assert result.steps[0].passed is True
        assert result.steps[1].failed is True

    @pytest.mark.asyncio
    async def test_all_pass(self, tmp_path):
        task = _task(
            tmp_path,
            build_cmd="echo build",
            test_cmd="echo test",
            lint_cmd="echo lint",
        )
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.passed is True
        assert len(result.steps) == 3

    @pytest.mark.asyncio
    async def test_short_circuit_on_failure(self, tmp_path):
        """Stops at first failure — lint should not run."""
        task = _task(
            tmp_path,
            build_cmd="echo build && exit 1",
            test_cmd="echo test",
            lint_cmd="echo lint",
        )
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.failed is True
        assert len(result.steps) == 1  # only build ran

    @pytest.mark.asyncio
    async def test_custom_checks(self, tmp_path):
        task = _task(
            tmp_path,
            custom_checks=["echo custom1", "echo custom2"],
        )
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.passed is True
        assert len(result.steps) == 2
        assert result.steps[0].name == "custom-0"
        assert result.steps[1].name == "custom-1"

    @pytest.mark.asyncio
    async def test_command_not_found(self, tmp_path):
        task = _task(tmp_path, build_cmd="nonexistent_command_xyz_123")
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.failed is True
        assert result.steps[0].failed is True

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        task = _task(tmp_path, build_cmd="sleep 100")
        engine = VerificationEngine(default_timeout=1)
        result = await engine.verify(task)
        assert result.failed is True
        assert "timed out" in result.steps[0].stderr

    @pytest.mark.asyncio
    async def test_total_duration_tracking(self, tmp_path):
        task = _task(tmp_path, build_cmd="sleep 0.1")
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.total_duration_ms > 0

    @pytest.mark.asyncio
    async def test_step_duration_tracking(self, tmp_path):
        task = _task(tmp_path, build_cmd="sleep 0.05")
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert result.steps[0].duration_ms > 0

    @pytest.mark.asyncio
    async def test_failure_summary_in_result(self, tmp_path):
        task = _task(
            tmp_path,
            build_cmd="echo ok",
            test_cmd="echo 'FAIL: npe' && exit 1",
        )
        engine = VerificationEngine()
        result = await engine.verify(task)
        assert "Verification failed" in result.failure_summary
        assert "FAIL: npe" in result.failure_summary

    @pytest.mark.asyncio
    async def test_verify_with_retry_passes_first_try(self, tmp_path):
        task = _task(tmp_path, build_cmd="echo ok", max_retries=3)
        engine = VerificationEngine()
        result = await engine.verify_with_retry(task)
        assert result.passed is True
        assert result.attempt == 0

    @pytest.mark.asyncio
    async def test_verify_with_retry_exhausted(self, tmp_path):
        task = _task(tmp_path, build_cmd="exit 1", max_retries=1)
        engine = VerificationEngine()
        result = await engine.verify_with_retry(task)
        assert result.failed is True
        assert result.attempt == 1

    @pytest.mark.asyncio
    async def test_verify_with_retry_callback(self, tmp_path):
        """on_failure is called before each retry."""
        failures = []

        async def on_fail(result: VerificationResult) -> None:
            failures.append(result.failure_summary)

        task = _task(tmp_path, build_cmd="echo 'oops' && exit 1", max_retries=2)
        engine = VerificationEngine()
        result = await engine.verify_with_retry(task, on_failure=on_fail)
        assert result.failed is True
        # on_failure called for attempts 0 and 1 (retry 1 and 2), not for the last
        assert len(failures) == 2
        assert "oops" in failures[0]

    @pytest.mark.asyncio
    async def test_verify_with_retry_eventual_success(self, tmp_path):
        """Flaky test: fails first, passes second."""
        counter_file = tmp_path / "counter"
        counter_file.write_text("0")

        # Script that fails on first run, passes on second
        script = (
            f"count=$(cat {counter_file}); "
            f"echo $((count+1)) > {counter_file}; "
            f"test $count -eq 0 && exit 1 || exit 0"
        )
        task = _task(tmp_path, test_cmd=script, max_retries=3)
        engine = VerificationEngine()
        result = await engine.verify_with_retry(task)
        assert result.passed is True
        assert result.attempt == 1
