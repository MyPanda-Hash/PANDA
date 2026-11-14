"""Knowledge-scoped routing for Enterprise WeChat conversations."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import transaction
from app.models.access import KnowledgeSpace
from app.models.documents import Document, DocumentVersion
from app.services.qa_service import QaServiceError, generate_answer

logger = logging.getLogger(__name__)

_COUNT_PATTERNS = (
    re.compile(r"多少(?:篇|份|个)"),
    re.compile(r"几(?:篇|份|个)"),
    re.compile(r"文档(?:数量|总数)"),
    re.compile(r"知识(?:库|文档)(?:数量|总数|有多少)"),
)
_SPACE_COUNT_PATTERNS = (
    re.compile(r"(?:每个|各个).*(?:知识|资料)?空间.*(?:多少|几).*(?:文档|篇)"),
)
_SPACE_LIST_PATTERNS = (
    re.compile(r"(?:知识|资料)?空间.*(?:有哪些|列出|列表|清单)"),
    re.compile(r"(?:有哪些|列出|列表|清单).*(?:知识|资料)?空间"),
)
_DOCUMENT_LIST_PATTERNS = (
    re.compile(r"(?:文档|知识文档).*(?:有哪些|列出|列表|清单)"),
    re.compile(r"(?:有哪些|列出|列表|清单).*(?:文档|知识文档)"),
)
_KNOWLEDGE_KEYWORDS = (
    "产品",
    "文档",
    "知识",
    "功能",
    "公司",
    "流程",
    "方案",
    "支持",
    "如何",
    "怎么",
    "什么是",
    "介绍",
    "说明",
    "接口",
    "API",
    "价格",
    "政策",
    "制度",
    "服务",
)
_NO_EVIDENCE_REPLY = "当前配置的知识范围内没有找到可核验的文档。"
def configured_space_ids(db: Any | None = None) -> list[str]:
    """Return configured spaces, or every non-deleted space by default."""
    raw = settings.wecom_knowledge_space_ids
    configured = [space_id.strip() for space_id in raw.split(",") if space_id.strip()]
    if configured:
        return configured
    if db is not None:
        return _all_space_ids(db)
    with transaction() as scope_db:
        return _all_space_ids(scope_db)


def _all_space_ids(db: Any) -> list[str]:
    return list(
        db.scalars(
            select(KnowledgeSpace.id).where(KnowledgeSpace.status != "deleted")
        )
    )



def is_knowledge_question(question: str) -> bool:
    """Use a conservative intent gate before sending a message to RAG."""
    normalized = question.strip()
    if not normalized:
        return False
    if _structured_query_kind(normalized) is not None:
        return True
    return any(keyword.lower() in normalized.lower() for keyword in _KNOWLEDGE_KEYWORDS)


def _queryable_document_count(db: Any, space_ids: list[str]) -> int:
    query = (
        select(func.count(func.distinct(Document.id)))
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.space_id.in_(space_ids),
            Document.status == "active",
            DocumentVersion.processing_stage == "ready",
            DocumentVersion.queryable.is_(True),
            DocumentVersion.is_current_published.is_(True),
        )
    )
    return int(db.scalar(query) or 0)


def _structured_query_kind(question: str) -> str | None:
    if any(pattern.search(question) for pattern in _COUNT_PATTERNS):
        return "count"
    if any(pattern.search(question) for pattern in _SPACE_COUNT_PATTERNS):
        return "space_counts"
    if any(pattern.search(question) for pattern in _SPACE_LIST_PATTERNS):
        return "spaces"
    if any(pattern.search(question) for pattern in _DOCUMENT_LIST_PATTERNS):
        return "documents"
    return None


def _queryable_document_rows(
    db: Any, space_ids: list[str]
) -> list[tuple[str, str, str]]:
    query = (
        select(KnowledgeSpace.name, Document.id, Document.name)
        .join(Document, Document.space_id == KnowledgeSpace.id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            KnowledgeSpace.id.in_(space_ids),
            KnowledgeSpace.status != "deleted",
            Document.status == "active",
            DocumentVersion.processing_stage == "ready",
            DocumentVersion.queryable.is_(True),
            DocumentVersion.is_current_published.is_(True),
        )
        .distinct()
        .order_by(KnowledgeSpace.name, Document.name, Document.id)
    )
    return [(str(space), str(doc_id), str(name)) for space, doc_id, name in db.execute(query).all()]


def _structured_reply(db: Any, space_ids: list[str], kind: str) -> str:
    if kind == "count":
        count = _queryable_document_count(db, space_ids)
        return f"当前知识范围内共有 {count} 篇已发布且可问答的知识文档。"

    if kind == "spaces":
        query = (
            select(KnowledgeSpace.name)
            .where(
                KnowledgeSpace.id.in_(space_ids),
                KnowledgeSpace.status != "deleted",
            )
            .order_by(KnowledgeSpace.name)
        )
        names = [str(name) for name in db.scalars(query)]
        if not names:
            return "当前没有可检索的知识空间。"
        return f"当前可检索知识空间共 {len(names)} 个：\n" + "、".join(names)

    rows = _queryable_document_rows(db, space_ids)
    if kind == "documents":
        if not rows:
            return "当前知识范围内没有已发布且可问答的知识文档。"
        names = [name for _space, _doc_id, name in rows[:20]]
        suffix = f"\n（另有 {len(rows) - 20} 篇未展开）" if len(rows) > 20 else ""
        return (
            f"当前知识范围内共有 {len(rows)} 篇已发布且可问答的知识文档：\n"
            + "、".join(names)
            + suffix
        )

    counts: dict[str, int] = {}
    for space_name, _doc_id, _doc_name in rows:
        counts[space_name] = counts.get(space_name, 0) + 1
    if not counts:
        return "当前没有已发布且可问答的知识文档。"
    lines = [f"- {space_name}：{counts[space_name]} 篇" for space_name in sorted(counts)]
    return "各知识空间的可问答文档数量：\n" + "\n".join(lines)


def _format_answer(answer: str, evidence: list[dict[str, Any]]) -> str:
    text = answer.strip()
    sources = []
    for item in evidence[:3]:
        citation_id = str(item.get("id") or "").strip()
        location = str(item.get("page_or_section") or "").strip()
        if citation_id and location:
            sources.append(f"- [{citation_id}] {location}")
    if not sources:
        return text
    return f"{text}\n\n参考文档：\n" + "\n".join(sources)


def answer_wecom_knowledge(question: str) -> str | None:
    """Answer a configured-scope knowledge question, or return None for general chat.

    An empty ``DOC_NEXUS_WECOM_KNOWLEDGE_SPACE_IDS`` exposes every non-deleted
    knowledge space. A configured value narrows the scope to those IDs.

    A knowledge question never falls through to the general Agent when the RAG
    scope has no evidence; this prevents an ungrounded answer for product and
    document questions.
    """
    kind = _structured_query_kind(question)
    if not is_knowledge_question(question):
        return None

    with transaction() as db:
        space_ids = configured_space_ids(db)
        if not space_ids:
            return _NO_EVIDENCE_REPLY
        if kind is not None:
            return _structured_reply(db, space_ids, kind)

        try:
            answer, status, _retrieval, _verification, _usage, _events, evidence = generate_answer(
                db, question.strip(), space_ids
            )
        except QaServiceError:
            logger.exception("wecom knowledge answer failed")
            return _NO_EVIDENCE_REPLY

        if status != "completed" or not answer:
            return _NO_EVIDENCE_REPLY
        return _format_answer(answer, evidence)
