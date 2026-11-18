"""Server-side official Skill catalog search.

Pi sessions must not browse skills.sh or GitHub via ``http_request``.
This module fetches the public catalog API from the backend process with a
dedicated host allowlist and SSRF guard. Results are discovery metadata only;
installation still goes through the Harness Git/archive pipeline.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

from app.services.harness.network import NetworkBroker, NetworkResponse, NetworkSecurityError

DEFAULT_CATALOG_BASE = "https://skills.sh"
MAX_QUERY_CHARS = 128
MAX_RESULTS = 8
_CACHE_TTL_SECONDS = 60.0
_SOURCE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._/-]{1,160}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

INSTALL_HINT = (
    "这些结果仅供发现。用户明确要求安装时，可调用 request_skill_install 提交到隔离区；"
    "安装不会自动批准或启用，管理员必须在 Harness 管理端查看扫描报告并批准启用。"
    "批准后，Skill 会在新会话中原生加载。"
)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class SkillCatalogError(ValueError):
    """Official catalog search failed a safety or protocol check."""


def _sanitize(value: object, *, max_len: int) -> str:
    text = _CONTROL.sub("", str(value or "")).strip()
    return text[:max_len]


def _normalize_query(query: str) -> str:
    normalized = " ".join(_sanitize(query, max_len=MAX_QUERY_CHARS).split())
    if not normalized:
        raise SkillCatalogError("search query is empty")
    return normalized


def _catalog_allowlist(base_url: str) -> tuple[str, ...]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https":
        raise SkillCatalogError("Skill catalog must use https://")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise SkillCatalogError("Skill catalog URL is missing a host")
    return (host,)


def _parse_item(raw: object, catalog_host: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _sanitize(raw.get("name") or raw.get("skillId"), max_len=128)
    skill_id = _sanitize(raw.get("skillId") or raw.get("id"), max_len=160)
    source = _sanitize(raw.get("source"), max_len=128)
    registry_id = _sanitize(raw.get("id"), max_len=160)
    if not name:
        return None
    try:
        installs = int(raw.get("installs") or 0)
    except (TypeError, ValueError):
        installs = 0
    item: dict[str, Any] = {
        "name": name,
        "skill_id": skill_id or name,
        "source": source,
        "installs": max(0, installs),
    }
    if registry_id and _SAFE_ID.match(registry_id):
        item["catalog_url"] = f"https://{catalog_host}/{registry_id}"
    if source and _SOURCE.match(source):
        item["git_url"] = f"https://github.com/{source}.git"
        subpath = skill_id if skill_id and "/" not in skill_id else registry_id.rsplit("/", 1)[-1]
        if subpath and _SAFE_ID.match(subpath) and "/" not in subpath:
            item["subpath"] = subpath
    return item


def _parse_response(payload: object, *, query: str, catalog_host: str, limit: int) -> dict[str, Any]:
    skills = payload.get("skills") if isinstance(payload, dict) else None
    items: list[dict[str, Any]] = []
    if isinstance(skills, list):
        for raw in skills:
            item = _parse_item(raw, catalog_host)
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                break
    return {
        "query": query,
        "items": items,
        "install_hint": INSTALL_HINT,
    }


def search_skill_catalog(
    query: str,
    *,
    limit: int = MAX_RESULTS,
    broker: NetworkBroker | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Search the official Skill catalog and return sanitized discovery rows."""
    from app.core.config import settings

    normalized = _normalize_query(query)
    capped = max(1, min(int(limit or MAX_RESULTS), MAX_RESULTS))
    catalog_base = (base_url or settings.pi_skill_catalog_base_url or DEFAULT_CATALOG_BASE).rstrip("/")
    allowlist = _catalog_allowlist(catalog_base)
    cache_key = f"{catalog_base}|{normalized}|{capped}"
    cached = _CACHE.get(cache_key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]

    url = f"{catalog_base}/api/search?q={quote(normalized)}&limit={capped}"
    client = broker or NetworkBroker(timeout_seconds=15, max_response_bytes=512_000)
    try:
        response = client.request("GET", url, allowlist=allowlist)
    except NetworkSecurityError as exc:
        raise SkillCatalogError(str(exc)) from exc
    if not isinstance(response, NetworkResponse) or response.status != 200:
        status = getattr(response, "status", "?")
        raise SkillCatalogError(f"catalog search failed (HTTP {status})")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillCatalogError("catalog search returned invalid JSON") from exc

    result = _parse_response(payload, query=normalized, catalog_host=allowlist[0], limit=capped)
    _CACHE[cache_key] = (now + _CACHE_TTL_SECONDS, result)
    return result


def catalog_unavailable(query: str, error: str) -> dict[str, Any]:
    """Fail closed to an empty discovery payload the model can report."""
    try:
        normalized = _normalize_query(query)
    except SkillCatalogError:
        normalized = ""
    return {
        "query": normalized,
        "items": [],
        "error": _sanitize(error, max_len=240),
        "install_hint": INSTALL_HINT,
    }
