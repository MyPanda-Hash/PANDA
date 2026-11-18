"""为当前聊天回合查找同一用户的往期话题；历史只作为只读线索。"""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.user_messages import redact_inline_secrets
from app.models.chat import ChatMessage, ChatSession
from app.models.chat_search import ChatHistoryEmbedding
from app.repositories import chat as chat_repo
from app.repositories import personal_search

logger = logging.getLogger(__name__)
_IDENTIFIERS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_CHINESE = re.compile(r"[\u4e00-\u9fff]{2,}")
_FILLER = {
    "什么",
    "怎么",
    "如何",
    "是否",
    "现在",
    "之前",
    "今天",
    "这个",
    "问题",
    "一下",
    "请问",
    "介绍",
    "解释",
}
_QUESTION_NOISE = re.compile(
    r"^(?:(?:请问|解释|介绍|关于|查询|帮我|我想问|什么是|之前|继续)+)"
    r"|(?:(?:是什么|是啥|什么意思|怎么样|有哪些|如何|怎么|介绍|解释|一下)+)$"
)


def _topic_terms(question: str) -> tuple[list[str], list[str]]:
    identifiers = list(
        dict.fromkeys(token.casefold() for token in _IDENTIFIERS.findall(question))
    )[:4]
    chinese: list[str] = []
    for raw in _CHINESE.findall(question):
        run = _QUESTION_NOISE.sub("", raw)
        for index in range(len(run) - 1):
            term = run[index : index + 2]
            if term not in _FILLER and term not in chinese:
                chinese.append(term)
            if len(chinese) == 8:
                break
    return identifiers, chinese[:8]


def recall_prior_topic(
    db: Session, *, owner_user_id: str, session_id: str, message_id: str
) -> list[dict[str, str]]:
    """仅以已提交的本轮提问为查询，返回最多两个不同旧会话的有效问答。"""
    current = db.scalar(
        select(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == session_id,
            ChatMessage.owner_user_id == owner_user_id,
            ChatSession.owner_user_id == owner_user_id,
            ChatMessage.role == "user",
            ChatMessage.variant_active.is_(True),
        )
    )
    if current is None or not current.content.strip():
        return []
    identifiers, chinese = _topic_terms(current.content[:500])
    if not identifiers and len(chinese) < 2:
        return []
    terms = (identifiers + chinese)[:8]
    lexical = personal_search.search_chat_history(
        db,
        owner_user_id=owner_user_id,
        query=" ".join(terms),
        exclude_session_id=session_id,
        limit=12,
    )
    candidates: list[tuple[personal_search.ChatHistorySearchResult, str]] = []
    for hit in lexical:
        text = hit.content.casefold()
        exact_identifier = any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
            for token in identifiers
        )
        shared_chinese = sum(term in text for term in chinese)
        if exact_identifier or shared_chinese >= 2:
            candidates.append((hit, "keyword"))

    # 历史正文没有权威持久等级；按最高 restricted 校验实际目标。
    # 不满足时保留词法结果，不构造任何外发客户端。
    if (
        not candidates
        and settings.qa_vector_enabled
        and len(current.content.strip()) >= 4
    ):
        has_index = db.scalar(
            select(ChatHistoryEmbedding.id)
            .where(
                ChatHistoryEmbedding.message_id.in_(
                    select(ChatMessage.id).where(
                        ChatMessage.owner_user_id == owner_user_id,
                        ChatMessage.session_id != session_id,
                        ChatMessage.variant_active.is_(True),
                    )
                ),
                ChatHistoryEmbedding.owner_user_id == owner_user_id,
                ChatHistoryEmbedding.status == "ready",
            )
            .limit(1)
        )
        if has_index:
            try:
                from app.services.pgvector_store import _build_embedding_function

                embed = _build_embedding_function(
                    timeout_seconds=2.0,
                    db=db,
                    data_level="restricted",
                )
                started = time.monotonic()
                embedding_succeeded = False

                def tracked_embedding(texts: list[str]) -> list[list[float]]:
                    nonlocal embedding_succeeded
                    vectors = embed(texts)
                    embedding_succeeded = True
                    return vectors

                try:
                    semantic = personal_search.search_chat_history_semantic(
                        db,
                        owner_user_id=owner_user_id,
                        query=redact_inline_secrets(current.content[:500]),
                        exclude_session_id=session_id,
                        embedding_fn=tracked_embedding,
                        limit=20,
                    )
                finally:
                    from app.services.pricing import fill_usage_cost

                    raw_usage = getattr(embed, "last_usage", None)
                    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
                    usage.setdefault("provider", "embedding_api")
                    usage.setdefault("model", getattr(embed, "_model", None) or settings.embedding_api_model)
                    fill_usage_cost(db, usage)
                    personal_search.record_embedding_usage(
                        db, owner_user_id=owner_user_id, message_id=None,
                        provider=str(usage["provider"]), model=str(usage["model"]),
                        usage=usage, retry_number=0,
                        status="succeeded" if embedding_succeeded else "failed",
                        latency_ms=int((time.monotonic() - started) * 1000),
                        query_type="query", vector_used=embedding_succeeded,
                    )
                candidates = [
                    (hit, "semantic")
                    for hit in semantic
                    if hit.session_id != session_id and hit.score >= 0.72
                ]
            except Exception as exc:
                logger.info("历史话题向量补召回不可用: %s", type(exc).__name__)

    recalled: list[dict[str, str]] = []
    seen_sessions: set[str] = set()
    for hit, match_kind in candidates:
        if hit.session_id in seen_sessions:
            continue
        window = personal_search.get_chat_history_context(
            db,
            owner_user_id=owner_user_id,
            session_id=hit.session_id,
            message_id=hit.message_id,
            before=0,
            after=2,
            max_chars=800,
        )
        if not window or window[0].message_id != hit.message_id:
            continue
        answer = ""
        outcome = "unanswered"
        for item in window[1:]:
            if item.role == "user":
                break
            if item.role == "assistant":
                stored = db.get(ChatMessage, item.message_id)
                if stored is not None and chat_repo.message_runtime_errors(stored):
                    outcome = "failed"
                else:
                    answer = redact_inline_secrets(item.content[:700])
                    outcome = "answered" if answer else "unanswered"
                break
        recalled.append(
            {
                "session_id": hit.session_id,
                "message_id": hit.message_id,
                "citation": hit.citation,
                "created_at": hit.created_at.isoformat(),
                "match_kind": match_kind,
                "source_user": redact_inline_secrets(window[0].content[:300]),
                "source_assistant": answer,
                "outcome": outcome,
            }
        )
        seen_sessions.add(hit.session_id)
        if len(recalled) == 2:
            break
    return recalled
