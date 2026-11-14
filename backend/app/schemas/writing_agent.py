from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamRunInput(StrictModel):
    writing_operation_id: str
    project_id: str
    outline_revision: int | None = None
    chapter_id: str | None = None
    candidate_revision: int | None = None


class TeamRunToolContext(StrictModel):
    scope_token: str
    scope_hash: str
    budget_reservation_id: str | None = None


class TeamRunCreate(StrictModel):
    team_id: str
    team_version: int = Field(ge=1)
    idempotency_key: str
    start_mode: Literal["immediate", "explicit"] = "immediate"
    workflow: Literal["outline", "document", "rewrite", "verify"]
    input: TeamRunInput
    tool_context: TeamRunToolContext


class TeamRunView(StrictModel):
    platform_run_id: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelling", "cancelled"]
    team_id: str
    team_version: int
    workflow: Literal["outline", "document", "rewrite", "verify"]
    event_cursor: int
    trace_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class TeamRunEvent(StrictModel):
    event_id: int
    platform_run_id: str
    type: str
    stage: str | None = None
    agent_name: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    action_key: str | None = None
    chapter_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class TeamRunEventList(StrictModel):
    items: list[TeamRunEvent]
    next_cursor: int


class ToolEnvelope(StrictModel):
    platform_run_id: str
    writing_operation_id: str
    project_id: str
    owner_user_id: str | None = None
    scope_hash: str
    team_id: str
    team_version: int
    workflow: Literal["outline", "document", "rewrite", "verify"]
    stage: str
    agent_name: str
    tool_call_id: str
    action_key: str
    attempt: int = Field(ge=1)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_key")
    @classmethod
    def stable_relative_action_key(cls, value: str) -> str:
        if not value.strip() or value.startswith("/") or ".." in value:
            raise ValueError("invalid action_key")
        return value


class ToolError(StrictModel):
    code: str
    retryable: bool = False
    message: str = ""
    diagnostic_id: str | None = None


class ToolResult(StrictModel):
    ok: bool
    result_ref: str | None = None
    sha256: str | None = None
    summary: str | None = None
    inline: dict[str, Any] | list[Any] | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def validate_error_shape(self):
        if self.ok and self.error is not None:
            raise ValueError("successful ToolResult cannot include error")
        if not self.ok and self.error is None:
            raise ValueError("failed ToolResult requires error")
        return self
