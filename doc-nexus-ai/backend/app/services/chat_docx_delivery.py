"""Auto-deliver a finished chat reply as a downloadable DOCX artifact.

Auto-export applies to substantive document deliverables or an explicit request
for a downloadable file. Reply length and Markdown structure alone do not make
ordinary questions, explanations, or capability introductions into documents.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.artifacts import Artifact
from app.models.writing import WritingProject
from app.repositories import chat as chat_repo
from app.repositories import usage as usage_repo
from app.repositories import writing as writing_repo

logger = logging.getLogger(__name__)

# 正文判定阈值：去空白后至少这些字符才可能是"写完稿"。
MIN_BODY_CHARS = 500
# 结构判定：至少出现这些数量的 markdown 标题才视为结构化文稿。
MIN_HEADING_COUNT = 2
# 标题行：一到四级。
_HEADING_RE = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)
# 非空行（段落近似）。
_PARAGRAPH_RE = re.compile(r"^\s*\S.*$", re.MULTILINE)
# fenced code block 累计行占比超此值时视为代码而非文稿。
_MAX_CODE_RATIO = 0.6
# 项目标题最大长度。
_MAX_TITLE = 60

_DOCUMENT_KIND = (
    r"方案|报价(?:单|书)?|报告|总结|计划|纪要|通知|公告|邮件|信函|申请书|建议书|说明书|"
    r"合同|制度|标书|文案|文章|文稿|文档|简历|讲稿|发言稿|邀请函|介绍|简介|"
    r"(?i:\b(?:word|docx|document|report|proposal|quotation|quote|plan|article|letter|email|memo|profile)\b)"
)
_WRITING_REQUEST_RE = re.compile(
    r"^(?:(?:你好|您好)[，,！!\s]*)?"
    r"(?:(?:你)?(?:能不能|能否|可以)(?:帮我|替我|为我)|"
    r"(?:请|麻烦)?(?:帮我|帮忙|替我|给我|为我)?|来)"
    r"\s*(?:把.{1,100}?)?"
    r"(?:撰写|写|起草|编写|拟定|生成|输出|制作|整理|润色|改写|扩写|完善|修订|导出|保存|一份|一篇|一封)"
    r"[\s\S]{0,200}?(?:" + _DOCUMENT_KIND + r")"
    r"|^(?i:(?:please\s+|(?:could|can|would)\s+you\s+)?"
    r"(?:write|draft|create|generate|compose|prepare|export|save)\b)"
    r"[\s\S]{0,200}?(?:" + _DOCUMENT_KIND + r")"
)
_INTRODUCTION_REQUEST_RE = re.compile(
    r"^(?:请|麻烦)?(?:帮我|给我)?介绍(?:一下)?"
    r".{0,100}(?:公司|企业|产品|项目|服务|平台)"
)
_DOWNLOAD_REQUEST_RE = re.compile(
    r"(?:给|提供|发|要|需要|附|带上).{0,24}(?:下载链接|下载地址|可下载|下载文件)"
    r"|(?:导出|另存|保存|转成|转换成|生成|制作).{0,24}(?:文件|文档|(?i:word|docx))"
    r"|(?:下载(?:链接|地址)|(?i:word|docx)(?:文件|文档)?)[。.!！]?$"
    r"|(?i:\b(?:download\s+link|downloadable\s+(?:file|document)|export\s+(?:as|to)\s+(?:word|docx))\b)"
)
_DOCUMENT_KIND_RE = re.compile(_DOCUMENT_KIND)
_NO_DOCUMENT_EXPORT_RE = re.compile(
    r"(?:不要|不用|无需|不必|不需要|别).{0,20}"
    r"(?:导出|下载|生成文件|生成文档|生成\s*(?i:word|docx)|(?i:word|docx))"
    r"|只(?:需|要|用)?(?:在聊天(?:里|中)?|回复文字|文字回复|文本回复)"
    r"|(?i:\b(?:do not|don't|no need to)\s+(?:export|create\s+(?:a\s+)?file|generate\s+(?:a\s+)?document)\b)"
)
_DOCUMENT_HEADING_RE = re.compile(r"^#{1,2}\s+(.+?)(?:\s+#+)?\s*$", re.MULTILINE)
_SECTION_HEADING_RE = re.compile(
    r"^(?:第?[一二三四五六七八九十百]+[、章节]|[0-9]+[.、\s])"
)
_PLACEHOLDER_TITLES = frozenset({"新对话", "新会话", "未命名", "new chat", "untitled"})


def _requests_document(user_request: str) -> bool:
    text = str(user_request or "").strip()
    return bool(
        text
        and (_WRITING_REQUEST_RE.search(text) or _INTRODUCTION_REQUEST_RE.search(text))
    )


def _fenced_code_line_ratio(reply: str) -> float:
    """Return the fraction of non-empty lines inside fenced code blocks."""
    lines = reply.splitlines()
    if not lines:
        return 0.0
    in_block = False
    code_lines = 0
    total = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_block = not in_block
            continue
        if not stripped:
            continue
        total += 1
        if in_block:
            code_lines += 1
    return code_lines / total if total else 0.0


def _has_generated_file_link(reply: str) -> bool:
    """Reply already carrying a downloadable link should not be re-exported."""
    return bool(re.search(r"\]\(https?://[^)]+\)", reply)) or bool(
        re.search(r"\]\(/api/files/[^)]+\)", reply)
    )


def should_auto_export_docx(
    reply: str,
    *,
    user_request: str,
    has_existing_project: bool,
    has_generated_files: bool,
) -> bool:
    """Deliver documents, not long answers; explicit download requests opt in."""
    request = str(user_request or "").strip()
    text = str(reply or "").strip()
    if (
        has_existing_project
        or has_generated_files
        or not text
        or _NO_DOCUMENT_EXPORT_RE.search(request)
        or _has_generated_file_link(text)
    ):
        return False
    if _DOWNLOAD_REQUEST_RE.search(request):
        return True
    if (
        not _requests_document(request)
        or _fenced_code_line_ratio(text) >= _MAX_CODE_RATIO
    ):
        return False
    # Short quotations and introductions can be complete deliverables too.
    document_heading = any(
        _DOCUMENT_KIND_RE.search(match.group(1))
        for match in _DOCUMENT_HEADING_RE.finditer(text)
    )
    if document_heading and len(_PARAGRAPH_RE.findall(text)) >= 3:
        return True
    if len(text) < MIN_BODY_CHARS:
        return False
    heading_count = len(_HEADING_RE.findall(text))
    if heading_count < MIN_HEADING_COUNT:
        return False
    return True


def _clean_title(raw: str | None, fallback: str) -> str:
    value = (raw or "").strip().replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value)
    if not value or value.casefold() in _PLACEHOLDER_TITLES or value.startswith("chs_"):
        return fallback
    return value[:_MAX_TITLE].rstrip()


def _document_title(reply: str, session_title: str | None, user_request: str) -> str:
    for match in _DOCUMENT_HEADING_RE.finditer(reply):
        heading = match.group(1).strip().strip("*_")
        if not _SECTION_HEADING_RE.match(heading):
            title = _clean_title(heading, "")
            if title:
                return title
    title = _clean_title(session_title, "")
    if title:
        return title
    if _DOWNLOAD_REQUEST_RE.search(user_request) and not _requests_document(
        user_request
    ):
        first_line = next(
            (line.strip() for line in reply.splitlines() if line.strip()), ""
        )
        return _clean_title(first_line.strip("#*_ "), "翠花成稿")
    request_title = re.sub(
        r"^(?:(?:请|麻烦)?(?:帮我|帮忙|替我|给我|为我)?\s*)?"
        r"(?:撰写|写|起草|编写|拟定|生成|输出|制作|整理|润色|改写|扩写|完善|修订|导出|保存|介绍)?"
        r"(?:一下|一份|一篇|一封|一个|个|份|篇)?\s*",
        "",
        user_request.strip(),
    ).strip("《》“”\"'，。！？!? ")
    return _clean_title(request_title, "翠花成稿")


def _session_title(db: Session, local_session_id: str, user: User) -> str | None:
    session = chat_repo.get_owned_session(db, local_session_id, user.id)
    if session is None:
        return None
    return (session.title or "").strip() or None


def try_deliver_for_session(
    *,
    db: Session,
    user: User,
    reply: str,
    user_request: str,
    local_session_id: str | None,
    assistant_message_id: str | None,
    has_existing_project: bool,
    has_generated_files: bool,
    evidence: list[dict] | None = None,
    space_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    evidence_payload: dict | None = None,
) -> dict[str, str] | None:
    """Session-aware entry: heuristic gate, then deliver as a DOCX project."""
    if not local_session_id or not assistant_message_id:
        return None

    existing_project = db.scalar(
        select(WritingProject).where(
            WritingProject.owner_user_id == user.id,
            WritingProject.source_chat_session_id == local_session_id,
            WritingProject.source_chat_assistant_message_id == assistant_message_id,
        )
    )
    if existing_project is not None:
        existing_artifact = db.scalar(
            select(Artifact)
            .where(
                Artifact.owner_user_id == user.id,
                Artifact.deleted_at.is_(None),
                Artifact.project_id == existing_project.id,
                Artifact.artifact_type == "writing_export",
            )
            .order_by(Artifact.created_at)
            .limit(1)
        )
        if existing_artifact is not None:
            return {
                "project_id": existing_project.id,
                "artifact_id": existing_artifact.id,
                "name": existing_artifact.name,
            }

    if not should_auto_export_docx(
        reply,
        user_request=user_request,
        has_existing_project=has_existing_project,
        has_generated_files=has_generated_files,
    ):
        return None

    title_hint = _document_title(
        reply, _session_title(db, local_session_id, user), user_request
    )
    return deliver_chat_reply_docx(
        db=db,
        user=user,
        reply=reply,
        title_hint=title_hint,
        local_session_id=local_session_id,
        assistant_message_id=assistant_message_id,
        evidence=evidence,
        space_ids=space_ids,
        document_ids=document_ids,
        evidence_payload=evidence_payload,
    )


def deliver_chat_reply_docx(
    *,
    db: Session,
    user: User,
    reply: str,
    title_hint: str | None = None,
    local_session_id: str | None = None,
    assistant_message_id: str | None = None,
    evidence: list[dict] | None = None,
    space_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    evidence_payload: dict | None = None,
) -> dict[str, str] | None:
    """Create a writing project seeded with the reply and export it as DOCX.

    Uses the caller's transaction. Returns ``{project_id, artifact_id, name}``
    on success, or ``None`` when the reply is not a deliverable document.
    """
    from app.services.evidence_export import validate_evidence_sources

    validation = validate_evidence_sources(
        db, user=user, texts=[reply], evidence=evidence or [],
        space_ids=space_ids or [], document_ids=document_ids,
        chat_session_id=local_session_id,
    )
    project_title = _clean_title(title_hint, "翠花成稿")
    project = writing_repo.create_project(
        db,
        owner_user_id=user.id,
        title=project_title,
        space_ids=space_ids or [],
        document_ids=document_ids or [],
        budget_limit=None,
        brief={
            "purpose": "聊天成稿自动导出",
            "notes": "由翠花聊天回复自动沉淀，可在写作工作区继续编辑。",
            "source": "chat-auto-export",
        },
        source_chat_session_id=local_session_id,
        source_chat_assistant_message_id=assistant_message_id,
    )
    project = writing_repo.replace_outline(
        db,
        project,
        [{"title": "正文"}],
    )
    first_chapter = sorted(project.outline.chapters, key=lambda item: item.position)[0]
    writing_repo.seed_chapter(db, project, first_chapter.id, reply)
    operation = writing_repo.start_operation(
        db,
        writing_repo.create_operation(db, project, "export"),
    )
    artifact = usage_repo.create_export_artifact(
        db,
        user=user,
        project=project,
        operation=operation,
        format="docx",
        evidence_validation=validation,
        draft=True,
    )
    writing_repo.complete_operation(
        db,
        operation,
        {
            "artifact_id": artifact.id,
            "format": "docx",
            "manifest_available": False,
            "evidence_sources": validation.sources,
            "evidence_payload": {**(evidence_payload or {}), "evidence": evidence or []},
            "evidence_blockers": validation.blockers,
            "document_status": "draft",
        },
    )
    return {
        "project_id": project.id,
        "artifact_id": artifact.id,
        "name": artifact.name,
    }
