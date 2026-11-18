"""Validate generation-time evidence and retain it with exported snapshots.

This module never searches or reads the current extracted-text sidecar. Project
membership and evidence access are separate: callers authorize the project, then
this boundary checks the acting user's current document ACL before snapshot I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.documents import Document, DocumentVersion
from app.models.writing import AgentRunBinding, WritingCandidate, WritingOperation, WritingProject, WritingToolReceipt
from app.services.authorization import PermissionService
from app.services.evidence_snapshots import parse_citation_id, read_snapshot
from app.services.image_documents import image_source_text, is_image_version
from app.services.writing_fact_check import check_claim_support, inline_citation_ids, reconcile_inventory, reconcile_inventory_reply, support_content_blocker


@dataclass(slots=True)
class EvidenceValidation:
    sources: list[dict[str, Any]]
    blockers: list[dict[str, Any]]


class EvidenceExportBlocked(ValueError):
    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        super().__init__("证据来源或正文覆盖尚未通过核验，不能正式导出或发布")
        self.blockers = blockers


def _object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _payload_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("evidence", "evidence_sources"):
        values = payload.get(key)
        if isinstance(values, list):
            items.extend(
                item for item in values
                if isinstance(item, dict) and item.get("snapshot_status") != "unavailable"
            )
    for key in ("evidence_payload", "inline"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            items.extend(_payload_evidence(nested))
    return items


def project_evidence(db: Session, project: WritingProject) -> list[dict[str, Any]]:
    """Read only server-persisted candidate/receipt/export evidence, not retrieval."""
    evidence: list[dict[str, Any]] = []
    for payload in db.scalars(select(WritingCandidate.payload_json).where(
        WritingCandidate.project_id == project.id,
    )):
        evidence.extend(_payload_evidence(_object(payload)))
    for payload in db.scalars(
        select(WritingToolReceipt.result_json)
        .join(AgentRunBinding, AgentRunBinding.platform_run_id == WritingToolReceipt.platform_run_id)
        .join(WritingOperation, WritingOperation.id == AgentRunBinding.writing_operation_id)
        .where(WritingOperation.project_id == project.id, WritingToolReceipt.status == "succeeded")
    ):
        evidence.extend(_payload_evidence(_object(payload)))
    # Auto-delivered chat drafts keep the very same validated source JSON in the
    # export operation, so later edits do not depend on a mutable chat message.
    for payload in db.scalars(select(WritingOperation.result).where(
        WritingOperation.project_id == project.id,
        WritingOperation.status == "completed",
    )):
        evidence.extend(_payload_evidence(_object(payload)))
    return evidence


def validate_evidence_sources(
    db: Session,
    *,
    user: User,
    texts: list[str],
    evidence: list[dict[str, Any]],
    space_ids: list[str],
    document_ids: list[str] | None = None,
    chat_session_id: str | None = None,
    attachment_document_ids: list[str] | None = None,
) -> EvidenceValidation:
    """Resolve only exact inline IDs against generation records and frozen bytes.

    Invalid sources remain explicit unavailable entries for editable chat drafts;
    formal callers reject ``blockers``. Never assign a citation by list position.
    """
    from app.services.chat_knowledge import require_documents_in_scope

    recorded: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        citation_id = item.get("citation_id") or item.get("id")
        if isinstance(citation_id, str):
            recorded.setdefault(citation_id, []).append(item)
    sources: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    snapshots: dict[tuple[str, str], str | None] = {}
    scope = set(space_ids)
    selected = set(document_ids) if document_ids is not None else None
    attachments = set(attachment_document_ids) if attachment_document_ids is not None else None
    authorized: dict[str, tuple[DocumentVersion, Document] | None] = {}
    for citation_id in dict.fromkeys(cid for text in texts for cid in inline_citation_ids(text)):
        parsed = parse_citation_id(citation_id)
        code = ""
        item: dict[str, Any] = {}
        snapshot: str | None = None
        if parsed is None:
            code = "malformed_citation"
        elif parsed[1] is None:
            code = "historical_snapshot_unavailable"
        elif citation_id not in recorded:
            code = "unknown_citation"
        else:
            version_id, digest, start, end = parsed
            assert digest is not None
            records = recorded[citation_id]
            item = records[0]
            if any(
                row.get("document_version_id") != version_id
                or row.get("text_sha256") != digest
                or row.get("char_start") != start
                or row.get("char_end") != end
                or row.get("document_id") != item.get("document_id")
                for row in records
            ):
                code = "citation_identity_mismatch"
            else:
                if version_id not in authorized:
                    version = db.get(DocumentVersion, version_id)
                    document = db.get(Document, version.document_id) if version is not None else None
                    allowed = bool(
                        user.status == "active" and document is not None
                        and document.status == "active"
                        and (
                            (
                                document.space_id in scope
                                and (selected is None or document.id in selected)
                            )
                            if document.space_id else (
                                bool(chat_session_id)
                                and (
                                    document.id in attachments
                                    if attachments is not None
                                    else selected is not None and document.id in selected
                                )
                                and document.temporary_cleanup_at is None
                            )
                        )
                    )
                    if allowed and version is not None and document is not None:
                        try:
                            if document.space_id:
                                PermissionService.require_space_access(
                                    db, user, document.space_id, minimum_role="reader",
                                )
                            require_documents_in_scope(
                                db, user, space_ids=space_ids, document_ids=[document.id],
                                chat_session_id=chat_session_id,
                            )
                        except HTTPException:
                            allowed = False
                    authorized[version_id] = (
                        (version, document)
                        if allowed and version is not None and document is not None
                        else None
                    )
                pair = authorized[version_id]
                if pair is None or pair[1].id != item.get("document_id"):
                    code = "source_no_longer_authorized"
                else:
                    key = (version_id, digest)
                    if key not in snapshots:
                        snapshots[key] = read_snapshot(version_id, digest)
                    snapshot = snapshots[key]
                    if snapshot is None:
                        code = "historical_snapshot_unavailable"
                    elif not 0 <= start < end <= len(snapshot):
                        code = "citation_range_invalid"
        if code:
            blockers.append({"code": code, "citation_id": citation_id})
            sources.append({"citation_id": citation_id, "snapshot_status": "unavailable", "unavailable_reason": code})
            continue
        assert parsed is not None and snapshot is not None
        _, _, start, end = parsed
        # 保留生成时的来源标签；可信标题只从当前已授权的文档记录取得，
        # 不接受 evidence payload 伪造同名字段来豁免正文数字核验。
        assert pair is not None
        source_text = image_source_text(pair[0], snapshot, db=db, start=start, end=end)
        projection: dict[str, Any] = {}
        if is_image_version(pair[0]):
            from app.services.evidence_snapshots import snapshot_page_location
            from app.services.qa_service import _evidence_support_state

            location = snapshot_page_location(db, pair[0].id, snapshot, start, end)
            # 旧图片的通用 uncertain 不是实际质量问题；仅在绑定验证后重算。
            boundary_state = item.get("support_state")
            preserved = (
                item if boundary_state in {"unsupported", "stale", "unavailable", "unlinked"}
                else None
            )
            projection["support_state"] = _evidence_support_state(
                pair[0], location, preserved, snapshot_text=snapshot,
            )
            if "excerpt" in item:
                projection["excerpt"] = source_text
        sources.append({
            **item,
            **projection,
            "citation_id": citation_id,
            "snapshot_status": "verified",
            "source_text": source_text,
            "verified_document_name": pair[1].name,
        })
    return EvidenceValidation(sources, blockers)


def validate_project_evidence(db: Session, *, user: User, project: WritingProject) -> EvidenceValidation:
    from app.repositories import writing
    from app.services.writing_scope import resolve_current_writing_scope

    scope = resolve_current_writing_scope(db, user=user, project=project)
    chapters = writing.export_chapter_view(db, project)
    evidence = project_evidence(db, project)
    result = validate_evidence_sources(
        db, user=user, texts=[text for _, text in chapters], evidence=evidence,
        space_ids=scope.space_ids, document_ids=scope.document_ids or None,
        chat_session_id=project.source_chat_session_id,
    )
    if not chapters or any(not text.strip() for _, text in chapters):
        result.blockers.append({"code": "incomplete_document"})
    # Concrete fact/field contradictions block separately from malformed source
    # identity. Unverified general semantics are not a universal export ban,
    # and unrelated retrieved documents need not be cited.
    verified = {item["citation_id"]: item for item in result.sources if item.get("snapshot_status") == "verified"}
    if scope.space_ids or scope.document_ids or result.sources:
        for title, text in chapters:
            checked = check_claim_support(text, inline_citation_ids(text), verified)
            blocker = support_content_blocker(checked)
            if blocker is not None:
                result.blockers.append({**blocker, "chapter_title": title})
    # Applied inventory provenance survives manual edits. Unpublished unrelated
    # attempts cannot veto another chapter or replace its last applied source.
    current_bodies = {
        chapter.id: chapter.content for chapter in (project.outline.chapters if project.outline else [])
    }
    seen_chapters: set[str] = set()
    for chapter_id, raw, candidate_status in db.execute(
        select(WritingCandidate.chapter_id, WritingCandidate.payload_json, WritingCandidate.status).where(
            WritingCandidate.project_id == project.id,
            WritingCandidate.kind == "section",
        ).order_by(
            WritingCandidate.published_at.desc().nulls_last(),
            WritingCandidate.created_at.desc(), WritingCandidate.id.desc(),
        )
    ):
        candidate = _object(raw)
        body = candidate.get("markdown")
        if (
            not isinstance(body, str) or chapter_id not in current_bodies
            or chapter_id in seen_chapters
            or (candidate_status != "published" and body != current_bodies[chapter_id])
        ):
            continue
        payload = candidate.get("evidence_payload")
        if not isinstance(payload, dict):
            continue
        retrieval = payload.get("retrieval")
        inventory = payload.get("provider") == "document_inventory" or (
            isinstance(retrieval, dict) and retrieval.get("retrieval_mode") == "inventory"
        )
        if not inventory:
            continue
        seen_chapters.add(chapter_id)
        coverage = payload.get("coverage")
        if inventory and (not isinstance(coverage, dict) or coverage.get("complete") is not True):
            result.blockers.append({"code": "incomplete_evidence_coverage", "coverage": coverage or {}})
        elif inventory:
            inventory_items = _payload_evidence(payload)
            inventory_validation = validate_evidence_sources(
                db, user=user,
                texts=[f"^[{item.get('citation_id') or item.get('id')}]" for item in inventory_items],
                evidence=inventory_items, space_ids=scope.space_ids,
                document_ids=scope.document_ids or None,
                chat_session_id=project.source_chat_session_id,
            )
            if inventory_validation.blockers:
                result.blockers.append({
                    "code": "incomplete_evidence_coverage",
                    "findings": inventory_validation.blockers,
                })
                continue
            reconciliation = reconcile_inventory(
                current_bodies[chapter_id], candidate.get("inventory_reconciliation"),
                {**payload, "evidence": inventory_validation.sources},
            )
            if reconciliation.get("passed") is not True:
                result.blockers.append({
                    "code": "incomplete_evidence_coverage",
                    "findings": reconciliation.get("findings") or [],
                })
    if project.source_chat_session_id and len(seen_chapters) < len(current_bodies):
        # Chat imports have no model candidate dispositions. Reconcile the live
        # text against the original request's recorded inventory, not its stale
        # success flag and not a newly searched set of documents.
        for raw in db.scalars(
            select(WritingOperation.result).where(
                WritingOperation.project_id == project.id,
                WritingOperation.status == "completed",
            ).order_by(WritingOperation.created_at.desc(), WritingOperation.id.desc())
        ):
            payload = _object(raw).get("evidence_payload")
            coverage = payload.get("coverage") if isinstance(payload, dict) else None
            if not isinstance(coverage, dict) or "record_reconciliation" not in coverage:
                continue
            inventory_items = _payload_evidence(payload)
            inventory_validation = validate_evidence_sources(
                db, user=user,
                texts=[f"^[{item.get('citation_id') or item.get('id')}]" for item in inventory_items],
                evidence=inventory_items, space_ids=scope.space_ids,
                document_ids=scope.document_ids or None,
                chat_session_id=project.source_chat_session_id,
            )
            if inventory_validation.blockers:
                result.blockers.append({
                    "code": "incomplete_evidence_coverage", "findings": inventory_validation.blockers,
                })
            else:
                reconciled = reconcile_inventory_reply(
                    "\n\n".join(text for _, text in chapters),
                    {**payload, "evidence": inventory_validation.sources},
                )
                if reconciled.get("complete") is not True:
                    result.blockers.append({
                        "code": "incomplete_evidence_coverage", "record_reconciliation": reconciled,
                    })
            break
    return result


def prepare_cited_chapters(
    chapters: Iterable[tuple[str, str]],
) -> list[tuple[str, str, list[str]]]:
    """Resolve exact citations before any presentation truncation or grouping.

    交付文件不展示引用编号与来源：这里只剔除标记，引用 ID 仅作为留痕与页面展示数据。
    """
    from app.services.markdown_docx import strip_evidence_markers

    prepared = []
    for title, content in chapters:
        text = str(content or "")
        prepared.append((
            str(title or "正文"),
            strip_evidence_markers(text),
            inline_citation_ids(text),
        ))
    return prepared
