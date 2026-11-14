from __future__ import annotations

from pydantic import BaseModel, Field


class LinkMindKeyCreateRequest(BaseModel):
    remark: str | None = Field(default=None, min_length=1, max_length=255)


class LinkMindKeyUpdateRequest(BaseModel):
    remark: str = Field(min_length=1, max_length=255)

class LinkMindKeyResponse(BaseModel):
    id: int
    user_id: str
    key_hint: str
    remark: str
    status: str
    created_at: int
    last_used_at: int | None = None
    revoked_at: int | None = None
    can_reveal: bool = False




class LinkMindCreatedKeyResponse(LinkMindKeyResponse):
    token: str


class LinkMindKeyListResponse(BaseModel):
    items: list[LinkMindKeyResponse]


class LinkMindTokenRevealResponse(BaseModel):
    token: str
