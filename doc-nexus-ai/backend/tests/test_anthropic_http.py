"""Contract tests for direct Anthropic Messages HTTP helpers."""

from __future__ import annotations

from app.services.anthropic_http import (
    ANTHROPIC_VERSION_HEADER,
    anthropic_headers,
    anthropic_messages_url,
)


def test_messages_url_from_root() -> None:
    assert (
        anthropic_messages_url("https://api.anthropic.com")
        == "https://api.anthropic.com/v1/messages"
    )


def test_messages_url_strips_trailing_v1() -> None:
    assert (
        anthropic_messages_url("https://proxy.example.test/v1")
        == "https://proxy.example.test/v1/messages"
    )
    assert (
        anthropic_messages_url("https://proxy.example.test/v1/")
        == "https://proxy.example.test/v1/messages"
    )


def test_messages_url_strips_full_messages_suffix() -> None:
    assert (
        anthropic_messages_url("https://proxy.example.test/v1/messages")
        == "https://proxy.example.test/v1/messages"
    )


def test_official_host_uses_x_api_key_and_version() -> None:
    headers = anthropic_headers("sk-ant-secret", "https://api.anthropic.com")
    assert headers["x-api-key"] == "sk-ant-secret"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION_HEADER
    assert "Authorization" not in headers


def test_proxy_host_uses_bearer_plus_version() -> None:
    headers = anthropic_headers("proxy-secret", "https://proxy.example.test/v1")
    assert headers["Authorization"] == "Bearer proxy-secret"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION_HEADER
    assert "x-api-key" not in headers
