"""Scoped capabilities exposed to Nexus-aware agents.

The agent receives a short-lived signed grant, not a Nexus login token. Every
call re-checks the current user and space ACL so a grant cannot outlive an
access change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import jwt
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import iso, new_token, now
from app.models.access import KnowledgeSpace, SpaceMember
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.documents import Document, DocumentVersion
from app.models.user_memory import UserMemoryState, UserMemoryTurn
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeEntityDocument,
    KnowledgeRelation,
)
from app.repositories import chat as chat_repo
from app.repositories import documents
from app.repositories import user_memory as user_memory_repo
from app.repositories import user_channel_bindings as channel_bindings_repo
from app.repositories import personal_search
from app.repositories import qa as qa_repo
from app.services import user_memory as user_memory_service
from app.services.authorization import PermissionService
from app.services.chat_knowledge import require_documents_in_scope
from app.schemas.user_memory import (
    MemoryForgetProposalInput,
    MemoryProposalInput,
)
from app.services.hybrid_retrieval import retrieve_for_qa
from app.services.evidence_snapshots import (
    freeze_text,
    make_citation_id,
    parse_citation_id,
    read_snapshot,
    snapshot_page_location,
)
from app.services.image_documents import image_source_text, is_image_version
from app.services.qa_service import (
    _build_evidence_blocks,
    _evidence_support_state as _source_support_state,
    _resolve_version_text,
)
from app.services.system_insights import (
    SYSTEM_OVERVIEW_PERMISSION,
    USER_DIRECTORY_PERMISSION,
    USER_USAGE_PERMISSION,
    SystemInsightError,
    get_system_overview,
    get_user_usage,
    list_system_users,
)

logger = logging.getLogger(__name__)

CAPABILITY_AUDIENCE = "doc-nexus:agent-capabilities"
CHAT_CAPABILITY_TOOLS = (
    "list_spaces",
    "list_documents",
    "search_evidence",
    "preview_document",
    "read_presentation",
    "read_image",
    "expand_citation",
    "match_entities",
    "get_project_context",
    "list_chat_sessions",
    "get_chat_session_summary",
    "rename_chat_session",
    "get_schedule_summary",
    "propose_schedule",
    "propose_schedule_action",
    # MES 只读：查单、流程追踪（含工序）、单据详情、库存、今日计划
    "mes_query_panel",
    "mes_trace_doc",
    "mes_doc_detail",
    "mes_stock",
    "mes_today_plan",
    "mes_owner_profile",
    "mes_weekly_review",
    "mes_root_cause",
)
MES_CAPABILITY_TOOLS = (
    "mes_query_panel",
    "mes_trace_doc",
    "mes_doc_detail",
    "mes_stock",
    "mes_today_plan",
)
# 今日计划要读事件库、按人过滤，签名和四个只读查询不同，单独一条
PLAN_CAPABILITY_TOOLS = (
    "mes_today_plan",
    "mes_owner_profile",
    "mes_weekly_review",
    "mes_root_cause",
)
SCHEDULE_CAPABILITY_TOOLS = (
    "get_schedule_summary",
    "propose_schedule",
    "propose_schedule_action",
)
PERSONAL_CAPABILITY_TOOLS = (
    "search_my_chat_history",
    "get_chat_history_context",
    "list_my_uploads",
)
SYSTEM_CAPABILITY_TOOLS = (
    "get_system_overview",
    "list_system_users",
    "get_user_usage",
)
# Durable personal memory rides on its own scope, NOT on personal_data_scope.
# Adding these to PERSONAL_CAPABILITY_TOOLS would hand every existing
# read-only "own history" grant the power to write memory.
MEMORY_CAPABILITY_TOOLS = (
    "get_my_memory",
    "propose_my_memory",
    "propose_forget_my_memory",
)
MEMORY_WRITE_CAPABILITY_TOOLS = (
    "propose_my_memory",
    "propose_forget_my_memory",
)
CAPABILITY_TOOLS = (
    CHAT_CAPABILITY_TOOLS
    + PERSONAL_CAPABILITY_TOOLS
    + SYSTEM_CAPABILITY_TOOLS
    + MEMORY_CAPABILITY_TOOLS
)
WRITING_CAPABILITY_TOOLS = (
    "list_spaces",
    "list_documents",
    "search_evidence",
    "preview_document",
    "expand_citation",
    "match_entities",
    "get_project_context",
)
SYSTEM_CAPABILITY_PERMISSIONS = {
    "get_system_overview": SYSTEM_OVERVIEW_PERMISSION,
    "list_system_users": USER_DIRECTORY_PERMISSION,
    "get_user_usage": USER_USAGE_PERMISSION,
}
_ALLOWED_ENTITY_TYPES = {
    "person",
    "organization",
    "product",
    "technology",
    "concept",
    "event",
    "location",
    "regulation",
}


class CapabilityGrantError(ValueError):
    """A capability grant or tool request is invalid."""


def _secret() -> str:
    configured = (settings.agent_capability_secret or "").strip()
    if settings.environment.lower() in {"production", "prod"} and not configured:
        raise RuntimeError(
            "DOC_NEXUS_AGENT_CAPABILITY_SECRET is required in production"
        )
    return configured or settings.writing_scope_secret or settings.jwt_secret


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def capability_scope_hash(
    *,
    user_id: str,
    space_mode: Literal["selected", "accessible"],
    space_ids: list[str],
    project_ids: list[str],
    document_ids: list[str] | None = None,
    attachment_document_ids: list[str] | None = None,
    conversation_id: str | None = None,
    personal_data_scope: Literal["none", "own"] = "none",
    memory_scope: Literal["none", "read", "propose"] = "none",
    history_turn_message_id: str | None = None,
    memory_turn_id: str | None = None,
    memory_source_hash: str | None = None,
    memory_epoch: int | None = None,
    auth_version: int | None = None,
    memory_channel_binding_id: str | None = None,
    memory_channel_binding_version: int | None = None,
) -> str:
    payload = {
        "user_id": user_id,
        "space_mode": space_mode,
        "space_ids": sorted(set(space_ids)),
        "project_ids": sorted(set(project_ids)),
    }
    # Null is the historical space-only grant. An empty selected set is not null.
    if document_ids is not None:
        payload["document_ids"] = sorted(set(document_ids))
    # 缺省保留旧令牌语义；空数组也明确启用知识与附件分离。
    if attachment_document_ids is not None:
        payload["attachment_document_ids"] = sorted(set(attachment_document_ids))
        payload["conversation_id"] = conversation_id
    # Keep the hash for the default scope compatible with short-lived tokens
    # issued before personal-data capabilities were introduced. The elevated
    # ``own`` scope remains part of the signed hash.
    if personal_data_scope != "none":
        payload["personal_data_scope"] = personal_data_scope
    if history_turn_message_id is not None:
        payload["history_turn_message_id"] = history_turn_message_id
    if memory_scope != "none":
        payload["memory_scope"] = memory_scope
        payload.update(
            memory_turn_id=memory_turn_id,
            memory_source_hash=memory_source_hash,
            memory_epoch=memory_epoch,
            auth_version=auth_version,
            memory_channel_binding_id=memory_channel_binding_id,
            memory_channel_binding_version=memory_channel_binding_version,
        )
    return "sha256:" + hashlib.sha256(_canonical(payload).encode()).hexdigest()


def create_capability_token(
    *,
    user_id: str,
    agent_id: str,
    space_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    attachment_document_ids: list[str] | None = None,
    evidence_max_bytes: int | None = None,
    space_mode: Literal["selected", "accessible"] = "selected",
    project_ids: list[str] | None = None,
    conversation_id: str | None = None,
    chat_history: bool = False,
    personal_data_scope: Literal["none", "own"] = "none",
    history_turn_message_id: str | None = None,
    memory_scope: Literal["none", "read", "propose"] = "none",
    memory_turn_id: str | None = None,
    memory_source_hash: str | None = None,
    memory_epoch: int | None = None,
    auth_version: int | None = None,
    memory_channel_binding_id: str | None = None,
    memory_channel_binding_version: int | None = None,
    allowed_tools: list[str] | tuple[str, ...] | None = None,
) -> str:
    if evidence_max_bytes is not None and (
        type(evidence_max_bytes) is not int or evidence_max_bytes < 0
    ):
        raise CapabilityGrantError("evidence byte budget must be a nonnegative integer")
    spaces = sorted(
        {item.strip() for item in (space_ids or []) if item and item.strip()}
    )
    selected_documents = (
        sorted({item.strip() for item in document_ids if item and item.strip()})
        if document_ids is not None
        else None
    )
    attachments = (
        sorted({item.strip() for item in attachment_document_ids if item and item.strip()})
        if attachment_document_ids is not None else None
    )
    projects = sorted(
        {item.strip() for item in (project_ids or []) if item and item.strip()}
    )
    if personal_data_scope not in {"none", "own"}:
        raise CapabilityGrantError("invalid personal data scope")
    if history_turn_message_id is not None and (
        not history_turn_message_id.strip()
        or not (conversation_id or "").strip()
        or personal_data_scope != "own"
    ):
        raise CapabilityGrantError("history recall requires an owned conversation turn")
    if memory_scope not in {"none", "read", "propose"}:
        raise CapabilityGrantError("invalid memory scope")
    if memory_scope == "propose" and not (
        memory_turn_id
        and memory_source_hash
        and type(memory_epoch) is int
        and memory_epoch >= 1
    ):
        # A write grant only exists for an input the backend has already
        # authenticated and registered. Without that binding the model could
        # attribute a proposal to a turn nobody made.
        raise CapabilityGrantError("propose scope requires a registered turn")
    if memory_scope != "none" and auth_version is None:
        raise CapabilityGrantError("memory scope requires an auth version")
    if (memory_channel_binding_id is None) != (memory_channel_binding_version is None):
        raise CapabilityGrantError("memory channel binding requires its version")
    # Memory tools are never part of the default set: they are added only when
    # the caller asks for a memory scope explicitly.
    default_tools = (
        CHAT_CAPABILITY_TOOLS
        + (PERSONAL_CAPABILITY_TOOLS if personal_data_scope == "own" else ())
        + SYSTEM_CAPABILITY_TOOLS
        + (
            ()
            if memory_scope == "none"
            else (
                ("get_my_memory",)
                if memory_scope == "read"
                else MEMORY_CAPABILITY_TOOLS
            )
        )
    )
    tools = list(
        dict.fromkeys(default_tools if allowed_tools is None else allowed_tools)
    )
    unknown = sorted(set(tools) - set(CAPABILITY_TOOLS))
    if unknown:
        raise CapabilityGrantError(f"unknown capability tools: {unknown}")
    if personal_data_scope != "own" and set(tools).intersection(
        PERSONAL_CAPABILITY_TOOLS
    ):
        raise CapabilityGrantError("personal capability tools require own data scope")
    if memory_scope == "none" and set(tools).intersection(MEMORY_CAPABILITY_TOOLS):
        raise CapabilityGrantError("memory capability tools require a memory scope")
    if memory_scope == "read" and set(tools).intersection(
        MEMORY_WRITE_CAPABILITY_TOOLS
    ):
        raise CapabilityGrantError("memory proposals require propose scope")
    if space_mode == "selected" and not spaces:
        # An explicit empty selection is still a valid scope: it can inspect
        # project/session metadata but cannot search any knowledge space.
        spaces = []
    issued = now()
    payload: dict[str, Any] = {
        "sub": user_id,
        "aud": CAPABILITY_AUDIENCE,
        "iat": _safe_int(issued.timestamp()),
        "exp": _safe_int(
            (
                issued + timedelta(seconds=settings.agent_capability_ttl_seconds)
            ).timestamp()
        ),
        "jti": new_token(24),
        "agent_id": agent_id,
        "space_mode": space_mode,
        "space_ids": spaces,
        "document_ids": selected_documents,
        "evidence_max_bytes": evidence_max_bytes,
        "project_ids": projects,
        "conversation_id": (conversation_id or "").strip() or None,
        "chat_history": bool(chat_history),
        "personal_data_scope": personal_data_scope,
        "history_turn_message_id": history_turn_message_id,
        "memory_scope": memory_scope,
        # Server-bound identity of the current input. The model supplies none
        # of these; it can only reference the turn this grant already names.
        "memory_turn_id": memory_turn_id,
        "memory_source_hash": memory_source_hash,
        "memory_epoch": memory_epoch,
        "auth_version": auth_version,
        "memory_channel_binding_id": memory_channel_binding_id,
        "memory_channel_binding_version": memory_channel_binding_version,
        "scope_hash": capability_scope_hash(
            user_id=user_id,
            space_mode=space_mode,
            space_ids=spaces,
            document_ids=selected_documents,
            attachment_document_ids=attachments,
            conversation_id=(conversation_id or "").strip() or None,
            project_ids=projects,
            personal_data_scope=personal_data_scope,
            history_turn_message_id=history_turn_message_id,
            memory_scope=memory_scope,
            memory_turn_id=memory_turn_id,
            memory_source_hash=memory_source_hash,
            memory_epoch=memory_epoch,
            auth_version=auth_version,
            memory_channel_binding_id=memory_channel_binding_id,
            memory_channel_binding_version=memory_channel_binding_version,
        ),
        "allowed_tools": tools,
    }
    if attachments is not None:
        payload["attachment_document_ids"] = attachments
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_capability_token(token: str) -> dict[str, Any]:
    """Decode one capability grant, honouring a still-running turn's lease.

    The grant's lifetime is short and it is injected into a Pi child process
    before it starts, so a long turn cannot be handed a fresh one. Expiry is
    therefore waived only while the runtime still holds a live lease for this
    exact token; every other claim is verified normally, and a token whose turn
    has ended expires as usual.
    """
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=CAPABILITY_AUDIENCE,
        )
    except jwt.ExpiredSignatureError:
        from app.services.pi_runtime.config import PiRuntimeSettings
        from app.services.pi_runtime.run_lease import secret_lease_active

        if not secret_lease_active(PiRuntimeSettings.from_env().root, token):
            raise
        return jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=CAPABILITY_AUDIENCE,
            options={"verify_exp": False},
        )


def _claims_list(claims: dict[str, Any], key: str) -> list[str]:
    value = claims.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CapabilityGrantError(f"invalid capability claim: {key}")
    return [item for item in value if item.strip()]


def _document_scope(claims: dict[str, Any]) -> list[str] | None:
    # Missing is retained only for signed, short-lived grants already in flight.
    return None if claims.get("document_ids") is None else _claims_list(claims, "document_ids")


def _attachment_scope(claims: dict[str, Any]) -> list[str] | None:
    return (
        None if claims.get("attachment_document_ids") is None
        else _claims_list(claims, "attachment_document_ids")
    )


def _resolve_attachment_scope(
    db: Session, user: User, claims: dict[str, Any],
) -> list[str] | None:
    attachments = _attachment_scope(claims)
    if not attachments:
        return attachments
    session_id = claims.get("conversation_id")
    if (
        not isinstance(session_id, str) or not session_id
        or chat_repo.get_owned_session(db, session_id, user.id) is None
    ):
        raise CapabilityGrantError("attachments require the current owned session")
    rows = list(db.scalars(select(Document).where(Document.id.in_(attachments))).all())
    if len(rows) != len(set(attachments)) or any(
        document.space_id is not None
        or not document.is_temporary
        or document.chat_session_id != session_id
        or document.created_by_user_id != user.id
        or document.temporary_cleanup_at is not None
        for document in rows
    ):
        raise CapabilityGrantError("attachment is outside the current session")
    try:
        require_documents_in_scope(
            db, user, space_ids=[], document_ids=attachments, chat_session_id=session_id,
        )
    except HTTPException as exc:
        raise CapabilityGrantError(str(exc.detail)) from exc
    return attachments


def _scope_receipt(
    claims: dict[str, Any], space_ids: list[str], document_ids: list[str] | None,
    attachment_document_ids: list[str] | None,
) -> dict[str, Any]:
    return {
        "space_mode": claims.get("space_mode"),
        "space_ids": space_ids,
        "document_ids": document_ids,
        "attachment_document_ids": attachment_document_ids,
        "coverage_basis": "authorized_scope_only",
    }


def _resolve_document_scope(
    db: Session,
    user: User,
    claims: dict[str, Any],
    space_ids: list[str],
    requested: list[str] | None = None,
) -> list[str] | None:
    granted = _document_scope(claims)
    if requested is not None and granted is not None and not set(requested).issubset(granted):
        raise CapabilityGrantError("requested document is outside capability scope")
    selected = granted if requested is None else requested
    if selected:
        if (granted is None or _attachment_scope(claims) is not None) and db.scalar(
            select(Document.id).where(
                Document.id.in_(selected), Document.space_id.is_(None)
            ).limit(1)
        ) is not None:
            raise CapabilityGrantError("temporary document is outside capability scope")
        try:
            require_documents_in_scope(
                db, user, space_ids=space_ids, document_ids=selected,
                chat_session_id=str(claims.get("conversation_id") or "").strip() or None,
            )
        except HTTPException as exc:
            raise CapabilityGrantError(str(exc.detail)) from exc
    return selected


def _resolve_evidence_scope(
    db: Session, user: User, claims: dict[str, Any], space_ids: list[str],
    requested: list[str] | None = None,
) -> tuple[list[str] | None, list[str] | None]:
    attachments = _resolve_attachment_scope(db, user, claims)
    if attachments is None:
        return _resolve_document_scope(db, user, claims, space_ids, requested), None
    # 显式工具参数只能缩小两个分支，空数组不能回退到整个空间。
    requested_knowledge = (
        [document_id for document_id in requested if document_id not in attachments]
        if requested is not None else None
    )
    selected = _resolve_document_scope(db, user, claims, space_ids, requested_knowledge)
    if requested is not None:
        attachments = [document_id for document_id in attachments if document_id in requested]
    return selected, attachments


def validate_claims(
    claims: dict[str, Any],
    *,
    tool_name: str,
    expected_agent_id: str | None = None,
) -> None:
    agent_id = claims.get("agent_id")
    if agent_id is None or not isinstance(agent_id, str):
        raise CapabilityGrantError("capability grant has no agent binding")
    if expected_agent_id and agent_id != expected_agent_id:
        raise CapabilityGrantError("capability grant is bound to another agent")
    allowed_tools = claims.get("allowed_tools")
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) for item in allowed_tools
    ):
        raise CapabilityGrantError("invalid capability tool claim")
    if tool_name not in allowed_tools:
        raise CapabilityGrantError("tool is outside capability grant")
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise CapabilityGrantError("capability grant has no user binding")
    mode = claims.get("space_mode")
    if mode not in {"selected", "accessible"}:
        raise CapabilityGrantError("invalid capability space mode")
    spaces = _claims_list(claims, "space_ids")
    projects = _claims_list(claims, "project_ids")
    personal_scope = claims.get("personal_data_scope", "none")
    if personal_scope not in {"none", "own"}:
        raise CapabilityGrantError("invalid personal data scope")
    # A grant issued before memory existed carries no scope, and absence means
    # none. Writing, unbound WeCom and the shared internal Pi identity all land
    # here, so none of them can read or propose memory.
    memory_scope = claims.get("memory_scope", "none")
    if memory_scope not in {"none", "read", "propose"}:
        raise CapabilityGrantError("invalid memory scope")
    if claims.get("scope_hash") != capability_scope_hash(
        user_id=user_id,
        space_mode=mode,
        space_ids=spaces,
        document_ids=_document_scope(claims),
        attachment_document_ids=_attachment_scope(claims),
        conversation_id=claims.get("conversation_id"),
        project_ids=projects,
        personal_data_scope=personal_scope,
        memory_scope=memory_scope,
        history_turn_message_id=claims.get("history_turn_message_id"),
        memory_turn_id=claims.get("memory_turn_id"),
        memory_source_hash=claims.get("memory_source_hash"),
        memory_epoch=claims.get("memory_epoch"),
        auth_version=claims.get("auth_version"),
        memory_channel_binding_id=claims.get("memory_channel_binding_id"),
        memory_channel_binding_version=claims.get("memory_channel_binding_version"),
    ):
        raise CapabilityGrantError("capability scope changed")
    if tool_name in PERSONAL_CAPABILITY_TOOLS and personal_scope != "own":
        raise CapabilityGrantError("personal capability requires own data scope")
    if tool_name in MEMORY_CAPABILITY_TOOLS:
        if memory_scope == "none":
            raise CapabilityGrantError("memory capability requires a memory scope")
        if tool_name in MEMORY_WRITE_CAPABILITY_TOOLS and memory_scope != "propose":
            raise CapabilityGrantError("memory proposal requires propose scope")


def chat_capability_tools(db: Session, user: User) -> tuple[str, ...]:
    """Return chat tools allowed for the current user's live permissions."""
    tools: list[str] = list(CHAT_CAPABILITY_TOOLS + PERSONAL_CAPABILITY_TOOLS)
    # Dependency overrides and lightweight callers may provide only a user id;
    # real authenticated users always carry a string id and role. Do not turn
    # capability discovery into a database failure for those metadata-only calls.
    if not isinstance(getattr(user, "id", None), str) or not isinstance(
        getattr(user, "role", None), str
    ):
        return tuple(tools)
    for tool_name in SYSTEM_CAPABILITY_TOOLS:
        permission_code = SYSTEM_CAPABILITY_PERMISSIONS[tool_name]
        if PermissionService.has_system_permission(db, user, permission_code):
            tools.append(tool_name)
    return tuple(tools)


