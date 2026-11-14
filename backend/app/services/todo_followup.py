"""待办状态回流。

待办的「状态」由人改（群里点接受 / 开始 / 完成），AI 这边只负责三件事：

1. **算**：这条待办现在是「刚派下去 / 还没开始 / 已超期 / 进行中但停住了」，
   停了多久，超期多久——全部由代码算，不让模型猜天数。
2. **催**：到了阈值就在群里 @ 当事人催一次，带冷却时间，不刷屏。
3. **升级**：催到上限还没动静，转给派活儿的人（或组长），并在待办上留痕。

语音助手通过 ``query_todo_status`` / ``nudge_todo`` 两个工具读写这里的能力。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import now as _now
from app.core.timeutil import as_utc
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMember

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("pending", "accepted", "doing")

STATE_LABELS = {
    "fresh": "刚派下去",
    "idle": "还没开始",
    "overdue": "已超期",
    "stuck": "进行中但停住了",
    "accepted": "已接受，还没开工",
    "doing": "进行中",
    "done": "已完成",
    "dismissed": "已取消",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return as_utc(value)


def classify(todo: GroupTodo, *, now: datetime | None = None) -> dict[str, Any]:
    """算一条待办现在是什么状态、停了多久、超期多久。"""
    moment = now or _now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=as_utc(moment).tzinfo)

    status = todo.status
    last_touch = _utc(todo.last_action_at) or _utc(todo.created_at) or moment
    idle_hours = max(0.0, (moment - last_touch).total_seconds() / 3600.0)
    due = _utc(todo.due_at)
    overdue_hours = 0.0
    if due is not None and status in OPEN_STATUSES:
        overdue_hours = max(0.0, (moment - due).total_seconds() / 3600.0)

    if status in ("done", "dismissed"):
        state = status
    elif overdue_hours > 0:
        state = "overdue"
    elif status == "pending" and idle_hours >= settings.todo_idle_nudge_hours:
        state = "idle"
    elif status == "doing" and idle_hours >= settings.todo_stuck_hours:
        state = "stuck"
    elif status in ("accepted", "doing"):
        state = status
    else:
        state = "fresh"

    return {
        "state": state,
        "label": STATE_LABELS.get(state, state),
        "idleHours": round(idle_hours, 1),
        "overdueHours": round(overdue_hours, 1),
        "nudgeCount": int(todo.nudge_count or 0),
        "nudgedAt": _utc(todo.nudged_at),
        "escalatedAt": _utc(todo.escalated_at),
        "ago": _humanize(idle_hours),
    }


def _humanize(hours: float) -> str:
    if hours < 1:
        return "刚更新"
    if hours < 24:
        return f"{int(hours)} 小时"
    return f"{int(hours // 24)} 天"


def describe(todo: GroupTodo, *, now: datetime | None = None) -> str:
    """一句话说清：谁、什么事、什么状态。"""
    info = classify(todo, now=now)
    parts = [info["label"]]
    if info["overdueHours"]:
        parts.append(f"超期 {_humanize(info['overdueHours'])}")
    elif info["state"] != "fresh":
        parts.append(f"已 {info['ago']}没动")
    if info["nudgeCount"]:
        parts.append(f"催过 {info['nudgeCount']} 次")
    return f"{todo.title}（{'，'.join(parts)}）"


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #


def open_todos(
    db: Session,
    *,
    person_id: str | None = None,
    keyword: str = "",
    limit: int = 20,
) -> list[GroupTodo]:
    stmt = select(GroupTodo).where(GroupTodo.status.in_(OPEN_STATUSES))
    if person_id:
        stmt = stmt.where(GroupTodo.assignee_user_id == person_id)
    if keyword.strip():
        stmt = stmt.where(GroupTodo.title.contains(keyword.strip()))
    stmt = stmt.order_by(GroupTodo.created_at.asc()).limit(max(1, min(limit, 100)))
    return list(db.scalars(stmt))


def attention_needed(
    db: Session, *, person_id: str | None = None, limit: int = 20
) -> list[GroupTodo]:
    """只挑真正需要动的：没开始、卡住、超期。"""
    rows = open_todos(db, person_id=person_id, limit=200)
    moment = _now()
    picked = [row for row in rows if classify(row, now=moment)["state"] in ("idle", "stuck", "overdue")]
    return picked[: max(1, min(limit, 100))]


def status_overview(
    db: Session, *, assignee: User | None = None, keyword: str = "", limit: int = 10
) -> dict[str, Any]:
    """给语音/接口用：一堆待办 + 一句能直接念的汇总。"""
    moment = _now()
    rows = open_todos(
        db, person_id=assignee.id if assignee else None, keyword=keyword, limit=limit
    )
    items = []
    for row in rows:
        owner = db.get(User, row.assignee_user_id)
        info = classify(row, now=moment)
        items.append(
            {
                "id": row.id,
                "title": row.title,
                "person": owner.display_name if owner else "",
                "state": info["state"],
                "label": info["label"],
                "idleHours": info["idleHours"],
                "overdueHours": info["overdueHours"],
                "nudgeCount": info["nudgeCount"],
                "priority": row.priority,
            }
        )

    if not items:
        scope = f"{assignee.display_name}名下" if assignee else "系统里"
        return {
            "count": 0,
            "items": [],
            "text": f"{scope}没有未完成的待办。",
        }

    by_state: dict[str, int] = {}
    for item in items:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
    risky = by_state.get("idle", 0) + by_state.get("stuck", 0) + by_state.get("overdue", 0)

    lines = []
    for item in items[:5]:
        who = f"{item['person']}的" if item["person"] else ""
        tail = item["label"]
        if item["overdueHours"]:
            tail += f"，超期 {_humanize(item['overdueHours'])}"
        elif item["idleHours"] >= 1 and item["state"] != "fresh":
            tail += f"，已 {_humanize(item['idleHours'])}没动"
        if item["nudgeCount"]:
            tail += f"，催过 {item['nudgeCount']} 次"
        lines.append(f"{who}{item['title']}：{tail}")

    head = f"未完成的待办 {len(items)} 条"
    if risky:
        head += f"，其中 {risky} 条卡住了"
    return {
        "count": len(items),
        "items": items,
        "byState": by_state,
        "attention": risky,
        "text": head + "。" + "；".join(lines) + "。",
    }


# --------------------------------------------------------------------------- #
# 催
# --------------------------------------------------------------------------- #


def _feed_group(db: Session) -> ProjectGroup | None:
    from app.services.assistant_watch import ensure_feed_group

    return ensure_feed_group(db)


def _announce(db: Session, *, group: ProjectGroup | None, content: str, part: dict) -> bool:
    """落一条 AI 消息。没有群就算没发出去，不能假装催到了。"""
    if group is None:
        logger.warning("todo_followup.no_feed_group")
        return False
    from app.services import group_chat

    group_chat.post_message(
        db,
        group=group,
        sender=None,
        sender_kind="ai",
        kind="ai",
        content=content,
        parts=[part],
    )
    return True


def can_nudge(todo: GroupTodo, *, now: datetime | None = None) -> tuple[bool, str]:
    """冷却判断：刚催过就别再催，避免刷屏。"""
    moment = now or _now()
    nudged = _utc(todo.nudged_at)
    if nudged is None:
        return True, ""
    waited = (moment - nudged).total_seconds() / 3600.0
    if waited >= settings.todo_nudge_cooldown_hours:
        return True, ""
    left = settings.todo_nudge_cooldown_hours - waited
    return False, f"{int(todo.nudge_count or 0)} 小时前刚催过，再等 {left:.0f} 小时"


def nudge(
    db: Session,
    *,
    todo: GroupTodo,
    by_label: str = "AI",
    reason: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """在群里 @ 当事人催一次，并记下催办痕迹。"""
    moment = _now()
    if not force:
        ok, why = can_nudge(todo, now=moment)
        if not ok:
            return {"ok": False, "reason": why}

    owner = db.get(User, todo.assignee_user_id)
    if owner is None:
        return {"ok": False, "reason": "找不到接收人"}

    info = classify(todo, now=moment)
    detail = reason or (
        f"{info['label']}，已 {info['ago']}没动" if info["ago"] != "刚更新" else info["label"]
    )
    if info["overdueHours"]:
        detail = f"已超期 {_humanize(info['overdueHours'])}"

    content = f"@{owner.username} 提醒一下：{todo.title}（{detail}）。"
    delivered = _announce(
        db,
        group=_feed_group(db),
        content=content,
        part={"type": "todo_nudge", "todoId": todo.id, "by": by_label},
    )
    if not delivered:
        return {"ok": False, "reason": "没有可用的通知群，这条催办发不出去"}
    todo.nudged_at = moment
    todo.nudge_count = int(todo.nudge_count or 0) + 1
    todo.updated_at = moment

    from app.services import inapp_notifications

    inapp_notifications.push(
        db,
        user_id=owner.id,
        kind="todo_nudged",
        title=f"催办：{todo.title}",
        body=detail,
        ref_type="todo",
        ref_id=todo.id,
        group_id=todo.group_id,
        actor_name=by_label,
        dedupe_key=f"todo_nudged:{todo.id}:{todo.nudge_count}",
    )
    logger.info("todo_followup.nudged todo=%s count=%s", todo.id, todo.nudge_count)
    return {
        "ok": True,
        "count": todo.nudge_count,
        "person": owner.display_name,
        "detail": detail,
        "text": f"已经在群里催了{owner.display_name}一次：{todo.title}（{detail}）。",
    }


def escalate(db: Session, *, todo: GroupTodo, by_label: str = "AI") -> dict[str, Any]:
    """催到上限还没动：交给派活儿的人（没有就找组长），并留痕。"""
    moment = _now()
    owner = db.get(User, todo.assignee_user_id)
    owner_name = owner.display_name if owner else "对方"

    target: User | None = None
    if todo.created_by_user_id:
        target = db.get(User, todo.created_by_user_id)
    if target is None and todo.group_id:
        group = db.get(ProjectGroup, todo.group_id)
        if group is not None and group.owner_user_id:
            target = db.get(User, group.owner_user_id)

    info = classify(todo, now=moment)
    if target is not None:
        content = (
            f"@{target.username} 这条待办卡住了：{todo.title}（派给{owner_name}，"
            f"{info['label']}，已催 {todo.nudge_count} 次）。需要你来定一下。"
        )
    else:
        content = (
            f"这条待办卡住了：{todo.title}（派给{owner_name}，{info['label']}，"
            f"已催 {todo.nudge_count} 次），找不到责任人可以升级。"
        )
    if not _announce(
        db,
        group=_feed_group(db),
        content=content,
        part={"type": "todo_escalation", "todoId": todo.id, "by": by_label},
    ):
        return {"ok": False, "text": "没有可用的通知群，升级消息发不出去"}
    todo.escalated_at = moment
    todo.updated_at = moment
    if target is not None:
        from app.services import inapp_notifications

        inapp_notifications.push(
            db,
            user_id=target.id,
            kind="todo_escalated",
            title=f"待办卡住了：{todo.title}",
            body=f"派给{owner_name}，{info['label']}，已催 {todo.nudge_count} 次",
            ref_type="todo",
            ref_id=todo.id,
            group_id=todo.group_id,
            actor_name=by_label,
            dedupe_key=f"todo_escalated:{todo.id}",
        )
    logger.info("todo_followup.escalated todo=%s to=%s", todo.id, target.id if target else None)
    return {"ok": True, "target": target.display_name if target else "", "text": content}


# --------------------------------------------------------------------------- #
# 定时扫：让状态自动回流，而不是等有人问
# --------------------------------------------------------------------------- #


def sweep(db: Session, *, limit: int = 50) -> dict[str, int]:
    """一轮跟进：该催的催，催够了的升级。幂等，靠冷却时间防重。"""
    summary = {"checked": 0, "nudged": 0, "escalated": 0, "skipped": 0}
    if not settings.todo_followup_enabled:
        return summary

    moment = _now()
    rows = open_todos(db, limit=limit)
    for todo in rows:
        summary["checked"] += 1
        info = classify(todo, now=moment)
        if info["state"] not in ("idle", "stuck", "overdue"):
            continue
        if (todo.nudge_count or 0) >= settings.todo_max_nudges:
            if todo.escalated_at is None:
                escalate(db, todo=todo)
                summary["escalated"] += 1
            else:
                summary["skipped"] += 1
            continue
        ok, _why = can_nudge(todo, now=moment)
        if not ok:
            summary["skipped"] += 1
            continue
        result = nudge(db, todo=todo)
        if result.get("ok"):
            summary["nudged"] += 1
        else:
            summary["skipped"] += 1
    if any(summary[key] for key in ("nudged", "escalated")):
        db.flush()
    return summary