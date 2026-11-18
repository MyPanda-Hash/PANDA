"""Audit event helpers and presentation labels."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import iso, new_token, now
from app.models.auth import AuditEvent, User

EVENT_LABELS: dict[str, str] = {
    "agent_capability_call": "调用 AI 业务工具",
    "admin_writing_audit_list": "查询用户文稿列表",
    "admin_writing_audit_detail": "查看用户文稿详情",
    "admin_chat_audit_list": "查询用户会话列表",
    "admin_chat_audit_detail": "查看用户会话详情",
    "admin_upload_audit_list": "查询用户上传状态",
    "bootstrap_admin_created": "初始化系统管理员",
    "bootstrap_setup_completed": "完成管理员初始化",
    "login_succeeded": "登录成功",
    "login_failed": "登录失败",
    "logout": "退出登录",
    "invitation_accepted": "接受邀请",
    "password_changed": "修改密码",
    "user_invited": "邀请用户",
    "user_created": "创建用户",
    "user_restored": "恢复已删除用户",
    "user_disabled": "停用用户",
    "user_enabled": "启用用户",
    "user_deleted": "删除用户",
    "user_purged": "永久删除用户",
    "user_password_reset": "重置用户密码",
    "user_sessions_revoked": "吊销用户会话",
    "user_roles_updated": "更新用户角色",
    "role_created": "创建角色",
    "role_updated": "更新角色",
    "role_deleted": "删除角色",
    "model_price_version_created": "创建价格版本",
    "budget_rule_created": "创建预算规则",
    "budget_rule_updated": "调整预算规则",
    "budget_rule_enabled": "启用预算规则",
    "budget_rule_disabled": "停用预算规则",
    "budget_rule_deleted": "删除预算规则",
    "vision_budget_rejected": "视觉预算拒绝",
    "schedule_budget_rejected": "定时任务预算拒绝",
    "model_endpoint_created": "创建模型端点",
    "model_endpoint_updated": "更新模型端点",
    "model_endpoint_deleted": "删除模型端点",
    "notification_connection_created": "创建通知渠道",
    "notification_connection_updated": "修改通知渠道",
    "notification_target_created": "创建通知目标",
    "notification_target_updated": "修改通知目标",
    "notification_target_disabled": "停用通知目标",
    "notification_target_verified": "验证通知邮箱",
    "notification_verification_issued": "发起邮箱验证",
    "notification_grant_created": "授权群通知",
    "notification_grant_revoked": "撤销群通知授权",
    "notification_preference_updated": "更新外部通知偏好",
    "document_uploaded": "上传文档",
    "chat_message_saved_as_document": "聊天回复存为资料",
    "document_downloaded": "下载文档",
    "document_archived": "归档文档",
    "document_deleted": "删除文档",
    "document_retry": "重试文档处理",
    "document_moved_to_space": "移动文档到文件夹",
    "document_renamed": "重命名文档",
    "category_created": "创建文档分类",
    "category_deleted": "删除文档分类",
    "space_created": "创建知识空间",
    "space_updated": "修改知识空间",
    "space_deleted": "删除知识空间",
    "space_restored": "恢复知识空间",
    "space_member_added": "添加空间成员",
    "writing_member_added": "添加文稿成员",
    "writing_member_role_changed": "修改文稿成员角色",
    "writing_member_removed": "移除文稿成员",
    "writing_comment_created": "新增文稿评论",
    "writing_comment_resolved": "解决文稿评论",
    "writing_suggestion_published": "采纳文稿建议",
    "writing_suggestion_rejected": "拒绝文稿建议",
    "writing_review_requested": "提交文稿审阅",
    "writing_review_approved": "审批通过文稿",
    "writing_review_rejected": "驳回文稿",
    "writing_doc_issued": "文稿定稿导出",
}

RESULT_LABELS: dict[str, str] = {
    "success": "成功",
    "failed": "失败",
    "rejected": "拒绝",
    "locked": "锁定",
}


def record(
    db: Session,
    actor: str | None,
    event_type: str,
    target: str | None,
    result: str,
    request_id: str | None = None,
) -> str:
    event_id = f"aud_{new_token(10)}"
    db.add(
        AuditEvent(
            id=event_id,
            actor_user_id=actor,
            event_type=event_type,
            target_id=target,
            result=result,
            request_id=request_id,
            created_at=now(),
        )
    )
    return event_id


def has_successful_event(db: Session, event_type: str, target_id: str) -> bool:
    return (
        db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == event_type,
                AuditEvent.target_id == target_id,
                AuditEvent.result == "success",
            )
        )
        is not None
    )


def event_label(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, event_type)


def result_label(result: str) -> str:
    return RESULT_LABELS.get(result, result)


def serialize_event(event: AuditEvent, actor: User | None = None) -> dict:
    return {
        "id": event.id,
        "actor_user_id": event.actor_user_id,
        "actor_username": actor.username if actor else None,
        "actor_display_name": actor.display_name if actor else None,
        "event_type": event.event_type,
        "event_label": event_label(event.event_type),
        "target_id": event.target_id,
        "result": event.result,
        "result_label": result_label(event.result),
        "request_id": event.request_id,
        "created_at": iso(event.created_at),
    }


def list_events(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    result: str | None = None,
) -> tuple[list[dict], int]:
    filters = []
    if event_type:
        filters.append(AuditEvent.event_type == event_type)
    if result == "success":
        filters.append(AuditEvent.result == "success")
    elif result == "other":
        filters.append(AuditEvent.result != "success")

    count_query = select(func.count()).select_from(AuditEvent)
    query = select(AuditEvent)
    if filters:
        count_query = count_query.where(*filters)
        query = query.where(*filters)

    raw_total = db.scalar(count_query)
    try:
        total = int(raw_total or 0)
    except (TypeError, ValueError):
        total = 0
    events = list(
        db.scalars(
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset(max(0, offset))
            .limit(max(1, limit))
        )
    )
    actor_ids = {item.actor_user_id for item in events if item.actor_user_id}
    actors: dict[str, User] = {}
    if actor_ids:
        for user in db.scalars(select(User).where(User.id.in_(actor_ids))):
            actors[user.id] = user
    return [
        serialize_event(item, actors.get(item.actor_user_id or "")) for item in events
    ], total
