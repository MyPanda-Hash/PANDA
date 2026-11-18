"""Bounded JSON envelopes and citation credentials for access requests.

Every JSON column in this feature uses one shape — ``{"items": [...],
"truncated": bool}`` — so a truncation flag has somewhere to live and readers
have exactly one parse path (a bare array would leave ``truncated`` homeless).

``NULL`` is reserved for the frozen publication column: it means "never
recorded / unknown", which is deliberately distinct from a recorded-but-empty
set (``{"items": [], "truncated": false}``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

MAX_CITATION_REFS = 50
MAX_SOURCE_REFS = 20
MAX_OPEN_LOCATORS = 50

#: ``target_type`` values this feature understands. Adding one requires
#: registering both its approver resolution and its grant action.
TARGET_SPACE = "space"
TARGET_WRITING_PROJECT = "writing_project"
SUPPORTED_TARGET_TYPES = frozenset({TARGET_SPACE, TARGET_WRITING_PROJECT})

#: Grant action per target type — the role written when a request is approved.
GRANTED_ROLES = {TARGET_SPACE: "reader", TARGET_WRITING_PROJECT: "viewer"}


def _identity(item: Any) -> Any:
    """Stable de-duplication marker for any JSON-ish item.

    Structured entries (locators, source refs) are dicts, which are not
    hashable, so the default marker serializes them instead of raising.
    """
    try:
        hash(item)
    except TypeError:
        return json.dumps(item, sort_keys=True, ensure_ascii=False)
    return item


def dump_bounded_items(
    items: Iterable[Any],
    limit: int,
    *,
    key: Callable[[Any], Any] | None = None,
) -> str:
    """Serialize ``items`` as an envelope, de-duplicated and capped at ``limit``."""
    key_fn = key or _identity
    unique: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        marker = key_fn(item)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return json.dumps(
        {"items": unique[:limit], "truncated": len(unique) > limit},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_bounded_items(raw: str | None) -> list[Any]:
    """Return the envelope's items; ``NULL`` and malformed values yield ``[]``."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    items = parsed.get("items")
    return list(items) if isinstance(items, list) else []


def envelope_was_recorded(raw: str | None) -> bool:
    """True when a value was written, even if its item list is empty.

    Callers use this to tell "never recorded (NULL, unknown)" apart from
    "recorded and confirmed empty" — both yield no targets, but only the first
    is an absence of information.
    """
    return raw is not None
