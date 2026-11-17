"""员工职能卡片：统计 + 蒸馏 + 项目经验沉淀。

设计原则：
1. **统计可复算**：所有数字都能从原始表重新算出来，不存储衍生猜测。
2. **蒸馏可选**：模型能给的只是「文字结论」，模型不可用时卡片照样有数字。
3. **管理员专属**：调用方必须是 admin，服务层也会再校验一次。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.core.timeutil import as_utc
from app.models.assistant_watch import AssistantEvent
from app.models.auth import AuditEvent, User
from app.models.chat import ChatMessage, ChatSession
from app.models.employee_profile import EmployeeProfile, ProjectExperience
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMessage

logger = logging.getLogger(__name__)

WEEK_START_WEEKDAY = 0  # 周一


class ProfileError(Exception):
    pass


class ProfileForbidden(ProfileError):
    pass


@dataclass
class ActivityStats:
    """一个周期内的行为统计。"""

    user_id: str
    period_start: datetime
    period_end: datetime
    chat_sessions: int = 0
    chat_messages: int = 0
    group_messages: int = 0
    todo_assigned: int = 0
    todo_completed: int = 0
    events_acked: int = 0
    actions_total: int = 0
    active_days: int = 0
    avg_response_hours: float | None = None
    action_breakdown: dict[str, int] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "chat_sessions": self.chat_sessions,
            "chat_messages": self.chat_messages,
            "group_messages": self.group_messages,
            "todo_assigned": self.todo_assigned,
            "todo_completed": self.todo_completed,
            "events_acked": self.events_acked,
            "actions_total": self.actions_total,
            "active_days": self.active_days,
            "avg_response_hours": self.avg_response_hours,
            "action_breakdown": self.action_breakdown,
            "groups": self.groups,
        }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def week_period(
    anchor: datetime | None = None, *, current_week: bool = False
) -> tuple[datetime, datetime]:
    """默认取「上一个完整自然周」；``current_week=True`` 取本周至今。

    周报用的是完整上周（数据已经跑完），而**首次生成卡片**用本周至今，
    否则新系统里所有人都显示「零活动」——这正是第一次实测暴露的问题。
    """
    current = (anchor or now()).astimezone()
    tz = current.tzinfo
    monday = current.date() - timedelta(days=current.weekday())
    if current_week:
        start_local = datetime.combine(monday, datetime.min.time()).replace(tzinfo=tz)
        return start_local.astimezone(timezone.utc), current.astimezone(timezone.utc)
    start_local = datetime.combine(monday - timedelta(days=7), datetime.min.time())
    end_local = datetime.combine(monday, datetime.min.time())
    return (
        start_local.replace(tzinfo=tz).astimezone(timezone.utc),
        end_local.replace(tzinfo=tz).astimezone(timezone.utc),
    )


def collect_stats(
    db: Session, *, user_id: str, period_start: datetime, period_end: datetime
) -> ActivityStats:
    """从原始表统计一个人在一个周期内的行为。"""
    start = _aware(period_start)
    end = _aware(period_end)
    stats = ActivityStats(
        user_id=user_id, period_start=period_start, period_end=period_end
    )

    # 个人会话
    sessions = list(
        db.scalars(
            select(ChatSession).where(
                ChatSession.owner_user_id == user_id,
                ChatSession.created_at >= start,
                ChatSession.created_at < end,
            )
        )
    )
    stats.chat_sessions = len(sessions)
    session_ids = {s.id for s in sessions}
    if session_ids:
        stats.chat_messages = int(
            db.scalar(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.session_id.in_(session_ids)
                )
            )
            or 0
        )

    # 群聊发言
    group_rows = list(
        db.scalars(
            select(ProjectGroupMessage).where(
                ProjectGroupMessage.sender_user_id == user_id,
                ProjectGroupMessage.created_at >= start,
                ProjectGroupMessage.created_at < end,
                ProjectGroupMessage.deleted_at.is_(None),
            )
        )
    )
    stats.group_messages = len(group_rows)
    if group_rows:
        group_ids = {row.group_id for row in group_rows}
        stats.groups = [
            row.name
            for row in db.scalars(
                select(ProjectGroup).where(ProjectGroup.id.in_(group_ids))
            )
        ]

    # 待办
    todos = list(
        db.scalars(
            select(GroupTodo).where(GroupTodo.assignee_user_id == user_id)
        )
    )
    for todo in todos:
        created = _aware(todo.created_at)
        if created and start <= created < end:
            stats.todo_assigned += 1
        completed = _aware(todo.completed_at)
        if completed and start <= completed < end:
            stats.todo_completed += 1

    # AI 派单的认领与响应时长
    responses: list[float] = []
    for event in db.scalars(select(AssistantEvent)):
        if event.acked_by_user_id != user_id:
            continue
        discovered = _aware(event.first_seen_at or event.created_at)
        acked = _aware(event.updated_at)
        if not (discovered and acked):
            continue
        if start <= acked < end:
            stats.events_acked += 1
            delta = (acked - discovered).total_seconds() / 3600
            if delta >= 0:
                responses.append(delta)
    if responses:
        responses.sort()
        stats.avg_response_hours = round(
            responses[len(responses) // 2], 1  # 中位数比均值抗异常
        )

    # 审计动作（后台行为轨迹）
    audit_rows = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == user_id,
                AuditEvent.created_at >= start,
                AuditEvent.created_at < end,
            )
        )
    )
    stats.actions_total = len(audit_rows)
    for row in audit_rows:
        stats.action_breakdown[row.event_type] = (
            stats.action_breakdown.get(row.event_type, 0) + 1
        )

    # 活跃天数：任一行为发生的不同日期数
    days: set[str] = set()
    for row in audit_rows:
        stamp = _aware(row.created_at)
        if stamp:
            days.add(stamp.date().isoformat())
    for message in group_rows:
        stamp = _aware(message.created_at)
        if stamp:
            days.add(stamp.date().isoformat())
    stats.active_days = len(days)

    return stats


ACTION_LABELS = {
    "login_succeeded": "登录",
    "login_mes_sso": "从 MES 单点登录",
    "group_created": "建群",
    "group_updated": "改群信息",
    "group_archived": "归档群",
    "group_deleted": "删除群",
    "group_member_added": "邀请成员",
    "group_member_removed": "移除成员",
    "group_analyzed": "触发 AI 分析",
    "group_todo_status": "更新待办状态",
    "group_message_recalled": "撤回消息",
    "agent_capability_call": "调用智能能力",
    "model_endpoint_created": "配置模型端点",
    "model_endpoint_updated": "修改模型端点",
    "model_endpoint_tested": "测试模型端点",
}


def render_activity_digest(stats: ActivityStats) -> str:
    """把行为统计排成给人看的一段轨迹摘要。"""
    if not stats.actions_total and not stats.group_messages and not stats.chat_messages:
        return "本周期没有可观察的操作记录。"
    parts: list[str] = []
    if stats.active_days:
        parts.append(f"活跃 {stats.active_days} 天")
    if stats.chat_messages:
        parts.append(f"个人会话 {stats.chat_sessions} 个 / {stats.chat_messages} 条消息")
    if stats.group_messages:
        where = "、".join(stats.groups[:3]) if stats.groups else ""
        parts.append(f"群聊发言 {stats.group_messages} 条" + (f"（{where}）" if where else ""))
    if stats.todo_assigned:
        parts.append(f"接收待办 {stats.todo_assigned} 条、完成 {stats.todo_completed} 条")
    if stats.events_acked:
        parts.append(f"认领 AI 派单 {stats.events_acked} 次")
    if stats.avg_response_hours is not None:
        parts.append(f"响应中位 {stats.avg_response_hours} 小时")
    if stats.action_breakdown:
        detail = "、".join(
            f"{ACTION_LABELS.get(k, k)} {v}"
            for k, v in sorted(stats.action_breakdown.items(), key=lambda kv: -kv[1])[:6]
        )
        parts.append(f"主要操作：{detail}")
    return "；".join(parts) + "。"


def _is_silent(stats: ActivityStats) -> bool:
    """整周期没有任何可观察行为。

    注意：**有派单也算有事实**——"被派了待办但一条没动"本身就是最重要的结论，
    不能因为没登录记录就当成"无数据"。
    """
    return not (
        stats.actions_total
        or stats.chat_messages
        or stats.group_messages
        or stats.todo_assigned
        or stats.events_acked
    )


def _rules_profile(stats: ActivityStats) -> dict[str, Any]:
    """不依赖模型时的结论：按阈值给出朴素的强弱项。"""
    strengths: list[str] = []
    risks: list[str] = []
    focus: list[str] = []

    if stats.todo_assigned and stats.todo_completed >= stats.todo_assigned:
        strengths.append("本周期分配的待办全部完成")
    if stats.avg_response_hours is not None and stats.avg_response_hours <= 4:
        strengths.append(f"响应及时（中位 {stats.avg_response_hours} 小时）")
    if stats.group_messages >= 5:
        strengths.append("在项目群里保持沟通")
    if stats.todo_assigned and stats.todo_completed == 0:
        risks.append("有派单但本周期没有完成记录")
    if stats.todo_assigned and stats.todo_completed < stats.todo_assigned:
        focus.append(f"还有 {stats.todo_assigned - stats.todo_completed} 条待办未闭环")
    if stats.avg_response_hours is not None and stats.avg_response_hours > 24:
        risks.append("认领响应偏慢（超过 24 小时）")
    if not stats.actions_total and not stats.chat_messages and not stats.group_messages:
        risks.append("本周期几乎没有系统内活动")
    if stats.active_days and stats.active_days < 2:
        focus.append("活跃天数较少，建议确认是否有账号共用或代操作")

    return {
        "summary": "",
        "strengths": strengths,
        "risks": risks,
        "focus_areas": focus,
        "capability_tags": _tags_from_actions(stats),
    }


def _tags_from_actions(stats: ActivityStats) -> list[str]:
    """从操作类型反推职能标签（朴素规则）。"""
    tags: list[str] = []
    actions = set(stats.action_breakdown)
    if actions & {"group_created", "group_member_added", "group_archived"}:
        tags.append("项目组织")
    if "group_analyzed" in actions:
        tags.append("AI 调度")
    if "group_todo_status" in actions:
        tags.append("任务执行")
    if actions & {"model_endpoint_created", "model_endpoint_updated"}:
        tags.append("平台配置")
    if stats.chat_messages >= 10:
        tags.append("深度使用 AI")
    if stats.group_messages >= 10:
        tags.append("协作沟通")
    return tags[:5]


DISTILL_PROMPT = """你是 HR 与运营管理顾问，为一位员工写职能画像。

