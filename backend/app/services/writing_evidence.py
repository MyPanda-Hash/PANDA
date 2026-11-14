"""Scoped writing evidence: relevance retrieval and exhaustive document inventory."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import as_utc, iso, now
from app.repositories.documents import creator_display_names

from app.services.evidence_inventory import _evidence_item, inventory_evidence, is_inventory_request
from app.services.evidence_snapshots import freeze_text, parse_citation_id, read_snapshot
from app.services.hybrid_retrieval import retrieve_for_qa
from app.services.image_documents import image_source_text
from app.services.retrieval_contract import build_retrieval_trace
from app.services.qa_service import _evidence_support_state, _lexical_relevance_score, _resolve_version_text
from app.services.writing_fact_check import detect_prompt_injection

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")
_DEFAULT_TOP_CHUNKS = 8
_MAX_CHUNKS_PER_DOC = 2
_CHUNK_TARGET_CHARS = 900
_CHUNK_MAX_CHARS = 1200


def _split_chunks(text: str) -> list[tuple[int, int, str]]:
    """Split document text into paragraph-ish spans with character offsets."""
    source = text or ""
    if not source.strip():
        return []
    # Certificates and other short records must retain related field context;
    # blank lines must not separate a certificate name from its owner/dates/ID.
    if len(source) <= 4_000:
        return [(0, len(source), source)]
    chunks: list[tuple[int, int, str]] = []
    cursor = 0
    for block in _PARAGRAPH_SPLIT_RE.split(source):
        idx = source.find(block, cursor)
        if idx < 0:
            idx = cursor
        start = idx
        end = idx + len(block)
        cursor = end
        paragraph = block.strip()
        if not paragraph:
            continue
        if len(paragraph) <= _CHUNK_MAX_CHARS:
            local_start = start + block.find(paragraph)
            chunks.append((local_start, local_start + len(paragraph), paragraph))
            continue
        # Long paragraphs: sliding windows on sentence-ish boundaries.
        offset = 0
        while offset < len(paragraph):
            window = paragraph[offset : offset + _CHUNK_TARGET_CHARS]
            if offset + _CHUNK_TARGET_CHARS < len(paragraph):
                cut = max(window.rfind("。"), window.rfind("！"), window.rfind("？"), window.rfind("\n"))
                if cut >= int(_CHUNK_TARGET_CHARS * 0.4):
                    window = window[: cut + 1]
            piece = window.strip()
            if piece:
                local_start = start + block.find(paragraph) + offset
                # Prefer exact piece location inside paragraph.
                rel = paragraph.find(piece, offset)
                if rel >= 0:
                    local_start = start + block.find(paragraph) + rel
                chunks.append((local_start, local_start + len(piece), piece))
            step = max(200, len(window) - 80)
            offset += step
    if not chunks and source.strip():
        excerpt = source.strip()[:_CHUNK_MAX_CHARS]
        start = source.find(excerpt)
        chunks.append((max(0, start), max(0, start) + len(excerpt), excerpt))
    return chunks




def search(
    db: Session,
    *,
    space_ids: list[str],
    chapter_id: str,
    query: str,
    purpose: str,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
    cursor: str | None = None,
    max_chars: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    if cursor is not None or is_inventory_request(query):
        return inventory_evidence(
            db, space_ids=space_ids, document_ids=document_ids,
            query=query, chapter_id=chapter_id, cursor=cursor, max_chars=max_chars,
            max_bytes=max_bytes,
        )
    adaptive_writing = purpose in {"outline", "research"}
    bundle = retrieve_for_qa(
        db,
        query,
        space_ids,
        top_k=top_k,
        document_ids=document_ids,
        mode="auto" if adaptive_writing else "legacy",
        purpose=purpose,
        rich_graph_context=adaptive_writing,
    )
    selected_k = max(0, top_k) if top_k is not None else _DEFAULT_TOP_CHUNKS
    scored: list[tuple[float, dict[str, Any]]] = []
    injection_flags: list[dict[str, str]] = []
    creator_names = creator_display_names(
        db, [getattr(document, "created_by_user_id", "") for document, _version in bundle.documents],
    )
    for document, version in bundle.documents:
        full_text = _resolve_version_text(version) or ""
        if not full_text.strip():
            continue
        chunks = _split_chunks(full_text)
        ranked_chunks: list[tuple[float, int, int, int, str]] = []
        for index, (start, end, excerpt) in enumerate(chunks):
            score = _lexical_relevance_score(
                query, title=getattr(document, "name", "") or "",
                body=excerpt, summary="",
            )
            if score > 0:
                ranked_chunks.append((score, index, start, end, excerpt))
        if not ranked_chunks and chunks:
            start, end, excerpt = chunks[0]
            ranked_chunks.append((0.01, 0, start, end, excerpt))
        text_sha256 = freeze_text(version.id, full_text)
        for score, index, start, end, excerpt in sorted(
            ranked_chunks, key=lambda item: (-item[0], item[1])
        )[:_MAX_CHUNKS_PER_DOC]:
            item = _evidence_item(
                db, document, version, text_sha256=text_sha256,
                start=start, end=end, excerpt=excerpt, index=index, score=score,
                snapshot_text=full_text,
            )
            creator_id = getattr(document, "created_by_user_id", None)
            created_at = getattr(document, "created_at", None)
            item.update(
                created_by_user_id=creator_id,
                created_by_display_name=creator_names.get(creator_id or ""),
                created_at=iso(created_at) if created_at is not None else None,
            )
            hits = detect_prompt_injection(item["excerpt"])
            if hits:
                injection_flags.append({
                    "document_version_id": version.id,
                    "chunk_index": str(index), "match": hits[0],
                })
            scored.append((score, item))

    scored.sort(key=lambda item: (
        -item[0], item[1]["document_id"], item[1]["document_version_id"],
        item[1]["chunk_index"],
    ))
    # Give every relevant document one slot before spending slots on its second
    # chunk. Stable relevance ordering applies within each diversity pass.
    first: list[dict[str, Any]] = []
    additional: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _score, item in scored:
        if item["document_id"] in seen:
            additional.append(item)
        else:
            seen.add(item["document_id"])
            first.append(item)
    evidence = (first + additional)[:selected_k]
    digest_input = {
        "space_ids": space_ids,
        "document_ids": document_ids,
        "chapter_id": chapter_id,
        "query": query,
        "purpose": purpose,
        "evidence": evidence,
    }
    digest = hashlib.sha256(
        json.dumps(digest_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "result_ref": f"writing-search:{digest[:20]}",
        "evidence": evidence,
        "knowledge_gaps": [] if evidence else ["no_queryable_evidence"],
        "provider": "hybrid_chunk",
        "retrieval": {
            **(bundle.meta or {}),
            "retrieval_trace": build_retrieval_trace(
                bundle, citation_count=len(evidence),
                support_states=(item.get("support_state", "uncertain") for item in evidence),
            ).model_dump(),
            "unit": "chunk",
            "exhaustive": False,
            "max_chunks_per_document": _MAX_CHUNKS_PER_DOC,
            "selected_chunk_count": len(evidence),
            "selected_document_count": len({item["document_id"] for item in evidence}),
            "injection_flags": injection_flags[:20],
        },
        "enrichment_text": bundle.enrichment_text,
        "untrusted_source": True,
        "sha256": digest,
    }


_MARKER_WRAPPER_RE = re.compile(
    r"^\^?\[(?:citation:)?((?:writing-evidence|vector-evidence):[^\]\r\n]+)\]$"
    r"|^\^?\[(citation:[^\]\r\n]+)\]$"
)


def normalize_citation_id(raw: str) -> str:
    """Normalize editor/export marker text into a bare citation id."""
    value = (raw or "").strip()
    if not value:
        return ""
    wrapped = _MARKER_WRAPPER_RE.match(value)
    if wrapped:
        value = (wrapped.group(1) or wrapped.group(2) or "").strip()
    if value.startswith("citation:") and not value.startswith("citation:writing-evidence:"):
        # Keep opaque legacy ids like citation:0 as-is; unwrap writing-evidence.
        inner = value[len("citation:") :]
        if inner.startswith("writing-evidence:") or inner.startswith("vector-evidence:"):
            return inner
        return value
    if value.startswith("citation:writing-evidence:") or value.startswith(
        "citation:vector-evidence:"
    ):
        return value[len("citation:") :]
    return value


_CITATION_MARKER_RE = re.compile(
    r"\^?\[((?:citation:)?(?:writing-evidence|vector-evidence):[^\]\r\n]+)\]"
)


def split_citation_span(citation_id: str) -> tuple[str, int, int] | None:
    """Read version/span without granting access or discarding hash identity.

    This compatibility projection is only for document authorization lookups.
    Exact citation comparisons must use the normalized complete ID.
    """
    parsed = parse_citation_id(normalize_citation_id(citation_id))
    if parsed is None:
        return None
    version_id, _text_sha256, start, end = parsed
    return version_id, start, end


def verified_citation_ids(
    texts: Iterable[str], recorded_ids: Iterable[str]
) -> set[str]:
    """Citations that are both written in the anchor text and recorded server-side.

    The intersection is the whole point: markers alone are not credentials
    (anyone with edit rights can type one), and recorded ids alone are not
    "cited in this snapshot". Callers pass the anchor's own text (live chapter
    content, or a frozen publication snapshot) plus the ids recovered from the
    successful ``search_evidence`` receipts.

    Pure function — no database access — so the live and the publish-time paths
    share exactly one implementation. Neither side is resolved against
    ``DocumentVersion`` / ``Document`` here.
    """
    recorded = {
        normalized
        for raw in recorded_ids
        if (normalized := normalize_citation_id(str(raw or "")))
        and parse_citation_id(normalized) is not None
    }
    if not recorded:
        return set()
    found: set[str] = set()
    for text in texts:
        for match in _CITATION_MARKER_RE.finditer(text or ""):
            normalized = normalize_citation_id(match.group(1))
            if normalized and parse_citation_id(normalized) is not None:
                found.add(normalized)
    return found & recorded


def expand_citation_for_project(
    db: Session,
    *,
    space_ids: list[str],
    document_ids: list[str] | None,
    citation_id: str,
    before: int = 400,
    after: int = 400,
) -> dict[str, Any] | None:
    """Authorize first, then resolve an immutable citation text snapshot.

    Hashless legacy IDs have no authoritative historical text binding. They
    remain identifiable references, but cannot safely expand current offsets.
    Explicit document IDs are an already-authorized caller selection and take
    precedence over spaces, including source-chat temporary documents.
    """
    from app.models.documents import Document, DocumentVersion

    citation_id = normalize_citation_id(citation_id)
    parsed = parse_citation_id(citation_id)
    if parsed is None:
        return None
    version_id, text_sha256, char_start, char_end = parsed
    version = db.get(DocumentVersion, version_id)
    if version is None:
        return None
    document = db.get(Document, version.document_id)
    if document is None or document.status != "active":
        return None
    if (
        document.is_temporary
        and document.expires_at is not None
        and as_utc(document.expires_at) <= now()
    ):
        return None
    if document_ids is not None:
        if document.id not in document_ids:
            return None
    elif not space_ids or document.space_id not in space_ids:
        return None
    creator_names = creator_display_names(db, [document.created_by_user_id])

    label = (
        f"文档《{document.name}》v{version.version_number} "
        f"片段 [{char_start}-{char_end}]"
    )
    result: dict[str, Any] = {
        "citation_id": citation_id,
        "document_id": document.id,
        "document_name": document.name,
        "document_version_id": version.id,
        "version_number": version.version_number,
        "space_id": document.space_id,
        "created_by_user_id": document.created_by_user_id,
        "created_by_display_name": creator_names.get(document.created_by_user_id),
        "created_at": iso(document.created_at),
        "text_sha256": text_sha256,
        "char_start": char_start,
        "char_end": char_end,
        "window_start": char_start,
        "window_end": char_end,
        "excerpt": "",
        "highlighted": "",
        "truncated": False,
        "page_or_section": label,
        "untrusted": True,
        "support_state": "uncertain",
        "binding_state": "stale",
    }
    text = read_snapshot(version_id, text_sha256) if text_sha256 else None
    if text is None:
        result["stale_reason"] = (
            "snapshot_unavailable" if text_sha256 else "legacy_unbound"
        )
        return result
    if char_start >= len(text) or char_end > len(text) or char_end <= char_start:
        result["stale_reason"] = "invalid_snapshot_span"
        return result

    before = max(0, min(before, 2_000))
    after = max(0, min(after, 2_000))
    # Keep the actual cited span unchanged; response limits apply only to display.
    window_start = max(0, char_start - before)
    window_end = min(len(text), char_end + after)
    result.update(
        window_start=window_start,
        window_end=window_end,
        excerpt=image_source_text(version, text, db=db, start=window_start, end=min(window_end, window_start + 8_000)),
        highlighted=image_source_text(version, text, db=db, start=char_start, end=min(char_end, char_start + 4_000)),
        truncated=window_end - window_start > 8_000 or char_end - char_start > 4_000,
        binding_state="verified",
    )
    from app.services.evidence_snapshots import snapshot_page_location

    try:
        location = snapshot_page_location(db, version.id, text, char_start, char_end)
    except SQLAlchemyError:
        location = None
    result["support_state"] = _evidence_support_state(version, location, snapshot_text=text)
    if location is not None:
        result.update(
            page_or_section=f"{label} · {location.location_label}",
            page_number=location.page_number,
            page_block_id=location.page_block_id,
            bbox=location.bbox,
            page_image_path=(
                f"/documents/{document.id}/versions/{version.id}/pages/{location.page_number}/image"
            ),
        )
    return result
