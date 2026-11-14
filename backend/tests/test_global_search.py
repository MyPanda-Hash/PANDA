"""Behavioral regressions for permission-scoped global search.

The fixture deliberately uses the real SQLAlchemy models and the SQLite vector
chunk table.  These tests exercise the repository contract directly so that
ACL filtering, ranking, pagination, deep links, and generated-file identity are
not hidden behind auth setup or an external service.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models import (
    Artifact,
    ChatMessage,
    ChatSession,
    ChatSessionPublication,
    Document,
    DocumentJob,
    DocumentPage,
    DocumentVersion,
    HarnessTask,
    KnowledgeSpace,
    SpaceMember,
    User,
    WritingChapter,
    WritingOperation,
    WritingOutline,
    WritingProject,
    WritingProjectPublication,
)
from app.services.document_processor import derive_category, stable_knowledge_id


@pytest.fixture
def global_search_db(tmp_path: Path):
    """Use a fresh SQLite database for every global-search scenario."""
    original = settings.db_path, settings.database_url
    path = tmp_path / "global-search.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _stamp(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


def _make_user(
    user_id: str,
    username: str,
    display_name: str,
    stamp: datetime,
    *,
    role: str = "user",
) -> User:
    return User(
        id=user_id,
        username=username,
        email=f"{username}@example.com",
        display_name=display_name,
        password_hash="unused",
        role=role,
        status="active",
        first_login=False,
        created_at=stamp,
    )


def _add_space(
    db: Session,
    *,
    space_id: str,
    owner_user_id: str,
    name: str,
    stamp: datetime,
    status: str = "active",
) -> KnowledgeSpace:
    space = KnowledgeSpace(
        id=space_id,
        name=name,
        description=f"{name}说明",
        owner_user_id=owner_user_id,
        status=status,
        scope_version=1,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(space)
    db.flush()
    return space


def _add_document(
    db: Session,
    *,
    document_id: str,
    version_id: str,
    space_id: str | None,
    owner_user_id: str,
    name: str,
    stamp: datetime,
    status: str = "active",
    is_temporary: bool = False,
    chat_session_id: str | None = None,
    processing_stage: str = "ready",
    queryable: bool = True,
    is_current_published: bool = True,
    version_number: int = 1,
    summary: str | None = "",
    source_relative_path: str | None = None,
) -> tuple[Document, DocumentVersion]:
    document = Document(
        id=document_id,
        space_id=space_id,
        category_id=None,
        name=name,
        status=status,
        is_temporary=is_temporary,
        chat_session_id=chat_session_id,
        expires_at=None,
        temporary_cleanup_at=None,
        created_by_user_id=owner_user_id,
        created_at=stamp,
        updated_at=stamp,
    )
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=version_number,
        processing_stage=processing_stage,
        queryable=queryable,
        retryable=False,
        retry_of_version_id=None,
        failure_code=None,
        failure_message=None,
        source_relative_path=source_relative_path,
        file_path=None,
        ext_task_id=None,
        chunk_count=0,
        summary=summary,
        extraction_method=None,
        page_count=0,
        ocr_page_count=0,
        vision_page_count=0,
        vision_usage_json="{}",
        extraction_warning_code=None,
        is_current_published=is_current_published,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add_all([document, version])
    db.flush()
    return document, version


def _add_document_page(
    db: Session,
    *,
    page_id: str,
    version_id: str,
    page_number: int,
    content: str,
    stamp: datetime,
) -> DocumentPage:
    page = DocumentPage(
        id=page_id,
        document_version_id=version_id,
        page_number=page_number,
        width_points=612,
        height_points=792,
        rotation=0,
        extraction_method="native",
        text=content,
        quality_score=1.0,
        average_confidence=1.0,
        warning_code=None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(page)
    db.flush()
    return page


def _add_chunk(
    db: Session,
    *,
    chunk_id: str,
    category: str,
    file_id: str,
    content: str,
    index: int = 0,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO document_embeddings
                (id, category, file_id, document, embedding, metadata)
            VALUES (:id, :category, :file_id, :document, :embedding, :metadata)
            """
        ),
        {
            "id": chunk_id,
            "category": category,
            "file_id": file_id,
            "document": content,
            "embedding": b"\x00" * 8,
            "metadata": json.dumps({"chunk_index": index}),
        },
    )


