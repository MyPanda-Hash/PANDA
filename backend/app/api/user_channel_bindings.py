"""Self-service binding to configured, verified Enterprise WeChat channels."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import current_user
from app.core.database import transaction
from app.core.security import iso
from app.models.auth import User
from app.repositories import user_channel_bindings as repo
from app.services.wecom import load_wecom_aibot_config, load_wecom_config

router = APIRouter(prefix="/me/channel-bindings", tags=["channel-bindings"])


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_kind: Literal["app", "aibot"]


class RevokeBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)


def _available_channels() -> dict[str, tuple[str, str]]:
    channels = {}
    app = load_wecom_config()
    bot = load_wecom_aibot_config()
    if app.configured:
        channels["app"] = (
            repo.verified_channel_key("app", app.corp_id, app.agent_id),
            "企业微信应用",
        )
    if bot.configured:
        channels["aibot"] = (
            repo.verified_channel_key("aibot", bot.aibot_id),
            "企业微信智能机器人",
        )
    return channels


def _fail(exc: repo.ChannelBindingError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error_code": exc.code, "message": str(exc)},
    )


@router.get("")
def list_channel_bindings(user: User = Depends(current_user)) -> dict:
    channels = _available_channels()
    with transaction() as db:
        items = repo.list_owned_bindings(db, owner_user_id=user.id)
        return {
            "available_channels": [
                {"channel_kind": kind, "channel_label": label}
                for kind, (_key, label) in channels.items()
            ],
            "items": [
                {
                    "id": item.id,
                    "provider": item.provider,
                    "channel_kind": item.channel_kind,
                    "channel_label": "企业微信应用"
                    if item.channel_kind == "app"
                    else "企业微信智能机器人",
                    "external_user_id_masked": (
                        item.external_user_id[:2] + "***" + item.external_user_id[-2:]
                        if len(item.external_user_id) > 4
                        else "***"
                    ),
                    "status": item.status,
                    "version": item.version,
                    "created_at": iso(item.created_at),
                }
                for item in items
            ],
        }


@router.post("/wecom/challenges")
def create_wecom_challenge(
    body: ChallengeRequest,
    user: User = Depends(current_user),
) -> dict:
    configured = _available_channels().get(body.channel_kind)
    if configured is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "binding_channel_unavailable",
                "message": "所选企业微信通道未启用",
            },
        )
    key, label = configured
    with transaction() as db:
        try:
            code, challenge = repo.create_challenge(
                db,
                owner_user_id=user.id,
                channel_kind=body.channel_kind,
                verified_channel_key=key,
            )
        except repo.ChannelBindingError as exc:
            raise _fail(exc) from exc
        return {
            "code": code,
            "expires_at": iso(challenge.expires_at),
            "channel_kind": body.channel_kind,
            "channel_label": label,
        }


@router.delete("/{binding_id}", status_code=204)
def revoke_channel_binding(
    binding_id: str,
    body: RevokeBindingRequest,
    user: User = Depends(current_user),
) -> Response:
    with transaction() as db:
        try:
            repo.revoke_binding(
                db,
                owner_user_id=user.id,
                binding_id=binding_id,
                expected_version=body.expected_version,
            )
        except repo.ChannelBindingError as exc:
            raise _fail(exc) from exc
    return Response(status_code=204)
