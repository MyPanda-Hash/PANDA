from datetime import datetime

from pydantic import BaseModel, Field


class SpaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class SpaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class SpaceResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    owner_user_id: str
    owner_display_name: str | None = None
    owner_username: str | None = None
    scope_version: int
    member_role: str | None = None
    document_count: int = 0
    queryable_document_count: int = 0
    updated_at: datetime


class MemberCreateRequest(BaseModel):
    """Add a space member by user id, username, or email."""

    user_id: str | None = Field(default=None, min_length=1, max_length=160)
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    role: str = Field(
        default="contributor", pattern="^(manager|contributor|reader)$"
    )


class MemberBulkAddRequest(BaseModel):
    role: str = Field(
        default="contributor", pattern="^(manager|contributor|reader)$"
    )


class MemberUpdateRequest(BaseModel):
    role: str = Field(pattern="^(manager|contributor|reader)$")


class MemberResponse(BaseModel):
    user_id: str
    role: str
    username: str | None = None
    display_name: str | None = None
    email: str | None = None
    created_at: datetime
    updated_at: datetime


class MemberBulkAddResponse(BaseModel):
    items: list[MemberResponse]
    added_count: int
    existing_count: int


class MemberBulkRemoveResponse(BaseModel):
    items: list[MemberResponse]
    removed_count: int


class DirectoryUserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None = None
    already_member: bool = False


class DirectoryUserListResponse(BaseModel):
    items: list[DirectoryUserResponse]
    total_active_users: int


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_id: str | None = None
    sort_order: int = Field(default=0, ge=0)


class CategoryResponse(BaseModel):
    id: str
    space_id: str
    parent_id: str | None
    name: str
    sort_order: int
    path: str


class WorkspaceResponse(BaseModel):
    user: dict
    recent_spaces: list[SpaceResponse]
    running_tasks: list[dict]
    usage: dict
