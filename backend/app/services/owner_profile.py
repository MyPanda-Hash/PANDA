"""责任人画像：这个人在系统里的待办处理表现。

只写系统能看见的事——派给他的事件多少、多久认领、多久关掉、有多少还压着。
**不评价工作态度、不猜能力**，也不引入系统外的任何信息。

对外话术也照这个边界走：说「他有 3 条超过 24 小时没动」，不说「他责任心不强」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import now as _now
from app.core.timeutil import as_utc
from app.models.auth import User
from app.models.assistant_watch import AssistantEvent
from app.services.rule_thresholds import current as _rules

# 「压多久算没动」在 rule_thresholds（MES_RULE_STALE_HOURS）


@dataclass
class OwnerProfile:
    username: str
    display_name: str
    total: int = 0
    open: int = 0
    handled: int = 0
    resolved: int = 0
    stale_open: int = 0
    avg_ack_hours: float | None = None
    avg_close_hours: float | None = None
    ack_within_24h: float | None = None
    top_kinds: list[tuple[str, int]] = field(default_factory=list)
    repeat_docs: list[tuple[str, int]] = field(default_factory=list)
    verdict: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "displayName": self.display_name,
            "total": self.total,
            "open": self.open,
            "handled": self.handled,
            "resolved": self.resolved,
            "staleOpen": self.stale_open,
            "avgAckHours": self.avg_ack_hours,
            "avgCloseHours": self.avg_close_hours,
            "ackWithin24h": self.ack_within_24h,
            "topKinds": [{"kind": k, "count": c} for k, c in self.top_kinds],
            "repeatDocs": [{"docNo": d, "count": c} for d, c in self.repeat_docs],
            "verdict": self.verdict,
        }

    def render(self) -> str:
        lines = [f"{self.display_name}（{self.username}）"]
        if not self.total:
            lines.append("名下没有派过待办，暂时没有可看的记录。")
            return "\n".join(lines)
        # 这里一律用「条事件」，和今日计划的「组」区分开，
        # 否则同一件事在两个视图里一个说张、一个说条，谁都对不上
        lines.append(
            f"共 {self.total} 条事件，已处理 {self.handled + self.resolved}，当前压着 {self.open} 条"
            + (f"（其中 {self.stale_open} 条超过 24 小时没动）" if self.stale_open else "")
        )
        if self.avg_ack_hours is not None:
            lines.append(f"平均 {self.avg_ack_hours:.1f} 小时认领")
        if self.avg_close_hours is not None:
            lines.append(f"平均 {self.avg_close_hours:.1f} 小时关掉")
        if self.top_kinds:
            detail = "、".join(f"{_kind_name(k)} {c} 条" for k, c in self.top_kinds[:3])
            lines.append(f"最多的是：{detail}")
        if self.repeat_docs:
            detail = "、".join(f"{d}（{c} 次）" for d, c in self.repeat_docs[:3])
            lines.append(f"反复出现的单据：{detail}")
        if self.verdict:
            lines.append(self.verdict)
        return "\n".join(lines)


def _kind_name(kind: str) -> str:
    from app.services.assistant_watch import KIND_SUMMARY

    return KIND_SUMMARY.get(kind, kind)


def _hours_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    begin = as_utc(start)
    finish = as_utc(end)
    if begin is None or finish is None:
        return None
    return max(0.0, (finish - begin).total_seconds() / 3600.0)


def find_user(db: Session, name: str) -> User | None:
    """按用户名或显示名找人；找不到就返回 None，不去猜。"""
    text = (name or "").strip()
    if not text:
        return None
    user = db.scalar(select(User).where(User.username == text))
    if user is not None:
        return user
    return db.scalar(select(User).where(User.display_name == text))


def build_profile(db: Session, *, user: User, days: int = 30) -> OwnerProfile:
    """算一个人的画像。窗口默认 30 天，太长的历史对当下判断没帮助。"""
    stamp = _now()
    rows: list[AssistantEvent] = []
    for event in db.scalars(select(AssistantEvent)):
        try:
            names = json.loads(event.target_usernames_json or "[]")
        except (TypeError, ValueError):
            names = []
        if user.username not in names:
            continue
        first = as_utc(event.first_seen_at or event.created_at)
        if first is not None and (stamp - first).days > days:
            continue
        rows.append(event)

    profile = OwnerProfile(
        username=user.username,
        display_name=user.display_name or user.username,
        total=len(rows),
    )
    if not rows:
        return profile

    ack_hours: list[float] = []
    close_hours: list[float] = []
    kinds: dict[str, int] = {}
    docs: dict[str, int] = {}
    for event in rows:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
        if event.doc_no:
            docs[event.doc_no] = docs.get(event.doc_no, 0) + 1
        first = as_utc(event.first_seen_at or event.created_at)
        if event.status == "open":
            profile.open += 1
            if (
                first is not None
                and (stamp - first).total_seconds() / 3600.0 >= _rules().stale_hours
            ):
                profile.stale_open += 1
        elif event.status == "handled":
            profile.handled += 1
        elif event.status == "resolved":
            profile.resolved += 1
        if event.acked_by_user_id:
            gap = _hours_between(first, event.updated_at if event.status == "handled" else None)
            if gap is not None:
                ack_hours.append(gap)
        if event.status in ("handled", "resolved"):
            gap = _hours_between(first, event.resolved_at or event.updated_at)
            if gap is not None:
                close_hours.append(gap)

    if ack_hours:
        profile.avg_ack_hours = round(sum(ack_hours) / len(ack_hours), 1)
        profile.ack_within_24h = round(
            sum(1 for h in ack_hours if h <= 24) / len(ack_hours), 2
        )
    if close_hours:
        profile.avg_close_hours = round(sum(close_hours) / len(close_hours), 1)
    profile.top_kinds = sorted(kinds.items(), key=lambda kv: -kv[1])[:5]
    profile.repeat_docs = sorted(
        ((doc, count) for doc, count in docs.items() if count >= 2),
        key=lambda kv: -kv[1],
    )[:5]
    profile.verdict = _verdict(profile)
    return profile


def _verdict(profile: OwnerProfile) -> str:
    """一句结论。只说数据里看得出来的事。"""
    if profile.open == 0 and profile.total >= 3:
        return "名下待办都清完了。"
    if profile.stale_open >= 3:
        return f"有 {profile.stale_open} 条超过一天没动，建议先把这些清掉再派新的。"
    if profile.repeat_docs:
        doc, count = profile.repeat_docs[0]
        return f"{doc} 反复出现 {count} 次，可能是根因没解决，光处理表面会一直回来。"
    if profile.avg_close_hours is not None and profile.avg_close_hours > 48:
        return f"平均 {profile.avg_close_hours:.0f} 小时才关掉，处理链路偏长。"
    if profile.open > 0:
        return f"当前 {profile.open} 条在办，节奏正常。"
    return ""