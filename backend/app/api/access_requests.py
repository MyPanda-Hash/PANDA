"""Access-request diagnosis: may this caller ask for something, and for what?

``POST /access-requests:resolve`` is read-only: it never grants anything and it
never returns content. Its whole safety story is one ordering rule —

    1. validate the anchor (visibility),
    2. decide citation membership against the anchor's *server-verified* set
       (live: current text intersected with recorded receipts; publication:
       the column frozen at publish time),
    3. only then open ``DocumentVersion`` / ``Document`` rows.

Step 2 must not touch those tables. A citation id is accepted from the client,
so if membership were decided by looking the id up, the endpoint would become
an existence oracle: probe any version id, read ``stale`` vs ``out_of_scope``
off the answer. Forged markers are excluded without a single row read.

Which source counts as "verified" differs by anchor and is taken from the
design's §6.3 table — never recomputed where the design says "frozen".
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import transaction
from app.models.access import AccessRequest, KnowledgeSpace
from app.models.auth import User
from app.models.documents import Document, DocumentVersion
from app.models.writing import WritingProject, WritingProjectPublication
from app.repositories import chat as chat_repo
from app.repositories import users, writing as writing_repo
from app.schemas.access_requests import (
    AccessRequestResolveRequest,
    AccessRequestResolveResponse,
    AccessTargetResponse,
    ChatPublicationSource,
    DiscoverySource,
    WritingPublicationSource,
    WritingSource,
)
from app.services.access_requests import (
    TARGET_SPACE,
    load_bounded_items,
)
from app.services.authorization import PermissionService
from app.services.chat_knowledge import require_documents_in_scope
from app.services.publication_access import can_cite_publication, publication_space_ids
from app.services.writing_evidence import (
    normalize_citation_id,
    split_citation_span,
    verified_citation_ids,
)
from app.services.writing_membership import require_project_role

router = APIRouter(tags=["access-requests"])

#: One shape for every refusal: a missing anchor, an invisible anchor and a
#: citation outside the verified set are indistinguishable on the wire.
def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="资源不存在")


@dataclass(frozen=True, slots=True)
class _Candidates:
    """The verified citation ids for one anchor plus how to reason about scope."""

    citation_ids: set[str]
    project: WritingProject | None = None


def _space_reader(db: Session, user: User, space_id: str) -> bool:
    """Read access without raising — used to *describe* state, not to gate."""
    return bool(
        PermissionService.check_space_owner(db, space_id, user.id)
        or PermissionService.has_space_member_role(
            db, user, space_id, minimum_role="reader"
        )
    )


def _open_plan(db: Session, space_id: str) -> tuple[KnowledgeSpace, str | None] | None:
    space = db.get(KnowledgeSpace, space_id)
    if space is None or space.status != "active":
        return None
    owner = users.get_by_id(db, space.owner_user_id)
    display = None
    if owner is not None:
        display = (owner.display_name or owner.username or owner.id).strip() or None
    return space, display


def _citation_document(
    db: Session, citation_id: str
) -> tuple[DocumentVersion, Document] | None:
    """The one place that opens a citation's version and document rows.

    Kept as a named seam so tests can assert it is *not* reached for citations
    that failed the membership check.
    """
    parts = split_citation_span(citation_id)
    if parts is None:
        return None
    version = db.get(DocumentVersion, parts[0])
    if version is None:
        return None
    document = db.get(Document, version.document_id)
    if document is None or document.status != "active":
        return None
    return version, document


# --- anchors -----------------------------------------------------------------


def _writing_candidates(db: Session, user: User, project_id: str) -> _Candidates:
    project = require_project_role(db, project_id, user, minimum_role="viewer")
    if project.outline is None:
        raise _not_found()
    texts = [chapter.content or "" for chapter in project.outline.chapters]
    recorded = writing_repo.recorded_evidence_ids(db, project.id)
    return _Candidates(citation_ids=verified_citation_ids(texts, recorded), project=project)


def _publication(db: Session, user: User, publication_id: str) -> WritingProjectPublication:
    publication = writing_repo.get_publication(db, publication_id)
    # allow_cite is enforced here, not merely in the UI: hiding a button must
    # not be the only thing that stops a direct API call.
    if publication is None or not can_cite_publication(db, user, publication):
        raise _not_found()
    return publication


def _chat_publication(db: Session, user: User, publication_id: str):
    """Chat publications live in their own table; same cite gate as writing."""
    publication = chat_repo.get_publication(db, publication_id)
    if publication is None or not can_cite_publication(db, user, publication):
        raise _not_found()
    return publication


def _writing_publication_candidates(
    db: Session, user: User, publication_id: str
) -> _Candidates:
    publication = _publication(db, user, publication_id)
    # Frozen at publish time. NULL (pre-existing rows) and a recorded empty set
    # both mean "nothing diagnosable", but they are different facts.
    frozen = {
        normalized
        for raw in load_bounded_items(publication.citation_refs_json)
        if (normalized := normalize_citation_id(str(raw or "")))
    }
    if not frozen:
        raise _not_found()
    return _Candidates(citation_ids=frozen)


# --- targets -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Enumeration:
    targets: list[AccessTargetResponse]
    states: dict[str, str]
    target_space_ids: set[str]
    document_scope_failure: bool


def _apply_pending_state(
    db: Session, requester_user_id: str, targets: dict[str, AccessTargetResponse]
) -> list[AccessTargetResponse]:
    """Mark targets that already have an in-flight request for this requester.

    Without this the diagnosis would keep offering a request the same caller
    already submitted.
    """
    if not targets:
        return []
    pending = set(
        db.scalars(
            select(AccessRequest.target_id).where(
                AccessRequest.requester_user_id == requester_user_id,
                AccessRequest.target_type == TARGET_SPACE,
                AccessRequest.target_id.in_(list(targets)),
                AccessRequest.status == "pending",
            )
        )
    )
    for target_id in pending:
        targets[target_id].state = "pending"
    return list(targets.values())


def _targets_from_citations(
    db: Session,
    user: User,
    candidates: set[str],
    *,
    project: WritingProject | None,
) -> _Enumeration:
    """Return (targets, per-citation state, document-level scope failure)."""
    scope_spaces: set[str] | None = None
    scope_documents: set[str] = set()
    if project is not None:
        scope_spaces = set(writing_repo.load_space_ids(project.selected_space_ids))
        scope_documents = set(
            writing_repo.load_document_ids(
                getattr(project, "selected_document_ids", None)
            )
        )
    states: dict[str, str] = {}
    by_space: dict[str, AccessTargetResponse] = {}
    document_scope_failure = False
    for citation_id in sorted(candidates):
        parts = split_citation_span(citation_id)
        if parts is None:
            # Legacy/opaque ids (``citation:0``) have no version to resolve.
            states[citation_id] = "stale"
            continue
        resolved = _citation_document(db, citation_id)
        if resolved is None:
            states[citation_id] = "stale"
            continue
        document = resolved[1]
        if scope_spaces is not None and (
            document.space_id not in scope_spaces
            or (scope_documents and document.id not in scope_documents)
        ):
            # A project-scope problem: no space grant can fix it.
            states[citation_id] = "out_of_scope"
            continue
        if document.space_id is None:
            states[citation_id] = "stale"
            continue
        opened = _open_plan(db, document.space_id)
        if opened is None:
            states[citation_id] = "stale"
            continue
        space, owner_display = opened
        if _space_reader(db, user, space.id):
            states[citation_id] = "already_allowed"
            continue
        states[citation_id] = "missing_access"
        by_space.setdefault(
            space.id,
            AccessTargetResponse(
                target_type=TARGET_SPACE,
                target_name=space.name,
                owner_display_name=owner_display,
                state="missing_access",
            ),
        )
    if scope_documents:
        for document_id in scope_documents:
            document = db.get(Document, document_id)
            if document is None or document.status != "active":
                document_scope_failure = True
                break
    return _Enumeration(
        targets=_apply_pending_state(db, user.id, by_space),
        states=states,
        target_space_ids=set(by_space),
        document_scope_failure=document_scope_failure,
    )


def _other_scope_blocked(
    db: Session,
    user: User,
    project: WritingProject,
    granted_space_ids: set[str],
) -> bool:
    """Failures beyond the ones this request would fix.

    A failing space that *is* this request's target is attributable to the
    request, so it does not count; anything else does. Blocked items are never
    listed — they may name spaces the requester cannot read.
    """
    space_ids = writing_repo.load_space_ids(project.selected_space_ids)
    for space_id in space_ids:
        if space_id in granted_space_ids:
            continue
        if not _space_reader(db, user, space_id):
            return True
    document_ids = writing_repo.load_document_ids(
        getattr(project, "selected_document_ids", None)
    )
    if not document_ids:
        return False
    try:
        # Reuse the very contract the writing scope enforces — existence,
        # activity, expiry, and membership in the project's spaces — instead of
        # re-deriving it here; a document moved out of those spaces must block,
        # and only one implementation can guarantee that.
        require_documents_in_scope(
            db, user, space_ids=space_ids, document_ids=document_ids
        )
    except HTTPException:
        return True
    return False


def _chat_publication_targets(
    db: Session, user: User, publication_id: str
) -> list[AccessTargetResponse]:
    publication = _chat_publication(db, user, publication_id)
    spaces = publication_space_ids(publication)
    if not spaces:
        # No server-verified provenance: there is nothing to request.
        raise _not_found()
    targets: dict[str, AccessTargetResponse] = {}
    for space_id in spaces:
        opened = _open_plan(db, space_id)
        if opened is None:
            continue
        space, owner_display = opened
        if _space_reader(db, user, space.id):
            continue
        targets.setdefault(
            space.id,
            AccessTargetResponse(
                target_type=TARGET_SPACE,
                target_name=space.name,
                owner_display_name=owner_display,
                state="missing_access",
            ),
        )
    return _apply_pending_state(db, user.id, targets)


# --- endpoint ----------------------------------------------------------------


@router.post(
    "/access-requests:resolve",
    response_model=AccessRequestResolveResponse,
)
def resolve_access_request(
    payload: AccessRequestResolveRequest,
    user: User = Depends(current_user),
) -> AccessRequestResolveResponse:
    with transaction() as db:
        source = payload.source
        if isinstance(source, WritingSource):
            candidates = _writing_candidates(db, user, source.project_id)
        elif isinstance(source, WritingPublicationSource):
            candidates = _writing_publication_candidates(
                db, user, source.publication_id
            )
        elif isinstance(source, ChatPublicationSource):
            targets = _chat_publication_targets(db, user, source.publication_id)
            return AccessRequestResolveResponse(
                citation_state=None,
                targets=targets,
                other_scope_blocked=False,
                requestable=bool(targets),
            )
        elif isinstance(source, DiscoverySource):
            # Discovery handles come from the discovery index, which is not
            # built yet: no handle can be valid, so every one is unknown. That
            # is the documented answer for an unknown handle, not a stub.
            raise _not_found()
        else:  # pragma: no cover - the schema union has no other branch
            raise _not_found()

        named: str | None = None
        if payload.citation_id is not None:
            named = normalize_citation_id(payload.citation_id)
            if not named or named not in candidates.citation_ids:
                # Membership first: no document row is opened for a citation
                # that is not in the anchor's verified set.
                raise _not_found()
            resolved_citations = {named}
        else:
            resolved_citations = candidates.citation_ids

        enumerated = _targets_from_citations(
            db, user, resolved_citations, project=candidates.project
        )
        if candidates.project is None and not enumerated.targets and named is None:
            raise _not_found()

        other_scope_blocked = enumerated.document_scope_failure
        if candidates.project is not None:
            other_scope_blocked = other_scope_blocked or _other_scope_blocked(
                db,
                user,
                candidates.project,
                enumerated.target_space_ids,
            )

        citation_state = (
            enumerated.states.get(named) if named is not None else None
        )
        return AccessRequestResolveResponse(
            citation_state=citation_state,  # type: ignore[arg-type]
            targets=enumerated.targets,
            other_scope_blocked=other_scope_blocked,
            requestable=bool(enumerated.targets),
        )
