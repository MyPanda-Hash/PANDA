"""管理员视角切换：可切换账号清单。

只返回「有真实 AI 账号」的人——切换的意义是看那个人的数据，
系统里不存在的账号切过去也是空的，徒增困惑。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import admin_user
from app.core.database import transaction
from app.models.auth import User
from app.services import view_as as view_as_service

router = APIRouter(prefix="/view-as", tags=["view-as"])


@router.get("/accounts")
def list_accounts(_: User = Depends(admin_user)) -> dict:
    """管理员：能切换成哪些账号。"""
    with transaction() as db:
        rows = db.scalars(select(User).order_by(User.username).limit(200)).all()
        return {
            "data": [view_as_service.describe(row) for row in rows],
        }