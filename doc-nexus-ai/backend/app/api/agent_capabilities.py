from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from jwt.exceptions import PyJWTError
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.core.database import transaction
from app.repositories.audit import record
from app.repositories.user_memory import MemoryRepositoryError
from app.schemas.agent_capability import AgentCapabilityRequest
from app.services.agent_capability import (
    CapabilityGrantError,
    authorize_image,
    authorize_presentation,
    decode_capability_token,
    execute_capability,
    validate_claims,
    require_grant_user,
)
from app.services.schedule_proposals import ScheduleProposalError
from app.services.user_memory import describe_error
from app.services.presentation_original import (
    CHUNK_BYTES,
    PresentationOriginalError,
    copy_original,
    presentation_format,
)
from app.services.image_documents import (
    IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, ImageDocumentError, validate_image_document,
)

router = APIRouter(prefix="/internal/v1/agent-capabilities", tags=["internal-agent-capabilities"])


def _claims(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing capability grant")
    try:
        return decode_capability_token(authorization[7:].strip())
    except (PyJWTError, RuntimeError):
        raise HTTPException(status_code=401, detail="invalid capability grant") from None


def _result(tool_name: str, inline: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(
        inline, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "ok": True,
        "tool": tool_name,
        "result_ref": inline.get("result_ref") or f"agent-capability:{tool_name}",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "inline": inline,
    }



@router.post("/history/recall")
def recall_chat_history(
    authorization: Annotated[str | None, Header()] = None,
    x_nexus_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """从签名 grant 所绑定的已提交提问中查找本人旧话题，不接受模型提供的查询。"""
    claims = _claims(authorization)
    try:
        validate_claims(
            claims,
            tool_name="search_my_chat_history",
            expected_agent_id=(x_nexus_agent_id or "").strip() or None,
        )
        if not x_nexus_agent_id or x_nexus_agent_id != claims.get("agent_id"):
            raise CapabilityGrantError("agent binding is required")
        session_id = claims.get("conversation_id")
        message_id = claims.get("history_turn_message_id")
        if not isinstance(session_id, str) or not isinstance(message_id, str):
            raise CapabilityGrantError("history recall requires a bound turn")
        if len(session_id) > 64 or len(message_id) > 64:
            raise CapabilityGrantError("invalid history turn")
        with transaction() as db:
            user = require_grant_user(db, claims)
            from app.services.chat_history_recall import recall_prior_topic

            items = recall_prior_topic(
                db,
                owner_user_id=user.id,
                session_id=session_id,
                message_id=message_id,
            )
            if items:
                record(
                    db, actor=user.id, event_type="agent_capability_call",
                    target="history_recall", result="success",
                )
            return {"items": items}
    except CapabilityGrantError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

@router.post("/read_presentation")
def read_presentation(
    body: AgentCapabilityRequest,
    authorization: Annotated[str | None, Header()] = None,
    x_nexus_agent_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """短期 capability 原件传输；日志只记录调用，不含二进制或宿主路径。"""
    stack = ExitStack()
    try:
        def authorized(db):
            claims = _claims(authorization)
            validate_claims(
                claims, tool_name="read_presentation",
                expected_agent_id=x_nexus_agent_id,
            )
            if not x_nexus_agent_id or x_nexus_agent_id != claims.get("agent_id"):
                raise CapabilityGrantError("agent binding is required")
            user = require_grant_user(db, claims)
            document, version = authorize_presentation(db, user, claims, body.arguments)
            return user, document, version

        with transaction() as db:
            _, document, version = authorized(db)
            document_id, version_id = document.id, version.id
            file_path = version.file_path
            source_format = presentation_format(version.source_relative_path, file_path)
        source, size, digest = stack.enter_context(copy_original(file_path))
        # 拷贝可能耗时；重新查库及验证 grant，避免撤权/清理后交付。
        with transaction() as db:
            user, document, version = authorized(db)
            if (
                document.id != document_id or version.id != version_id
                or version.file_path != file_path
                or presentation_format(version.source_relative_path, version.file_path) != source_format
            ):
                raise CapabilityGrantError("presentation source changed")
            record(
                db, actor=user.id, event_type="agent_capability_call",
                target="read_presentation", result="success",
            )

        def chunks():
            try:
                while chunk := source.read(CHUNK_BYTES):
                    yield chunk
            finally:
                stack.close()

        return StreamingResponse(
            chunks(), media_type="application/octet-stream",
            headers={
                "Content-Length": str(size),
                "X-Nexus-Document-ID": document_id,
                "X-Nexus-Version-ID": version_id,
                "X-Nexus-Source-Format": source_format,
                "X-Nexus-SHA256": digest,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(stack.close),
        )
    except (CapabilityGrantError, PresentationOriginalError) as exc:
        stack.close()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except BaseException:
        stack.close()
        raise


@router.post("/read_image")
def read_image(
    body: AgentCapabilityRequest,
    authorization: Annotated[str | None, Header()] = None,
    x_nexus_agent_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """将授权的固定版本图片原件交给沙箱，复制后再次核对授权。"""
    stack = ExitStack()
    try:
        def authorized(db):
            claims = _claims(authorization)
            validate_claims(claims, tool_name="read_image", expected_agent_id=x_nexus_agent_id)
            if not x_nexus_agent_id or x_nexus_agent_id != claims.get("agent_id"):
                raise CapabilityGrantError("agent binding is required")
            user = require_grant_user(db, claims)
            document, version = authorize_image(db, user, claims, body.arguments)
            suffix = PurePosixPath(version.source_relative_path or version.file_path).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                raise CapabilityGrantError("source is not an image")
            return user, document, version, suffix

        with transaction() as db:
            _, document, version, suffix = authorized(db)
            document_id, version_id, file_path = document.id, version.id, version.file_path
        source, size, digest = stack.enter_context(copy_original(file_path))
        if size > MAX_IMAGE_BYTES:
            raise CapabilityGrantError("image exceeds transfer limit")
        page_count = validate_image_document(source.read(), f"source{suffix}")
        source.seek(0)
        with transaction() as db:
            user, document, version, current_suffix = authorized(db)
            if (
                document.id != document_id or version.id != version_id
                or version.file_path != file_path or current_suffix != suffix
            ):
                raise CapabilityGrantError("image source changed")
            record(
                db, actor=user.id, event_type="agent_capability_call",
                target="read_image", result="success",
            )

        def chunks():
            try:
                while chunk := source.read(CHUNK_BYTES):
                    yield chunk
            finally:
                stack.close()

        return StreamingResponse(
            chunks(), media_type="application/octet-stream",
            headers={
                "Content-Length": str(size),
                "X-Nexus-Document-ID": document_id,
                "X-Nexus-Version-ID": version_id,
                "X-Nexus-Source-Format": suffix[1:],
                "X-Nexus-Page-Count": str(page_count),
                "X-Nexus-SHA256": digest,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(stack.close),
        )
    except (CapabilityGrantError, PresentationOriginalError, ImageDocumentError):
        stack.close()
        raise HTTPException(status_code=403, detail="图片资料不可用或访问范围已变化") from None
    except BaseException:
        stack.close()
        raise


@router.post("/{tool_name}")
def invoke_capability(
    tool_name: str,
    body: AgentCapabilityRequest,
    authorization: Annotated[str | None, Header()] = None,
    x_nexus_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    claims = _claims(authorization)
    with transaction() as db:
        try:
            user = require_grant_user(db, claims)
            inline = execute_capability(
                db,
                user=user,
                claims=claims,
                tool_name=tool_name,
                arguments=body.arguments,
                expected_agent_id=(x_nexus_agent_id or "").strip() or None,
                # Carried through as a real idempotency key rather than merely
                # logged: a retried memory write must not save twice.
                request_id=body.request_id,
            )
        except MemoryRepositoryError as exc:
            # Idempotency and CAS failures are conflicts, not authorisation
            # failures; answering 403 would tell the agent to give up on a
            # request it should retry with a fresh key.
            status, message = describe_error(exc.code)
            raise HTTPException(
                status_code=status,
                detail={"error_code": exc.code, "message": message},
            ) from exc
        except ScheduleProposalError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"error_code": "schedule_proposal_rejected", "message": str(exc)},
            ) from exc
        except CapabilityGrantError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        record(
            db,
            actor=user.id,
            event_type="agent_capability_call",
            target=tool_name,
            result="success",
        )
        return _result(tool_name, inline)
