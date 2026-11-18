"""会话记忆蒸馏：把一个人一段时间的 AI 会话蒸成可读、可回溯的记忆。

流程与 employee_profiles 一致（先采集事实 -> 再让模型解读 -> 失败退回规则），
区别在于素材是「会话正文」而不是行为计数，产物是「做了什么」而不是「做得怎样」。

设计取舍：
- 只取 user 与 assistant 的正文，跳过系统/工具噪声，避免提示词里的模板撑爆上下文
- 单条消息截断，总量有上限：宁可少读，也不让一次蒸馏把上下文打满
- 模型不可用时退回规则版（按会话标题聚合），保证功能不因模型故障而空白
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.session_memory import SessionMemory

logger = logging.getLogger(__name__)

# 单条消息最多取多少字：够看出这件事是什么，又不至于让一段长文吃掉整个预算
PER_MESSAGE_CHARS = 400
# 一次蒸馏最多读多少条消息、多少个会话
MAX_MESSAGES = 120
MAX_SESSIONS = 20


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


@dataclass
class SessionDigest:
    """采集到的原始素材。"""

    user_id: str
    period_start: datetime
    period_end: datetime
    session_ids: list[str] = field(default_factory=list)
    session_titles: list[str] = field(default_factory=list)
    message_count: int = 0
    transcript: str = ""

    @property
    def session_count(self) -> int:
        return len(self.session_ids)

    @property
    def confidence(self) -> float:
        """证据够不够：会话越多、消息越多越可信，封顶 0.95。"""
        if not self.message_count:
            return 0.0
        volume = min(self.message_count / 40.0, 1.0)
        breadth = min(self.session_count / 5.0, 1.0)
        return round(min(0.95, 0.35 + 0.35 * volume + 0.25 * breadth), 2)


def collect_sessions(
    db: Session,
    *,
    user_id: str,
    period_start: datetime,
    period_end: datetime,
) -> SessionDigest:
    """把一个人一个周期内的会话正文摊平成一段可喂给模型的素材。"""
    digest = SessionDigest(
        user_id=user_id, period_start=period_start, period_end=period_end
    )
    sessions = list(
        db.scalars(
            select(ChatSession)
            .where(
                ChatSession.owner_user_id == user_id,
                ChatSession.created_at >= period_start,
                ChatSession.created_at < period_end,
            )
            .order_by(ChatSession.created_at.desc())
            .limit(MAX_SESSIONS)
        )
    )
    if not sessions:
        return digest

    chunks: list[str] = []
    used = 0
    for session in sessions:
        if used >= MAX_MESSAGES:
            break
        rows = list(
            db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == session.id,
                    ChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(ChatMessage.created_at.asc())
                .limit(MAX_MESSAGES - used)
            )
        )
        convo = []
        for row in rows:
            text = (row.content or "").strip().replace("\n", " ")
            if not text:
                continue
            convo.append(f"[{row.role}] {text[:PER_MESSAGE_CHARS]}")
        if not convo:
            continue
        digest.session_ids.append(session.id)
        digest.session_titles.append(session.title or "未命名会话")
        used += len(convo)
        chunks.append(f"### 会话：{session.title or '未命名会话'}\n" + "\n".join(convo))

    digest.message_count = used
    digest.transcript = "\n\n".join(chunks)
    return digest


DISTILL_PROMPT = (
    "你是企业 AI 使用分析助手。给你某位员工一段时间内与 AI 助手的会话摘录，"
    "请提炼他「用 AI 实际做了什么」。只依据材料，不要推测人品或能力倾向。"
    "输出严格 JSON，字段如下：\n"
    "  headline: 一句话概括这段时间用 AI 做了什么（<=60 字）\n"
    "  topic: 主要主题，2-8 字，如「MES 日报」「故障排查」\n"
    "  highlights: 数组，3-6 条，每条 {\"title\": 事项标题(<=24字), "
    "\"detail\": 具体做了什么与结论(<=60字), \"session_hint\": 涉及的会话标题}\n"
    "  lessons: 数组，0-4 条可复用经验（同类问题下次怎么做更快）\n"
    "  tools: 数组，0-6 个用到的能力或数据源，如「MES 数据」「日报生成」\n"
    "材料不足时 highlights 给 1 条并说明「记录较少」，不要编造。"
)


def build_distill_prompt(digest: SessionDigest, *, display_name: str) -> str:
    if not digest.transcript:
        return f"员工：{display_name}\n\n这段时间没有可用的 AI 会话记录。"
    return (
        f"员工：{display_name}\n"
        f"统计周期：{digest.period_start:%Y-%m-%d} ~ {digest.period_end:%Y-%m-%d}\n"
        f"会话数：{digest.session_count}，消息数：{digest.message_count}\n\n"
        f"会话摘录：\n{digest.transcript}"
    )


def parse_distilled(raw: str) -> dict:
    """模型输出容错解析：取第一个 JSON 对象。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value, limit: int) -> list:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _rules_memory(digest: SessionDigest) -> dict:
    """模型不可用时的退路：按会话标题聚合，绝不编造内容。"""
    titles = [t for t in digest.session_titles if t]
    if not titles:
        return {
            "headline": "这段时间没有可蒸馏的 AI 会话。",
            "topic": "",
            "highlights": [],
            "lessons": [],
            "tools": [],
        }
    counts: dict[str, int] = {}
    for title in titles:
        counts[title] = counts.get(title, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = ordered[0][0]
    return {
        "headline": f"共 {digest.session_count} 个会话、{digest.message_count} 条消息，主要是「{top}」。",
        "topic": top[:8],
        "highlights": [
            {"title": name[:24], "detail": f"进行了 {n} 次会话", "session_hint": name}
            for name, n in ordered[:5]
        ],
        "lessons": [],
        "tools": [],
    }


def _session_lookup(db: Session, user_id: str, hints: list[str]) -> dict[str, str]:
    """把模型给的会话标题映射回真实 session_id，保证「跳回原会话」可用。"""
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.owner_user_id == user_id)
            .order_by(ChatSession.created_at.desc())
            .limit(200)
        )
    )
    by_title = {row.title: row.id for row in rows if row.title}
    return {hint: by_title[hint] for hint in hints if hint in by_title}


