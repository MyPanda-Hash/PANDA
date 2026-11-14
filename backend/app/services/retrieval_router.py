"""Low-cost retrieval routing without an extra LLM call.

The router is deliberately conservative: existing callers can keep the legacy
lexical+graph path, while capability and writing callers can opt into adaptive
routing. It only decides which retrieval branches are worth trying; it never
bypasses authorization or evidence validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class RetrievalMode(StrEnum):
    LEGACY = "legacy"
    AUTO = "auto"
    NONE = "none"
    EXACT = "exact"
    FACTUAL = "factual"
    RELATION = "relation"
    COMPLEX = "complex"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    mode: RetrievalMode
    use_lexical: bool = True
    use_graph: bool = False
    use_vector: bool = False
    vector_fallback: bool = False
    rich_graph_context: bool = False
    reason: str = ""


_ID_RE = re.compile(r"(?i)\b[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+\b")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")
_DATE_RE = re.compile(r"(?<!\d)\d{2,4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)")
_RELATION_TERMS = (
    "关系",
    "关联",
    "联系",
    "负责",
    "创始",
    "团队",
    "属于",
    "合作",
    "影响",
    "演进",
    "依赖",
    "对比",
    "比较",
    "之间",
    "related",
    "relationship",
    "who owns",
    "depends on",
)
_COMPLEX_TERMS = (
    "梳理",
    "分析",
    "分别",
    "为什么",
    "如何",
    "哪些资料",
    "发展历程",
    "知识地图",
    "research",
    "compare",
)
_PURE_CHAT_PATTERNS = (
    re.compile(r"^(你好|您好|嗨|hello|hi|hey)[!！。,.， ]*$", re.I),
    re.compile(r"^(谢谢|多谢|辛苦了|好的|明白了|收到)[!！。,.， ]*$", re.I),
    re.compile(r"^(哈哈+|呵呵+|嗯嗯+|ok|okay)[!！。,.， ]*$", re.I),
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def is_non_retrieval_request(query: str, *, purpose: str | None = None) -> bool:
    """Recognize safe no-retrieval cases; factual questions are never filtered here."""
    text = (query or "").strip()
    purpose_text = (purpose or "").strip().lower()
    if purpose_text in {"rewrite_style", "rewrite_text", "chat", "none"}:
        return True
    return bool(text and any(pattern.fullmatch(text) for pattern in _PURE_CHAT_PATTERNS))


def has_exact_signal(query: str) -> bool:
    text = query or ""
    return bool(_ID_RE.search(text) or _LONG_NUMBER_RE.search(text) or _DATE_RE.search(text))


def plan_retrieval(
    query: str,
    *,
    mode: str | RetrievalMode = RetrievalMode.AUTO,
    purpose: str | None = None,
) -> RetrievalPlan:
    """Return a conservative branch plan for a scoped retrieval request."""
    try:
        requested = RetrievalMode(mode)
    except ValueError:
        requested = RetrievalMode.AUTO

    if requested == RetrievalMode.LEGACY:
        return RetrievalPlan(
            mode=requested,
            use_lexical=True,
            use_graph=True,
            reason="legacy caller compatibility",
        )
    if requested == RetrievalMode.NONE or is_non_retrieval_request(query, purpose=purpose):
        return RetrievalPlan(mode=RetrievalMode.NONE, use_lexical=False, reason="no factual retrieval needed")
    if requested in {RetrievalMode.EXACT, RetrievalMode.FACTUAL, RetrievalMode.RELATION, RetrievalMode.COMPLEX}:
        selected = requested
    else:
        text = (query or "").strip()
        if not text:
            return RetrievalPlan(mode=RetrievalMode.NONE, use_lexical=False, reason="empty query")
        if has_exact_signal(text):
            selected = RetrievalMode.EXACT
        elif _contains_any(text, _COMPLEX_TERMS) and _contains_any(text, _RELATION_TERMS):
            selected = RetrievalMode.COMPLEX
        elif _contains_any(text, _RELATION_TERMS):
            selected = RetrievalMode.RELATION
        else:
            selected = RetrievalMode.FACTUAL

    if selected == RetrievalMode.EXACT:
        return RetrievalPlan(
            mode=selected,
            use_lexical=True,
            reason="identifier/date/exact-token query",
        )
    if selected == RetrievalMode.RELATION:
        return RetrievalPlan(
            mode=selected,
            use_lexical=True,
            use_graph=True,
            vector_fallback=True,
            reason="entity or relationship query",
        )
    if selected == RetrievalMode.COMPLEX:
        return RetrievalPlan(
            mode=selected,
            use_lexical=True,
            use_graph=True,
            use_vector=True,
            rich_graph_context=True,
            reason="multi-entity or analytical query",
        )
    return RetrievalPlan(
        mode=RetrievalMode.FACTUAL,
        use_lexical=True,
        use_vector=False,
        vector_fallback=True,
        reason="semantic factual query",
    )


def should_escalate_to_vector(
    lexical_scores: dict[str, float],
    *,
    minimum_score: float,
    minimum_margin: float,
) -> bool:
    """Escalate only when lexical evidence is absent or ambiguous."""
    positive = sorted((score for score in lexical_scores.values() if score > 0), reverse=True)
    if not positive:
        return True
    if positive[0] < minimum_score:
        return True
    return len(positive) > 1 and positive[0] - positive[1] < minimum_margin
