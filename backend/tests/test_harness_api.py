"""Tests for the Harness API endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
from httpx import ASGITransport, AsyncClient as ASGIAsyncClient
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import harness as harness_api
from app.core import database
from app.core.config import settings
from app.main import app
from app.services.harness.gateway_auth import (
    issue_task_gateway_token,
    verify_task_gateway_token,
)
from app.services.harness.network import NetworkResponse
from app.services.pi_runtime.config import PiRuntimeSettings, ResolvedPiModel
from app.services.pi_runtime import model_proxy as pi_model_proxy
from app.services.pi_runtime.model_proxy import (
    issue_model_proxy_grant,
    read_model_proxy_usage,
)
from tests._auth import bootstrap_admin_response


@pytest.fixture(autouse=True)
def isolated_skill_runtime(tmp_path, monkeypatch):
    # TestClient 会执行内置 Skill 自检，测试不可写入本机真实运行目录。
    monkeypatch.setattr(settings, "pi_runtime_dir", tmp_path / "pi-runtime")
    monkeypatch.setattr(settings, "pi_root", tmp_path / "pi-runtime")


@pytest.mark.asyncio
async def test_model_proxy_body_reader_stops_at_limit(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(harness_api, "_MODEL_PROXY_MAX_BODY_BYTES", 5, raising=False)
    yielded: list[bytes] = []

    class ChunkedRequest:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        async def stream(self):
            for chunk in (b"123", b"456", b"should-not-be-read"):
                yielded.append(chunk)
                yield chunk

    with pytest.raises(HTTPException) as caught:
        await harness_api._read_model_request_body(ChunkedRequest())

    assert caught.value.status_code == 413
    assert yielded == [b"123", b"456"]


@pytest.mark.asyncio
async def test_model_proxy_request_logs_redact_grant_token():
    sensitive_token = "sensitive-model-proxy-grant"
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = CapturingHandler()
    logger = logging.getLogger("app.core.request_middleware")
    original_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        transport = ASGITransport(app=app)
        async with ASGIAsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/admin/harness/model-proxy/{sensitive_token}/chat/completions",
                headers={"Authorization": f"Bearer {sensitive_token}"},
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)

    assert response.status_code == 401
    lifecycle_records = [
        record
        for record in records
        if record.getMessage() in {"request.start", "request.end"}
    ]
    assert len(lifecycle_records) == 2
    assert {getattr(record, "path", None) for record in lifecycle_records} == {
        "/admin/harness/model-proxy/<redacted>/chat/completions"
    }
    assert all(
        sensitive_token not in str(record.__dict__) for record in lifecycle_records
    )


@pytest.mark.asyncio
async def test_model_proxy_releases_grant_when_body_is_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")
    monkeypatch.setattr(harness_api, "_MODEL_PROXY_MAX_BODY_BYTES", 5, raising=False)
    model = ResolvedPiModel(
        credential_id="credential-1",
        provider_name="test-provider",
        model="test-model",
        base_url="https://model.example/v1",
        api_key="provider-secret",
    )
    grant, _ = issue_model_proxy_grant(
        model,
        "http://testserver",
        "master-secret",
        runtime.root / "model-grants",
        hard_lifetime_seconds=60,
        lease_seconds=60,
    )

    transport = ASGITransport(app=app)
    async with ASGIAsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/admin/harness/model-proxy/{grant.token}/chat/completions",
            headers={"Authorization": f"Bearer {grant.token}"},
            content=b"123456",
        )

    assert response.status_code == 413
    marker = next((runtime.root / "model-grants").glob("*.json"))
    assert json.loads(marker.read_text(encoding="utf-8"))["active"] == 0


@pytest.mark.asyncio
async def test_model_proxy_returns_bad_gateway_when_upstream_connection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")

    model = ResolvedPiModel(
        credential_id="credential-1",
        provider_name="test-provider",
        model="test-model",
        base_url="https://model.example/v1",
        api_key="provider-secret",
    )
    grant, _ = issue_model_proxy_grant(
        model,
        "http://testserver",
        "master-secret",
        runtime.root / "model-grants",
        hard_lifetime_seconds=60,
        lease_seconds=60,
    )
    clients = []

    class FailingAsyncClient:
        def __init__(self, **kwargs: object):
            del kwargs
            self.closed = False
            clients.append(self)

        def build_request(
            self, method: str, url: str, **kwargs: object
        ) -> httpx.Request:
            del kwargs
            return httpx.Request(method, url)

        async def send(self, request: httpx.Request, *, stream: bool):
            assert stream is True
            raise httpx.ConnectError("connection refused", request=request)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(harness_api.httpx, "AsyncClient", FailingAsyncClient)

    transport = ASGITransport(app=app)
    async with ASGIAsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/admin/harness/model-proxy/{grant.token}/chat/completions",
            headers={"Authorization": f"Bearer {grant.token}"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "模型端点不可达，请检查 endpoint_url、网络和服务状态"
    }
    assert clients and getattr(clients[0], "closed", False)
    marker = next((runtime.root / "model-grants").glob("*.json"))
    assert json.loads(marker.read_text(encoding="utf-8"))["active"] == 0


@pytest.mark.asyncio
async def test_model_proxy_omits_empty_upstream_auth_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")

    model = ResolvedPiModel(
        credential_id="credential-1",
        provider_name="local",
        model="local-model",
        base_url="http://host.docker.internal:8080/v1",
        api_key="",
    )
    grant, _ = issue_model_proxy_grant(
        model,
        "http://testserver",
        "master-secret",
        runtime.root / "model-grants",
        hard_lifetime_seconds=60,
        lease_seconds=60,
    )
    captured: dict[str, object] = {}

    class UpstreamResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_raw(self):
            yield b"{}"

        async def aclose(self):
            return None

    class RecordingAsyncClient:
        def __init__(self, **kwargs: object):
            captured.update(kwargs)

        def build_request(
            self, method: str, url: str, **kwargs: object
        ) -> httpx.Request:
            captured["headers"] = dict(kwargs.get("headers") or {})
            return httpx.Request(
                method,
                url,
                headers=kwargs.get("headers"),
                content=kwargs.get("content"),
            )

        async def send(self, request: httpx.Request, *, stream: bool):
            del request
            assert stream is True
            return UpstreamResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(harness_api.httpx, "AsyncClient", RecordingAsyncClient)

    transport = ASGITransport(app=app)
    async with ASGIAsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/admin/harness/model-proxy/{grant.token}/chat/completions",
            headers={"Authorization": f"Bearer {grant.token}"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    upstream_headers = {
        str(key).lower(): str(value) for key, value in captured["headers"].items()
    }
    assert "authorization" not in upstream_headers
    assert "api-key" not in upstream_headers
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.write == 30.0
    assert timeout.pool == 10.0
    assert timeout.read is None


@pytest.mark.asyncio
async def test_model_proxy_anthropic_accepts_x_api_key_and_forwards_messages_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sandbox Anthropic chat: grant presented as x-api-key (SDK default) and
    the upstream call carries x-api-key + anthropic-version to /v1/messages."""
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")

    model = ResolvedPiModel(
        credential_id="credential-1",
        provider_name="anthropic",
        model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-upstream",
        adapter="anthropic",
        api_protocol="messages",
    )
    grant, _ = issue_model_proxy_grant(
        model,
        "http://testserver",
        "master-secret",
        runtime.root / "model-grants",
        hard_lifetime_seconds=60,
        lease_seconds=60,
    )
    captured: dict[str, object] = {}

    class UpstreamResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_raw(self):
            yield b'{"content":[{"type":"text","text":"hi"}]}'

        async def aclose(self):
            return None

    class RecordingAsyncClient:
        def __init__(self, **kwargs: object):
            captured.update(kwargs)

        def build_request(
            self, method: str, url: str, **kwargs: object
        ) -> httpx.Request:
            captured["url"] = url
            captured["headers"] = dict(kwargs.get("headers") or {})
            return httpx.Request(
                method,
                url,
                headers=kwargs.get("headers"),
                content=kwargs.get("content"),
            )

        async def send(self, request: httpx.Request, *, stream: bool):
            del request
            assert stream is True
            return UpstreamResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(harness_api.httpx, "AsyncClient", RecordingAsyncClient)

    transport = ASGITransport(app=app)
    async with ASGIAsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/admin/harness/model-proxy/{grant.token}/v1/messages",
            # The Anthropic SDK sends the grant token as x-api-key.
            headers={"x-api-key": grant.token},
            json={"model": "claude-sonnet-4-5", "max_tokens": 2048},
        )

    assert response.status_code == 200
    assert str(captured["url"]).endswith("/v1/messages")
    upstream_headers = {
        str(key).lower(): str(value) for key, value in captured["headers"].items()
    }
    # Official Anthropic upstream auth via x-api-key + version header.
    assert upstream_headers.get("x-api-key") == "sk-ant-upstream"
    assert upstream_headers.get("anthropic-version") == "2023-06-01"
    assert "authorization" not in upstream_headers


