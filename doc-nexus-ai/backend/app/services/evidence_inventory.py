"""Deterministic evidence inventory over an already-authorized document scope.

Unlike relevance retrieval, inventory enumerates every scoped active document.
Context limits bound emitted text, never the document manifest or coverage count.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import iso
from app.repositories.documents import creator_display_names

from app.models.documents import Document, DocumentVersion
from app.repositories.qa import ready_documents_for_scope
from app.services.evidence_snapshots import freeze_text, make_citation_id, read_snapshot
from app.services.image_documents import image_source_text, is_image_version
from app.services.qa_service import _evidence_support_state, _resolve_version_text
from app.services.writing_fact_check import detect_prompt_injection, quarantine_excerpt

_DOCUMENT_BATCH_SIZE = 100
_PAGE_CHARS = 4_000
_MAX_DOCUMENT_CHARS = 24_000
_MAX_CONTEXT_CHARS = 240_000
_MAX_CONTEXT_BYTES = 1_000_000
_CURSOR_RE = re.compile(r"inventory:([0-9a-f]{32}):([0-9a-f]{64})")
_INVENTORY_RE = re.compile(
    r"清单|一览|盘点|逐一|逐个|不(?:要)?遗漏|无遗漏|"
    r"(?:列出|列举|罗列|整理|汇总|统计).{0,24}(?:所有|全部|完整|全量)|"
    r"(?:列出|列举|罗列|汇总|统计|有哪些).{0,24}(?:资料|文档|证书|资质|专利|软著|著作权)|"
    r"(?:所有|全部|完整|全量).{0,24}(?:列出|列举|罗列|整理|汇总|统计|资料|文档|证书|资质|专利|软著|著作权)|"
    r"\b(?:inventory|exhaustive|comprehensive\s+list|list\s+(?:all|every)|"
    r"(?:list|enumerate)\s+(?:the\s+)?(?:documents?|files?|certificates?|qualifications?|patents?)|"
    r"(?:all|every)\s+(?:documents?|files?|certificates?|qualifications?|patents?))\b",
    re.IGNORECASE,
)


def is_inventory_request(query: str) -> bool:
    """Recognize explicit enumeration intent, not ordinary relevance questions."""
    return bool(_INVENTORY_RE.search(query or ""))


def evidence_prompt_byte_budget(
    *,
    max_context_tokens: int,
    context_used_tokens: int = 0,
    reserve_tokens: int | None = None,
) -> int:
    """Conservative UTF-8 admission budget shared by writing and chat prompts.

    A byte budget no greater than remaining tokens is safe for tokenizers with
    byte fallback. Callers also subtract their serialized prompt scaffolding.
    """
    reserve = max_context_tokens // 2 if reserve_tokens is None else reserve_tokens
    return max(0, max_context_tokens - max(0, context_used_tokens) - max(0, reserve))


def merge_inventory_pages(
    base: dict[str, Any] | None, page: dict[str, Any],
) -> dict[str, Any]:
    """Merge authorized same-chain receipts using actually received spans.

    Cursor acknowledgements describe server iteration, not what this prompt has
    received. The merged payload is intentionally not per-page byte bounded;
    its caller must apply the final combined prompt budget.
    """
    from app.services.evidence_snapshots import parse_citation_id
    from app.services.writing_fact_check import inventory_records

    def chain(payload: dict[str, Any]) -> str:
        coverage = payload.get("coverage") or {}
        reference = payload.get("manifest_ref") or coverage.get("manifest_ref")
        match = _CURSOR_RE.fullmatch(str(reference or ""))
        if match is None:
            raise ValueError("Inventory receipt has no valid manifest reference")
        return match.group(1)

    chain_id = chain(page)
    if base and chain(base) != chain_id:
        raise ValueError("Cannot merge different inventory manifests")
    previous = base or {}
    page_coverage = page.get("coverage") or {}
    expected = int(page_coverage.get("expected") or 0)
    if base and int((base.get("coverage") or {}).get("expected") or 0) != expected:
        raise ValueError("Inventory manifest document count changed")
    documents: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for payload in (previous, page):
        for row in (payload.get("coverage") or {}).get("documents", []):
            document_id = row["document_id"]
            old = documents.get(document_id)
            if old and any(
                old.get(key) != row.get(key)
                for key in ("document_version_id", "text_sha256", "total_chars")
            ):
                raise ValueError("Inventory manifest text binding changed")
            documents[document_id] = {
                **row, "source_status": row.get("source_status") or row["status"],
            }
        for item in payload.get("evidence", []):
            citation_id = str(item.get("citation_id") or item.get("id") or "")
            parsed = parse_citation_id(citation_id)
            if parsed is None or parsed[1] is None:
                continue
            version_id, digest, start, end = parsed
            if (
                item.get("document_version_id") != version_id
                or item.get("text_sha256") != digest
                or item.get("char_start") != start
                or item.get("char_end") != end
            ):
                raise ValueError("Inventory evidence identity changed")
            old = evidence.get(citation_id)
            if old and old.get("document_id") != item.get("document_id"):
                raise ValueError("Inventory evidence document changed")
            evidence[citation_id] = {**item, "id": citation_id}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for item in evidence.values():
        row = documents.get(item["document_id"])
        if (
            row is None
            or row.get("document_version_id") != item["document_version_id"]
            or row.get("text_sha256") != item["text_sha256"]
            or not isinstance(row.get("total_chars"), int)
            or item["char_end"] > row["total_chars"]
        ):
            raise ValueError("Inventory evidence lacks its frozen document metadata")
        ranges.setdefault(item["document_id"], []).append((item["char_start"], item["char_end"]))
    for document_id, row in documents.items():
        if row["source_status"] in {"missing", "failed"} or not row.get("text_sha256"):
            row["status"] = row["source_status"]
            continue
        total = row["total_chars"]
        received: list[list[int]] = []
        for start, end in sorted(ranges.get(document_id, [])):
            if received and start <= received[-1][1]:
                received[-1][1] = max(received[-1][1], end)
            else:
                received.append([start, end])
        missing = []
        cursor = 0
        for start, end in received:
            if start > cursor:
                missing.append([cursor, start])
            cursor = end
        if cursor < total:
            missing.append([cursor, total])
        covered = sum(end - start for start, end in received)
        row.update(
            status="processed" if not missing else "truncated" if covered else "unprocessed",
            covered_chars=covered, received_ranges=received, unprocessed_ranges=missing,
        )
    counts = {
        status: sum(row["status"] == status for row in documents.values())
        for status in ("processed", "missing", "failed", "truncated")
    }
    source_counts = page_coverage.get("cursor_coverage") or page_coverage
    # Missing/failed totals are server-observed availability, not received text.
    # Preserve even compactly unreported failures; unseen ready docs stay unread.
    for status in ("missing", "failed"):
        counts[status] = max(counts[status], int(source_counts.get(status) or 0))
    unprocessed = expected - sum(counts.values())
    if unprocessed < 0 or len(documents) > expected:
        raise ValueError("Inventory source coverage cannot be reconciled")
    coverage = {
        **page_coverage, **counts, "unprocessed": unprocessed,
        "complete": counts["processed"] == expected,
        "delivery_basis": "received_evidence_ranges",
        "documents": [documents[key] for key in sorted(documents)],
        "unresolved_count": expected - counts["processed"],
        "unreported_document_count": expected - len(documents),
        "cursor_coverage": {
            key: source_counts.get(key)
            for key in ("expected", "processed", "missing", "failed", "unprocessed", "truncated", "complete")
        },
    }
    merged = {**previous, **page, "coverage": coverage, "evidence": list(evidence.values())}
    if not coverage["complete"] and not page.get("next_cursor") and previous.get("next_cursor"):
        merged["next_cursor"] = previous["next_cursor"]
    merged["retrieval"] = {
        **(previous.get("retrieval") or {}), **(page.get("retrieval") or {}),
        "coverage": coverage, "exhaustive": coverage["complete"],
        "selected_count": len(ranges), "selected_chunk_count": len(evidence),
    }
    merged["inventory_records"] = inventory_records(merged)
    gaps = [
        gap for payload in (previous, page) for gap in payload.get("knowledge_gaps", [])
        if gap not in {"inventory_incomplete", "inventory_page_budget_exhausted", "no_queryable_evidence"}
    ]
    if not evidence:
        gaps.append("no_queryable_evidence")
    if not coverage["complete"]:
        gaps.append("inventory_incomplete")
    merged["knowledge_gaps"] = list(dict.fromkeys(gaps))
    return merged


def _evidence_item(
    db: Session,
    document: Any,
    version: Any,
    *,
    text_sha256: str,
    start: int,
    end: int,
    excerpt: str,
    index: int,
    score: float,
    snapshot_text: str | None = None,
) -> dict[str, Any]:
    """Build one hash-bound span; display sanitization never changes its offsets."""
    location = None
    if snapshot_text is not None:
        from app.services.evidence_snapshots import snapshot_page_location

        try:
            location = snapshot_page_location(db, version.id, snapshot_text, start, end)
        except SQLAlchemyError:
            location = None
    support_state = _evidence_support_state(version, location, snapshot_text=snapshot_text)
    if snapshot_text is not None:
        excerpt = image_source_text(version, snapshot_text, db=db, start=start, end=end)
    label = (
        f"文档《{document.name}》v{version.version_number} "
        f"片段#{index + 1} [{start}-{end}]"
    )
    if location is not None:
        label += f" · {location.location_label}"
    result = {
        "id": make_citation_id(version.id, text_sha256, start, end),
        "document_id": document.id,
        "document_name": document.name,
        "space_id": getattr(document, "space_id", None),
        "document_version_id": version.id,
        "version_number": version.version_number,
        "text_sha256": text_sha256,
        "binding_state": "verified",
        "chunk_id": f"{version.id}:{text_sha256}:{start}-{end}",
        "chunk_index": index,
        "char_start": start,
        "char_end": end,
        "page_or_section": label,
        "excerpt": quarantine_excerpt(excerpt, max_chars=max(len(excerpt), 1)),
        "untrusted": True,
        "support_state": support_state,
        "score": round(score, 4),
    }
    if location is not None:
        result.update({
            "page_number": location.page_number,
            "page_block_id": location.page_block_id,
            "bbox": location.bbox,
        })
    return result


def inventory_evidence(
    db: Session,
    *,
    space_ids: list[str],
    document_ids: list[str] | None,
    attachment_document_ids: list[str] | None = None,
    query: str,
    chapter_id: str = "",
    cursor: str | None = None,
    max_chars: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Deliver a bounded page of an authorized, immutable document inventory.

    Explicit document IDs take precedence over spaces, matching the ready-doc
    repository contract. Callers reauthorize the actor on every invocation.
    A continuation binds the original versions, hashes and cumulative offsets,
    never newly extracted text or a newly published version. Invalid cursors or
    a changed scope fail closed with ValueError, not a fresh unscoped search.
    """
    selected_ids = sorted(set(document_ids)) if document_ids is not None else None
    scope = {
        "space_ids": sorted(set(space_ids)), "document_ids": selected_ids,
        "query": query, "chapter_id": chapter_id,
    }
    # 旧游标保持原摘要；新授权同时绑定附件集合及分离语义。
    attachment_ids = (
        sorted(set(attachment_document_ids))
        if attachment_document_ids is not None else None
    )
    if attachment_ids is not None:
        scope["attachment_document_ids"] = attachment_ids
    scope_digest = hashlib.sha256(json.dumps(
        scope, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if max_chars is not None and (type(max_chars) is not int or max_chars < _PAGE_CHARS):
        raise ValueError("Inventory text budget must be at least 4000 characters")
    batch_chars = min(max_chars, _MAX_CONTEXT_CHARS) if max_chars is not None else _MAX_CONTEXT_CHARS
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ValueError("Inventory byte budget must be positive")
    batch_bytes = min(max_bytes, _MAX_CONTEXT_BYTES) if max_bytes is not None else _MAX_CONTEXT_BYTES
    knowledge_scope = (
        Document.id.in_(selected_ids) if selected_ids is not None
        else Document.space_id.in_(space_ids)
    )
    if attachment_ids is not None and selected_ids is not None:
        knowledge_scope = and_(knowledge_scope, Document.space_id.in_(space_ids))
    if attachment_ids:
        knowledge_scope = or_(
            knowledge_scope,
            and_(
                Document.id.in_(attachment_ids),
                Document.is_temporary.is_(True),
                Document.space_id.is_(None),
            ),
        )
    filters = [
        Document.status == "active",
        or_(
            Document.is_temporary.is_(False),
            Document.expires_at.is_(None),
            Document.expires_at > datetime.now(timezone.utc),
        ),
        knowledge_scope,
    ]
    if attachment_ids is not None:
        filters.extend([
            Document.temporary_cleanup_at.is_(None),
            or_(
                Document.expires_at.is_(None),
                Document.expires_at > datetime.now(timezone.utc),
            ),
        ])
    if cursor is not None:
        match = _CURSOR_RE.fullmatch(cursor)
        if match is None:
            raise ValueError("Invalid inventory cursor")
        inventory_id = match.group(1)
        raw = read_snapshot(f"inventory_{inventory_id}", match.group(2))
        if raw is None:
            raise ValueError("Inventory cursor snapshot unavailable")
        try:
            state = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid inventory cursor state") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("scope_digest") != scope_digest
            or not isinstance(state.get("documents"), list)
        ):
            raise ValueError("Inventory cursor scope changed")
        manifest = state["documents"]
    else:
        inventory_id = uuid4().hex
        manifest = _capture_manifest(
            db, filters=filters,
            selected_ids=sorted(set((selected_ids or []) + (attachment_ids or []))),
        )

    evidence: list[dict[str, Any]] = []
    injection_flags: list[dict[str, str]] = []
    progress_before: dict[str, tuple[int, str, str | None]] = {}
    remaining = batch_chars
    candidate_bytes = 0
    candidate_budget_full = False
    for offset in range(0, len(manifest), _DOCUMENT_BATCH_SIZE):
        batch = manifest[offset:offset + _DOCUMENT_BATCH_SIZE]
        document_ids_in_batch = [row["document_id"] for row in batch]
        authorized = {
            document.id: document
            for document in db.scalars(select(Document).where(
                *filters, Document.id.in_(document_ids_in_batch),
            ))
        }
        versions = {
            version.id: version
            for version in db.scalars(select(DocumentVersion).where(
                DocumentVersion.id.in_([
                    row["document_version_id"] for row in batch
                    if row.get("document_version_id")
                ]),
            ))
        }
        for row in batch:
            # Even already-processed manifest names must not be returned after
            # deletion, expiry, or moving a document outside the current scope.
            if row.get("document_name") is not None and row["document_id"] not in authorized:
                raise ValueError("Inventory document scope is no longer available")
            text_sha256 = row.get("text_sha256")
            if not text_sha256 or row["status"] in {"processed", "missing", "failed"}:
                continue
            version_id = row["document_version_id"]
            if version_id not in versions or versions[version_id].document_id != row["document_id"]:
                row.update(status="missing", reason="snapshot_version_unavailable")
                continue
            start_offset = row["covered_chars"]
            total_chars = row["total_chars"]
            available = total_chars - start_offset
            if candidate_budget_full:
                row["reason"] = "response_byte_limit"
                continue
            if remaining <= 0 or (total_chars <= _PAGE_CHARS and available > remaining):
                row["reason"] = "context_budget_exhausted"
                continue
            text = read_snapshot(version_id, text_sha256)
            if text is None or len(text) != total_chars:
                row.update(status="missing", reason="snapshot_unavailable")
                continue
            if is_image_version(versions[version_id]) and row.get("extraction_warning_code") == "image_vision_uncertain":
                row["extraction_warning_code"] = None
            progress_before[row["document_id"]] = (
                start_offset, row["status"], row.get("reason"),
            )
            end_offset = min(total_chars, start_offset + _MAX_DOCUMENT_CHARS, start_offset + remaining)
            # 引用身份仍绑定冻结文本；页面映射只在原始全文逐字匹配后使用。
            document = SimpleNamespace(
                id=row["document_id"], name=row["document_name"], space_id=row["space_id"],
            )
            version = SimpleNamespace(
                id=version_id, version_number=row["version_number"],
                extraction_method=row["extraction_method"],
                source_relative_path=versions[version_id].source_relative_path,
                file_path=versions[version_id].file_path,
                extraction_warning_code=row.get(
                    "extraction_warning_code", versions[version_id].extraction_warning_code,
                ),
            )
            emitted_end = start_offset
            for start in range(start_offset, end_offset, _PAGE_CHARS):
                end = min(end_offset, start + _PAGE_CHARS)
                item = _evidence_item(
                    db, document, version, text_sha256=text_sha256,
                    start=start, end=end, excerpt=text[start:end],
                    index=start // _PAGE_CHARS, score=1.0,
                    snapshot_text=text,
                )
                item.update({
                    "citation_id": item["id"],
                    "created_by_user_id": row["created_by_user_id"],
                    "created_by_display_name": row["created_by_display_name"],
                    "created_at": row["created_at"],
                })
                item_bytes = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
                if candidate_bytes + item_bytes > batch_bytes:
                    candidate_budget_full = True
                    break
                candidate_bytes += item_bytes
                emitted_end = end
                evidence.append(item)
                if len(injection_flags) < 20:
                    hits = detect_prompt_injection(item["excerpt"])
                    if hits:
                        injection_flags.append({
                            "document_version_id": version_id,
                            "chunk_index": str(item["chunk_index"]), "match": hits[0],
                        })
            remaining -= emitted_end - start_offset
            row.update(
                status=(
                    "processed" if emitted_end == total_chars
                    else "truncated" if emitted_end else "unprocessed"
                ),
                covered_chars=emitted_end,
                unprocessed_ranges=[] if emitted_end == total_chars else [[emitted_end, total_chars]],
            )
            row.pop("reason", None)
            if emitted_end < total_chars:
                row["reason"] = "response_byte_limit" if candidate_budget_full else "batch_text_limit"

    manifest_by_id = {row["document_id"]: row for row in manifest}
    issue_rows = []
    for row in manifest:
        if row["status"] in {"missing", "failed"} or (
            row["status"] == "unprocessed" and not row.get("text_sha256")
        ):
            issue_rows.append(row)
            if len(issue_rows) == 20:
                break

    from app.services.writing_fact_check import inventory_records

    def page_payload(count: int, issue_count: int = 0) -> dict[str, Any]:
        # Admission must not acknowledge spans omitted from this response.
        for document_id, (covered, status, reason) in progress_before.items():
            row = manifest_by_id[document_id]
            row.update(
                covered_chars=covered, status=status,
                unprocessed_ranges=[[covered, row["total_chars"]]],
            )
            if reason:
                row["reason"] = reason
            else:
                row.pop("reason", None)
        page_evidence = evidence[:count]
        page_ids: dict[str, None] = {}
        for item in page_evidence:
            row = manifest_by_id[item["document_id"]]
            page_ids[row["document_id"]] = None
            covered = item["char_end"]
            row.update(
                covered_chars=covered,
                status="processed" if covered == row["total_chars"] else "truncated",
                unprocessed_ranges=[] if covered == row["total_chars"] else [[covered, row["total_chars"]]],
            )
            row.pop("reason", None)
            if covered < row["total_chars"]:
                row["reason"] = "batch_text_limit"
        counts = {
            status: sum(row["status"] == status for row in manifest)
            for status in ("processed", "missing", "failed", "unprocessed", "truncated")
        }
        has_remaining = any(
            row.get("text_sha256") and row["status"] in {"truncated", "unprocessed"}
            and row["covered_chars"] < row["total_chars"]
            for row in manifest
        )
        coverage: dict[str, Any] = {
            "expected": len(manifest), **counts,
            "complete": counts["processed"] == len(manifest),
            "text_basis": "resolved_text",
            "extraction_completeness": "unverified",
            "cumulative": True,
            "manifest_ref": f"inventory:{inventory_id}:{'0' * 64}",
            "documents": [
                *[manifest_by_id[document_id] for document_id in page_ids],
                *issue_rows[:issue_count],
            ],
            "unresolved_count": len(manifest) - counts["processed"],
            "unreported_document_count": len(manifest) - len(page_ids) - issue_count,
            "limits": {
                "document_batch_size": _DOCUMENT_BATCH_SIZE,
                "page_chars": _PAGE_CHARS,
                "document_chars_per_batch": _MAX_DOCUMENT_CHARS,
                "context_chars_per_batch": batch_chars,
                "response_bytes_per_batch": batch_bytes,
            },
        }
        gaps = [] if page_evidence else [
            "inventory_page_budget_exhausted" if has_remaining else "no_queryable_evidence"
        ]
        if not coverage["complete"]:
            gaps.append("inventory_incomplete")
        page_spans = {(item["document_version_id"], str(item["chunk_index"])) for item in page_evidence}
        # Fixed-length digest slots permit exact byte admission before persisting
        # the final cursor; trial pages must not create durable intermediate state.
        return {
            "result_ref": f"writing-inventory:{'0' * 20}",
            "evidence": page_evidence,
            "inventory_records": inventory_records({"evidence": page_evidence, "coverage": coverage}),
            "knowledge_gaps": gaps,
            "coverage": coverage,
            "next_cursor": f"inventory:{inventory_id}:{'0' * 64}" if has_remaining else None,
            "manifest_ref": f"inventory:{inventory_id}:{'0' * 64}",
            "provider": "document_inventory",
            "retrieval": {
                "retrieval_mode": "inventory",
                "retrieval_method": "scoped_document_inventory",
                "unit": "document_page",
                "pool_size": len(manifest),
                "selected_count": len(page_ids),
                "selected_chunk_count": len(page_evidence),
                "exhaustive": coverage["complete"],
                "coverage": coverage,
                "injection_flags": [
                    flag for flag in injection_flags
                    if (flag["document_version_id"], flag["chunk_index"]) in page_spans
                ],
            },
            "enrichment_text": (
                "清点覆盖率仅表示累计读取了多少当前可用提取文本，不证明原始文件提取完整；"
                "不得据此断言所有证书或字段均已覆盖。next_cursor 非空时仍有原快照文本待读取。"
            ),
            "untrusted_source": True,
            "sha256": "0" * 64,
        }

    result = page_payload(len(evidence))
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > batch_bytes:
        # Binary admission avoids repeatedly serializing each growing prefix.
        low, high = 0, len(evidence)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = page_payload(middle)
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= batch_bytes:
                low = middle
            else:
                high = middle - 1
        result = page_payload(low)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > batch_bytes:
            raise ValueError("Inventory byte budget cannot fit coverage metadata")
    admitted_count = len(result["evidence"])
    low, high = 0, len(issue_rows)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = page_payload(admitted_count, middle)
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= batch_bytes:
            low = middle
        else:
            high = middle - 1
    result = page_payload(admitted_count, low)
    state_text = json.dumps({
        "schema_version": 1, "scope_digest": scope_digest, "documents": manifest,
    }, ensure_ascii=False, sort_keys=True)
    state_sha256 = freeze_text(f"inventory_{inventory_id}", state_text)
    result["manifest_ref"] = f"inventory:{inventory_id}:{state_sha256}"
    result["coverage"]["manifest_ref"] = result["manifest_ref"]
    if result["next_cursor"] is not None:
        result["next_cursor"] = result["manifest_ref"]
    digest = hashlib.sha256(json.dumps({
        "scope_digest": scope_digest, "evidence": result["evidence"],
        "inventory_records": result["inventory_records"],
        "coverage": result["coverage"], "next_cursor": result["next_cursor"],
        "manifest_ref": result["manifest_ref"],
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    result["result_ref"] = f"writing-inventory:{digest[:20]}"
    result["sha256"] = digest
    return result


def _capture_manifest(
    db: Session, *, filters: list[Any], selected_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Freeze the full initial version set before returning any continuation."""
    manifest: list[dict[str, Any]] = []
    seen: set[str] = set()
    document_cursor = ""
    while True:
        documents = list(db.scalars(
            select(Document)
            .where(*filters, Document.id > document_cursor)
            .order_by(Document.id)
            .limit(_DOCUMENT_BATCH_SIZE)
        ))
        if not documents:
            break
        document_cursor = documents[-1].id
        page_ids = [document.id for document in documents]
        creator_names = creator_display_names(
            db, [document.created_by_user_id for document in documents],
        )
        ready = {
            document.id: version
            for document, version in ready_documents_for_scope(db, document_ids=page_ids)
        }
        not_ready_ids = [document_id for document_id in page_ids if document_id not in ready]
        latest: dict[str, DocumentVersion] = {}
        if not_ready_ids:
            latest_numbers = (
                select(
                    DocumentVersion.document_id,
                    func.max(DocumentVersion.version_number).label("version_number"),
                )
                .where(DocumentVersion.document_id.in_(not_ready_ids))
                .group_by(DocumentVersion.document_id)
                .subquery()
            )
            latest = {
                version.document_id: version
                for version in db.scalars(select(DocumentVersion).join(
                    latest_numbers,
                    and_(
                        DocumentVersion.document_id == latest_numbers.c.document_id,
                        DocumentVersion.version_number == latest_numbers.c.version_number,
                    ),
                ))
            }
        for document in documents:
            seen.add(document.id)
            version = ready.get(document.id)
            row: dict[str, Any] = {
                "document_id": document.id,
                "document_name": document.name,
                "space_id": document.space_id,
                "created_by_user_id": document.created_by_user_id,
                "created_by_display_name": creator_names.get(document.created_by_user_id),
                "created_at": iso(document.created_at),
                "document_version_id": version.id if version else None,
                "status": "unprocessed",
                "covered_chars": 0,
                "total_chars": None,
                "extraction_warning_code": getattr(version, "extraction_warning_code", None),
            }
            manifest.append(row)
            if version is None:
                stage = getattr(latest.get(document.id), "processing_stage", None)
                row.update(
                    status="failed" if stage == "failed" else "unprocessed",
                    reason="processing_failed" if stage == "failed" else "no_queryable_version",
                    processing_stage=stage,
                )
                continue
            try:
                text = _resolve_version_text(version)
                if not text or not text.strip():
                    row.update(status="missing", reason="missing_text")
                    continue
                text_sha256 = freeze_text(version.id, text)
            except Exception:
                row.update(status="failed", reason="text_or_snapshot_unavailable")
                continue
            row.update(
                text_sha256=text_sha256, total_chars=len(text),
                version_number=version.version_number,
                extraction_method=version.extraction_method,
                unprocessed_ranges=[[0, len(text)]],
            )
    for document_id in selected_ids or []:
        if document_id not in seen:
            manifest.append({
                "document_id": document_id, "document_version_id": None,
                "status": "missing", "reason": "selected_document_unavailable",
                "covered_chars": 0, "total_chars": None,
            })
    manifest.sort(key=lambda row: row["document_id"])
    return manifest
