"""Authenticated, read-only global search and recent-target resolution."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user
from app.core.database import transaction
from app.models.auth import User
from app.repositories import search as search_repo
from app.schemas.search import (
    SearchGroup,
    SearchItem,
    SearchQueryType,
    SearchResolveRequest,
    SearchResolveResponse,
    SearchResponse,
)

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def global_search(
    q: str = Query("", max_length=255),
    type: SearchQueryType = Query("all"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
) -> SearchResponse:
    with transaction() as db:
        groups = search_repo.search(db, user, query=q, type=type, limit=limit, offset=offset)
        return SearchResponse(
            query=q.strip(),
            groups=[SearchGroup.model_validate(group, from_attributes=True) for group in groups],
        )


@router.post("/search/resolve", response_model=SearchResolveResponse)
def resolve_search_items(
    request: SearchResolveRequest,
    user: User = Depends(current_user),
) -> SearchResolveResponse:
    with transaction() as db:
        items = search_repo.resolve(db, user, [(item.type, item.id) for item in request.items], query=request.q)
        return SearchResolveResponse(items=[SearchItem.model_validate(item, from_attributes=True) for item in items])
