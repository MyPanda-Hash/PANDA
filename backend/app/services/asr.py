from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.config import settings


class ASRNotConfiguredError(RuntimeError):
    """The server has no DashScope credential configured."""


class ASRProviderError(RuntimeError):
    """DashScope returned an unusable or unsuccessful response."""


_MIME_TO_FORMAT = {
    "audio/aac": "aac",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}


def audio_format_from_mime_type(media_type: str) -> str:
    normalized = (media_type or "").split(";", 1)[0].strip().lower()
    return _MIME_TO_FORMAT.get(normalized, "webm")


def build_dashscope_payload(audio_bytes: bytes, media_type: str) -> dict[str, Any]:
    mime = (media_type or "audio/webm").split(";", 1)[0].strip().lower()
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    return {
        "model": settings.dashscope_asr_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{mime};base64,{encoded}"
                            },
                        }
                    ],
                }
            ]
        },
        # The recorded container is passed through as-is. DashScope requires
        # this value to match the actual audio format.
        "parameters": {"format": audio_format_from_mime_type(mime)},
    }


def extract_transcription_text(body: dict[str, Any]) -> str:
    output = body.get("output")
    if not isinstance(output, dict):
        raise ASRProviderError("ASR 返回结果格式异常")

    nested = output.get("output")
    sentence = nested.get("sentence") if isinstance(nested, dict) else None
    candidates = [
        sentence.get("text") if isinstance(sentence, dict) else None,
        output.get("text"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ASRProviderError("ASR 未返回识别文本")


async def transcribe_audio(audio_bytes: bytes, media_type: str) -> str:
    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise ASRNotConfiguredError("DASHSCOPE_API_KEY 未配置")

    try:
        async with httpx.AsyncClient(timeout=settings.asr_timeout_seconds) as client:
            response = await client.post(
                settings.dashscope_asr_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-DashScope-SSE": "disable",
                },
                json=build_dashscope_payload(audio_bytes, media_type),
            )
    except httpx.HTTPError as exc:
        raise ASRProviderError("ASR 服务连接失败") from exc

    if not response.is_success:
        raise ASRProviderError(f"ASR 服务返回 HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ASRProviderError("ASR 服务返回内容无法解析") from exc
    if not isinstance(body, dict):
        raise ASRProviderError("ASR 服务返回结果格式异常")
    return extract_transcription_text(body)
