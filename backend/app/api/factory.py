"""账套（MES factory）：AI 侧选择与切换当前账套。

MES 是权威源，这里只做两件事：
1. 列出可切换的账套（供工作台下拉）
2. 记录当前选择（写 Cookie，后续请求据此过滤）

不新建表：账套列表实时从 MES 拉，AI 只保存「当前选的是哪个」。
拉不到时退回本地已知的两个默认账套，避免 MES 没起就完全打不开。
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, Response
from pydantic import BaseModel

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.config import settings
from app.models.auth import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/factory", tags=["factory"])

COOKIE_NAME = "doc_nexus_factory"

# MES 未启动时的兜底：与 MES factory 表的初始数据一致
FALLBACK = [
    {"code": "F01", "name": "一号工厂"},
    {"code": "F02", "name": "二号工厂"},
]


class FactoryItem(BaseModel):
    code: str
    name: str


class FactoryState(BaseModel):
    current: str = ""
    items: list[FactoryItem] = []
    source: str = ""


def _current(header_code: str, cookie_code: str) -> str:
    value = (header_code or cookie_code or "").strip().upper()
    return value or "F01"


async def _load_from_mes() -> tuple[list[FactoryItem], str]:
    base = (settings.mes_watch_base_url or "").rstrip("/")
    if not base:
        return [FactoryItem(**x) for x in FALLBACK], "fallback"
    url = f"{base}/api/base/factory/list"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = (resp.json() or {}).get("data") or []
        items = [
            FactoryItem(code=str(row.get("code") or "").upper(), name=str(row.get("name") or ""))
            for row in data
            if isinstance(row, dict) and row.get("code")
        ]
        if items:
            return items, "mes"
    except Exception as exc:  # noqa: BLE001 - MES 不可用是常态，不该让接口挂掉
        logger.info("factory.list_unavailable: %s", exc)
    return [FactoryItem(**x) for x in FALLBACK], "fallback"


@router.get("", response_model=FactoryState)
async def get_factory(
    _: User = Depends(current_user_from_bearer_or_cookie),
    x_factory_code: str = Header(default="", alias="X-Factory-Code"),
    factory_cookie: str = Cookie(default="", alias=COOKIE_NAME),
) -> FactoryState:
    items, source = await _load_from_mes()
    return FactoryState(
        current=_current(x_factory_code, factory_cookie),
        items=items,
        source=source,
    )


@router.post("/switch", response_model=FactoryState)
async def switch_factory(
    payload: FactoryItem,
    response: Response,
    _: User = Depends(current_user_from_bearer_or_cookie),
) -> FactoryState:
    """切换当前账套：写 Cookie，之后所有请求按它过滤。"""
    code = (payload.code or "").strip().upper()
    response.set_cookie(
        COOKIE_NAME,
        code,
        max_age=7 * 24 * 3600,
        httponly=False,
        samesite="lax",
        path="/",
    )
    items, source = await _load_from_mes()
    return FactoryState(current=code, items=items, source=source)