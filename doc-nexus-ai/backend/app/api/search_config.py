"""联网检索的管理接口：读当前配置、保存、连通自检。

只给管理员：换搜索服务商、改密钥都属于运维动作。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import admin_user
from app.core.database import transaction
from app.models.auth import User
from app.services import search_config, web_search

router = APIRouter(prefix="/admin/search", tags=["search-config"])


@router.get("")
def read_config(_: User = Depends(admin_user)) -> dict:
    """管理员：看当前检索配置（密钥只报是否已配置）。"""
    with transaction() as db:
        search_config.apply_overrides(db)
    return {"data": search_config.current_view()}


@router.patch("")
def save_config(body: dict[str, Any], _: User = Depends(admin_user)) -> dict:
    """管理员：保存检索配置，立刻生效（不用重启）。"""
    with transaction() as db:
        view = search_config.save(db, body or {})
    return {"data": view}


@router.post(":test")
def test_search(_: User = Depends(admin_user)) -> dict:
    """管理员：用当前配置跑一次真实检索，确认能用。"""
    try:
        text = web_search.answer("今天的日期")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="检索不可用：" + str(exc)[:180]) from exc
    return {"data": {"ok": True, "sample": text[:400]}}