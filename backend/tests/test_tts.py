from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import admin_user
from app.core import database
from app.core.config import settings
from app.main import app
from app.models.auth import User
from app.services import tts


@pytest.fixture
def tts_api_database(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "tts.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    try:
        yield
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


class FakeStream:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakeClient:
    def __init__(self, response: httpx.Response, captured: dict[str, object]):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def stream(self, method: str, url: str, *, json: dict[str, object]):
        self.captured.update(method=method, url=url, json=json)
        return FakeStream(self.response)


def _response(body: dict[str, object]) -> httpx.Response:
    request = httpx.Request("POST", "https://example.com/tts")
    return httpx.Response(200, request=request, content=json.dumps(body).encode())


@pytest.mark.asyncio
async def test_synthesize_speech_sends_voice_settings_and_validates_pcm(monkeypatch):
    captured: dict[str, object] = {}
    pcm = b"\x00\x00\xff\x7f"
    response = _response(
        {
            "success": True,
            "audio": base64.b64encode(pcm).decode("ascii"),
            "format": "audio/L16;rate=22050",
        }
    )
    monkeypatch.setattr(
        tts.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(response, captured),
    )
    monkeypatch.setattr(settings, "tts_service_url", "https://example.com/tts")
    monkeypatch.setattr(settings, "tts_voice", "x4_yezi")
    monkeypatch.setattr(settings, "tts_speed", 48)
    monkeypatch.setattr(settings, "tts_pitch", 52)
    monkeypatch.setattr(settings, "tts_volume", 55)

    result = await tts.synthesize_speech("请确认公司名称")

    assert result.audio == base64.b64encode(pcm).decode("ascii")
    assert result.format == "audio/l16;rate=22050"
    assert result.sample_rate == 22050
    assert captured == {
        "method": "POST",
        "url": "https://example.com/tts",
        "json": {
            "text": "请确认公司名称",
            "vcn": "x4_yezi",
            "speed": 48,
            "pitch": 52,
            "volume": 55,
        },
    }


@pytest.mark.asyncio
async def test_synthesize_speech_requires_service_url(monkeypatch):
    monkeypatch.setattr(settings, "tts_service_url", "")

    with pytest.raises(tts.TTSNotConfiguredError):
        await tts.synthesize_speech("你好")


@pytest.mark.asyncio
async def test_synthesize_speech_maps_upstream_http_failure(monkeypatch):
    captured: dict[str, object] = {}
    request = httpx.Request("POST", "https://example.com/tts")
    response = httpx.Response(503, request=request)
    monkeypatch.setattr(
        tts.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(response, captured),
    )
    monkeypatch.setattr(settings, "tts_service_url", "https://example.com/tts")

    with pytest.raises(tts.TTSProviderError, match="upstream request failed"):
        await tts.synthesize_speech("你好")


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_invalid_base64(monkeypatch):
    captured: dict[str, object] = {}
    response = _response({"success": True, "audio": "not-base64!", "format": "pcm"})
    monkeypatch.setattr(
        tts.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(response, captured),
    )
    monkeypatch.setattr(settings, "tts_service_url", "https://example.com/tts")

    with pytest.raises(tts.TTSProviderError, match="invalid base64"):
        await tts.synthesize_speech("你好")


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_oversized_response(monkeypatch):
    captured: dict[str, object] = {}
    response = _response({"success": True, "audio": "A" * 128, "format": "pcm"})
    monkeypatch.setattr(
        tts.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(response, captured),
    )
    monkeypatch.setattr(settings, "tts_service_url", "https://example.com/tts")
    monkeypatch.setattr(settings, "tts_max_response_bytes", 32)

    with pytest.raises(tts.TTSProviderError, match="response exceeds"):
        await tts.synthesize_speech("你好")


def test_tts_api_requires_admin_authentication(tts_api_database):
    with TestClient(app) as client:
        response = client.post("/tts/synthesize", json={"text": "你好"})

    assert response.status_code == 401


def test_tts_api_returns_synthesized_audio_for_admin(tts_api_database):
    admin = User(id="usr-tts-admin", username="admin", role="admin", first_login=False)
    speech = tts.SynthesizedSpeech(audio="AAA=", format="pcm", sample_rate=16000)
    app.dependency_overrides[admin_user] = lambda: admin
    try:
        with (
            patch("app.api.tts.synthesize_speech", AsyncMock(return_value=speech)) as synthesize,
            TestClient(app) as client,
        ):
            response = client.post("/tts/synthesize", json={"text": "  请确认  "})
    finally:
        app.dependency_overrides.pop(admin_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "audio": "AAA=",
        "format": "pcm",
        "sample_rate": 16000,
    }
    synthesize.assert_awaited_once_with("请确认")


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (tts.TTSNotConfiguredError("missing"), 503),
        (tts.TTSProviderError("failed"), 502),
    ],
)
def test_tts_api_maps_service_failures(
    tts_api_database,
    error: RuntimeError,
    expected_status: int,
):
    admin = User(id="usr-tts-admin", username="admin", role="admin", first_login=False)
    app.dependency_overrides[admin_user] = lambda: admin
    try:
        with (
            patch(
                "app.api.tts.synthesize_speech",
                AsyncMock(side_effect=error),
            ),
            TestClient(app) as client,
        ):
            response = client.post("/tts/synthesize", json={"text": "请确认"})
    finally:
        app.dependency_overrides.pop(admin_user, None)

    assert response.status_code == expected_status
