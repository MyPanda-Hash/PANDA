"""Page-role vocabulary shared by ordinary DeckSpec and chat sidecar checks."""

from __future__ import annotations

ALLOWED_ROLES = frozenset({
    "cover",
    "section",
    "problem",
    "objective",
    "solution",
    "architecture",
    "process",
    "timeline",
    "metric",
    "comparison",
    "case",
    "risk",
    "acceptance",
    "closing",
})

# The sidecar's generic page: ordinary content, needs at least one point.
DEFAULT_ROLE = "content"