@pytest.mark.parametrize(
    ("retry_after", "expected_retry_after"),
    [("9", 9), ("120", 120), ("invalid", None)],
)
@pytest.mark.asyncio
async def test_model_proxy_logs_and_forwards_upstream_error_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
    expected_retry_after: int | None,
):
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")

    model = ResolvedPiModel(
        credential_id="credential-1",
        provider_name="test-provider",
        model="test-model",
        base_url="https://model.example/v1",
        api_key="provider-secret",
    )
    grant, _ = issue_model_proxy_grant(
        model,
        "http://testserver",
        "master-secret",
        runtime.root / "model-grants",
        hard_lifetime_seconds=60,
        lease_seconds=60,
    )
    error_payload = json.dumps(
        {"error": {"message": "model does not exist", "type": "invalid_request_error"}}
    ).encode()

    class ErrorUpstreamResponse:
        status_code = 429
        headers = {
            "content-type": "application/json",
            "retry-after": retry_after,
        }

        async def aread(self):
            return error_payload

        async def aclose(self):
            return None

    class ErrorAsyncClient:
        def __init__(self, **kwargs: object):
            del kwargs

        def build_request(
            self, method: str, url: str, **kwargs: object
        ) -> httpx.Request:
            return httpx.Request(method, url)

        async def send(self, request: httpx.Request, *, stream: bool):
            assert stream is True
            return ErrorUpstreamResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(harness_api.httpx, "AsyncClient", ErrorAsyncClient)
    response_constructed = False
    model_proxy_response = harness_api._ModelProxyResponse

    def observed_model_proxy_response(*args, **kwargs):
        nonlocal response_constructed
        assert (
            pi_model_proxy.read_model_proxy_retry_after(
                grant.token,
                runtime.root / "model-grants",
            )
            == expected_retry_after
        )
        response_constructed = True
        return model_proxy_response(*args, **kwargs)

    monkeypatch.setattr(
        harness_api,
        "_ModelProxyResponse",
        observed_model_proxy_response,
    )

    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord):
            records.append(record)

    handler = CapturingHandler()
    logger = logging.getLogger("app.api.harness")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        transport = ASGITransport(app=app)
        async with ASGIAsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/admin/harness/model-proxy/{grant.token}/chat/completions",
                headers={"Authorization": f"Bearer {grant.token}"},
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 429
    assert response.json() == json.loads(error_payload.decode())
    assert response.headers["retry-after"] == retry_after
    assert (
        pi_model_proxy.read_model_proxy_retry_after(
            grant.token,
            runtime.root / "model-grants",
        )
        == expected_retry_after
    )
    assert response_constructed is True
    upstream_logs = [
        r for r in records if r.getMessage().startswith("Upstream model endpoint")
    ]
    assert upstream_logs
    assert "status=429" in upstream_logs[0].getMessage()
    marker = next((runtime.root / "model-grants").glob("*.json"))
    assert json.loads(marker.read_text(encoding="utf-8"))["active"] == 0


@pytest.fixture
def budget_model_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(harness_api.PiRuntimeSettings, "from_env", lambda: runtime)
    monkeypatch.setattr(settings, "pi_harness_gateway_token", "master-secret")
    grant_root = runtime.root / "model-grants"

    def issue(
        protocol: str = "chat_completions",
        *,
        max_tokens: int | None = 500_000,
        track_usage: bool = False,
        require_verified_usage: bool = False,
    ):
        model = ResolvedPiModel(
            credential_id="budget-credential",
            provider_name="controlled-provider",
            model="controlled-model",
            base_url="https://model.example/v1",
            api_key="provider-secret",
            api_protocol=protocol,
        )
        grant, _ = issue_model_proxy_grant(
            model,
            "http://testserver",
            "master-secret",
            grant_root,
            hard_lifetime_seconds=None,
            lease_seconds=60,
            max_tokens=max_tokens,
            track_usage=track_usage,
            require_verified_usage=require_verified_usage,
        )
        return grant

    return grant_root, issue


class _ModelProxyChunks(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.body), 17):
            yield self.body[offset : offset + 17]

    async def aclose(self):
        await asyncio.sleep(0)
        self.closed = True