def save_memory(
    db: Session,
    *,
    user: User,
    digest: SessionDigest,
    distilled: dict | None,
) -> SessionMemory:
    """落库：同一周期覆盖写，保证记忆是「当前版本」而不是流水。"""
    data = distilled or _rules_memory(digest)
    highlights = _as_list(data.get("highlights"), 6)

    hints = [
        str(item.get("session_hint") or "")
        for item in highlights
        if isinstance(item, dict)
    ]
    id_map = _session_lookup(db, user.id, [h for h in hints if h])
    # 模型没给或对不上时，按顺序把已知会话补进去，避免记忆断链
    fallback = list(digest.session_ids)
    clean_highlights = []
    for index, item in enumerate(highlights):
        if not isinstance(item, dict):
            continue
        sid = id_map.get(str(item.get("session_hint") or ""))
        if not sid and index < len(fallback):
            sid = fallback[index]
        clean_highlights.append(
            {
                "title": str(item.get("title") or "")[:60],
                "detail": str(item.get("detail") or "")[:200],
                "session_id": sid or "",
            }
        )

    record = db.scalar(
        select(SessionMemory).where(
            SessionMemory.user_id == user.id,
            SessionMemory.period_start == digest.period_start,
        )
    )
    if record is None:
        record = SessionMemory(
            id=_new_id("mem"),
            user_id=user.id,
            period_start=digest.period_start,
            period_end=digest.period_end,
            created_at=now(),
        )
        db.add(record)

    record.period_end = digest.period_end
    record.headline = str(data.get("headline") or "")[:400]
    record.topic = str(data.get("topic") or "")[:96]
    record.highlights = json.dumps(clean_highlights, ensure_ascii=False)
    record.lessons = json.dumps(
        [str(x)[:300] for x in _as_list(data.get("lessons"), 4)], ensure_ascii=False
    )
    record.tools = json.dumps(
        [str(x)[:60] for x in _as_list(data.get("tools"), 6)], ensure_ascii=False
    )
    record.session_ids = json.dumps(digest.session_ids, ensure_ascii=False)
    record.message_count = digest.message_count
    record.session_count = digest.session_count
    record.confidence = digest.confidence
    record.generated_by = "model" if distilled else "rules"
    record.created_at = now()
    db.flush()
    return record


async def distill_sessions(
    db: Session,
    *,
    user: User,
    days: int = 7,
    with_model: bool = True,
) -> SessionMemory:
    """采集 -> 模型蒸馏 -> 落库；模型不可用时退回规则版。"""
    end = now()
    start = end - timedelta(days=max(1, days))
    digest = collect_sessions(
        db, user_id=user.id, period_start=start, period_end=end
    )

    distilled: dict = {}
    if with_model and digest.message_count:
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
                            digest, display_name=user.display_name or user.username
                        ),
                    },
                ],
                response_format_json=True,
                disable_thinking=True,
                max_tokens=1200,
            )
            distilled = parse_distilled(reply.text)
        except (LlmUnavailable, LlmCallFailed) as exc:
            logger.info("session_memory.model_unavailable", extra={"error": str(exc)})
        except Exception:
            logger.exception("session_memory.distill_failed")

    return save_memory(db, user=user, digest=digest, distilled=distilled or None)


def list_memories(
    db: Session, *, user_id: str, limit: int = 20
) -> list[SessionMemory]:
    return list(
        db.scalars(
            select(SessionMemory)
            .where(SessionMemory.user_id == user_id)
            .order_by(SessionMemory.created_at.desc())
            .limit(limit)
        )
    )