"""Authorisation for writing projects with collaborators.

Two layers guard every collaborative writing request and neither replaces the
other:

1. **Project role** — may this person do this to this document?
   Resolved here from ``writing_project_members`` plus the implicit owner.
2. **Knowledge-space ACL** — may this person read the material the project
   cites? Resolved per *acting user* by
   ``app.services.writing_scope.resolve_current_writing_scope``.

A member of a document does NOT inherit the owner's space access. Anything that
surfaces source material (evidence expansion, knowledge match, generation) must
still pass layer 2 for the acting user, otherwise a shared document becomes a
channel around the space ACL.

Failures raise 404 rather than 403 so a non-member cannot probe which project
ids exist, matching the existing ``_not_found`` behaviour in ``api/writing.py``.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.writing import WritingProject
from app.repositories import writing as writing_repo
from app.repositories import writing_members
from app.repositories.writing_members import ASSIGNABLE_ROLES, ROLE_ORDER

__all__ = [
    "ASSIGNABLE_ROLES",
    "ROLE_ORDER",
    "member_role",
    "project_not_found",
    "require_project_role",
    "resolve_project_role",
]


def project_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="写作项目不存在")


def member_role(db: Session, project: WritingProject, user_id: str) -> str | None:
    return writing_members.resolve_member_role(db, project, user_id)


def _satisfies(role: str | None, minimum_role: str) -> bool:
    if role is None:
        return False
    required = ROLE_ORDER.get(minimum_role)
    if required is None:
        raise ValueError(f"unknown minimum_role: {minimum_role}")
    return ROLE_ORDER.get(role, 0) >= required


def resolve_project_role(
    db: Session, project_id: str, user_id: str, *, minimum_role: str
) -> tuple[WritingProject, str] | None:
    """Load the project and the acting role, or ``None`` when not permitted."""
    project = writing_repo.project_by_id(db, project_id)
    if project is None:
        return None
    role = writing_members.resolve_member_role(db, project, user_id)
    if not _satisfies(role, minimum_role):
        return None
    assert role is not None
    return project, role


def require_project_role(
    db: Session,
    project_id: str,
    user: User,
    *,
    minimum_role: str = "viewer",
) -> WritingProject:
    """Return the project when ``user`` holds at least ``minimum_role``.

    Raises 404 otherwise — both for a missing project and for an insufficient
    role, so project existence is not disclosed.
    """
    resolved = resolve_project_role(db, project_id, user.id, minimum_role=minimum_role)
    if resolved is None:
        raise project_not_found()
    return resolved[0]
