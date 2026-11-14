"""Shared, safe evidence and retrieval response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GroundingStatus = Literal[
    "grounded", "partial", "knowledge_gap", "degraded", "not_requested"
]
EvidenceSupportState = Literal["supported", "uncertain", "unsupported"]
RetrievalChannel = Literal["lexical", "vector", "graph"]


class EvidenceLocationResponse(BaseModel):
    """A bounded, permission-safe pointer to evidence content."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(min_length=1, max_length=512)
    document_id: str = Field(min_length=1, max_length=128)
    document_version_id: str = Field(min_length=1, max_length=128)
    document_name: str = Field(default="文档", max_length=255)
    version_number: int = Field(default=1, ge=1)
    location_label: str = Field(default="", max_length=255)
    page_number: int | None = Field(default=None, ge=1)
    page_block_id: str | None = Field(default=None, max_length=128)
    bbox: tuple[float, float, float, float] | None = None
    excerpt: str = Field(default="", max_length=4_000)
    support_state: EvidenceSupportState = "uncertain"

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        x0, y0, x1, y1 = value
        if not all(0 <= item <= 1 for item in value):
            raise ValueError("bbox coordinates must be normalized to 0..1")
        if not (x0 < x1 and y0 < y1):
            raise ValueError("bbox must have positive width and height")
        return value

    @model_validator(mode="after")
    def _validate_page_location(self) -> "EvidenceLocationResponse":
        if self.page_block_id and self.page_number is None:
            raise ValueError("page_block_id requires page_number")
        return self


class RetrievalTraceResponse(BaseModel):
    """Stable metadata for explaining retrieval without storing user content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: str = Field(default="legacy", max_length=32)
    method: str = Field(default="pool_fallback", max_length=96)
    route_reason: str = Field(default="", max_length=255)
    pool_size: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    channels: dict[RetrievalChannel, bool] = Field(
        default_factory=lambda: {"lexical": False, "vector": False, "graph": False}
    )
    degraded: bool = False
    degradation_reason: str | None = Field(default=None, max_length=255)
    citation_count: int = Field(default=0, ge=0)
    grounding_status: GroundingStatus = "knowledge_gap"
    coverage: dict[str, Any] | None = None


def evidence_location_from_dict(value: dict[str, Any]) -> EvidenceLocationResponse:
    """Validate an internal evidence mapping using the public field names."""

    return EvidenceLocationResponse(**value)
