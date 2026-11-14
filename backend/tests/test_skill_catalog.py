from __future__ import annotations

import json

import pytest

from app.services.harness.network import NetworkResponse, NetworkSecurityError
from app.services.harness.skill_catalog import (
    INSTALL_HINT,
    SkillCatalogError,
    _CACHE,
    catalog_unavailable,
    search_skill_catalog,
)


class _FakeBroker:
    def __init__(self, response: NetworkResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def request(self, method, url, *, allowlist, headers=None, body=None):  # noqa: ANN001
        self.calls.append((method, url, tuple(allowlist)))
        if self.error:
            raise self.error
        return self.response


def setup_function() -> None:
    _CACHE.clear()


def test_search_skill_catalog_parses_official_payload():
    payload = {
        "skills": [
            {
                "id": "vercel-labs/agent-skills/flight-status",
                "skillId": "flight-status",
                "name": "flight-status",
                "installs": 1200,
                "source": "vercel-labs/agent-skills",
            },
            {"id": "bad", "name": "", "source": "nope"},
        ]
    }
    broker = _FakeBroker(
        NetworkResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
        )
    )

    result = search_skill_catalog("flight status", limit=8, broker=broker)

    assert result["query"] == "flight status"
    assert result["install_hint"] == INSTALL_HINT
    assert result["items"] == [
        {
            "name": "flight-status",
            "skill_id": "flight-status",
            "source": "vercel-labs/agent-skills",
            "installs": 1200,
            "catalog_url": "https://skills.sh/vercel-labs/agent-skills/flight-status",
            "git_url": "https://github.com/vercel-labs/agent-skills.git",
            "subpath": "flight-status",
        }
    ]
    assert broker.calls[0][0] == "GET"
    assert broker.calls[0][1].startswith("https://skills.sh/api/search?")
    assert broker.calls[0][2] == ("skills.sh",)


def test_search_skill_catalog_rejects_non_https_override():
    with pytest.raises(SkillCatalogError, match="https"):
        search_skill_catalog("weather", base_url="http://skills.sh")


def test_search_skill_catalog_rejects_empty_query():
    with pytest.raises(SkillCatalogError, match="empty"):
        search_skill_catalog("   ")


def test_search_skill_catalog_surfaces_ssrf_guard():
    broker = _FakeBroker(error=NetworkSecurityError("URL resolves to a private or reserved address"))
    with pytest.raises(SkillCatalogError, match="private"):
        search_skill_catalog("flight", broker=broker, base_url="https://skills.sh")


def test_catalog_unavailable_keeps_install_hint():
    result = catalog_unavailable("  flight  ", "down")
    assert result["items"] == []
    assert result["query"] == "flight"
    assert result["error"] == "down"
    assert "Harness" in result["install_hint"]