def _install_model_proxy_upstream(monkeypatch: pytest.MonkeyPatch, handler):
    clients: list[ASGIAsyncClient] = []

    def create_client(**kwargs):
        client = ASGIAsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(harness_api.httpx, "AsyncClient", create_client)
    return clients


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol", "path", "body", "terminal"),
    [
        (
            "chat_completions",
            "chat/completions",
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            b'data: {"usage":{"prompt_tokens":499980,"completion_tokens":20,'
            b'"completion_tokens_details":{"reasoning_tokens":15}}}\n\n',
            b"data: [DONE]\n\n",
        ),
        (
            "responses",
            "responses",
            b'event: response.output_text.delta\n'
            b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n',
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":{"usage":'
            b'{"input_tokens":499980,"output_tokens":20,'
            b'"output_tokens_details":{"reasoning_tokens":15}}}}\n\n',
        ),
        (
            "messages",
            "v1/messages",
            b'event: message_start\n'
            b'data: {"type":"message_start","message":{"usage":'
            b'{"input_tokens":400000,"cache_read_input_tokens":90000,'
            b'"cache_creation_input_tokens":9980,"output_tokens":0}}}\n\n'
            b'event: message_delta\n'
            b'data: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
            b'event: message_delta\n'
            b'data: {"type":"message_delta","usage":{"output_tokens":20}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ),
    ],
)
async def test_model_proxy_budget_is_durable_before_terminal_reaches_child(
    budget_model_proxy, monkeypatch: pytest.MonkeyPatch, protocol, path, body, terminal
):
    grant_root, issue = budget_model_proxy
    grant = issue(protocol)
    raw_body = body + terminal + b": provider trailer\n\n"
    upstream_requests: list[httpx.Request] = []
    streams: list[_ModelProxyChunks] = []

    def upstream(request: httpx.Request):
        upstream_requests.append(request)
        stream = _ModelProxyChunks(raw_body)
        streams.append(stream)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "cache-control": "no-cache",
                "x-request-id": "controlled-request",
                "retry-after": "9",
            },
            stream=stream,
        )

    clients = _install_model_proxy_upstream(monkeypatch, upstream)
    terminal_response = None
    terminal_usage = None
    delivered = bytearray()
    checked_terminal = False
    url = f"/admin/harness/model-proxy/{grant.token}/{path}"
    headers = {"Authorization": f"Bearer {grant.token}"}
    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "stream_options": {"include_usage": False, "custom_option": "preserved"},
        "max_tokens": 900_000,
    }
    endpoint_app = FastAPI()
    endpoint_app.include_router(harness_api.router)

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            nonlocal checked_terminal, terminal_response, terminal_usage
            if message["type"] == "http.response.body":
                delivered.extend(message.get("body", b""))
                if not checked_terminal and terminal in delivered:
                    checked_terminal = True
                    terminal_usage = read_model_proxy_usage(grant.token, grant_root)
                    terminal_response = await child.post(url, headers=headers, json=payload)
            await send(message)

        # Exercise the actual endpoint without middleware prefetching its body:
        # the provider iterator remains suspended at the terminal chunk here.
        await endpoint_app(scope, receive, observed_send)

    async with ASGIAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as child, ASGIAsyncClient(
        transport=ASGITransport(app=observed_app), base_url="http://testserver"
    ) as client:
        response = await client.post(url, headers=headers, json=payload)
        assert response.status_code == 200
        assert response.content == raw_body
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-request-id"] == "controlled-request"
        assert response.headers["retry-after"] == "9"
        assert terminal_response is not None
        assert terminal_response.status_code == 400
        error = terminal_response.json()["error"]
        assert error["type"] == "invalid_request_error"
        assert error["code"] == "chat_token_budget_exceeded"
        assert error["message"].startswith(f'[{error["code"]}] ')
        assert len(upstream_requests) == 1
        assert terminal_usage is not None
        assert {
            key: terminal_usage[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        } == {
            "prompt_tokens": 499_980,
            "completion_tokens": 20,
            "total_tokens": 500_000,
        }
        assert read_model_proxy_usage(grant.token, grant_root) == terminal_usage
        if protocol == "chat_completions":
            forwarded = json.loads(upstream_requests[0].content)
            assert forwarded["stream_options"] == {
                "include_usage": True,
                "custom_option": "preserved",
            }
            assert forwarded["max_tokens"] == 32_768

        fresh = issue(protocol)
        fresh_response = await child.post(
            f"/admin/harness/model-proxy/{fresh.token}/{path}",
            headers={"Authorization": f"Bearer {fresh.token}"},
            json=payload,
        )
        assert fresh_response.status_code == 200
        assert len(upstream_requests) == 2

    assert all(stream.closed for stream in streams)
    assert all(client.is_closed for client in clients)
    assert all(
        json.loads(marker.read_text())["active"] == 0
        for marker in grant_root.glob("*.json")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n',
        b'{"usage":{"prompt_tokens":10,"completion_tokens":"unknown"}}',
    ],
    ids=["missing-usage", "invalid-json-usage"],
)
@pytest.mark.parametrize(
    "track_only",
    [False, True],
    ids=["hard-cap", "track-only"],
)
async def test_model_proxy_unmetered_response_is_charged_then_retried(
    budget_model_proxy,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    track_only: bool,
):
    """无法计量的响应按保守值记账，连续失败后停止。"""
    grant_root, issue = budget_model_proxy
    grant = (
        issue(max_tokens=None, track_usage=True)
        if track_only
        else issue()
    )
    streams = []

    def upstream(request):
        stream = _ModelProxyChunks(body)
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    clients = _install_model_proxy_upstream(monkeypatch, upstream)
    attempts = pi_model_proxy._MAX_UNMETERED_STREAK
    async with ASGIAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        url = f"/admin/harness/model-proxy/{grant.token}/chat/completions"
        headers = {"Authorization": f"Bearer {grant.token}"}
        responses = [
            await client.post(url, headers=headers, json={"messages": []})
            for _ in range(attempts + 1)
        ]

    assert [response.status_code for response in responses] == (
        [200] * attempts + [400]
    )
    assert responses[0].content == body
    error = responses[-1].json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "chat_token_usage_unavailable"
    assert error["message"].startswith(f'[{error["code"]}] ')
    assert len(streams) == attempts
    assert all(stream.closed for stream in streams)
    assert all(child.is_closed for child in clients)
    marker = next(grant_root.glob("*.json"))
    state = json.loads(marker.read_text())
    assert state["active"] == 0
    assert state["unmetered_streak"] == attempts
    # Unmetered never means free: the turn still moves toward its ceiling.
    assert state["total_tokens"] == (
        attempts * pi_model_proxy._UNMETERED_TOKEN_CHARGE
    )


@pytest.mark.asyncio
async def test_model_proxy_strict_usage_blocks_after_first_unmetered_response(
    budget_model_proxy,
    monkeypatch: pytest.MonkeyPatch,
):
    grant_root, issue = budget_model_proxy
    grant = issue(
        max_tokens=None,
        require_verified_usage=True,
    )
    upstream_requests: list[httpx.Request] = []
    body = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'

    def upstream(request: httpx.Request):
        upstream_requests.append(request)
        return httpx.Response(200, stream=_ModelProxyChunks(body))

    clients = _install_model_proxy_upstream(monkeypatch, upstream)
    url = f"/admin/harness/model-proxy/{grant.token}/chat/completions"
    headers = {"Authorization": f"Bearer {grant.token}"}
    async with ASGIAsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(url, headers=headers, json={"messages": []})
        second = await client.post(url, headers=headers, json={"messages": []})

    assert first.status_code == 200
    assert first.content == body
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "chat_token_usage_unavailable"
    assert len(upstream_requests) == 1
    state = json.loads(next(grant_root.glob("*.json")).read_text())
    assert state["usage_unavailable"] is True
    assert state["total_tokens"] == 0
    assert state["unmetered_responses"] == 1
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "track_only",
    [False, True],
    ids=["hard-cap", "track-only"],
)
async def test_model_proxy_meters_a_stream_without_a_terminator(
    budget_model_proxy,
    monkeypatch: pytest.MonkeyPatch,
    track_only: bool,
):
    """即使供应商不发送终止事件，也必须记录完整响应中的用量。"""
    grant_root, issue = budget_model_proxy
    grant = (
        issue(max_tokens=None, track_usage=True)
        if track_only
        else issue()
    )
    body = b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'

    def upstream(request):
        return httpx.Response(200, stream=_ModelProxyChunks(body))

    _install_model_proxy_upstream(monkeypatch, upstream)
    async with ASGIAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        url = f"/admin/harness/model-proxy/{grant.token}/chat/completions"
        headers = {"Authorization": f"Bearer {grant.token}"}
        first = await client.post(url, headers=headers, json={"messages": []})
        second = await client.post(url, headers=headers, json={"messages": []})

    assert (first.status_code, second.status_code) == (200, 200)
    state = json.loads(next(grant_root.glob("*.json")).read_text())
    assert state["unmetered_streak"] == 0
    assert state["total_tokens"] == 30


