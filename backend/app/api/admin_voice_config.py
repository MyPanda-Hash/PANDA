"""语音助手的配置接口（仅管理员）。

语音走 MOSS-Live 实时通话 + DashScope ASR，与 chat 模型端点不是一套体系，
所以单独一个分组，不混进 /admin/model-endpoints。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import admin_user
from app.core.config import settings
from app.core.database import transaction
from app.models.auth import User
from app.services import voice_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/voice-config", tags=["admin-voice-config"])


class VoiceConfigUpdate(BaseModel):
    moss_api_key: str | None = Field(default=None, max_length=200)
    moss_realtime_model: str | None = Field(default=None, max_length=64)
    moss_realtime_url: str | None = Field(default=None, max_length=200)
    moss_voice_id: str | None = Field(default=None, max_length=64)
    moss_voice_persona: str | None = Field(default=None, max_length=32)
    moss_voice_max_seconds: int | None = Field(default=None, ge=30, le=3600)
    moss_voice_enabled: bool | None = None
    moss_voice_write_enabled: bool | None = None
    moss_voice_write_limit: int | None = Field(default=None, ge=0, le=1000)
    moss_voice_briefing_enabled: bool | None = None
    dashscope_asr_model: str | None = Field(default=None, max_length=64)
    dashscope_api_key: str | None = Field(default=None, max_length=200)


@router.get("")
def get_voice_config(_: User = Depends(admin_user)) -> dict[str, Any]:
    """当前生效的语音配置；密钥只回是否已配置。"""
    return voice_config.current_view()


@router.patch("")
def update_voice_config(
    payload: VoiceConfigUpdate,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    """改配置并立即生效，不需要重启后端。"""
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    with transaction() as db:
        view = voice_config.save(db, values)
    logger.info("voice config updated: %s", sorted(values.keys()))
    return view


@router.post("/test")
async def test_voice_config(_: User = Depends(admin_user)) -> dict[str, Any]:
    """连通性自检：只校验配置完整性，不发起真实通话（避免占用计费时长）。"""
    problems: list[str] = []
    if not (settings.moss_api_key or "").strip():
        problems.append("未配置 MOSS API Key")
    if not (settings.moss_realtime_url or "").startswith("wss://"):
        problems.append("实时通话地址必须是 wss:// 开头")
    if not (settings.moss_realtime_model or "").strip():
        problems.append("未配置实时通话模型")
    if not (settings.dashscope_api_key or "").strip():
        problems.append("未配置 DashScope Key（语音转文字需要）")

    reachable = False
    detail = ""
    base = (settings.moss_realtime_url or "").strip()
    if base.startswith("wss://"):
        # 把 wss 换成 https 探一下主机可达性；握手时长会真的开一次会话，这里只探 DNS/端口
        probe = "https://" + base[len("wss://"):].split("/")[0]
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(probe)
            reachable = True
        except Exception as exc:  # noqa: BLE001 - 探测失败只作为提示，不代表配置错
            detail = str(exc)[:160]

    return {
        "ok": not problems,
        "problems": problems,
        "host_reachable": reachable,
        "host_detail": detail,
        "model": settings.moss_realtime_model,
        "asr_model": settings.dashscope_asr_model,
    }