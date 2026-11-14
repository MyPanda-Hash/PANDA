"""系统初始化：给 MES 用的内部接口（组织架构图 → 部门树草案）。

调用方是 MES 后端，不是浏览器，所以用共享令牌 `X-Nexus-Internal-Token` 把关：
MES 侧配 `MES_INIT_AI_TOKEN`，本服务配 `DOC_NEXUS_INTERNAL_TOKEN`，两边一致才放行。
未配置令牌时接口直接关闭（宁可不可用，也不要裸奔）。
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import init_org_chart
from app.services.init_org_chart import OrgChartError

router = APIRouter(prefix="/internal/v1/init", tags=["internal-init"])
logger = logging.getLogger(__name__)

TOKEN_ENV = "DOC_NEXUS_INTERNAL_TOKEN"


def _expected_token() -> str:
    """优先读配置（.env 里那份），再退回进程环境变量。"""
    value = (getattr(settings, "internal_token", "") or "").strip()
    return value or (os.environ.get(TOKEN_ENV) or "").strip()


def _guard(token: str | None) -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(status_code=503, detail="初始化接口未启用：未配置内部令牌")
    if (token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="内部令牌无效")


@router.post("/org-chart")
async def parse_org_chart(
    file: Annotated[UploadFile, File()],
    hint: Annotated[str, Form()] = "",
    x_nexus_internal_token: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """上传一张组织架构图，返回部门树草案（不落库）。"""
    _guard(x_nexus_internal_token)
    content = await file.read()
    media_type = file.content_type or "image/png"
    try:
        with SessionLocal() as db:
            result = await init_org_chart.parse_org_chart(
                db, content, media_type, extra_hint=hint
            )
    except OrgChartError as exc:
        logger.info("init.org_chart.rejected", extra={"code": exc.code})
        return JSONResponse(status_code=400, content={"code": exc.code, "message": exc.message})
    logger.info(
        "init.org_chart.parsed",
        extra={"depts": len(result.get("depts") or []), "model": result.get("model")},
    )
    return JSONResponse(status_code=200, content=result)