@pytest.mark.asyncio
async def test_model_proxy_accounts_json_but_not_unsuccessful_responses(
    budget_model_proxy, monkeypatch: pytest.MonkeyPatch
):
    grant_root, issue = budget_model_proxy
    grant = issue("messages")
    bodies = [
        (400, b'{"error":{"message":"invalid request"},"usage":{"input_tokens":500000,"output_tokens":0}}'),
        (200, b'{"usage":{"input_tokens":249980,"output_tokens":20}}'),
        (200, b'{"usage":{"input_tokens":249980,"output_tokens":20}}'),
    ]
    streams = []

    def upstream(request):
        status, body = bodies[len(streams)]
        stream = _ModelProxyChunks(body)
        streams.append(stream)
        return httpx.Response(
            status, headers={"content-type": "application/json"}, stream=stream
        )

    clients = _install_model_proxy_upstream(monkeypatch, upstream)
    async with ASGIAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        url = f"/admin/harness/model-proxy/{grant.token}/v1/messages"
        headers = {"x-api-key": grant.token}
        first = await client.post(url, headers=headers, json={"max_tokens": 1024})
        assert first.status_code == 400 and first.content == bodies[0][1]
        usage = read_model_proxy_usage(grant.token, grant_root)
        assert usage is not None and usage["total_tokens"] == 0
        second = await client.post(url, headers=headers, json={"max_tokens": 1024})
        usage = read_model_proxy_usage(grant.token, grant_root)
        assert usage is not None and usage["total_tokens"] == 250_000
        third = await client.post(url, headers=headers, json={"max_tokens": 1024})
        fourth = await client.post(url, headers=headers, json={"max_tokens": 1024})

    assert second.status_code == 200 and second.content == bodies[1][1]
    assert third.status_code == 200 and third.content == bodies[2][1]
    assert fourth.status_code == 400
    assert fourth.json()["error"]["code"] == "chat_token_budget_exceeded"
    usage = read_model_proxy_usage(grant.token, grant_root)
    assert usage is not None
    assert {
        key: usage[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    } == {
        "prompt_tokens": 499_960, "completion_tokens": 40, "total_tokens": 500_000
    }
    assert len(streams) == 3
    assert all(stream.closed for stream in streams)
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "read_error", "before_body"])
@pytest.mark.parametrize(
    "track_only",
    [False, True],
    ids=["hard-cap", "track-only"],
)
async def test_model_proxy_interrupted_stream_fails_closed_and_releases_resources(
    budget_model_proxy,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    track_only: bool,
):
    grant_root, issue = budget_model_proxy
    grant = (
        issue(max_tokens=None, track_usage=True)
        if track_only
        else issue()
    )
    started = asyncio.Event()
    unblock = asyncio.Event()
    endpoint_app = FastAPI()
    endpoint_app.include_router(harness_api.router)

    class InterruptedStream(_ModelProxyChunks):
        async def __aiter__(self):
            yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
            started.set()
            await unblock.wait()
            raise httpx.ReadError("controlled stream failure")

    stream = InterruptedStream(b"")
    upstream_requests = []

    def upstream(request):
        upstream_requests.append(request)
        if len(upstream_requests) > 1:
            return httpx.Response(200, stream=_ModelProxyChunks(b"{}"))
        return httpx.Response(200, stream=stream)

    async def interrupted_app(scope, receive, send):
        async def interrupted_send(message):
            if message["type"] == "http.response.start" and message["status"] == 200:
                started.set()
                await unblock.wait()
            await send(message)

        await endpoint_app(scope, receive, interrupted_send)

    clients = _install_model_proxy_upstream(monkeypatch, upstream)
    async with ASGIAsyncClient(
        transport=ASGITransport(
            app=interrupted_app if failure == "before_body" else app
        ),
        base_url="http://testserver",
    ) as client:
        url = f"/admin/harness/model-proxy/{grant.token}/chat/completions"
        headers = {"Authorization": f"Bearer {grant.token}"}
        pending = asyncio.create_task(
            client.post(url, headers=headers, json={"messages": []})
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            if failure != "read_error":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                unblock.set()
                with pytest.raises(httpx.ReadError):
                    await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        if failure == "before_body":
            # 首次响应头阻塞已完成；重试必须放行同一个 ASGI send 包装器。
            unblock.set()
        retried = await client.post(url, headers=headers, json={"messages": []})

    # A transport failure or a client disconnect is one unmetered response, not
    # the end of the turn: the caller may retry, and the attempt is charged
    # rather than silently free.
    assert retried.status_code == 200
    assert len(upstream_requests) == 2
    assert stream.closed
    assert all(child.is_closed for child in clients)
    state = json.loads(next(grant_root.glob("*.json")).read_text())
    assert state["active"] == 0
    assert state["unmetered_streak"] == 2
    assert state["total_tokens"] == 2 * pi_model_proxy._UNMETERED_TOKEN_CHARGE


def test_task_gateway_token_is_identity_scoped_and_expires():
    token = issue_task_gateway_token(
        "master", "task-1", "tenant-1", "user-1", issued_at=1_000
    )

    assert verify_task_gateway_token(
        token, "master", "task-1", "tenant-1", "user-1", now=1_100
    )
    assert not verify_task_gateway_token(
        token, "master", "task-2", "tenant-1", "user-1", now=1_100
    )
    assert not verify_task_gateway_token(
        token, "master", "task-1", "tenant-1", "user-1", now=5_000
    )


def test_task_gateway_token_outlives_its_window_only_while_the_turn_runs(
    tmp_path: Path,
):
    """A turn with no wall-clock budget cannot be handed a fresh token."""
    from app.services.pi_runtime import run_lease

    token = issue_task_gateway_token(
        "master", "task-1", "tenant-1", "user-1", issued_at=1_000
    )
    # Expired, with no run holding it.
    assert not verify_task_gateway_token(
        token, "master", "task-1", "tenant-1", "user-1", now=5_000,
        lease_root=tmp_path,
    )
    identifier = run_lease.open_run_lease(tmp_path, token, lease_seconds=960)
    assert verify_task_gateway_token(
        token, "master", "task-1", "tenant-1", "user-1", now=5_000,
        lease_root=tmp_path,
    )
    # A lease extends one credential, never a forged or unrelated one.
    assert not verify_task_gateway_token(
        token, "master", "task-2", "tenant-1", "user-1", now=5_000,
        lease_root=tmp_path,
    )
    other = issue_task_gateway_token(
        "master", "task-1", "tenant-1", "user-1", issued_at=900
    )
    assert not verify_task_gateway_token(
        other, "master", "task-1", "tenant-1", "user-1", now=5_000,
        lease_root=tmp_path,
    )
    # The lease dies with the turn, and an expired one is never resurrected.
    run_lease.close_run_lease(tmp_path, identifier)
    assert not run_lease.renew_run_lease(tmp_path, identifier, lease_seconds=960)
    assert not verify_task_gateway_token(
        token, "master", "task-1", "tenant-1", "user-1", now=5_000,
        lease_root=tmp_path,
    )


def test_capability_token_outlives_its_ttl_only_while_the_turn_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The child holds this grant in its env and cannot be given a new one."""
    import jwt

    from app.services.agent_capability import (
        create_capability_token,
        decode_capability_token,
    )
    from app.services.pi_runtime import run_lease
    from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID

    monkeypatch.setattr(settings, "pi_runtime_dir", tmp_path)
    monkeypatch.setattr(settings, "agent_capability_ttl_seconds", -60)
    token = create_capability_token(
        user_id="user-owner",
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_capability_token(token)
    identifier = run_lease.open_run_lease(tmp_path, token, lease_seconds=960)
    assert decode_capability_token(token)["sub"] == "user-owner"
    # Only this exact grant is extended, and only until its turn ends.
    monkeypatch.setattr(settings, "agent_capability_ttl_seconds", -60)
    other = create_capability_token(
        user_id="user-owner",
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["get_chat_history_context"],
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_capability_token(other)
    run_lease.close_run_lease(tmp_path, identifier)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_capability_token(token)


def test_run_lease_expires_on_its_own_and_prunes_abandoned_leases(tmp_path: Path):
    """A crashed run must not leave a credential valid forever."""
    from app.services.pi_runtime import run_lease

    identifier = run_lease.open_run_lease(tmp_path, "secret", lease_seconds=60)
    assert run_lease.secret_lease_active(tmp_path, "secret")
    assert not run_lease.secret_lease_active(tmp_path, "other-secret")
    assert not run_lease.secret_lease_active(tmp_path, "")
    assert run_lease.run_lease_active(tmp_path, identifier, now=time.time() + 59)
    assert not run_lease.run_lease_active(tmp_path, identifier, now=time.time() + 61)
    abandoned = run_lease.open_run_lease(tmp_path, "crashed", lease_seconds=1)
    lease_file = tmp_path / "run-leases" / f"{abandoned}.json"
    lease_file.write_text(json.dumps({"expires_at": time.time() - 1}))
    run_lease.open_run_lease(tmp_path, "another", lease_seconds=60)
    assert not lease_file.exists()


def test_harness_api_full_flow(tmp_path: Path):
    """End-to-end: create task, list, verify, audit, cleanup."""
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "harness.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            # Login as admin
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}

            # -- Runtime status --------------------------------------------------
            resp = client.get("/admin/harness/runtime/status", headers=headers)
            assert resp.status_code == 200
            status = resp.json()
            assert status["harness"] == "active"
            assert status["policy_rules"] > 0

            # -- Create task -----------------------------------------------------
            resp = client.post(
                "/admin/harness/tasks",
                headers=headers,
                json={
                    "task_id": "TASK-API-001",
                    "objective": "test the API",
                    "permissions": {"shell": True, "git_push": False},
                    "limits": {"max_steps": 10, "timeout_seconds": 60},
                    "verification": {"build_cmd": "echo ok"},
                },
            )
            assert resp.status_code == 201
            task_data = resp.json()
            assert task_data["task"]["task_id"] == "TASK-API-001"
            assert task_data["task"]["permissions"]["shell"] is True
            assert task_data["task"]["permissions"]["git_push"] is False
            assert task_data["workspace"]["path"]  # workspace created

            # -- List tasks ------------------------------------------------------
            resp = client.get("/admin/harness/tasks", headers=headers)
            assert resp.status_code == 200
            tasks = resp.json()["items"]
            assert any(t["task_id"] == "TASK-API-001" for t in tasks)

            # -- List policies ---------------------------------------------------
            resp = client.get("/admin/harness/policies", headers=headers)
            assert resp.status_code == 200
            policies = resp.json()["items"]
            assert len(policies) > 0
            # Should have default rules
            rule_ids = [r["id"] for r in policies]
            assert "deny-rf-root" in rule_ids
            assert "ask-git-push" in rule_ids

            # -- Add custom policy -----------------------------------------------
            resp = client.post(
                "/admin/harness/policies",
                headers=headers,
                json={
                    "id": "deny-curl-external",
                    "tool_pattern": "bash",
                    "arg_pattern": r"\bcurl\b",
                    "decision": "DENY",
                    "reason": "external HTTP blocked",
                    "priority": 800,
                },
            )
            assert resp.status_code == 201
            assert resp.json()["added"] == "deny-curl-external"

            # -- Execute tool through gateway (allowed) -------------------------
            resp = client.post(
                "/admin/harness/tasks/TASK-API-001/execute",
                headers=headers,
                json={"tool_name": "bash", "arguments": {"command": "echo hello"}},
            )
            assert resp.status_code == 200
            exec_result = resp.json()
            assert exec_result["success"] is True
            assert "hello" in exec_result["output"]

            # -- Execute tool (denied by policy) ---------------------------------
            resp = client.post(
                "/admin/harness/tasks/TASK-API-001/execute",
                headers=headers,
                json={"tool_name": "bash", "arguments": {"command": "rm -rf /"}},
            )
            assert resp.status_code == 200
            exec_result = resp.json()
            assert exec_result["is_denied"] is True

            # -- Execute tool (ask — creates approval) --------------------------
            resp = client.post(
                "/admin/harness/tasks/TASK-API-001/execute",
                headers=headers,
                json={
                    "tool_name": "bash",
                    "arguments": {"command": "git push origin main"},
                },
            )
            assert resp.status_code == 200
            exec_result = resp.json()
            assert exec_result["approval_required"] is True
            request_id = exec_result["approval_request_id"]

            # -- List approvals --------------------------------------------------
            resp = client.get("/admin/harness/approvals", headers=headers)
            assert resp.status_code == 200
            approvals = resp.json()["items"]
            assert len(approvals) >= 1
            assert approvals[0]["status"] == "pending"

            # -- Approve ---------------------------------------------------------
            resp = client.post(
                f"/admin/harness/approvals/{request_id}/resolve",
                headers=headers,
                json={"approved": True, "approver_id": "admin", "notes": "OK"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "approved"

            # -- Verify task (skipped because fresh task has no verification config)
            resp = client.post(
                "/admin/harness/tasks/TASK-API-001/verify",
                headers=headers,
            )
            assert resp.status_code == 200
            verify_result = resp.json()
            assert verify_result["status"] in ("passed", "skipped")

            # -- Audit log -------------------------------------------------------
            resp = client.get("/admin/harness/audit", headers=headers)
            assert resp.status_code == 200
            audit = resp.json()
            assert audit["total"] > 0
            event_types = {e["event_type"] for e in audit["items"]}
            assert "task_start" in event_types
            assert "tool_call" in event_types

            # -- Audit filtered by task ------------------------------------------
            resp = client.get(
                "/admin/harness/audit?task_id=TASK-API-001", headers=headers
            )
            assert resp.status_code == 200
            for entry in resp.json()["items"]:
                assert entry["task_id"] == "TASK-API-001"

            # -- Remove custom policy --------------------------------------------
            resp = client.delete(
                "/admin/harness/policies/deny-curl-external", headers=headers
            )
            assert resp.status_code == 200
            assert resp.json()["removed"] == "deny-curl-external"

            # -- Delete task (cleanup) -------------------------------------------
            resp = client.delete("/admin/harness/tasks/TASK-API-001", headers=headers)
            assert resp.status_code == 200

            # -- Task gone from list ---------------------------------------------
            resp = client.get("/admin/harness/tasks", headers=headers)
            assert resp.status_code == 200
            assert not any(t["task_id"] == "TASK-API-001" for t in resp.json()["items"])

    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness.sqlite3")


def test_harness_routes_require_admin(tmp_path: Path):
    """Non-admin users cannot access Harness endpoints."""
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "harness-auth.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}

            # Create a regular user
            invite = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "reader",
                    "email": "reader@example.com",
                    "display_name": "普通用户",
                },
            ).json()
            client.post(
                f"/auth/invitations/{invite['invite_token']}/accept",
                json={
                    "password": "Reader-password-123",
                    "password_confirmation": "Reader-password-123",
                },
            )
            reader = client.post(
                "/auth/login",
                json={"username": "reader", "password": "Reader-password-123"},
            ).json()
            reader_headers = {"Authorization": f"Bearer {reader['access_token']}"}

            # All harness endpoints should be 403 for non-admin
            assert (
                client.get(
                    "/admin/harness/runtime/status", headers=reader_headers
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/admin/harness/policies", headers=reader_headers
                ).status_code
                == 403
            )
            assert (
                client.get("/admin/harness/tasks", headers=reader_headers).status_code
                == 403
            )
            assert (
                client.get(
                    "/admin/harness/approvals", headers=reader_headers
                ).status_code
                == 403
            )
            assert (
                client.get("/admin/harness/audit", headers=reader_headers).status_code
                == 403
            )
            assert (
                client.post(
                    "/admin/harness/skills/install/_system/archive",
                    headers=reader_headers,
                    json={"request_ids": ["skill-1"], "archived": True},
                ).status_code
                == 403
            )

            # Unauthenticated → 401
            assert client.get("/admin/harness/runtime/status").status_code == 401

    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness-auth.sqlite3")


def test_harness_delete_nonexistent_policy_returns_404(tmp_path: Path):
    """Removing a non-existent policy rule returns 404."""
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "harness-404.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            resp = client.delete("/admin/harness/policies/nonexistent", headers=headers)
            assert resp.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness-404.sqlite3")


def test_harness_resolve_nonexistent_approval_returns_404(tmp_path: Path):
    """Resolving a non-existent approval request returns 404."""
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "harness-apr-404.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            resp = client.post(
                "/admin/harness/approvals/nonexistent/resolve",
                headers=headers,
                json={"approved": True},
            )
            assert resp.status_code == 404
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness-apr-404.sqlite3")


def test_evaluate_tool_call_allow_and_deny(tmp_path: Path):
    """POST /admin/harness/tools/evaluate returns correct decisions."""
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "harness-eval.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {settings.pi_harness_gateway_token}"}
            # ALLOW — normal bash command
            resp = client.post(
                "/admin/harness/tools/evaluate",
                headers=headers,
                json={"tool_name": "bash", "arguments": {"command": "echo hello"}},
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["is_denied"] is False
            assert result["approval_required"] is False

            # DENY — dangerous command
            resp = client.post(
                "/admin/harness/tools/evaluate",
                headers=headers,
                json={"tool_name": "bash", "arguments": {"command": "rm -rf /"}},
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["is_denied"] is True

            # ASK — git push
            resp = client.post(
                "/admin/harness/tools/evaluate",
                headers=headers,
                json={
                    "tool_name": "bash",
                    "arguments": {"command": "git push origin main"},
                },
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["approval_required"] is True
            assert result["approval_request_id"]  # non-empty

            public_page = client.post(
                "/admin/harness/tools/evaluate",
                headers=headers,
                json={
                    "tool_name": "http_request",
                    "arguments": {"method": "GET", "url": "https://skills.sh/"},
                },
            )
            assert public_page.status_code == 200
            assert public_page.json()["is_denied"] is False
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness-eval.sqlite3")


def test_network_request_allows_public_get_for_web_read_task(
    tmp_path: Path, monkeypatch
):
    """A chat-style task may fetch a public page through the broker."""
    original_path, original_url = settings.db_path, settings.database_url
    original_gateway_token = settings.pi_harness_gateway_token
    settings.db_path = tmp_path / "harness-network.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.pi_harness_gateway_token = "harness-network-secret"

    def public_read_response(
        self, method, url, *, allowlist, allow_public_read, headers, body
    ):
        assert method == "GET"
        assert url == "https://www.zhipuai.cn/"
        assert allowlist == []
        assert allow_public_read is True
        assert headers == {}
        assert body is None
        return NetworkResponse(status=200, headers={}, body=b"page")

    monkeypatch.setattr(
        harness_api.NetworkBroker,
        "request",
        public_read_response,
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/admin/harness/tasks",
                headers=admin_headers,
                json={
                    "task_id": "TASK-PUBLIC-WEB-001",
                    "tenant_id": "tenant-web",
                    "user_id": "user-web",
                    "objective": "read public web pages",
                    "permissions": {"network": True, "public_web_read": True},
                },
            )
            assert created.status_code == 201

            task_token = issue_task_gateway_token(
                settings.pi_harness_gateway_token,
                "TASK-PUBLIC-WEB-001",
                "tenant-web",
                "user-web",
            )
            response = client.post(
                "/admin/harness/network/request",
                headers={"Authorization": f"Bearer {task_token}"},
                json={
                    "method": "GET",
                    "url": "https://www.zhipuai.cn/",
                    "task_id": "TASK-PUBLIC-WEB-001",
                    "tenant_id": "tenant-web",
                    "user_id": "user-web",
                },
            )

            assert response.status_code == 200
            assert response.json() == {
                "status": 200,
                "headers": {},
                "body": "page",
                "body_format": "text",
                "truncated": False,
                "original_body_bytes": 4,
            }
    finally:
        settings.pi_harness_gateway_token = original_gateway_token
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "harness-network.sqlite3")


def test_skill_upload_capability_uses_task_workspace(tmp_path: Path, monkeypatch):
    """Broker-client upload delegates storage and cannot escape the task root."""
    original_path, original_url = settings.db_path, settings.database_url
    original_gateway_token = settings.pi_harness_gateway_token
    settings.db_path = tmp_path / "harness-upload.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.pi_harness_gateway_token = "test-gateway-token"
    monkeypatch.setattr(
        "app.services.harness.file_storage.minio_storage.is_minio_enabled",
        lambda: False,
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            task = client.post(
                "/admin/harness/tasks",
                headers=admin_headers,
                json={
                    "task_id": "TASK-UPLOAD-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "objective": "upload one generated file",
                    "permissions": {"workspace_file_upload": True},
                },
            ).json()["workspace"]["path"]
            workspace = Path(task)
            generated = workspace / "report.txt"
            generated.write_bytes(b"not-a-docx")
            gateway_headers = {
                "Authorization": f"Bearer {settings.pi_harness_gateway_token}"
            }

            response = client.post(
                "/admin/harness/files/upload",
                headers=gateway_headers,
                json={
                    "task_id": "TASK-UPLOAD-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "path": "report.txt",
                },
            )
            assert response.status_code == 200
            assert response.json()["file"] == {
                "name": "report.txt",
                "path": "report.txt",
                "size": len(b"not-a-docx"),
                "url": "/api/files/TASK-UPLOAD-001/report.txt",
            }

            escaped = client.post(
                "/admin/harness/files/upload",
                headers=gateway_headers,
                json={
                    "task_id": "TASK-UPLOAD-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "path": "../outside.txt",
                },
            )
            assert escaped.status_code == 403
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_harness_gateway_token = original_gateway_token
        database.reset_database(tmp_path / "harness-upload.sqlite3")


def test_skill_catalog_search_uses_server_catalog(tmp_path: Path, monkeypatch):
    original_path, original_url = settings.db_path, settings.database_url
    original_gateway_token = settings.pi_harness_gateway_token
    settings.db_path = tmp_path / "harness-catalog.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.pi_harness_gateway_token = "test-gateway-token"
    monkeypatch.setattr(
        "app.api.harness.search_skill_catalog",
        lambda query, limit=8: {
            "query": query,
            "items": [
                {
                    "name": "flight-status",
                    "skill_id": "flight-status",
                    "source": "example/skills",
                    "installs": 10,
                    "git_url": "https://github.com/example/skills.git",
                    "subpath": "flight-status",
                }
            ],
            "install_hint": "install via harness",
        },
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            client.post(
                "/admin/harness/tasks",
                headers=admin_headers,
                json={
                    "task_id": "TASK-CATALOG-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "objective": "search catalog",
                },
            )
            scoped_token = issue_task_gateway_token(
                settings.pi_harness_gateway_token,
                "TASK-CATALOG-001",
                "_system",
                "doc-nexus-pi",
            )
            response = client.post(
                "/admin/harness/skills/catalog/search",
                headers={"Authorization": f"Bearer {scoped_token}"},
                json={
                    "query": "flight status",
                    "task_id": "TASK-CATALOG-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["query"] == "flight status"
            assert body["items"][0]["subpath"] == "flight-status"
            wrong_task_token = issue_task_gateway_token(
                settings.pi_harness_gateway_token,
                "TASK-CATALOG-OTHER",
                "_system",
                "doc-nexus-pi",
            )
            wrong_task = client.post(
                "/admin/harness/skills/catalog/search",
                headers={"Authorization": f"Bearer {wrong_task_token}"},
                json={
                    "query": "flight status",
                    "task_id": "TASK-CATALOG-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                },
            )
            assert wrong_task.status_code == 401
            mismatch = client.post(
                "/admin/harness/skills/catalog/search",
                headers={
                    "Authorization": f"Bearer {settings.pi_harness_gateway_token}"
                },
                json={
                    "query": "flight status",
                    "task_id": "TASK-CATALOG-001",
                    "tenant_id": "_system",
                    "user_id": "someone-else",
                },
            )
            assert mismatch.status_code == 403
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_harness_gateway_token = original_gateway_token
        database.reset_database(tmp_path / "harness-catalog.sqlite3")


def test_skill_catalog_install_request_is_async_and_quarantined(
    tmp_path: Path, monkeypatch
):
    original_path, original_url = settings.db_path, settings.database_url
    original_runtime_dir = settings.pi_runtime_dir
    original_gateway_token = settings.pi_harness_gateway_token
    original_linkmind_enabled = settings.linkmind_enabled
    settings.db_path = tmp_path / "harness-install-request.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.pi_runtime_dir = tmp_path / "pi-runtime"
    settings.pi_harness_gateway_token = "test-gateway-token"
    settings.linkmind_enabled = False

    def fake_catalog(query, limit=8):
        assert query == "weather"
        assert limit == 8
        return {
            "query": query,
            "items": [
                {
                    "skill_id": "weather",
                    "source": "openclaw/openclaw",
                    "subpath": "weather",
                    "git_url": "https://github.com/openclaw/openclaw.git",
                }
            ],
        }

    fetch_calls: list[str] = []

    def fake_fetch(url, ref, destination, *, subpath=None):
        fetch_calls.append(str(subpath or ""))
        destination.mkdir(parents=True)
        if subpath:
            return {"source_type": "git", "url": url, "ref": ref, "subpath": subpath}
        skill_root = destination / "tools" / "llm" / "weather"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Weather lookup\n---\n",
            encoding="utf-8",
        )
        return {"source_type": "git", "url": url, "ref": ref, "subpath": ""}

    monkeypatch.setattr("app.api.harness.search_skill_catalog", fake_catalog)
    monkeypatch.setattr("app.api.harness.fetch_git_archive", fake_fetch)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            denied_task = client.post(
                "/admin/harness/tasks",
                headers=admin_headers,
                json={
                    "task_id": "TASK-INSTALL-DENIED",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "objective": "request weather Skill",
                },
            )
            assert denied_task.status_code == 201
            denied_token = issue_task_gateway_token(
                settings.pi_harness_gateway_token,
                "TASK-INSTALL-DENIED",
                "_system",
                "doc-nexus-pi",
            )
            denied = client.post(
                "/admin/harness/skills/catalog/request-install",
                headers={"Authorization": f"Bearer {denied_token}"},
                json={
                    "skill_id": "weather",
                    "source": "openclaw/openclaw",
                    "subpath": "weather",
                    "task_id": "TASK-INSTALL-DENIED",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                },
            )
            assert denied.status_code == 403

            created = client.post(
                "/admin/harness/tasks",
                headers=admin_headers,
                json={
                    "task_id": "TASK-INSTALL-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "objective": "request weather Skill",
                    "permissions": {"skill_install_request": True},
                },
            )
            assert created.status_code == 201
            scoped_token = issue_task_gateway_token(
                settings.pi_harness_gateway_token,
                "TASK-INSTALL-001",
                "_system",
                "doc-nexus-pi",
            )
            response = client.post(
                "/admin/harness/skills/catalog/request-install",
                headers={"Authorization": f"Bearer {scoped_token}"},
                json={
                    "skill_id": "weather",
                    "source": "openclaw/openclaw",
                    "subpath": "weather",
                    "version": "HEAD",
                    "task_id": "TASK-INSTALL-001",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                },
            )
            assert response.status_code == 200
            assert response.json()["installation"]["state"] == "requested"

            listed = client.get(
                "/admin/harness/skills/install/_system", headers=admin_headers
            )
            assert listed.status_code == 200
            record = next(
                item
                for item in listed.json()["items"]
                if item["request_id"] == response.json()["installation"]["request_id"]
            )
            assert record["state"] == "awaiting_approval"
            assert record["skill_id"] == "weather"
            assert record["scan_report"]["source"]["git_url"].endswith("openclaw.git")
            assert fetch_calls == ["weather", ""]
            assert record["scan_report"]["source"]["subpath"] == "tools/llm/weather"
            assert record["scan_report"]["source"]["catalog_subpath"] == "weather"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_runtime_dir = original_runtime_dir
        settings.pi_harness_gateway_token = original_gateway_token
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(tmp_path / "harness-install-request.sqlite3")


def test_runtime_skill_candidates_show_and_quarantine_unmanifested_skill(
    tmp_path: Path,
):
    """Runtime Skills without manifests are visible and safely imported."""
    original_path, original_url = settings.db_path, settings.database_url
    original_runtime_dir = settings.pi_runtime_dir
    settings.db_path = tmp_path / "harness-runtime-skill.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    runtime_root = tmp_path / "pi-runtime"
    settings.pi_runtime_dir = runtime_root
    skill_dir = runtime_root / "skills" / "_system" / "doc-nexus-pi" / "dingtalk-bot"
    prompt_skill_dir = runtime_root / "skills" / "_system" / "doc-nexus-pi" / "pdf"
    (skill_dir / "scripts").mkdir(parents=True)
    prompt_skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: dingtalk-bot\ndescription: DingTalk integration\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "dingtalk_bot.py").write_text(
        "import requests\nrequests.get('https://oapi.dingtalk.com')\n",
        encoding="utf-8",
    )
    (prompt_skill_dir / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: Use the PDF document skill\n---\n# PDF\n",
        encoding="utf-8",
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}

            candidates = client.get("/admin/harness/skills/candidates", headers=headers)
            assert candidates.status_code == 200
            item = next(
                item
                for item in candidates.json()["items"]
                if item["relative_path"] == "dingtalk-bot"
            )
            assert item["manifest"] is None
            assert item["version"] == "latest"
            assert item["kind"] == "needs_adaptation"
            assert item["adaptation"]["runtime"] == "python"
            assert item["adaptation"]["requires_review"] is True

            admitted = client.post(
                "/admin/harness/skills/admit-existing",
                headers=headers,
                json={"relative_path": "dingtalk-bot"},
            )
            assert admitted.status_code == 200
            record = admitted.json()["installation"]
            assert record["state"] == "awaiting_approval"
            assert record["skill_id"] == "dingtalk-bot"
            assert record["version"] == "1.0.0"
            assert (
                "oapi.dingtalk.com"
                in record["scan_report"]["adaptation"]["network_calls"]
            )
            assert Path(record["quarantine_path"]).is_dir()
            assert (
                Path(record["quarantine_path"]) / "scripts" / "dingtalk_bot.py"
            ).is_file()

            request_id = record["request_id"]
            approved = client.post(
                f"/admin/harness/skills/install/_system/{request_id}/approve",
                headers=headers,
            )
            assert approved.status_code == 200
            approved_record = approved.json()["installation"]
            materialized_skill_md = (
                Path(approved_record["quarantine_path"]).parent
                / "materialized"
                / "SKILL.md"
            )
            approved_original = materialized_skill_md.read_text(encoding="utf-8")
            materialized_skill_md.write_text(
                "# changed after approval\n", encoding="utf-8"
            )
            approved_integrity = client.get(
                "/admin/harness/skills/install/_system", headers=headers
            )
            approved_view = next(
                item
                for item in approved_integrity.json()["items"]
                if item["request_id"] == request_id
            )
            assert approved_view["integrity_state"] == "tampered"
            tampered_candidates = client.get(
                "/admin/harness/skills/candidates", headers=headers
            ).json()["items"]
            assert (
                next(
                    item
                    for item in tampered_candidates
                    if item["relative_path"] == "dingtalk-bot"
                )["state"]
                == "invalid"
            )
            materialized_skill_md.write_text(approved_original, encoding="utf-8")
            installed = client.post(
                f"/admin/harness/skills/install/_system/{request_id}/install",
                headers=headers,
            )
            assert installed.status_code == 200
            mounted = client.post(
                f"/admin/harness/skills/install/_system/{request_id}/mount",
                headers=headers,
            )
            assert mounted.status_code == 200
            assert not (
                runtime_root
                / "skills"
                / "_system"
                / "doc-nexus-pi"
                / ".harness"
                / request_id
            ).exists()
            enabled = client.post(
                f"/admin/harness/skills/install/_system/{request_id}/enable",
                headers=headers,
            )
            assert enabled.status_code == 200
            assert enabled.json()["installation"]["state"] == "enabled"
            assert enabled.json()["installation"]["skill_id"] == "dingtalk-bot"
            assert len(enabled.json()["installation"]["approved_sha256"]) == 64
            assert "skill" not in enabled.json()

            installed_skill_md = (
                Path(enabled.json()["installation"]["installed_path"]) / "SKILL.md"
            )
            original_skill_md = installed_skill_md.read_text(encoding="utf-8")
            installed_skill_md.write_text("# tampered\n", encoding="utf-8")
            integrity = client.get(
                "/admin/harness/skills/install/_system", headers=headers
            )
            tampered_record = next(
                item
                for item in integrity.json()["items"]
                if item["request_id"] == request_id
            )
            assert tampered_record["integrity_state"] == "tampered"
            installed_skill_md.write_text(original_skill_md, encoding="utf-8")

            refreshed = client.get("/admin/harness/skills/candidates", headers=headers)
            assert refreshed.status_code == 200
            dingtalk_candidates = [
                item
                for item in refreshed.json()["items"]
                if item["skill_id"] == "dingtalk-bot"
            ]
            assert len(dingtalk_candidates) == 1
            assert dingtalk_candidates[0]["state"] == "approved"
            assert dingtalk_candidates[0]["kind"] == "native"

            revoked = client.post(
                "/admin/harness/skills/revoke",
                headers=headers,
                json={
                    "tenant_id": "_system",
                    "skill_id": "dingtalk-bot",
                    "version": "1.0.0",
                },
            )
            assert revoked.status_code == 200
            assert revoked.json()["installation"]["state"] == "revoked"

            prompt_admitted = client.post(
                "/admin/harness/skills/admit-existing",
                headers=headers,
                json={"relative_path": "pdf"},
            )
            assert prompt_admitted.status_code == 200
            assert (
                prompt_admitted.json()["installation"]["state"] == "awaiting_approval"
            )
            prompt_request_id = prompt_admitted.json()["installation"]["request_id"]
            prompt_enabled = client.post(
                f"/admin/harness/skills/install/_system/{prompt_request_id}/approve-enable",
                headers=headers,
            )
            assert prompt_enabled.status_code == 200
            prompt_payload = prompt_enabled.json()

            refreshed = client.get("/admin/harness/skills/candidates", headers=headers)
            pdf_candidates = [
                item for item in refreshed.json()["items"] if item["skill_id"] == "pdf"
            ]
            assert len(pdf_candidates) == 1
            assert pdf_candidates[0]["kind"] == "prompt_only"
            assert pdf_candidates[0]["state"] == "approved"
            assert pdf_candidates[0]["manifest"] is None
            assert prompt_payload is not None
            assert prompt_payload["installation"]["skill_id"] == "pdf"
            assert "prompt_skill" not in prompt_payload

            escaped = client.post(
                "/admin/harness/skills/admit-existing",
                headers=headers,
                json={"relative_path": "../dingtalk-bot"},
            )
            assert escaped.status_code == 422
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_runtime_dir = original_runtime_dir
        database.reset_database(tmp_path / "harness-runtime-skill.sqlite3")


def test_legacy_fixed_entrypoint_skill_execution_is_retired(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    original_gateway_token = settings.pi_harness_gateway_token
    settings.db_path = tmp_path / "retired-skill-execute.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.pi_harness_gateway_token = "test-gateway-token"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/harness/skills/execute",
                headers={"Authorization": "Bearer test-gateway-token"},
                json={
                    "skill_id": "demo",
                    "version": "1.0.0",
                    "input": {},
                    "task_id": "task-1",
                    "tenant_id": "_system",
                    "user_id": "doc-nexus-pi",
                    "workspace": str(tmp_path),
                },
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_harness_gateway_token = original_gateway_token
        database.reset_database(tmp_path / "retired-skill-execute-clean.sqlite3")

    assert response.status_code == 410


def test_harness_approval_expiry_handles_sqlite_and_postgres_timestamps():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.repositories.harness import _is_expired

    recent_aware = SimpleNamespace(
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        ttl_seconds=600.0,
    )
    recent_naive = SimpleNamespace(
        status="pending",
        created_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).replace(
            tzinfo=None
        ),
        ttl_seconds=600.0,
    )
    stale_aware = SimpleNamespace(
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=900),
        ttl_seconds=600.0,
    )

    assert _is_expired(recent_aware) is False
    assert _is_expired(recent_naive) is False
    assert _is_expired(stale_aware) is True


def _seed_install_state(runtime_root: Path, tenant: str, records: dict) -> Path:
    state_dir = runtime_root / "skill-installations" / tenant
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "install-state.json"
    state_path.write_text(json.dumps(records), encoding="utf-8")
    return state_dir


def test_skill_install_management_list_is_newest_first_and_keeps_archived(tmp_path):
    """管理列表按提交时间排序，不影响内部顺序。"""
    original_path, original_url = settings.db_path, settings.database_url
    original_runtime_dir = settings.pi_runtime_dir
    settings.db_path = tmp_path / "harness-install-order.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    runtime_root = tmp_path / "pi-runtime"
    settings.pi_runtime_dir = runtime_root

    def record(request_id, state, **extra):
        return {
            "request_id": request_id,
            "tenant_id": "_system",
            "source_name": request_id,
            "state": state,
            "quarantine_path": f"/tmp/{request_id}",
            **extra,
        }

    state_dir = _seed_install_state(
        runtime_root,
        "_system",
        {
            "skill-mid": record(
                "skill-mid",
                "revoked",
                created_at="2026-01-15T00:00:00+00:00",
                archived_at="2026-01-20T00:00:00+00:00",
            ),
            "skill-old": record(
                "skill-old", "failed", created_at="2026-01-01T00:00:00Z"
            ),
            "skill-new": record(
                "skill-new", "failed", created_at="2026-02-01T00:00:00+00:00"
            ),
            "skill-legacy": record("skill-legacy", "failed"),
        },
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            listed = client.get(
                "/admin/harness/skills/install/_system", headers=headers
            )
            assert listed.status_code == 200
            items = listed.json()["items"]
            assert [item["request_id"] for item in items] == [
                "skill-new",
                "skill-mid",
                "skill-old",
                "skill-legacy",
            ]
            archived = next(
                item for item in items if item["request_id"] == "skill-mid"
            )
            assert archived["archived_at"] == "2026-01-20T00:00:00+00:00"
            # 读取历史记录时不补造生命周期时间。
            legacy = next(
                item for item in items if item["request_id"] == "skill-legacy"
            )
            assert legacy["created_at"] is None
            assert legacy["state_changed_at"] is None
            assert legacy["integrity_state"] == "not_applicable"
            assert legacy["integrity_message"] is None
            # 未保存批准摘要的撤销记录说明无法校验的原因。
            assert archived["integrity_state"] == "unverified"
            assert archived["integrity_message"]

        internal = [r.request_id for r in harness_api.SkillInstaller(state_dir).list()]
        assert internal == sorted(internal)
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_runtime_dir = original_runtime_dir
        database.reset_database(tmp_path / "harness-install-order.sqlite3")


def test_skill_install_archive_endpoint_guards_state_tenant_and_partial(tmp_path):
    original_path, original_url = settings.db_path, settings.database_url
    original_runtime_dir = settings.pi_runtime_dir
    settings.db_path = tmp_path / "harness-install-archive.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    runtime_root = tmp_path / "pi-runtime"
    settings.pi_runtime_dir = runtime_root

    def record(request_id, tenant, state):
        return {
            "request_id": request_id,
            "tenant_id": tenant,
            "source_name": request_id,
            "state": state,
            "quarantine_path": f"/tmp/{request_id}",
        }

    _seed_install_state(
        runtime_root,
        "_system",
        {
            "skill-revoked": record("skill-revoked", "_system", "revoked"),
            "skill-pending": record("skill-pending", "_system", "awaiting_approval"),
        },
    )
    _seed_install_state(
        runtime_root,
        "other-tenant",
        {"skill-foreign": record("skill-foreign", "other-tenant", "revoked")},
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            archive_url = "/admin/harness/skills/install/_system/archive"

            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={"request_ids": [], "archived": True},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={"request_ids": ["skill-x"] * 101, "archived": True},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={"request_ids": [""], "archived": True},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={"request_ids": ["skill-missing"], "archived": True},
                ).status_code
                == 404
            )
            # 当前租户不可操作另一个租户的记录。
            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={
                        "request_ids": ["skill-revoked", "skill-foreign"],
                        "archived": True,
                    },
                ).status_code
                == 404
            )
            # 整批校验通过前不写入任何记录。
            assert (
                client.post(
                    archive_url,
                    headers=headers,
                    json={
                        "request_ids": ["skill-revoked", "skill-pending"],
                        "archived": True,
                    },
                ).status_code
                == 409
            )
            listed = client.get(
                "/admin/harness/skills/install/_system", headers=headers
            ).json()["items"]
            assert (
                next(i for i in listed if i["request_id"] == "skill-revoked")[
                    "archived_at"
                ]
                is None
            )

            archived = client.post(
                archive_url,
                headers=headers,
                json={"request_ids": ["skill-revoked", "skill-revoked"], "archived": True},
            )
            assert archived.status_code == 200
            assert [i["request_id"] for i in archived.json()["items"]] == [
                "skill-revoked"
            ]
            assert archived.json()["items"][0]["archived_at"]

            # 幂等重试无新副作用，不返回重复变更。
            repeat = client.post(
                archive_url,
                headers=headers,
                json={"request_ids": ["skill-revoked"], "archived": True},
            )
            assert repeat.status_code == 200
            assert repeat.json()["items"] == []

            restored = client.post(
                archive_url,
                headers=headers,
                json={"request_ids": ["skill-revoked"], "archived": False},
            )
            assert restored.status_code == 200
            assert restored.json()["items"][0]["archived_at"] is None

            foreign = client.get(
                "/admin/harness/skills/install/other-tenant", headers=headers
            ).json()["items"]
            assert (
                next(i for i in foreign if i["request_id"] == "skill-foreign")[
                    "archived_at"
                ]
                is None
            )
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        settings.pi_runtime_dir = original_runtime_dir
        database.reset_database(tmp_path / "harness-install-archive.sqlite3")
