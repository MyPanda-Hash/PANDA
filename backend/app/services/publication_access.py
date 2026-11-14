from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import User
from app.services.authorization import PermissionService


def publication_space_ids(publication: Any) -> list[str]:
    raw = getattr(publication, "space_ids_json", None)
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return list(
        dict.fromkeys(
            item.strip() for item in parsed if isinstance(item, str) and item.strip()
        )
    )


def _has_current_space_access(db: Session, user: User, space_ids: list[str]) -> bool:
    if not space_ids:
        return False
    return all(
        PermissionService.check_space_owner(db, space_id, user.id)
        or PermissionService.has_space_member_role(
            db,
            user,
            space_id,
            minimum_role="reader",
        )
        for space_id in space_ids
    )


def can_view_publication(db: Session, user: User, publication: Any) -> bool:
    if user.status != "active" or getattr(publication, "status", None) != "active":
        return False
    if user.id in {
        getattr(publication, "source_owner_user_id", None),
        getattr(publication, "shared_by_user_id", None),
    }:
        return True
    scope = str(getattr(publication, "share_scope", "") or "")
    if scope == "all_internal":
        return True
    if scope == "space":
        return _has_current_space_access(
            db,
            user,
            publication_space_ids(publication),
        )
    return False


def can_cite_publication(db: Session, user: User, publication: Any) -> bool:
    return bool(getattr(publication, "allow_cite", False)) and can_view_publication(
        db,
        user,
        publication,
    )
