"""项目群聊 API。

路径前缀 ``/groups``，与个人对话（``/chat``）区分：群是成员制，非成员一律
403/404，系统管理员也不例外（见 ``services.group_chat.require_member``）。

附件落在本地磁盘（``settings.upload_dir/group/<群 id>/``），不依赖 MinIO，
因为本机 ``DOC_NEXUS_MINIO_ENABLED=0``。
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote
import logging
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.config import settings
from app.core.database import transaction
from app.core.security import new_token, now
from app.core.timeutil import as_utc
from app.models.auth import User
from app.models.group_chat import (
    GroupTodo,
    ProjectGroup,
    ProjectGroupAiRun,
    ProjectGroupAttachment,
    ProjectGroupMember,
    ProjectGroupMessage,
)
from app.repositories import audit, users
from app.schemas.group_chat import (
    AnalyzeResponse,
    QuotedMessage,
    MarkReadRequest,
    AttachmentResponse,
    DirectoryUserResponse,
    GroupCreateRequest,
    GroupDetailResponse,
    GroupMemberAddRequest,
    GroupMemberResponse,
    GroupSummaryResponse,
    GroupUpdateRequest,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    TodoResponse,
    TodoStatusRequest,
)
from app.services import asr, group_assistant, group_chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["group-chat"])
todo_router = APIRouter(prefix="/todos", tags=["group-chat"])

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, group_chat.GroupNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, group_chat.NotGroupMember):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, group_chat.GroupPermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, group_chat.InvalidGroupInput):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail="请求无法处理")


def _attachment_response(attachment: ProjectGroupAttachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        kind=attachment.kind,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        extract_status=attachment.extract_status,
        transcript=attachment.extracted_text if attachment.kind == "voice" else None,
        url=f"/groups/{attachment.group_id}/attachments/{attachment.id}",
    )


def _read_positions(db: Session, group_id: str) -> dict[str, datetime]:
    """每个成员读到哪儿（把 last_read_message_id 换成时间点，便于按时间比较）。"""
    members = list(
        db.scalars(
            select(ProjectGroupMember).where(ProjectGroupMember.group_id == group_id)
        )
    )
    ids = [m.last_read_message_id for m in members if m.last_read_message_id]
    anchors: dict[str, datetime] = {}
    if ids:
        for row in db.scalars(
            select(ProjectGroupMessage).where(ProjectGroupMessage.id.in_(ids))
        ):
            anchors[row.id] = as_utc(row.created_at) or row.created_at
    return {
        m.user_id: anchors[m.last_read_message_id]
        for m in members
        if m.last_read_message_id and m.last_read_message_id in anchors
    }


def _quoted_of(db: Session, message: ProjectGroupMessage) -> "QuotedMessage | None":
    if not message.reply_to_message_id:
        return None
    target = db.get(ProjectGroupMessage, message.reply_to_message_id)
    if target is None or target.deleted_at is not None:
        return None
    sender = db.get(User, target.sender_user_id) if target.sender_user_id else None
    name = "AI 助手" if target.sender_kind == "ai" else (
        (sender.display_name or sender.username) if sender else "已退出成员"
    )
    return QuotedMessage(
        id=target.id,
        sender_name=name,
        content=(target.content or "")[:80],
    )


def _message_response(
    db: Session,
    message: ProjectGroupMessage,
    *,
    viewer_id: str,
    read_positions: dict[str, datetime] | None = None,
    member_total: int = 0,
) -> MessageResponse:
    sender = db.get(User, message.sender_user_id) if message.sender_user_id else None
    if message.sender_kind == "ai":
        name = "AI 助手"
    elif message.sender_kind == "system":
        name = "系统"
    else:
        name = (sender.display_name or sender.username) if sender else "已退出成员"
    try:
        attachment_ids = json.loads(message.attachments_json or "[]")
    except (TypeError, ValueError):
        attachment_ids = []
    attachments: list[ProjectGroupAttachment] = []
    if attachment_ids:
        attachments = list(
            db.scalars(
                select(ProjectGroupAttachment).where(
                    ProjectGroupAttachment.id.in_([str(i) for i in attachment_ids])
                )
            )
        )
    from app.api.user_avatar import avatar_url as avatar_of

    return MessageResponse(
        id=message.id,
        sender_user_id=message.sender_user_id,
        sender_name=name,
        sender_avatar_url=avatar_of(message.sender_user_id) if message.sender_kind == "user" else None,
        sender_kind=message.sender_kind,
        kind=message.kind,
        content=message.content,
        attachments=[_attachment_response(item) for item in attachments],
        parts_json=message.parts_json,
        mine=message.sender_user_id == viewer_id,
        read_count=(
            sum(
                1
                for user_id, stamp in (read_positions or {}).items()
                if user_id != message.sender_user_id
                and stamp >= (as_utc(message.created_at) or message.created_at)
            )
            if message.sender_user_id == viewer_id
            else 0
        ),
        read_total=max(0, member_total - 1),
        quoted=_quoted_of(db, message),
        created_at=as_utc(message.created_at),
    )


def _member_response(db: Session, member: ProjectGroupMember) -> GroupMemberResponse:
    user = db.get(User, member.user_id)
    from app.api.user_avatar import avatar_url as avatar_of

    return GroupMemberResponse(
        user_id=member.user_id,
        display_name=(user.display_name if user else member.user_id),
        avatar_url=avatar_of(member.user_id),
        username=(user.username if user else ""),
        role=member.role,
        joined_at=as_utc(member.joined_at),
    )


def _summary_response(
    db: Session,
    group: ProjectGroup,
    *,
    viewer: User,
    member: ProjectGroupMember | None,
) -> GroupSummaryResponse:
    owner = db.get(User, group.owner_user_id)
    member_count = (
        db.scalar(
            select(func.count(ProjectGroupMember.id)).where(
                ProjectGroupMember.group_id == group.id
            )
        )
        or 0
    )
    unread = 0
    if member is not None:
        unread = group_chat.unread_count(db, group_id=group.id, member=member)
    return GroupSummaryResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        status=group.status,
        owner_user_id=group.owner_user_id,
        owner_display_name=(owner.display_name if owner else None),
        member_count=int(member_count),
        unread_count=unread,
        my_role=member.role if member is not None else None,
        updated_at=as_utc(group.updated_at),
    )


AI_ASSISTANT_MEMBER_ID = "ai_assistant"


def _detail_response(
    db: Session, group: ProjectGroup, *, viewer: User, member: ProjectGroupMember
) -> GroupDetailResponse:
    summary = _summary_response(db, group, viewer=viewer, member=member)
    rows = db.scalars(
        select(ProjectGroupMember).where(ProjectGroupMember.group_id == group.id)
    )
    memory = group_assistant.load_memory(db, group.id)
    return GroupDetailResponse(
        **summary.model_dump(),
        mes_project_code=group.mes_project_code,
        members=[_assistant_member(group)]
        + [_member_response(db, row) for row in rows],
        memory_summary=(memory.summary if memory else ""),
        memory_message_count=(memory.covered_message_count if memory else 0),
    )


def _assistant_member(group: ProjectGroup) -> GroupMemberResponse:
    """AI 助手是群的固定成员，但不是真实账号，所以按需合成一条。"""
    return GroupMemberResponse(
        user_id=AI_ASSISTANT_MEMBER_ID,
        display_name="AI 助手",
        username="ai_assistant",
        role="assistant",
        joined_at=group.created_at,
    )


def _mes_form_url(panel: str, doc_no: str) -> str:
    """MES 是 hash 路由：/#/panelx/form/<PANEL>?code=<单据号>。"""
    base = (settings.mes_watch_ui_url or "").strip().rstrip("/")
    if not base or not panel or not doc_no:
        return ""
    return f"{base}/#/panelx/form/{panel}?code={quote(str(doc_no))}"


