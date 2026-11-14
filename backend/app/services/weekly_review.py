"""周回顾：这一周到底发生了什么，和上周比是好了还是坏了。

和日报的区别：日报回答「今天有什么要办」，回顾回答「这周为什么一直在办同样的事」。
所以重点不在罗列，而在**重复**——同一类问题反复出现，说明根因没动，
加规则、加提醒都只是让同一个坑被踩得更响。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import now as _now
from app.core.timeutil import as_utc
from app.models.assistant_watch import AssistantEvent
from app.services.rule_thresholds import current as _rules

# 门槛统一在 rule_thresholds（MES_RULE_REPEAT_KIND_TIMES / MES_RULE_REPEAT_DOC_TIMES）


@dataclass
class WeekStats:
    created: int = 0
    closed: int = 0
    stale: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "closed": self.closed, "stale": self.stale}


@dataclass
class PersonLine:
    name: str
    closed: int = 0
    open_now: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "closed": self.closed, "openNow": self.open_now}


@dataclass
class WeeklyReview:
    start: date
    end: date
    this_week: WeekStats = field(default_factory=WeekStats)
    last_week: WeekStats = field(default_factory=WeekStats)
    kinds: list[tuple[str, int]] = field(default_factory=list)
    repeat_docs: list[tuple[str, int]] = field(default_factory=list)
    people: list[PersonLine] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "thisWeek": self.this_week.as_dict(),
            "lastWeek": self.last_week.as_dict(),
            "kinds": [{"kind": k, "count": c} for k, c in self.kinds],
            "repeatDocs": [{"docNo": d, "count": c} for d, c in self.repeat_docs],
            "people": [p.as_dict() for p in self.people],
            "focus": self.focus,
        }

    def render(self) -> str:
        lines = [f"本周回顾（{self.start.isoformat()} ~ {self.end.isoformat()}）", ""]
        lines.append("### 这周的数字")
        delta = self.this_week.created - self.last_week.created
        trend = ""
        if self.last_week.created:
            trend = "，比上周多" if delta > 0 else ("，比上周少" if delta < 0 else "，和上周持平")
            if delta:
                trend += f" {abs(delta)} 条"
        lines.append(
            f"- 新发现 {self.this_week.created} 条，处理 {self.this_week.closed} 条"
            + (f"，仍有 {self.this_week.stale} 条超过一天没动" if self.this_week.stale else "")
            + trend
        )

        if self.kinds:
            lines.append("")
            lines.append("### 出现最多的问题")
            for kind, count in self.kinds[:5]:
                mark = "  ← 反复出现" if count >= _rules().repeat_kind_times else ""
                lines.append(f"- {_kind_name(kind)}：{count} 条{mark}")

        if self.repeat_docs:
            lines.append("")
            lines.append("### 反复出问题的单据")
            for doc, count in self.repeat_docs[:5]:
                lines.append(f"- {doc}：{count} 次")

        if self.people:
            lines.append("")
            lines.append("### 各人的情况")
            for person in self.people[:8]:
                lines.append(
                    f"- {person.name}：本周处理 {person.closed} 条，手上还有 {person.open_now} 条"
                )

        if self.focus:
            lines.append("")
            lines.append("### 下周先解决什么")
            for index, item in enumerate(self.focus, 1):
                lines.append(f"{index}. {item}")

        return "\n".join(lines).strip()


def _kind_name(kind: str) -> str:
    from app.services.assistant_watch import KIND_SUMMARY

    return KIND_SUMMARY.get(kind, kind)


def _week_bounds(today: date | None = None) -> tuple[date, date, date, date]:
    """返回 (本周一, 本周日, 上周一, 上周日)。"""
    day = today or _now().date()
    this_monday = day - timedelta(days=day.weekday())
    this_sunday = this_monday + timedelta(days=6)
    return (
        this_monday,
        this_sunday,
        this_monday - timedelta(days=7),
        this_monday - timedelta(days=1),
    )


def _in_range(value: datetime | None, begin: date, end: date) -> bool:
    stamp = as_utc(value) if value is not None else None
    if stamp is None:
        return False
    day = stamp.date()
    return begin <= day <= end


def build_review(db: Session, *, today: date | None = None) -> WeeklyReview:
    """算一份周回顾。纯读，不调模型。"""
    this_start, this_end, last_start, last_end = _week_bounds(today)
    review = WeeklyReview(start=this_start, end=this_end)

    rows = list(db.scalars(select(AssistantEvent)))
    kind_counts: dict[str, int] = {}
    doc_counts: dict[str, int] = {}
    people: dict[str, PersonLine] = {}
    stamp = _now()

    for event in rows:
        first = event.first_seen_at or event.created_at
        closed_at = event.resolved_at or (
            event.updated_at if event.status in ("handled", "resolved") else None
        )

        if _in_range(first, last_start, last_end):
            review.last_week.created += 1
        if _in_range(closed_at, last_start, last_end):
            review.last_week.closed += 1

        if _in_range(first, this_start, this_end):
            review.this_week.created += 1
            kind_counts[event.kind] = kind_counts.get(event.kind, 0) + 1
            if event.doc_no:
                doc_counts[event.doc_no] = doc_counts.get(event.doc_no, 0) + 1
        if _in_range(closed_at, this_start, this_end):
            review.this_week.closed += 1
            for name in _targets(event):
                line = people.setdefault(name, PersonLine(name=name))
                line.closed += 1

        if event.status == "open" and event.archived_at is None:
            seen = as_utc(first)
            if seen is not None and (stamp - seen).total_seconds() / 3600.0 >= 24:
                review.this_week.stale += 1
            for name in _targets(event):
                line = people.setdefault(name, PersonLine(name=name))
                line.open_now += 1

    review.kinds = sorted(kind_counts.items(), key=lambda kv: -kv[1])
    review.repeat_docs = sorted(
        ((doc, count) for doc, count in doc_counts.items() if count >= _rules().repeat_doc_times),
        key=lambda kv: -kv[1],
    )[:5]
    review.people = sorted(
        people.values(), key=lambda p: (-p.open_now, -p.closed)
    )
    review.focus = _focus_of(review)
    return review


def _targets(event: AssistantEvent) -> list[str]:
    try:
        names = json.loads(event.target_usernames_json or "[]")
    except (TypeError, ValueError):
        return []
    return [str(name) for name in names if str(name).strip()]


def _focus_of(review: WeeklyReview) -> list[str]:
    """下周关注点：从数据里推，不写死套话。"""
    focus: list[str] = []

    repeated = [(kind, count) for kind, count in review.kinds if count >= _rules().repeat_kind_times]
    if repeated:
        kind, count = repeated[0]
        focus.append(
            f"{_kind_name(kind)}这周出现 {count} 次。别再靠提醒去顶，"
            "先找出为什么同一件事反复发生。"
        )

    if review.repeat_docs:
        doc, count = review.repeat_docs[0]
        focus.append(f"{doc} 这周反复出问题 {count} 次，根因大概不是在它本身，往上下游看一步。")

    if review.this_week.stale >= 3:
        focus.append(
            f"还有 {review.this_week.stale} 条超过一天没动，先清积压再谈优化，否则越堆越多。"
        )

    if review.people and review.people[0].open_now >= 3:
        person = review.people[0]
        focus.append(f"{person.name} 手上压着 {person.open_now} 条，看看是不是派得不合适。")

    if not focus:
        if review.this_week.created == 0:
            focus.append("这周没有新问题，正常。")
        else:
            focus.append("这周没有反复出现的问题，保持现在的处理节奏。")
    return focus[:3]