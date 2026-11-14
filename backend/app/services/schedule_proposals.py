"""Server-bound chat proposals for schedules and their Skill dependencies."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession
from app.services.schedule_skills import (
    ScheduleSkillError,
    ScheduleSkillFile,
    get_schedule_skill_application,
    schedule_skill_application_view,
    submit_schedule_skill_application,
)


class ScheduleProposalError(ValueError):
    """Safe proposal failure that must not be converted into a schedule."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def require_bound_schedule_turn(
    db: Session, *, owner_user_id: str, claims: dict[str, Any]
) -> tuple[ChatSession, ChatMessage]:
    """Resolve the committed user turn named by the signed capability grant."""
    session_id = claims.get("conversation_id")
    message_id = claims.get("history_turn_message_id")
    if not isinstance(session_id, str) or not isinstance(message_id, str):
        raise ScheduleProposalError("定时提案必须绑定已保存的当前用户消息")
    if len(session_id) > 64 or len(message_id) > 64:
        raise ScheduleProposalError("定时提案来源无效")
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    message = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == session_id,
            ChatMessage.owner_user_id == owner_user_id,
            ChatMessage.role == "user",
        )
    )
    if session is None or message is None or not message.content.strip():
        raise ScheduleProposalError("定时提案来源不存在或已失效")
    return session, message


def _request_id(message_id: str, tool_request_id: str | None, operation: str) -> str:
    if not (tool_request_id or "").strip():
        raise ScheduleProposalError("定时提案缺少幂等标识")
    # 工具调用重放可能产生新的 call id；同一已提交用户消息和操作仍只产生一个草案。
    identity = "\0".join((message_id, operation))
    return "chat_" + hashlib.sha256(identity.encode()).hexdigest()[:40]


def _skill_files(raw: object) -> list[ScheduleSkillFile]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScheduleProposalError("Skill 候选文件格式无效")
    files: list[ScheduleSkillFile] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ScheduleProposalError("Skill 候选文件格式无效")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ScheduleProposalError("Skill 候选文件格式无效")
        files.append(ScheduleSkillFile(path=path, content=content))
    return files


def _application_from_arguments(
    *,
    owner_user_id: str,
    session_id: str,
    message_id: str,
    request_id: str,
    arguments: dict[str, Any],
):
    raw = arguments.get("new_skill")
    if not isinstance(raw, dict):
        raise ScheduleProposalError("缺少新 Skill 候选内容")
    required = ("skill_id", "version", "description", "instructions")
    if not all(isinstance(raw.get(key), str) for key in required):
        raise ScheduleProposalError("新 Skill 候选内容不完整")
    try:
        return submit_schedule_skill_application(
            owner_user_id=owner_user_id,
            source_session_id=session_id,
            source_message_id=message_id,
            request_key=request_id,
            skill_id=raw["skill_id"],
            version=raw["version"],
            description=raw["description"],
            instructions=raw["instructions"],
            files=_skill_files(raw.get("files")),
        )
    except ScheduleSkillError as exc:
        raise ScheduleProposalError(str(exc)) from exc


def propose_schedule(
    db: Session,
    *,
    owner_user_id: str,
    claims: dict[str, Any],
    arguments: dict[str, Any],
    tool_request_id: str | None,
) -> dict[str, Any]:
    """Persist a draft only; no model/tool call can confirm or execute it."""
    session, message = require_bound_schedule_turn(
        db, owner_user_id=owner_user_id, claims=claims
    )
    mode = arguments.get("capability_mode", "existing_skill")
    if mode not in {"existing_skill", "task_template", "new_skill"}:
        raise ScheduleProposalError("不支持的能力提案类型")
    config = arguments.get("config")
    if not isinstance(config, dict):
        raise ScheduleProposalError("定时提案配置无效")
    config = json.loads(json.dumps(config, ensure_ascii=False))
    proposal_request_id = _request_id(message.id, tool_request_id, "create")
    application = None
    if mode == "new_skill":
        application = _application_from_arguments(
            owner_user_id=owner_user_id,
            session_id=session.id,
            message_id=message.id,
            request_id=proposal_request_id,
            arguments=arguments,
        )
        # 未批准候选不能伪装成可绑定 Skill；安装身份只通过申请记录关联。
        config.pop("skill_id", None)
        config.pop("skill_version", None)
        config.pop("skill_digest", None)

    from app.services import schedules

    try:
        draft = schedules.create_draft(
            db,
            owner_user_id,
            config,
            proposal_request_id,
            source_session_id=session.id,
            source_message_id=message.id,
            skill_request_id=application.id if application is not None else None,
        )
    except (KeyError, ValueError) as exc:
        raise ScheduleProposalError(
            str(exc), status_code=int(getattr(exc, "status_code", 422))
        ) from exc
    result = schedules.draft_response(db, draft)
    return {
        "proposal_type": mode,
        "draft": result,
        "skill_application": (
            schedule_skill_application_view(application) if application else None
        ),
        "confirmation_required": True,
    }