def _todo_refs(todo: GroupTodo) -> list[dict]:
    try:
        refs = json.loads(todo.refs_json or "[]")
    except (TypeError, ValueError):
        return []
    out: list[dict] = []
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        panel = str(ref.get("panel") or "")
        doc_no = str(ref.get("docNo") or "")
        url = _mes_form_url(panel, doc_no)
        if not url:
            continue
        out.append(
            {
                "panel": panel,
                "docNo": doc_no,
                "label": str(ref.get("label") or panel),
                "url": url,
            }
        )
    return out


def _todo_response(db: Session, todo: GroupTodo) -> TodoResponse:
    assignee = db.get(User, todo.assignee_user_id)
    group = db.get(ProjectGroup, todo.group_id) if todo.group_id else None
    return TodoResponse(
        id=todo.id,
        group_id=todo.group_id,
        group_name=group.name if group else None,
        assignee_user_id=todo.assignee_user_id,
        assignee_display_name=(assignee.display_name if assignee else None),
        title=todo.title,
        detail=todo.detail,
        priority=todo.priority,
        status=todo.status,
        due_at=as_utc(todo.due_at),
        origin=todo.origin,
        source_message_id=todo.source_message_id,
        confidence=todo.confidence,
        created_at=as_utc(todo.created_at),
        refs=_todo_refs(todo),
        nudge_count=int(todo.nudge_count or 0),
        nudged_at=as_utc(todo.nudged_at),
        escalated_at=as_utc(todo.escalated_at),
        last_action_at=as_utc(todo.last_action_at),
    )


