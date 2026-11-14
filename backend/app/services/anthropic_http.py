"""Shared Anthropic Messages API HTTP helpers for direct (non-Pi) calls.

The Pi sandbox talks to Anthropic through the bundled SDK, but QA and
vision fallback call the upstream endpoint directly with httpx and must
reproduce the wire format: x-api-key + anthropic-version for the official
service, plain Bearer for compatible proxies.
"""

from __future__ import annotations

from urllib.parse import urlsplit

ANTHROPIC_OFFICIAL_HOSTS = ("api.anthropic.com",)
ANTHROPIC_VERSION_HEADER = "2023-06-01"


def anthropic_messages_url(base_url: str) -> str:
    """Return the Messages endpoint for a base URL.

    The official Anthropic SDK treats its baseURL as the root and appends
    ``/v1/messages``. Strip a trailing ``/v1`` first so a proxy configured
    as ``https://proxy/v1`` does not become ``/v1/v1/messages``.
    """
    raw = (base_url or "").strip().split("?")[0].rstrip("/")
    if not raw:
        return raw
    if raw.endswith("/v1/messages"):
        raw = raw[: -len("/v1/messages")]
    elif raw.endswith("/v1"):
        raw = raw[: -len("/v1")]
    raw = raw.rstrip("/")
    return f"{raw}/v1/messages"


def anthropic_headers(secret: str, base_url: str) -> dict[str, str]:
    """Headers for a direct Anthropic Messages call.

    Official api.anthropic.com requires x-api-key plus anthropic-version.
    Compatible proxies usually accept Bearer; sending anthropic-version is
    harmless there and keeps one code path.
    """
    host = (urlsplit((base_url or "").split("?")[0]).hostname or "").lower()
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION_HEADER,
    }
    if host in ANTHROPIC_OFFICIAL_HOSTS:
        headers["x-api-key"] = secret
    else:
        headers["Authorization"] = f"Bearer {secret}"
    return headers