def _add_chat(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    title: str,
    stamp: datetime,
    messages: list[tuple[str, str, str]] | None = None,
) -> ChatSession:
    session = ChatSession(
        id=session_id,
        owner_user_id=owner_user_id,
        title=title,
        messages_revision=0,
        title_finalized=True,
        agent_session_id=None,
        preferred_model=None,
        category_id=None,
        pinned=False,
        pinned_at=None,
        archived=False,
        archived_at=None,
        writing_project_id=None,
        forked_from_session_id=None,
        forked_from_message_id=None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(session)
    db.flush()
    for message_id, role, content in messages or []:
        db.add(
            ChatMessage(
                id=message_id,
                session_id=session_id,
                owner_user_id=owner_user_id,
                dispatch_id=None,
                role=role,
                content=content,
                thinking=None,
                parts_json=None,
                space_ids_json="[]",
                document_ids_json="[]",
                references_json="[]",
                session_references_json="[]",
                writing_references_json="[]",
                images_json="[]",
                retrieval_summary=None,
                retrieval_trace_json=None,
                writing_project_id=None,
                usage=None,
                created_at=stamp,
            )
        )
    db.flush()
    return session


def _add_chat_publication(
    db: Session,
    *,
    publication_id: str,
    source_session_id: str,
    source_owner_user_id: str,
    shared_by_user_id: str,
    title: str,
    stamp: datetime,
    version: int = 1,
    summary: str = "",
    snapshot: list[dict[str, object]] | None = None,
    share_scope: str = "all_internal",
    space_ids: list[str] | None = None,
    status: str = "active",
) -> ChatSessionPublication:
    payload = snapshot or [{"id": "snapshot-message", "role": "assistant", "content": summary}]
    publication = ChatSessionPublication(
        id=publication_id,
        source_session_id=source_session_id,
        source_owner_user_id=source_owner_user_id,
        shared_by_user_id=shared_by_user_id,
        title=title,
        summary=summary,
        version=version,
        snapshot_json=json.dumps(payload, ensure_ascii=False),
        content_hash=f"hash-{publication_id}",
        share_scope=share_scope,
        space_ids_json=json.dumps(space_ids or [], ensure_ascii=False),
        allow_cite=True,
        allow_remix=True,
        status=status,
        published_at=stamp,
        revoked_at=None,
        created_at=stamp,
    )
    db.add(publication)
    db.flush()
    return publication


def _add_writing_project(
    db: Session,
    *,
    project_id: str,
    outline_id: str,
    chapter_id: str,
    owner_user_id: str,
    title: str,
    stamp: datetime,
    chapter_title: str = "章节",
    chapter_content: str = "",
) -> tuple[WritingProject, WritingChapter]:
    project = WritingProject(
        id=project_id,
        owner_user_id=owner_user_id,
        title=title,
        selected_space_ids="[]",
        selected_document_ids="[]",
        budget_limit=None,
        brief_json="{}",
        brief_revision=1,
        pi_agent_session_id=None,
        pi_session_stage=None,
        pi_lease_owner=None,
        pi_lease_operation_id=None,
        pi_lease_expires_at=None,
        source_chat_session_id=None,
        source_chat_assistant_message_id=None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(project)
    db.flush()
    outline = WritingOutline(
        id=outline_id,
        project_id=project_id,
        status="confirmed",
        confirmed_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(outline)
    db.flush()
    chapter = WritingChapter(
        id=chapter_id,
        outline_id=outline_id,
        position=1,
        title=chapter_title,
        content=chapter_content,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(chapter)
    db.flush()
    return project, chapter


def _add_writing_publication(
    db: Session,
    *,
    publication_id: str,
    source_project_id: str,
    source_owner_user_id: str,
    shared_by_user_id: str,
    title: str,
    stamp: datetime,
    version: int = 1,
    summary: str = "",
    chapter_id: str = "published-section",
    chapter_title: str = "章节",
    chapter_content: str = "",
    share_scope: str = "all_internal",
    space_ids: list[str] | None = None,
    status: str = "active",
) -> WritingProjectPublication:
    snapshot = {
        "outline_status": "confirmed",
        "chapters": [
            {
                "id": chapter_id,
                "position": 1,
                "title": chapter_title,
                "content": chapter_content,
            }
        ],
    }
    publication = WritingProjectPublication(
        id=publication_id,
        source_project_id=source_project_id,
        source_owner_user_id=source_owner_user_id,
        shared_by_user_id=shared_by_user_id,
        title=title,
        summary=summary,
        version=version,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        content_hash=f"hash-{publication_id}",
        share_scope=share_scope,
        space_ids_json=json.dumps(space_ids or [], ensure_ascii=False),
        allow_cite=True,
        allow_remix=True,
        status=status,
        published_at=stamp,
        revoked_at=None,
        created_at=stamp,
    )
    db.add(publication)
    db.flush()
    return publication


def _add_operation_and_artifact(
    db: Session,
    *,
    operation_id: str,
    artifact_id: str,
    project_id: str,
    owner_user_id: str,
    name: str,
    stamp: datetime,
) -> Artifact:
    operation = WritingOperation(
        id=operation_id,
        project_id=project_id,
        kind="export",
        status="completed",
        idempotency_key=None,
        attempt=1,
        input_revision=None,
        input_payload="{}",
        error_code=None,
        error_message_safe=None,
        last_event_cursor=0,
        usage="{}",
        result="{}",
        created_at=stamp,
        started_at=stamp,
        completed_at=stamp,
    )
    db.add(operation)
    db.flush()
    artifact = Artifact(
        id=artifact_id,
        owner_user_id=owner_user_id,
        project_id=project_id,
        operation_id=operation_id,
        name=name,
        artifact_type="writing_export",
        status="ready",
        storage_key=f"artifacts/{artifact_id}/{name}",
        content=b"fixture artifact",
        checksum_sha256=f"checksum-{artifact_id}",
        content_type="application/octet-stream",
        content_size=16,
        source_version_ids=None,
        manifest_json="{}",
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _add_document_job(
    db: Session,
    *,
    job_id: str,
    document_id: str,
    version_id: str,
    space_id: str | None,
    owner_user_id: str,
    stamp: datetime,
    status: str = "completed",
) -> DocumentJob:
    job = DocumentJob(
        id=job_id,
        document_id=document_id,
        version_id=version_id,
        space_id=space_id,
        created_by_user_id=owner_user_id,
        kind="document_processing",
        status=status,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(job)
    db.flush()
    return job


def _seed_base(db: Session) -> dict[str, str]:
    """Create one owner, one admin, one outsider, and all six search domains."""
    stamp = _stamp(1)
    admin = _make_user("usr-admin", "admin", "管理员", stamp, role="admin")
    alice = _make_user("usr-alice", "alice", "Alice", stamp)
    bob = _make_user("usr-bob", "bob", "Bob", stamp)
    db.add_all([admin, alice, bob])
    db.flush()

    alice_space = _add_space(
        db,
        space_id="space-alice",
        owner_user_id=alice.id,
        name="机场资料空间",
        stamp=stamp,
    )
    bob_space = _add_space(
        db,
        space_id="space-bob",
        owner_user_id=bob.id,
        name="共享技术资料",
        stamp=stamp,
    )
    private_space = _add_space(
        db,
        space_id="space-private",
        owner_user_id=bob.id,
        name="Bob私密空间",
        stamp=stamp,
    )
    db.add(
        SpaceMember(
            id="member-alice-bob",
            space_id=bob_space.id,
            user_id=alice.id,
            role="reader",
            created_at=stamp,
            updated_at=stamp,
        )
    )
    db.flush()

    airport_document, airport_version = _add_document(
        db,
        document_id="doc-airport",
        version_id="ver-airport",
        space_id=alice_space.id,
        owner_user_id=alice.id,
        name="机场方案.pdf",
        summary="机场方案摘要",
        source_relative_path="plans/机场方案.pdf",
        stamp=stamp,
    )
    _add_document(
        db,
        document_id="doc-bob",
        version_id="ver-bob",
        space_id=bob_space.id,
        owner_user_id=bob.id,
        name="技术架构.docx",
        summary="系统架构摘要",
        stamp=stamp,
    )
    _add_chunk(
        db,
        chunk_id="chunk-airport",
        category=derive_category(alice_space.id),
        file_id=str(stable_knowledge_id(airport_version.id)),
        content="机场方案的实施内容",
    )

    alice_chat = _add_chat(
        db,
        session_id="chat-alice",
        owner_user_id=alice.id,
        title="机场方案讨论",
        stamp=stamp,
        messages=[
            ("msg-airport", "user", "请分析机场方案"),
            ("msg-airport-answer", "assistant", "机场方案可以落地"),
        ],
    )
    bob_chat = _add_chat(
        db,
        session_id="chat-bob",
        owner_user_id=bob.id,
        title="Bob的私密会话",
        stamp=stamp,
    )
    _add_chat_publication(
        db,
        publication_id="pub-airport-chat",
        source_session_id=alice_chat.id,
        source_owner_user_id=alice.id,
        shared_by_user_id=alice.id,
        title="机场方案分享",
        summary="机场讨论公开版本",
        snapshot=[
            {"id": "msg-airport", "role": "user", "content": "机场方案"},
            {"id": "msg-airport-answer", "role": "assistant", "content": "机场方案可以落地"},
        ],
        stamp=stamp,
    )

    alice_project, airport_chapter = _add_writing_project(
        db,
        project_id="project-alice",
        outline_id="outline-alice",
        chapter_id="chapter-airport",
        owner_user_id=alice.id,
        title="机场技术白皮书",
        chapter_title="机场方案概述",
        chapter_content="本章介绍机场方案",
        stamp=stamp,
    )
    _add_writing_publication(
        db,
        publication_id="pub-airport-writing",
        source_project_id=alice_project.id,
        source_owner_user_id=alice.id,
        shared_by_user_id=alice.id,
        title="机场白皮书分享",
        summary="机场技术白皮书公开版本",
        chapter_id=airport_chapter.id,
        chapter_title=airport_chapter.title,
        chapter_content=airport_chapter.content,
        stamp=stamp,
    )
    _add_operation_and_artifact(
        db,
        operation_id="operation-airport-export",
        artifact_id="artifact-airport",
        project_id=alice_project.id,
        owner_user_id=alice.id,
        name="机场白皮书.docx",
        stamp=stamp,
    )
    _add_document_job(
        db,
        job_id="job-airport",
        document_id=airport_document.id,
        version_id=airport_version.id,
        space_id=alice_space.id,
        owner_user_id=alice.id,
        stamp=stamp,
    )
    db.commit()
    return {
        "admin_id": admin.id,
        "alice_id": alice.id,
        "bob_id": bob.id,
        "alice_space_id": alice_space.id,
        "bob_space_id": bob_space.id,
        "private_space_id": private_space.id,
        "airport_document_id": airport_document.id,
        "airport_version_id": airport_version.id,
        "alice_chat_id": alice_chat.id,
        "bob_chat_id": bob_chat.id,
        "alice_project_id": alice_project.id,
        "airport_chapter_id": airport_chapter.id,
        "airport_chat_publication_id": "pub-airport-chat",
        "airport_writing_publication_id": "pub-airport-writing",
        "airport_artifact_id": "artifact-airport",
        "airport_job_id": "job-airport",
    }


def _group(groups, group_type: str):
    return next(group for group in groups if group.type == group_type)


def _user(db: Session, ids: dict[str, str], key: str) -> User:
    user = db.get(User, ids[key])
    assert user is not None
    return user


def _assert_deep_link(
    path: str,
    *,
    pathname: str,
    params: dict[str, str],
    query: str,
) -> None:
    parsed = urlsplit(path)
    assert parsed.path == pathname
    parsed_params = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in {**params, "search": query}.items():
        assert parsed_params[key] == [value]
    encoded_query = quote(query, safe="")
    assert encoded_query in parsed.query


def test_search_all_six_domains(global_search_db: Session) -> None:
    """A readable owner receives one useful group for every search domain."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    groups = search_repo.search(
        global_search_db,
        alice,
        query="机场",
        type="all",
        limit=20,
        offset=0,
    )

    expected_types = {"space", "document", "chat", "writing", "artifact", "task"}
    assert {group.type for group in groups} == expected_types
    for group in groups:
        assert group.total >= 1
        assert group.items

    assert ids["alice_space_id"] in {item.id for item in _group(groups, "space").items}
    assert ids["airport_document_id"] in {item.id for item in _group(groups, "document").items}
    assert ids["alice_chat_id"] in {item.id for item in _group(groups, "chat").items}
    assert ids["alice_project_id"] in {item.id for item in _group(groups, "writing").items}
    assert f"artifact:{ids['airport_artifact_id']}" in {
        item.id for item in _group(groups, "artifact").items
    }
    assert f"document:{ids['airport_job_id']}" in {
        item.id for item in _group(groups, "task").items
    }
    writing_group = _group(groups, "writing")
    assert not any(item.id.startswith("shared:") for item in writing_group.items)
    assert not any("publication=" in item.path for item in writing_group.items)
    assert global_search_db.get(WritingProjectPublication, ids["airport_writing_publication_id"]) is not None
    resolved = search_repo.resolve(
        global_search_db, alice,
        [("writing", ids["alice_project_id"]),
         ("writing", f"shared:{ids['airport_writing_publication_id']}")],
    )
    assert [item.id for item in resolved] == [ids["alice_project_id"]]


@pytest.mark.parametrize("publication_type", ["chat", "writing"])
def test_authorized_shared_publication_is_found_past_inaccessible_page(
    global_search_db: Session,
    publication_type: str,
) -> None:
    """聊天快照按 ACL 分页；历史文稿快照完全退出搜索和最近访问解析。"""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    bob = _user(global_search_db, ids, "bob_id")
    private_space = global_search_db.get(KnowledgeSpace, ids["private_space_id"])
    assert private_space is not None

    source_session_id = ids["bob_chat_id"]
    source_project_id = ids["alice_project_id"]
    if publication_type == "writing":
        source_project, _ = _add_writing_project(
            global_search_db,
            project_id="project-gate-bob",
            outline_id="outline-gate-bob",
            chapter_id="chapter-gate-bob",
            owner_user_id=bob.id,
            title="Bob gate source",
            stamp=_stamp(2),
        )
        source_project_id = source_project.id

    for index, day in enumerate((20, 19, 18)):
        if publication_type == "chat":
            _add_chat_publication(
                global_search_db,
                publication_id=f"pub-gate-blocked-{index}",
                source_session_id=source_session_id,
                source_owner_user_id=bob.id,
                shared_by_user_id=bob.id,
                title=f"gate-token blocked {index}",
                summary="",
                share_scope="space",
                space_ids=[private_space.id],
                stamp=_stamp(day),
                version=index + 1,
            )
        else:
            _add_writing_publication(
                global_search_db,
                publication_id=f"pub-gate-blocked-{index}",
                source_project_id=source_project_id,
                source_owner_user_id=bob.id,
                shared_by_user_id=bob.id,
                title=f"gate-token blocked {index}",
                share_scope="space",
                space_ids=[private_space.id],
                stamp=_stamp(day),
                version=index + 1,
            )

    if publication_type == "chat":
        allowed = _add_chat_publication(
            global_search_db,
            publication_id="pub-gate-allowed-chat",
            source_session_id=source_session_id,
            source_owner_user_id=bob.id,
            shared_by_user_id=bob.id,
            title="gate-token allowed",
            summary="",
            stamp=_stamp(2),
        )
    else:
        allowed = _add_writing_publication(
            global_search_db,
            publication_id="pub-gate-allowed-writing",
            source_project_id=source_project_id,
            source_owner_user_id=bob.id,
            shared_by_user_id=bob.id,
            title="gate-token allowed",
            stamp=_stamp(2),
        )
    global_search_db.commit()

    groups = search_repo.search(
        global_search_db,
        alice,
        query="gate-token",
        type=publication_type,
        limit=1,
        offset=0,
    )
    group = groups[0]
    if publication_type == "chat":
        assert group.total == 1
        assert [item.id for item in group.items] == [f"shared:{allowed.id}"]
    else:
        assert group.total == 0
        assert group.items == []
        assert search_repo.resolve(
            global_search_db, alice, [("writing", f"shared:{allowed.id}")],
        ) == []
        assert global_search_db.get(WritingProjectPublication, allowed.id) is allowed
        assert allowed.status == "active"
    assert group.next_offset is None


@pytest.mark.parametrize("search_type", ["chat", "writing"])
def test_private_and_shared_results_share_ranked_pagination(
    global_search_db: Session,
    search_type: str,
) -> None:
    """聊天保留共享快照排序；文稿仅对自有原稿排序。"""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    bob = _user(global_search_db, ids, "bob_id")
    expected_ids: list[str]

    if search_type == "chat":
        exact = _add_chat(
            global_search_db,
            session_id="chat-rank-exact",
            owner_user_id=alice.id,
            title="rank-token",
            stamp=_stamp(1),
        )
        contains = _add_chat(
            global_search_db,
            session_id="chat-rank-contains",
            owner_user_id=alice.id,
            title="Older rank-token note",
            stamp=_stamp(3),
        )
        body = _add_chat(
            global_search_db,
            session_id="chat-rank-body",
            owner_user_id=alice.id,
            title="Fresh body conversation",
            stamp=_stamp(10),
            messages=[("msg-rank-body", "assistant", "fresh rank-token evidence")],
        )
        shared = _add_chat_publication(
            global_search_db,
            publication_id="pub-rank-chat",
            source_session_id=ids["bob_chat_id"],
            source_owner_user_id=bob.id,
            shared_by_user_id=bob.id,
            title="rank-token shared copy",
            summary="",
            stamp=_stamp(2),
        )
        expected_ids = [exact.id, f"shared:{shared.id}", contains.id, body.id]
    else:
        exact, _ = _add_writing_project(
            global_search_db,
            project_id="project-rank-exact",
            outline_id="outline-rank-exact",
            chapter_id="chapter-rank-exact",
            owner_user_id=alice.id,
            title="rank-token",
            stamp=_stamp(1),
        )
        contains, _ = _add_writing_project(
            global_search_db,
            project_id="project-rank-contains",
            outline_id="outline-rank-contains",
            chapter_id="chapter-rank-contains",
            owner_user_id=alice.id,
            title="Older rank-token note",
            stamp=_stamp(3),
        )
        body, _ = _add_writing_project(
            global_search_db,
            project_id="project-rank-body",
            outline_id="outline-rank-body",
            chapter_id="chapter-rank-body",
            owner_user_id=alice.id,
            title="Fresh body document",
            chapter_content="fresh rank-token evidence",
            stamp=_stamp(10),
        )
        source_project, source_chapter = _add_writing_project(
            global_search_db,
            project_id="project-rank-shared-source",
            outline_id="outline-rank-shared-source",
            chapter_id="chapter-rank-shared-source",
            owner_user_id=bob.id,
            title="Bob shared source",
            stamp=_stamp(2),
        )
        shared = _add_writing_publication(
            global_search_db,
            publication_id="pub-rank-writing",
            source_project_id=source_project.id,
            source_owner_user_id=bob.id,
            shared_by_user_id=bob.id,
            title="rank-token shared copy",
            chapter_id=source_chapter.id,
            chapter_title=source_chapter.title,
            chapter_content=source_chapter.content,
            stamp=_stamp(2),
        )
        expected_ids = [exact.id, contains.id, body.id]

    global_search_db.commit()
    observed: list[str] = []
    for offset, expected_id in enumerate(expected_ids):
        group = search_repo.search(
            global_search_db,
            alice,
            query="rank-token",
            type=search_type,
            limit=1,
            offset=offset,
        )[0]
        assert group.total == len(expected_ids)
        assert len(group.items) == 1
        assert group.items[0].id == expected_id
        assert group.items[0].matched_field == ("content" if expected_id == body.id else "title")
        assert group.next_offset == (offset + 1 if offset < len(expected_ids) - 1 else None)
        observed.append(group.items[0].id)
    assert observed == expected_ids

def test_equal_timestamp_uses_id_ascending(global_search_db: Session) -> None:
    """Equal-ranked rows use a stable ascending id tie-breaker."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    _add_chat(
        global_search_db,
        session_id="chat-tie-b",
        owner_user_id=alice.id,
        title="tie-token",
        stamp=_stamp(11),
    )
    _add_chat(
        global_search_db,
        session_id="chat-tie-a",
        owner_user_id=alice.id,
        title="tie-token",
        stamp=_stamp(11),
    )
    global_search_db.commit()

    group = search_repo.search(
        global_search_db,
        alice,
        query="tie-token",
        type="chat",
        limit=20,
        offset=0,
    )[0]
    assert [item.id for item in group.items] == ["chat-tie-a", "chat-tie-b"]


def test_resolve_q_preserves_message_section_version_page_and_encoded_search(
    global_search_db: Session,
) -> None:
    """Resolve recomputes precise context instead of returning generic metadata."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    space_id = ids["alice_space_id"]
    query = "定位词&/+"

    chat = _add_chat(
        global_search_db,
        session_id="chat-deep-link",
        owner_user_id=alice.id,
        title="Deep link chat",
        stamp=_stamp(4),
        messages=[("msg-deep-link", "assistant", f"消息包含{query}")],
    )
    project, chapter = _add_writing_project(
        global_search_db,
        project_id="project-deep-link",
        outline_id="outline-deep-link",
        chapter_id="section-deep-link",
        owner_user_id=alice.id,
        title="Deep link writing",
        chapter_title="目标章节",
        chapter_content=f"章节包含{query}",
        stamp=_stamp(4),
    )
    document, version = _add_document(
        global_search_db,
        document_id="doc-deep-link",
        version_id="ver-deep-link",
        space_id=space_id,
        owner_user_id=alice.id,
        name="Deep link document.pdf",
        stamp=_stamp(4),
        summary="",
        source_relative_path="deep/document.pdf",
    )
    _add_document_page(
        global_search_db,
        page_id="page-deep-link",
        version_id=version.id,
        page_number=7,
        content=f"页面包含{query}",
        stamp=_stamp(4),
    )
    global_search_db.commit()

    chat_match = _group(
        search_repo.search(global_search_db, alice, query=query, type="chat", limit=20, offset=0),
        "chat",
    ).items
    writing_match = _group(
        search_repo.search(
            global_search_db, alice, query=query, type="writing", limit=20, offset=0
        ),
        "writing",
    ).items
    document_match = _group(
        search_repo.search(
            global_search_db, alice, query=query, type="document", limit=20, offset=0
        ),
        "document",
    ).items
    chat_item = next(item for item in chat_match if item.id == chat.id)
    writing_item = next(item for item in writing_match if item.id == project.id)
    document_item = next(item for item in document_match if item.id == document.id)

    resolved = search_repo.resolve(
        global_search_db,
        alice,
        items=[
            ("chat", chat.id),
            ("writing", project.id),
            ("document", document.id),
        ],
        query=query,
    )
    by_key = {(item.type, item.id): item for item in resolved}
    assert set(by_key) == {
        ("chat", chat.id),
        ("writing", project.id),
        ("document", document.id),
    }
    _assert_deep_link(
        chat_item.path,
        pathname=f"/chat/{chat.id}",
        params={"message": "msg-deep-link"},
        query=query,
    )
    _assert_deep_link(
        writing_item.path,
        pathname=f"/writing/{project.id}",
        params={"section": chapter.id},
        query=query,
    )
    _assert_deep_link(
        document_item.path,
        pathname=f"/spaces/{space_id}/documents/{document.id}",
        params={"preview": "1", "version": version.id, "page": "7"},
        query=query,
    )
    _assert_deep_link(
        by_key["chat", chat.id].path,
        pathname=f"/chat/{chat.id}",
        params={"message": "msg-deep-link"},
        query=query,
    )
    _assert_deep_link(
        by_key["writing", project.id].path,
        pathname=f"/writing/{project.id}",
        params={"section": chapter.id},
        query=query,
    )
    _assert_deep_link(
        by_key["document", document.id].path,
        pathname=f"/spaces/{space_id}/documents/{document.id}",
        params={"preview": "1", "version": version.id, "page": "7"},
        query=query,
    )


def test_document_resolve_omits_temporary_and_space_less_rows(
    global_search_db: Session,
) -> None:
    """Temporary attachments and orphan documents never become global targets."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    temporary, _ = _add_document(
        global_search_db,
        document_id="doc-temporary-leak",
        version_id="ver-temporary-leak",
        space_id=None,
        owner_user_id=alice.id,
        name="temporary-leak-token.txt",
        stamp=_stamp(5),
        is_temporary=True,
        chat_session_id=ids["alice_chat_id"],
    )
    orphan, _ = _add_document(
        global_search_db,
        document_id="doc-space-less-leak",
        version_id="ver-space-less-leak",
        space_id=None,
        owner_user_id=alice.id,
        name="space-less-leak-token.txt",
        stamp=_stamp(5),
        is_temporary=False,
    )
    global_search_db.commit()

    search_group = search_repo.search(
        global_search_db,
        alice,
        query="leak-token",
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert search_group.total == 0
    assert search_group.items == []
    resolved = search_repo.resolve(
        global_search_db,
        alice,
        items=[("document", temporary.id), ("document", orphan.id)],
        query="leak-token",
    )
    assert resolved == []


def test_admin_cannot_resolve_another_users_generated_file(
    global_search_db: Session,
    tmp_path: Path,
) -> None:
    """Generated-file access remains owner-scoped even for an admin user."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    admin = _user(global_search_db, ids, "admin_id")
    bob = _user(global_search_db, ids, "bob_id")
    task_id = "pi-chat-foreign-generated"
    relative = "outputs/foreign-report.txt"
    workspace = tmp_path / "foreign-generated-workspace"
    workspace.mkdir()
    (workspace / "outputs").mkdir()
    (workspace / relative).write_text("private", encoding="utf-8")
    task = HarnessTask(
        id=task_id,
        tenant_id="default",
        user_id=bob.id,
        project_id="default",
        objective="foreign generated file",
        permissions_json="{}",
        max_steps=10,
        max_tokens=1000,
        timeout_seconds=60,
        max_cost=None,
        verification_build_cmd=None,
        verification_test_cmd=None,
        verification_lint_cmd=None,
        verification_build_command_id=None,
        verification_test_command_id=None,
        verification_lint_command_id=None,
        verification_custom_command_ids="[]",
        verification_max_retries=0,
        workspace_path=str(workspace),
        repository=None,
        branch=None,
        status="completed",
        created_at=_stamp(6),
        updated_at=_stamp(6),
        completed_at=_stamp(6),
    )
    global_search_db.add(task)
    session = _add_chat(
        global_search_db,
        session_id="foreign-generated",
        owner_user_id=bob.id,
        title="Foreign generated file session",
        stamp=_stamp(6),
        messages=[
            (
                "msg-foreign-generated",
                "assistant",
                f"[foreign-report.txt](/api/files/{task_id}/{relative})",
            )
        ],
    )
    global_search_db.commit()
    stable_id = f"file:{task_id}:{quote(relative, safe='')}"

    assert search_repo.resolve(
        global_search_db,
        admin,
        items=[("artifact", stable_id)],
        query="foreign-report",
    ) == []
    owner_items = search_repo.resolve(
        global_search_db,
        bob,
        items=[("artifact", stable_id)],
        query="foreign-report",
    )
    assert len(owner_items) == 1
    assert owner_items[0].id == stable_id
    assert owner_items[0].title == "foreign-report.txt"
    assert f"/chat/{session.id}" in owner_items[0].path


def test_generated_file_search_uses_real_owner_task_session_and_deduplicates(
    global_search_db: Session,
    tmp_path: Path,
) -> None:
    """A file result is backed by its task/message identity, not generic metadata."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    bob = _user(global_search_db, ids, "bob_id")
    task_id = "pi-chat-generated-file"
    relative = "outputs/report-final.txt"
    workspace = tmp_path / "generated-file-workspace"
    (workspace / "outputs").mkdir(parents=True)
    (workspace / relative).write_text("generated", encoding="utf-8")
    global_search_db.add(
        HarnessTask(
            id=task_id,
            tenant_id="default",
            user_id=alice.id,
            project_id="default",
            objective="generate report",
            permissions_json="{}",
            max_steps=10,
            max_tokens=1000,
            timeout_seconds=60,
            max_cost=None,
            verification_build_cmd=None,
            verification_test_cmd=None,
            verification_lint_cmd=None,
            verification_build_command_id=None,
            verification_test_command_id=None,
            verification_lint_command_id=None,
            verification_custom_command_ids="[]",
            verification_max_retries=0,
            workspace_path=str(workspace),
            repository=None,
            branch=None,
            status="completed",
            created_at=_stamp(7),
            updated_at=_stamp(7),
            completed_at=_stamp(7),
        )
    )
    session = _add_chat(
        global_search_db,
        session_id="generated-file",
        owner_user_id=alice.id,
        title="Generated file session",
        stamp=_stamp(7),
        messages=[
            (
                "msg-generated-file",
                "assistant",
                "\n".join(
                    [
                        f"[report-final.txt](/api/files/{task_id}/{relative})",
                        f"[report-final.txt](/api/files/{task_id}/{relative})",
                    ]
                ),
            )
        ],
    )
    wrong_task_id = "pi-chat-wrong-generated-file"
    wrong_workspace = tmp_path / "wrong-generated-file-workspace"
    wrong_workspace.mkdir()
    global_search_db.add(
        HarnessTask(
            id=wrong_task_id,
            tenant_id="default",
            user_id=bob.id,
            project_id="default",
            objective="wrong owner",
            permissions_json="{}",
            max_steps=10,
            max_tokens=1000,
            timeout_seconds=60,
            max_cost=None,
            verification_build_cmd=None,
            verification_test_cmd=None,
            verification_lint_cmd=None,
            verification_build_command_id=None,
            verification_test_command_id=None,
            verification_lint_command_id=None,
            verification_custom_command_ids="[]",
            verification_max_retries=0,
            workspace_path=str(wrong_workspace),
            repository=None,
            branch=None,
            status="completed",
            created_at=_stamp(8),
            updated_at=_stamp(8),
            completed_at=_stamp(8),
        )
    )
    _add_chat(
        global_search_db,
        session_id="wrong-generated-file",
        owner_user_id=bob.id,
        title="Wrong generated file session",
        stamp=_stamp(8),
        messages=[
            (
                "msg-wrong-generated-file",
                "assistant",
                f"[report-final.txt](/api/files/{wrong_task_id}/{relative})",
            )
        ],
    )
    global_search_db.commit()

    stable_id = f"file:{task_id}:{quote(relative, safe='')}"
    group = search_repo.search(
        global_search_db,
        alice,
        query="report-final",
        type="artifact",
        limit=20,
        offset=0,
    )[0]
    assert group.total == 1
    assert [item.id for item in group.items] == [stable_id]
    item = group.items[0]
    assert item.title == "report-final.txt"
    target = urlsplit(item.path)
    assert target.path == f"/chat/{session.id}"
    assert parse_qs(target.query) == {"result": [stable_id], "search": ["report-final"]}
    assert item.matched_field == "title"


def test_document_chunk_scope_uses_current_ready_queryable_category_and_file_id(
    global_search_db: Session,
) -> None:
    """Only the authorized current version's exact vector key can match."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    bob = _user(global_search_db, ids, "bob_id")
    visible_space_id = ids["alice_space_id"]
    private_space_id = ids["private_space_id"]
    query = "pair-scope-token"

    current, current_version = _add_document(
        global_search_db,
        document_id="doc-scope-current",
        version_id="ver-scope-current",
        space_id=visible_space_id,
        owner_user_id=alice.id,
        name="Current scope document",
        stamp=_stamp(9),
        summary="",
    )
    stale, stale_version = _add_document(
        global_search_db,
        document_id="doc-scope-stale",
        version_id="ver-scope-stale",
        space_id=visible_space_id,
        owner_user_id=alice.id,
        name="Stale scope document",
        stamp=_stamp(9),
        summary="",
        version_number=2,
    )
    old_stale_version = DocumentVersion(
        id="ver-scope-stale-old",
        document_id=stale.id,
        version_number=1,
        processing_stage="ready",
        queryable=True,
        retryable=False,
        retry_of_version_id=None,
        failure_code=None,
        failure_message=None,
        source_relative_path=None,
        file_path=None,
        ext_task_id=None,
        chunk_count=1,
        summary="",
        extraction_method=None,
        page_count=0,
        ocr_page_count=0,
        vision_page_count=0,
        vision_usage_json="{}",
        extraction_warning_code=None,
        is_current_published=False,
        created_at=_stamp(8),
        updated_at=_stamp(8),
    )
    global_search_db.add(old_stale_version)
    nonqueryable, nonqueryable_version = _add_document(
        global_search_db,
        document_id="doc-scope-nonqueryable",
        version_id="ver-scope-nonqueryable",
        space_id=visible_space_id,
        owner_user_id=alice.id,
        name="Nonqueryable scope document",
        stamp=_stamp(9),
        queryable=False,
        is_current_published=True,
        summary="",
    )
    archived, archived_version = _add_document(
        global_search_db,
        document_id="doc-scope-archived",
        version_id="ver-scope-archived",
        space_id=visible_space_id,
        owner_user_id=alice.id,
        name="Archived scope document",
        stamp=_stamp(9),
        status="archived",
        summary="",
    )
    private, private_version = _add_document(
        global_search_db,
        document_id="doc-scope-private",
        version_id="ver-scope-private",
        space_id=private_space_id,
        owner_user_id=bob.id,
        name="Private scope document",
        stamp=_stamp(9),
        summary="",
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-current",
        category=derive_category(visible_space_id),
        file_id=str(stable_knowledge_id(current_version.id)),
        content=query,
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-wrong-category",
        category=derive_category("space-other-category"),
        file_id=str(stable_knowledge_id(current_version.id)),
        content=query,
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-stale-version",
        category=derive_category(visible_space_id),
        file_id=str(stable_knowledge_id(old_stale_version.id)),
        content=query,
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-nonqueryable",
        category=derive_category(visible_space_id),
        file_id=str(stable_knowledge_id(nonqueryable_version.id)),
        content=query,
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-archived",
        category=derive_category(visible_space_id),
        file_id=str(stable_knowledge_id(archived_version.id)),
        content=query,
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-scope-private",
        category=derive_category(private_space_id),
        file_id=str(stable_knowledge_id(private_version.id)),
        content=query,
    )
    global_search_db.commit()

    group = search_repo.search(
        global_search_db,
        alice,
        query=query,
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert group.total == 1
    assert [item.id for item in group.items] == [current.id]
    assert group.items[0].content_searchable is True
    assert group.items[0].matched_field == "content"
    assert query in (group.items[0].snippet or "")


def test_name_only_indexed_document_is_content_searchable(global_search_db: Session) -> None:
    """Readiness is exposed independently from whether the name matched."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    document, _ = _add_document(
        global_search_db,
        document_id="doc-name-only-indexed",
        version_id="ver-name-only-indexed",
        space_id=ids["alice_space_id"],
        owner_user_id=alice.id,
        name="name-only-indexed.txt",
        stamp=_stamp(11),
        summary="",
    )
    _add_chunk(
        global_search_db,
        chunk_id="chunk-name-only",
        category=derive_category(ids["alice_space_id"]),
        file_id=str(stable_knowledge_id("ver-name-only-indexed")),
        content="This indexed body does not contain the filename query.",
    )
    global_search_db.commit()

    group = search_repo.search(
        global_search_db,
        alice,
        query="name-only",
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert group.total == 1
    assert [item.id for item in group.items] == [document.id]
    assert group.items[0].matched_field == "title"
    assert group.items[0].content_searchable is True


def test_membership_and_share_revocation_remove_search_and_resolve_targets(
    global_search_db: Session,
) -> None:
    """A permission change affects results, totals, and hydration immediately."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    bob = _user(global_search_db, ids, "bob_id")
    revocable_space = _add_space(
        global_search_db,
        space_id="space-revocable",
        owner_user_id=bob.id,
        name="membership-token space",
        stamp=_stamp(12),
    )
    membership = SpaceMember(
        id="member-revocable",
        space_id=revocable_space.id,
        user_id=alice.id,
        role="reader",
        created_at=_stamp(12),
        updated_at=_stamp(12),
    )
    global_search_db.add(membership)
    document, _ = _add_document(
        global_search_db,
        document_id="doc-membership-revocable",
        version_id="ver-membership-revocable",
        space_id=revocable_space.id,
        owner_user_id=bob.id,
        name="membership-token document",
        stamp=_stamp(12),
    )
    global_search_db.commit()

    space_group_before = search_repo.search(
        global_search_db,
        alice,
        query="membership-token",
        type="space",
        limit=20,
        offset=0,
    )[0]
    document_group_before = search_repo.search(
        global_search_db,
        alice,
        query="membership-token",
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert space_group_before.total == 1
    assert [item.id for item in space_group_before.items] == [revocable_space.id]
    assert document_group_before.total == 1
    assert [item.id for item in document_group_before.items] == [document.id]
    assert {
        (item.type, item.id)
        for item in search_repo.resolve(
            global_search_db,
            alice,
            items=[("space", revocable_space.id), ("document", document.id)],
        )
    } == {("space", revocable_space.id), ("document", document.id)}

    global_search_db.delete(membership)
    global_search_db.commit()
    space_group_after = search_repo.search(
        global_search_db,
        alice,
        query="membership-token",
        type="space",
        limit=20,
        offset=0,
    )[0]
    document_group_after = search_repo.search(
        global_search_db,
        alice,
        query="membership-token",
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert space_group_after.total == 0
    assert space_group_after.items == []
    assert document_group_after.total == 0
    assert document_group_after.items == []
    assert search_repo.resolve(
        global_search_db,
        alice,
        items=[("space", revocable_space.id), ("document", document.id)],
    ) == []

    source = _add_chat(
        global_search_db,
        session_id="chat-share-revocable-source",
        owner_user_id=bob.id,
        title="Share source",
        stamp=_stamp(13),
        messages=[("msg-share-revocable", "assistant", "share-token body")],
    )
    publication = _add_chat_publication(
        global_search_db,
        publication_id="pub-share-revocable",
        source_session_id=source.id,
        source_owner_user_id=bob.id,
        shared_by_user_id=bob.id,
        title="share-token publication",
        summary="",
        stamp=_stamp(13),
    )
    global_search_db.commit()
    shared_id = f"shared:{publication.id}"
    shared_before = search_repo.search(
        global_search_db,
        alice,
        query="share-token",
        type="chat",
        limit=20,
        offset=0,
    )[0]
    assert shared_before.total == 1
    assert [item.id for item in shared_before.items] == [shared_id]
    assert [item.id for item in search_repo.resolve(
        global_search_db,
        alice,
        items=[("chat", shared_id)],
        query="share-token",
    )] == [shared_id]

    publication.status = "revoked"
    publication.revoked_at = _stamp(14)
    global_search_db.commit()
    shared_after = search_repo.search(
        global_search_db,
        alice,
        query="share-token",
        type="chat",
        limit=20,
        offset=0,
    )[0]
    assert shared_after.total == 0
    assert shared_after.items == []
    assert search_repo.resolve(
        global_search_db,
        alice,
        items=[("chat", shared_id)],
        query="share-token",
    ) == []


@pytest.mark.parametrize(
    ("query", "target_name", "unrelated_name"),
    [
        ("%", "literal%token.txt", "literalXtoken.txt"),
        ("_", "literal_token.txt", "literalXtoken.txt"),
    ],
)
def test_literal_like_wildcards_match_only_literal_rows(
    global_search_db: Session,
    query: str,
    target_name: str,
    unrelated_name: str,
) -> None:
    """Percent and underscore are input characters, not SQL wildcard syntax."""
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    target, _ = _add_document(
        global_search_db,
        document_id=f"doc-literal-{query}-target",
        version_id=f"ver-literal-{query}-target",
        space_id=ids["alice_space_id"],
        owner_user_id=alice.id,
        name=target_name,
        stamp=_stamp(15),
    )
    _add_document(
        global_search_db,
        document_id=f"doc-literal-{query}-unrelated",
        version_id=f"ver-literal-{query}-unrelated",
        space_id=ids["alice_space_id"],
        owner_user_id=alice.id,
        name=unrelated_name,
        stamp=_stamp(15),
    )
    global_search_db.commit()

    group = search_repo.search(
        global_search_db,
        alice,
        query=query,
        type="document",
        limit=20,
        offset=0,
    )[0]
    assert group.total == 1
    assert [item.id for item in group.items] == [target.id]
    assert group.items[0].title == target_name
    assert group.items[0].matched_field == "title"


def test_blank_query_returns_empty(global_search_db: Session) -> None:
    from app.repositories import search as search_repo

    ids = _seed_base(global_search_db)
    alice = _user(global_search_db, ids, "alice_id")
    for query in ("", "   "):
        assert search_repo.search(
            global_search_db,
            alice,
            query=query,
            type="all",
            limit=20,
            offset=0,
        ) == []