@router.get("", response_model=list[GroupSummaryResponse])
def list_groups(
    user: User = Depends(current_user_from_bearer_or_cookie),
    include_archived: bool = Query(default=False),
) -> list[GroupSummaryResponse]:
    with transaction() as db:
        groups = group_chat.list_groups(db, user, include_archived=include_archived)
        return [
            _summary_response(
                db,
                group,
                viewer=user,
                member=group_chat.find_member(db, group.id, user.id),
            )
            for group in groups
        ]


@router.post("", response_model=GroupDetailResponse, status_code=201)
def create_group(
    payload: GroupCreateRequest, user: User = Depends(current_user_from_bearer_or_cookie)
) -> GroupDetailResponse:
    with transaction() as db:
        try:
            group = group_chat.create_group(
                db,
                owner=user,
                name=payload.name,
                description=payload.description,
                mes_project_code=payload.mes_project_code,
            )
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_created", group.id, "success")
        member = group_chat.find_member(db, group.id, user.id)
        assert member is not None
        return _detail_response(db, group, viewer=user, member=member)


@router.get("/directory-users", response_model=list[DirectoryUserResponse])
def directory_users(
    user: User = Depends(current_user_from_bearer_or_cookie),
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DirectoryUserResponse]:
    with transaction() as db:
        return [
            DirectoryUserResponse(
                id=item.id,
                display_name=item.display_name,
                username=item.username,
                email=item.email,
            )
            for item in users.list_directory(db, q, limit)
        ]


@router.get("/{group_id}", response_model=GroupDetailResponse)
def get_group(group_id: str, user: User = Depends(current_user_from_bearer_or_cookie)) -> GroupDetailResponse:
    with transaction() as db:
        try:
            group, member = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        return _detail_response(db, group, viewer=user, member=member)


@router.patch("/{group_id}", response_model=GroupDetailResponse)
def update_group(
    group_id: str, payload: GroupUpdateRequest, user: User = Depends(current_user_from_bearer_or_cookie)
) -> GroupDetailResponse:
    if payload.name is None and payload.description is None:
        raise HTTPException(status_code=422, detail="请至少提供群名或说明")
    with transaction() as db:
        try:
            group, member = group_chat.require_manager(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="群名不能为空")
            group.name = name[: group_chat.GROUP_NAME_MAX]
        if payload.description is not None:
            group.description = payload.description.strip()
        group.updated_at = now()
        audit.record(db, user.id, "group_updated", group.id, "success")
        return _detail_response(db, group, viewer=user, member=member)


async def _distill_dissolved_group(group_id: str) -> None:
    """解散后从这段讨论里提炼项目经验。

    放后台跑：要调模型，几秒到几十秒，不能让「解散」按钮一直转。
    失败只记日志——群已经解散成功，提炼是加分项。
    """
    from datetime import timedelta

    from app.services import employee_profiles

    try:
        with transaction() as db:
            group = db.get(ProjectGroup, group_id)
            if group is None:
                return
            created = await employee_profiles.extract_from_groups(
                db,
                period_start=as_utc(group.created_at) - timedelta(seconds=1),
                period_end=now() + timedelta(days=1),
                group_ids=[group_id],
            )
        logger.info("dissolve distillation group=%s cards=%s", group_id, len(created))
    except Exception:  # noqa: BLE001
        logger.exception("dissolve distillation failed group=%s", group_id)


