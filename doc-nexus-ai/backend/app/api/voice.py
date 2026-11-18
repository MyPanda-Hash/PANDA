"""AI 数字员工「悬浮语音窗」的后端入口。

浏览器只连这里，不连上游：密钥、人设、工具全在服务端（见 moss_voice）。

WebSocket 鉴权说明：浏览器原生 WebSocket 无法设置 Authorization 头，
所以优先读同源 Cookie；跨源（例如把悬浮窗嵌进 MES 页面）时允许用
一次性 ticket 走 ``?token=``。ticket 复用现有 JWT，不额外发密钥。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi import HTTPException

from app.api.deps import (
    ACCESS_COOKIE,
    _user_from_token,
    current_user_from_bearer_or_cookie,
)
from app.core.config import settings
from app.models.auth import User
from app.services import moss_voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant/voice", tags=["assistant-voice"])


@router.get("/status")
def voice_status(
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    available, reason = moss_voice.voice_available()
    return {
        "data": {
            "available": available,
            "reason": reason,
            "model": settings.moss_realtime_model,
            "voiceId": settings.moss_voice_id,
            "maxSeconds": settings.moss_voice_max_seconds,
            "writeEnabled": bool(settings.moss_voice_write_enabled),
            "writeLimit": settings.moss_voice_write_limit,
            "activeSessions": moss_voice.active_count(),
        }
    }


def _user_from_websocket(websocket: WebSocket, token: str) -> User | None:
    candidate = (token or "").strip()
    if not candidate:
        candidate = (websocket.cookies.get(ACCESS_COOKIE) or "").strip()
    if not candidate:
        return None
    try:
        return _user_from_token(candidate, allow_first_login=True)
    except HTTPException:
        return None


@router.websocket("/ws")
async def voice_ws(
    websocket: WebSocket,
    ticket: Annotated[str, Query(alias="token", max_length=4096)] = "",
) -> None:
    user = _user_from_websocket(websocket, ticket)
    if user is None:
        # 4401 是自定义码，前端据此提示「登录已过期」
        await websocket.close(code=4401)
        return

    available, reason = moss_voice.voice_available()
    if not available:
        await websocket.accept()
        await websocket.send_json({"action": "pod.error", "data": {"message": reason}})
        await websocket.close(code=1013)
        return

    if not moss_voice.claim_slot(user.id, websocket):
        await websocket.accept()
        await websocket.send_json(
            {"action": "pod.error", "data": {"message": "你已经有一个语音窗口在通话中"}}
        )
        await websocket.close(code=1013)
        return

    await websocket.accept()
    relay = moss_voice.VoiceRelay(websocket, user)
    try:
        await relay.run()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - 任何异常都要保证槽位被释放
        logger.exception("moss_voice.relay_crashed user=%s", user.id)
    finally:
        # 先放槽位再收尾：关闭握手可能慢，不能让人卡在「已有通话中」
        moss_voice.release_slot(user.id)
        try:
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError):
            pass