def require_grant_user(
    db: Session, claims: dict[str, Any], *, for_update: bool = False
) -> User:
    user_id = claims.get("sub")
    if for_update:
        changed = db.execute(
            update(User)
            .where(User.id == user_id, User.status == "active")
            .values(auth_version=User.auth_version)
        )
        if changed.rowcount != 1:
            raise CapabilityGrantError("capability owner is no longer active")
    user = (
        db.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )
        if isinstance(user_id, str)
        else None
    )
    if user is None or user.status != "active":
        raise CapabilityGrantError("capability owner is no longer active")
    # A grant that names an auth version is refused once that version moves:
    # a password change or a forced sign-out revokes it without waiting for the
    # token to expire. Older grants carry no claim and keep their behaviour.
    claimed_auth_version = claims.get("auth_version")
    if (
        claims.get("memory_scope", "none") != "none"
        and type(claimed_auth_version) is not int
    ):
        raise CapabilityGrantError("memory grant has no auth version")
    if (
        claimed_auth_version is not None
        and _safe_int(claimed_auth_version, -1) != user.auth_version
    ):
        raise CapabilityGrantError("capability grant was revoked")
    binding_id = claims.get("memory_channel_binding_id")
    binding_version = claims.get("memory_channel_binding_version")
    if binding_id is not None or binding_version is not None:
        if not isinstance(binding_id, str) or type(binding_version) is not int:
            raise CapabilityGrantError("invalid memory channel binding")
        binding = channel_bindings_repo.get_active_binding_by_id(
            db, owner_user_id=user.id, binding_id=binding_id, version=binding_version
        )
        if binding is None or claims.get("conversation_id") != (
            channel_bindings_repo.binding_runtime_session_id(binding)
        ):
            raise CapabilityGrantError("memory channel binding was revoked")
    return user


