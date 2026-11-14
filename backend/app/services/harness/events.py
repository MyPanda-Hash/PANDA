"""EventBridge — Unified event conversion for all agent runtimes.

Converts runtime-specific events (Pi JSONL, future Codex, etc.) into a
single ``HarnessEvent`` envelope that the frontend and audit layer consume.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """Unified event emitted by the Harness to the frontend / audit.

    Every event has a ``type``, a ``task_id``, a timestamp, and an
    optional payload.  The frontend renders events by type; the audit
    logger serialises them for compliance.
    """

    type: str
    task_id: str
    timestamp: float = field(default_factory=time.time)
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Canonical event types
    # "thinking"       — agent is reasoning
    # "message"        — agent text output
    # "tool_call"      — tool invocation request
    # "tool_result"    — tool execution result
    # "approval"       — approval required / resolved
    # "verification"   — verification running / passed / failed
    # "completed"      — task finished successfully
    # "failed"         — task finished with error
    # "policy_hit"     — policy rule matched
    # "limit_warning"  — approaching resource limit

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "metadata": self.metadata,
        }


class EventBridge:
    """Collects and converts events from any source into :class:`HarnessEvent`.

    Usage::

        bridge = EventBridge()
        bridge.emit("thinking", "TASK-1", content="analysing code…")
        events = bridge.drain()
    """

    def __init__(self) -> None:
        self._buffer: list[HarnessEvent] = []

    def emit(
        self,
        event_type: str,
        task_id: str,
        *,
        content: str = "",
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessEvent:
        """Create and buffer a unified event."""
        event = HarnessEvent(
            type=event_type,
            task_id=task_id,
            content=content,
            tool_name=tool_name,
            tool_args=tool_args or {},
            metadata=metadata or {},
        )
        self._buffer.append(event)
        return event

    def drain(self) -> list[HarnessEvent]:
        """Return all buffered events and clear the buffer."""
        events = list(self._buffer)
        self._buffer.clear()
        return events

    def peek(self) -> list[HarnessEvent]:
        """Return buffered events without clearing."""
        return list(self._buffer)

    @property
    def count(self) -> int:
        return len(self._buffer)

    # -- Pi-specific conversion ------------------------------------------------

    def convert_pi_event(
        self, raw: dict[str, Any], task_id: str
    ) -> HarnessEvent | None:
        """Convert a Pi JSONL event to a unified :class:`HarnessEvent`.

        Returns ``None`` for events the Harness does not need to surface.
        """
        etype = raw.get("type", "")

        if etype == "thinking":
            return self.emit("thinking", task_id, content=raw.get("content", ""))
        if etype == "text":
            return self.emit(
                "message", task_id, content=raw.get("content", "") or raw.get("text", "")
            )
        if etype == "tool_use":
            return self.emit(
                "tool_call",
                task_id,
                tool_name=raw.get("name", ""),
                tool_args=raw.get("input", {}),
            )
        if etype == "tool_result":
            return self.emit(
                "tool_result",
                task_id,
                content=str(raw.get("content", "")),
                tool_name=raw.get("name", ""),
            )
        if etype == "error":
            return self.emit("failed", task_id, content=raw.get("message", ""))

        # start, end, session_info, etc. → skip
        return None
