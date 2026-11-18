"""Permission-scoped lexical search over existing persisted content.

Relational sources filter/count/rank in SQL. Shared snapshots are authorized
before parsing, then merged with the ranked private prefix before pagination.
No embeddings, external requests, database writes, or recent-candidate cutoff.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.orm import Session, defer, load_only

from app.core.security import as_utc
from app.models.access import KnowledgeSpace, SpaceMember
from app.models.artifacts import Artifact
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession, ChatSessionPublication
from app.models.document_pages import DocumentPage
from app.models.documents import Document, DocumentVersion
from app.models.harness import HarnessTask
from app.models.image_generation import ImageGenerationTask
from app.models.video_generation import VideoGenerationTask
from app.models.writing import WritingChapter, WritingOutline, WritingProject
from app.repositories import chat as chat_repo, tasks as task_repo
from app.schemas.search import MatchedField, SearchQueryType, SearchType
from app.services.document_processor import derive_category, stable_knowledge_id
from app.services.pgvector_store import _DOCUMENT_EMBEDDINGS
from app.services.publication_access import can_view_publication


@dataclass(frozen=True)
class SearchItem:
    type: SearchType
    id: str
    title: str
    subtitle: str
    updated_at: datetime
    status: str | None
    snippet: str | None
    matched_field: MatchedField | None
    path: str
    match_count: int
    content_searchable: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "updated_at", as_utc(self.updated_at))


@dataclass(frozen=True)
class SearchGroup:
    type: SearchType
    items: list[SearchItem]
    total: int
    next_offset: int | None


@dataclass
class _Hit:
    count: int = 0
    snippet: str | None = None
    target: str | int | None = None

    def add(self, content: str | None, needle: str, target=None) -> None:
        count = _count(content, needle)
        if count:
            self.count += count
            if self.snippet is None:
                self.snippet = _snippet(content or "", needle)
                self.target = target


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(column, needle: str):
    return column.ilike(f"%{_escape_like(needle)}%", escape="\\")


def _count(value: str | None, needle: str) -> int:
    return (value or "").lower().count(needle.lower()) if needle else 0


def _snippet(value: str, needle: str) -> str | None:
    index = value.lower().find(needle.lower()) if needle else -1
    if index < 0:
        return None
    start = max(0, index - 60)
    end = min(len(value), max(index + len(needle), start + 180))
    return ("…" if start else "") + value[start:end].strip() + ("…" if end < len(value) else "")


def _path(path: str, **params) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None and value != ""})
    return f"{path}?{query}" if query else path


def _rank_sql(title, needle: str):
    return case(
        (func.lower(title) == needle.lower(), 0),
        (title.ilike(f"{_escape_like(needle)}%", escape="\\"), 1),
        (_like(title, needle), 2),
        else_=3,
    )


def _sort_key(item: SearchItem, needle: str):
    title, query = item.title.lower(), needle.lower()
    rank = 0 if title == query else 1 if title.startswith(query) else 2 if query in title else 3
    return rank, -as_utc(item.updated_at).timestamp(), item.id


def _rows(db: Session, statement, take: int):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    return db.execute(statement.limit(take)).all(), total


def _visible_spaces(user: User):
    statement = select(KnowledgeSpace.id).where(KnowledgeSpace.status == "active")
    if user.role != "admin":
        statement = statement.where(or_(
            KnowledgeSpace.owner_user_id == user.id,
            KnowledgeSpace.id.in_(select(SpaceMember.space_id).where(SpaceMember.user_id == user.id)),
        ))
    return statement


def _spaces(db: Session, user: User, needle: str, take: int, ids=None):
    statement = select(KnowledgeSpace).where(KnowledgeSpace.id.in_(_visible_spaces(user)))
    statement = statement.where(KnowledgeSpace.id.in_(ids)) if ids is not None else statement.where(
        or_(_like(KnowledgeSpace.name, needle), _like(KnowledgeSpace.description, needle))
    )
    rows, total = _rows(db, statement.order_by(_rank_sql(KnowledgeSpace.name, needle), KnowledgeSpace.updated_at.desc(), KnowledgeSpace.id), take)
    return [SearchItem(
        "space", space.id, space.name, space.description or "", space.updated_at, space.status,
        _snippet(space.description or "", needle),
        "title" if _count(space.name, needle) else "description" if _count(space.description, needle) else None,
        _path(f"/spaces/{quote(space.id, safe='')}", search=needle),
        _count(space.name, needle) + _count(space.description, needle), False,
    ) for (space,) in rows], total


def _document_scope(user: User):
    return select(Document).where(
        Document.space_id.in_(_visible_spaces(user)), Document.status == "active", Document.is_temporary.is_(False),
    )


def _document_bodies(db: Session, scope, needle: str):
    """Map only authorized current queryable versions to exact vector partitions."""
    versions = db.execute(select(Document.id, Document.space_id, DocumentVersion.id).join(
        DocumentVersion, DocumentVersion.document_id == Document.id,
    ).where(
        Document.id.in_(scope.with_only_columns(Document.id)),
        DocumentVersion.is_current_published.is_(True), DocumentVersion.queryable.is_(True),
        DocumentVersion.processing_stage == "ready",
    )).all()
    version_docs = {version_id: doc_id for doc_id, _, version_id in versions}
    available: set[str] = set()
    hits: dict[str, _Hit] = {}
    # Batching limits SQL parameter/expression size, never the searchable corpus.
    version_ids = list(version_docs)
    for start in range(0, len(version_ids), 400):
        batch = version_ids[start:start + 400]
        available.update(version_docs[value] for value in db.scalars(select(DocumentPage.document_version_id).where(
            DocumentPage.document_version_id.in_(batch), func.trim(DocumentPage.text) != "",
        ).distinct()))
        if needle:
            pages = db.execute(select(DocumentPage.document_version_id, DocumentPage.page_number, DocumentPage.text).where(
                DocumentPage.document_version_id.in_(batch), _like(DocumentPage.text, needle),
            ).order_by(DocumentPage.page_number, DocumentPage.id)).yield_per(100)
            for version_id, page, content in pages:
                hits.setdefault(version_docs[version_id], _Hit()).add(content, needle, page)
    partitions = {(derive_category(space_id), str(stable_knowledge_id(version_id))): doc_id for doc_id, space_id, version_id in versions}
    keys = list(partitions)
    chunks = _DOCUMENT_EMBEDDINGS.c
    for start in range(0, len(keys), 400):
        condition = tuple_(chunks.category, chunks.file_id).in_(keys[start:start + 400])
        available.update(partitions[(category, file_id)] for category, file_id in db.execute(
            select(chunks.category, chunks.file_id).where(condition, func.trim(chunks.document) != "").distinct()
        ))
        if needle:
            matches = db.execute(select(chunks.category, chunks.file_id, chunks.document).where(
                condition, _like(chunks.document, needle),
            ).order_by(chunks.id)).yield_per(100)
            for category, file_id, content in matches:
                doc_id = partitions[(category, file_id)]
                # PDF page text is the authoritative page target; chunks may overlap it.
                if hits.get(doc_id, _Hit()).target is None:
                    hits.setdefault(doc_id, _Hit()).add(content, needle)
    return available, hits


def _documents(db: Session, user: User, needle: str, take: int, ids=None):
    scope = _document_scope(user)
    if ids is not None:
        scope = scope.where(Document.id.in_(ids))
    available, hits = _document_bodies(db, scope, needle)
    statement = select(Document, KnowledgeSpace, DocumentVersion).join(
        KnowledgeSpace, KnowledgeSpace.id == Document.space_id,
    ).outerjoin(DocumentVersion, and_(
        DocumentVersion.document_id == Document.id, DocumentVersion.is_current_published.is_(True),
    )).where(Document.id.in_(scope.with_only_columns(Document.id)))
    if ids is None:
        statement = statement.where(or_(
            _like(Document.name, needle), Document.id.in_(list(hits)),
            and_(DocumentVersion.queryable.is_(True), DocumentVersion.processing_stage == "ready", _like(DocumentVersion.summary, needle)),
        ))
    rows, total = _rows(db, statement.order_by(_rank_sql(Document.name, needle), Document.updated_at.desc(), Document.id), take)
    items = []
    for document, space, version in rows:
        hit = hits.get(document.id, _Hit())
        summary = version.summary if version and version.queryable and version.processing_stage == "ready" else ""
        title_count, summary_count = _count(document.name, needle), _count(summary, needle)
        items.append(SearchItem(
            "document", document.id, document.name, space.name, document.updated_at, document.status,
            hit.snippet or _snippet(summary or "", needle),
            "title" if title_count else "content" if hit.count else "description" if summary_count else None,
            _path(f"/spaces/{quote(space.id, safe='')}/documents/{quote(document.id, safe='')}", preview=1,
                  version=version.id if version else None, page=hit.target, search=needle),
            title_count + summary_count + hit.count, document.id in available,
        ))
    return items, total


def _private_chat(db: Session, user: User, needle: str, take: int, ids=None):
    message_match = select(ChatMessage.id).where(
        ChatMessage.session_id == ChatSession.id, ChatMessage.owner_user_id == user.id,
        ChatMessage.role.in_(("user", "assistant")), _like(ChatMessage.content, needle),
    ).exists()
    statement = select(ChatSession).where(ChatSession.owner_user_id == user.id)
    statement = statement.where(ChatSession.id.in_(ids)) if ids is not None else statement.where(or_(_like(ChatSession.title, needle), message_match))
    rows, total = _rows(db, statement.order_by(_rank_sql(ChatSession.title, needle), ChatSession.updated_at.desc(), ChatSession.id), take)
    hits: dict[str, _Hit] = {}
    if needle and rows:
        messages = db.execute(select(ChatMessage.session_id, ChatMessage.id, ChatMessage.content).where(
            ChatMessage.session_id.in_([session.id for (session,) in rows]), ChatMessage.owner_user_id == user.id,
            ChatMessage.role.in_(("user", "assistant")), _like(ChatMessage.content, needle),
        ).order_by(ChatMessage.created_at.desc(), ChatMessage.id)).yield_per(100)
        for session_id, message_id, content in messages:
            hits.setdefault(session_id, _Hit()).add(content, needle, message_id)
    items = []
    for (session,) in rows:
        hit = hits.get(session.id, _Hit())
        count = _count(session.title, needle)
        items.append(SearchItem("chat", session.id, session.title or "新对话", "我的会话", session.updated_at, None,
            hit.snippet, "title" if count else "content" if hit.count else None,
            _path(f"/chat/{quote(session.id, safe='')}", message=hit.target, search=needle), count + hit.count, True))
    return items, total


def _private_writing(db: Session, user: User, needle: str, take: int, ids=None):
    chapter_match = select(WritingChapter.id).join(WritingOutline, WritingOutline.id == WritingChapter.outline_id).where(
        WritingOutline.project_id == WritingProject.id, or_(_like(WritingChapter.title, needle), _like(WritingChapter.content, needle)),
    ).exists()
    statement = select(WritingProject).where(WritingProject.owner_user_id == user.id)
    statement = statement.where(WritingProject.id.in_(ids)) if ids is not None else statement.where(or_(_like(WritingProject.title, needle), chapter_match))
    rows, total = _rows(db, statement.order_by(_rank_sql(WritingProject.title, needle), WritingProject.updated_at.desc(), WritingProject.id), take)
    hits: dict[str, _Hit] = {}
    if needle and rows:
        chapters = db.execute(select(WritingOutline.project_id, WritingChapter.id, WritingChapter.title, WritingChapter.content).join(
            WritingChapter, WritingChapter.outline_id == WritingOutline.id,
        ).where(WritingOutline.project_id.in_([project.id for (project,) in rows]),
                or_(_like(WritingChapter.title, needle), _like(WritingChapter.content, needle)),
        ).order_by(WritingChapter.position, WritingChapter.id)).yield_per(100)
        for project_id, chapter_id, title, content in chapters:
            hit = hits.setdefault(project_id, _Hit())
            hit.add(content, needle, chapter_id)
            hit.add(title, needle, chapter_id)
    items = []
    for (project,) in rows:
        hit = hits.get(project.id, _Hit())
        count = _count(project.title, needle)
        items.append(SearchItem("writing", project.id, project.title or "未命名文稿", "我的文稿", project.updated_at, None,
            hit.snippet, "title" if count else "content" if hit.count else None,
            _path(f"/writing/{quote(project.id, safe='')}", section=hit.target, search=needle), count + hit.count, True))
    return items, total


def _shared_chat(db: Session, user: User, needle: str, ids=None):
    statement = select(ChatSessionPublication).where(ChatSessionPublication.status == "active")
    if ids is not None:
        statement = statement.where(ChatSessionPublication.id.in_([value.removeprefix("shared:") for value in ids]))
    items = []
    # JSON is parsed only after the existing publication ACL. Raw JSON metadata
    # (IDs, trace fields, private draft pointers) is not searchable content.
    for publication in db.scalars(statement).yield_per(100):
        if not can_view_publication(db, user, publication):
            continue
        parts = chat_repo.publication_snapshot(publication)
        hit = _Hit()
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict) or part.get("role") not in ("user", "assistant"):
                continue
            content = part.get("content")
            target = part.get("id") if isinstance(part.get("id"), str) else None
            if isinstance(content, str):
                hit.add(content, needle, target)
        title_count, summary_count = _count(publication.title, needle), _count(publication.summary, needle)
        if ids is None and not (title_count or summary_count or hit.count):
            continue
        owner = db.get(User, publication.source_owner_user_id)
        owner_name = (owner.display_name or owner.username) if owner else "原作者"
        params = {"publication": publication.id, "search": needle, "message": hit.target}
        items.append(SearchItem("chat", f"shared:{publication.id}", publication.title,
            f"{owner_name} · 共享快照 v{publication.version}", publication.published_at, publication.status,
            hit.snippet or _snippet(publication.summary or "", needle),
            "title" if title_count else "content" if hit.count else "description" if summary_count else None,
            _path("/chat", **params), title_count + summary_count + hit.count, True))
    return items


_FILE_LINK = re.compile(r"\[([^\]\n]+)\]\(<?(/(?:api/)?files/[^\s)>]+)>?\)")


def _generated_files(db: Session, user: User, needle: str, ids=None):
    statement = select(ChatMessage.id, ChatMessage.session_id, ChatMessage.content, ChatMessage.created_at, ChatSession.title).join(
        ChatSession, ChatSession.id == ChatMessage.session_id,
    ).where(ChatMessage.owner_user_id == user.id, ChatSession.owner_user_id == user.id,
            ChatMessage.role == "assistant", or_(ChatMessage.content.contains("/api/files/"), ChatMessage.content.contains("/files/")))
    owner_tasks = set(db.scalars(select(HarnessTask.id).where(HarnessTask.user_id == user.id)))
    # Pi chat workspaces are namespaced per user; older links omit the user.
    runtime_prefix = f"pi-chat-{user.id}"
    found: dict[str, SearchItem] = {}
    for _, session_id, content, stamp, session_title in db.execute(statement.order_by(ChatMessage.created_at.desc(), ChatMessage.id)).yield_per(100):
        for name, href in _FILE_LINK.findall(content or ""):
            path = urlsplit(href).path.removeprefix("/api").removeprefix("/files/")
            task, separator, relative = path.partition("/")
            task, relative = unquote(task), unquote(relative)
            if not separator or task == "artifacts" or not relative or "\\" in relative or any(part in ("", ".", "..") for part in relative.split("/")):
                continue
            if task not in owner_tasks and task not in (
                runtime_prefix,
                f"{runtime_prefix}-{session_id}",
                f"pi-chat-{session_id}",
            ):
                continue
            stable_id = f"file:{task}:{quote(relative, safe='')}"
            if stable_id in found or (ids is not None and stable_id not in ids):
                continue
            count = _count(name, needle)
            context_count = _count(session_title, needle)
            if ids is None and not count and not context_count:
                continue
            found[stable_id] = SearchItem("artifact", stable_id, name, session_title or "会话生成文件", stamp, "ready",
                _snippet(session_title or "", needle), "title" if count else "description" if context_count else None,
                _path(f"/chat/{quote(session_id, safe='')}", result=stable_id, search=needle), count + context_count, False)
    return list(found.values())


def _artifacts(db: Session, user: User, needle: str, take: int, ids=None):
    items = []
    total = 0
    statement = select(Artifact).options(defer(Artifact.content)).where(Artifact.owner_user_id == user.id, Artifact.deleted_at.is_(None))
    statement = statement.where(Artifact.id.in_([value.removeprefix("artifact:") for value in ids if value.startswith("artifact:")])) if ids is not None else statement.where(_like(Artifact.name, needle))
    rows, count = _rows(db, statement.order_by(_rank_sql(Artifact.name, needle), Artifact.updated_at.desc(), Artifact.id), take)
    total += count
    for (artifact,) in rows:
        count = _count(artifact.name, needle)
        items.append(SearchItem("artifact", f"artifact:{artifact.id}", artifact.name, "文稿导出", artifact.updated_at, artifact.status,
            None, "title" if count else None, _path("/artifacts", artifact=f"artifact:{artifact.id}", search=needle), count, False))
    for prefix, model, label in (("image", ImageGenerationTask, "生成图片"), ("video", VideoGenerationTask, "生成视频")):
        statement = select(model, ChatSession.title).options(load_only(model.id, model.prompt, model.chat_session_id, model.updated_at, model.status)).join(
            ChatSession, ChatSession.id == model.chat_session_id,
        ).where(model.owner_user_id == user.id, ChatSession.owner_user_id == user.id, model.status == "succeeded")
        statement = statement.where(model.id.in_([value.removeprefix(f"{prefix}:") for value in ids if value.startswith(f"{prefix}:")])) if ids is not None else statement.where(or_(_like(model.prompt, needle), _like(ChatSession.title, needle)))
        rows, count = _rows(db, statement.order_by(_rank_sql(model.prompt, needle), model.updated_at.desc(), model.id), take)
        total += count
        for task, session_title in rows:
            stable_id = f"{prefix}:{task.id}"
            title = task.prompt or label
            count = _count(title, needle)
            context_count = _count(session_title, needle)
            items.append(SearchItem("artifact", stable_id, title, f"{label} · {session_title or '会话'}", task.updated_at, task.status,
                _snippet(title, needle), "title" if count else "description" if context_count else None,
                _path(f"/chat/{quote(task.chat_session_id, safe='')}", result=stable_id, search=needle), count + context_count, False))
    files = _generated_files(db, user, needle, ids)
    return items + files, total + len(files)


def _tasks(db: Session, user: User, needle: str, ids=None):
    raw_projections = (
        [task_repo.task_by_key(db, owner_user_id=user.id, task_key=value) for value in ids]
        if ids is not None
        else task_repo._source_items(db, owner_user_id=user.id, limit=None)
    )
    # 未确认状态决定任务归属视图，搜索深链必须拿到与任务中心一致的 unread。
    projections = task_repo._with_unread(
        db,
        owner_user_id=user.id,
        items=[item for item in raw_projections if item is not None],
    )
    media_names = {}
    for source, model in (("image", ImageGenerationTask), ("video", VideoGenerationTask)):
        media_names.update({(source, task_id): f"{prompt}\n{title}" for task_id, prompt, title in db.execute(
            select(model.id, model.prompt, ChatSession.title).join(ChatSession, ChatSession.id == model.chat_session_id).where(
                model.owner_user_id == user.id, ChatSession.owner_user_id == user.id,
            )
        )})
    items = []
    for task in projections:
        context = media_names.get((task.source, task.source_id), "")
        label_count = _count(task.label, needle)
        hit = _Hit()
        hit.add(context, needle)
        hit.add(task.error_message_safe, needle)
        if ids is None and not (label_count or hit.count):
            continue
        items.append(SearchItem("task", task.id, task.label, task.kind, task.updated_at, task.status,
            hit.snippet, "title" if label_count else "description" if hit.count else None,
            _path("/chat", task=task.id, task_view="attention" if task_repo.is_attention_task(task) else "history", search=needle),
            label_count + hit.count, False))
    return items


def _collect(db: Session, user: User, kind: SearchType, needle: str, take: int, ids=None):
    if kind == "space":
        return _spaces(db, user, needle, take, ids)
    if kind == "document":
        return _documents(db, user, needle, take, ids)
    if kind == "writing":
        return _private_writing(db, user, needle, take, ids)
    if kind == "chat":
        private_ids = [value for value in ids if not value.startswith("shared:")] if ids is not None else None
        shared_ids = [value for value in ids if value.startswith("shared:")] if ids is not None else None
        private, total = _private_chat(db, user, needle, take, private_ids)
        shared = _shared_chat(db, user, needle, shared_ids)
        return private + shared, total + len(shared)
    if kind == "artifact":
        return _artifacts(db, user, needle, take, ids)
    items = _tasks(db, user, needle, ids)
    return items, len(items)


def search(db: Session, user: User, *, query: str, type: SearchQueryType, limit: int, offset: int) -> list[SearchGroup]:
    needle = query.strip()
    if not needle:
        return []
    kinds = ("chat", "space", "document", "writing", "artifact", "task") if type == "all" else ("space", "document") if type == "resources" else (type,)
    groups = []
    for kind in kinds:
        # Each source's first N ranked rows suffice for the union's first N.
        items, total = _collect(db, user, kind, needle, offset + limit)
        items.sort(key=lambda item: _sort_key(item, needle))
        groups.append(SearchGroup(kind, items[offset:offset + limit], total, offset + limit if total > offset + limit else None))
    return groups


def resolve(db: Session, user: User, items: list[tuple[SearchType, str]], *, query: str = "") -> list[SearchItem]:
    """Use the same authorized builders, preserving query-derived exact targets."""
    needle = query.strip()
    by_type: dict[SearchType, list[str]] = {}
    for kind, item_id in items:
        by_type.setdefault(kind, []).append(item_id)
    current = {}
    for kind, ids in by_type.items():
        values, _ = _collect(db, user, kind, needle, len(ids), ids)
        current.update({(item.type, item.id): item for item in values})
    return [current[key] for key in dict.fromkeys(items) if key in current]