def _requested_ids(arguments: dict[str, Any], key: str, *, limit: int) -> list[str]:
    raw = arguments.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise CapabilityGrantError(f"{key} must be an array of strings")
    values = [item.strip() for item in raw if item.strip()]
    if len(values) > limit or len(set(values)) != len(values):
        raise CapabilityGrantError(
            f"{key} exceeds its scope limit or contains duplicates"
        )
    return values


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return default


def _int_argument(
    arguments: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = arguments.get(key, default)
    try:
        value = int(raw if raw is not None else default)
    except (TypeError, ValueError) as exc:
        raise CapabilityGrantError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise CapabilityGrantError(f"{key} must be between {minimum} and {maximum}")
    return value


def resolve_space_scope(
    db: Session,
    user: User,
    claims: dict[str, Any],
    requested_space_ids: list[str] | None = None,
) -> list[str]:
    mode = claims.get("space_mode")
    granted = set(_claims_list(claims, "space_ids"))
    requested = requested_space_ids or []
    if mode == "selected":
        if not requested:
            requested = sorted(granted)
        if not set(requested).issubset(granted):
            raise CapabilityGrantError("requested space is outside capability scope")
    elif requested:
        # In accessible mode the grant intentionally permits current ACL-visible
        # spaces, but every requested id is still checked below.
        pass
    else:
        requested = _accessible_space_ids(db, user)
    for space_id in requested:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )
    return requested


def _accessible_space_ids(db: Session, user: User) -> list[str]:
    query = select(KnowledgeSpace.id).where(KnowledgeSpace.status == "active")
    if user.role != "admin":
        member_space_ids = select(SpaceMember.space_id).where(
            SpaceMember.user_id == user.id,
            SpaceMember.role.in_(("reader", "contributor", "manager")),
        )
        query = query.where(
            or_(
                KnowledgeSpace.owner_user_id == user.id,
                KnowledgeSpace.id.in_(member_space_ids),
            )
        )
    return list(
        db.scalars(query.order_by(KnowledgeSpace.name, KnowledgeSpace.id)).all()
    )


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return (
        [str(item) for item in data if isinstance(item, str)]
        if isinstance(data, list)
        else []
    )


def _published_or_latest_version(
    document: Document, version_id: str | None = None
) -> DocumentVersion | None:
    versions = list(document.versions or [])
    if version_id:
        match = next((item for item in versions if item.id == version_id), None)
        if match is None:
            raise CapabilityGrantError("version not found")
        return match
    queryable = [item for item in versions if item.queryable]
    current = next((item for item in queryable if item.is_current_published), None)
    pool = queryable or versions
    return current or max(pool, key=lambda item: item.version_number, default=None)


def _authorize_document(
    db: Session, user: User, claims: dict[str, Any], document: Document
) -> None:
    if document.status != "active":
        raise CapabilityGrantError("document not found")
    attachments = _resolve_attachment_scope(db, user, claims)
    if attachments is not None and document.temporary_cleanup_at is not None:
        raise CapabilityGrantError("document is no longer available")
    if attachments is not None and document.id in attachments:
        return
    selected = _document_scope(claims)
    if selected is not None and document.id not in selected:
        raise CapabilityGrantError("document is outside capability scope")
    spaces = (
        resolve_space_scope(db, user, claims, [document.space_id])
        if document.space_id else []
    )
    _resolve_document_scope(db, user, claims, spaces, [document.id])


def _load_authorized_document(
    db: Session, user: User, claims: dict[str, Any], document_id: str
) -> Document:
    document_id = document_id.strip()
    if not document_id:
        raise CapabilityGrantError("document_id is required")
    document = db.get(Document, document_id)
    if document is None:
        raise CapabilityGrantError("document not found")
    _authorize_document(db, user, claims, document)
    return document


def _load_authorized_version(
    db: Session,
    user: User,
    claims: dict[str, Any],
    *,
    version_id: str | None = None,
    document_id: str | None = None,
) -> tuple[Document, DocumentVersion]:
    version: DocumentVersion | None = None
    if version_id:
        version = db.get(DocumentVersion, version_id)
        if version is None:
            raise CapabilityGrantError("version not found")
        document = db.get(Document, version.document_id)
    elif document_id:
        document = _load_authorized_document(db, user, claims, document_id)
        version = _published_or_latest_version(document)
    else:
        raise CapabilityGrantError("document_id or document_version_id is required")
    if document is None or version is None:
        raise CapabilityGrantError("document not found")
    _authorize_document(db, user, claims, document)
    return document, version


