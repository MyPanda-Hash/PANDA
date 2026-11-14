"""PolicyEngine — Execution governance for agent tool calls.

Every tool call from the agent runtime passes through the PolicyEngine
*before* execution.  The engine evaluates a prioritised rule list and
returns one of four decisions: ALLOW, DENY, ASK (human approval), or
REWRITE (modify arguments then proceed).

This module is *purely additive* — it does not modify any existing code.
The ToolGateway (gateway.py) invokes the engine; the existing
``tool_policy.ts`` prompt-level guard continues to work as a soft hint.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.services.harness.task import AgentTask, TaskPermissions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------

class DecisionKind(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"
    REWRITE = "REWRITE"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Outcome of a policy evaluation."""

    kind: DecisionKind
    reason: str = ""
    rule_id: str = ""
    rewritten_tool: str | None = None
    rewritten_args: dict[str, Any] | None = None
    approval_request_id: str | None = None

    # convenience factories ---------------------------------------------------

    @classmethod
    def allow(cls, *, rule_id: str = "") -> PolicyDecision:
        return cls(kind=DecisionKind.ALLOW, rule_id=rule_id)

    @classmethod
    def deny(cls, reason: str, *, rule_id: str = "") -> PolicyDecision:
        return cls(kind=DecisionKind.DENY, reason=reason, rule_id=rule_id)

    @classmethod
    def ask(cls, reason: str, *, rule_id: str = "") -> PolicyDecision:
        return cls(kind=DecisionKind.ASK, reason=reason, rule_id=rule_id)

    @classmethod
    def rewrite(
        cls,
        reason: str,
        *,
        rewritten_tool: str | None = None,
        rewritten_args: dict[str, Any] | None = None,
        rule_id: str = "",
    ) -> PolicyDecision:
        return cls(
            kind=DecisionKind.REWRITE,
            reason=reason,
            rewritten_tool=rewritten_tool,
            rewritten_args=rewritten_args,
            rule_id=rule_id,
        )


# ---------------------------------------------------------------------------
# Policy rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A single policy rule evaluated against tool calls.

    Rules are evaluated in *descending* ``priority`` order.  The first rule
    whose conditions match produces a decision; if no rule matches, the
    engine falls through to the default (ALLOW).
    """

    id: str
    tool_pattern: str  # fnmatch pattern, e.g. "shell", "*", "git_*"
    arg_pattern: str = ""  # regex on the stringified arguments; "" = match any
    decision: DecisionKind = DecisionKind.DENY
    reason: str = ""
    priority: int = 0
    enabled: bool = True
    rewritten_tool: str | None = None
    rewritten_args: dict[str, Any] | None = None

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return True if this rule matches the given tool call."""
        if not self.enabled:
            return False
        if not fnmatch.fnmatchcase(tool_name, self.tool_pattern):
            return False
        if self.arg_pattern:
            arg_str = " ".join(f"{k}={v}" for k, v in sorted(arguments.items()))
            if not re.search(self.arg_pattern, arg_str, re.IGNORECASE):
                return False
        return True


# ---------------------------------------------------------------------------
# Built-in default rules
# ---------------------------------------------------------------------------

