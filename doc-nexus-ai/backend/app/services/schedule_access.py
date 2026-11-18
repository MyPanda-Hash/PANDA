"""定时任务的业务身份、资料授权与已批准能力绑定。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.documents import Document
from app.services.authorization import PermissionService
from app.services.chat_knowledge import require_documents_in_scope
from app.services.pi_runtime.config import PiRuntimeSettings
from app.services.pi_runtime.skills import list_agent_skills

if TYPE_CHECKING:
    from app.models.schedules import ScheduleRun


class ScheduleAccessError(ValueError):
    """只向客户提供可操作的安全原因，不透出路径、凭据或供应商响应。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def schedule_scope_space_ids(db: Session, config: dict[str, Any]) -> list[str]:
    """补齐精确文档的所属空间，仅用于鉴权，绝不扩大文档选择。"""
    space_ids = set(config.get("space_ids") or [])
    document_ids = config.get("document_ids") or []
    if document_ids:
        space_ids.update(
            db.scalars(
                select(Document.space_id)
                .where(
                    Document.id.in_(document_ids),
                    Document.space_id.is_not(None),
                )
                .distinct()
            )
        )
    return sorted(space_ids)


def validate_schedule_scope(
    db: Session, owner_user_id: str, config: dict[str, Any]
) -> None:
    user = db.get(User, owner_user_id, populate_existing=True)
    if user is None or user.status != "active":
        raise ScheduleAccessError("owner_unavailable", "任务所有者当前不可用")
    space_ids = config.get("space_ids") or []
    document_ids = config.get("document_ids") or []
    if not isinstance(space_ids, list) or not isinstance(document_ids, list):
        raise ScheduleAccessError("scope_invalid", "请重新选择任务的资料范围")
    if any(
        not isinstance(item, str) or not item for item in (*space_ids, *document_ids)
    ):
        raise ScheduleAccessError("scope_invalid", "请重新选择任务的资料范围")
    space_ids = schedule_scope_space_ids(db, config)
    try:
        PermissionService.require_space_access_batch(db, user, space_ids)
        if document_ids:
            # 定时任务不继承会话临时附件；必须先转为有长期 ACL 的正式资料。
            temporary = db.scalar(
                select(Document.id)
                .where(
                    Document.id.in_(document_ids),
                    (Document.is_temporary.is_(True) | Document.space_id.is_(None)),
                )
                .limit(1)
            )
            if temporary is not None:
                raise ScheduleAccessError(
                    "temporary_source", "请先将临时附件保存至资料空间，再用于定时任务"
                )
            require_documents_in_scope(
                db, user, space_ids=space_ids, document_ids=document_ids
            )
    except HTTPException as exc:
        raise ScheduleAccessError(
            "scope_unavailable", "任务引用的资料已不可用或访问权限已改变，请重新选择"
        ) from exc


def list_schedule_skills() -> list[dict[str, str]]:
    """复用 Pi 发布前的 enabled 状态和完整批准树摘要校验。"""
    records = list_agent_skills(None, PiRuntimeSettings.from_env())
    return [
        {
            "id": str(record["skill_id"]),
            "name": str(
                record.get("label") or record.get("description") or "自定义能力"
            )[:120],
            "version": str(record["version"]),
            "digest": str(record["approved_sha256"]),
            "description": str(record.get("description") or "")[:1000],
        }
        for record in records
    ]


def resolve_schedule_skill(config: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(config)
    if config.get("kind") == "reminder":
        return resolved
    skill_id = config.get("skill_id")
    if not skill_id:
        return resolved
    skill = next(
        (item for item in list_schedule_skills() if item["id"] == skill_id), None
    )
    if skill is None:
        raise ScheduleAccessError("skill_unavailable", "所选能力尚未启用或已不可用")
    if (
        config.get("skill_version") and config["skill_version"] != skill["version"]
    ) or (config.get("skill_digest") and config["skill_digest"] != skill["digest"]):
        raise ScheduleAccessError(
            "skill_changed", "所选能力版本已改变，请重新选择并确认"
        )
    resolved.update(
        skill_id=skill["id"],
        skill_version=skill["version"],
        skill_digest=skill["digest"],
    )
    return resolved


def can_access_schedule_run(db: Session, run: ScheduleRun, owner_user_id: str) -> bool:
    # 定时结果是个人对象，管理员不会仅凭角色获得另一人的正文或文件。
    if run.owner_user_id != owner_user_id:
        return False
    try:
        config = json.loads(run.config_json)
        if not isinstance(config, dict):
            return False
        validate_schedule_scope(db, owner_user_id, config)
    except (ValueError, TypeError):
        return False
    return True
