"""Resolve browser-reachable invite base URLs for LAN registration QR codes."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from app.core.config import settings

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def parse_frontend_origins(raw: str | None = None) -> list[str]:
    """Parse DOC_NEXUS_FRONTEND_ORIGIN (comma-separated) into unique origins."""
    source = raw if raw is not None else settings.frontend_origin
    origins: list[str] = []
    for part in source.split(","):
        origin = part.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    return origins or ["http://localhost:5173"]


def _host_of(origin: str) -> str | None:
    try:
        return (urlparse(origin).hostname or "").lower() or None
    except ValueError:
        return None


def is_loopback_origin(origin: str) -> bool:
    host = _host_of(origin)
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_lan_origin(origin: str) -> bool:
    """True for private / link-local IPs that phones on Wi-Fi can reach."""
    host = _host_of(origin)
    if not host or host in _LOOPBACK_HOSTS:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_link_local
        or getattr(address, "is_site_local", False)
    )


def prefer_invite_origin(origins: list[str] | None = None) -> str:
    """Prefer a LAN origin, then any non-loopback, then the first configured origin."""
    items = origins if origins is not None else parse_frontend_origins()
    for origin in items:
        if is_lan_origin(origin):
            return origin
    for origin in items:
        if not is_loopback_origin(origin):
            return origin
    return items[0]


def invite_origin_payload(raw: str | None = None) -> dict[str, object]:
    origins = parse_frontend_origins(raw)
    preferred = prefer_invite_origin(origins)
    lan_origins = [item for item in origins if is_lan_origin(item)]
    return {
        "origins": origins,
        "lan_origins": lan_origins,
        "preferred": preferred,
    }


def looks_like_origin(value: str) -> bool:
    return bool(re.match(r"^https?://[^/\s]+$", value.strip().rstrip("/")))
