"""待办归类：同一件事的不同细节，要并进同一条待办。

问题现场：对「中天客户来访」这件事，语音里分几次说了接送车辆、样品清单、
午餐伴手礼、参观路线、接待准备，结果出现了 6 条待办，人看着像 6 件事。

做法：给每条待办算一个 **主题键** = 主要对象 + 粗类别。
- 主要对象：单号 / 客户名 / 供应商名 / 人名（会把「中天客户」归到「中天精工」）；
- 粗类别只有 5 类（事务接待 / 业务交付 / 财务 / 生产 / 质量）。
同一个人、同一个主题键、还在进行中、且没超过时间窗 → 并进已有那条，不新建。

刻意不做的事：不按「标题相似度」合并，也不让模型判断。
规则可测、可解释；合并错了人还能从明细里看出来。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_token, now
from app.core.timeutil import as_utc
from app.models.group_chat import GroupTodo

logger = logging.getLogger(__name__)

# 粗类别：只分五类。分得细了就等于不合并，分得粗了会把不相关的事搅在一起。
_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("财务", ("回款", "对账", "开票", "收款", "发票", "结算", "账期")),
    ("质量", ("检验", "质检", "公差", "复检", "来料", "不合格", "返修", "客诉")),
    ("生产", ("排产", "生产", "工单", "车间", "完工", "试模", "工艺", "图纸", "加工", "模具")),
    ("业务交付", ("订单", "下单", "签约", "报价", "发货", "交货", "送货", "出库", "交付")),
    ("事务接待", ("来访", "接待", "参观", "接送", "午餐", "晚餐", "伴手礼", "会议室", "样品", "迎接", "拜访")),
)

_DIRECTIONAL = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,8})(?=客户|公司|集团|厂家|供应商|配套厂)")
_DOC_NO = re.compile(r"\b[A-Z]{2,4}(?:-\d{2,8}){1,3}\b")

_known_cache: tuple[float, tuple[str, ...]] = (0.0, ())
_KNOWN_TTL_SECONDS = 600.0


def known_entities() -> tuple[str, ...]:
    """客户/供应商/人名名单，用来把「中天」补全成「中天精工」。取不到就返回空。"""
    global _known_cache
    stamp, names = _known_cache
    if names and time.monotonic() - stamp < _KNOWN_TTL_SECONDS:
        return names

    collected: list[str] = []
    try:
        from app.services.mes_client import MesClient

        client = MesClient()
        for panel, field in (("SO_ORDER", "客户"), ("PU_ORDER", "供应商")):
            rows = client.query_panel(panel, page_size=100)
            for row in rows:
                value = str(row.get(field) or "").strip()
                if value:
                    collected.append(value)
    except Exception:  # noqa: BLE001 - 没配 MES 也要能用
        pass

    # 只认客户/供应商：人名会当主语出现在标题里，混进来会把「给王销售的对账」认成王销售
    names = tuple(sorted({name for name in collected if name}, key=len, reverse=True))
    _known_cache = (time.monotonic(), names)
    return names


def reset_known_entities_cache() -> None:
    global _known_cache
    _known_cache = (0.0, ())


_LEADING_VERBS = (
    "确认", "核对", "准备", "安排", "整理", "统计", "联系", "跟进", "回复", "通知",
    "告知", "协调", "处理", "落实", "检查", "把", "让", "请", "给", "帮", "催", "查",
)


def _strip_leading_verbs(text: str) -> str:
    """「确认中天」→「中天」：正则会把前面的动词一起吞进来。"""
    result = text
    changed = True
    while changed and len(result) > 2:
        changed = False
        for verb in _LEADING_VERBS:
            if result.startswith(verb) and len(result) > len(verb) + 1:
                result = result[len(verb):]
                changed = True
                break
    return result


def _canonical_entity(candidate: str, known: tuple[str, ...]) -> str:
    """「中天」→「中天精工」：把简称补全成名单里的全称。"""
    cand = _strip_leading_verbs((candidate or "").strip())
    if not cand:
        return ""
    for name in known:
        if name == cand or cand in name:
            return name
    # 候选尾部对得上已知名称的前缀（「明天中天」→「中天精工」）
    for name in known:
        for length in range(min(len(name), 6), 1, -1):
            if cand.endswith(name[:length]):
                return name
    return cand


def _category(text: str) -> str:
    for label, words in _CATEGORY_HINTS:
        if any(word in text for word in words):
            return label
    return ""


@dataclass(frozen=True)
class Topic:
    key: str
    entity: str
    category: str

    @property
    def usable(self) -> bool:
        return bool(self.key)


def topic_of(title: str, detail: str = "", *, known: tuple[str, ...] | None = None) -> Topic:
    """算主题键。算不出来就返回空 key —— 宁可各自独立，也不乱合并。

    只看**标题**：明细里混着派发人、建议、单据清单，
    拿它当判断依据会把「管理员」「质检报告」这类词当成主题。
    """
    text = (title or "").strip()
    if not text:
        return Topic("", "", "")

    doc = _DOC_NO.search(text.upper())
    if doc is not None:
        number = doc.group(0)
        category = _category(text) or "单据"
        return Topic(f"{number}#{category}", number, category)

    names = known_entities() if known is None else known
    entity = ""
    # 名单里的全称优先（「中天精工」比「中天」更准）；从长到短，避免「中天」抢在「中天精工」前面
    for name in names:
        if len(name) >= 2 and name in text:
            entity = name
            break
    if not entity:
        match = _DIRECTIONAL.search(text)
        if match is not None:
            entity = _canonical_entity(match.group(1), names)
    if not entity:
        return Topic("", "", _category(text))

    category = _category(text)
    if not category:
        # 只提到对象、说不出做什么：不合并，避免把无关的事捆在一起
        return Topic("", entity, "")
    return Topic(f"{entity}#{category}", entity, category)


def merge_or_create(
    db: Session,
    *,
    assignee_id: str,
    title: str,
    detail: str = "",
    priority: str = "normal",
    due_at: Any = None,
    created_by_user_id: str | None = None,
    group_id: str | None = None,
    origin: str = "ai",
    source_message_id: str | None = None,
    topic: Topic | None = None,
) -> tuple[GroupTodo, bool]:
    """同主题的未完成待办存在就并进去，否则新建。返回 (待办, 是否合并)。"""
    resolved = topic or topic_of(title, detail)
    moment = now()

    if resolved.usable:
        window = timedelta(days=float(settings.todo_merge_window_days or 7))
        cutoff = moment - window
        existing = db.scalar(
            select(GroupTodo)
            .where(
                GroupTodo.assignee_user_id == assignee_id,
                GroupTodo.topic == resolved.key,
                GroupTodo.status.in_(("pending", "accepted", "doing")),
                GroupTodo.created_at >= cutoff,
            )
            .order_by(GroupTodo.created_at)
        )
        if existing is not None:
            _append_item(existing, title=title, detail=detail, priority=priority, moment=moment)
            db.flush()
            logger.info("todo_topics.merged todo=%s topic=%s", existing.id, resolved.key)
            return existing, True

    todo = GroupTodo(
        id=f"gtodo_{new_token(10)}",
        group_id=group_id,
        assignee_user_id=assignee_id,
        created_by_user_id=created_by_user_id,
        origin=origin,
        title=title[:255],
        detail=detail,
        priority=priority,
        status="pending",
        due_at=due_at,
        topic=resolved.key[:160],
        source_message_id=source_message_id,
        created_at=moment,
        updated_at=moment,
    )
    db.add(todo)
    db.flush()
    return todo, False


_PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "urgent": 3}


def _append_item(
    todo: GroupTodo, *, title: str, detail: str, priority: str, moment
) -> None:
    """把新说出的一件事并进已有待办的明细里，一行一条。"""
    line = title.strip()
    if detail.strip():
        line = f"{line}（{detail.strip()}）"
    if line and line not in (todo.detail or ""):
        todo.detail = ((todo.detail or "").rstrip() + "\n· " + line).strip()
    if _PRIORITY_RANK.get(priority, 1) > _PRIORITY_RANK.get(todo.priority, 1):
        todo.priority = priority
    todo.updated_at = moment


# --------------------------------------------------------------------------- #
# 存量整理：把已经拆散的同主题待办并回去
# --------------------------------------------------------------------------- #


def merge_existing(db: Session, *, dry_run: bool = True) -> list[dict[str, str]]:
    """把同一人、同一主题的多条未完成待办合成一条。保留最早那条。"""
    rows = list(
        db.scalars(
            select(GroupTodo)
            .where(GroupTodo.status.in_(("pending", "accepted", "doing")))
            .order_by(GroupTodo.created_at)
        )
    )
    buckets: dict[str, list[GroupTodo]] = {}
    for todo in rows:
        key = todo.topic or topic_of(todo.title, todo.detail or "").key
        if not key:
            continue
        buckets.setdefault(f"{todo.assignee_user_id}|{key}", []).append(todo)

    report: list[dict[str, str]] = []
    moment = now()
    for _bucket_key, group in buckets.items():
        if len(group) < 2:
            continue
        keep = group[0]
        for extra in group[1:]:
            report.append(
                {
                    "keep": keep.id,
                    "keepTitle": keep.title,
                    "drop": extra.id,
                    "dropTitle": extra.title,
                }
            )
            if dry_run:
                continue
            _append_item(
                keep,
                title=extra.title,
                detail=_strip_voice_note(extra.detail or ""),
                priority=extra.priority,
                moment=moment,
            )
            keep.topic = keep.topic or topic_of(keep.title, keep.detail or "").key
            extra.status = "dismissed"
            extra.completed_at = moment
            extra.updated_at = moment
    if not dry_run and report:
        db.flush()
        logger.info("todo_topics.merged_existing count=%s", len(report))
    return report


def _strip_voice_note(detail: str) -> str:
    lines = [
        line
        for line in detail.splitlines()
        if "语音派发" not in line and not line.strip().startswith("· ")
    ]
    return " ".join(line.strip() for line in lines if line.strip())[:200]