@router.post("/{group_id}:dissolve", response_model=GroupSummaryResponse)
async def dissolve_group(
    group_id: str,
    background: BackgroundTasks,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> GroupSummaryResponse:
    """解散群聊：从列表里消失，但消息、附件、待办全部保留下来。

    保留是为了让 AI 还能从这里提炼项目经验。真删除走 DELETE。
    """
    with transaction() as db:
        try:
            group = group_chat.archive_group(db, actor=user, group_id=group_id)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_dissolved", group.id, "success")
        payload = _summary_response(
            db,
            group,
            viewer=user,
            member=group_chat.find_member(db, group.id, user.id),
        )
    background.add_task(_distill_dissolved_group, group_id)
    return payload


@router.post("/{group_id}:distill", response_model=dict)
async def distill_group(
    group_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> dict:
    """手动从某个群（通常是已解散的）提炼经验，给管理员补跑用。"""
    from datetime import timedelta

    from app.services import employee_profiles

    with transaction() as db:
        group = db.get(ProjectGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="群不存在")
        member = group_chat.find_member(db, group_id, user.id)
        if not (user.role == "admin" or (member is not None and member.role == "owner")):
            raise HTTPException(status_code=403, detail="只有组长或管理员可以提炼")
        created = await employee_profiles.extract_from_groups(
            db,
            period_start=as_utc(group.created_at) - timedelta(seconds=1),
            period_end=now() + timedelta(days=1),
            group_ids=[group_id],
        )
        return {"data": {"created": len(created), "titles": [c.title for c in created]}}


@router.post("/{group_id}:archive", response_model=GroupSummaryResponse)
def archive_group(
    group_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> GroupSummaryResponse:
    with transaction() as db:
        try:
            group = group_chat.archive_group(db, actor=user, group_id=group_id)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_archived", group.id, "success")
        return _summary_response(
            db,
            group,
            viewer=user,
            member=group_chat.find_member(db, group.id, user.id),
        )


@router.delete("/{group_id}", response_model=None, status_code=204)
def delete_group(group_id: str, user: User = Depends(current_user_from_bearer_or_cookie)) -> None:
    with transaction() as db:
        try:
            group_chat.delete_group(db, actor=user, group_id=group_id)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_deleted", group_id, "success")


@router.post("/{group_id}/members", response_model=GroupDetailResponse, status_code=201)
def add_member(
    group_id: str, payload: GroupMemberAddRequest, user: User = Depends(current_user_from_bearer_or_cookie)
) -> GroupDetailResponse:
    with transaction() as db:
        try:
            group_chat.add_member(
                db,
                actor=user,
                group_id=group_id,
                user_id=payload.user_id,
                role=payload.role,
            )
            group, member = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_member_added", group_id, "success")
        return _detail_response(db, group, viewer=user, member=member)


@router.delete("/{group_id}/members/{member_user_id}", response_model=None, status_code=204)
def remove_member(
    group_id: str, member_user_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> None:
    with transaction() as db:
        try:
            group_chat.remove_member(
                db, actor=user, group_id=group_id, user_id=member_user_id
            )
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_member_removed", group_id, "success")


@router.get("/{group_id}/messages", response_model=MessageListResponse)
def list_messages(
    group_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    after: str | None = Query(default=None, max_length=64),
    before: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> MessageListResponse:
    with transaction() as db:
        try:
            group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        _group, member = group_chat.require_member(db, group_id, user)
        messages = group_chat.list_messages(
            db,
            group_id=group_id,
            after_message_id=after,
            before_message_id=before,
            limit=limit,
        )
        read_positions = _read_positions(db, group_id)
        member_total = int(
            db.scalar(
                select(func.count(ProjectGroupMember.id)).where(
                    ProjectGroupMember.group_id == group_id
                )
            )
            or 0
        )
        read_up_to = member.last_read_message_id
        unread_from = None
        if messages and not read_up_to:
            # 从没读过：全部都是新的
            unread_from = messages[0].id
        elif read_up_to:
            seen = False
            for item in messages:
                if seen:
                    unread_from = item.id
                    break
                if item.id == read_up_to:
                    seen = True
        return MessageListResponse(
            messages=[
                _message_response(
                    db,
                    item,
                    viewer_id=user.id,
                    read_positions=read_positions,
                    member_total=member_total,
                )
                for item in messages
            ],
            latest_message_id=messages[-1].id if messages else None,
            unread_from_id=unread_from,
            read_up_to_id=read_up_to,
        )


@router.post("/{group_id}/messages", response_model=MessageResponse, status_code=201)
def post_message(
    group_id: str,
    payload: MessageCreateRequest,
    background: BackgroundTasks,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> MessageResponse:
    with transaction() as db:
        try:
            group, _ = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc

        attachments: list[ProjectGroupAttachment] = []
        if payload.attachment_ids:
            rows = db.scalars(
                select(ProjectGroupAttachment).where(
                    ProjectGroupAttachment.group_id == group_id,
                    ProjectGroupAttachment.id.in_(payload.attachment_ids),
                )
            )
            attachments = list(rows)
            if len(attachments) != len(set(payload.attachment_ids)):
                raise HTTPException(status_code=422, detail="附件不属于该群")
            for item in attachments:
                if item.message_id is not None:
                    raise HTTPException(status_code=409, detail="附件已被其他消息引用")

        content = payload.content
        if payload.kind == "voice" and not content.strip() and attachments:
            transcript = (attachments[0].extracted_text or "").strip()
            if transcript:
                content = transcript
        try:
            message = group_chat.post_message(
                db,
                group=group,
                sender=user,
                kind=payload.kind,
                content=content,
                attachments=attachments,
                reply_to_message_id=payload.reply_to_message_id,
            )
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        member_total = int(
            db.scalar(
                select(func.count(ProjectGroupMember.id)).where(
                    ProjectGroupMember.group_id == group_id
                )
            )
            or 0
        )
        response = _message_response(
            db,
            message,
            viewer_id=user.id,
            read_positions=_read_positions(db, group_id),
            member_total=member_total,
        )
        spoken = content

    # 「实时」：问项目情况的、@AI 的，发完就让助手接话（后台跑，不阻塞发送）。
    if group_assistant.should_auto_reply(spoken):
        background.add_task(
            _run_analysis_safely,
            group_id,
            "message",
            spoken,
            user.display_name or user.username,
        )
    return response


def _run_analysis_safely(
    group_id: str,
    trigger: str,
    focus_message: str | None = None,
    focus_sender: str | None = None,
) -> None:
    """后台跑一次分析：模型不可用时静默跳过，避免每次发言都留失败记录。"""
    import asyncio

    async def _run() -> None:
        with transaction() as db:
            group = db.get(ProjectGroup, group_id)
            if group is None or group.status != "active":
                return
            try:
                await group_assistant.analyze_group(
                    db,
                    group=group,
                    trigger=trigger,
                    focus_message=focus_message,
                    focus_sender=focus_sender,
                )
            except group_assistant.GroupAssistantUnavailable:
                logger.info("group_assistant.skipped_no_model", extra={"group_id": group_id})
            except group_assistant.GroupAssistantError:
                logger.warning(
                    "group_assistant.failed", extra={"group_id": group_id}, exc_info=True
                )

    try:
        asyncio.run(_run())
    except RuntimeError:
        # 已在事件循环里（BackgroundTasks 同步执行时可能出现）：放弃这次自动分析。
        logger.warning("group_assistant.background_loop_conflict", extra={"group_id": group_id})


@router.post("/{group_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    group_id: str,
    file: Annotated[UploadFile, File(description="文档、图片或语音")],
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> AttachmentResponse:
    with transaction() as db:
        try:
            group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc

    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="文件不能超过 25 MB")

    filename = Path(file.filename or "未命名文件").name
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    kind = _attachment_kind(mime, filename)
    digest = hashlib.sha256(payload).hexdigest()
    folder = Path(settings.upload_dir) / "group" / group_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    target = folder / f"{digest[:16]}{suffix}"
    if not target.exists():
        target.write_bytes(payload)

    attachment_id = _new_id("gatt")
    if kind == "voice":
        extracted, status, extract_error = await _transcribe_voice(payload, mime)
    else:
        extracted, status = _extract_text(kind, payload, suffix)
        extract_error = None
    with transaction() as db:
        attachment = ProjectGroupAttachment(
            id=attachment_id,
            group_id=group_id,
            message_id=None,
            uploader_user_id=user.id,
            kind=kind,
            filename=filename[:255],
            mime_type=mime[:128],
            size_bytes=len(payload),
            storage_path=str(target),
            sha256=digest,
            extracted_text=extracted,
            extract_status=status,
            extract_error=extract_error,
            created_at=now(),
        )
        db.add(attachment)
        db.flush()
        return _attachment_response(attachment)


async def _transcribe_voice(payload: bytes, mime: str) -> tuple[str | None, str, str | None]:
    """语音落库前就转写成文字，AI 直接读文字，不需要人再手动补。"""
    # 百炼 ASR 单次上限 5 分钟；16k 单声道 16bit 约合 32KB/s，10MB ≈ 5.2 分钟。
    if len(payload) > settings.asr_max_upload_bytes:
        return None, "failed", "语音超过识别上限（约 5 分钟），请分段发送"
    try:
        text = await asr.transcribe_audio(payload, mime or "audio/webm")
    except asr.ASRNotConfiguredError:
        return None, "pending", "语音识别未配置（DASHSCOPE_API_KEY）"
    except asr.ASRProviderError:
        logger.warning("group_voice.transcribe_failed", exc_info=True)
        return None, "failed", "语音识别失败，可稍后重试"
    if not (text or "").strip():
        return None, "failed", "语音识别没有返回文字"
    return text.strip()[:20000], "done", None


@router.post(
    "/{group_id}/attachments/{attachment_id}:transcribe",
    response_model=AttachmentResponse,
)
async def retranscribe_voice(
    group_id: str,
    attachment_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> AttachmentResponse:
    """配置好语音识别后，对之前的语音补一次转写。"""
    with transaction() as db:
        try:
            group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        attachment = db.get(ProjectGroupAttachment, attachment_id)
        if attachment is None or attachment.group_id != group_id:
            raise HTTPException(status_code=404, detail="附件不存在")
        if attachment.kind != "voice":
            raise HTTPException(status_code=422, detail="只有语音可以转写")
        path = Path(attachment.storage_path)
        mime = attachment.mime_type or "audio/webm"

    if not path.is_file():
        raise HTTPException(status_code=410, detail="语音文件已丢失")
    payload = path.read_bytes()
    text, status, error = await _transcribe_voice(payload, mime)
    with transaction() as db:
        attachment = db.get(ProjectGroupAttachment, attachment_id)
        assert attachment is not None
        attachment.extracted_text = text
        attachment.extract_status = status
        attachment.extract_error = error
        db.flush()
        return _attachment_response(attachment)


def _attachment_kind(mime: str, filename: str) -> str:
    if mime.startswith("audio/"):
        return "voice"
    if mime.startswith("image/"):
        return "image"
    if Path(filename).suffix.lower() in {".webm", ".mp3", ".m4a", ".wav", ".ogg"}:
        return "voice"
    return "file"


def _extract_text(kind: str, payload: bytes, suffix: str) -> tuple[str | None, str]:
    """本地可做的正文抽取：纯文本直接解；语音和二进制文档留待后续处理。"""
    if kind == "voice":
        return None, "pending"
    if suffix in TEXT_SUFFIXES or kind == "text":
        for encoding in ("utf-8", "gb18030"):
            try:
                return payload.decode(encoding)[:200_000], "done"
            except UnicodeDecodeError:
                continue
        return None, "failed"
    return None, "skipped"


@router.get("/{group_id}/attachments/{attachment_id}", response_model=None)
def download_attachment(
    group_id: str, attachment_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> FileResponse:
    with transaction() as db:
        try:
            group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        attachment = db.get(ProjectGroupAttachment, attachment_id)
        if attachment is None or attachment.group_id != group_id:
            raise HTTPException(status_code=404, detail="附件不存在")
        path = Path(attachment.storage_path)
        filename = attachment.filename
        mime = attachment.mime_type
    if not path.is_file():
        raise HTTPException(status_code=410, detail="附件文件已丢失")
    return FileResponse(path, media_type=mime or "application/octet-stream", filename=filename)


@router.post("/{group_id}:read", response_model=None, status_code=204)
def mark_read(
    group_id: str, payload: MarkReadRequest, user: User = Depends(current_user_from_bearer_or_cookie)
) -> None:
    with transaction() as db:
        try:
            _group, member = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        message = db.get(ProjectGroupMessage, payload.message_id)
        if message is None or message.group_id != group_id:
            raise HTTPException(status_code=404, detail="消息不存在")
        group_chat.mark_read(db, member=member, message_id=payload.message_id)


@router.delete("/{group_id}/messages/{message_id}", response_model=None, status_code=204)
def recall_message(
    group_id: str, message_id: str, user: User = Depends(current_user_from_bearer_or_cookie)
) -> None:
    """撤回：本人 2 分钟内可撤；组长/管理员可撤任意消息。"""
    with transaction() as db:
        try:
            group, member = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        message = db.get(ProjectGroupMessage, message_id)
        if message is None or message.group_id != group_id:
            raise HTTPException(status_code=404, detail="消息不存在")
        if message.deleted_at is not None:
            return
        is_manager = member.role in ("owner", "manager")
        if message.sender_user_id != user.id and not is_manager:
            raise HTTPException(status_code=403, detail="只能撤回自己的消息")
        if message.sender_user_id == user.id and not is_manager:
            age = (as_utc(now()) or now()) - (as_utc(message.created_at) or message.created_at)
            if age.total_seconds() > 120:
                raise HTTPException(status_code=403, detail="超过 2 分钟，不能撤回")
        message.deleted_at = now()
        db.flush()
        group_chat.post_message(
            db,
            group=group,
            sender=None,
            sender_kind="system",
            kind="system",
            content=f"{user.display_name or user.username} 撤回了一条消息",
        )
        audit.record(db, user.id, "group_message_recalled", message.id, "success")


@router.post("/{group_id}:analyze", response_model=AnalyzeResponse)
async def analyze(
    group_id: str,
    background: BackgroundTasks,
    user: User = Depends(current_user_from_bearer_or_cookie),
    sync: bool = Query(default=True),
) -> AnalyzeResponse:
    with transaction() as db:
        try:
            group, _ = group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc

    if not sync:
        background.add_task(_run_analysis_safely, group_id, "manual")
        return AnalyzeResponse(run_id="", status="queued", todos_created=0)

    unavailable = ""
    with transaction() as db:
        group = db.get(ProjectGroup, group_id)
        assert group is not None
        try:
            run = await group_assistant.analyze_group(db, group=group, trigger="manual")
        except group_assistant.GroupAssistantUnavailable as exc:
            # 没模型：不写记录，提交后再抛 503（事务内抛会把记录一起回滚）。
            unavailable = str(exc)
            run = None
        if run is not None:
            output = json.loads(run.output_json or "{}")
            latest = db.scalar(
                select(ProjectGroupMessage)
                .where(
                    ProjectGroupMessage.group_id == group_id,
                    ProjectGroupMessage.kind == "ai",
                )
                .order_by(ProjectGroupMessage.created_at.desc())
                .limit(1)
            )
            audit.record(
                db,
                user.id,
                "group_analyzed",
                group_id,
                "success" if run.status == "done" else "failed",
            )
            response = AnalyzeResponse(
                run_id=run.id,
                status=run.status,
                todos_created=run.todos_created,
                summary=str(output.get("summary") or ""),
                message_id=latest.id if latest else None,
                error=run.error,
            )
    if unavailable:
        raise HTTPException(status_code=503, detail=unavailable)
    if response.status != "done":
        raise HTTPException(
            status_code=502,
            detail=f"AI 分析失败：{response.error or '未知原因'}",
        )
    return response


@todo_router.get("", response_model=list[TodoResponse])
def list_todos(
    user: User = Depends(current_user_from_bearer_or_cookie),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[TodoResponse]:
    with transaction() as db:
        rows = group_assistant.list_my_todos(
            db, user_id=user.id, status=status, limit=limit
        )
        return [_todo_response(db, item) for item in rows]


@todo_router.patch("/{todo_id}", response_model=TodoResponse)
def update_todo(
    todo_id: str, payload: TodoStatusRequest, user: User = Depends(current_user_from_bearer_or_cookie)
) -> TodoResponse:
    with transaction() as db:
        todo = db.get(GroupTodo, todo_id)
        if todo is None:
            raise HTTPException(status_code=404, detail="待办不存在")
        try:
            group_assistant.set_todo_status(
                db, todo=todo, actor=user, status=payload.status
            )
        except (group_chat.GroupChatError,) as exc:
            raise _translate(exc) from exc
        audit.record(db, user.id, "group_todo_status", todo.id, "success")
        return _todo_response(db, todo)


@router.get("/{group_id}/runs", response_model=list[AnalyzeResponse])
def list_runs(
    group_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AnalyzeResponse]:
    with transaction() as db:
        try:
            group_chat.require_member(db, group_id, user)
        except group_chat.GroupChatError as exc:
            raise _translate(exc) from exc
        rows = db.scalars(
            select(ProjectGroupAiRun)
            .where(ProjectGroupAiRun.group_id == group_id)
            .order_by(ProjectGroupAiRun.created_at.desc())
            .limit(limit)
        )
        return [
            AnalyzeResponse(
                run_id=row.id,
                status=row.status,
                todos_created=row.todos_created,
                summary="",
            )
            for row in rows
        ]
