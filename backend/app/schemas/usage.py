from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UsageCallResponse(BaseModel):
    """One provider call contributing to an operation usage snapshot."""

    purpose: str
    provider: str = ""
    model: str | None = None
    endpoint_id: str | None = None
    input_tokens: int = 0
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: str = "0"
    latency_ms: int = 0
    status: str = "completed"
    error_code: str | None = None
    price_version_id: str | None = None
    currency: str | None = None


class UsageSnapshotResponse(BaseModel):
    provider: str
    model: str | None = None
    answer_model: str | None = None
    vision_model: str | None = None
    status: str = "completed"
    error_code: str | None = None
    input_tokens: int
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int
    total_tokens: int
    cost: str
    latency_ms: int
    route_mode: str | None = None
    vision_fallback_used: bool = False
    calls: list[UsageCallResponse] = Field(default_factory=list, max_length=8)


class UsageEventResponse(BaseModel):
    provider: str
    model: str | None = None
    purpose: str
    status: str
    input_tokens: int
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int
    total_tokens: int
    cost: str
    latency_ms: int


class OperationResponse(BaseModel):
    id: str
    feature: str
    action: str
    status: str
    model: str
    usage: UsageSnapshotResponse
    resource_path: str
    created_at: datetime
    completed_at: datetime | None


class OperationDetailResponse(OperationResponse):
    kind: str
    label: str
    retryable: bool = False
    retry_action: dict | None = None
    operation_id: str
    job_id: str | None = None
    usage_events: list[UsageEventResponse]


class OperationListResponse(BaseModel):
    items: list[OperationResponse]
    next_offset: int | None


class ArtifactPreviewResponse(BaseModel):
    """First-pages preview state for a finished export."""

    state: str
    pages: int = 0
    message: str | None = None


class ArtifactResponse(BaseModel):
    id: str
    name: str
    artifact_type: str
    status: str
    resource_path: str
    content_type: str | None
    content_size: int | None
    checksum_sha256: str | None
    source_version_ids: list[str]
    manifest: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    share_path: str | None = None
    share_scope: str | None = None
    source_title: str | None = None

class ArtifactRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)

class ArtifactShareRequest(BaseModel):
    share_scope: Literal["space", "all_internal"]

class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]


class UserUsageSummaryResponse(BaseModel):
    user_id: str
    username: str
    display_name: str
    status: str
    operation_count: int
    input_tokens: int
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int
    total_tokens: int
    cost: str
    by_feature: dict[str, int]
    by_model: dict[str, int] = {}
    by_model_detail: dict[str, dict] = {}


class ModelUsageSummaryResponse(BaseModel):
    model: str
    input_tokens: int = 0
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: str
    operation_count: int = 0


class AdminUsageSummaryResponse(BaseModel):
    items: list[UserUsageSummaryResponse]
    total_users: int
    users_with_usage: int
    total_operations: int
    total_input_tokens: int
    total_input_uncached_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_output_tokens: int
    total_tokens: int
    total_cost: str
    by_model: list[ModelUsageSummaryResponse] = []
