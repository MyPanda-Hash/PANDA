from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.security import as_utc, iso, new_token, now
from app.core.config import settings
from app.models.writing import (
    AgentEventReceipt,
    AgentRunBinding,
    WritingCandidate,
    WritingChapter,
    WritingClaim,
    WritingDispatchOutbox,
    WritingOperation,
    WritingOutline,
    WritingProject,
    WritingProjectPublication,
    WritingToolReceipt,
    WritingVersion,
)

ZERO_USAGE = {
    "provider": "deterministic_writing_fixture",
    "input_tokens": 0,
    "input_uncached_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": "0",
    "latency_ms": 0,
}


def _id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def export_chapter_view(
    db: Session, project: WritingProject
) -> list[tuple[str, str]]:
    """Read-only ``(title, content)`` view of the current chapters, in order.

    Preflight must not create a ``WritingVersion``: it plans against the live
    chapter content, and the actual export takes its own snapshot.
    """
    rows = db.execute(
        select(WritingChapter.title, WritingChapter.content)
        .join(WritingOutline, WritingChapter.outline_id == WritingOutline.id)
        .where(WritingOutline.project_id == project.id)
        .order_by(WritingChapter.position)
    ).all()
    return [(str(title or ""), str(content or "")) for title, content in rows]

# Prefix per writing skill, used for the issued document number.
_DOC_PREFIXES: dict[str, str] = {
    "quote": "QT",
    "proposal": "PR",
    "bid": "BD",
    "report": "RP",
}
_DEFAULT_PREFIX = "DOC"


def document_prefix(project: WritingProject) -> str:
    from app.services.writing_brief import decode_brief

    brief = decode_brief(getattr(project, "brief_json", None))
    key = str(brief.get("document_type") or "").strip().lower()
    return _DOC_PREFIXES.get(key, _DEFAULT_PREFIX)


