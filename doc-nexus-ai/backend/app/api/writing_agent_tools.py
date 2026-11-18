from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from jwt.exceptions import PyJWTError

from app.core.config import settings
from app.core.database import transaction
from app.models.writing import WritingOperation
from app.repositories import users, writing
from app.schemas.writing_agent import ToolEnvelope, ToolResult
from app.services import mock_evidence, writing_evidence
from app.services.writing_membership import resolve_project_role
from app.services.writing_scope import (
    WritingScopeUnavailableError,
    decode_scope_token,
    resolve_current_writing_scope,
    scope_hash,
)

router = APIRouter(prefix="/internal/v1", tags=["internal-writing-agent-tools"])

_AGENT_TOOL_SLUGS = {
    "project-brief": "get_project_brief",
    "confirmed-outline": "get_confirmed_outline",
    "rewrite-context": "get_rewrite_context",
    "search-evidence": "search_evidence",
    "save-outline-candidate": "save_outline_candidate",
    "save-section-candidate": "save_section_candidate",
    "save-verification": "save_verification_result",
    "operation-budget": "get_operation_budget",
    "emit-event": "emit_run_event",
    "finalize-document-candidate": "finalize_document_candidate",
}

_TOOL_POLICY = {
    "outline": {
        "doc_nexus_writing_coordinator": {
            "get_project_brief",
            "get_operation_budget",
            "emit_run_event",
        },
        "doc_nexus_outline_agent": {
            "get_project_brief",
            "search_evidence",
            "save_outline_candidate",
            "emit_run_event",
        },
    },
    "document": {
        "doc_nexus_writing_coordinator": {
            "get_project_brief",
            "get_confirmed_outline",
            "get_operation_budget",
            "emit_run_event",
            "finalize_document_candidate",
        },
        "doc_nexus_research_agent": {
            "get_confirmed_outline",
            "search_evidence",
            "emit_run_event",
        },
        "doc_nexus_writer_agent": {
            "get_project_brief",
            "get_confirmed_outline",
            "search_evidence",
            "save_section_candidate",
            "emit_run_event",
        },
        "doc_nexus_verifier_agent": {
            "get_project_brief",
            "get_confirmed_outline",
            "search_evidence",
            "save_verification_result",
            "emit_run_event",
        },
        "doc_nexus_rewrite_agent": {
            "get_confirmed_outline",
            "search_evidence",
            "save_section_candidate",
            "emit_run_event",
        },
    },
    "rewrite": {
        "doc_nexus_writing_coordinator": {
            "get_project_brief",
            "get_confirmed_outline",
            "get_rewrite_context",
            "get_operation_budget",
            "emit_run_event",
        },
        "doc_nexus_rewrite_agent": {
            "get_confirmed_outline",
            "get_rewrite_context",
            "save_section_candidate",
            "emit_run_event",
        },
        "doc_nexus_verifier_agent": {
            "get_confirmed_outline",
            "get_rewrite_context",
            "save_verification_result",
            "emit_run_event",
        },
    },
    "verify": {
        "doc_nexus_writing_coordinator": {
            "get_project_brief",
            "get_confirmed_outline",
            "get_operation_budget",
            "emit_run_event",
        },
        "doc_nexus_verifier_agent": {
            "get_project_brief",
            "get_confirmed_outline",
            "search_evidence",
            "save_verification_result",
            "emit_run_event",
        },
    },
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _token(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing writing scope token")
    try:
        return decode_scope_token(authorization[7:])
    except PyJWTError:
        raise HTTPException(
            status_code=401, detail="invalid writing scope token"
        ) from None


def _validated_context(
    db, envelope: ToolEnvelope, claims: dict[str, Any], tool_name: str
):
    expected = {
        "writing_operation_id": envelope.writing_operation_id,
        "project_id": envelope.project_id,
        "scope_hash": envelope.scope_hash,
        "team_id": envelope.team_id,
        "team_version": envelope.team_version,
        "workflow": envelope.workflow,
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise HTTPException(
            status_code=403, detail="run scope does not match tool envelope"
        )
    if tool_name not in claims.get("allowed_tools", []):
        raise HTTPException(status_code=403, detail="tool is outside run scope")
    allowed = _TOOL_POLICY.get(envelope.workflow, {}).get(envelope.agent_name, set())
    if envelope.stage != envelope.workflow or tool_name not in allowed:
        raise HTTPException(
            status_code=403,
            detail="agent tool call is outside workflow policy",
        )

    operation = writing.operation_by_id(db, envelope.writing_operation_id)
    if operation is None or operation.project_id != envelope.project_id:
        raise HTTPException(status_code=404, detail="writing operation not found")
    if operation.status in {"cancelling", "cancelled", "failed", "stopped"}:
        raise HTTPException(
            status_code=409, detail="writing operation no longer accepts tools"
        )
    binding = writing.binding_for_platform_run(db, envelope.platform_run_id)
    if binding is None or binding.writing_operation_id != operation.id:
        raise HTTPException(status_code=409, detail="platform run is not bound")
    resolved = resolve_project_role(
        db, envelope.project_id, claims["sub"], minimum_role="editor"
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="writing project not found")
    project = resolved[0]
    user = users.get_by_id(db, claims["sub"])
    if user is None or user.status != "active":
        raise HTTPException(status_code=403, detail="scope owner is no longer active")
    try:
        current_scope = resolve_current_writing_scope(
            db,
            user=user,
            project=project,
        )
    except WritingScopeUnavailableError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    current_hash = scope_hash(
        project.owner_user_id,
        project.id,
        current_scope.space_ids,
        current_scope.document_ids,
    )
    if current_hash != claims["scope_hash"]:
        raise HTTPException(status_code=403, detail="knowledge scope changed")
    return operation, project


def _result(
    ref: str, inline: dict[str, Any] | list[Any], summary: str
) -> dict[str, Any]:
    digest = _sha(inline)
    return ToolResult(
        ok=True, result_ref=ref, sha256=digest, summary=summary, inline=inline
    ).model_dump(mode="json")


def _execute_tool(
    db,
    *,
    tool_name: str,
    envelope: ToolEnvelope,
    operation: WritingOperation,
    project,
) -> dict[str, Any]:
    args = envelope.arguments
    if tool_name == "get_project_brief":
        from app.services.writing_brief import decode_brief

        brief = decode_brief(getattr(project, "brief_json", None))
        inline = {
            "project_id": project.id,
            "title": project.title,
            "space_ids": writing.load_space_ids(project.selected_space_ids),
            "document_ids": writing.load_document_ids(
                getattr(project, "selected_document_ids", None)
            ),
            "budget_limit": project.budget_limit,
            "brief_revision": int(getattr(project, "brief_revision", 1) or 1),
            "brief": brief,
        }
        return _result(f"brief_{project.id}", inline, "project brief loaded")

    if tool_name == "get_confirmed_outline":
        if not project.outline or project.outline.status != "confirmed":
            raise HTTPException(status_code=409, detail="outline is not confirmed")
        chapters = [
            {"id": item.id, "position": item.position, "title": item.title}
            for item in sorted(project.outline.chapters, key=lambda item: item.position)
        ]
        return _result(
            f"outline_{project.outline.id}",
            {"chapters": chapters},
            "confirmed outline loaded",
        )

    if tool_name == "get_rewrite_context":
        if operation.kind != "rewrite":
            raise HTTPException(status_code=409, detail="operation is not a rewrite")
        payload = json.loads(operation.input_payload)
        chapter_id = payload.get("chapter_id")
        chapter = (
            writing.chapter_for_project(db, project.id, chapter_id)
            if isinstance(chapter_id, str)
            else None
        )
        if chapter is None:
            raise HTTPException(
                status_code=409, detail="rewrite chapter no longer exists"
            )
        if len(chapter.versions) != operation.input_revision:
            raise HTTPException(
                status_code=409, detail="rewrite source revision is stale"
            )
        inline = {
            "chapter_id": chapter_id,
            "instruction": payload.get("instruction"),
            "selection_start": payload.get("selection_start"),
            "selection_end": payload.get("selection_end"),
            "selected_text": payload.get("selected_text"),
            "source_content": payload.get("source_content"),
            "source_revision": operation.input_revision,
            "fact_locks": payload.get("fact_locks", []),
        }
        return _result(f"rewrite_{operation.id}", inline, "rewrite context loaded")

    if tool_name == "search_evidence":
        for key in ("chapter_id", "query", "purpose"):
            if not isinstance(args.get(key), str) or not args[key].strip():
                raise HTTPException(status_code=422, detail=f"missing argument: {key}")
        provider = settings.writing_evidence_provider.strip().lower()
        payload = (
            mock_evidence.search(
                chapter_id=args["chapter_id"],
                query=args["query"],
                purpose=args["purpose"],
            )
            if provider == "mock"
            else writing_evidence.search(
                db,
                space_ids=writing.load_space_ids(project.selected_space_ids),
                document_ids=writing.load_document_ids(
                    getattr(project, "selected_document_ids", None)
                )
                or None,
                chapter_id=args["chapter_id"],
                query=args["query"],
                purpose=args["purpose"],
            )
        )
        return ToolResult(
            ok=True,
            result_ref=payload["result_ref"],
            sha256=payload["sha256"],
            summary=f"{len(payload['evidence'])} evidence items",
            inline=payload,
        ).model_dump(mode="json")

    if tool_name == "save_outline_candidate":
        if not isinstance(args.get("outline"), dict):
            raise HTTPException(status_code=422, detail="outline must be an object")
        candidate = writing.save_candidate(
            db,
            operation=operation,
            platform_run_id=envelope.platform_run_id,
            tool_call_id=envelope.tool_call_id,
            kind="outline",
            chapter_id=None,
            payload=args["outline"],
        )
        return _result(
            candidate.id,
            {"candidate_revision": candidate.candidate_revision},
            "outline candidate saved",
        )

    if tool_name == "save_section_candidate":
        chapter_id = args.get("chapter_id")
        if not isinstance(chapter_id, str) or not writing.chapter_for_project(
            db, project.id, chapter_id
        ):
            raise HTTPException(status_code=422, detail="invalid chapter_id")
        if not isinstance(args.get("markdown"), str):
            raise HTTPException(status_code=422, detail="markdown must be a string")
        citations = args.get("citations", [])
        if not isinstance(citations, list) or not all(
            isinstance(item, str) for item in citations
        ):
            raise HTTPException(status_code=422, detail="citations must be strings")
        rewrite_metadata: dict[str, Any] = {}
        required_citations: set[str] = set()
        if operation.kind == "rewrite":
            rewrite_input = json.loads(operation.input_payload)
            if chapter_id != rewrite_input.get("chapter_id"):
                raise HTTPException(
                    status_code=422, detail="rewrite cannot change another chapter"
                )
            required_citations = {
                citation
                for lock in rewrite_input.get("fact_locks", [])
                if isinstance(lock, dict)
                for citation in lock.get("citation_ids", [])
                if isinstance(citation, str)
            }
            if not required_citations.issubset(set(citations)):
                raise HTTPException(
                    status_code=422,
                    detail="rewrite candidate dropped a fact-lock citation",
                )
            rewrite_metadata = {
                "rewrite_of_revision": operation.input_revision,
                "selection_start": rewrite_input.get("selection_start"),
                "selection_end": rewrite_input.get("selection_end"),
            }
        allowed_citations = writing.evidence_ids_for_run(db, envelope.platform_run_id)
        allowed_citations.update(required_citations)
        if not set(citations).issubset(allowed_citations):
            raise HTTPException(
                status_code=422,
                detail="candidate contains citations outside retrieved evidence",
            )
        candidate = writing.save_candidate(
            db,
            operation=operation,
            platform_run_id=envelope.platform_run_id,
            tool_call_id=envelope.tool_call_id,
            kind="section",
            chapter_id=chapter_id,
            payload={
                "markdown": args["markdown"],
                "citations": citations,
                **rewrite_metadata,
            },
        )
        return _result(
            candidate.id,
            {"candidate_revision": candidate.candidate_revision},
            "section candidate saved",
        )

    if tool_name == "save_verification_result":
        chapter_id = args.get("chapter_id")
        if not isinstance(chapter_id, str) or not writing.chapter_for_project(
            db, project.id, chapter_id
        ):
            raise HTTPException(status_code=422, detail="invalid chapter_id")
        candidate = writing.save_candidate(
            db,
            operation=operation,
            platform_run_id=envelope.platform_run_id,
            tool_call_id=envelope.tool_call_id,
            kind="verification",
            chapter_id=chapter_id,
            payload=args,
        )
        return _result(
            candidate.id,
            {"candidate_revision": candidate.candidate_revision},
            "verification result saved",
        )

    if tool_name == "get_operation_budget":
        return _result(
            f"budget_{operation.id}",
            {"budget_limit": project.budget_limit, "reservation_id": None},
            "operation budget loaded",
        )

    if tool_name == "emit_run_event":
        return _result(
            f"event_{envelope.tool_call_id}",
            {"accepted": True, "event_type": args.get("event_type")},
            "run event accepted",
        )

    if tool_name == "finalize_document_candidate":
        candidate = writing.save_candidate(
            db,
            operation=operation,
            platform_run_id=envelope.platform_run_id,
            tool_call_id=envelope.tool_call_id,
            kind="document",
            chapter_id=None,
            payload={"chapter_ids": args.get("chapter_ids", [])},
        )
        return _result(
            candidate.id,
            {"candidate_revision": candidate.candidate_revision},
            "document candidate finalized",
        )

    raise HTTPException(status_code=404, detail="unknown writing tool")


@router.post("/writing/tools/{tool_name}", response_model=ToolResult)
def invoke_writing_tool(
    tool_name: str,
    envelope: ToolEnvelope,
    authorization: Annotated[str | None, Header()] = None,
) -> ToolResult:
    claims = _token(authorization)
    logical_request = {
        "platform_run_id": envelope.platform_run_id,
        "writing_operation_id": envelope.writing_operation_id,
        "project_id": envelope.project_id,
        "action_key": envelope.action_key,
        "tool_name": tool_name,
        "arguments": envelope.arguments,
    }
    request_sha256 = _sha(logical_request)
    with transaction() as db:
        operation, project = _validated_context(db, envelope, claims, tool_name)
        existing = writing.tool_receipt(
            db, envelope.platform_run_id, envelope.action_key
        )
        if existing:
            if existing.request_sha256 != request_sha256:
                raise HTTPException(
                    status_code=409,
                    detail="action_key already used with different arguments",
                )
            existing.attempt_count += 1
            db.flush()
            return ToolResult.model_validate_json(existing.result_json)
        result = _execute_tool(
            db,
            tool_name=tool_name,
            envelope=envelope,
            operation=operation,
            project=project,
        )
        writing.save_tool_result(
            db,
            platform_run_id=envelope.platform_run_id,
            tool_call_id=envelope.tool_call_id,
            action_key=envelope.action_key,
            tool_name=tool_name,
            request_sha256=request_sha256,
            result=result,
        )
        return ToolResult.model_validate(result)


@router.post("/agent-tools/{tool_slug}", response_model=ToolResult)
def invoke_named_agent_tool(
    tool_slug: str,
    envelope: ToolEnvelope,
    authorization: Annotated[str | None, Header()] = None,
) -> ToolResult:
    tool_name = _AGENT_TOOL_SLUGS.get(tool_slug)
    if tool_name is None:
        raise HTTPException(status_code=404, detail="unknown writing tool")
    return invoke_writing_tool(tool_name, envelope, authorization)


@router.get(
    "/agent-tools/receipts/{platform_run_id}/{action_key:path}",
    response_model=ToolResult,
)
def get_tool_receipt(
    platform_run_id: str,
    action_key: str,
    authorization: Annotated[str | None, Header()] = None,
) -> ToolResult:
    claims = _token(authorization)
    with transaction() as db:
        binding = writing.binding_for_platform_run(db, platform_run_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="platform run not found")
        operation = writing.operation_by_id(db, binding.writing_operation_id)
        if operation is None or operation.id != claims.get("writing_operation_id"):
            raise HTTPException(status_code=403, detail="receipt is outside run scope")
        receipt = writing.tool_receipt(db, platform_run_id, action_key)
        if receipt is None:
            raise HTTPException(status_code=404, detail="tool receipt not found")
        return ToolResult.model_validate_json(receipt.result_json)
