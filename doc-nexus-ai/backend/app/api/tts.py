from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import admin_user
from app.models.auth import User
from app.schemas.tts import TTSSynthesizeRequest, TTSSynthesizeResponse
from app.services.tts import (
    TTSNotConfiguredError,
    TTSProviderError,
    synthesize_speech,
)


router = APIRouter(prefix="/tts", tags=["tts"])


@router.post("/synthesize", response_model=TTSSynthesizeResponse)
async def synthesize(
    payload: TTSSynthesizeRequest,
    _: User = Depends(admin_user),
) -> TTSSynthesizeResponse:
    try:
        speech = await synthesize_speech(payload.text)
    except TTSNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="语音服务未配置") from exc
    except TTSProviderError as exc:
        raise HTTPException(status_code=502, detail="语音服务暂时不可用") from exc
    return TTSSynthesizeResponse(
        audio=speech.audio,
        format=speech.format,
        sample_rate=speech.sample_rate,
    )
