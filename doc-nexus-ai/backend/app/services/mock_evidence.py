from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.config import settings

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "writing_evidence"
    / "mock_evidence_v1.json"
)


class MockEvidenceConfigurationError(RuntimeError):
    pass


def search(*, chapter_id: str, query: str, purpose: str) -> dict[str, Any]:
    if settings.writing_evidence_provider != "mock":
        raise MockEvidenceConfigurationError("real retrieval provider is not implemented")
    if settings.environment.lower() in {"production", "prod"}:
        raise MockEvidenceConfigurationError("mock evidence is disabled in production")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    mode = "partial" if "missing" in query.lower() else "default"
    evidence = fixture[mode]
    canonical = json.dumps(
        {
            "chapter_id": chapter_id,
            "query": query,
            "purpose": purpose,
            "evidence": evidence,
            "provider": "mock",
            "fixture_version": fixture["fixture_version"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "result_ref": f"mep_{digest[:20]}",
        "evidence": evidence,
        "knowledge_gaps": ["mock fixture has partial coverage"] if mode == "partial" else [],
        "provider": "mock",
        "fixture_version": fixture["fixture_version"],
        "sha256": digest,
    }
