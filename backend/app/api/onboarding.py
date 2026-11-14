from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import admin_user
from app.core.assistant_identity import DEFAULT_COMPANY_NAME
from app.core.config import settings
from app.core.database import transaction
from app.models.auth import User
from app.repositories import system_config
from app.schemas.onboarding import (
    OnboardingCompleteRequest,
    OnboardingMessageRequest,
    OnboardingMessageResponse,
    OnboardingStatusResponse,
)
from app.services import linkmind, onboarding
from app.services.model_routing import resolve_model_endpoint


router = APIRouter(prefix="/admin/onboarding", tags=["admin-onboarding"])
_COMPLETED_KEY = "onboarding_completed"


def _require_linkmind() -> None:
    if not settings.linkmind_enabled:
        raise HTTPException(
            status_code=409, detail="智能初始化仅在 LinkMind 模式下可用"
        )


@router.get("/status", response_model=OnboardingStatusResponse)
def get_status(_: User = Depends(admin_user)) -> OnboardingStatusResponse:
    with transaction() as db:
        stored = system_config.get_all(db)
    explicitly_completed = stored.get(_COMPLETED_KEY) == "1"
    previously_customized = bool(
        (stored.get("app_name") or "").strip()
        and (stored.get("pi_persona_name") or "").strip()
    )
    completed = (
        not settings.linkmind_enabled or explicitly_completed or previously_customized
    )
    return OnboardingStatusResponse(
        required=settings.linkmind_enabled and not completed,
        completed=completed,
        company_name=(stored.get("app_name") or "").strip(),
        persona_name=(stored.get("pi_persona_name") or "").strip(),
    )


@router.post("/message", response_model=OnboardingMessageResponse)
def message(
    payload: OnboardingMessageRequest,
    admin: User = Depends(admin_user),
) -> OnboardingMessageResponse:
    _require_linkmind()
    with transaction() as db:
        target = resolve_model_endpoint(db, "chat")
    if target is None:
        raise HTTPException(status_code=503, detail="尚未配置可用的 Chat 模型")
    try:
        return onboarding.converse(
            target=target,
            user_id=admin.id,
            stage=payload.stage,
            message=payload.message,
            company_name=payload.company_name.strip(),
            history=payload.history,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/complete", response_model=OnboardingStatusResponse)
def complete(
    payload: OnboardingCompleteRequest,
    admin: User = Depends(admin_user),
) -> OnboardingStatusResponse:
    _require_linkmind()
    try:
        company_name = onboarding.validate_identity_value(
            "company", payload.company_name
        )
        persona_name = onboarding.validate_identity_value(
            "persona", payload.persona_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with transaction() as db:
        stored = system_config.get_all(db)
        explicitly_completed = stored.get(_COMPLETED_KEY) == "1"
        previously_customized = bool(
            (stored.get("app_name") or "").strip()
            and (stored.get("pi_persona_name") or "").strip()
        )
        should_initialize_box = (
            db.get(User, admin.id) is not None
            and not explicitly_completed
            and not previously_customized
            and stored.get(linkmind.LINKMIND_BOX_INITIALIZED_CONFIG_KEY)
            != linkmind.LINKMIND_BOX_INITIALIZATION_COMPLETE
        )

    if should_initialize_box:
        try:
            linkmind.sync_user_by_id(admin.id)
            linkmind.ensure_user_key(admin.id)
            linkmind.initialize_box_once(admin.id)
        except linkmind.LinkMindError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    with transaction() as db:
        system_config.upsert(db, "app_name", company_name, "初始化设置的公司名称")
        system_config.upsert(
            db, "pi_persona_name", persona_name, "初始化设置的数字员工名称"
        )
        system_config.upsert(
            db,
            "pi_persona_title",
            f"{company_name}数字员工",
            "初始化设置的数字员工职称",
        )
        system_config.upsert(db, _COMPLETED_KEY, "1", "智能初始化完成标记")
    return OnboardingStatusResponse(
        required=False,
        completed=True,
        company_name=company_name or DEFAULT_COMPANY_NAME,
        persona_name=persona_name,
    )