只能依据我给出的统计数字和操作类型，**不要编造任何具体事件、客户名或单号**。

输出一个 JSON 对象（不要代码块标记）：
{
  "summary": "两三句话概括这个人的工作状态与角色特征",
  "strengths": ["看到的具体优势，最多 3 条"],
  "risks": ["需要注意的问题，最多 2 条，没有就空数组"],
  "focus_areas": ["下周期该关注什么，最多 3 条"],
  "capability_tags": ["职能标签，2-5 个"]
}"""


def build_distill_prompt(stats: ActivityStats, *, display_name: str) -> str:
    lines = [
        f"员工：{display_name}",
        f"统计周期：{stats.period_start.astimezone().date()} ~ {stats.period_end.astimezone().date()}",
        "",
        "【确定的事实】",
        f"- 活跃天数：{stats.active_days}",
        f"- 个人会话：{stats.chat_sessions} 个，消息 {stats.chat_messages} 条",
        f"- 群聊发言：{stats.group_messages} 条",
        f"- 接收待办：{stats.todo_assigned} 条，完成 {stats.todo_completed} 条",
        f"- 认领 AI 派单：{stats.events_acked} 次",
    ]
    if stats.avg_response_hours is not None:
        lines.append(f"- 响应中位时长：{stats.avg_response_hours} 小时")
    if stats.action_breakdown:
        lines.append("- 操作类型分布：")
        for kind, count in sorted(stats.action_breakdown.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {ACTION_LABELS.get(kind, kind)}: {count} 次")
    if stats.groups:
        lines.append(f"- 参与的项目群：{'、'.join(stats.groups[:5])}")
    lines.append("")
    lines.append("请基于以上事实输出 JSON。")
    return "\n".join(lines)


def parse_distilled(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}

    def _list(key: str, limit: int) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(x).strip() for x in value if str(x).strip()][:limit]

    return {
        "summary": str(data.get("summary") or "").strip(),
        "strengths": _list("strengths", 3),
        "risks": _list("risks", 2),
        "focus_areas": _list("focus_areas", 3),
        "capability_tags": _list("capability_tags", 5),
    }


def _slugify(text: str) -> str:
    """把标题压成稳定 slug：同类经验要能归到同一个 topic。

    中文没有空格分词，所以用「去掉标点 + 取前若干字符」的朴素做法，
    保证同一件事反复出现时能落到同一个键上。
    """
    import re as _re

    cleaned = _re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text.strip().lower())
    cleaned = _re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:60] or "untitled"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def distill_user(
    db: Session,
    *,
    user: User,
    anchor: datetime | None = None,
    period: tuple[datetime, datetime] | None = None,
    distilled: dict[str, Any] | None = None,
) -> EmployeeProfile:
    """生成（或刷新）某人某个周期的职能卡片。

    ``distilled``：模型给出的结论；为空时退回规则版结论。
    """
    start, end = period or week_period(anchor, current_week=True)
    stats = collect_stats(db, user_id=user.id, period_start=start, period_end=end)
    silent = _is_silent(stats)
    if silent:
        # 零数据时不让模型猜职能——它只会照着岗位名编，属于编造
        distilled = None
    elif not (stats.actions_total or stats.chat_messages or stats.group_messages):
        # 只有派单、没有任何系统内动作：模型也只能猜，退回规则
        distilled = None

    profile = db.scalar(
        select(EmployeeProfile).where(
            EmployeeProfile.user_id == user.id,
            EmployeeProfile.period_start == start,
        )
    )
    if profile is None:
        profile = EmployeeProfile(
            id=_new_id("emp"),
            user_id=user.id,
            period_start=start,
            period_end=end,
            generated_at=now(),
        )
        db.add(profile)

    profile.period_end = end
    profile.chat_sessions = stats.chat_sessions
    profile.chat_messages = stats.chat_messages
    profile.group_messages = stats.group_messages
    profile.todo_assigned = stats.todo_assigned
    profile.todo_completed = stats.todo_completed
    profile.events_acked = stats.events_acked
    profile.actions_total = stats.actions_total
    profile.active_days = stats.active_days
    profile.avg_response_hours = stats.avg_response_hours
    profile.activity_digest = render_activity_digest(stats)

    result = _rules_profile(stats)
    if silent:
        result = {
            "summary": "本周期没有系统内的操作或协作记录。",
            "strengths": [],
            "risks": ["整周期无任何系统活动记录，需确认是在岗、休假还是账号未使用"],
            "focus_areas": ["确认该员工的实际工作入口（是否从 MES 单点登录）"],
            "capability_tags": [],
        }
    elif distilled:
        for key in ("summary", "strengths", "risks", "focus_areas", "capability_tags"):
            if distilled.get(key):
                result[key] = distilled[key]
        profile.generated_by = "model"
    else:
        profile.generated_by = "rules"

    # 模型没给 summary 时用规则拼接一句，保证卡片不留空
    if not result["summary"]:
        bits = [f"活跃 {stats.active_days} 天"]
        if stats.todo_assigned:
            bits.append(f"待办 {stats.todo_completed}/{stats.todo_assigned} 完成")
        if stats.events_acked:
            bits.append(f"认领 AI 派单 {stats.events_acked} 次")
        result["summary"] = "本周期" + "、".join(bits) + "。"

    profile.summary = result["summary"]
    profile.strengths = json.dumps(result["strengths"], ensure_ascii=False)
    profile.risks = json.dumps(result["risks"], ensure_ascii=False)
    profile.focus_areas = json.dumps(result["focus_areas"], ensure_ascii=False)
    profile.capability_tags = json.dumps(result["capability_tags"], ensure_ascii=False)
    profile.generated_at = now()
    db.flush()
    return profile


async def distill_user_async(
    db: Session,
    *,
    user: User,
    anchor: datetime | None = None,
    period: tuple[datetime, datetime] | None = None,
    with_model: bool = True,
) -> EmployeeProfile:
    """带模型解读的蒸馏；模型不可用时退回规则版。"""
    start, end = period or week_period(anchor, current_week=True)
    distilled: dict[str, Any] = {}
    if with_model:
        stats = collect_stats(db, user_id=user.id, period_start=start, period_end=end)
        try:
            from app.services.plain_llm import (
                LlmCallFailed,
                LlmUnavailable,
                complete_chat,
            )

            reply = await complete_chat(
                db,
                [
                    {"role": "system", "content": DISTILL_PROMPT},
                    {
                        "role": "user",
                        "content": build_distill_prompt(
                            stats, display_name=user.display_name or user.username
                        ),
                    },
                ],
                response_format_json=True,
                disable_thinking=True,
                max_tokens=800,
            )
            distilled = parse_distilled(reply.text)
        except (LlmUnavailable, LlmCallFailed) as exc:
            logger.info("employee_profile.model_unavailable", extra={"error": str(exc)})
        except Exception:
            logger.exception("employee_profile.distill_failed")
    return distill_user(db, user=user, period=(start, end), distilled=distilled)


def require_admin(actor: User) -> None:
    if actor.role != "admin":
        raise ProfileForbidden("只有管理员可以查看员工画像")


def list_profiles(
    db: Session,
    *,
    actor: User,
    user_id: str | None = None,
    limit: int = 50,
) -> list[EmployeeProfile]:
    require_admin(actor)
    stmt = select(EmployeeProfile)
    if user_id:
        stmt = stmt.where(EmployeeProfile.user_id == user_id)
    return list(
        db.scalars(
            stmt.order_by(EmployeeProfile.period_start.desc()).limit(max(1, min(limit, 200)))
        )
    )


# ---------------------------------------------------------------------------
# 项目经验资料
# ---------------------------------------------------------------------------


def extract_experience(
    db: Session,
    *,
    group_id: str,
    period_start: datetime,
    period_end: datetime,
    distilled: dict[str, Any] | None = None,
) -> ProjectExperience | None:
    """从一个项目群的讨论里沉淀一条经验。

    没有 ``distilled``（模型结论）时不生成——经验资料必须有人可读的结论，
    不能只有统计数字。
    """
    group = db.get(ProjectGroup, group_id)
    if group is None:
        return None
    data = distilled or {}
    title = str(data.get("title") or "").strip()
    if not title:
        return None

    messages = list(
        db.scalars(
            select(ProjectGroupMessage).where(
                ProjectGroupMessage.group_id == group_id,
                ProjectGroupMessage.created_at >= _aware(period_start),
                ProjectGroupMessage.created_at < _aware(period_end),
                ProjectGroupMessage.sender_user_id.is_not(None),
            )
        )
    )
    contributors = sorted(
        {
            message.sender_user_id
            for message in messages
            if message.sender_user_id
        }
    )

    # 同群同标题视为已沉淀，避免每周重复生成同一条经验
    existing = db.scalar(
        select(ProjectExperience).where(
            ProjectExperience.group_id == group_id,
            ProjectExperience.title == title[:255],
        )
    )
    if existing is not None:
        return existing

    topic = _slugify(str(data.get("topic") or title))
    record = ProjectExperience(
        id=_new_id("pexp"),
        group_id=group_id,
        topic=topic,
        title=title[:255],
        problem=str(data.get("problem") or "").strip(),
        resolution=str(data.get("resolution") or "").strip(),
        lesson=str(data.get("lesson") or "").strip(),
        tags=json.dumps(data.get("tags") or [], ensure_ascii=False),
        source="group",
        source_ref=group.name,
        contributors=json.dumps(contributors, ensure_ascii=False),
        # 从群聊自动提炼的经验，默认「未验证」：模型总结不等于验证过
        status=str(data.get("status") or "unverified"),
        reusable=bool(data.get("reusable", False)),
        not_applicable=str(data.get("not_applicable") or "").strip(),
        # 一条经验至少有群名 + 发言两个人，才算两个独立来源
        source_count=min(2, max(1, 1 + (1 if len(contributors) >= 2 else 0))),
        created_at=now(),
    )
    db.add(record)
    db.flush()
    return record


EXTRACT_PROMPT = """你是制造企业的知识管理顾问，从项目群聊记录里提炼可复用的经验。

