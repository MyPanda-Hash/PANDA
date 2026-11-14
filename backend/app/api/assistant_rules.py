"""检测阈值的查看与调整（仅管理员）。

阈值直接决定「会不会报警」，所以每一次改动都留痕：谁、什么时候、
从多少改到多少、为什么。接口只做三件事：看当前值、改值、翻历史。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.services import rule_thresholds

router = APIRouter(prefix="/api/assistant/rules", tags=["assistant-rules"])


class ThresholdUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: float
    reason: str = Field(default="", max_length=500)


def _require_admin(user: User) -> None:
    if (user.role or "") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以调整阈值")


@router.get("", response_model=dict)
def list_thresholds(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict[str, Any]:
    """当前生效的阈值，带来源（default / env / override）。"""
    return {"items": rule_thresholds.describe()}


@router.get("/history", response_model=dict)
def threshold_history(
    key: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict[str, Any]:
    """改动历史，最近的在前。谁都能看——透明比保密更能防住乱调。"""
    with transaction() as db:
        return {"items": rule_thresholds.history(db, key=key, limit=limit)}


@router.post("", response_model=dict)
def update_threshold(
    body: ThresholdUpdate,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict[str, Any]:
    """改一个阈值并留痕。"""
    _require_admin(user)
    with transaction() as db:
        try:
            return rule_thresholds.set_value(
                db, key=body.key, value=body.value, actor=user.username, reason=body.reason
            )
        except rule_thresholds.ThresholdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{key}", response_model=dict)
def revert_threshold(
    key: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict[str, Any]:
    """恢复成环境变量/默认值（同样留痕）。"""
    _require_admin(user)
    with transaction() as db:
        try:
            return rule_thresholds.revert(db, key=key, actor=user.username, reason="恢复默认")
        except rule_thresholds.ThresholdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc