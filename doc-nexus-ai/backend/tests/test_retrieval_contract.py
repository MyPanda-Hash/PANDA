from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.evidence import EvidenceLocationResponse
from app.services.hybrid_retrieval import RetrievalBundle
from app.services.retrieval_contract import (
    build_evidence_location,
    build_retrieval_trace,
)


def test_retrieval_trace_is_bounded_and_excludes_content() -> None:
    bundle = RetrievalBundle(
        documents=[],
        meta={
            "retrieval_mode": "factual",
            "retrieval_method": "pool_fallback",
            "route_reason": "semantic factual query",
            "pool_size": 8,
            "query": "private question",
            "prompt": "private prompt",
            "vector": ["private embedding"],
        },
    )

    payload = build_retrieval_trace(bundle, citation_count=0).model_dump()

    assert payload["grounding_status"] == "knowledge_gap"
    assert payload["channels"] == {"lexical": False, "vector": False, "graph": False}
    assert "query" not in payload
    assert "prompt" not in payload
    assert "vector" not in payload


def test_trace_prioritizes_degradation_and_exposes_only_channel_flags() -> None:
    bundle = RetrievalBundle(
        documents=[object()],
        meta={
            "retrieval_mode": "auto",
            "retrieval_method": "adaptive_lexical_vector",
            "route_reason": "ambiguous",
            "pool_size": 2,
            "selected_count": 1,
            "channels": {"lexical": True, "vector": True, "graph": False},
            "degraded": True,
            "degradation_reason": "vector_unavailable",
        },
    )

    trace = build_retrieval_trace(bundle, citation_count=1)

    assert trace.grounding_status == "degraded"
    assert trace.degraded is True
    assert trace.degradation_reason == "vector_unavailable"
    assert trace.channels == {"lexical": True, "vector": True, "graph": False}


def test_evidence_location_validates_bbox_and_bounds_excerpt() -> None:
    evidence = build_evidence_location(
        {
            "id": "cit-1",
            "document_id": "doc-1",
            "document_version_id": "ver-1",
            "document_name": "产品说明",
            "version_number": 2,
            "page_or_section": "第 3 页",
            "page_number": 3,
            "page_block_id": "block-1",
            "bbox": (0.1, 0.2, 0.8, 0.9),
            "excerpt": "x" * 8_000,
            "support_state": "supported",
        }
    )

    assert evidence.citation_id == "cit-1"
    assert evidence.page_number == 3
    assert evidence.location_label == "第 3 页"
    assert len(evidence.excerpt) == 4_000

    with pytest.raises(ValidationError):
        EvidenceLocationResponse(
            citation_id="cit-2",
            document_id="doc-1",
            document_version_id="ver-1",
            document_name="产品说明",
            version_number=1,
            location_label="第 1 页",
            page_number=0,
            bbox=(0.9, 0.1, 0.2, 0.8),
            excerpt="bad",
            support_state="supported",
        )


def test_grounding_status_supports_partial_and_not_requested() -> None:
    none_bundle = RetrievalBundle(
        documents=[],
        meta={"retrieval_mode": "none", "retrieval_method": "none"},
    )
    assert build_retrieval_trace(none_bundle, citation_count=0).grounding_status == "not_requested"

    partial_bundle = RetrievalBundle(
        documents=[object(), object()],
        meta={
            "retrieval_mode": "legacy",
            "retrieval_method": "hybrid_lexical_graph",
            "pool_size": 2,
            "selected_count": 2,
        },
    )
    assert build_retrieval_trace(partial_bundle, citation_count=1).grounding_status == "partial"