def _default_rules() -> list[PolicyRule]:
    """Default enterprise policy rules.

    These are always loaded first; user-defined rules take precedence
    (higher priority) over them.
    """
    return [
        # -- DENY: dangerous commands ------------------------------------------
        PolicyRule(
            id="deny-rf-root",
            tool_pattern="bash",
            arg_pattern=r"rm\s+-rf\s+/",
            decision=DecisionKind.DENY,
            reason="rm -rf / is forbidden",
            priority=1000,
        ),
        PolicyRule(
            id="deny-sudo",
            tool_pattern="bash",
            arg_pattern=r"\bsudo\b",
            decision=DecisionKind.DENY,
            reason="sudo is forbidden",
            priority=999,
        ),
        PolicyRule(
            id="deny-write-etc",
            tool_pattern="bash",
            arg_pattern=r">\s*/etc/",
            decision=DecisionKind.DENY,
            reason="writing to /etc is forbidden",
            priority=998,
        ),
        PolicyRule(
            id="deny-network",
            tool_pattern="bash",
            arg_pattern=r"\b(?:curl|wget|nc|ncat|netcat|socat|nmap)\b",
            decision=DecisionKind.DENY,
            reason="network requests from session are forbidden",
            priority=997,
        ),
        PolicyRule(
            id="deny-interpreter",
            tool_pattern="bash",
            arg_pattern=r"\b(?:python3?|pypy3?|node(?:js)?|perl|ruby|php|lua)\b",
            decision=DecisionKind.DENY,
            reason="direct interpreter execution is forbidden; use an approved Skill",
            priority=996,
        ),
        PolicyRule(
            id="deny-shell-eval",
            tool_pattern="bash",
            arg_pattern=r"\b(?:bash|sh|zsh)\s+-c\b",
            decision=DecisionKind.DENY,
            reason="shell command chaining is forbidden",
            priority=995,
        ),
        PolicyRule(
            id="deny-ssh",
            tool_pattern="bash",
            arg_pattern=r"\b(?:ssh|scp|sftp|docker|podman|kubectl)\b",
            decision=DecisionKind.DENY,
            reason="external host access is forbidden",
            priority=996,
        ),
        PolicyRule(
            id="deny-pkg-install",
            tool_pattern="bash",
            arg_pattern=r"\b(?:pip\d*|npm|npx|pnpm|yarn|apt(?:-get)?|yum|dnf|apk)\s+",
            decision=DecisionKind.DENY,
            reason="package installation is forbidden",
            priority=995,
        ),
        PolicyRule(
            id="deny-system-ctl",
            tool_pattern="bash",
            arg_pattern=r"\b(?:systemctl|service|launchctl|mount|umount|mkfs|fdisk)\b",
            decision=DecisionKind.DENY,
            reason="system control is forbidden",
            priority=994,
        ),
        # -- ASK: git push -----------------------------------------------------
        PolicyRule(
            id="ask-git-push",
            tool_pattern="bash",
            arg_pattern=r"\bgit\s+push\b",
            decision=DecisionKind.ASK,
            reason="git push requires approval",
            priority=500,
        ),
        PolicyRule(
            id="ask-git-commit",
            tool_pattern="bash",
            arg_pattern=r"\bgit\s+commit\b",
            decision=DecisionKind.ASK,
            reason="git commit requires approval",
            priority=499,
        ),
        # -- REWRITE: pip install to internal mirror ---------------------------
        PolicyRule(
            id="rewrite-pip-mirror",
            tool_pattern="bash",
            arg_pattern=r"\bpip\s+install\b",
            decision=DecisionKind.REWRITE,
            reason="rewritten to use internal PyPI mirror",
            priority=996,
            rewritten_args={"command": None},  # placeholder; real rewrite in engine
        ),
        # Package installation is only reachable after the permission gate below.
    ]


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PolicyEngine:
    """Evaluates tool calls against an ordered rule list.

    Usage::

        engine = PolicyEngine(rules=[...])  # or PolicyEngine() for defaults
        decision = engine.evaluate("bash", {"command": "rm -rf /"}, task)
    """

    rules: list[PolicyRule] = field(default_factory=_default_rules)
    _internal_pypi: str = "https://pypi.tuna.tsinghua.edu.cn/simple"

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        task: AgentTask | None = None,
    ) -> PolicyDecision:
        """Evaluate a tool call and return a policy decision.

        Evaluation order:
        1. Permission check (if task is provided)
        2. Rules sorted by priority (descending)
        3. Default: ALLOW
        """
        # In the disposable Pi container, the container boundary replaces
        # host-side interpreter/network/package blocking.  Keep the task
        # permission gate and the host-control rules (docker, sudo, mounts,
        # database, and credential access) active.
        sandbox = bool(task is not None and task.permissions.sandbox)
        if task is not None:
            if not task.permissions.is_tool_allowed(tool_name):
                return PolicyDecision.deny(
                    f"permission denied: {tool_name} not enabled for this task",
                    rule_id="permission-gate",
                )
        if (
            not sandbox
            and tool_name == "bash"
            and re.search(r"\b(?:pip\d*|pipenv)\s+install\b", str(arguments.get("command", "")), re.IGNORECASE)
            and task is not None
            and not task.permissions.install_packages
        ):
            return PolicyDecision.deny(
                "permission denied: package installation is not enabled for this task",
                rule_id="package-install-permission",
            )

        # 2. Rule evaluation (descending priority)
        sorted_rules = sorted(self.rules, key=lambda r: r.priority, reverse=True)
        sandbox_relaxed_rules = {
            "deny-network",
            "deny-interpreter",
            "deny-pkg-install",
            "rewrite-pip-mirror",
        }
        for rule in sorted_rules:
            if sandbox and rule.id in sandbox_relaxed_rules:
                continue
            if rule.matches(tool_name, arguments):
                decision = self._apply_rule(rule, arguments)
                logger.debug(
                    "Policy hit: rule=%s tool=%s decision=%s",
                    rule.id,
                    tool_name,
                    decision.kind.value,
                )
                return decision

        # 3. Default: allow
        return PolicyDecision.allow()

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a user-defined rule."""
        self.rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID.  Returns True if found."""
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        return len(self.rules) < before

    def list_rules(self) -> list[dict[str, Any]]:
        """Return all rules as dicts (for API/UI)."""
        return [
            {
                "id": r.id,
                "tool_pattern": r.tool_pattern,
                "arg_pattern": r.arg_pattern,
                "decision": r.decision.value,
                "reason": r.reason,
                "priority": r.priority,
                "enabled": r.enabled,
            }
            for r in sorted(self.rules, key=lambda r: r.priority, reverse=True)
        ]

    # -- internals -------------------------------------------------------------

    def _apply_rule(
        self, rule: PolicyRule, arguments: dict[str, Any]
    ) -> PolicyDecision:
        """Convert a matched rule into a concrete PolicyDecision."""
        if rule.decision == DecisionKind.ALLOW:
            return PolicyDecision.allow(rule_id=rule.id)
        if rule.decision == DecisionKind.DENY:
            return PolicyDecision.deny(rule.reason, rule_id=rule.id)
        if rule.decision == DecisionKind.ASK:
            return PolicyDecision.ask(rule.reason, rule_id=rule.id)
        if rule.decision == DecisionKind.REWRITE:
            rewritten = self._rewrite_arguments(rule, arguments)
            return PolicyDecision.rewrite(
                rule.reason,
                rewritten_tool=rule.rewritten_tool,
                rewritten_args=rewritten,
                rule_id=rule.id,
            )
        return PolicyDecision.allow(rule_id=rule.id)

    def _rewrite_arguments(
        self, rule: PolicyRule, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply rewrite logic for REWRITE decisions."""
        # pip install → internal mirror
        if "pip" in rule.id and "command" in arguments:
            cmd = arguments["command"]
            if "--index-url" not in cmd and "-i " not in cmd:
                cmd = re.sub(
                    r"(pip\s+install)",
                    f"\\1 --index-url {self._internal_pypi}",
                    cmd,
                )
            return {**arguments, "command": cmd}
        # Generic: merge rule's rewritten_args
        if rule.rewritten_args:
            return {**arguments, **{
                k: v for k, v in rule.rewritten_args.items() if v is not None
            }}
        return arguments