def list_spaces(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _int_argument(arguments, "limit", default=50, minimum=1, maximum=100)
    offset = _int_argument(arguments, "offset", default=0, minimum=0, maximum=1_000_000)
    scope_ids = resolve_space_scope(db, user, claims)
    selected, attachments = _resolve_evidence_scope(db, user, claims, scope_ids)
    scope = _scope_receipt(claims, scope_ids, selected, attachments)
    if not scope_ids:
        return {"items": [], "next_offset": None, "scope": scope}
    count_subquery = (
        select(func.count(Document.id))
        .where(Document.space_id == KnowledgeSpace.id, Document.status == "active")
        .where(Document.id.in_(selected) if selected is not None else True)
        .where(
            and_(
                Document.temporary_cleanup_at.is_(None),
                or_(Document.expires_at.is_(None), Document.expires_at > now()),
            ) if attachments is not None else True
        )
        .correlate(KnowledgeSpace)
        .scalar_subquery()
    )
    rows = list(
        db.execute(
            select(KnowledgeSpace, count_subquery)
            .where(KnowledgeSpace.id.in_(scope_ids), KnowledgeSpace.status == "active")
            .order_by(KnowledgeSpace.name, KnowledgeSpace.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    items = [
        {
            "id": space.id,
            "name": space.name,
            "description": (space.description or "")[:500],
            "document_count": _safe_int(count or 0),
            "updated_at": space.updated_at.isoformat() if space.updated_at else None,
        }
        for space, count in rows[:limit]
    ]
    return {"items": items, "next_offset": offset + limit if has_more else None, "scope": scope}


def list_documents(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    space_id = str(arguments.get("space_id") or "").strip()
    if not space_id:
        raise CapabilityGrantError("space_id is required")
    resolve_space_scope(db, user, claims, [space_id])
    limit = _int_argument(arguments, "limit", default=50, minimum=1, maximum=100)
    offset = _int_argument(arguments, "offset", default=0, minimum=0, maximum=1_000_000)
    query_text = str(arguments.get("query") or "").strip()
    query = select(Document).where(
        Document.space_id == space_id, Document.status == "active"
    )
    selected, attachments = _resolve_evidence_scope(
        db, user, claims, resolve_space_scope(db, user, claims)
    )
    if attachments is not None:
        query = query.where(
            Document.temporary_cleanup_at.is_(None),
            or_(Document.expires_at.is_(None), Document.expires_at > now()),
        )
    if selected is not None:
        query = query.where(Document.id.in_(selected))
    if query_text:
        query = query.where(Document.name.ilike(f"%{query_text[:120]}%"))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    docs = list(
        db.scalars(
            query.order_by(Document.updated_at.desc(), Document.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
    )
    has_more = len(docs) > limit
    creator_names = documents.creator_display_names(
        db, [document.created_by_user_id for document in docs[:limit]]
    )
    items: list[dict[str, Any]] = []
    for document in docs[:limit]:
        versions = [version for version in document.versions if version.queryable]
        current = next(
            (version for version in versions if version.is_current_published), None
        )
        current = current or max(
            versions, key=lambda version: version.version_number, default=None
        )
        latest = _published_or_latest_version(document)
        items.append(
            {
                "id": document.id,
                "space_id": document.space_id,
                "name": document.name,
                "created_by_user_id": document.created_by_user_id,
                "created_by_display_name": creator_names.get(document.created_by_user_id),
                "created_at": iso(document.created_at),
                "status": document.status,
                "version": current.version_number if current else None,
                "queryable": bool(current and current.queryable),
                "processing_stage": latest.processing_stage if latest else None,
                "version_id": current.id if current else None,
                "summary": (current.summary or "")[:1000] if current else "",
                "updated_at": document.updated_at.isoformat()
                if document.updated_at
                else None,
            }
        )
    return {
        "items": items, "next_offset": offset + limit if has_more else None,
        "offset": offset, "total": total,
        "scope": _scope_receipt(claims, [space_id], selected, [] if attachments is not None else None),
        "empty_reason": "no_documents_in_authorized_scope" if not total else None,
        "coverage": {"expected": total, "returned": len(items), "complete": offset == 0 and not has_more},
    }


def _evidence_support_state(item: object, version: Any) -> str:
    return _source_support_state(
        version, None, item if isinstance(item, dict) else None,
    )


def _chunk_evidence_items(
    bundle: Any,
    query: str,
    *,
    limit: int,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """Rank small excerpts inside already-selected documents.

    Document selection remains handled by the adaptive bundle. This second
    pass avoids returning an unrelated document prefix and only reads the
    bounded set of selected versions.
    """
    from app.services.qa_service import _lexical_relevance_score, _resolve_version_text
    from app.services.writing_evidence import _split_chunks

    scored: list[tuple[float, dict[str, Any]]] = []
    for document, version in bundle.documents:
        text = _resolve_version_text(version) or ""
        if not text:
            continue
        text_sha256 = freeze_text(version.id, text)
        chunks = _split_chunks(text)
        ranked: list[tuple[float, int, int, int, str]] = []
        for index, (start, end, excerpt) in enumerate(chunks):
            score = _lexical_relevance_score(
                query,
                title=document.name or "",
                body=excerpt,
                summary="",
            )
            if score > 0:
                ranked.append((score, index, start, end, excerpt))
        for score, index, start, end, excerpt in sorted(
            ranked,
            key=lambda item: (-item[0], item[1]),
        )[:2]:
            excerpt = excerpt[:2200]
            end = start + len(excerpt)
            location = None
            if db is not None:
                try:
                    location = snapshot_page_location(db, version.id, text, start, end)
                except (TypeError, ValueError):
                    location = None
            page_or_section = (
                f"文档《{document.name}》v{version.version_number} "
                f"片段#{index + 1} [{start}-{end}]"
            )
            evidence_item: dict[str, Any] = {
                "id": make_citation_id(version.id, text_sha256, start, end),
                "citation_id": make_citation_id(version.id, text_sha256, start, end),
                "text_sha256": text_sha256,
                "document_id": document.id,
                "document_name": document.name,
                "document_version_id": version.id,
                "space_id": document.space_id,
                "page_or_section": page_or_section,
                "excerpt": image_source_text(version, text, db=db, start=start, end=end),
                "support_state": _source_support_state(version, location, snapshot_text=text),
                "chunk_index": index,
                "char_start": start,
                "char_end": end,
            }
            if location is not None:
                evidence_item["page_or_section"] = (
                    f"{page_or_section} · {location.location_label}"
                )
                evidence_item.update(
                    {
                        "page_number": location.page_number,
                        "page_block_id": location.page_block_id,
                        "bbox": location.bbox,
                    }
                )
            scored.append((score, evidence_item))
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1]["document_version_id"],
            item[1]["chunk_index"],
        )
    )
    return [item for _score, item in scored[: max(0, limit)]]


def _vector_evidence_items(bundle: Any, *, limit: int, db: Session | None = None) -> list[dict[str, Any]]:
    """Convert vector hits into bounded, source-labelled evidence records.

    This is only used when adaptive retrieval actually returned vector chunks.
    Legacy lexical/graph callers continue to use the existing document evidence
    builder unchanged.
    """
    by_doc = {doc.id: (doc, version) for doc, version in bundle.documents}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in bundle.vector_hits[: max(0, limit)]:
        hit_id = str(hit.get("id") or "")
        document_id = str(hit.get("document_id") or "")
        if not hit_id or not document_id or hit_id in seen:
            continue
        document_version_id = str(hit.get("document_version_id") or "")
        document, version = by_doc.get(document_id, (None, None))
        if document is None:
            continue
        metadata = hit.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        chunk_index = metadata.get("chunk_index", "?")
        source_excerpt = str(hit.get("document") or "")
        excerpt = source_excerpt[:2200]
        if not source_excerpt:
            continue
        if document_version_id and document_version_id != version.id:
            continue
        seen.add(hit_id)
        full_text = _resolve_version_text(version) or ""
        located = full_text.find(source_excerpt) if full_text else -1
        if located < 0:
            # Unmatched vector text is not an offset-bearing source quotation.
            continue
        text_sha256 = freeze_text(version.id, full_text)
        char_start = located
        char_end = located + len(excerpt)
        citation_id = make_citation_id(version.id, text_sha256, char_start, char_end)
        items.append(
            {
                "id": citation_id,
                "citation_id": citation_id,
                "text_sha256": text_sha256,
                "document_id": document.id,
                "document_name": document.name,
                "document_version_id": document_version_id or version.id,
                "space_id": document.space_id,
                "page_or_section": (
                    f"文档《{document.name}》v{version.version_number} "
                    f"向量片段#{chunk_index}"
                ),
                "excerpt": image_source_text(version, full_text, db=db, start=char_start, end=char_end),
                "support_state": (
                    _source_support_state(version, None, snapshot_text=full_text)
                    if is_image_version(version) else "uncertain"
                ),
                "retrieval_score": round(_safe_float(hit.get("score")), 4),
                "chunk_index": chunk_index if chunk_index != "?" else None,
                "char_start": char_start,
                "char_end": char_end,
            }
        )
    return items


def search_evidence(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise CapabilityGrantError("query is required")
    from app.services.evidence_inventory import inventory_evidence, is_inventory_request

    requested_space_ids = _requested_ids(arguments, "space_ids", limit=20)
    requested_documents = (
        _requested_ids(arguments, "document_ids", limit=100)
        if arguments.get("document_ids") is not None else None
    )
    spaces = resolve_space_scope(db, user, claims, requested_space_ids)
    document_ids, attachment_document_ids = _resolve_evidence_scope(
        db, user, claims, spaces, requested_documents,
    )
    scope = _scope_receipt(claims, spaces, document_ids, attachment_document_ids)
    empty_scope = (document_ids == [] or not (spaces or document_ids)) and not attachment_document_ids
    cursor = arguments.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise CapabilityGrantError("cursor must be a nonempty string")
    chapter_id = arguments.get("chapter_id", "")
    if not isinstance(chapter_id, str) or len(chapter_id) > 64:
        raise CapabilityGrantError("chapter_id must be a string of at most 64 characters")
    if cursor is not None or is_inventory_request(query) or empty_scope:
        evidence_max_bytes = claims.get("evidence_max_bytes")
        if evidence_max_bytes is None:
            evidence_max_bytes = 32_768
        if type(evidence_max_bytes) is not int or evidence_max_bytes <= 0:
            raise CapabilityGrantError("no valid remaining evidence byte budget")
        # 范围与空结果说明同样计入已有回执字节预算。
        receipt = {"scope": scope, "empty_reason": "no_documents_in_authorized_scope"}
        receipt_bytes = len(json.dumps(receipt, ensure_ascii=False).encode("utf-8"))
        try:
            result = inventory_evidence(
                db, space_ids=spaces, document_ids=document_ids, query=query,
                attachment_document_ids=attachment_document_ids,
                chapter_id=chapter_id, cursor=cursor,
                max_bytes=min(32_768, evidence_max_bytes) - receipt_bytes,
            )
        except ValueError as exc:
            raise CapabilityGrantError(str(exc)) from exc
        if result.get("next_cursor") and (
            not result.get("evidence") or result["next_cursor"] == cursor
        ):
            raise CapabilityGrantError(
                "inventory byte budget cannot advance source evidence; increase the server-side context budget"
            )
        result["scope"] = scope
        result["empty_reason"] = (
            None if result.get("evidence") else
            "empty_authorized_scope" if empty_scope else
            "no_documents_in_authorized_scope" if not result["coverage"]["expected"] else
            "text_unavailable"
        )
        return result
    top_k = _int_argument(arguments, "top_k", default=5, minimum=1, maximum=8)
    bundle = retrieve_for_qa(
        db,
        query,
        spaces,
        top_k=top_k,
        document_ids=document_ids,
        attachment_document_ids=attachment_document_ids,
        mode="auto",
        purpose="agent_evidence",
        rich_graph_context=False,
    )
    for document, _version in bundle.documents:
        _authorize_document(db, user, claims, document)
    evidence = _vector_evidence_items(bundle, limit=top_k, db=db)
    if not evidence:
        evidence = _chunk_evidence_items(bundle, query, limit=top_k, db=db)
    if not evidence:
        _, evidence = _build_evidence_blocks(bundle.documents, db=db)
    by_doc = {doc.id: doc for doc, _version in bundle.documents}
    by_version = {version.id: version for _doc, version in bundle.documents}
    creator_names = documents.creator_display_names(
        db, [document.created_by_user_id for document in by_doc.values()]
    )
    items = []
    for item in evidence:
        document_id = item.get("document_id")
        document = by_doc.get(document_id) if isinstance(document_id, str) else None
        version = (
            by_version.get(str(item.get("document_version_id") or ""))
            if item.get("document_version_id") is not None
            else None
        )
        enriched = {
            "id": item.get("id"),
            "citation_id": item.get("citation_id") or item.get("id"),
            "document_id": item.get("document_id"),
            "document_name": document.name if document else "文档",
            "document_version_id": item.get("document_version_id"),
            "space_id": document.space_id if document else None,
            "created_by_user_id": document.created_by_user_id if document else None,
            "created_by_display_name": creator_names.get(document.created_by_user_id)
            if document
            else None,
            "created_at": iso(document.created_at) if document else None,
            "page_or_section": item.get("page_or_section"),
            "excerpt": item.get("excerpt") or "",
            "support_state": _evidence_support_state(item, version),
            "chunk_index": item.get("chunk_index"),
            "char_start": item.get("char_start"),
            "char_end": item.get("char_end"),
            "retrieval_score": item.get("retrieval_score"),
            "text_sha256": item.get("text_sha256"),
            "page_number": item.get("page_number"),
            "page_block_id": item.get("page_block_id"),
            "bbox": item.get("bbox"),
        }
        if (
            enriched.get("page_number") is None
            and item.get("char_start") is not None
            and item.get("char_end") is not None
            and item.get("text_sha256")
        ):
            try:
                snapshot_version_id = str(item.get("document_version_id") or "")
                snapshot_text = read_snapshot(
                    snapshot_version_id, str(item["text_sha256"])
                )
                location = (
                    snapshot_page_location(
                        db, snapshot_version_id, snapshot_text,
                        int(item["char_start"]), int(item["char_end"]),
                    )
                    if snapshot_text is not None else None
                )
                if location is not None:
                    location_state = (
                        str(getattr(location, "support_state", "") or "")
                        .strip()
                        .lower()
                    )
                    enriched.update(
                        {
                            "page_number": location.page_number,
                            "page_block_id": location.page_block_id,
                            "bbox": location.bbox,
                            "support_state": (
                                enriched["support_state"]
                                if enriched["support_state"] in {"unsupported", "stale", "unavailable", "unlinked"}
                                else "uncertain"
                                if (
                                    enriched["support_state"] == "uncertain"
                                    or location_state == "uncertain"
                                )
                                else location_state or "supported"
                            ),
                        }
                    )
            except (TypeError, ValueError):
                pass
        items.append(enriched)
    return {
        "query": query,
        "space_ids": spaces,
        "document_ids": document_ids,
        "attachment_document_ids": attachment_document_ids,
        "scope": scope,
        "empty_reason": None if items else "no_relevant_readable_evidence",
        "evidence": items,
        "knowledge_gaps": [] if items else ["授权资料中没有找到足够相关的内容"],
        "retrieval_method": bundle.meta.get("retrieval_method"),
        "retrieval_mode": bundle.meta.get("retrieval_mode"),
        "route_reason": bundle.meta.get("route_reason"),
        "vector_used": bool(bundle.meta.get("vector_used")),
        "retrieval_scores": bundle.meta.get("scores", []),
        "coverage": {"complete": False, "mode": "relevance", "returned": len(items)},
        "result_ref": f"agent-search:{qa_repo.next_citation_id()}",
    }


def authorize_presentation(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> tuple[Document, DocumentVersion]:
    """原件必须是本轮显式选择；可读文字状态不参与授权。"""
    selected = set(_document_scope(claims) or []) | set(_attachment_scope(claims) or [])
    document_id = arguments.get("document_id")
    version_id = arguments.get("version_id")
    if not isinstance(document_id, str) or not document_id or len(document_id) > 64:
        raise CapabilityGrantError("document_id is required")
    if version_id is not None and (
        not isinstance(version_id, str) or not version_id or len(version_id) > 64
    ):
        raise CapabilityGrantError("invalid version_id")
    if not selected or document_id not in selected:
        raise CapabilityGrantError("presentation requires explicit document selection")
    document = _load_authorized_document(db, user, claims, document_id)
    if document.temporary_cleanup_at is not None:
        raise CapabilityGrantError("document is no longer available")
    if document.is_temporary or document.space_id is None:
        session_id = claims.get("conversation_id")
        if (
            not isinstance(session_id, str) or not session_id
            or document.chat_session_id != session_id
            or document.created_by_user_id != user.id
            or chat_repo.get_owned_session(db, session_id, user.id) is None
        ):
            raise CapabilityGrantError("presentation is outside the current session")
    version = (
        db.get(DocumentVersion, version_id)
        if version_id else documents.latest_version(db, document.id)
    )
    if version is None or version.document_id != document.id or not version.file_path:
        raise CapabilityGrantError("presentation version not found")
    return document, version


def authorize_image(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any],
) -> tuple[Document, DocumentVersion]:
    """图片原件须为本轮显式选择，且固定版本和实时 ACL 均有效。"""
    document_id = arguments.get("document_id")
    version_id = arguments.get("version_id")
    if not isinstance(document_id, str) or not document_id or len(document_id) > 64:
        raise CapabilityGrantError("document_id is required")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 64:
        raise CapabilityGrantError("version_id is required")
    selected = set(_document_scope(claims) or []) | set(_attachment_scope(claims) or [])
    if document_id not in selected:
        raise CapabilityGrantError("image requires explicit document selection")
    document = _load_authorized_document(db, user, claims, document_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != document.id or not version.file_path:
        raise CapabilityGrantError("image version not found")
    return document, version


def preview_document(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    document_id = str(arguments.get("document_id") or "").strip()
    version_id = str(arguments.get("version_id") or "").strip() or None
    document = _load_authorized_document(db, user, claims, document_id)
    version = _published_or_latest_version(document, version_id)
    if version is None:
        raise CapabilityGrantError("document has no readable version")
    max_chars = _int_argument(
        arguments, "max_chars", default=8_000, minimum=200, maximum=16_000
    )
    offset = _int_argument(arguments, "offset", default=0, minimum=0, maximum=2_000_000)
    requested_hash = str(arguments.get("text_sha256") or "").strip() or None
    text = read_snapshot(version.id, requested_hash) if requested_hash else _resolve_version_text(version)
    if requested_hash and text is None:
        raise CapabilityGrantError("source snapshot is unavailable")
    text = text or ""
    text_sha256 = requested_hash or (freeze_text(version.id, text) if text else None)
    window = image_source_text(version, text, db=db, start=offset, end=offset + max_chars)
    next_offset = offset + len(window) if offset + len(window) < len(text) else None
    creator_names = documents.creator_display_names(db, [document.created_by_user_id])
    return {
        "document_id": document.id,
        "space_id": document.space_id,
        "name": document.name,
        "created_by_user_id": document.created_by_user_id,
        "created_by_display_name": creator_names.get(document.created_by_user_id),
        "created_at": iso(document.created_at),
        "version_id": version.id,
        "version": version.version_number,
        "queryable": bool(version.queryable),
        "processing_stage": version.processing_stage,
        "text_sha256": text_sha256,
        "citation_id": make_citation_id(version.id, text_sha256, offset, offset + len(window))
        if window and text_sha256 else None,
        "source_state": "snapshot" if text_sha256 else "unavailable",
        "unavailable_reason": None if text else "text_unavailable",
        "scope": _scope_receipt(
            claims, [document.space_id] if document.space_id else [],
            [document.id] if document.space_id else [],
            [document.id] if document.space_id is None else [],
        ),
        "coverage": {"complete": bool(text) and offset == 0 and next_offset is None},
        "content": window,
        "offset": offset,
        "char_count": len(text),
        "truncated": next_offset is not None,
        "next_offset": next_offset,
        "untrusted": True,
    }


def expand_citation(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    citation_id = str(arguments.get("citation_id") or "").strip()
    version_id = str(arguments.get("document_version_id") or "").strip() or None
    document_id = str(arguments.get("document_id") or "").strip() or None
    char_start: int | None = None
    char_end: int | None = None
    text_sha256: str | None = None
    parsed = parse_citation_id(citation_id) if citation_id else None
    if parsed:
        parsed_version, text_sha256, char_start, char_end = parsed
        if version_id and version_id != parsed_version:
            raise CapabilityGrantError("citation version does not match request")
        version_id = parsed_version
        for key, value in (("char_start", char_start), ("char_end", char_end)):
            if arguments.get(key) is not None and arguments[key] != value:
                raise CapabilityGrantError("citation span does not match request")
    elif citation_id:
        raise CapabilityGrantError("citation_id is not an expandable source span")
    if not parsed and arguments.get("char_start") is not None:
        char_start = _int_argument(
            arguments,
            "char_start",
            default=char_start or 0,
            minimum=0,
            maximum=2_000_000,
        )
    if not parsed and arguments.get("char_end") is not None:
        char_end = _int_argument(
            arguments, "char_end", default=char_end or 0, minimum=0, maximum=2_000_000
        )
    if char_start is None:
        char_start = 0
    if char_end is None:
        char_end = char_start
    if char_end < char_start:
        raise CapabilityGrantError("char_end must be >= char_start")
    before = _int_argument(arguments, "before", default=800, minimum=0, maximum=2_000)
    after = _int_argument(arguments, "after", default=800, minimum=0, maximum=2_000)
    document, version = _load_authorized_version(
        db, user, claims, version_id=version_id, document_id=document_id
    )
    if document_id and document_id != document.id:
        raise CapabilityGrantError("citation document does not match request")
    legacy = bool(citation_id and text_sha256 is None)
    text = (
        "" if legacy else read_snapshot(version.id, text_sha256)
        if text_sha256 else _resolve_version_text(version)
    )
    if text is None:
        raise CapabilityGrantError("source snapshot is unavailable")
    if not legacy and (char_start > len(text) or char_end > len(text)):
        raise CapabilityGrantError("citation span is outside source text")
    source_state = "stale" if legacy else "snapshot"
    if not citation_id:
        text_sha256 = freeze_text(version.id, text)
    span_start = char_start
    span_end = char_end
    window_start = 0 if legacy else max(0, span_start - before)
    requested_end = min(len(text), span_end + after)
    window_end = min(requested_end, window_start + 8_000)
    excerpt = image_source_text(version, text, db=db, start=window_start, end=window_end)
    location = snapshot_page_location(db, version.id, text, span_start, span_end) if not legacy else None
    creator_names = documents.creator_display_names(db, [document.created_by_user_id])
    return {
        "citation_id": citation_id or (
            make_citation_id(version.id, text_sha256, span_start, span_end)
            if text_sha256 and span_start < span_end else None
        ),
        "text_sha256": text_sha256,
        "source_state": source_state,
        "unavailable_reason": "legacy_snapshot_unavailable" if legacy else None if text else "text_unavailable",
        "scope": _scope_receipt(
            claims, [document.space_id] if document.space_id else [],
            [document.id] if document.space_id else [],
            [document.id] if document.space_id is None else [],
        ),
        "support_state": (
            "uncertain" if legacy
            else _source_support_state(version, location, snapshot_text=text)
        ),
        "document_id": document.id,
        "document_name": document.name,
        "document_version_id": version.id,
        "space_id": document.space_id,
        "created_by_user_id": document.created_by_user_id,
        "created_by_display_name": creator_names.get(document.created_by_user_id),
        "created_at": iso(document.created_at),
        "char_start": span_start,
        "char_end": span_end,
        "window_start": window_start,
        "window_end": window_end,
        "excerpt": excerpt,
        "truncated": window_end < requested_end,
        "untrusted": True,
    }


def _entity_payload(db: Session, entity: KnowledgeEntity) -> dict[str, Any]:
    description = (entity.description or "").strip()
    return {
        "id": entity.id,
        "canonical_name": entity.canonical_name,
        "entity_type": entity.entity_type,
        "description": description[:500],
        "aliases": _parse_json_list(entity.aliases_json)[:20],
        "confidence": _safe_float(entity.confidence),
        "document_count": _safe_int(
            db.scalar(
                select(func.count()).where(
                    KnowledgeEntityDocument.entity_id == entity.id
                )
            )
            or 0
        ),
    }


def _walk_related_entity_ids(
    db: Session, space_id: str, seed_ids: set[str], *, max_hops: int, limit: int
) -> set[str]:
    visited = set(seed_ids)
    current = set(seed_ids)
    for _ in range(max_hops):
        if not current:
            break
        outgoing = set(
            db.scalars(
                select(KnowledgeRelation.object_entity_id).where(
                    KnowledgeRelation.subject_entity_id.in_(current),
                    KnowledgeRelation.space_id == space_id,
                )
            ).all()
        )
        incoming = set(
            db.scalars(
                select(KnowledgeRelation.subject_entity_id).where(
                    KnowledgeRelation.object_entity_id.in_(current),
                    KnowledgeRelation.space_id == space_id,
                )
            ).all()
        )
        nxt = (outgoing | incoming) - visited
        if not nxt:
            break
        visited.update(nxt)
        current = nxt
        if len(visited) >= limit:
            break
    return visited - seed_ids


def match_entities(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    space_id = str(arguments.get("space_id") or "").strip()
    if not space_id:
        raise CapabilityGrantError("space_id is required")
    resolve_space_scope(db, user, claims, [space_id])
    _resolve_attachment_scope(db, user, claims)
    if _document_scope(claims) is not None:
        raise CapabilityGrantError(
            "space-level entity aggregates are unavailable in a selected-document scope; use search_evidence"
        )
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise CapabilityGrantError("query is required")
    if len(query) > 500:
        raise CapabilityGrantError("query exceeds 500 characters")
    raw_types = arguments.get("entity_types")
    entity_types: list[str] = []
    if raw_types is not None:
        if not isinstance(raw_types, list) or not all(
            isinstance(item, str) for item in raw_types
        ):
            raise CapabilityGrantError("entity_types must be an array of strings")
        entity_types = [item.strip() for item in raw_types if item.strip()]
        unknown = sorted(set(entity_types) - _ALLOWED_ENTITY_TYPES)
        if unknown:
            raise CapabilityGrantError(f"unsupported entity_types: {unknown}")
    limit = _int_argument(arguments, "limit", default=20, minimum=1, maximum=50)
    max_hops = _int_argument(arguments, "max_hops", default=0, minimum=0, maximum=2)
    stmt = select(KnowledgeEntity).where(
        KnowledgeEntity.space_id == space_id,
        KnowledgeEntity.status == "active",
    )
    if entity_types:
        stmt = stmt.where(KnowledgeEntity.entity_type.in_(entity_types))
    terms = [term for term in query.split() if term]
    conditions = []
    for term in terms:
        pattern = f"%{term[:120]}%"
        conditions.append(KnowledgeEntity.canonical_name.ilike(pattern))
        conditions.append(KnowledgeEntity.aliases_json.ilike(pattern))
        conditions.append(KnowledgeEntity.description.ilike(pattern))
    if conditions:
        stmt = stmt.where(or_(*conditions))
    entities = list(
        db.scalars(stmt.order_by(KnowledgeEntity.confidence.desc()).limit(limit)).all()
    )
    entity_ids = {item.id for item in entities}
    relations: list[dict[str, Any]] = []
    if len(entity_ids) > 1:
        rows = list(
            db.scalars(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.subject_entity_id.in_(entity_ids),
                    KnowledgeRelation.object_entity_id.in_(entity_ids),
                    KnowledgeRelation.space_id == space_id,
                )
            ).all()
        )
        relations = [
            {
                "id": row.id,
                "relation_type": row.relation_type,
                "description": (row.description or "")[:300],
                "subject_id": row.subject_entity_id,
                "object_id": row.object_entity_id,
                "subject_name": row.subject_entity.canonical_name
                if row.subject_entity
                else "",
                "object_name": row.object_entity.canonical_name
                if row.object_entity
                else "",
                "confidence": _safe_float(row.confidence),
            }
            for row in rows
        ]
    related: list[dict[str, Any]] = []
    if max_hops and entity_ids:
        related_ids = _walk_related_entity_ids(
            db, space_id, entity_ids, max_hops=max_hops, limit=limit + 20
        )
        if related_ids:
            related_rows = list(
                db.scalars(
                    select(KnowledgeEntity)
                    .where(
                        KnowledgeEntity.id.in_(related_ids),
                        KnowledgeEntity.status == "active",
                    )
                    .limit(limit)
                ).all()
            )
            related = [_entity_payload(db, item) for item in related_rows]
    return {
        "space_id": space_id,
        "query": query,
        "entities": [_entity_payload(db, item) for item in entities],
        "relations": relations,
        "related": related,
        "total_matched": len(entities),
        "untrusted": True,
    }


def get_project_context(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    project_id = str(arguments.get("project_id") or "").strip()
    if not project_id:
        raise CapabilityGrantError("project_id is required")
    allowed_projects = set(_claims_list(claims, "project_ids"))
    if project_id not in allowed_projects:
        raise CapabilityGrantError("project is outside capability scope")
    from app.services.writing_membership import resolve_project_role
    from app.services.writing_scope import (
        WritingScopeUnavailableError,
        resolve_current_writing_scope,
    )

    # The acting user may be the owner or a project editor; either way the
    # space scope below is still resolved against this user's own ACL.
    resolved = resolve_project_role(db, project_id, user.id, minimum_role="editor")
    if resolved is None:
        raise CapabilityGrantError("project not found")
    project = resolved[0]
    try:
        scope = resolve_current_writing_scope(db, user=user, project=project)
    except WritingScopeUnavailableError as exc:
        raise CapabilityGrantError(str(exc)) from exc
    spaces, document_ids = scope.space_ids, scope.document_ids
    # An empty project scope must not fall back to the grant's other spaces.
    if spaces:
        resolve_space_scope(db, user, claims, spaces)
    _resolve_evidence_scope(db, user, claims, spaces, document_ids)
    from app.services.writing_brief import brief_for_prompt, decode_brief

    brief = brief_for_prompt(
        decode_brief(project.brief_json),
        revision=_safe_int(getattr(project, "brief_revision", 1) or 1, default=1),
    )
    outline = project.outline
    chapters = []
    if outline:
        chapters = [
            {"id": item.id, "position": item.position, "title": item.title}
            for item in sorted(outline.chapters, key=lambda item: item.position)
        ]
    return {
        "project_id": project.id,
        "title": project.title,
        "space_ids": spaces,
        "document_ids": document_ids,
        "brief": brief,
        "outline_status": outline.status if outline else None,
        "chapters": chapters,
        "brief_revision": _safe_int(
            getattr(project, "brief_revision", 1) or 1, default=1
        ),
    }


def list_chat_sessions(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _int_argument(arguments, "limit", default=20, minimum=1, maximum=50)
    offset = _int_argument(arguments, "offset", default=0, minimum=0, maximum=1_000_000)
    query = (
        select(ChatSession)
        .where(ChatSession.owner_user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    sessions = list(db.scalars(query).all())
    has_more = len(sessions) > limit
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "agent_session_id": item.agent_session_id,
                "writing_project_id": item.writing_project_id,
                "pinned": bool(item.pinned),
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in sessions[:limit]
        ],
        "next_offset": offset + limit if has_more else None,
    }


def get_chat_session_summary(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    requested = str(
        arguments.get("session_id") or claims.get("conversation_id") or ""
    ).strip()
    if not requested:
        raise CapabilityGrantError("session_id is required")
    bound = str(claims.get("conversation_id") or "").strip()
    # 本人历史授权与搜索、上下文读取一致；实际会话仍须通过 owner 校验。
    if (
        not bool(claims.get("chat_history"))
        and claims.get("personal_data_scope") != "own"
        and requested != bound
    ):
        raise CapabilityGrantError("session is outside capability scope")
    session = chat_repo.get_owned_session(db, requested, user.id)
    if session is None:
        raise CapabilityGrantError("session not found")
    messages = [
        {
            "role": message.role,
            "content": (message.content or "")[:4000],
            "created_at": message.created_at.isoformat()
            if message.created_at
            else None,
            "references": _parse_json_list(message.references_json),
            "tool_executions": chat_repo.message_tool_executions(message),
            "runtime_errors": chat_repo.message_runtime_errors(message),
        }
        for message in chat_repo.active_messages(session.messages or [])[-20:]
    ]
    return {
        "id": session.id,
        "title": session.title,
        "writing_project_id": session.writing_project_id,
        "messages": messages,
    }


def rename_chat_session(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """Rename one of the caller's own conversations.

    Scope is ownership rather than conversation binding: ``list_chat_sessions``
    already exposes every own title to the same grant, so renaming widens no
    read, and a skill that normalises titles has to fix more than whichever
    conversation it happens to run in. Only ``owner_user_id == user.id``
    sessions are reachable, so another user's session can never be renamed.
    """
    title = str(arguments.get("title") or "").strip()
    if not title:
        raise CapabilityGrantError("title is required")
    if len(title) > 255:
        raise CapabilityGrantError("title is too long")
    explicit_session_id = str(arguments.get("session_id") or "").strip()
    bound_conversation_id = str(claims.get("conversation_id") or "").strip()
    requested = explicit_session_id or bound_conversation_id
    if not requested:
        raise CapabilityGrantError("session_id is required")
    session = chat_repo.get_owned_session(db, requested, user.id)
    if session is None:
        if not explicit_session_id and bound_conversation_id:
            raise CapabilityGrantError(
                "session_id is required when the bound conversation is not a local Nexus session"
            )
        raise CapabilityGrantError("session not found")
    previous_title = session.title
    # An explicit rename wins over the summarizer's frozen title, exactly like
    # the browser's PATCH /chat/sessions/{id}.
    chat_repo.update_session(db, session, title=title, override_frozen_title=True)
    return {
        "id": session.id,
        "title": session.title,
        "previous_title": previous_title,
    }


def _personal_datetime_argument(arguments: dict[str, Any], key: str) -> datetime | None:
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw) > 64:
        raise CapabilityGrantError(f"{key} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CapabilityGrantError(f"{key} must be an ISO datetime") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _chat_history_embedding_function() -> Any | None:
    if not settings.qa_vector_enabled:
        return None
    try:
        from app.services.pgvector_store import build_embedding_function

        return build_embedding_function()
    except Exception as exc:
        logger.debug(
            "Personal history semantic search unavailable: %s", type(exc).__name__
        )
        return None


def search_my_chat_history(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise CapabilityGrantError("query is required")
    if len(query) > 4_000:
        raise CapabilityGrantError("query exceeds 4000 characters")
    session_id = str(arguments.get("session_id") or "").strip() or None
    if session_id and len(session_id) > 64:
        raise CapabilityGrantError("session_id exceeds 64 characters")
    top_k = _int_argument(arguments, "top_k", default=10, minimum=1, maximum=20)
    created_after = _personal_datetime_argument(arguments, "date_from")
    created_before = _personal_datetime_argument(arguments, "date_to")
    if created_after and created_before and created_after > created_before:
        raise CapabilityGrantError("date_from must be before date_to")
    started = time.monotonic()
    lexical = personal_search.search_chat_history(
        db,
        owner_user_id=user.id,
        query=query,
        session_id=session_id,
        limit=top_k,
        created_after=created_after,
        created_before=created_before,
    )
    semantic: list[personal_search.ChatHistorySearchResult] = []
    embedding_fn = _chat_history_embedding_function()
    if embedding_fn is not None:
        embedding_started = time.monotonic()
        embedding_status = "succeeded"
        embedding_usage: dict[str, object] | None = None

        def tracked_embedding(texts: list[str]) -> list[list[float]]:
            nonlocal embedding_status, embedding_usage
            try:
                vectors = embedding_fn(texts)
            except Exception:
                embedding_status = "failed"
                raise
            raw_usage = getattr(embedding_fn, "last_usage", None)
            embedding_usage = dict(raw_usage) if isinstance(raw_usage, dict) else None
            return vectors

        try:
            semantic = personal_search.search_chat_history_semantic(
                db,
                owner_user_id=user.id,
                query=query,
                embedding_fn=tracked_embedding,
                session_id=session_id,
                limit=top_k,
                created_after=created_after,
                created_before=created_before,
            )
        except Exception as exc:
            logger.debug(
                "Personal history semantic search failed: %s", type(exc).__name__
            )
        finally:
            from app.services.pricing import fill_usage_cost, find_active_price

            model = (
                getattr(embedding_fn, "_model", None) or settings.embedding_api_model
            )
            priced_usage = dict(embedding_usage or {})
            priced_usage.setdefault("provider", "embedding_api")
            priced_usage.setdefault("model", model)
            fill_usage_cost(db, priced_usage)
            if not priced_usage.get("price_version_id"):
                price = find_active_price(db, provider="embedding_api", model=model)
                if price is not None:
                    priced_usage["price_version_id"] = price.id
                    priced_usage["currency"] = price.currency
            personal_search.record_embedding_usage(
                db,
                owner_user_id=user.id,
                message_id=None,
                provider="embedding_api",
                model=model,
                usage=priced_usage,
                retry_number=0,
                status=embedding_status,
                latency_ms=int((time.monotonic() - embedding_started) * 1000),
                query_type="query",
                vector_used=embedding_status == "succeeded",
            )
    results = personal_search.fuse_chat_history_results(lexical, semantic, limit=top_k)
    personal_search.record_search_usage(
        db,
        owner_user_id=user.id,
        query_type="history",
        query=query,
        candidate_count=len({item.message_id for item in lexical + semantic}),
        result_count=len(results),
        vector_used=bool(semantic),
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return {
        "query": query,
        "items": [
            {
                "message_id": item.message_id,
                "session_id": item.session_id,
                "session_title": item.session_title,
                "role": item.role,
                "content": item.content[:800],
                "created_at": item.created_at.isoformat(),
                "citation": item.citation,
                "score": round(item.score, 4),
            }
            for item in results
        ],
        "next_offset": None,
        "retrieval_method": "hybrid" if semantic else "lexical",
        "vector_used": bool(semantic),
        "untrusted": True,
    }


def get_chat_history_context(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    message_id = str(arguments.get("message_id") or "").strip()
    if not session_id or not message_id:
        raise CapabilityGrantError("session_id and message_id are required")
    before = _int_argument(arguments, "before", default=2, minimum=0, maximum=5)
    after = _int_argument(arguments, "after", default=2, minimum=0, maximum=5)
    max_chars = _int_argument(
        arguments, "max_chars", default=4_000, minimum=200, maximum=16_000
    )
    context = personal_search.get_chat_history_context(
        db,
        owner_user_id=user.id,
        session_id=session_id,
        message_id=message_id,
        before=before,
        after=after,
        max_chars=max_chars,
    )
    if not context:
        raise CapabilityGrantError("message not found")
    stored_messages = {
        message.id: message
        for message in db.scalars(
            select(ChatMessage).where(
                ChatMessage.owner_user_id == user.id,
                ChatMessage.session_id == session_id,
                ChatMessage.id.in_([item.message_id for item in context]),
            )
        ).all()
    }
    return {
        "session_id": session_id,
        "message_id": message_id,
        "items": [
            {
                "message_id": item.message_id,
                "session_id": item.session_id,
                "role": item.role,
                "content": item.content[:max_chars],
                "created_at": item.created_at.isoformat(),
                "citation": item.citation,
                "tool_executions": chat_repo.message_tool_executions(stored)
                if (stored := stored_messages.get(item.message_id)) is not None
                else [],
                "runtime_errors": chat_repo.message_runtime_errors(stored)
                if stored is not None
                else [],
            }
            for item in context
        ],
        "untrusted": True,
    }


def list_my_uploads(
    db: Session, user: User, claims: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if len(query) > 120:
        raise CapabilityGrantError("query exceeds 120 characters")
    space_id = str(arguments.get("space_id") or "").strip() or None
    if space_id and len(space_id) > 64:
        raise CapabilityGrantError("space_id exceeds 64 characters")
    include_temporary = arguments.get("include_temporary", True)
    if not isinstance(include_temporary, bool):
        raise CapabilityGrantError("include_temporary must be a boolean")
    offset = _int_argument(arguments, "offset", default=0, minimum=0, maximum=1_000_000)
    limit = _int_argument(arguments, "limit", default=20, minimum=1, maximum=50)
    selected = _document_scope(claims)
    attachments = _attachment_scope(claims)
    if attachments is not None:
        spaces = resolve_space_scope(db, user, claims)
        selected, attachments = _resolve_evidence_scope(db, user, claims, spaces)
        if space_id:
            spaces = resolve_space_scope(db, user, claims, [space_id])
        listed_attachments = attachments if include_temporary and not space_id else []
        knowledge = Document.space_id.in_(spaces)
        if selected is not None:
            knowledge = and_(knowledge, Document.id.in_(selected))
        # 必须先在数据库约束两个分支再分页，不能先取个人全量页再过滤。
        statement = select(Document).where(
            Document.created_by_user_id == user.id,
            Document.status == "active",
            Document.temporary_cleanup_at.is_(None),
            or_(Document.expires_at.is_(None), Document.expires_at > now()),
            or_(
                knowledge,
                and_(Document.space_id.is_(None), Document.id.in_(listed_attachments)),
            ),
        )
        if not include_temporary:
            statement = statement.where(Document.is_temporary.is_(False))
        if query:
            statement = statement.where(Document.name.ilike(f"%{query}%"))
        rows = list(db.scalars(
            statement.order_by(Document.updated_at.desc(), Document.id.desc())
            .offset(offset).limit(limit + 1)
        ).all())
        next_offset = offset + limit if len(rows) > limit else None
        uploads = [
            personal_search.PersonalUploadResult(
                document_id=document.id, name=document.name, status=document.status,
                space_id=document.space_id, chat_session_id=document.chat_session_id,
                is_temporary=document.is_temporary, created_at=document.created_at,
                updated_at=document.updated_at, citation=f"document://{document.id}",
            )
            for document in rows[:limit]
        ]
        scope = {
            **_scope_receipt(claims, spaces, selected, listed_attachments),
            "created_by_user_id": user.id,
        }
    else:
        # 旧令牌的个人历史权限独立于空间选择，保留已签发授权语义。
        visible_space_ids = (
            set(resolve_space_scope(db, user, claims, [space_id] if space_id else None))
            if selected is not None
            else None if user.role == "admin" else set(_accessible_space_ids(db, user))
        )
        if selected is not None:
            _resolve_document_scope(db, user, claims, list(visible_space_ids or []))
        uploads = personal_search.list_owned_uploads(
            db,
            owner_user_id=user.id,
            query=query or None,
            space_id=space_id,
            include_temporary=include_temporary,
            visible_space_ids=visible_space_ids,
            offset=offset,
            limit=limit,
        )
        next_offset = offset + len(uploads) if len(uploads) == limit else None
        if selected is not None:
            uploads = [item for item in uploads if item.document_id in selected]
        scope = {"personal_data_scope": "own", "document_ids": selected}
    return {
        "items": [
            {
                "document_id": item.document_id,
                "name": item.name,
                "status": item.status,
                "space_id": item.space_id,
                "chat_session_id": item.chat_session_id,
                "is_temporary": item.is_temporary,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
                "citation": item.citation,
            }
            for item in uploads
        ],
        "offset": offset,
        "next_offset": next_offset,
        "scope": scope,
        "empty_reason": "empty_page_in_authorized_scope" if not uploads else None,
        "coverage": {"complete": offset == 0 and next_offset is None, "returned": len(uploads)},
        "untrusted": True,
    }


# ---------------------------------------------------------------------------
# Durable personal memory.
#
# The persistent tool results below carry state and receipt identifiers only.
# The snapshot itself reaches the model through the extension's temporary
# context block, never through this generic tool-output path, because anything
# returned here is written into the Pi transcript and would leave a second,
# uncontrolled copy of the employee's private file behind.
# ---------------------------------------------------------------------------


def _memory_turn_from_claims(
    db: Session, *, user: User, claims: dict[str, Any]
) -> tuple[UserMemoryTurn, UserMemoryState]:
    """Lock the signed input and owner before distinguishing a receipt replay.

    A completed operation remains readable after its turn closes. Only a new
    operation needs live source authority; both paths still require the current
    authenticated owner and follow user/session/state lock order.
    """

    user = require_grant_user(db, claims, for_update=True)
    turn_id = claims.get("memory_turn_id")
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise CapabilityGrantError("memory grant has no current input")
    turn = user_memory_repo.get_owned_turn(
        db, owner_user_id=user.id, turn_id=turn_id.strip()
    )
    if turn is None:
        raise CapabilityGrantError("memory grant names an unknown input")
    if turn.chat_session_id:
        locked = db.execute(
            update(ChatSession)
            .where(
                ChatSession.id == turn.chat_session_id,
                ChatSession.owner_user_id == user.id,
            )
            .values(messages_revision=ChatSession.messages_revision)
        )
        if locked.rowcount != 1:
            raise CapabilityGrantError("memory grant session is no longer available")
    state = user_memory_repo.lock_owned_state(db, user.id)
    db.refresh(turn)
    return turn, state


def _require_live_memory_turn(
    db: Session,
    *,
    claims: dict[str, Any],
    turn: UserMemoryTurn,
    state: UserMemoryState,
) -> None:
    """Recheck mutable authority only when creating a new operation."""
    if turn.status != "live":
        raise CapabilityGrantError("memory grant input is no longer live")
    claimed_hash = claims.get("memory_source_hash")
    if not isinstance(claimed_hash, str) or claimed_hash != turn.source_hash:
        raise CapabilityGrantError("memory grant input changed")
    claimed_epoch = claims.get("memory_epoch")
    if (
        type(claimed_epoch) is not int
        or claimed_epoch != state.epoch
        or turn.epoch != state.epoch
    ):
        raise CapabilityGrantError("memory grant was revoked")
    conversation_id = claims.get("conversation_id")
    if (
        isinstance(conversation_id, str)
        and conversation_id
        and turn.runtime_session_id
        and turn.runtime_session_id != conversation_id
    ):
        raise CapabilityGrantError("memory grant input belongs to another run")
    # Re-read the live source: an edited or deleted message is not a source,
    # whatever the cached turn row still says.
    if user_memory_service.resolve_turn_source_text(db, turn) is None:
        raise CapabilityGrantError("memory grant input is no longer available")
    if not state.use_enabled or not settings.personal_memory_enabled:
        raise CapabilityGrantError("memory learning is disabled")


def get_my_memory(
    db: Session,
    user: User,
    claims: dict[str, Any],
    arguments: dict[str, Any],
    *,
    tool_request_id: str | None = None,
) -> dict[str, Any]:
    """Read-only state check. Needs no live turn, so an older Open API request
    without a request id can still use memory it already has."""

    state = user_memory_repo.get_or_create_state(db, user.id)
    entries = (
        user_memory_repo.list_active_entries(db, owner_user_id=user.id)
        if state.use_enabled and settings.personal_memory_enabled
        else []
    )
    return {
        "ok": True,
        "use_enabled": state.use_enabled and settings.personal_memory_enabled,
        "learning_mode": state.learning_mode,
        "revision": state.revision,
        "epoch": state.epoch,
        # A count, not the contents. The values are already in this run's
        # context block; repeating them here would persist them again.
        "active_count": len(entries),
        "keys": sorted({entry.key for entry in entries}),
    }


def propose_my_memory(
    db: Session,
    user: User,
    claims: dict[str, Any],
    arguments: dict[str, Any],
    *,
    tool_request_id: str | None = None,
) -> dict[str, Any]:
    """Submit a candidate drawn from the current, server-bound input."""

    turn, state = _memory_turn_from_claims(db, user=user, claims=claims)
    try:
        proposal = MemoryProposalInput.model_validate(arguments)
    except ValidationError as exc:
        raise CapabilityGrantError("invalid memory proposal") from exc

    request_id = _memory_request_id(claims, tool_request_id, turn_id=turn.id)
    payload_hash = user_memory_repo.request_hash(
        {"turn_id": turn.id, **proposal.model_dump()}
    )
    operation, replay = user_memory_repo.claim_operation(
        db,
        owner_user_id=user.id,
        kind="propose",
        request_id=request_id,
        payload_hash=payload_hash,
        turn_id=turn.id,
    )
    if replay:
        # A retried tool call reports the receipt it already produced instead
        # of raising a second candidate for the same sentence.
        receipt = user_memory_service.operation_receipt(
            db, operation, revision=state.revision
        )
        current = (
            user_memory_repo.get_owned_entry(
                db, owner_user_id=user.id, memory_id=operation.result_memory_id
            )
            if operation.result_memory_id and not receipt.revoked
            else None
        )
        return {
            "ok": operation.status == "succeeded",
            "replay": True,
            "candidate_id": current.id if current else None,
            "state": (
                "saved"
                if current and current.status == "active"
                else "pending"
                if current and current.status == "candidate"
                else "revoked"
                if receipt.revoked
                else "unavailable"
            ),
            "revision": state.revision,
        }

    _require_live_memory_turn(db, claims=claims, turn=turn, state=state)

    try:
        value, quote = user_memory_service.validate_memory_proposal(
            db, owner_user_id=user.id, turn=turn, proposal=proposal
        )
    except user_memory_service.MemoryPolicyError as exc:
        raise CapabilityGrantError(exc.code) from exc

    active = user_memory_repo.get_active_entry(
        db, owner_user_id=user.id, key=proposal.key
    )
    entry = user_memory_repo.propose_entry(
        db,
        owner_user_id=user.id,
        key=proposal.key,
        value=value,
        origin="explicit" if proposal.explicitness == "explicit" else "automatic",
        turn_id=turn.id,
        quote=quote,
        quote_start=proposal.quote_start,
        quote_end=proposal.quote_end,
        expires_at=user_memory_service.default_expiry(proposal.key),
        auto_confirm=user_memory_service.apply_memory_policy(
            state, proposal=proposal, has_conflicting_active=active is not None
        ),
    )
    saved = entry.status == "active"
    state = user_memory_repo.get_or_create_state(db, user.id)
    user_memory_repo.complete_operation(
        db,
        operation=operation,
        status="succeeded",
        result_memory_id=entry.id,
        result_revision=state.revision,
    )
    return {
        "ok": True,
        "candidate_id": entry.id,
        # "pending" means the employee still has to confirm in 我的记忆.
        # Saying "已记住" on the strength of this would be a lie.
        "state": "saved" if saved else "pending",
        "revision": state.revision,
    }


def propose_forget_my_memory(
    db: Session,
    user: User,
    claims: dict[str, Any],
    arguments: dict[str, Any],
    *,
    tool_request_id: str | None = None,
) -> dict[str, Any]:
    """Raise a forget request for one key; the employee confirms it in the UI.

    Nothing is erased here. There is no "forget everything" argument either:
    clearing memory is a deliberate action with its own confirmation, not
    something a model can trigger from a sentence.
    """

    turn, state = _memory_turn_from_claims(db, user=user, claims=claims)
    try:
        request = MemoryForgetProposalInput.model_validate(arguments)
    except ValidationError as exc:
        raise CapabilityGrantError("invalid forget proposal") from exc

    target = user_memory_repo.get_active_entry(
        db, owner_user_id=user.id, key=request.key
    )
    request_id = _memory_request_id(claims, tool_request_id, turn_id=turn.id)
    operation, replay = user_memory_repo.claim_operation(
        db,
        owner_user_id=user.id,
        kind="forget_proposal",
        request_id=request_id,
        payload_hash=user_memory_repo.request_hash(
            {"turn_id": turn.id, **request.model_dump()}
        ),
        turn_id=turn.id,
        memory_id=target.id if target else None,
    )
    if not replay:
        _require_live_memory_turn(db, claims=claims, turn=turn, state=state)
        source_text = user_memory_service.resolve_turn_source_text(db, turn)
        if source_text is None:
            raise CapabilityGrantError("memory grant input is no longer available")
        try:
            user_memory_service.verify_quote(
                source_text,
                quote=request.quote,
                quote_start=request.quote_start,
                quote_end=request.quote_end,
            )
        except user_memory_service.MemoryPolicyError as exc:
            raise CapabilityGrantError(exc.code) from exc
        user_memory_repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=target.id if target else None,
            result_revision=state.revision,
        )
    receipt = user_memory_service.operation_receipt(
        db, operation, revision=state.revision
    )
    return {
        "ok": operation.status == "succeeded",
        "memory_id": receipt.memory_id if not receipt.revoked else None,
        "state": "needs_confirmation"
        if receipt.memory_id and not receipt.revoked
        else "nothing_to_forget",
        "revision": state.revision,
    }


def _memory_request_id(
    claims: dict[str, Any], request_id: str | None, *, turn_id: str
) -> str:
    """Namespace the tool's request id under the server-bound turn.

    The model may repeat or invent a request id; anchoring it to the turn the
    backend chose means a replay can only ever collapse onto the same input,
    never onto a different employee's or a different sentence's.
    """

    suffix = (
        request_id.strip()
        if request_id and request_id.strip()
        else claims.get("jti", "")
    )
    return f"{turn_id}:{str(suffix)[:64]}"


def execute_capability(
    db: Session,
    *,
    user: User,
    claims: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    expected_agent_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    validate_claims(
        claims,
        tool_name=tool_name,
        expected_agent_id=expected_agent_id,
    )
    if tool_name == "list_spaces":
        return list_spaces(db, user, claims, arguments)
    if tool_name == "list_documents":
        return list_documents(db, user, claims, arguments)
    if tool_name == "search_evidence":
        return search_evidence(db, user, claims, arguments)
    if tool_name == "preview_document":
        return preview_document(db, user, claims, arguments)
    if tool_name == "expand_citation":
        return expand_citation(db, user, claims, arguments)
    if tool_name == "match_entities":
        return match_entities(db, user, claims, arguments)
    if tool_name == "get_project_context":
        return get_project_context(db, user, claims, arguments)
    if tool_name == "list_chat_sessions":
        return list_chat_sessions(db, user, claims, arguments)
    if tool_name == "get_chat_session_summary":
        return get_chat_session_summary(db, user, claims, arguments)
    if tool_name == "rename_chat_session":
        return rename_chat_session(db, user, claims, arguments)
    if tool_name == "mes_root_cause":
        from app.services import root_cause

        causes = root_cause.analyze(db)
        return {
            "text": root_cause.render_all(causes),
            "count": len(causes),
            "causes": [cause.as_dict() for cause in causes],
        }

    if tool_name in ("mes_owner_profile", "mes_weekly_review"):
        from app.services import owner_profile, weekly_review

        if tool_name == "mes_weekly_review":
            review = weekly_review.build_review(db)
            return {"text": review.render(), **review.as_dict()}
        name = str(arguments.get("person") or "").strip() if isinstance(arguments, dict) else ""
        target = owner_profile.find_user(db, name) or user
        profile = owner_profile.build_profile(db, user=target)
        return {"text": profile.render(), **profile.as_dict()}

    if tool_name in PLAN_CAPABILITY_TOOLS:
        from app.services import assistant_watch

        limit = arguments.get("limit") if isinstance(arguments, dict) else None
        try:
            limit = max(1, min(int(limit or 3), 10))
        except (TypeError, ValueError):
            limit = 3
        plan = assistant_watch.today_plan(db, user=user, limit=limit)
        return {
            "text": plan["summary"],
            "total": plan.get("total", 0),
            "counts": plan.get("counts", {}),
            "top": [
                {
                    "title": item.get("title"),
                    "impact": item.get("impact"),
                    "steps": item.get("steps"),
                    "ageHours": round(float(item.get("age_hours") or 0), 1),
                }
                for item in plan.get("top", [])
            ],
        }
    if tool_name in MES_CAPABILITY_TOOLS:
        from app.services import mes_capability

        handler = {
            "mes_query_panel": mes_capability.query_panel,
            "mes_trace_doc": mes_capability.trace_doc,
            "mes_doc_detail": mes_capability.doc_detail,
            "mes_stock": mes_capability.stock,
        }[tool_name]
        try:
            return handler(arguments)
        except mes_capability.MesCapabilityError as exc:
            raise CapabilityGrantError(str(exc)) from exc
    if tool_name in SCHEDULE_CAPABILITY_TOOLS:
        from app.services.schedule_proposals import (
            get_schedule_summary,
            propose_schedule,
            propose_schedule_action,
        )

        if tool_name == "get_schedule_summary":
            return get_schedule_summary(
                db,
                owner_user_id=user.id,
                claims=claims,
                arguments=arguments,
            )
        if tool_name == "propose_schedule":
            return propose_schedule(
                db,
                owner_user_id=user.id,
                claims=claims,
                arguments=arguments,
                tool_request_id=request_id,
            )
        return propose_schedule_action(
            db,
            owner_user_id=user.id,
            claims=claims,
            arguments=arguments,
            tool_request_id=request_id,
        )
    if tool_name == "search_my_chat_history":
        return search_my_chat_history(db, user, claims, arguments)
    if tool_name == "get_chat_history_context":
        return get_chat_history_context(db, user, claims, arguments)
    if tool_name == "list_my_uploads":
        return list_my_uploads(db, user, claims, arguments)
    if tool_name == "get_my_memory":
        return get_my_memory(db, user, claims, arguments, tool_request_id=request_id)
    if tool_name == "propose_my_memory":
        return propose_my_memory(
            db, user, claims, arguments, tool_request_id=request_id
        )
    if tool_name == "propose_forget_my_memory":
        return propose_forget_my_memory(
            db, user, claims, arguments, tool_request_id=request_id
        )
    if tool_name == "get_system_overview":
        try:
            return get_system_overview(db, user)
        except SystemInsightError as exc:
            raise CapabilityGrantError(str(exc)) from exc
    if tool_name == "list_system_users":
        try:
            return list_system_users(db, user, arguments)
        except SystemInsightError as exc:
            raise CapabilityGrantError(str(exc)) from exc
    if tool_name == "get_user_usage":
        try:
            return get_user_usage(db, user, arguments)
        except SystemInsightError as exc:
            raise CapabilityGrantError(str(exc)) from exc
    raise CapabilityGrantError("unknown capability tool")


__all__ = [
    "CAPABILITY_TOOLS",
    "CHAT_CAPABILITY_TOOLS",
    "SCHEDULE_CAPABILITY_TOOLS",
    "SYSTEM_CAPABILITY_TOOLS",
    "WRITING_CAPABILITY_TOOLS",
    "PERSONAL_CAPABILITY_TOOLS",
    "MEMORY_CAPABILITY_TOOLS",
    "MEMORY_WRITE_CAPABILITY_TOOLS",
    "CapabilityGrantError",
    "authorize_presentation",
    "chat_capability_tools",
    "create_capability_token",
    "decode_capability_token",
    "execute_capability",
]
