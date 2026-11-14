from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings


class TTSNotConfiguredError(RuntimeError):
    pass


class TTSProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio: str
    format: str
    sample_rate: int


async def synthesize_speech(text: str) -> SynthesizedSpeech:
    endpoint = settings.tts_service_url.strip()
    if not endpoint:
        raise TTSNotConfiguredError("TTS service URL is empty")

    payload = {
        "text": text,
        "vcn": settings.tts_voice,
        "speed": settings.tts_speed,
        "pitch": settings.tts_pitch,
        "volume": settings.tts_volume,
    }
    timeout = httpx.Timeout(settings.tts_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, json=payload) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > settings.tts_max_response_bytes:
                        raise TTSProviderError("TTS response exceeds configured limit")
    except TTSProviderError:
        raise
    except httpx.HTTPError as exc:
        raise TTSProviderError("TTS upstream request failed") from exc

    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TTSProviderError("TTS upstream returned invalid JSON") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise TTSProviderError("TTS upstream rejected the synthesis request")

    audio = body.get("audio")
    audio_format = str(body.get("format") or "pcm").strip().lower()
    if not isinstance(audio, str) or not audio:
        raise TTSProviderError("TTS upstream returned no audio")
    if "pcm" not in audio_format and not audio_format.startswith("audio/l16"):
        raise TTSProviderError("TTS upstream returned an unsupported audio format")
    try:
        decoded = base64.b64decode(audio, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise TTSProviderError("TTS upstream returned invalid base64 audio") from exc
    if not decoded or len(decoded) > settings.tts_max_audio_bytes:
        raise TTSProviderError("TTS audio exceeds configured limit")
    if len(decoded) % 2:
        raise TTSProviderError("TTS PCM audio is truncated")

    format_rate = re.search(r"(?:^|;)rate=(\d+)(?:;|$)", audio_format)
    sample_rate_value = body.get("sample_rate")
    if sample_rate_value is None and format_rate:
        sample_rate_value = format_rate.group(1)
    try:
        sample_rate = int(sample_rate_value or settings.tts_sample_rate)
    except (TypeError, ValueError) as exc:
        raise TTSProviderError("TTS upstream returned an invalid sample rate") from exc
    if not 8000 <= sample_rate <= 48000:
        raise TTSProviderError("TTS upstream returned an invalid sample rate")

    return SynthesizedSpeech(
        audio=audio,
        format=audio_format,
        sample_rate=sample_rate,
    )
