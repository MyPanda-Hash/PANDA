from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SearchType = Literal["chat", "space", "document", "writing", "artifact", "task"]
SearchQueryType = Literal[
    "all",
    "resources",
    "chat",
    "space",
    "document",
    "writing",
    "artifact",
    "task",
]
MatchedField = Literal["title", "content", "description"]


class SearchItem(BaseModel):
    """A permission-filtered, server-addressable global-search result."""

    type: SearchType
    id: str
    title: str
    subtitle: str = ""
    updated_at: datetime
    status: str | None = None
    snippet: str | None = None
    matched_field: MatchedField | None = None
    path: str
    match_count: int = Field(default=0, ge=0)
    content_searchable: bool = False


class SearchGroup(BaseModel):
    type: SearchType
    items: list[SearchItem]
    total: int = Field(default=0, ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SearchResponse(BaseModel):
    query: str
    groups: list[SearchGroup]


class SearchResolveItem(BaseModel):
    type: SearchType
    id: str = Field(min_length=1, max_length=16384)


class SearchResolveRequest(BaseModel):
    items: list[SearchResolveItem] = Field(min_length=1, max_length=10)
    q: str = Field(default="", max_length=255)


class SearchResolveResponse(BaseModel):
    items: list[SearchItem]