def propose_schedule_action(
    db: Session,
    *,
    owner_user_id: str,
    claims: dict[str, Any],
    arguments: dict[str, Any],
    tool_request_id: str | None,
) -> dict[str, Any]:
    """Persist a version-bound management draft; never apply the action here."""
    session, message = require_bound_schedule_turn(
        db, owner_user_id=owner_user_id, claims=claims
    )
    operation = arguments.get("operation")
    if operation not in {"update", "pause", "resume", "archive", "run_now"}:
        raise ScheduleProposalError("不支持的定时任务操作")
    schedule_id = arguments.get("schedule_id")
    expected_revision = arguments.get("expected_revision")
    if not isinstance(schedule_id, str) or not schedule_id.strip():
        raise ScheduleProposalError("必须明确指定定时任务")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ScheduleProposalError("必须提供当前任务版本")

    from app.models.schedules import Schedule
    from app.services import schedules

    schedule = db.scalar(
        select(Schedule).where(
            Schedule.id == schedule_id.strip(),
            Schedule.owner_user_id == owner_user_id,
        )
    )
    if schedule is None:
        raise ScheduleProposalError("定时任务不存在", status_code=404)
    if schedule.revision != expected_revision:
        raise ScheduleProposalError("任务已更新，请刷新后重新提案", status_code=409)
    if operation == "update":
        config = arguments.get("config")
        if not isinstance(config, dict):
            raise ScheduleProposalError("修改提案缺少完整配置")
    else:
        try:
            config = json.loads(schedule.config_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ScheduleProposalError("任务配置暂时不可用") from exc
    proposal_request_id = _request_id(message.id, tool_request_id, operation)
    try:
        draft = schedules.create_draft(
            db,
            owner_user_id,
            config,
            proposal_request_id,
            source_session_id=session.id,
            source_message_id=message.id,
            operation=operation,
            target_schedule_id=schedule.id,
            target_revision=schedule.revision,
        )
    except (KeyError, ValueError) as exc:
        raise ScheduleProposalError(
            str(exc), status_code=int(getattr(exc, "status_code", 422))
        ) from exc
    return {
        "proposal_type": "management",
        "draft": schedules.draft_response(db, draft),
        "confirmation_required": True,
    }


def get_schedule_summary(
    db: Session,
    *,
    owner_user_id: str,
    claims: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Read one explicitly named owner-scoped schedule and its persisted result."""
    require_bound_schedule_turn(db, owner_user_id=owner_user_id, claims=claims)
    schedule_id = arguments.get("schedule_id")
    if not isinstance(schedule_id, str) or not schedule_id.strip():
        raise ScheduleProposalError("必须明确指定定时任务")
    from app.models.schedules import Schedule
    from app.services import schedules

    schedule = db.scalar(
        select(Schedule).where(
            Schedule.id == schedule_id.strip(),
            Schedule.owner_user_id == owner_user_id,
        )
    )
    if schedule is None:
        raise ScheduleProposalError("定时任务不存在", status_code=404)
    return {"schedule": schedules.schedule_response(db, schedule, owner_user_id)}


def refresh_skill_application_drafts(
    db: Session, *, owner_user_id: str, application_id: str
) -> dict[str, Any]:
    """Project current installer state; approval never confirms a schedule."""
    application = get_schedule_skill_application(
        application_id, owner_user_id=owner_user_id
    )
    if application is None:
        raise ScheduleProposalError("Skill 申请不存在", status_code=404)
    view = schedule_skill_application_view(application)
    from app.services import schedules

    from app.models.schedules import ScheduleDraft

    drafts = list(
        db.scalars(
            select(ScheduleDraft).where(
                ScheduleDraft.owner_user_id == owner_user_id,
                ScheduleDraft.skill_request_id == application.id,
            )
        )
    )
    if view["ready"]:
        for draft in drafts:
            if draft.status != "waiting_skill":
                continue
            config = json.loads(draft.config_json)
            config.update(
                skill_id=application.skill_id,
                skill_version=application.version,
                skill_digest=view["approved_digest"],
            )
            schedules.refresh_draft_dependency(db, draft, candidate_config=config)

    return {
        "application": view,
        "drafts": [schedules.draft_response(db, draft) for draft in drafts],
    }
