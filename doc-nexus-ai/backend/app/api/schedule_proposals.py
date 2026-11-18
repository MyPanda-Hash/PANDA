"""Owner-scoped chat proposal and Skill-application projections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import current_user
from app.core.database import transaction
from app.models.auth import User
from app.services.schedule_proposals import (
    ScheduleProposalError,
    refresh_skill_application_drafts,
    require_bound_schedule_turn,
)
from app.services.schedule_skills import (
    ScheduleSkillError,
    ScheduleSkillFile,
    get_schedule_skill_application,
    list_schedule_skill_applications,
    schedule_skill_application_view,
    submit_schedule_skill_application,
)

router = APIRouter(prefix="/schedule-proposals", tags=["schedule-proposals"])


class ScheduleSkillFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=262_144)


class ScheduleSkillApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_session_id: str = Field(min_length=1, max_length=64)
    source_message_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    skill_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1, max_length=262_144)
    files: list[ScheduleSkillFileInput] = Field(default_factory=list, max_length=32)


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=int(getattr(exc, "status_code", 422)),
        detail=str(exc),
    )


@router.post("/skill-requests", status_code=201)
def create_skill_request(
    payload: ScheduleSkillApplicationInput,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Submit an owned candidate; this endpoint cannot approve or enable it."""
    with transaction() as db:
        try:
            require_bound_schedule_turn(
                db,
                owner_user_id=user.id,
                claims={
                    "conversation_id": payload.source_session_id,
                    "history_turn_message_id": payload.source_message_id,
                },
            )
            application = submit_schedule_skill_application(
                owner_user_id=user.id,
                source_session_id=payload.source_session_id,
                source_message_id=payload.source_message_id,
                request_key=payload.request_id,
                skill_id=payload.skill_id,
                version=payload.version,
                description=payload.description,
                instructions=payload.instructions,
                files=[
                    ScheduleSkillFile(path=item.path, content=item.content)
                    for item in payload.files
                ],
            )
            return {"application": schedule_skill_application_view(application)}
        except (ScheduleProposalError, ScheduleSkillError) as exc:
            raise _http_error(exc) from exc


@router.get("/skill-requests")
def list_skill_requests(
    session_id: str | None = Query(default=None, min_length=1, max_length=64),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        items = list_schedule_skill_applications(
            owner_user_id=user.id, source_session_id=session_id
        )
        return {"items": [schedule_skill_application_view(item) for item in items]}
    except ScheduleSkillError as exc:
        raise _http_error(exc) from exc


@router.get("/skill-requests/{application_id}")
def get_skill_request(
    application_id: str,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    application = get_schedule_skill_application(application_id, owner_user_id=user.id)
    if application is None:
        raise HTTPException(status_code=404, detail="Skill 申请不存在")
    try:
        return {"application": schedule_skill_application_view(application)}
    except ScheduleSkillError as exc:
        raise _http_error(exc) from exc


@router.post("/skill-requests/{application_id}/refresh")
def refresh_skill_request(
    application_id: str,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    with transaction() as db:
        try:
            return refresh_skill_application_drafts(
                db, owner_user_id=user.id, application_id=application_id
            )
        except ScheduleProposalError as exc:
            if str(exc) == "Skill 申请不存在":
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise _http_error(exc) from exc
        except (ScheduleSkillError, KeyError, ValueError) as exc:
            raise _http_error(exc) from exc
