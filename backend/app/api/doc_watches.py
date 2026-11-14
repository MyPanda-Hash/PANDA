"""盯单的读取与取消（语音里也能建，这里给界面和排查用）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.services import doc_watch

router = APIRouter(prefix="/assistant/watches", tags=["assistant-watches"])


def _payload(row) -> dict:
    return {
        "id": row.id,
        "docNo": row.doc_no,
        "panelCode": row.panel_code,
        "condition": row.condition,
        "step": row.target_keyword,
        "note": row.note,
        "status": row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "expiresAt": row.expires_at.isoformat() if row.expires_at else "",
    }


@router.get("")
def list_watches(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    with transaction() as db:
        rows = doc_watch.watches_of(db, user.id)
        items = [_payload(row) for row in rows]
    return {"data": {"items": items, "active": sum(1 for i in items if i["status"] == "active")}}


@router.delete("/{doc_no}")
def cancel_watch(doc_no: str, user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    with transaction() as db:
        removed = doc_watch.cancel(db, user_id=user.id, doc_no=doc_no)
    if not removed:
        raise HTTPException(status_code=404, detail=f"没有在盯 {doc_no}")
    return {"data": {"cancelled": removed}}
