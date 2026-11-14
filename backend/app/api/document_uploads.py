from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_user
from app.models.auth import User
from app.schemas.documents import DocumentResponse, TaskSnapshotResponse
from app.services import document_uploads

router = APIRouter(tags=["document-uploads"])


class DocumentUploadCreateRequest(BaseModel):
    client_upload_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str | None = Field(default=None, max_length=255)
    category_id: str | None = Field(default=None, max_length=64)
    source_relative_path: str | None = Field(default=None, max_length=1024)


class UploadedPartResponse(BaseModel):
    part_number: int
    size_bytes: int
    etag: str


class DocumentUploadSessionResult(BaseModel):
    document: DocumentResponse
    task: TaskSnapshotResponse


class DocumentUploadSessionResponse(BaseModel):
    id: str
    status: str
    filename: str
    size_bytes: int
    part_size: int
    expires_at: datetime
    uploaded_parts: list[UploadedPartResponse]
    error: str | None = None
    result: DocumentUploadSessionResult | None = None


class DocumentUploadPartResponse(BaseModel):
    url: str
    headers: dict[str, str]


def _service_error(exc: document_uploads.DocumentUploadError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/document-upload-policy")
def get_document_upload_policy() -> dict[str, int | bool]:
    return document_uploads.upload_policy()


@router.post(
    "/knowledge-spaces/{space_id}/document-uploads",
    response_model=DocumentUploadSessionResponse,
    status_code=201,
)
def create_document_upload(
    space_id: str,
    payload: DocumentUploadCreateRequest,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        session = document_uploads.create_upload_session(
            user_id=user.id,
            space_id=space_id,
            client_upload_id=payload.client_upload_id,
            filename=payload.filename,
            size_bytes=payload.size_bytes,
            sha256=payload.sha256,
            name=payload.name,
            category_id=payload.category_id,
            source_relative_path=payload.source_relative_path,
        )
        return document_uploads.get_session(session.id, user.id)
    except document_uploads.DocumentUploadError as exc:
        raise _service_error(exc) from exc


@router.get(
    "/document-uploads/{upload_id}",
    response_model=DocumentUploadSessionResponse,
)
def get_document_upload(
    upload_id: str, user: User = Depends(current_user)
) -> dict[str, Any]:
    try:
        return document_uploads.get_session(upload_id, user.id)
    except document_uploads.DocumentUploadError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/document-uploads/{upload_id}/parts/{part_number}",
    response_model=DocumentUploadPartResponse,
)
def sign_document_upload_part(
    upload_id: str,
    part_number: int,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        return document_uploads.sign_part(upload_id, part_number, user.id)
    except document_uploads.DocumentUploadError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/document-uploads/{upload_id}/complete",
    response_model=DocumentUploadSessionResponse,
)
def complete_document_upload(
    upload_id: str, user: User = Depends(current_user)
) -> dict[str, Any]:
    try:
        return document_uploads.complete_session(upload_id, user.id)
    except document_uploads.DocumentUploadError as exc:
        raise _service_error(exc) from exc


@router.delete(
    "/document-uploads/{upload_id}",
    response_model=DocumentUploadSessionResponse,
)
def abort_document_upload(
    upload_id: str, user: User = Depends(current_user)
) -> dict[str, Any]:
    try:
        return document_uploads.abort_session(upload_id, user.id)
    except document_uploads.DocumentUploadError as exc:
        raise _service_error(exc) from exc
