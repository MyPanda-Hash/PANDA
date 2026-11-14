"""Tests for the Enterprise Harness task contract and sandbox."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.harness.sandbox import (
    CloneError,
    SandboxManager,
    WorkspaceCreateError,
    WorkspaceNotFoundError,
)
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskLimits,
    TaskPermissions,
    TaskVerification,
)


# ============================================================================
# TaskPermissions
# ============================================================================


class TestTaskPermissions:
    def test_defaults_allow_common_tools(self):
        perms = TaskPermissions()
        assert perms.file_read is True
        assert perms.file_write is True
        assert perms.shell is True
        assert perms.git_commit is False
        assert perms.git_push is False
        assert perms.network is False
        assert perms.public_web_read is False

    def test_as_dict_roundtrip(self):
        perms = TaskPermissions(file_write=False, shell=False, public_web_read=True)
        d = perms.as_dict()
        assert d["file_write"] is False
        assert d["shell"] is False
        assert d["file_read"] is True
        assert d["public_web_read"] is True

    def test_is_tool_allowed_basic(self):
        perms = TaskPermissions(shell=False, file_write=False)
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("bash") is False
        assert perms.is_tool_allowed("write") is False
        assert perms.is_tool_allowed("edit") is False

    def test_is_tool_allowed_unknown_tools_pass_through(self):
        perms = TaskPermissions()
        assert perms.is_tool_allowed("some_future_tool") is True

    def test_git_push_requires_explicit_permission(self):
        perms = TaskPermissions()
        assert perms.is_tool_allowed("git_push") is False

        perms_with_push = TaskPermissions(git_push=True)
        assert perms_with_push.is_tool_allowed("git_push") is True

    def test_search_skill_catalog_allowed_without_network(self):
        perms = TaskPermissions(network=False)
        assert perms.is_tool_allowed("search_skill_catalog") is True
        assert perms.is_tool_allowed("http_request") is False


    def test_skill_install_request_requires_explicit_permission(self):
        perms = TaskPermissions()
        assert perms.is_tool_allowed("request_skill_install") is False
        assert (
            TaskPermissions(skill_install_request=True).is_tool_allowed(
                "request_skill_install"
            )
            is True
        )


# ============================================================================
# TaskLimits
# ============================================================================


class TestTaskLimits:
    def test_defaults(self):
        limits = TaskLimits()
        assert limits.max_steps == 60
        assert limits.max_tokens == 500_000
        assert limits.timeout_seconds == 1800
        assert limits.max_cost is None

    def test_as_dict_with_cost(self):
        limits = TaskLimits(max_cost=Decimal("10.50"))
        d = limits.as_dict()
        assert d["max_cost"] == "10.50"

    def test_as_dict_without_cost(self):
        limits = TaskLimits()
        d = limits.as_dict()
        assert d["max_cost"] is None


# ============================================================================
# TaskVerification
# ============================================================================


class TestTaskVerification:
    def test_has_checks_false_when_empty(self):
        v = TaskVerification()
        assert v.has_checks is False

    def test_has_checks_true_with_build(self):
        v = TaskVerification(build_cmd="mvn package")
        assert v.has_checks is True

    def test_has_checks_true_with_custom(self):
        v = TaskVerification(custom_checks=["check-headers"])
        assert v.has_checks is True

    def test_as_dict(self):
        v = TaskVerification(test_cmd="pytest", max_retries=5)
        d = v.as_dict()
        assert d["test_cmd"] == "pytest"
        assert d["max_retries"] == 5
        assert d["custom_checks"] == []


# ============================================================================
# AgentTask
# ============================================================================


class TestAgentTask:
    def _make_task(self, **kwargs):
        defaults = dict(
            task_id="TASK-001",
            tenant_id="t1",
            user_id="u1",
            project=ProjectContext(project_id="proj-1", workspace="/tmp/ws"),
            objective="fix the bug",
        )
        defaults.update(kwargs)
        return AgentTask(**defaults)

    def test_basic_creation(self):
        task = self._make_task()
        assert task.task_id == "TASK-001"
        assert task.tenant_id == "t1"
        assert task.objective == "fix the bug"

    def test_workspace_property(self):
        task = self._make_task()
        assert task.workspace == Path("/tmp/ws")

    def test_workspace_empty_when_not_set(self):
        task = self._make_task(project=ProjectContext(project_id="p1"))
        assert task.workspace == Path("")

    def test_as_dict(self):
        task = self._make_task()
        d = task.as_dict()
        assert d["task_id"] == "TASK-001"
        assert d["permissions"]["shell"] is True
        assert d["limits"]["max_steps"] == 60
        assert d["verification"]["test_cmd"] is None

    def test_custom_permissions(self):
        task = self._make_task(
            permissions=TaskPermissions(shell=False, network=True)
        )
        assert task.permissions.shell is False
        assert task.permissions.network is True

    def test_custom_limits(self):
        task = self._make_task(
            limits=TaskLimits(max_steps=10, timeout_seconds=300)
        )
        assert task.limits.max_steps == 10
        assert task.limits.timeout_seconds == 300


# ============================================================================
# SandboxManager
# ============================================================================


class TestSandboxManager:
    def test_create_workspace_creates_repo_dir(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        task = AgentTask(
            task_id="T1",
            tenant_id="ten",
            user_id="usr",
            project=ProjectContext(project_id="p1"),
            objective="test",
        )
        info = mgr.create_workspace(task, clone=False)
        assert info.path.is_dir()
        assert info.task_id == "T1"
        assert info.cloned is False
        # workspace wired back into task
        assert task.workspace == info.path

    def test_destroy_workspace_removes_directory(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        task = AgentTask(
            task_id="T2",
            tenant_id="ten",
            user_id="usr",
            project=ProjectContext(project_id="p1"),
            objective="test",
        )
        mgr.create_workspace(task, clone=False)
        mgr.destroy_workspace("T2")
        with pytest.raises(WorkspaceNotFoundError):
            mgr.get_workspace("T2")

    def test_destroy_nonexistent_is_safe(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        mgr.destroy_workspace("nope")  # should not raise

    def test_get_workspace_raises_when_missing(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        with pytest.raises(WorkspaceNotFoundError):
            mgr.get_workspace("missing")

    def test_list_active(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        for tid in ("A", "B"):
            task = AgentTask(
                task_id=tid,
                tenant_id="t",
                user_id="u",
                project=ProjectContext(project_id="p"),
                objective="o",
            )
            mgr.create_workspace(task, clone=False)
        active = mgr.list_active()
        ids = [a["task_id"] for a in active]
        assert "A" in ids
        assert "B" in ids

    def test_clone_failure_raises(self, tmp_path):
        mgr = SandboxManager(base_dir=tmp_path / "workspaces")
        task = AgentTask(
            task_id="T3",
            tenant_id="ten",
            user_id="usr",
            project=ProjectContext(
                project_id="p1",
                repository="https://invalid.example.com/repo.git",
            ),
            objective="test",
        )
        with pytest.raises(CloneError):
            mgr.create_workspace(task, clone=True)

    def test_safe_path_segment_sanitizes(self, tmp_path):
        """Task IDs with path separators are sanitized."""
        mgr = SandboxManager(base_dir=tmp_path / "ws")
        task = AgentTask(
            task_id="../../../etc/passwd",
            tenant_id="t",
            user_id="u",
            project=ProjectContext(project_id="p"),
            objective="o",
        )
        info = mgr.create_workspace(task, clone=False)
        # Must be inside base_dir, not /etc
        assert str(info.path).startswith(str(tmp_path / "ws"))
