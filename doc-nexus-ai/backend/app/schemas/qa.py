from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evidence import RetrievalTraceResponse


class QaSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_ids: list[str] = Field(min_length=1, max_length=20)


class QaSessionResponse(BaseModel):
    id: str
    space_ids: list[str]
    scope_version: dict[str, int]
    created_at: datetime
    updated_at: datetime

class QaSessionSummaryResponse(BaseModel):
    id: str
    space_ids: list[str]
    turn_count: int
    last_question: str | None = None
    created_at: datetime
    updated_at: datetime


class QaSessionDetailResponse(QaSessionResponse):
    turns: list[QaTurnResponse]


class QaTurnCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)


class CitationResponse(BaseModel):
    id: str
    turn_id: str
    document_id: str
    document_version_id: str
    document_name: str
    version_number: int
    page_or_section: str
    excerpt: str
    support_state: Literal["supported", "uncertain", "unsupported"]
    location_label: str | None = None
    page_number: int | None = None
    page_block_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class QaEventResponse(BaseModel):
    type: Literal["status", "retrieval_summary", "answer_delta", "citation", "verification_summary", "usage"]
    status: str | None = None
    retrieval_summary: dict[str, Any] | None = None
    answer_delta: str | None = None
    citation: CitationResponse | None = None
    verification_summary: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None


class QaTurnResponse(BaseModel):
    id: str
    session_id: str
    question: str
    space_ids: list[str]
    scope_version: dict[str, int]
    status: Literal["completed", "knowledge_gap", "budget_exceeded", "source_permission_denied"]
    retrieval_summary: dict[str, Any]
    retrieval_trace: RetrievalTraceResponse | None = None
    answer: str | None
    citations: list[CitationResponse]
    verification_summary: dict[str, Any]
    usage: dict[str, Any]
    events: list[QaEventResponse]
    created_at: datetime
    completed_at: datetime


class CitationSourceResponse(BaseModel):
    citation: CitationResponse
    document_id: str
    document_version_id: str
    page_or_section: str
    excerpt: str
    page_image_path: str | None = None
