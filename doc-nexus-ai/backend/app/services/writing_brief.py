"""Structured writing brief helpers (immutable revisioned project requirements)."""

from __future__ import annotations

import json
from typing import Any


BRIEF_FIELDS = (
    "audience",
    "document_type",
    "purpose",
    "length_hint",
    "tone",
    "must_cover",
    "forbidden",
    "fact_scope",
    "citation_style",
    "notes",
    "preferred_model",
)


def empty_brief() -> dict[str, Any]:
    return {
        "audience": "",
        "document_type": "",
        "purpose": "",
        "length_hint": "",
        "tone": "",
        "must_cover": [],
        "forbidden": [],
        "fact_scope": "",
        "citation_style": "",
        "notes": "",
        "preferred_model": "",
    }


def normalize_brief(value: Any) -> dict[str, Any]:
    base = empty_brief()
    if not isinstance(value, dict):
        return base
    for key in (
        "audience",
        "document_type",
        "purpose",
        "length_hint",
        "tone",
        "fact_scope",
        "citation_style",
        "notes",
    ):
        raw = value.get(key)
        if isinstance(raw, str):
            base[key] = raw.strip()[:4000]
    model = value.get("preferred_model")
    if isinstance(model, str):
        base["preferred_model"] = model.strip()[:255]
    for key in ("must_cover", "forbidden"):
        raw = value.get(key)
        if isinstance(raw, list):
            base[key] = [
                str(item).strip()[:500]
                for item in raw[:50]
                if str(item).strip()
            ]
        elif isinstance(raw, str) and raw.strip():
            base[key] = [part.strip()[:500] for part in raw.split("\n") if part.strip()][:50]
    return base


def encode_brief(brief: dict[str, Any]) -> str:
    return json.dumps(normalize_brief(brief), ensure_ascii=False, sort_keys=True)


def decode_brief(raw: str | None) -> dict[str, Any]:
    if not raw:
        return empty_brief()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return empty_brief()
    return normalize_brief(value)


def brief_query_text(brief: dict[str, Any], *, title: str = "") -> str:
    parts = [
        title.strip(),
        str(brief.get("purpose") or "").strip(),
        str(brief.get("audience") or "").strip(),
        str(brief.get("document_type") or "").strip(),
        str(brief.get("tone") or "").strip(),
        str(brief.get("fact_scope") or "").strip(),
        str(brief.get("notes") or "").strip(),
    ]
    must = brief.get("must_cover") if isinstance(brief.get("must_cover"), list) else []
    parts.extend(str(item).strip() for item in must if str(item).strip())
    return " ".join(part for part in parts if part)


def brief_for_prompt(brief: dict[str, Any], *, revision: int) -> dict[str, Any]:
    payload = normalize_brief(brief)
    payload.pop("preferred_model", None)
    payload["revision"] = max(0, int(revision))
    return payload
