from __future__ import annotations

import base64

import httpx
import pytest

from app.core.config import settings
from app.services import asr


@pytest.mark.asyncio
async def test_transcribe_audio_sends_dashscope_data_uri_and_reads_text(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                json={
                    "output": {
                        "output": {"sentence": {"text": "你好，翠花。"}},
                        "text": "你好，翠花。",
                    }
                },
            )

    monkeypatch.setattr(asr.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(
        settings,
        "dashscope_asr_url",
        "https://example.com/asr",
    )

    result = await asr.transcribe_audio(b"wav-bytes", "audio/wav")

    assert result == "你好，翠花。"
    assert captured["url"] == "https://example.com/asr"
    assert captured["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }
    payload = captured["json"]
    assert isinstance(payload, dict)
    audio_data = payload["input"]["messages"][0]["content"][0]["input_audio"]["data"]
    assert audio_data == (
        "data:audio/wav;base64," + base64.b64encode(b"wav-bytes").decode("ascii")
    )
    assert payload["model"] == "qwen-audio-3.0-asr-flash"
    assert payload["parameters"] == {"format": "wav"}


@pytest.mark.asyncio
async def test_transcribe_audio_requires_dashscope_key(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "")

    with pytest.raises(asr.ASRNotConfiguredError):
        await asr.transcribe_audio(b"audio", "audio/webm")