以下四类都算可沉淀的结论，任意命中一类就要提炼：
- **问题解决**：出现了问题并有处理办法（如质量异常、交期冲突怎么化解）
- **分工确定**：明确了谁负责什么、交期怎么排
- **决策形成**：定下了方案、标准、先后顺序
- **经验纠正**：发现之前做法不对，改成了新做法

只有以下情况才输出 skip：纯粹寒暄、纯系统通知、纯重复内容、没有形成任何共识。

规则：
1. 不能编造记录里没有的事实、单号、人名。
2. 有结论时输出一个 JSON 对象，不要代码块标记。

输出格式：
{
  "skip": false,
  "title": "12 字以内的经验标题",
  "problem": "遇到的问题，一两句",
  "resolution": "怎么处理的，一两句",
  "lesson": "以后遇到类似情况该怎么做，一句话",
  "tags": ["标签1", "标签2"]
}"""

MIN_MESSAGES_FOR_EXPERIENCE = 4
# 只有 AI 自发通知、没有人类参与的群不值得沉淀
MIN_HUMAN_MESSAGES = 2


def build_extract_prompt(group_name: str, messages: list[ProjectGroupMessage],
                         names: dict[str, str]) -> str:
    lines = [f"项目群：{group_name}", "", "【聊天记录】"]
    for message in messages:
        who = "AI 助手" if message.sender_kind == "ai" else names.get(
            message.sender_user_id or "", "成员"
        )
        lines.append(f"{who}：{(message.content or '').strip()}")
    lines.append("")
    lines.append("请提炼这段讨论里的经验，没有可沉淀的就输出 skip。")
    return "\n".join(lines)


def parse_extracted(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("skip"):
        return {}
    tags = data.get("tags")
    return {
        "title": str(data.get("title") or "").strip(),
        "problem": str(data.get("problem") or "").strip(),
        "resolution": str(data.get("resolution") or "").strip(),
        "lesson": str(data.get("lesson") or "").strip(),
        "tags": [str(x).strip() for x in tags if str(x).strip()][:5]
        if isinstance(tags, list)
        else [],
    }


async def extract_from_groups(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
    with_model: bool = True,
    max_messages: int = 40,
    group_ids: list[str] | None = None,
) -> list[ProjectExperience]:
    """把一个周期内有实质讨论的项目群逐个提炼成经验条目。

    只处理消息数达到门槛的群；模型判断没有结论就跳过（``skip``）。

    ``group_ids`` 指定时不再看群状态——解散（归档）的群正是最该提炼的，
    它的讨论已经结束，素材不会再变。
    """
    start = _aware(period_start)
    end = _aware(period_end)
    wanted = set(group_ids or [])
    all_groups = list(db.scalars(select(ProjectGroup)))
    if wanted:
        groups = [row for row in all_groups if row.id in wanted]
    else:
        groups = [row for row in all_groups if row.status == "active"]

    created: list[ProjectExperience] = []
    for group in groups:
        # 先把上一轮可能挂起的写入提交掉：模型调用要几十秒，攥着 SQLite 写锁
        # 别人连登录都写不进去（历史故障：database is locked）。
        db.commit()
        messages = list(
            db.scalars(
                select(ProjectGroupMessage)
                .where(
                    ProjectGroupMessage.group_id == group.id,
                    ProjectGroupMessage.created_at >= start,
                    ProjectGroupMessage.created_at < end,
                    ProjectGroupMessage.deleted_at.is_(None),
                    ProjectGroupMessage.sender_kind.in_(("user", "ai")),
                )
                .order_by(ProjectGroupMessage.created_at)
                .limit(max_messages)
            )
        )
        human_messages = [
            m for m in messages if m.sender_kind == "user" and (m.content or "").strip()
        ]
        if len(messages) < MIN_MESSAGES_FOR_EXPERIENCE:
            continue
        if len(human_messages) < MIN_HUMAN_MESSAGES:
            # 全是 AI 通知/汇总的群没有讨论价值，省一次模型调用
            continue
        if not with_model:
            continue

        user_ids = {m.sender_user_id for m in messages if m.sender_user_id}
        names = {
            user.id: (user.display_name or user.username)
            for user in db.scalars(select(User).where(User.id.in_(user_ids)))
        } if user_ids else {}

        distilled: dict[str, Any] = {}
        try:
            from app.services.plain_llm import (
                LlmCallFailed,
                LlmUnavailable,
                complete_chat,
            )

            reply = await complete_chat(
                db,
                [
                    {"role": "system", "content": EXTRACT_PROMPT},
                    {
                        "role": "user",
                        "content": build_extract_prompt(group.name, messages, names),
                    },
                ],
                response_format_json=True,
                disable_thinking=True,
                max_tokens=800,
            )
            distilled = parse_extracted(reply.text)
        except (LlmUnavailable, LlmCallFailed) as exc:
            logger.info("experience.model_unavailable", extra={"error": str(exc)})
            continue
        except Exception:
            logger.exception("experience.extract_failed")
            continue

        if not distilled.get("title"):
            continue
        record = extract_experience(
            db,
            group_id=group.id,
            period_start=period_start,
            period_end=period_end,
            distilled=distilled,
        )
        if record is not None:
            created.append(record)
        # 写完立刻提交：下一个群还要调模型，不能带着写锁去等
        db.commit()
    return created


# ---------------------------------------------------------------------------
# 证据晋升（借鉴 skill-cairn 的 promotion gate）
# ---------------------------------------------------------------------------

# 默认门槛，与 skill-cairn 一致；可在配置里调
PROMOTION_MIN_CARDS = 3          # 同 topic 至少 3 张卡
PROMOTION_MIN_VERIFIED = 2       # 其中至少 2 张已验证
PROMOTION_MIN_SOURCES = 2        # 至少 2 个独立来源


def promotion_readiness(
    db: Session,
    *,
    topic: str,
    min_cards: int = PROMOTION_MIN_CARDS,
    min_verified: int = PROMOTION_MIN_VERIFIED,
    min_sources: int = PROMOTION_MIN_SOURCES,
) -> dict[str, Any]:
    """判断某个 topic 是否够格晋升成 SOP。

    四个条件全部满足才算通过——重复出现不等于正确，
    一个反复犯的错也会反复出现，所以还要求验证过 + 独立来源 + 可复用标记。
    """
    cards = list(
        db.scalars(select(ProjectExperience).where(ProjectExperience.topic == topic))
    )
    verified = [c for c in cards if c.status == "verified"]
    sources = {c.source_ref for c in cards if c.source_ref}
    reusable = [c for c in cards if c.reusable]

    checks = {
        "enough_cards": {
            "passed": len(cards) >= min_cards,
            "actual": len(cards),
            "required": min_cards,
            "label": "同类经验条数",
        },
        "enough_verified": {
            "passed": len(verified) >= min_verified,
            "actual": len(verified),
            "required": min_verified,
            "label": "已验证条数",
        },
        "independent_sources": {
            "passed": len(sources) >= min_sources,
            "actual": len(sources),
            "required": min_sources,
            "label": "独立来源数",
        },
        "has_reusable": {
            "passed": bool(reusable),
            "actual": len(reusable),
            "required": 1,
            "label": "标记可复用的条数",
        },
    }
    return {
        "topic": topic,
        "ready": all(item["passed"] for item in checks.values()),
        "cards": len(cards),
        "checks": checks,
    }


def set_experience_status(
    db: Session,
    *,
    experience_id: str,
    status: str,
    reusable: bool | None = None,
    not_applicable: str | None = None,
) -> ProjectExperience:
    """人工确认证据状态。只有管理员能通过 API 调到。"""
    if status not in ("unverified", "verified", "stale"):
        raise ProfileError(f"不支持的状态：{status}")
    record = db.get(ProjectExperience, experience_id)
    if record is None:
        raise ProfileError("经验条目不存在")
    record.status = status
    if reusable is not None:
        record.reusable = reusable
    if not_applicable is not None:
        record.not_applicable = not_applicable.strip()
    db.flush()
    return record


def list_promotion_candidates(db: Session) -> list[dict[str, Any]]:
    """列出所有 topic 的晋升就绪情况，够格的在前面。"""
    topics = {
        topic
        for topic in db.scalars(select(ProjectExperience.topic))
        if topic
    }
    rows = [promotion_readiness(db, topic=topic) for topic in topics]
    rows.sort(key=lambda r: (not r["ready"], -r["cards"]))
    return rows


# ---------------------------------------------------------------------------
# 语义去重（借鉴 skill-cairn 的 dedupe）
# ---------------------------------------------------------------------------


def _ngrams(text: str, size: int = 3) -> set[str]:
    """中文用字符 n-gram，英文用词 n-gram，够用且零依赖。"""
    normalized = _slugify(text).replace("-", "")
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def similarity(a: str, b: str) -> float:
    """Jaccard 相似度，0~1。"""
    left, right = _ngrams(a), _ngrams(b)
    if not left or not right:
        return 0.0
    return round(len(left & right) / len(left | right), 3)


def find_duplicates(
    db: Session, *, threshold: float = 0.55, limit: int = 200
) -> list[dict[str, Any]]:
    """找出可能重复的经验条目，供人工合并。"""
    rows = list(
        db.scalars(
            select(ProjectExperience)
            .order_by(ProjectExperience.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
    )
    pairs: list[dict[str, Any]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            score = similarity(rows[i].title, rows[j].title)
            if score >= threshold:
                pairs.append(
                    {
                        "left": {"id": rows[i].id, "title": rows[i].title},
                        "right": {"id": rows[j].id, "title": rows[j].title},
                        "similarity": score,
                    }
                )
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs


def list_experiences(
    db: Session, *, actor: User, keyword: str = "", limit: int = 50
) -> list[ProjectExperience]:
    require_admin(actor)
    stmt = select(ProjectExperience)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            ProjectExperience.title.like(like)
            | ProjectExperience.problem.like(like)
            | ProjectExperience.resolution.like(like)
        )
    return list(
        db.scalars(
            stmt.order_by(ProjectExperience.created_at.desc()).limit(max(1, min(limit, 200)))
        )
    )