def assign_document_number(db: Session, project: WritingProject) -> str:
    """Mark a successful export issued, allocating its stable number only once."""
    stamp = now()
    if not project.doc_number:
        prefix = f"{document_prefix(project)}-{stamp.year}-"
        existing = db.scalars(
            select(WritingProject.doc_number).where(
                WritingProject.doc_number.is_not(None),
                WritingProject.doc_number.like(f"{prefix}%"),
            )
        )
        highest = 0
        for value in existing:
            tail = str(value).rsplit("-", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        project.doc_number = f"{prefix}{highest + 1:04d}"
    project.doc_status = "issued"
    if project.issued_at is None:
        project.issued_at = stamp
    project.updated_at = stamp
    db.flush()
    return project.doc_number


def load_space_ids(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item.strip()]


def load_document_ids(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item.strip()]


def owned_project(
    db: Session, project_id: str, owner_user_id: str
) -> WritingProject | None:
    return db.scalar(
        select(WritingProject)
        .options(
            selectinload(WritingProject.outline)
            .selectinload(WritingOutline.chapters)
            .selectinload(WritingChapter.versions),
            selectinload(WritingProject.claims),
            selectinload(WritingProject.operations),
        )
        .where(
            WritingProject.id == project_id,
            WritingProject.owner_user_id == owner_user_id,
        )
    )


def project_by_id(db: Session, project_id: str) -> WritingProject | None:
    """Load a project without any ownership filter.

    Callers MUST authorise separately — use
    ``app.services.writing_membership.require_project_role``.
    """
    return db.scalar(
        select(WritingProject)
        .options(
            selectinload(WritingProject.outline)
            .selectinload(WritingOutline.chapters)
            .selectinload(WritingChapter.versions),
            selectinload(WritingProject.claims),
            selectinload(WritingProject.operations),
        )
        .where(WritingProject.id == project_id)
    )


def required_owned_project(
    db: Session, project_id: str, owner_user_id: str
) -> WritingProject:
    project = owned_project(db, project_id, owner_user_id)
    assert project is not None
    return project


PROJECT_PAGE_SIZE = 20
PROJECT_MAX_PAGE_SIZE = 100


def _completed_export_count():
    return (
        select(func.count(WritingOperation.id))
        .where(
            WritingOperation.project_id == WritingProject.id,
            WritingOperation.kind == "export",
            WritingOperation.status == "completed",
        )
        .scalar_subquery()
    )


def _title_matches(query: str):
    """标题子串匹配；LIKE 的通配符按字面处理。"""
    needle = query.strip().lower()
    if not needle:
        return None
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(WritingProject.title).like(f"%{escaped}%", escape="\\")


def _cursor_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def encode_project_cursor(updated_at: datetime, project_id: str) -> str:
    """v1.<base64 payload>.<HMAC>，与资料库游标相同的签名规范。"""
    payload = json.dumps(
        {
            "version": 1,
            "updated_at": _cursor_utc(updated_at).isoformat(),
            "id": project_id,
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(
            settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
    ).decode().rstrip("=")
    return f"v1.{encoded}.{sig}"


def decode_project_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        version, encoded, sig = cursor.split(".")
        if version != "v1":
            raise ValueError
        expected = hmac.new(
            settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        expected_sig = base64.urlsafe_b64encode(expected).decode().rstrip("=")
        if not hmac.compare_digest(expected_sig, sig):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(padded.encode())
        if not isinstance(payload, bytes):
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        stamp = _cursor_utc(datetime.fromisoformat(data["updated_at"]))
        pid = data.get("id")
        if data["version"] != 1 or not isinstance(pid, str) or not pid:
            raise ValueError
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        raise ValueError("invalid cursor")
    return stamp, pid


def project_summary_page(
    db: Session,
    *,
    conditions: Sequence[Any],
    query: str = "",
    delivery: str = "all",
    limit: int = PROJECT_PAGE_SIZE,
    cursor: tuple[datetime, str] | None = None,
) -> tuple[list[Any], str | None]:
    """列表用计数版查询：只回计数与状态，不再取回章节/版本正文。

    按 ``updated_at, id`` 倒序并走游标分页：文稿会被持续编辑，offset 翻页在更新
    时间重排后会出现重复或漏项。
    """
    chapter_count = (
        select(func.count(WritingChapter.id))
        .where(WritingChapter.outline_id == WritingOutline.id)
        .scalar_subquery()
    )
    version_count = (
        select(func.count(WritingVersion.id))
        .join(WritingChapter, WritingVersion.chapter_id == WritingChapter.id)
        .where(WritingChapter.outline_id == WritingOutline.id)
        .scalar_subquery()
    )
    unsupported_claim_count = (
        select(func.count(WritingClaim.id))
        .where(
            WritingClaim.project_id == WritingProject.id,
            WritingClaim.support_state == "unsupported",
        )
        .scalar_subquery()
    )
    completed_export_count = _completed_export_count()

    wheres = list(conditions)
    title_filter = _title_matches(query)
    if title_filter is not None:
        wheres.append(title_filter)
    if delivery == "delivered":
        wheres.append(completed_export_count > 0)
    elif delivery == "active":
        wheres.append(completed_export_count == 0)
    if cursor is not None:
        stamp, project_id = cursor
        wheres.append(
            or_(
                WritingProject.updated_at < stamp,
                and_(
                    WritingProject.updated_at == stamp,
                    WritingProject.id < project_id,
                ),
            )
        )

    rows = list(
        db.execute(
            select(
                WritingProject.id,
                WritingProject.title,
                WritingProject.selected_space_ids,
                WritingProject.brief_json,
                WritingProject.updated_at,
                WritingOutline.status.label("outline_status"),
                chapter_count.label("chapter_count"),
                version_count.label("version_count"),
                unsupported_claim_count.label("unsupported_claim_count"),
                completed_export_count.label("completed_export_count"),
            )
            .join(WritingOutline, WritingOutline.project_id == WritingProject.id)
            .where(*wheres)
            .order_by(WritingProject.updated_at.desc(), WritingProject.id.desc())
            .limit(limit + 1)
        )
    )
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    last = page[-1]
    return page, encode_project_cursor(last.updated_at, last.id)


def list_project_summaries(
    db: Session,
    owner_user_id: str,
    *,
    query: str = "",
    delivery: str = "all",
    limit: int = PROJECT_PAGE_SIZE,
    cursor: tuple[datetime, str] | None = None,
) -> tuple[list[Any], str | None]:
    return project_summary_page(
        db,
        conditions=[WritingProject.owner_user_id == owner_user_id],
        query=query,
        delivery=delivery,
        limit=limit,
        cursor=cursor,
    )


def delete_project(db: Session, project: WritingProject) -> None:
    db.delete(project)
    db.flush()


def get_publication(
    db: Session, publication_id: str
) -> WritingProjectPublication | None:
    return db.scalar(
        select(WritingProjectPublication).where(
            WritingProjectPublication.id == publication_id,
            WritingProjectPublication.status == "active",
        )
    )


def revoke_publication(db: Session, publication: WritingProjectPublication) -> None:
    publication.status = "revoked"
    publication.revoked_at = now()
    db.flush()


def publication_snapshot(publication: WritingProjectPublication) -> dict[str, Any]:
    try:
        parsed = json.loads(publication.snapshot_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def invalidate_pi_session(project: WritingProject) -> None:
    """Drop Pi continuity after source-of-truth changes (brief/outline/spaces)."""
    project.pi_agent_session_id = None
    project.pi_session_stage = None


def create_project(
    db: Session,
    *,
    owner_user_id: str,
    title: str,
    space_ids: list[str],
    budget_limit: str | None,
    brief: dict | None = None,
    document_ids: list[str] | None = None,
    source_chat_session_id: str | None = None,
    source_chat_assistant_message_id: str | None = None,
) -> WritingProject:
    from app.services.writing_brief import encode_brief

    created = now()
    project = WritingProject(
        id=_id("wpr"),
        owner_user_id=owner_user_id,
        title=title,
        selected_space_ids=json.dumps(space_ids),
        selected_document_ids=json.dumps(document_ids or []),
        budget_limit=budget_limit,
        brief_json=encode_brief(brief or {}),
        brief_revision=1,
        source_chat_session_id=source_chat_session_id,
        source_chat_assistant_message_id=source_chat_assistant_message_id,
        created_at=created,
        updated_at=created,
    )
    db.add(project)
    db.add(
        WritingOutline(
            id=_id("wol"),
            project_id=project.id,
            status="draft",
            confirmed_at=None,
            created_at=created,
            updated_at=created,
        )
    )
    db.flush()
    return required_owned_project(db, project.id, owner_user_id)


def update_project(
    db: Session,
    project: WritingProject,
    *,
    title: str | None = None,
    space_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    budget_limit: str | None = None,
    brief: dict | None = None,
    update_budget: bool = False,
) -> WritingProject:
    from app.services.writing_brief import encode_brief, normalize_brief

    changed = False
    invalidate = False
    if title is not None and title.strip() and title.strip() != project.title:
        project.title = title.strip()
        changed = True
    if space_ids is not None:
        encoded = json.dumps(space_ids)
        if encoded != project.selected_space_ids:
            project.selected_space_ids = encoded
            changed = True
            invalidate = True
    if document_ids is not None:
        encoded = json.dumps(document_ids)
        current = getattr(project, "selected_document_ids", None) or "[]"
        if encoded != current:
            project.selected_document_ids = encoded
            changed = True
            invalidate = True
    if update_budget and budget_limit != project.budget_limit:
        project.budget_limit = budget_limit
        changed = True
    if brief is not None:
        encoded = encode_brief(normalize_brief(brief))
        if encoded != (project.brief_json or "{}"):
            project.brief_json = encoded
            project.brief_revision = (project.brief_revision or 1) + 1
            changed = True
            invalidate = True
    if changed:
        project.updated_at = now()
    if invalidate:
        invalidate_pi_session(project)
    db.flush()
    return required_owned_project(db, project.id, project.owner_user_id)


def replace_outline(
    db: Session,
    project: WritingProject,
    chapters: list[dict[str, str]],
    *,
    preserve_existing_content: bool = False,
) -> WritingProject:
    outline = project.outline
    assert outline is not None
    existing_chapters = sorted(outline.chapters, key=lambda item: item.position)
    has_versions = has_saved_versions(db, project.id)
    if has_versions and not preserve_existing_content:
        raise ValueError("已有保存版本，不能替换大纲")
    if has_versions and len(existing_chapters) != 1:
        raise ValueError("已有保存版本，不能替换大纲")
    seed_content = ""
    if preserve_existing_content:
        # 可编辑正文属于当前草稿，不是需要被新大纲删除的内容。
        # 换大纲时把它带到新大纲的第一章，避免采用候选后丢失原回答。
        seed_content = next(
            (
                chapter.content
                for chapter in existing_chapters
                if chapter.content and chapter.content.strip()
            ),
            "",
        )
    outline.status, outline.confirmed_at, outline.updated_at = "draft", None, now()
    if has_versions:
        # 兼容已有保存版本的旧项目：保留旧章节和版本，将旧正文迁移到新大纲第一章，
        # 避免用户必须重新建立文稿。
        first = existing_chapters[0]
        first.position = 1
        first.title = chapters[0]["title"]
        first.content = seed_content
        for position, chapter in enumerate(chapters[1:], start=2):
            db.add(
                WritingChapter(
                    id=_id("wch"),
                    outline_id=outline.id,
                    position=position,
                    title=chapter["title"],
                    content="",
                    created_at=outline.updated_at,
                    updated_at=outline.updated_at,
                )
            )
        project.updated_at = outline.updated_at
        invalidate_pi_session(project)
        db.flush()
        db.expire(project)
        return required_owned_project(db, project.id, project.owner_user_id)
    for chapter in existing_chapters:
        db.delete(chapter)
    db.flush()
    for position, chapter in enumerate(chapters, start=1):
        content = seed_content if position == 1 else ""
        db.add(
            WritingChapter(
                id=_id("wch"),
                outline_id=outline.id,
                position=position,
                title=chapter["title"],
                content=content,
                created_at=outline.updated_at,
                updated_at=outline.updated_at,
            )
        )
    project.updated_at = outline.updated_at
    invalidate_pi_session(project)
    db.flush()
    # The caller already holds a selectin-loaded outline collection.  Expire it so
    # its response is rebuilt from the replacement rows, not the deleted ones.
    db.expire(project)
    return required_owned_project(db, project.id, project.owner_user_id)


def confirm_outline(db: Session, project: WritingProject) -> WritingProject:
    outline = project.outline
    assert outline is not None
    changed = now()
    outline.status, outline.confirmed_at, outline.updated_at, project.updated_at = (
        "confirmed",
        changed,
        changed,
        changed,
    )
    # Outline confirmation is a stage boundary: start a fresh Pi session for document work.
    invalidate_pi_session(project)
    db.flush()
    return required_owned_project(db, project.id, project.owner_user_id)


def create_operation(
    db: Session,
    project: WritingProject,
    kind: str,
    *,
    idempotency_key: str | None = None,
    input_revision: int | None = None,
    input_payload: dict | None = None,
) -> WritingOperation:
    if idempotency_key:
        existing = db.scalar(
            select(WritingOperation).where(
                WritingOperation.project_id == project.id,
                WritingOperation.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.kind != kind:
                raise ValueError(
                    "idempotency key already belongs to another writing workflow"
                )
            if input_payload is not None:
                encoded_input = json.dumps(
                    input_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if existing.input_payload != encoded_input:
                    raise ValueError(
                        "idempotency key already belongs to different workflow input"
                    )
            return existing
    created = now()
    operation = WritingOperation(
        id=_id("wop"),
        project_id=project.id,
        kind=kind,
        status="queued",
        idempotency_key=idempotency_key,
        attempt=0,
        input_revision=input_revision,
        input_payload=json.dumps(
            input_payload or {},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        error_code=None,
        error_message_safe=None,
        last_event_cursor=0,
        usage=json.dumps(ZERO_USAGE),
        result=json.dumps({}),
        created_at=created,
        started_at=None,
        completed_at=None,
    )
    db.add(operation)
    project.updated_at = created
    db.flush()
    return operation


def start_operation(db: Session, operation: WritingOperation) -> WritingOperation:
    operation.status, operation.started_at = "running", now()
    db.flush()
    return operation


def complete_operation(
    db: Session, operation: WritingOperation, result: dict
) -> WritingOperation:
    operation.status, operation.result, operation.completed_at = (
        "completed",
        json.dumps(result),
        now(),
    )
    db.flush()
    return operation


def stop_operation(
    db: Session, operation: WritingOperation, reason: str
) -> WritingOperation:
    operation.status, operation.usage, operation.result, operation.completed_at = (
        "stopped",
        json.dumps(ZERO_USAGE),
        json.dumps({"reason": reason}),
        now(),
    )
    db.flush()
    return operation


def operation_by_id(db: Session, operation_id: str) -> WritingOperation | None:
    return db.get(WritingOperation, operation_id)


def binding_for_operation(db: Session, operation_id: str) -> AgentRunBinding | None:
    return db.scalar(
        select(AgentRunBinding).where(
            AgentRunBinding.writing_operation_id == operation_id
        )
    )


def binding_for_platform_run(
    db: Session, platform_run_id: str
) -> AgentRunBinding | None:
    return db.scalar(
        select(AgentRunBinding).where(
            AgentRunBinding.platform_run_id == platform_run_id
        )
    )


def dispatch_for_operation(
    db: Session, operation_id: str
) -> WritingDispatchOutbox | None:
    return db.scalar(
        select(WritingDispatchOutbox).where(
            WritingDispatchOutbox.operation_id == operation_id
        )
    )


def dispatch_by_id(db: Session, outbox_id: str) -> WritingDispatchOutbox | None:
    return db.get(WritingDispatchOutbox, outbox_id)


def record_agent_event(
    db: Session,
    *,
    platform_run_id: str,
    event_id: int,
    event_type: str,
    payload_sha256: str,
    payload_json: str | None = None,
) -> AgentEventReceipt:
    existing = db.scalar(
        select(AgentEventReceipt).where(
            AgentEventReceipt.platform_run_id == platform_run_id,
            AgentEventReceipt.event_id == event_id,
        )
    )
    if existing:
        if existing.payload_sha256 != payload_sha256:
            raise ValueError("platform event id reused with different payload")
        return existing
    receipt = AgentEventReceipt(
        id=_id("aer"),
        platform_run_id=platform_run_id,
        event_id=event_id,
        event_type=event_type,
        payload_sha256=payload_sha256,
        payload_json=payload_json or "{}",
        created_at=now(),
    )
    db.add(receipt)
    db.flush()
    return receipt


def list_agent_events(
    db: Session,
    *,
    platform_run_id: str,
    after_event_id: int = 0,
    limit: int = 200,
) -> list[AgentEventReceipt]:
    return list(
        db.scalars(
            select(AgentEventReceipt)
            .where(
                AgentEventReceipt.platform_run_id == platform_run_id,
                AgentEventReceipt.event_id > after_event_id,
            )
            .order_by(AgentEventReceipt.event_id.asc())
            .limit(max(1, min(limit, 500)))
        )
    )


#: Event type carrying one chapter's latest cumulative preview snapshot.
PREVIEW_EVENT_TYPE = "chapter.preview.snapshot"


def chapter_preview_events(
    db: Session, *, platform_run_id: str
) -> list[AgentEventReceipt]:
    """Retained ``chapter.preview.snapshot`` events, oldest first.

    Superseded snapshots are dropped on write, so this is at most one row per
    chapter of the run — never a growing list of full-content histories.
    """
    return list(
        db.scalars(
            select(AgentEventReceipt)
            .where(
                AgentEventReceipt.platform_run_id == platform_run_id,
                AgentEventReceipt.event_type == PREVIEW_EVENT_TYPE,
            )
            .order_by(AgentEventReceipt.event_id.asc())
        )
    )


def delete_agent_event(db: Session, receipt: AgentEventReceipt) -> None:
    db.delete(receipt)
    db.flush()


def bind_platform_run(
    db: Session,
    operation: WritingOperation,
    *,
    platform_run_id: str,
    team_id: str,
    team_version: int,
    status: str,
) -> AgentRunBinding:
    existing = binding_for_operation(db, operation.id)
    if existing:
        if existing.platform_run_id != platform_run_id:
            raise ValueError("operation already bound to another platform run")
        return existing
    binding = AgentRunBinding(
        id=_id("arb"),
        writing_operation_id=operation.id,
        platform_run_id=platform_run_id,
        team_id=team_id,
        team_version=team_version,
        status=status,
        last_event_cursor=0,
        manifest_sha256=None,
        usage_summary="{}",
        created_at=now(),
        finished_at=None,
    )
    db.add(binding)
    if operation.status not in {"cancelling", "cancelled"}:
        operation.status = "running" if status == "running" else "queued"
    db.flush()
    return binding


def fail_dispatch(
    db: Session, operation: WritingOperation, code: str, message: str
) -> None:
    operation.status = "failed"
    operation.error_code = code
    operation.error_message_safe = message[:500]
    operation.completed_at = now()
    db.flush()


def enqueue_dispatch(db: Session, operation: WritingOperation) -> WritingDispatchOutbox:
    existing = db.scalar(
        select(WritingDispatchOutbox).where(
            WritingDispatchOutbox.operation_id == operation.id
        )
    )
    if existing:
        return existing
    created = now()
    outbox = WritingDispatchOutbox(
        id=_id("wdo"),
        operation_id=operation.id,
        status="pending",
        attempt=0,
        next_attempt_at=created,
        lease_owner=None,
        lease_expires_at=None,
        last_error_safe=None,
        created_at=created,
        updated_at=created,
    )
    db.add(outbox)
    db.flush()
    return outbox


def try_acquire_project_pi_lease(
    db: Session,
    project_id: str,
    *,
    worker_id: str,
    operation_id: str,
    lease_seconds: int,
) -> bool:
    """Serialize Pi writing work per project. Returns False if another lease is active."""
    project = db.scalar(
        select(WritingProject).where(WritingProject.id == project_id).with_for_update()
    )
    if project is None:
        return False
    current = now()
    expires = (
        as_utc(project.pi_lease_expires_at) if project.pi_lease_expires_at else None
    )
    held = (
        expires is not None
        and expires > current
        and project.pi_lease_owner
        and project.pi_lease_operation_id
    )
    if held and (
        project.pi_lease_operation_id != operation_id
        or project.pi_lease_owner != worker_id
    ):
        return False
    project.pi_lease_owner = worker_id
    project.pi_lease_operation_id = operation_id
    project.pi_lease_expires_at = current + timedelta(seconds=max(1, lease_seconds))
    db.flush()
    return True


def renew_project_pi_lease(
    db: Session,
    project_id: str,
    *,
    worker_id: str,
    operation_id: str,
    lease_seconds: int,
) -> None:
    project = db.get(WritingProject, project_id)
    if project is None:
        return
    if (
        project.pi_lease_owner != worker_id
        or project.pi_lease_operation_id != operation_id
    ):
        return
    project.pi_lease_expires_at = now() + timedelta(seconds=max(1, lease_seconds))
    db.flush()


def release_project_pi_lease(
    db: Session,
    *,
    operation_id: str,
    worker_id: str,
) -> None:
    operation = operation_by_id(db, operation_id)
    if operation is None:
        return
    project = db.get(WritingProject, operation.project_id)
    if project is None:
        return
    if (
        project.pi_lease_operation_id == operation_id
        and project.pi_lease_owner == worker_id
    ):
        project.pi_lease_owner = None
        project.pi_lease_operation_id = None
        project.pi_lease_expires_at = None
        db.flush()


def claim_dispatch(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    max_attempts: int = 5,
    serialize_by_project: bool = False,
) -> WritingDispatchOutbox | None:
    current = now()
    exhausted = list(
        db.scalars(
            select(WritingDispatchOutbox)
            .where(
                WritingDispatchOutbox.status == "claimed",
                WritingDispatchOutbox.attempt >= max_attempts,
                WritingDispatchOutbox.lease_expires_at <= current,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for stale in exhausted:
        stale_owner = stale.lease_owner
        stale.status = "failed"
        stale.lease_owner = None
        stale.lease_expires_at = None
        stale.last_error_safe = "Writing dispatch worker attempts exhausted"
        stale.updated_at = current
        stale_operation = operation_by_id(db, stale.operation_id)
        if stale_operation is not None:
            stale_operation.status = "failed"
            stale_operation.error_code = "writing_dispatch_exhausted"
            stale_operation.error_message_safe = stale.last_error_safe
            stale_operation.completed_at = current
        if stale_owner:
            release_project_pi_lease(
                db, operation_id=stale.operation_id, worker_id=stale_owner
            )
    skipped_ids: list[str] = []
    while True:
        statement = (
            select(WritingDispatchOutbox)
            .where(
                WritingDispatchOutbox.attempt < max_attempts,
                (
                    (
                        WritingDispatchOutbox.status.in_(["pending", "retry"])
                        & (WritingDispatchOutbox.next_attempt_at <= current)
                    )
                    | (
                        (WritingDispatchOutbox.status == "claimed")
                        & (WritingDispatchOutbox.lease_expires_at <= current)
                    )
                ),
            )
            .order_by(WritingDispatchOutbox.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if skipped_ids:
            statement = statement.where(WritingDispatchOutbox.id.notin_(skipped_ids))
        outbox = db.scalar(statement)
        if outbox is None:
            return None
        operation = operation_by_id(db, outbox.operation_id)
        if serialize_by_project and operation is not None:
            acquired = try_acquire_project_pi_lease(
                db,
                operation.project_id,
                worker_id=worker_id,
                operation_id=operation.id,
                lease_seconds=lease_seconds,
            )
            if not acquired:
                # Defer this outbox briefly; try another project's work.
                outbox.next_attempt_at = current + timedelta(seconds=2)
                outbox.updated_at = current
                skipped_ids.append(outbox.id)
                db.flush()
                continue
        outbox.status = "claimed"
        outbox.attempt += 1
        outbox.lease_owner = worker_id
        outbox.lease_expires_at = current + timedelta(seconds=max(1, lease_seconds))
        outbox.updated_at = current
        if operation is not None:
            operation.status = "dispatching"
            operation.attempt = outbox.attempt
        db.flush()
        return outbox


def mark_dispatch_succeeded(
    db: Session, outbox: WritingDispatchOutbox, *, worker_id: str
) -> None:
    if outbox.status != "claimed" or outbox.lease_owner != worker_id:
        raise ValueError("dispatch outbox lease is not owned by worker")
    outbox.status = "dispatched"
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    outbox.last_error_safe = None
    outbox.updated_at = now()
    release_project_pi_lease(db, operation_id=outbox.operation_id, worker_id=worker_id)
    db.flush()


def mark_dispatch_retry(
    db: Session,
    outbox: WritingDispatchOutbox,
    *,
    worker_id: str,
    error_message_safe: str,
    max_attempts: int = 5,
    error_code: str | None = None,
) -> None:
    if outbox.status != "claimed" or outbox.lease_owner != worker_id:
        raise ValueError("dispatch outbox lease is not owned by worker")
    operation = operation_by_id(db, outbox.operation_id)
    exhausted = outbox.attempt >= max_attempts
    outbox.status = "failed" if exhausted else "retry"
    outbox.next_attempt_at = now() + timedelta(
        seconds=min(300, 2 ** min(outbox.attempt, 8))
    )
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    outbox.last_error_safe = error_message_safe[:500]
    outbox.updated_at = now()
    if operation is not None:
        operation.status = "failed" if exhausted else "queued"
        operation.error_code = (
            "writing_dispatch_exhausted" if exhausted else (error_code or None)
        )
        operation.error_message_safe = error_message_safe[:500] if exhausted else None
        operation.completed_at = now() if exhausted else None
    release_project_pi_lease(db, operation_id=outbox.operation_id, worker_id=worker_id)
    db.flush()


def cancel_pending_dispatch(
    db: Session, operation_id: str
) -> WritingDispatchOutbox | None:
    outbox = db.scalar(
        select(WritingDispatchOutbox).where(
            WritingDispatchOutbox.operation_id == operation_id
        )
    )
    if outbox and outbox.status in {"pending", "retry"}:
        outbox.status = "cancelled"
        outbox.updated_at = now()
        db.flush()
    return outbox


def mark_claimed_dispatch_cancelled(
    db: Session, outbox: WritingDispatchOutbox, *, worker_id: str
) -> None:
    if outbox.status != "claimed" or outbox.lease_owner != worker_id:
        raise ValueError("dispatch outbox lease is not owned by worker")
    outbox.status = "cancelled"
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    outbox.updated_at = now()
    release_project_pi_lease(db, operation_id=outbox.operation_id, worker_id=worker_id)
    db.flush()


def tool_receipt(
    db: Session, platform_run_id: str, action_key: str
) -> WritingToolReceipt | None:
    return db.scalar(
        select(WritingToolReceipt).where(
            WritingToolReceipt.platform_run_id == platform_run_id,
            WritingToolReceipt.action_key == action_key,
        )
    )


def save_tool_result(
    db: Session,
    *,
    platform_run_id: str,
    tool_call_id: str,
    action_key: str,
    tool_name: str,
    request_sha256: str,
    result: dict,
) -> WritingToolReceipt:
    existing = tool_receipt(db, platform_run_id, action_key)
    if existing:
        if existing.request_sha256 != request_sha256:
            raise ValueError("action_key reused with different request")
        existing.attempt_count += 1
        db.flush()
        return existing
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    receipt = WritingToolReceipt(
        id=_id("wtr"),
        platform_run_id=platform_run_id,
        tool_call_id=tool_call_id,
        action_key=action_key,
        tool_name=tool_name,
        request_sha256=request_sha256,
        status="succeeded",
        result_ref=result.get("result_ref"),
        result_sha256=result.get("sha256"),
        result_json=encoded,
        attempt_count=1,
        error_code=None,
        created_at=now(),
        completed_at=now(),
    )
    db.add(receipt)
    db.flush()
    return receipt


def delete_tool_receipt(db: Session, *, platform_run_id: str, action_key: str) -> None:
    """Drop a cached tool result that no longer passes its caller's contract."""
    receipt = tool_receipt(db, platform_run_id, action_key)
    if receipt is not None:
        db.delete(receipt)
        db.flush()


def recorded_evidence_ids(db: Session, project_id: str) -> set[str]:
    """Every citation id the server recorded for this project's runs.

    ``platform_run_id`` lives on ``AgentRunBinding`` (not on the operation), so
    the join is the only correct way to attribute receipts to a project.
    """
    run_ids = db.scalars(
        select(AgentRunBinding.platform_run_id)
        .join(
            WritingOperation,
            WritingOperation.id == AgentRunBinding.writing_operation_id,
        )
        .where(WritingOperation.project_id == project_id)
    )
    recorded: set[str] = set()
    for run_id in run_ids:
        recorded.update(evidence_ids_for_run(db, run_id))
    # Chat imports and automatic DOCX drafts have no AgentRunBinding. Their
    # completed operation stores the exact sources already validated at import,
    # which must remain recognizable when access is later revoked. Do not read
    # source bytes here or accept model-declared/retrieved-only citation arrays.
    from app.services.evidence_snapshots import parse_citation_id

    for raw in db.scalars(select(WritingOperation.result).where(
        WritingOperation.project_id == project_id,
        WritingOperation.status == "completed",
        WritingOperation.kind.in_(("export", "manual_save")),
    )):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        sources = payload.get("evidence_sources") if isinstance(payload, dict) else None
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict) or source.get("snapshot_status") != "verified":
                continue
            citation_id = source.get("citation_id") or source.get("id")
            parsed = parse_citation_id(citation_id) if isinstance(citation_id, str) else None
            if parsed is None or parsed[1] is None:
                continue
            version_id, digest, start, end = parsed
            if (
                source.get("document_version_id") == version_id
                and source.get("text_sha256") == digest
                and source.get("char_start") == start
                and source.get("char_end") == end
            ):
                recorded.add(citation_id)
    return recorded


def evidence_ids_for_run(db: Session, platform_run_id: str) -> set[str]:
    receipts = db.scalars(
        select(WritingToolReceipt).where(
            WritingToolReceipt.platform_run_id == platform_run_id,
            WritingToolReceipt.tool_name == "search_evidence",
            WritingToolReceipt.status == "succeeded",
        )
    )
    evidence_ids: set[str] = set()
    for receipt in receipts:
        try:
            inline = json.loads(receipt.result_json).get("inline", {})
        except (AttributeError, json.JSONDecodeError):
            continue
        evidence = inline.get("evidence", []) if isinstance(inline, dict) else []
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            for key in ("id", "citation_id"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    evidence_ids.add(value)
    return evidence_ids


def save_candidate(
    db: Session,
    *,
    operation: WritingOperation,
    platform_run_id: str,
    tool_call_id: str,
    kind: str,
    chapter_id: str | None,
    payload: dict,
    origin: str = "agent",
    author_user_id: str | None = None,
) -> WritingCandidate:
    existing = db.scalar(
        select(WritingCandidate).where(
            WritingCandidate.writing_operation_id == operation.id,
            WritingCandidate.kind == kind,
            WritingCandidate.source_tool_call_id == tool_call_id,
        )
    )
    if existing is not None:
        return existing
    revision = (
        db.scalar(
            select(func.count(WritingCandidate.id)).where(
                WritingCandidate.writing_operation_id == operation.id,
                WritingCandidate.kind == kind,
                WritingCandidate.chapter_id == chapter_id,
            )
        )
        or 0
    ) + 1
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )

    candidate = WritingCandidate(
        id=_id("wca"),
        writing_operation_id=operation.id,
        project_id=operation.project_id,
        chapter_id=chapter_id,
        kind=kind,
        candidate_revision=revision,
        source_platform_run_id=platform_run_id,
        source_tool_call_id=tool_call_id,
        payload_json=encoded,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        status="pending",
        origin=origin,
        author_user_id=author_user_id,
        created_at=now(),
        published_at=None,
    )
    db.add(candidate)
    db.flush()
    return candidate


def save_human_suggestion(
    db: Session,
    project: WritingProject,
    chapter: WritingChapter,
    *,
    author_user_id: str,
    markdown: str,
) -> tuple[WritingOperation, WritingCandidate]:
    """Record a person's proposed rewrite as a pending candidate.

    A human suggestion is deliberately the same object as an agent candidate so
    accepting or rejecting it reuses ``publish_section_candidate`` /
    ``reject_candidate``, including the staleness guard. It still creates a
    completed ``WritingOperation`` because every body change in this system is
    traceable to one; its usage is zero, so token statistics are untouched.
    """
    operation = complete_operation(
        db,
        start_operation(db, create_operation(db, project, "human_suggestion")),
        {"chapter_id": chapter.id, "origin": "human"},
    )
    candidate = save_candidate(
        db,
        operation=operation,
        platform_run_id=f"human:{operation.id}",
        tool_call_id=f"human:{new_token(10)}",
        kind="section",
        chapter_id=chapter.id,
        payload={
            "markdown": markdown,
            "base_revision": chapter_revision(chapter),
        },
        origin="human",
        author_user_id=author_user_id,
    )
    return operation, candidate


def candidates_for_project(
    db: Session, project_id: str, *, kind: str | None = None
) -> list[WritingCandidate]:
    statement = select(WritingCandidate).where(
        WritingCandidate.project_id == project_id
    )
    if kind is not None:
        statement = statement.where(WritingCandidate.kind == kind)
    return list(
        db.scalars(
            statement.order_by(
                WritingCandidate.created_at.desc(),
                WritingCandidate.candidate_revision.desc(),
            )
        )
    )


def latest_section_candidate(
    db: Session, *, operation_id: str, chapter_id: str
) -> WritingCandidate | None:
    """Newest section candidate one operation produced for a chapter."""
    return db.scalar(
        select(WritingCandidate)
        .where(
            WritingCandidate.writing_operation_id == operation_id,
            WritingCandidate.kind == "section",
            WritingCandidate.chapter_id == chapter_id,
        )
        .order_by(WritingCandidate.candidate_revision.desc())
        .limit(1)
    )


def candidate_for_project(
    db: Session, project_id: str, candidate_id: str
) -> WritingCandidate | None:
    return db.scalar(
        select(WritingCandidate).where(
            WritingCandidate.id == candidate_id,
            WritingCandidate.project_id == project_id,
        )
    )


def reject_candidate(candidate: WritingCandidate) -> WritingCandidate:
    if candidate.status != "pending":
        raise ValueError("candidate is not rejectable")
    candidate.status = "rejected"
    return candidate


def publish_outline_candidate(
    db: Session, project: WritingProject, candidate: WritingCandidate
) -> WritingProject:
    if candidate.kind != "outline" or candidate.status != "pending":
        raise ValueError("outline candidate is not publishable")
    try:
        payload = json.loads(candidate.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("outline candidate payload is invalid") from exc
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("outline candidate has no chapters")
    normalized = []
    for item in chapters:
        title = item.get("title") if isinstance(item, dict) else None
        if not isinstance(title, str) or not title.strip():
            raise ValueError("outline candidate contains an invalid chapter")
        normalized.append({"title": title.strip()})
    updated = replace_outline(db, project, normalized, preserve_existing_content=True)
    candidate.status = "published"
    candidate.published_at = now()
    db.flush()
    return updated


def _align_datetimes(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif right.tzinfo is None and left.tzinfo is not None:
        right = right.replace(tzinfo=left.tzinfo)
    return left, right


def candidate_base_revision(candidate: WritingCandidate) -> int | None:
    """The chapter revision a candidate was generated from, when recorded."""
    try:
        payload = json.loads(candidate.payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("base_revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def is_section_candidate_stale(
    chapter: WritingChapter, candidate: WritingCandidate
) -> bool:
    """Whether the chapter moved on after this candidate was generated.

    A candidate records the chapter revision the model actually read, so an edit
    that landed *while* it was writing is caught even though the candidate row is
    newer than the edit. Older candidates without that record fall back to the
    timestamp comparison.
    """
    base_revision = candidate_base_revision(candidate)
    if base_revision is not None:
        return chapter_revision(chapter) != base_revision
    chapter_updated, candidate_created = _align_datetimes(
        chapter.updated_at, candidate.created_at
    )
    return chapter_updated > candidate_created


def publish_section_candidate(
    db: Session,
    project: WritingProject,
    candidate: WritingCandidate,
    *,
    force: bool = False,
) -> WritingVersion:
    if candidate.kind != "section" or candidate.status != "pending":
        raise ValueError("section candidate is not publishable")
    if not candidate.chapter_id:
        raise ValueError("section candidate has no chapter")
    chapter = chapter_for_project(db, project.id, candidate.chapter_id)
    if chapter is None:
        raise ValueError("section candidate chapter no longer exists")
    if not force and is_section_candidate_stale(chapter, candidate):
        raise ValueError(
            "该章节在候选生成后又被修改过。直接应用会覆盖当前正文；如确认覆盖请强制应用。"
        )
    try:
        payload = json.loads(candidate.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("section candidate payload is invalid") from exc
    markdown = payload.get("markdown")
    if not isinstance(markdown, str):
        raise ValueError("section candidate has no markdown")
    changed = now()
    current_content_is_versioned = (
        db.scalar(
            select(WritingVersion.id)
            .where(
                WritingVersion.chapter_id == chapter.id,
                WritingVersion.content == chapter.content,
            )
            .limit(1)
        )
        is not None
    )
    preserve_current = bool(chapter.content.strip()) and not current_content_is_versioned
    if preserve_current:
        db.add(
            WritingVersion(
                id=_id("wve"),
                chapter_id=chapter.id,
                content=chapter.content,
                kind="regeneration_backup",
                created_at=changed,
            )
        )
    published_at = changed + timedelta(microseconds=1) if preserve_current else changed
    _advance_chapter(project, chapter, markdown, editor_user_id=None)
    chapter.updated_at = published_at
    project.updated_at = published_at
    version = WritingVersion(
        id=_id("wve"),
        chapter_id=chapter.id,
        content=markdown,
        kind="agent_candidate",
        created_at=published_at,
    )
    db.add(version)
    candidate.status = "published"
    candidate.published_at = published_at
    db.flush()
    return version


def add_claim(
    db: Session, project: WritingProject, text: str, support_state: str
) -> WritingClaim:
    claim = WritingClaim(
        id=_id("wcl"),
        project_id=project.id,
        text=text,
        support_state=support_state,
        created_at=now(),
    )
    db.add(claim)
    db.flush()
    return claim


def unsupported_claim_ids(db: Session, project_id: str) -> list[str]:
    return list(
        db.scalars(
            select(WritingClaim.id)
            .where(
                WritingClaim.project_id == project_id,
                WritingClaim.support_state == "unsupported",
            )
            .order_by(WritingClaim.id)
        )
    )


def has_saved_versions(db: Session, project_id: str) -> bool:
    statement = (
        select(WritingVersion.id)
        .join(WritingChapter, WritingVersion.chapter_id == WritingChapter.id)
        .join(WritingOutline, WritingChapter.outline_id == WritingOutline.id)
        .where(WritingOutline.project_id == project_id)
        .limit(1)
    )
    return db.scalar(statement) is not None


def chapter_has_versions(db: Session, chapter_id: str) -> bool:
    """Whether this chapter already has saved history worth protecting.

    A chapter whose body was cleared still counts: the human edits behind those
    versions must not be overwritten by an automatic regeneration.
    """
    statement = (
        select(WritingVersion.id)
        .where(WritingVersion.chapter_id == chapter_id)
        .limit(1)
    )
    return db.scalar(statement) is not None


def chapter_for_project(
    db: Session, project_id: str, chapter_id: str
) -> WritingChapter | None:
    return db.scalar(
        select(WritingChapter)
        .join(WritingOutline)
        .where(WritingChapter.id == chapter_id, WritingOutline.project_id == project_id)
    )


def seed_chapter(
    db: Session, project: WritingProject, chapter_id: str, content: str
) -> WritingChapter | None:
    """写入从聊天沉淀来的可编辑初稿，但不创建锁定大纲的版本快照。"""
    chapter = chapter_for_project(db, project.id, chapter_id)
    if not chapter:
        return None
    changed = now()
    _advance_chapter(project, chapter, content, editor_user_id=None)
    chapter.updated_at, project.updated_at = changed, changed
    db.flush()
    return chapter


class ChapterRevisionConflict(RuntimeError):
    """Another editor saved this chapter since the caller loaded it."""

    def __init__(self, chapter: WritingChapter) -> None:
        super().__init__("chapter revision conflict")
        self.chapter = chapter


def chapter_revision(chapter: WritingChapter) -> int:
    """Revision of a chapter, tolerating rows written before the column existed."""
    return int(getattr(chapter, "revision", None) or 1)


def project_content_hash(project: WritingProject) -> str:
    """Stable hash of the whole body — what an approval is an approval OF."""
    outline = project.outline
    chapters = (
        sorted(outline.chapters, key=lambda item: item.position) if outline else []
    )
    payload = json.dumps(
        [
            {"id": chapter.id, "title": chapter.title, "content": chapter.content}
            for chapter in chapters
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _advance_chapter(
    project: WritingProject,
    chapter: WritingChapter,
    content: str,
    *,
    editor_user_id: str | None,
) -> None:
    """Apply new content and bump the optimistic-concurrency revision.

    Every path that writes ``WritingChapter.content`` must go through here so
    the revision cannot drift from the content and changed documents become drafts.
    """
    if chapter.content != content:
        project.doc_status = "draft"
    chapter.content = content
    chapter.revision = chapter_revision(chapter) + 1
    if editor_user_id:
        chapter.last_editor_user_id = editor_user_id


def active_lock_holder(chapter: WritingChapter) -> str | None:
    """User id holding a still-valid advisory edit lock, else ``None``."""
    holder = getattr(chapter, "edit_lock_user_id", None)
    expires_at = getattr(chapter, "edit_lock_expires_at", None)
    if not holder or expires_at is None:
        return None
    return holder if as_utc(expires_at) > now() else None


def acquire_chapter_edit_lock(
    db: Session, chapter: WritingChapter, user_id: str, *, ttl_seconds: int = 120
) -> WritingChapter:
    """Take or renew the advisory edit lock.

    Advisory on purpose: it tells other editors someone is typing, it never
    blocks a save. Concurrency safety comes from the revision check in
    ``save_chapter``, not from this lock. An expired lock is simply taken over.
    """
    holder = active_lock_holder(chapter)
    if holder is not None and holder != user_id:
        return chapter
    chapter.edit_lock_user_id = user_id
    chapter.edit_lock_expires_at = now() + timedelta(seconds=max(30, ttl_seconds))
    db.flush()
    return chapter


def release_chapter_edit_lock(
    db: Session, chapter: WritingChapter, user_id: str
) -> WritingChapter:
    if getattr(chapter, "edit_lock_user_id", None) == user_id:
        chapter.edit_lock_user_id = None
        chapter.edit_lock_expires_at = None
        db.flush()
    return chapter


def save_chapter(
    db: Session,
    project: WritingProject,
    chapter_id: str,
    content: str,
    *,
    base_revision: int | None = None,
    editor_user_id: str | None = None,
) -> WritingVersion | None:
    """Persist chapter content.

    ``base_revision`` is the revision the caller edited from. When provided and
    it no longer matches the stored revision, another editor got there first and
    ``ChapterRevisionConflict`` is raised instead of silently overwriting their
    work. When omitted the call keeps the historical last-writer-wins behaviour,
    so clients that predate multi-editor support are not broken.
    """
    chapter = chapter_for_project(db, project.id, chapter_id)
    if not chapter:
        return None
    if base_revision is not None:
        # Compare against the database, not an earlier ORM snapshot. The matching
        # UPDATE holds the row's write lock until this transaction commits; the
        # shared content writer below performs the single revision increment.
        guarded = db.execute(
            update(WritingChapter)
            .where(
                WritingChapter.id == chapter.id,
                WritingChapter.revision == base_revision,
            )
            .values(revision=base_revision)
            .execution_options(synchronize_session=False)
        )
        db.refresh(chapter)
        if guarded.rowcount != 1:
            raise ChapterRevisionConflict(chapter)
    if chapter.content != content:
        invalidate_pi_session(project)
    changed = now()
    _advance_chapter(project, chapter, content, editor_user_id=editor_user_id)
    chapter.updated_at, project.updated_at = changed, changed
    version = WritingVersion(
        id=_id("wve"),
        chapter_id=chapter.id,
        content=content,
        kind="manual_save",
        created_at=changed,
    )
    db.add(version)
    db.flush()
    return version


def restore_chapter_version(
    db: Session,
    project: WritingProject,
    chapter_id: str,
    source_version_id: str,
    current_content: str,
) -> tuple[WritingVersion, WritingVersion | None] | None:
    chapter = chapter_for_project(db, project.id, chapter_id)
    if not chapter:
        return None
    source = db.scalar(
        select(WritingVersion).where(
            WritingVersion.id == source_version_id,
            WritingVersion.chapter_id == chapter.id,
        )
    )
    if not source:
        return None

    changed = now()
    backup = None
    if chapter.content != current_content:
        backup = WritingVersion(
            id=_id("wve"),
            chapter_id=chapter.id,
            content=current_content,
            kind="restore_backup",
            created_at=changed,
        )
        db.add(backup)

    restored_at = changed + timedelta(microseconds=1)
    if chapter.content != source.content:
        invalidate_pi_session(project)
    _advance_chapter(project, chapter, source.content, editor_user_id=None)
    chapter.updated_at = restored_at
    project.updated_at = restored_at
    restored = WritingVersion(
        id=_id("wve"),
        chapter_id=chapter.id,
        content=source.content,
        kind="version_restore",
        created_at=restored_at,
    )
    db.add(restored)
    db.flush()
    return restored, backup


def generate_chapters(db: Session, project: WritingProject) -> list[WritingVersion]:
    outline = project.outline
    assert outline is not None
    changed = now()
    versions: list[WritingVersion] = []
    for chapter in sorted(outline.chapters, key=lambda item: item.position):
        # This deterministic fallback deliberately makes no external factual
        # claim.  A completed operation therefore always has persisted content.
        content = (
            chapter.content.strip()
            or f"## {chapter.title}\n\n待补充：请基于已授权知识空间完善本节。"
        )
        _advance_chapter(project, chapter, content, editor_user_id=None)
        chapter.updated_at = changed
        version = WritingVersion(
            id=_id("wve"),
            chapter_id=chapter.id,
            content=content,
            kind="batch_generated",
            created_at=changed,
        )
        db.add(version)
        versions.append(version)
    project.updated_at = changed
    db.flush()
    return versions
