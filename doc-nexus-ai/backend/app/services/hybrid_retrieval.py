"""Hybrid lexical + knowledge-graph retrieval for QA and chat knowledge injection."""

from __future__ import annotations

import logging
import inspect
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.knowledge_engine.graph_retriever import GraphRetriever, ScoredEntity
from app.models.documents import Document, DocumentVersion
from app.repositories import qa as qa_repo
from app.services.retrieval_router import (
    RetrievalMode,
    RetrievalPlan,
    plan_retrieval,
    should_escalate_to_vector,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievalBundle:
    documents: list[tuple[Document, DocumentVersion]]
    scores: list[dict[str, Any]] = field(default_factory=list)
    graph_entities: list[ScoredEntity] = field(default_factory=list)
    vector_hits: list[dict[str, Any]] = field(default_factory=list)
    enrichment_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def select_recommended_documents(
    score_rows: list[dict[str, Any]],
    *,
    threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """Keep documents with enough retrieval evidence for auto-selection.

    AUTO-mode fused ``score`` uses Reciprocal Rank Fusion (about 0.01–0.05)
    and must not be compared with a 0–1 threshold. Channel scores (lexical /
    vector / graph) are judged relative to the strongest hit so a clear title
    match like 「缔智元公司介绍」 can pass while unrelated docs stay out.
    """
    cutoff = max(0.0, min(1.0, threshold))
    rows = [
        row
        for row in score_rows
        if float(row.get("score") or 0) > 0
        and (
            float(row.get("lex_score") or 0) > 0
            or float(row.get("vector_score") or 0) > 0
            or float(row.get("graph_score") or 0) > 0
        )
    ]
    if not rows:
        return []
    peak_lex = max(float(row.get("lex_score") or 0) for row in rows)
    peak_vec = max(float(row.get("vector_score") or 0) for row in rows)
    peak_graph = max(float(row.get("graph_score") or 0) for row in rows)

    def _relative(value: float, peak: float) -> float:
        return value / peak if peak > 0 else 0.0

    selected: list[dict[str, Any]] = []
    for row in rows:
        relative = max(
            _relative(float(row.get("lex_score") or 0), peak_lex),
            _relative(float(row.get("vector_score") or 0), peak_vec),
            _relative(float(row.get("graph_score") or 0), peak_graph),
        )
        if relative >= cutoff:
            selected.append(row)
    return selected


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    peak = max(scores.values())
    if peak <= 0:
        return {key: 0.0 for key in scores}
    return {key: value / peak for key, value in scores.items()}


def _lexical_scores(
    question: str,
    pool: list[tuple[Document, DocumentVersion]],
) -> dict[str, float]:
    from app.services.qa_service import _lexical_relevance_score, _resolve_version_text

    out: dict[str, float] = {}
    for document, version in pool:
        body = _resolve_version_text(version) or ""
        out[document.id] = _lexical_relevance_score(
            question,
            title=document.name or "",
            body=body,
            summary=version.summary or "",
        )
    return out


def _vector_branch(
    question: str,
    space_ids: list[str],
    pool: list[tuple[Document, DocumentVersion]],
    *,
    status: dict[str, str] | None = None,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Search existing vector-store chunks and map them back to ready documents.

    The scope is built from the already-authorized document pool. If the vector
    store or its embedding endpoint is unavailable, callers retain lexical/graph
    behavior instead of failing the request.
    """
    if not settings.qa_vector_enabled or not pool:
        return {}, []
    try:
        from app.services.document_processor import derive_category, stable_knowledge_id
        from app.services.pgvector_store import PgVectorStore

        category_by_space = {
            space_id: derive_category(space_id) for space_id in set(space_ids)
        }
        scopes_by_category: dict[str, list[str]] = {}
        by_file: dict[tuple[str, str], tuple[Document, DocumentVersion]] = {}
        for document, version in pool:
            space_id = document.space_id
            if not space_id:
                continue
            category = category_by_space.get(space_id) or derive_category(space_id)
            file_id = str(stable_knowledge_id(version.id))
            scopes_by_category.setdefault(category, []).append(file_id)
            by_file[(category, file_id)] = (document, version)

        scopes = [
            (category, sorted(set(file_ids)))
            for category, file_ids in scopes_by_category.items()
        ]
        hits = PgVectorStore().search(
            query=question,
            scopes=scopes,
            top_k=max(1, int(settings.qa_vector_top_k)),
        )
        scores: dict[str, float] = {}
        mapped_hits: list[dict[str, Any]] = []
        for hit in hits:
            pair = by_file.get((str(hit.get("category") or ""), str(hit.get("file_id") or "")))
            if pair is None:
                continue
            document, version = pair
            score = float(hit.get("score") or 0.0)
            scores[document.id] = max(scores.get(document.id, 0.0), score)
            mapped_hits.append(
                {
                    **hit,
                    "document_id": document.id,
                    "document_version_id": version.id,
                    "document_name": document.name,
                }
            )
        return scores, mapped_hits
    except Exception:
        if status is not None:
            status.setdefault("vector", "vector_unavailable")
        logger.exception("hybrid vector branch failed")
        return {}, []


def _graph_branch(
    db: Session,
    question: str,
    space_ids: list[str],
    *,
    rich_context: bool = True,
    status: dict[str, str] | None = None,
) -> tuple[dict[str, float], list[ScoredEntity], str, bool, set[str]]:
    """Return graph doc scores, entities, optional enrichment, and boosted ids."""
    graph_scores: dict[str, float] = {}
    all_entities: list[ScoredEntity] = []
    enrichment_parts: list[str] = []
    graph_available = False
    boosted: set[str] = set()

    for space_id in space_ids[:5]:
        try:
            retriever = GraphRetriever(db)
            if retriever.space_has_active_entities(space_id):
                graph_available = True
            matched = retriever.match_and_expand(space_id, question)
            if not matched:
                continue
            all_entities.extend(matched)
            for document, _version, score in retriever.documents_for_entities(
                space_id, matched
            ):
                prev = graph_scores.get(document.id, 0.0)
                graph_scores[document.id] = max(prev, float(score))
                boosted.add(document.id)

            if rich_context:
                package = retriever.build_evidence_package(space_id, question, matched)
                surprises = retriever.detect_surprises(space_id, matched)
                gaps = retriever.detect_gaps(space_id, [], matched)
                ctx = retriever.assemble_writing_context(
                    section_objective=question,
                    matched_entities=matched,
                    evidence=package,
                    surprises=surprises,
                    gaps=gaps,
                    max_tokens=1200,
                )
                piece = (ctx.system_prompt_enrichment or "").strip()
                if piece:
                    enrichment_parts.append(piece)
                if ctx.key_entities:
                    enrichment_parts.append(
                        "关键实体：" + "、".join(ctx.key_entities[:8])
                    )
                if ctx.community_briefs:
                    enrichment_parts.extend(ctx.community_briefs[:3])
        except Exception:
            if status is not None:
                status.setdefault("graph", "graph_unavailable")
            logger.exception("hybrid graph branch failed space=%s", space_id)

    # Deduplicate entities by id, keep highest score
    by_id: dict[str, ScoredEntity] = {}
    for entity in all_entities:
        current = by_id.get(entity.entity_id)
        if current is None or entity.score > current.score:
            by_id[entity.entity_id] = entity

    return (
        graph_scores,
        list(by_id.values()),
        "\n\n".join(part for part in enrichment_parts if part).strip(),
        graph_available,
        boosted,
    )


def retrieve_for_qa(
    db: Session,
    question: str,
    space_ids: list[str],
    *,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
    attachment_document_ids: list[str] | None = None,
    mode: str | RetrievalMode = RetrievalMode.LEGACY,
    purpose: str | None = None,
    rich_graph_context: bool | None = None,
) -> RetrievalBundle:
    """Retrieve authorized documents with legacy or adaptive branch selection.

    The default remains the historical lexical+graph behavior so existing QA,
    chat, and writing callers do not lose coverage. Capability callers can opt
    into ``mode="auto"`` to avoid unnecessary Graph/vector work.
    """
    plan: RetrievalPlan = plan_retrieval(
        question,
        mode=mode,
        purpose=purpose,
    )
    branch_status: dict[str, str] = {}

    def _supports_status(function: Any) -> bool:
        try:
            return "status" in inspect.signature(function).parameters
        except (TypeError, ValueError):
            return False

    def _run_graph(*args: Any, **kwargs: Any) -> Any:
        if _supports_status(_graph_branch):
            return _graph_branch(*args, **kwargs, status=branch_status)
        return _graph_branch(*args, **kwargs)

    def _run_vector(*args: Any, **kwargs: Any) -> Any:
        if _supports_status(_vector_branch):
            return _vector_branch(*args, **kwargs, status=branch_status)
        return _vector_branch(*args, **kwargs)
    selected_k = top_k if top_k is not None else max(1, int(settings.qa_retrieval_top_k))
    if plan.mode == RetrievalMode.NONE:
        return RetrievalBundle(
            documents=[],
            meta={
                "retrieval_method": "none",
                "retrieval_mode": plan.mode.value,
                "route_reason": plan.reason,
                "selected_count": 0,
                "top_k": selected_k,
                "channels": {"lexical": False, "vector": False, "graph": False},
                "degraded": False,
                "degradation_reason": None,
            },
        )

    pool_limit = max(1, int(settings.qa_retrieval_pool_limit))
    pool = qa_repo.ready_documents_for_scope(
        db, space_ids, limit=pool_limit, document_ids=document_ids,
        attachment_document_ids=attachment_document_ids,
    )
    pool_by_id = {document.id: (document, version) for document, version in pool}
    lex_raw = _lexical_scores(question, pool) if plan.use_lexical and pool else {}

    if plan.mode == RetrievalMode.LEGACY:
        graph_raw, entities, enrichment, graph_available, boosted = _run_graph(
            db, question, space_ids
        )
    else:
        use_rich = (
            plan.rich_graph_context
            if rich_graph_context is None
            else bool(rich_graph_context)
        )
        if plan.use_graph:
            graph_raw, entities, enrichment, graph_available, boosted = _run_graph(
                db,
                question,
                space_ids,
                rich_context=use_rich,
            )
        else:
            graph_raw, entities, enrichment, graph_available, boosted = (
                {},
                [],
                "",
                False,
                set(),
            )

    allowed_ids = set(document_ids) if document_ids is not None else None
    if attachment_document_ids is not None:
        # SQL 候选池已经约束附件类型与空间，不信任原始附件 ID 扩大召回。
        allowed_ids = set(pool_by_id)
    if allowed_ids is not None:
        graph_raw = {
            doc_id: score
            for doc_id, score in graph_raw.items()
            if doc_id in allowed_ids
        }
        boosted = boosted & allowed_ids

    vector_raw: dict[str, float] = {}
    vector_hits: list[dict[str, Any]] = []
    should_vector = plan.use_vector
    if plan.vector_fallback and not should_vector:
        should_vector = should_escalate_to_vector(
            lex_raw,
            minimum_score=float(settings.qa_vector_min_lexical_score),
            minimum_margin=float(settings.qa_vector_min_lexical_margin),
        )
    if should_vector and pool:
        vector_raw, vector_hits = _run_vector(question, space_ids, pool)
        if allowed_ids is not None:
            vector_raw = {
                doc_id: score
                for doc_id, score in vector_raw.items()
                if doc_id in allowed_ids
            }
            vector_hits = [
                hit for hit in vector_hits if hit.get("document_id") in allowed_ids
            ]

    if plan.mode == RetrievalMode.LEGACY:
        graph_weight = min(1.0, max(0.0, float(settings.qa_graph_weight)))
        lex_weight = 1.0 - graph_weight
        lex_norm = _normalize(lex_raw)
        graph_norm = _normalize(graph_raw)
        fused: list[tuple[str, float, float, float, float]] = []
        for doc_id in pool_by_id:
            lex_s = lex_norm.get(doc_id, 0.0)
            graph_s = graph_norm.get(doc_id, 0.0)
            final = lex_weight * lex_s + graph_weight * graph_s
            if (
                final > 0
                or lex_raw.get(doc_id, 0.0) > 0
                or graph_raw.get(doc_id, 0.0) > 0
            ):
                fused.append(
                    (
                        doc_id,
                        final,
                        lex_raw.get(doc_id, 0.0),
                        graph_raw.get(doc_id, 0.0),
                        0.0,
                    )
                )
        fused.sort(key=lambda item: (-item[1], -item[3], -item[2], item[0]))
        method = "hybrid_lexical_graph"
    else:
        # Rank fusion avoids comparing incomparable lexical, graph, and vector
        # score scales. Each source contributes only by candidate rank.
        source_scores = {
            "lexical": lex_raw,
            "graph": graph_raw,
            "vector": vector_raw,
        }
        ranks: dict[str, dict[str, int]] = {}
        for source, scores in source_scores.items():
            ordered = sorted(
                (
                    (doc_id, score)
                    for doc_id, score in scores.items()
                    if score > 0 and doc_id in pool_by_id
                ),
                key=lambda item: (-item[1], item[0]),
            )
            ranks[source] = {
                doc_id: position for position, (doc_id, _score) in enumerate(ordered, 1)
            }
        candidate_ids = set().union(*(scores.keys() for scores in source_scores.values()))
        fused = []
        for doc_id in candidate_ids:
            if doc_id not in pool_by_id:
                continue
            final = sum(
                1.0 / (60.0 + rank_map[doc_id])
                for rank_map in ranks.values()
                if doc_id in rank_map
            )
            if final <= 0:
                continue
            fused.append(
                (
                    doc_id,
                    final,
                    lex_raw.get(doc_id, 0.0),
                    graph_raw.get(doc_id, 0.0),
                    vector_raw.get(doc_id, 0.0),
                )
            )
        fused.sort(key=lambda item: (-item[1], -item[4], -item[3], -item[2], item[0]))
        active_sources = [
            name for name, scores in source_scores.items() if any(value > 0 for value in scores.values())
        ]
        method = (
            "adaptive_" + "_".join(active_sources)
            if active_sources
            else "pool_fallback"
        )

    if not fused:
        selected_pairs = pool[:selected_k]
        method = "pool_fallback"
        score_rows = [
            {
                "document_id": document.id,
                "score": 0.0,
                "lex_score": 0.0,
                "graph_score": 0.0,
                "vector_score": 0.0,
            }
            for document, _version in selected_pairs
        ]
    else:
        selected_pairs = [
            pool_by_id[doc_id] for doc_id, *_scores in fused[:selected_k]
        ]
        score_rows = [
            {
                "document_id": doc_id,
                "score": round(final, 4),
                "lex_score": round(lex_score, 4),
                "graph_score": round(graph_score, 4),
                "vector_score": round(vector_score, 4),
            }
            for doc_id, final, lex_score, graph_score, vector_score in fused[:selected_k]
        ]
        if plan.mode == RetrievalMode.LEGACY and not any(
            graph_raw.get(doc_id, 0.0) > 0 for doc_id, *_scores in fused[:selected_k]
        ):
            if not graph_available or not entities:
                method = "lexical_relevance"

    graph_boosted_selected = [
        document.id for document, _version in selected_pairs if document.id in boosted
    ]
    meta = {
        "retrieval_method": method,
        "retrieval_mode": plan.mode.value,
        "route_reason": plan.reason,
        "pool_size": len(pool),
        "selected_count": len(selected_pairs),
        "top_k": selected_k,
        "graph_available": graph_available,
        "graph_entity_count": len(entities),
        "graph_boosted_doc_ids": graph_boosted_selected,
        "graph_weight": float(settings.qa_graph_weight),
        "vector_enabled": bool(settings.qa_vector_enabled),
        "vector_used": bool(vector_hits),
        "channels": {
            "lexical": bool(plan.use_lexical and lex_raw),
            "vector": bool(vector_hits),
            "graph": bool(
                (plan.mode == RetrievalMode.LEGACY or plan.use_graph)
                and (graph_raw or entities or graph_available)
            ),
        },
        "degraded": bool(branch_status),
        "degradation_reason": ",".join(
            sorted(set(branch_status.values()))
        ) or None,
        "scores": score_rows,
    }
    return RetrievalBundle(
        documents=selected_pairs,
        scores=score_rows,
        graph_entities=entities,
        vector_hits=vector_hits,
        enrichment_text=enrichment,
        meta=meta,
    )
