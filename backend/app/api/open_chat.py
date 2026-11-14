from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import SessionLocal, transaction
from app.core.security import as_utc, hash_token
from app.models.auth import User
from app.repositories import users
from app.repositories import user_memory as memory_repo
from app.services import user_memory as memory_service
from app.services.agent_capability import (
    MEMORY_CAPABILITY_TOOLS,
    chat_capability_tools,
    create_capability_token,
)
from app.services.chat_knowledge import build_knowledge_context
from app.services.model_routing import endpoint_model_names, resolve_model_endpoint
from app.services.pi_local import LocalPiAgentClient
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID

router = APIRouter(prefix="/api/open", tags=["open-chat"])
logger = logging.getLogger(__name__)


class OpenChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^\S+$"
    )


def integration_user(
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API Key 缺失")
    with SessionLocal() as db:
        user = users.get_by_api_key(db, authorization[7:].strip())
        if user is None:
            raise HTTPException(status_code=401, detail="API Key 无效")
        if settings.linkmind_enabled and user.first_login:
            raise HTTPException(status_code=403, detail="首次登录必须先修改密码")
        db.expunge(user)
        return user


@router.post("/chat", response_model=None)
async def open_chat(
    body: OpenChatRequest,
    user: User = Depends(integration_user),
) -> dict[str, Any] | JSONResponse:
    session_id = (body.session_id or "").strip() or "default"
    runtime_session_id = f"pi-open-{user.id}-{hash_token(session_id)[:32]}"
    requested_model = (body.model or "").strip() or None
    with SessionLocal() as db:
        available_tools = chat_capability_tools(db, user)
        endpoint = resolve_model_endpoint(db, "chat", requested_model=requested_model)
        if endpoint is None:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "指定模型不可用",
                    "memory_turn_id": None,
                    "memory_status": "none",
                    "memory_reason": "request_not_started",
                    "memory_auto_save": False,
                    "memory_receipts": [],
                },
            )
        available_models = endpoint_model_names(endpoint)
        effective_requested_model = requested_model or endpoint.model
        if effective_requested_model not in available_models:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "指定模型不可用",
                    "memory_turn_id": None,
                    "memory_status": "none",
                    "memory_reason": "request_not_started",
                    "memory_auto_save": False,
                    "memory_receipts": [],
                },
            )
        upstream_message, retrieval = build_knowledge_context(
            db, user=user, message=body.message, space_ids=[], document_ids=None
        )

    memory: dict[str, Any] = {
        "memory_turn_id": None,
        "memory_status": "disabled",
        "memory_auto_save": False,
        "memory_receipts": [],
    }
    grant: dict[str, Any] = {"memory_scope": "none"}
    if settings.personal_memory_enabled:
        try:
            with transaction() as db:
                state = memory_repo.lock_owned_state(db, user.id)
                if state.use_enabled:
                    grant = {
                        "memory_scope": "read",
                        "memory_epoch": state.epoch,
                        "auth_version": user.auth_version,
                    }
                    memory["memory_status"] = "read_only"
                    memory["memory_reason"] = "request_id_required"
                    if body.request_id:
                        safe_text, too_large = memory_service.sanitize_source_snapshot(
                            body.message
                        )
                        source_usable = (
                            not too_large
                            and safe_text == body.message
                            and not memory_service.contains_secret(body.message)
                        )
                        turn = memory_repo.register_turn(
                            db,
                            owner_user_id=user.id,
                            channel="open",
                            request_key=f"open:{hash_token(session_id)}:{body.request_id}",
                            raw_text=body.message,
                            runtime_session_id=runtime_session_id,
                            source_text_safe=safe_text if source_usable else None,
                            source_text_retention=memory_service.source_retention()
                            if source_usable
                            else None,
                        )
                        memory["memory_turn_id"] = turn.id
                        current_source = (
                            source_usable
                            and turn.status == "live"
                            and turn.epoch == state.epoch
                            and turn.runtime_session_id == runtime_session_id
                            and memory_service.resolve_turn_source_text(db, turn)
                            is not None
                        )
                        if current_source:
                            grant.update(
                                memory_scope="propose",
                                memory_turn_id=turn.id,
                                memory_source_hash=turn.source_hash,
                            )
                            memory.pop("memory_reason", None)
                            memory["memory_status"] = "none"
                            if state.learning_mode != "manual" and (
                                state.automatic_source_cutoff_at is None
                                or as_utc(turn.created_at)
                                >= as_utc(state.automatic_source_cutoff_at)
                            ):
                                memory_repo.enqueue_extraction_job(
                                    db,
                                    owner_user_id=user.id,
                                    turn_id=turn.id,
                                    source_hash_value=turn.source_hash,
                                    extractor_version=settings.personal_memory_extractor_version,
                                    epoch=state.epoch,
                                )
                                memory["memory_auto_save"] = (
                                    state.learning_mode == "auto_low_risk"
                                )
                        else:
                            memory["memory_status"] = (
                                "unavailable" if not source_usable else "read_only"
                            )
                            memory["memory_reason"] = (
                                "source_too_large"
                                if too_large
                                else "source_rejected"
                                if not source_usable
                                else "source_not_live"
                            )
        except memory_repo.MemoryConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "error_code": exc.code,
                        "message": "相同 request_id 已用于其他内容，请使用新 request_id",
                    },
                    **memory,
                    "memory_status": "unavailable",
                    "memory_reason": exc.code,
                },
            )
        except Exception:
            logger.warning("open memory source registration failed", exc_info=False)
            grant = {"memory_scope": "none"}
            memory.update(
                memory_turn_id=None,
                memory_status="unavailable",
                memory_reason="memory_unavailable",
            )
    memory_tools = (
        list(MEMORY_CAPABILITY_TOOLS)
        if grant["memory_scope"] == "propose"
        else ["get_my_memory"]
        if grant["memory_scope"] == "read"
        else []
    )
    capability_token = create_capability_token(
        user_id=user.id,
        agent_id=PI_RUNTIME_AGENT_ID,
        space_ids=[],
        space_mode="accessible",
        conversation_id=runtime_session_id,
        chat_history=False,
        personal_data_scope="own",
        allowed_tools=list(available_tools) + memory_tools,
        **grant,
    )
    started = time.monotonic()
    client = LocalPiAgentClient(
        agent_id=PI_RUNTIME_AGENT_ID, user_id=user.id, model=effective_requested_model
    )
    result = None
    failed = False
    try:
        result, _ = await client.chat_async(
            upstream_message,
            session_id=runtime_session_id,
            capability_token=capability_token,
        )
    except Exception:
        failed = True
    finally:
        if memory["memory_turn_id"]:
            try:
                with transaction() as db:
                    memory_repo.close_turn(
                        db, owner_user_id=user.id, turn_id=memory["memory_turn_id"]
                    )
                with transaction() as db:
                    status = memory_service.read_turn_status(
                        db,
                        owner_user_id=user.id,
                        turn_id=memory["memory_turn_id"],
                    )
                    if (
                        memory["memory_status"] not in {"unavailable", "read_only"}
                        or status.receipts
                    ):
                        memory["memory_status"] = status.memory_status
                    memory["memory_receipts"] = [
                        receipt.model_dump(mode="json") for receipt in status.receipts
                    ]
                    memory["memory_revision"] = status.revision
            except Exception:
                memory.update(
                    memory_status="unavailable", memory_reason="memory_unavailable"
                )
                logger.warning(
                    "open memory completion could not be read", exc_info=False
                )
    if failed or result is None:
        return JSONResponse(
            status_code=502, content={"detail": "AI 服务暂时不可用", **memory}
        )
    effective_model = getattr(client, "effective_model", "") or endpoint.model
    usage = {
        "provider": "pi",
        "model": effective_model,
        "input_tokens": result.token_usage.get("prompt_tokens", 0),
        "output_tokens": result.token_usage.get("completion_tokens", 0),
        "total_tokens": result.token_usage.get("total_tokens", 0),
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
    }
    return {
        "session_id": session_id,
        "answer": result.reply,
        "references": (retrieval or {}).get("references", []),
        "retrieval": retrieval,
        "effective_model": effective_model,
        "usage": usage,
        **memory,
    }
