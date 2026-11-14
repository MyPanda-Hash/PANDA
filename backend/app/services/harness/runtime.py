"""AgentRuntime — Unified interface for agent execution engines.

Defines the abstract protocol that all runtime adapters must implement,
and provides the Pi-specific adapter that wraps the existing
``PiRpcRunner`` behind the new interface.

Existing callers (Chat, Writing) continue to use ``LocalPiAgentClient``
directly — this adapter is used by the Harness orchestrator for new
``AgentTask`` flows.
"""

import asyncio
import inspect
import logging
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings as app_settings
from app.services.harness.sandbox import SandboxManager
from app.services.harness.task import AgentTask
from app.services.pi_runtime.config import (
    PI_RUNTIME_AGENT_ID,
    CredentialStore,
    PiRuntimeSettings,
    resolve_pi_model,
)
from app.services.pi_runtime.persona import load_pi_persona
from app.services.pi_runtime.runner import PiRpcRunner, PiRunResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentEvent — unified event envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Unified event emitted by any runtime adapter.

    The frontend and audit layer consume only this type — they never see
    Pi-specific or Codex-specific event shapes.
    """

    type: str  # thinking | message | tool_call | tool_result | completed | failed
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AgentSession — opaque session handle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentSession:
    """Opaque handle returned by ``create_session`` and passed to ``run``."""

    session_id: str
    runtime_type: str  # "pi" | "codex" | "claude"
    workspace: Path
    native_session: Any = None  # runtime-specific internals


# ---------------------------------------------------------------------------
# AgentRuntime protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRuntime(Protocol):
    """Abstract interface for agent execution engines."""

    async def create_session(
        self,
        task: AgentTask,
        workspace: Path,
    ) -> AgentSession:
        """Create a session bound to *task* and *workspace*."""
        ...

    async def run(
        self,
        session: AgentSession,
        prompt: str,
        *,
        on_event: Any | None = None,
    ) -> AsyncIterable[AgentEvent]:
        """Execute the agent loop, yielding events."""
        ...

    async def cancel(self, session: AgentSession) -> None:
        """Cancel a running session."""
        ...


# ---------------------------------------------------------------------------
# PiAgentAdapter — wraps existing PiRpcRunner
# ---------------------------------------------------------------------------


class _CredentialStore(CredentialStore):
    def __init__(self, credential: Any) -> None:
        self._credential = credential

    def get_credential(self, credential_id: str) -> Any:
        return self._credential if credential_id == "local" else None


@dataclass(slots=True)
class PiAgentAdapter:
    """Adapts Pi RPC to the runtime-neutral AgentEvent stream.

    This adapter:
    * resolves the model endpoint (same logic as ``LocalPiAgentClient``)
    * creates a Pi session with ``cwd`` pointing to the sandbox workspace
    * converts Pi JSONL events to unified :class:`AgentEvent` instances
    * forwards events while the Pi process is still running

    It keeps Pi-specific protocol details inside the adapter.
    """

    runtime_settings: PiRuntimeSettings | None = None
    sandbox: SandboxManager | None = None

    async def create_session(
        self,
        task: AgentTask,
        workspace: Path,
    ) -> AgentSession:
        """Create a Pi session for *task*.

        The session is not yet started — call ``run`` to begin execution.
        """
        runtime = self.runtime_settings or PiRuntimeSettings.from_env()
        return AgentSession(
            session_id=f"harness-{task.task_id}",
            runtime_type="pi",
            workspace=workspace,
            native_session={
                "runtime": runtime,
                "task": task,
            },
        )
    async def run(
        self,
        session: AgentSession,
        prompt: str,
        *,
        on_event: Any | None = None,
    ) -> AsyncIterable[AgentEvent]:
        """Run Pi and yield unified events as JSONL records arrive."""
        task: AgentTask = session.native_session["task"]
        runtime: PiRuntimeSettings = session.native_session["runtime"]

        agent, resolved = self._build_pi_agent(task, runtime)
        runner = PiRpcRunner(runtime)
        raw_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def receive(raw_event: dict[str, Any]) -> None:
            await raw_events.put(raw_event)

        runner_task = asyncio.create_task(
            runner.run(
                agent=agent,
                resolved_model=resolved,
                session_id=session.session_id,
                prompt=prompt,
                on_event=receive,
                task_id=task.task_id,
                gateway_tenant_id=task.tenant_id,
                gateway_user_id=task.user_id,
                collect_generated_files=True,
            )
        )
        session.native_session["runner_task"] = runner_task
        try:
            while not runner_task.done() or not raw_events.empty():
                try:
                    raw_event = await asyncio.wait_for(raw_events.get(), timeout=0.25)
                except TimeoutError:
                    continue
                unified = _convert_pi_event(raw_event)
                if unified is not None:
                    yield unified
                if on_event is not None:
                    try:
                        callback_result = on_event(raw_event)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    except Exception:
                        logger.warning("Pi runtime event callback failed", exc_info=True)
            result: PiRunResult = await runner_task
        except Exception as exc:
            if not runner_task.done():
                runner_task.cancel()
                await asyncio.gather(runner_task, return_exceptions=True)
            yield AgentEvent(type="failed", content=str(exc))
            return
        finally:
            if not runner_task.done():
                runner_task.cancel()
                await asyncio.gather(runner_task, return_exceptions=True)
            session.native_session.pop("runner_task", None)

        yield AgentEvent(
            type="completed",
            content=result.reply,
            metadata={
                "token_usage": result.token_usage,
                "events_count": len(result.events),
                "generated_files": result.generated_files,
            },
        )

    async def cancel(self, session: AgentSession) -> None:
        """Cancel the active Pi task and let the runner send ``abort``."""
        running = session.native_session.get("runner_task")
        if isinstance(running, asyncio.Task) and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    # -- internals -------------------------------------------------------------

    def _build_pi_agent(
        self,
        task: AgentTask,
        runtime: PiRuntimeSettings,
    ) -> tuple[Any, Any]:
        """Build the agent + resolved model objects that PiRpcRunner expects.

        This mirrors ``pi_local._agent`` and ``pi_local._resolve`` but uses
        the AgentTask's permissions to decide which tools to register.
        """
        from app.services.pi_local import resolve_local_pi_endpoint

        endpoint = resolve_local_pi_endpoint(
            capability="chat",
            user_id=task.user_id,
        )
        persona = load_pi_persona()

        tools: list[str] = ["read"]
        perms = task.permissions
        if perms.file_read:
            tools.extend(["read", "find", "ls", "grep"])
        if perms.file_write:
            tools.extend(["write", "edit"])
        if perms.shell:
            tools.append("bash")
        if perms.skill_install_request:
            tools.append("request_skill_install")
        tools.append("search_skill_catalog")
        if perms.network:
            tools.append("http_request")
        try:
            configured_timeout = max(1, int(app_settings.pi_agent_timeout_seconds))
        except (TypeError, ValueError):
            configured_timeout = 900
        pi_timeout = min(
            configured_timeout,
            max(1, int(task.limits.timeout_seconds)),
        )

        agent = SimpleNamespace(
            id=PI_RUNTIME_AGENT_ID,
            name="doc-nexus-assistant",
            tenant_id=task.tenant_id or "_system",
            system_message=persona.system_prompt,
            model_ref=SimpleNamespace(credential_id="local", model=endpoint.model),
            pi_config=SimpleNamespace(
                thinking_level="medium",
                tools=tuple(tools),
                timeout_sec=pi_timeout,
                enable_skills=True,
            ),
        )

        credential = SimpleNamespace(
            provider=endpoint.adapter,
            adapter=endpoint.adapter,
            api_protocol=endpoint.api_protocol,
            api_key=endpoint.api_key,
            allow_anonymous_api_key=endpoint.allow_anonymous_api_key,
            base_url=endpoint.base_url,
            default_model=endpoint.model,
            azure_endpoint=(
                endpoint.base_url
                if hasattr(endpoint, "adapter") and endpoint.adapter == "azure"
                else ""
            ),
            azure_deployment=(
                endpoint.model
                if hasattr(endpoint, "adapter") and endpoint.adapter == "azure"
                else ""
            ),
            azure_api_version=getattr(endpoint, "api_version", ""),
        )
        store = _CredentialStore(credential)
        resolved = resolve_pi_model(agent, store)
        return agent, resolved


# ---------------------------------------------------------------------------
# Event conversion
# ---------------------------------------------------------------------------


def _convert_pi_event(raw: dict[str, Any]) -> AgentEvent | None:
    """Convert Pi RPC JSONL events to the runtime-neutral event envelope."""
    etype = str(raw.get("type") or "")
    if etype == "message_update":
        delta = raw.get("assistantMessageEvent")
        if not isinstance(delta, dict):
            return None
        delta_type = str(delta.get("type") or "")
        if delta_type == "text_delta":
            return AgentEvent(type="message", content=str(delta.get("delta") or ""))
        if delta_type == "thinking_delta":
            return AgentEvent(type="thinking", content=str(delta.get("delta") or ""))
        if delta_type == "toolcall_start":
            return AgentEvent(
                type="tool_call",
                tool_name=str(delta.get("toolName") or ""),
                metadata={"tool_call_id": str(delta.get("id") or "")},
            )
        if delta_type == "toolcall_end":
            call = delta.get("toolCall")
            if isinstance(call, dict):
                return AgentEvent(
                    type="tool_call",
                    tool_name=str(call.get("name") or call.get("toolName") or ""),
                    tool_args=(
                        call.get("arguments")
                        if isinstance(call.get("arguments"), dict)
                        else {}
                    ),
                    metadata={"tool_call_id": str(call.get("id") or "")},
                )
        return None
    if etype == "tool_execution_start":
        return AgentEvent(
            type="tool_call",
            tool_name=str(raw.get("toolName") or ""),
            tool_args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
            metadata={"tool_call_id": str(raw.get("toolCallId") or "")},
        )
    if etype == "tool_execution_update":
        partial = raw.get("partialResult")
        return AgentEvent(
            type="tool_result",
            content=str(partial or ""),
            tool_name=str(raw.get("toolName") or ""),
            metadata={"tool_call_id": str(raw.get("toolCallId") or "")},
        )
    if etype == "tool_execution_end":
        return AgentEvent(
            type="tool_result",
            content=str(raw.get("result") or ""),
            tool_name=str(raw.get("toolName") or ""),
            metadata={
                "tool_call_id": str(raw.get("toolCallId") or ""),
                "is_error": bool(raw.get("isError")),
            },
        )
    if etype in {"agent_start", "turn_start"}:
        return AgentEvent(type="message", metadata={"event": etype})
    if etype in {"agent_end", "turn_end", "agent_settled"}:
        return None
    # Compatibility with the legacy provider event vocabulary.
    if etype == "thinking":
        return AgentEvent(type="thinking", content=str(raw.get("content") or ""))
    if etype == "text":
        return AgentEvent(
            type="message",
            content=str(raw.get("content") or raw.get("text") or ""),
        )
    if etype == "tool_use":
        return AgentEvent(
            type="tool_call",
            tool_name=str(raw.get("name") or ""),
            tool_args=raw.get("input") if isinstance(raw.get("input"), dict) else {},
        )
    if etype == "tool_result":
        return AgentEvent(
            type="tool_result",
            content=str(raw.get("content") or ""),
            tool_name=str(raw.get("name") or ""),
        )
    if etype in {"error", "extension_error"}:
        return AgentEvent(
            type="failed",
            content=str(raw.get("message") or raw.get("error") or ""),
        )
    return None
