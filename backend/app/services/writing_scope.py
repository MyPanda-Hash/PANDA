from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from fastapi import HTTPException
from sqlalchemy.orm import Session


from app.core.config import settings
from app.core.security import now
from app.models.auth import User
from app.models.writing import WritingProject
from app.repositories import writing
from app.services.authorization import PermissionService
from app.services.chat_knowledge import require_documents_in_scope
from app.services.writing_runtime_config import resolve_writing_scope_secret

AUDIENCE = "doc-nexus:writing-agent-tools"


@dataclass(frozen=True, slots=True)
class CurrentWritingScope:
    space_ids: list[str]
    document_ids: list[str]


class WritingScopeUnavailableError(RuntimeError):
    """The project scope no longer passes the acting user's current ACL."""

    def __init__(self) -> None:
        super().__init__("writing knowledge scope is no longer available")

    @property
    def detail(self) -> dict[str, object]:
        return {
            "error_code": "scope_no_longer_available",
            "stale_scope": True,
        }


def resolve_current_writing_scope(
    db: Session,
    *,
    user: User,
    project: WritingProject,
) -> CurrentWritingScope:
    """Resolve a project's stored scope against the ACTING user's current ACL.

    ``user`` is whoever is driving this request — the owner, or a project
    member. Membership in a writing project never implies knowledge-space
    access, so every space and document in the stored scope is re-checked
    against this user. Callers are responsible for having already authorised
    ``user`` on the project itself (see
    ``app.services.writing_membership.require_project_role``); this function
    only decides whether that user may still reach the cited material.
    """
    if user.status != "active":
        raise WritingScopeUnavailableError
    space_ids = writing.load_space_ids(project.selected_space_ids)
    document_ids = writing.load_document_ids(
        getattr(project, "selected_document_ids", None)
    )
    source_session_id = getattr(project, "source_chat_session_id", None)
    if document_ids and not source_session_id:
        from sqlalchemy import select
        from app.models.documents import Document

        # A project cannot turn owner-wide temporary uploads into a grant.
        # Only imports retaining their originating session may use attachments.
        if db.scalar(select(Document.id).where(
            Document.id.in_(document_ids), Document.space_id.is_(None),
        )) is not None:
            raise WritingScopeUnavailableError
    try:
        for space_id in space_ids:
            PermissionService.require_space_access(
                db, user, space_id, minimum_role="reader"
            )
        require_documents_in_scope(
            db,
            user,
            space_ids=space_ids,
            document_ids=document_ids,
            chat_session_id=source_session_id,
        )
    except HTTPException as exc:
        raise WritingScopeUnavailableError from exc
    return CurrentWritingScope(
        space_ids=space_ids,
        document_ids=document_ids,
    )


WORKFLOW_TOOLS: dict[str, list[str]] = {
    "outline": [
        "get_project_brief",
        "search_evidence",
        "save_outline_candidate",
        "get_operation_budget",
        "emit_run_event",
    ],
    "document": [
        "get_project_brief",
        "get_confirmed_outline",
        "search_evidence",
        "save_section_candidate",
        "save_verification_result",
        "get_operation_budget",
        "emit_run_event",
        "finalize_document_candidate",
    ],
    "rewrite": [
        "get_project_brief",
        "get_confirmed_outline",
        "get_rewrite_context",
        "save_section_candidate",
        "save_verification_result",
        "get_operation_budget",
        "emit_run_event",
    ],
    "verify": [
        "get_project_brief",
        "get_confirmed_outline",
        "search_evidence",
        "save_verification_result",
        "get_operation_budget",
        "emit_run_event",
    ],
}


def scope_hash(
    owner_user_id: str,
    project_id: str,
    space_ids: list[str],
    document_ids: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "owner_user_id": owner_user_id,
        "project_id": project_id,
        "space_ids": sorted(space_ids),
    }
    docs = sorted(item for item in (document_ids or []) if item)
    # Keep hashes stable for whole-space projects that predate document filters.
    if docs:
        payload["document_ids"] = docs
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _secret() -> str:
    configured = resolve_writing_scope_secret()
    if settings.environment.lower() in {"production", "prod"} and not configured:
        raise RuntimeError("DOC_NEXUS_WRITING_SCOPE_SECRET is required in production")
    return configured or settings.jwt_secret


def create_scope_token(
    *,
    operation_id: str,
    project_id: str,
    owner_user_id: str,
    scope_hash_value: str,
    team_id: str,
    team_version: int,
    workflow: str,
) -> str:
    issued = now()
    payload = {
        "sub": owner_user_id,
        "aud": AUDIENCE,
        "iat": int(issued.timestamp()),
        "exp": int(
            (issued + timedelta(seconds=settings.writing_scope_ttl_seconds)).timestamp()
        ),
        "writing_operation_id": operation_id,
        "project_id": project_id,
        "scope_hash": scope_hash_value,
        "team_id": team_id,
        "team_version": team_version,
        "workflow": workflow,
        "allowed_tools": WORKFLOW_TOOLS[workflow],
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_scope_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        _secret(),
        algorithms=["HS256"],
        audience=AUDIENCE,
    )
