from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import current_user
from app.core.config import settings
from app.models.auth import User
from app.services.asr import (
    ASRNotConfiguredError,
    ASRProviderError,
    transcribe_audio,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/asr", tags=["asr"])

_ALLOWED_AUDIO_TYPES = {
    "audio/aac",
    "audio/mp3",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
}


@router.post("/transcribe")
async def transcribe(
    file: Annotated[UploadFile, File(description="待识别的语音文件")],
    _: User = Depends(current_user),
) -> dict[str, str]:
    media_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="不支持的音频格式")

    audio_bytes = await file.read(settings.asr_max_upload_bytes + 1)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频内容为空")
    if len(audio_bytes) > settings.asr_max_upload_bytes:
        raise HTTPException(status_code=413, detail="音频文件不能超过 10 MB")

    try:
        text = await transcribe_audio(audio_bytes, media_type)
    except ASRNotConfiguredError:
        raise HTTPException(status_code=503, detail="语音识别服务尚未配置") from None
    except ASRProviderError:
        logger.exception("DashScope ASR request failed")
        raise HTTPException(status_code=502, detail="语音识别失败，请稍后重试") from None
    return {"text": text}
