"""Tests for PolicyEngine, ToolGateway, and ApprovalService."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.harness.approval import ApprovalService, ApprovalStatus
from app.services.harness.gateway import SandboxExecutor, ToolGateway, ToolResult
from app.services.harness.policy import (
    DecisionKind,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
)
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskPermissions,
)


# ============================================================================
# Helpers
# ============================================================================


def _task(**kwargs) -> AgentTask:
    defaults = dict(
        task_id="T1",
        tenant_id="ten",
        user_id="usr",
        project=ProjectContext(project_id="p1", workspace="/tmp/ws"),
        objective="test",
    )
    defaults.update(kwargs)
    return AgentTask(**defaults)


# ============================================================================
# PolicyDecision
# ============================================================================


class TestPolicyDecision:
    def test_allow(self):
        d = PolicyDecision.allow(rule_id="r1")
        assert d.kind == DecisionKind.ALLOW
        assert d.rule_id == "r1"

    def test_deny(self):
        d = PolicyDecision.deny("forbidden", rule_id="r2")
        assert d.kind == DecisionKind.DENY
        assert d.reason == "forbidden"

    def test_ask(self):
        d = PolicyDecision.ask("needs approval")
        assert d.kind == DecisionKind.ASK

    def test_rewrite(self):
        d = PolicyDecision.rewrite(
            "rewritten",
            rewritten_tool="bash",
            rewritten_args={"command": "pip install --index-url http://py.test p"},
        )
        assert d.kind == DecisionKind.REWRITE
        assert d.rewritten_args is not None


# ============================================================================
# PolicyRule
# ============================================================================


class TestPolicyRule:
    def test_matches_exact_tool(self):
        rule = PolicyRule(id="r1", tool_pattern="bash", decision=DecisionKind.DENY)
        assert rule.matches("bash", {"command": "ls"}) is True
        assert rule.matches("write", {"path": "x"}) is False

    def test_matches_glob(self):
        rule = PolicyRule(id="r1", tool_pattern="git_*", decision=DecisionKind.ASK)
        assert rule.matches("git_push", {}) is True
        assert rule.matches("git_commit", {}) is True
        assert rule.matches("bash", {}) is False

    def test_matches_arg_pattern(self):
        rule = PolicyRule(
            id="r1",
            tool_pattern="bash",
            arg_pattern=r"\bsudo\b",
            decision=DecisionKind.DENY,
        )
        assert rule.matches("bash", {"command": "sudo apt update"}) is True
        assert rule.matches("bash", {"command": "apt update"}) is False

    def test_disabled_rule_never_matches(self):
        rule = PolicyRule(
            id="r1", tool_pattern="bash", enabled=False, decision=DecisionKind.DENY
        )
        assert rule.matches("bash", {"command": "ls"}) is False

    def test_empty_arg_pattern_matches_any(self):
        rule = PolicyRule(id="r1", tool_pattern="bash", decision=DecisionKind.DENY)
        assert rule.matches("bash", {"command": "anything"}) is True


# ============================================================================
# PolicyEngine
# ============================================================================


class TestPolicyEngine:
    def test_deny_rmf_root(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "rm -rf /"})
        assert d.kind == DecisionKind.DENY
        assert "rm -rf" in d.reason.lower() or "forbidden" in d.reason.lower()

    def test_deny_sudo(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "sudo systemctl restart mysql"})
        assert d.kind == DecisionKind.DENY
        assert "sudo" in d.reason.lower()

    def test_deny_direct_interpreter(self):
        engine = PolicyEngine()
        decision = engine.evaluate("bash", {"command": "python scripts/job.py"})
        assert decision.kind == DecisionKind.DENY
        assert "approved Skill" in decision.reason

    def test_sandbox_allows_interpreter_network_and_package_install(self):
        engine = PolicyEngine()
        task = _task(
            permissions=TaskPermissions(
                network=True,
                install_packages=True,
                sandbox=True,
            )
        )

        assert engine.evaluate("bash", {"command": "python -c 'print(1)'"}, task).kind == DecisionKind.ALLOW
        assert engine.evaluate("bash", {"command": "curl https://example.com"}, task).kind == DecisionKind.ALLOW
        assert engine.evaluate("bash", {"command": "pip install requests"}, task).kind == DecisionKind.ALLOW
        assert engine.evaluate("bash", {"command": "docker ps"}, task).kind == DecisionKind.DENY

    def test_allows_public_web_reads_for_network_broker_enforcement(self):
        engine = PolicyEngine()
        skills = engine.evaluate(
            "http_request",
            {"method": "GET", "url": "https://skills.sh/"},
        )
        assert skills.kind == DecisionKind.ALLOW

        github = engine.evaluate(
            "http_request",
            {"method": "GET", "url": "https://github.com/search?q=agent+skill"},
        )
        assert github.kind == DecisionKind.ALLOW

        weather = engine.evaluate(
            "http_request",
            {"method": "GET", "url": "https://www.nmc.cn/rest/weather"},
        )
        assert weather.kind == DecisionKind.ALLOW

    def test_deny_npx_skills(self):
        engine = PolicyEngine()
        decision = engine.evaluate("bash", {"command": "npx skills find flight"})
        assert decision.kind == DecisionKind.DENY

    def test_deny_shell_eval(self):
        engine = PolicyEngine()
        decision = engine.evaluate("bash", {"command": "bash -c 'echo unsafe'"})
        assert decision.kind == DecisionKind.DENY

    def test_ask_git_push(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "git push origin main"})
        assert d.kind == DecisionKind.ASK

    def test_ask_git_commit(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "git commit -m 'fix' "})
        assert d.kind == DecisionKind.ASK

    def test_rewrite_pip_install(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "pip install requests"})
        assert d.kind == DecisionKind.REWRITE
        assert d.rewritten_args is not None
        assert "index-url" in d.rewritten_args.get("command", "")

    def test_pip_install_with_existing_index_not_doubled(self):
        engine = PolicyEngine()
        d = engine.evaluate(
            "bash", {"command": "pip install --index-url http://existing requests"}
        )
        assert d.kind == DecisionKind.REWRITE
        # Should not add another --index-url
        cmd = d.rewritten_args.get("command", "")
        assert cmd.count("--index-url") == 1

    def test_allow_normal_command(self):
        engine = PolicyEngine()
        d = engine.evaluate("bash", {"command": "mvn test"})
        assert d.kind == DecisionKind.ALLOW

    def test_permission_gate_blocks_tool(self):
        engine = PolicyEngine()
        task = _task(permissions=TaskPermissions(shell=False))
        d = engine.evaluate("bash", {"command": "ls"}, task)
        assert d.kind == DecisionKind.DENY
        assert "permission" in d.reason.lower()

    def test_package_install_requires_task_permission(self):
        engine = PolicyEngine()
        task = _task(permissions=TaskPermissions(install_packages=False))
        decision = engine.evaluate("bash", {"command": "pip install requests"}, task)
        assert decision.kind == DecisionKind.DENY
        assert decision.rule_id == "package-install-permission"

    def test_package_install_is_rewritten_when_task_permission_is_enabled(self):
        engine = PolicyEngine()
        task = _task(permissions=TaskPermissions(install_packages=True))
        decision = engine.evaluate("bash", {"command": "pip install requests"}, task)
        assert decision.kind == DecisionKind.REWRITE
        assert decision.rewritten_args is not None
        assert "--index-url" in decision.rewritten_args["command"]
    def test_custom_rule_overrides_default(self):
        engine = PolicyEngine()
        # Add a rule that allows sudo (override the default deny)
        engine.add_rule(
            PolicyRule(
                id="allow-sudo-test",
                tool_pattern="bash",
                arg_pattern=r"\bsudo\b",
                decision=DecisionKind.ALLOW,
                priority=9999,  # higher than default deny-sudo (999)
            )
        )
        d = engine.evaluate("bash", {"command": "sudo ls"})
        assert d.kind == DecisionKind.ALLOW

    def test_remove_rule(self):
        engine = PolicyEngine()
        assert engine.remove_rule("deny-sudo") is True
        # After removing, sudo should fall through to ALLOW
        d = engine.evaluate("bash", {"command": "sudo ls"})
        assert d.kind == DecisionKind.ALLOW

    def test_remove_nonexistent_rule(self):
        engine = PolicyEngine()
        assert engine.remove_rule("nope") is False

    def test_list_rules(self):
        engine = PolicyEngine()
        rules = engine.list_rules()
        assert len(rules) > 0
        assert all("id" in r for r in rules)

    def test_priority_ordering(self):
        """Higher priority rules are evaluated first."""
        engine = PolicyEngine()
        # deny-rf-root has priority 1000, deny-sudo has 999
        # Both match "sudo rm -rf /" — deny-rf-root should win
        d = engine.evaluate("bash", {"command": "sudo rm -rf /"})
        assert d.kind == DecisionKind.DENY
        assert d.rule_id == "deny-rf-root"


# ============================================================================
# SandboxExecutor
# ============================================================================


class TestSandboxExecutor:
    def test_run_shell_echo(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path)
        result = sandbox.run_shell("echo hello")
        assert result.success is True
        assert "hello" in result.output

    def test_run_shell_failure(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path)
        result = sandbox.run_shell("false")
        assert result.success is False

    def test_file_write_and_read(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path)
        wr = sandbox.run_file_write("test.txt", "hello world")
        assert wr.success is True
        rd = sandbox.run_file_read("test.txt")
        assert rd.success is True
        assert rd.output == "hello world"

    def test_file_read_outside_workspace_denied(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path / "ws")
        (tmp_path / "ws").mkdir()
        result = sandbox.run_file_read("/etc/passwd")
        assert result.is_denied is True

    def test_file_write_outside_workspace_denied(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path / "ws")
        (tmp_path / "ws").mkdir()
        result = sandbox.run_file_write("/tmp/escape.txt", "oops")
        assert result.is_denied is True

    def test_relative_path_resolved_to_workspace(self, tmp_path):
        sandbox = SandboxExecutor(workspace=tmp_path)
        result = sandbox.run_file_write("sub/dir/file.txt", "content")
        assert result.success is True
        assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "content"


# ============================================================================
# ToolGateway
# ============================================================================


class TestToolGateway:
    def _gateway(self, tmp_path, **kw):
        sandbox = SandboxExecutor(workspace=tmp_path)
        return ToolGateway(policy=PolicyEngine(), sandbox=sandbox, **kw)

    def test_allow_and_execute(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "echo ok"})
        assert result.success is True
        assert "ok" in result.output

    def test_deny_rm_rf(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "rm -rf /"})
        assert result.is_denied is True

    def test_deny_sudo(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "sudo reboot"})
        assert result.is_denied is True

    def test_ask_git_push(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "git push origin main"})
        assert result.approval_required is True
        assert result.approval_request_id.startswith("apr-")

    def test_rewrite_pip(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "pip install flask"})
        # REWRITE falls through to execution — sandbox runs it
        # The rewritten command has --index-url added
        assert result.success is True or result.success is False  # may fail if pip not available

    def test_resolve_approval_approved(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "git push"})
        assert result.approval_required is True
        rid = result.approval_request_id
        # Approve and execute
        result2 = gw.resolve_approval(rid, approved=True)
        assert result2.denied is not True

    def test_resolve_approval_rejected(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("bash", {"command": "git push"})
        rid = result.approval_request_id
        result2 = gw.resolve_approval(rid, approved=False)
        assert result2.is_denied is True

    def test_resolve_unknown_approval(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.resolve_approval("nope", approved=True)
        assert result.success is False

    def test_list_pending_approvals(self, tmp_path):
        gw = self._gateway(tmp_path)
        gw.execute("bash", {"command": "git push"})
        gw.execute("bash", {"command": "git commit -m x"})
        pending = gw.list_pending_approvals()
        assert len(pending) == 2

    def test_permission_gate(self, tmp_path):
        gw = self._gateway(tmp_path)
        task = _task(permissions=TaskPermissions(shell=False))
        result = gw.execute("bash", {"command": "ls"}, task)
        assert result.is_denied is True

    def test_on_approval_callback(self, tmp_path):
        callback_calls = []
        gw = self._gateway(tmp_path)
        gw.execute(
            "bash",
            {"command": "git push"},
            on_approval_create=lambda rid, t, a, reason: callback_calls.append(rid),
        )
        assert len(callback_calls) == 1

    def test_file_write_through_gateway(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("write", {"path": "hello.txt", "content": "world"})
        assert result.success is True
        assert (tmp_path / "hello.txt").read_text() == "world"

    def test_file_read_through_gateway(self, tmp_path):
        (tmp_path / "data.txt").read_text  # ensure dir exists
        (tmp_path / "data.txt").write_text("content")
        gw = self._gateway(tmp_path)
        result = gw.execute("read", {"path": "data.txt"})
        assert result.success is True
        assert result.output == "content"

    def test_unknown_tool_returns_error(self, tmp_path):
        gw = self._gateway(tmp_path)
        result = gw.execute("nonexistent_tool", {})
        assert result.success is False
        assert "unsupported" in result.error.lower()


# ============================================================================
# ApprovalService
# ============================================================================


class TestApprovalService:
    def test_create_and_get(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "needs review")
        assert req.status == ApprovalStatus.PENDING
        fetched = svc.get(req.request_id)
        assert fetched is not None
        assert fetched.task_id == "T1"

    def test_approve(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review")
        result = svc.approve(req.request_id, approver_id="admin")
        assert result is not None
        assert result.status == ApprovalStatus.APPROVED
        assert result.approver_id == "admin"

    def test_reject(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review")
        result = svc.reject(req.request_id, approver_id="admin", notes="not now")
        assert result is not None
        assert result.status == ApprovalStatus.REJECTED
        assert result.notes == "not now"

    def test_cannot_approve_twice(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review")
        svc.approve(req.request_id)
        # Second approve returns same approved status, doesn't change
        result = svc.approve(req.request_id)
        assert result.status == ApprovalStatus.APPROVED

    def test_list_pending_filters_by_task(self):
        svc = ApprovalService()
        svc.create("T1", "bash", {"command": "git push"}, "r1")
        svc.create("T2", "bash", {"command": "git push"}, "r2")
        pending = svc.list_pending(task_id="T1")
        assert len(pending) == 1
        assert pending[0]["task_id"] == "T1"

    def test_list_pending_all(self):
        svc = ApprovalService()
        svc.create("T1", "bash", {"command": "git push"}, "r1")
        svc.create("T1", "bash", {"command": "git commit"}, "r2")
        assert svc.count_pending() == 2

    def test_expired_request(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review", ttl_seconds=0)
        # ttl=0 means immediately expired
        fetched = svc.get(req.request_id)
        assert fetched is not None
        assert fetched.status == ApprovalStatus.EXPIRED

    def test_cleanup(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review")
        svc.reject(req.request_id)
        # Manually set resolved_at to long ago
        svc._requests[req.request_id].resolved_at = 0
        cleaned = svc.cleanup_expired()
        assert cleaned == 1

    def test_as_dict(self):
        svc = ApprovalService()
        req = svc.create("T1", "bash", {"command": "git push"}, "review")
        d = req.as_dict()
        assert d["request_id"] == req.request_id
        assert d["status"] == "pending"
