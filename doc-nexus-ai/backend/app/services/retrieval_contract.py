"""Pure adapters for the shared retrieval and evidence contracts.

This module deliberately does not retrieve, authorize, or access the database.
It only turns already-authorized internal result mappings into bounded public
metadata.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.schemas.evidence import EvidenceLocationResponse, RetrievalTraceResponse


_TRACE_KEYS = {
    "retrieval_mode",
    "retrieval_method",
    "route_reason",
    "pool_size",
    "selected_count",
    "channels",
    "degraded",
    "degradation_reason",
}
_DEFAULT_CHANNELS = {"lexical": False, "vector": False, "graph": False}


def _safe_text(value: object, *, limit: int = 255) -> str:
    return str(value or "")[:limit]


def _grounding_status(
    *,
    mode: str,
    selected_count: int,
    citation_count: int,
    degraded: bool,
    support_states: Iterable[str] = (),
) -> str:
    if mode == "none":
        return "not_requested"
    if degraded:
        return "degraded"
    if selected_count <= 0 or citation_count <= 0:
        return "knowledge_gap"
    if citation_count < selected_count or any(
        state in {"uncertain", "unsupported"} for state in support_states
    ):
        return "partial"
    return "grounded"


def build_retrieval_trace(
    bundle: Any,
    *,
    citation_count: int = 0,
    support_states: Iterable[str] = (),
) -> RetrievalTraceResponse:
    """Build a safe trace from a RetrievalBundle-like object.

    Only allowlisted scalar metadata and boolean channel flags cross the
    boundary. Query text, prompts, embeddings, provider payloads and scores
    are intentionally omitted.
    """

    raw = getattr(bundle, "meta", None)
    meta = raw if isinstance(raw, dict) else {}
    mode = _safe_text(meta.get("retrieval_mode") or "legacy", limit=32)
    method = _safe_text(meta.get("retrieval_method") or "pool_fallback", limit=96)
    channels = dict(_DEFAULT_CHANNELS)
    raw_channels = meta.get("channels")
    if isinstance(raw_channels, dict):
        for name in channels:
            channels[name] = bool(raw_channels.get(name, False))
    selected_count = max(0, int(meta.get("selected_count") or len(getattr(bundle, "documents", []) or [])))
    pool_size = max(0, int(meta.get("pool_size") or 0))
    degraded = bool(meta.get("degraded", False))
    degradation_reason = (
        _safe_text(meta.get("degradation_reason"), limit=255) or None
        if degraded
        else None
    )
    return RetrievalTraceResponse(
        mode=mode,
        method=method,
        route_reason=_safe_text(meta.get("route_reason"), limit=255),
        pool_size=pool_size,
        selected_count=selected_count,
        channels=channels,
        degraded=degraded,
        degradation_reason=degradation_reason,
        citation_count=max(0, int(citation_count)),
        grounding_status=_grounding_status(
            mode=mode,
            selected_count=selected_count,
            citation_count=max(0, int(citation_count)),
            degraded=degraded,
            support_states=support_states,
        ),
    )


def build_evidence_location(value: dict[str, Any]) -> EvidenceLocationResponse:
    """Adapt a legacy evidence dict to the public location contract."""

    page_number = value.get("page_number")
    if page_number is not None:
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = None
    bbox = value.get("bbox")
    if bbox is None and all(key in value for key in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")):
        bbox = tuple(value[key] for key in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1"))
    if bbox is not None:
        try:
            bbox = tuple(float(item) for item in bbox)
        except (TypeError, ValueError):
            bbox = None
    location_label = _safe_text(
        value.get("location_label") or value.get("page_or_section") or "",
        limit=255,
    )
    return EvidenceLocationResponse(
        citation_id=str(value.get("citation_id") or value.get("id") or ""),
        document_id=_safe_text(value.get("document_id"), limit=128),
        document_version_id=_safe_text(value.get("document_version_id"), limit=128),
        document_name=_safe_text(value.get("document_name") or "文档", limit=255),
        version_number=max(1, int(value.get("version_number") or 1)),
        location_label=location_label,
        page_number=page_number,
        page_block_id=_safe_text(value.get("page_block_id"), limit=128) or None,
        bbox=bbox,
        excerpt=_safe_text(value.get("excerpt"), limit=4_000),
        support_state=value.get("support_state") or "uncertain",
    )
