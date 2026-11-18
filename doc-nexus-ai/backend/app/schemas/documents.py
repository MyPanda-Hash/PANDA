from __future__ import annotations

from datetime import datetime
from typing import Literal


from pydantic import BaseModel, Field


class DocumentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category_id: str | None = Field(default=None, max_length=64)


class DocumentCategoryUpdateRequest(BaseModel):
    category_id: str | None = Field(default=None, max_length=64)


class DocumentRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PersistTemporaryDocumentRequest(BaseModel):
    space_id: str = Field(min_length=1, max_length=64)
    category_id: str | None = Field(default=None, max_length=64)


class SaveChatMessageAsDocumentRequest(BaseModel):
    space_id: str = Field(min_length=1, max_length=64)
    result_id: str | None = Field(default=None, min_length=1, max_length=128)


class ChatSavedDocument(BaseModel):
    document_id: str
    space_id: str
    space_name: str


class ChatSaveableResult(BaseModel):
    id: str
    message_id: str
    name: str
    href: str
    created_at: str
    can_save: bool
    reason: str | None
    saved_documents: list[ChatSavedDocument]


class ChatSaveableResultsResponse(BaseModel):
    items: list[ChatSaveableResult]
    total: int
    next_offset: int | None



class MoveDocumentRequest(BaseModel):
    space_id: str = Field(min_length=1, max_length=64)
    category_id: str | None = Field(default=None, max_length=64)


class DocumentVersionResponse(BaseModel):
    id: str
    version_number: int
    processing_stage: str
    queryable: bool
    retryable: bool
    failure_code: str | None
    failure_message: str | None
    chunk_count: int = 0
    summary: str | None = None
    extraction_method: Literal["native", "ocr", "vision", "mixed"] | None = None
    page_count: int = 0
    ocr_page_count: int = 0
    vision_page_count: int = 0
    extraction_warning_code: str | None = None
    file_extension: str | None = None
    source_relative_path: str | None = None
    is_current_published: bool
    created_at: datetime
    updated_at: datetime


class DocumentSearchItem(BaseModel):
    """A lightweight cross-space document hit for fuzzy search results."""

    id: str
    name: str
    space_id: str
    space_name: str
    category_id: str | None = None
    status: str
    created_by_display_name: str | None = None
    updated_at: datetime


class DocumentSearchResponse(BaseModel):
    items: list[DocumentSearchItem]
    total: int
    limit: int


class DocumentResponse(BaseModel):
    id: str
    space_id: str | None
    category_id: str | None = None
    name: str
    status: str
    is_temporary: bool = False
    chat_session_id: str | None = None
    expires_at: datetime | None = None
    temporary_cleanup_at: datetime | None = None
    created_by_user_id: str
    created_by_display_name: str | None = None
    latest_version: DocumentVersionResponse | None
    current_published_version: DocumentVersionResponse | None
    created_at: datetime
    updated_at: datetime


class SaveChatMessageAsDocumentResponse(BaseModel):
    document: DocumentResponse
    reused: bool



class DocumentDetailResponse(DocumentResponse):
    versions: list[DocumentVersionResponse]


class DocumentCounts(BaseModel):
    all: int
    by_category: dict[str, int]


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    next_cursor: str | None
    total: int | None = None
    counts: DocumentCounts | None = None
    can_manage_documents: bool = False


class TaskSnapshotResponse(BaseModel):
    id: str
    kind: str
    status: str
    label: str
    resource_path: str | None
    retryable: bool
    retry_action: dict[str, str] | None
    cancel_action: dict[str, str] | None = None
    operation_id: str | None = None
    job_id: str | None = None
    document_id: str | None = None


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    task: TaskSnapshotResponse


class DocumentPreviewResponse(BaseModel):
    document_id: str
    version_id: str | None = None
    kind: Literal["markdown", "text", "pdf", "image", "summary", "unavailable"]
    content: str | None = None
    truncated: bool = False
    page_count: int = 0
