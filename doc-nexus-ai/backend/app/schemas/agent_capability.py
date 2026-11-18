from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentCapabilityRequest(BaseModel):
    """Arguments sent by the fixed Pi capability extension."""

    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, max_length=128)
