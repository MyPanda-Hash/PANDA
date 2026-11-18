from __future__ import annotations

import pytest

from app.services.harness.network import (
    MODEL_RESPONSE_BODY_MAX_BYTES,
    NetworkBroker,
    NetworkResponse,
    NetworkSecurityError,
)


def test_network_broker_rejects_private_ip():
    broker = NetworkBroker()
    with pytest.raises(NetworkSecurityError):
        broker.request("GET", "http://127.0.0.1/health", allowlist=["127.0.0.1"])


def test_network_broker_rejects_non_allowlisted_host():
    broker = NetworkBroker()
    with pytest.raises(NetworkSecurityError, match="allowlist"):
        broker.request("GET", "https://example.com/", allowlist=["api.example.com"])


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_network_broker_allows_public_read_without_business_allowlist(monkeypatch, method):
    broker = NetworkBroker()
    monkeypatch.setattr(
        NetworkBroker,
        "_request_once",
        lambda self, *args, **kwargs: {"status": 200, "headers": {}, "body": b"ok"},
    )

    response = broker.request(
        method,
        "https://93.184.216.34/",
        allowlist=[],
        allow_public_read=True,
    )

    assert response.status == 200
    assert response.body == b"ok"


def test_network_broker_keeps_non_read_methods_on_business_allowlist():
    broker = NetworkBroker()

    with pytest.raises(NetworkSecurityError, match="allowlist"):
        broker.request(
            "POST",
            "https://www.zhipuai.cn/",
            allowlist=[],
            allow_public_read=True,
        )


def test_network_broker_rejects_request_body_for_public_read(monkeypatch):
    broker = NetworkBroker()
    monkeypatch.setattr(
        NetworkBroker,
        "_request_once",
        lambda self, *args, **kwargs: {"status": 200, "headers": {}, "body": b"ok"},
    )

    with pytest.raises(NetworkSecurityError, match="request body"):
        broker.request(
            "GET",
            "https://www.zhipuai.cn/",
            allowlist=[],
            allow_public_read=True,
            body="unexpected",
        )


def test_network_broker_rejects_credentials_and_unsupported_scheme():
    broker = NetworkBroker()
    with pytest.raises(NetworkSecurityError):
        broker.request("GET", "file:///etc/passwd", allowlist=["example.com"])
    with pytest.raises(NetworkSecurityError):
        broker.request("GET", "https://user:pass@example.com/", allowlist=["example.com"])


def test_network_broker_revalidates_redirect(monkeypatch):
    broker = NetworkBroker()
    calls = iter([
        {"status": 302, "headers": {"Location": "http://127.0.0.1/private"}, "body": b""},
    ])
    monkeypatch.setattr(
        NetworkBroker,
        "_request_once",
        lambda self, *args, **kwargs: next(calls),
    )
    with pytest.raises(NetworkSecurityError):
        broker.request(
            "GET",
            "https://93.184.216.34/",
            allowlist=[],
            allow_public_read=True,
        )


def test_network_broker_limits_body(monkeypatch):
    broker = NetworkBroker(max_response_bytes=3)
    monkeypatch.setattr(
        NetworkBroker,
        "_request_once",
        lambda self, *args, **kwargs: {"status": 200, "headers": {}, "body": b"abcd"},
    )
    with pytest.raises(NetworkSecurityError, match="response"):
        broker.request("GET", "https://93.184.216.34/", allowlist=["93.184.216.34"])


def test_model_response_extracts_and_bounds_large_html():
    article = "<h1>BBC 中文标题</h1><p>这是需要保留的报道正文。</p>"
    html = (
        "<!doctype html><html><head>"
        f"<style>{'x' * 260_000}</style>"
        f"<script>{'y' * 266_960}</script>"
        f"</head><body>{article}</body></html>"
    ).encode()
    response = NetworkResponse(
        status=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(html)),
            "Server": "not-needed-by-the-model",
        },
        body=html,
    )

    result = response.as_model_dict()

    assert result == {
        "status": 200,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(html)),
        },
        "body": "BBC 中文标题\n这是需要保留的报道正文。",
        "body_format": "html_text",
        "truncated": True,
        "original_body_bytes": len(html),
    }


def test_model_response_truncates_on_utf8_boundary():
    prefix = "a" * (MODEL_RESPONSE_BODY_MAX_BYTES - 1)
    body = (prefix + "中文").encode()
    result = NetworkResponse(
        status=200,
        headers={"Content-Type": "text/plain; charset=utf-8"},
        body=body,
    ).as_model_dict()

    assert result["body"] == prefix
    assert len(result["body"].encode()) <= MODEL_RESPONSE_BODY_MAX_BYTES
    assert "\ufffd" not in result["body"]
    assert result["truncated"] is True
    assert result["original_body_bytes"] == len(body)


def test_model_response_preserves_small_json_and_raw_response_contract():
    body = '{"city":"北京","temperature":23}'.encode()
    response = NetworkResponse(
        status=200,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "ETag": '"weather-v1"',
            "Server": "not-needed-by-the-model",
        },
        body=body,
    )

    assert response.as_model_dict() == {
        "status": 200,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "ETag": '"weather-v1"',
        },
        "body": body.decode(),
        "body_format": "text",
        "truncated": False,
        "original_body_bytes": len(body),
    }
    assert response.as_dict() == {
        "status": 200,
        "headers": response.headers,
        "body": body.decode(),
    }
