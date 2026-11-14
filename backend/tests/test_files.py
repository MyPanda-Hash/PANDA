from __future__ import annotations

from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app.api.chat import _runtime_session_id
from app.api import files as files_api
from app.core import database
from app.core.config import settings
from app.main import app
from app.services import minio_storage
from app.services.harness import file_storage
from app.services.markdown_docx import markdown_to_docx_bytes
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from tests._auth import bootstrap_admin_response
from tests.test_usage import _isolated_client, _member_login, _restore_database


@pytest.mark.parametrize("suffix", [".html", ".HTM"])
@pytest.mark.parametrize("use_minio", [False, True])
def test_generated_html_is_collected_and_downloaded_as_attachment(
    tmp_path: Path, monkeypatch, suffix: str, use_minio: bool
) -> None:
    original_path, original_url, database_path = _isolated_client(tmp_path, "html.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.html").write_text("旧轮次文件", encoding="utf-8")
    baseline = file_storage.snapshot_deliverable_files(workspace)
    target = workspace / f"声浪 H5{suffix}"
    payload = '<!doctype html><html><script>alert("声浪")</script></html>'.encode()
    target.write_bytes(payload)
    internal = workspace / "node_modules" / "example.html"
    internal.parent.mkdir()
    internal.write_bytes(payload)
    objects: dict[str, bytes] = {}
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: use_minio)
    monkeypatch.setattr(
        minio_storage, "upload_bytes",
        lambda **kwargs: objects.setdefault(kwargs["object_key"], kwargs["content"]) and "minio://ok",
    )
    monkeypatch.setattr(minio_storage, "download_bytes", lambda **kwargs: objects[kwargs["object_key"]])
    monkeypatch.setattr(file_storage, "_workspace_registry", {})
    if use_minio:
        # 禁用本地 fallback，避免对象上传失败时把本地下载误判为 MinIO 成功。
        monkeypatch.setattr(files_api, "resolve_workspace", lambda _task_id: None)
    try:
        files = file_storage.scan_and_upload_workspace(
            "html-task", workspace, since=baseline,
            storage_task_id=f"html-task-turn-{'a' * 32}",
        )
        assert [item["name"] for item in files] == [target.name]
        href = files[0]["url"].removeprefix("/api")
        target.write_text("下一轮覆盖", encoding="utf-8")
        with TestClient(app) as client:
            assert client.get(href).status_code == 401
            app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = (
                lambda: SimpleNamespace(id="html-owner", role="admin")
            )
            response = client.get(href)
            assert response.status_code == 200
            assert response.content == payload
            assert response.headers["content-disposition"].startswith("attachment;")
            assert response.headers["content-type"].startswith("text/html")
            assert client.get(href + "?preview=true").status_code == 415
            assert client.get(href + "?preview=pdf").status_code == 415
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        _restore_database(original_path, original_url, database_path)


def test_download_generated_file_serves_workspace_and_legacy_prefix(tmp_path: Path) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()

    workspace = tmp_path / "pi-chat-chs_test"
    workspace.mkdir()
    payload = markdown_to_docx_bytes("慧云科技公司介绍\n\n公司主营企业知识管理。")
    (workspace / "慧云科技公司介绍.docx").write_bytes(payload)
    file_storage.register_workspace("pi-chat-chs_test", workspace)

    user = SimpleNamespace(id="usr-1", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            direct = client.get("/files/pi-chat-chs_test/慧云科技公司介绍.docx")
            legacy = client.get("/files/pi-chat-chs_test/files/慧云科技公司介绍.docx")
            preview = client.get("/files/pi-chat-chs_test/慧云科技公司介绍.docx?preview=true")
        local_response = files_api.download_generated_file(
            "pi-chat-chs_test",
            "慧云科技公司介绍.docx",
            user,
        )
        assert isinstance(local_response, FileResponse)
        assert direct.status_code == 200
        assert direct.content == payload
        assert legacy.status_code == 200
        assert legacy.content == payload
        assert preview.status_code == 200
        assert preview.json() == {
            "kind": "text",
            "content": "慧云科技公司介绍\n\n公司主营企业知识管理。",
            "truncated": False,
        }
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files.sqlite3")


def test_download_generated_file_accepts_access_cookie(tmp_path: Path) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-cookie.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()

    workspace = tmp_path / "pi-chat-chs_cookie"
    workspace.mkdir()
    payload = b"PK cookie-docx"
    (workspace / "intro.docx").write_bytes(payload)
    file_storage.register_workspace("pi-chat-chs_cookie", workspace)

    try:
        with TestClient(app) as anonymous:
            denied = anonymous.get("/files/pi-chat-chs_cookie/intro.docx")
        assert denied.status_code == 401

        with TestClient(app) as client:
            login = bootstrap_admin_response(client)
            token = login.json()["access_token"]
            via_login_cookie = client.get("/files/pi-chat-chs_cookie/intro.docx")
        assert via_login_cookie.status_code == 200
        assert via_login_cookie.content == payload

        with TestClient(app) as client:
            client.cookies.set("doc_nexus_access", token)
            allowed = client.get("/files/pi-chat-chs_cookie/intro.docx")
        assert allowed.status_code == 200
        assert allowed.content == payload
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-cookie.sqlite3")


def test_download_generated_file_preserves_real_files_directory(tmp_path: Path) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-directory.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()

    workspace = tmp_path / "pi-chat-files-directory"
    (workspace / "files").mkdir(parents=True)
    (workspace / "report.docx").write_bytes(b"root report")
    (workspace / "files" / "report.docx").write_bytes(b"nested report")
    file_storage.register_workspace("pi-chat-files-directory", workspace)

    user = SimpleNamespace(id="usr-1", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            response = client.get("/files/pi-chat-files-directory/files/report.docx")
        assert response.status_code == 200
        assert response.content == b"nested report"
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-directory.sqlite3")


def test_download_generated_file_reads_minio_without_exposing_presigned_host(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-minio.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()

    workspace = tmp_path / "pi-chat-chs_minio"
    workspace.mkdir()
    local_payload = b"local fallback"
    (workspace / "report.docx").write_bytes(local_payload)
    file_storage.register_workspace("pi-chat-chs_minio", workspace)

    minio_payload = markdown_to_docx_bytes("MinIO 中的正式文稿")
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        minio_storage,
        "download_bytes",
        lambda **kwargs: minio_payload,
    )
    user = SimpleNamespace(id="usr-1", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            response = client.get("/files/pi-chat-chs_minio/report.docx")
            preview = client.get("/files/pi-chat-chs_minio/report.docx?preview=true")
        assert response.status_code == 200
        assert response.content == minio_payload
        assert preview.status_code == 200
        assert preview.json()["content"] == "MinIO 中的正式文稿"
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-minio.sqlite3")


def test_download_generated_file_reads_minio_after_workspace_expires(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-minio-persistent.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()

    captured: list[str] = []
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: True)

    def download_bytes(*, object_key: str) -> bytes:
        captured.append(object_key)
        if object_key.endswith("/files/report.docx"):
            raise FileNotFoundError(object_key)
        return b"persistent object"

    monkeypatch.setattr(minio_storage, "download_bytes", download_bytes)
    monkeypatch.setattr(files_api, "resolve_workspace", lambda _task_id: None)
    user = SimpleNamespace(id="usr-1", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            response = client.get("/files/pi-chat-expired/files/report.docx")
        assert response.status_code == 200
        assert response.content == b"persistent object"
        assert captured == [
            "harness/pi-chat-expired/files/report.docx",
            "harness/pi-chat-expired/report.docx",
        ]
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-minio-persistent.sqlite3")


def test_per_turn_generated_file_namespace_authorizes_base_chat_session(
    monkeypatch,
) -> None:
    monkeypatch.setattr(files_api, "transaction", lambda: nullcontext(object()))
    monkeypatch.setattr(
        files_api.harness_repo,
        "get_task",
        lambda _db, _task_id: None,
    )
    monkeypatch.setattr(
        files_api.chat_repo,
        "get_owned_session",
        lambda _db, session_id, user_id: object()
        if session_id == "session-1" and user_id == "usr-1"
        else None,
    )
    user = SimpleNamespace(id="usr-1", role="member")
    task_id = f"pi-chat-usr-1-session-1-turn-{'a' * 32}"

    assert files_api._can_access_generated_file(user, task_id)
    assert not files_api._can_access_generated_file(
        SimpleNamespace(id="usr-2", role="member"), task_id
    )


def test_scheduled_result_download_rechecks_owner_and_source_permission(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.core.security import now
    from app.models import Base
    from app.models.access import KnowledgeSpace, SpaceMember
    from app.models.auth import User
    from app.models.schedules import Schedule, ScheduleRun

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    at = now()
    config = json.dumps(
        {"kind": "skill", "space_ids": ["schedule-source"], "document_ids": []}
    )
    with Session(engine) as db:
        for user_id, role in (("schedule-owner", "user"), ("schedule-other", "admin")):
            db.add(User(
                id=user_id, username=user_id, display_name=user_id,
                password_hash="unused", role=role, status="active", created_at=at,
            ))
        db.flush()
        db.add(KnowledgeSpace(
            id="schedule-source", name="来源资料", description="",
            owner_user_id="schedule-other", status="active", created_at=at, updated_at=at,
        ))
        db.flush()
        db.add(SpaceMember(
            id="schedule-reader", space_id="schedule-source", user_id="schedule-owner",
            role="reader", created_at=at, updated_at=at,
        ))
        db.add(Schedule(
            id="schedule-files", owner_user_id="schedule-owner", name="定时成果",
            kind="skill", status="completed", revision=1, config_json=config,
            created_at=at, updated_at=at,
        ))
        db.flush()
        db.add(ScheduleRun(
            id="schedule-file-run", schedule_id="schedule-files",
            owner_user_id="schedule-owner", trigger_kind="scheduled",
            occurrence_key=at.isoformat(), scheduled_for=at, config_json=config,
            schedule_revision=1, status="completed", runtime_id="pi-schedule-schedule-file-run",
            created_at=at, updated_at=at,
        ))
        db.commit()

    @contextmanager
    def scoped_transaction():
        with Session(engine) as db:
            yield db
            db.commit()

    monkeypatch.setattr(files_api, "transaction", scoped_transaction)
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    workspace = tmp_path / "schedule-result"
    workspace.mkdir()
    (workspace / "result.txt").write_bytes(b"durable scheduled result")
    delivery_id = f"pi-schedule-schedule-file-run-turn-{'b' * 32}"
    monkeypatch.setitem(file_storage._workspace_registry, delivery_id, workspace)
    url = f"/files/{delivery_id}/result.txt"
    identity = SimpleNamespace(id="schedule-owner", role="user")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: identity
    try:
        client = TestClient(app)
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == b"durable scheduled result"
        identity.id, identity.role = "schedule-other", "admin"
        assert client.get(url).status_code == 403
        identity.id, identity.role = "schedule-owner", "user"
        with Session(engine) as db:
            db.delete(db.get(SpaceMember, "schedule-reader"))
            db.commit()
        assert client.get(url).status_code == 403
        # 归属与来源检查同样覆盖预览，不能用 query 参数绕开下载边界。
        assert client.get(f"{url}?preview=true").status_code == 403
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        engine.dispose()


def test_download_generated_file_rejects_traversal_before_minio(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        minio_storage,
        "download_bytes",
        lambda **kwargs: calls.append(kwargs["object_key"]) or b"unexpected",
    )
    user = SimpleNamespace(id="usr-1", role="admin")

    with pytest.raises(HTTPException) as caught:
        files_api.download_generated_file("task-1", "../secret.txt", user)
    assert caught.value.status_code == 404
    assert calls == []


def test_generated_docx_preview_rejects_corrupt_content_and_foreign_access(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    workspace = tmp_path / "preview-workspace"
    workspace.mkdir()
    (workspace / "broken.docx").write_bytes(b"not a DOCX")
    (workspace / "page.html").write_text("<script>alert(1)</script>")
    with (workspace / "large.docx").open("wb") as large:
        large.truncate(10 * 1024 * 1024 + 1)
    file_storage.register_workspace("preview-task", workspace)
    user = SimpleNamespace(id="usr-preview", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            assert client.get("/files/preview-task/broken.docx?preview=true").status_code == 422
            assert client.get("/files/preview-task/page.html?preview=true").status_code == 415
            assert client.get("/files/preview-task/large.docx?preview=true").status_code == 413
            # The rendered preview shares the same package bounds, so a corrupt
            # or oversized DOCX never reaches the converter.
            assert client.get("/files/preview-task/broken.docx?preview=pdf").status_code == 422
            assert client.get("/files/preview-task/large.docx?preview=pdf").status_code == 413
            user.role = "user"
            assert client.get("/files/preview-task/broken.docx?preview=true").status_code == 403
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-preview.sqlite3")


def test_generated_docx_preview_prefers_rendered_pdf_with_text_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-preview-pdf.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    workspace = tmp_path / "preview-pdf-workspace"
    workspace.mkdir()
    payload = markdown_to_docx_bytes("# 慧云科技公司介绍\n\n公司主营企业知识管理。")
    (workspace / "慧云科技公司介绍.docx").write_bytes(payload)
    (workspace / "notes.txt").write_text("hello")
    file_storage.register_workspace("preview-pdf-task", workspace)
    monkeypatch.setattr(
        files_api,
        "render_office_preview_pdf",
        lambda content, filename: b"%PDF-1.7 rendered",
    )
    user = SimpleNamespace(id="usr-pdf", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            rendered = client.get(
                "/files/preview-pdf-task/慧云科技公司介绍.docx?preview=pdf"
            )
            unsupported = client.get("/files/preview-pdf-task/notes.txt?preview=pdf")
            unknown = client.get(
                "/files/preview-pdf-task/慧云科技公司介绍.docx?preview=sometimes"
            )
            text = client.get(
                "/files/preview-pdf-task/慧云科技公司介绍.docx?preview=true"
            )
            download = client.get("/files/preview-pdf-task/慧云科技公司介绍.docx")
        assert rendered.status_code == 200
        assert rendered.headers["content-type"] == "application/pdf"
        assert rendered.headers["content-disposition"] == "inline"
        assert rendered.content == b"%PDF-1.7 rendered"
        assert unsupported.status_code == 415
        assert unknown.status_code == 400
        assert text.status_code == 200
        assert text.json()["content"] == "慧云科技公司介绍\n\n公司主营企业知识管理。"
        assert download.status_code == 200
        assert download.content == payload

        # No renderer: the PDF request fails loudly so the caller can fall back
        # to the bounded text preview instead of showing a broken document.
        def unavailable(content: bytes, filename: str) -> bytes:
            raise files_api.LibreOfficeUnavailable("文档渲染组件不可用，请下载原文件查看")

        monkeypatch.setattr(files_api, "render_office_preview_pdf", unavailable)
        with TestClient(app) as client:
            assert (
                client.get(
                    "/files/preview-pdf-task/慧云科技公司介绍.docx?preview=pdf"
                ).status_code
                == 422
            )
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-preview-pdf.sqlite3")


def _minimal_pptx() -> bytes:
    """A real zip package with an accepted suffix; contents never converted."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def _minimal_xlsx() -> bytes:
    """A real zip package with an accepted suffix; contents never converted."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def test_preview_pdf_routes_by_suffix_and_keeps_suffix_guard(monkeypatch) -> None:
    """A deck must reach the Impress filter; anything else stays rejected."""
    calls: list[str] = []
    monkeypatch.setattr(
        files_api,
        "render_office_preview_pdf",
        lambda content, filename: calls.append("writer") or b"%PDF-1.7 writer",
    )
    monkeypatch.setattr(
        files_api,
        "render_presentation_preview_pdf",
        lambda content, filename: calls.append("impress") or b"%PDF-1.7 impress",
    )
    monkeypatch.setattr(
        files_api,
        "render_spreadsheet_preview_pdf",
        lambda content, filename: calls.append("calc") or b"%PDF-1.7 calc",
    )
    deck = _minimal_pptx()
    book = _minimal_xlsx()

    rendered_deck = files_api._rendered_office_preview(deck, "慧云科技_介绍.pptx")
    rendered_doc = files_api._rendered_office_preview(
        markdown_to_docx_bytes("# 标题"), "慧云科技公司介绍.docx"
    )
    rendered_book = files_api._rendered_office_preview(book, "季度数据.xlsx")

    assert calls == ["impress", "writer", "calc"]
    assert rendered_deck.body == b"%PDF-1.7 impress"
    assert rendered_doc.body == b"%PDF-1.7 writer"
    assert rendered_book.body == b"%PDF-1.7 calc"
    with pytest.raises(HTTPException) as unsupported:
        files_api._rendered_office_preview(deck, "慧云科技_介绍.key")
    assert unsupported.value.status_code == 415
    # Macro-enabled and legacy binary workbooks stay download-only.
    for guarded in ("旧表.xls", "旧表.xlsm"):
        with pytest.raises(HTTPException) as refused:
            files_api._rendered_office_preview(book, guarded)
        assert refused.value.status_code == 415


def test_generated_pptx_preview_renders_pdf_without_text_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-pptx-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    workspace = tmp_path / "pptx-preview-workspace"
    workspace.mkdir()
    deck = _minimal_pptx()
    (workspace / "慧云科技_介绍.pptx").write_bytes(deck)
    (workspace / "broken.pptx").write_bytes(b"not a deck")
    (workspace / "page.html").write_text("<script>alert(1)</script>")
    with (workspace / "large.pptx").open("wb") as large:
        large.truncate(files_api.MAX_PRESENTATION_PREVIEW_BYTES + 1)
    file_storage.register_workspace("pptx-preview-task", workspace)
    monkeypatch.setattr(
        files_api,
        "render_presentation_preview_pdf",
        lambda content, filename: b"%PDF-1.7 slides",
    )
    user = SimpleNamespace(id="usr-pptx", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            rendered = client.get(
                "/files/pptx-preview-task/慧云科技_介绍.pptx?preview=pdf"
            )
            # A deck has no extracted-text mode: its layout is the content.
            text = client.get("/files/pptx-preview-task/慧云科技_介绍.pptx?preview=true")
            broken = client.get("/files/pptx-preview-task/broken.pptx?preview=pdf")
            large = client.get("/files/pptx-preview-task/large.pptx?preview=pdf")
            html = client.get("/files/pptx-preview-task/page.html?preview=pdf")
            download = client.get("/files/pptx-preview-task/慧云科技_介绍.pptx")
        assert rendered.status_code == 200
        assert rendered.headers["content-type"] == "application/pdf"
        assert rendered.headers["content-disposition"] == "inline"
        assert rendered.content == b"%PDF-1.7 slides"
        assert text.status_code == 415
        assert broken.status_code == 422
        assert large.status_code == 413
        assert html.status_code == 415
        assert download.status_code == 200
        assert download.content == deck

        # No Impress filter: fail loudly and leave the download as the only way.
        def unavailable(content: bytes, filename: str) -> bytes:
            raise files_api.LibreOfficeUnavailable("文档渲染组件不可用，请下载原文件查看")

        monkeypatch.setattr(files_api, "render_presentation_preview_pdf", unavailable)
        with TestClient(app) as client:
            assert (
                client.get(
                    "/files/pptx-preview-task/慧云科技_介绍.pptx?preview=pdf"
                ).status_code
                == 422
            )
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-pptx-preview.sqlite3")


def test_generated_xlsx_preview_renders_pdf_without_text_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "files-xlsx-preview.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    database.reset_database(settings.db_path)
    database.initialize_database()
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    workspace = tmp_path / "xlsx-preview-workspace"
    workspace.mkdir()
    book = _minimal_xlsx()
    (workspace / "季度数据.xlsx").write_bytes(book)
    (workspace / "broken.xlsx").write_bytes(b"not a sheet")
    (workspace / "page.html").write_text("<script>alert(1)</script>")
    (workspace / "旧表.xls").write_bytes(book)
    (workspace / "旧表.xlsm").write_bytes(book)
    with (workspace / "large.xlsx").open("wb") as large:
        large.truncate(files_api.MAX_SPREADSHEET_PREVIEW_BYTES + 1)
    file_storage.register_workspace("xlsx-preview-task", workspace)
    monkeypatch.setattr(
        files_api,
        "render_spreadsheet_preview_pdf",
        lambda content, filename: b"%PDF-1.7 sheet",
    )
    user = SimpleNamespace(id="usr-xlsx", role="admin")
    app.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: user
    try:
        with TestClient(app) as client:
            rendered = client.get("/files/xlsx-preview-task/季度数据.xlsx?preview=pdf")
            # A workbook has no extracted-text mode: its grid is the content.
            text = client.get("/files/xlsx-preview-task/季度数据.xlsx?preview=true")
            broken = client.get("/files/xlsx-preview-task/broken.xlsx?preview=pdf")
            large = client.get("/files/xlsx-preview-task/large.xlsx?preview=pdf")
            html = client.get("/files/xlsx-preview-task/page.html?preview=pdf")
            legacy = client.get("/files/xlsx-preview-task/旧表.xls?preview=pdf")
            macro = client.get("/files/xlsx-preview-task/旧表.xlsm?preview=pdf")
            download = client.get("/files/xlsx-preview-task/季度数据.xlsx")
        assert rendered.status_code == 200
        assert rendered.headers["content-type"] == "application/pdf"
        assert rendered.headers["content-disposition"] == "inline"
        assert rendered.content == b"%PDF-1.7 sheet"
        assert text.status_code == 415
        assert broken.status_code == 422
        assert large.status_code == 413
        assert html.status_code == 415
        assert legacy.status_code == 415
        assert macro.status_code == 415
        assert download.status_code == 200
        assert download.content == book

        # No Calc filter: fail loudly and leave the download as the only way.
        def unavailable(content: bytes, filename: str) -> bytes:
            raise files_api.LibreOfficeUnavailable("文档渲染组件不可用，请下载原文件查看")

        monkeypatch.setattr(files_api, "render_spreadsheet_preview_pdf", unavailable)
        with TestClient(app) as client:
            failure = client.get("/files/xlsx-preview-task/季度数据.xlsx?preview=pdf")
        assert failure.status_code == 422
        assert failure.json()["detail"] == "表格暂无法在线预览，请下载原文件查看"
    finally:
        app.dependency_overrides.pop(files_api.current_user_from_bearer_or_cookie, None)
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "files-xlsx-preview.sqlite3")


@pytest.mark.parametrize("workspace_kind", ["session", "legacy", "default"])
def test_employee_generated_docx_access_is_owner_scoped(
    tmp_path: Path, monkeypatch, workspace_kind: str
) -> None:
    original_path, original_url, database_path = _isolated_client(
        tmp_path, "employee-files.sqlite3"
    )
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    payload = markdown_to_docx_bytes("# 员工新闻稿\n\n仅文件所属员工可访问。")
    workspace = tmp_path / "employee-workspace"
    workspace.mkdir()
    (workspace / "新闻稿.docx").write_bytes(payload)
    try:
        with TestClient(app) as client:
            owner, owner_id = _member_login(client, "file-owner")
            other, other_id = _member_login(client, "file-other")
            created = client.post("/chat/sessions", headers=owner, json={})
            assert created.status_code == 201
            session_id = created.json()["id"]
            task_id = {
                "session": _runtime_session_id(owner_id, session_id),
                "legacy": f"pi-chat-{session_id}",
                "default": f"pi-chat-{owner_id}",
            }[workspace_kind]
            file_storage.register_workspace(task_id, workspace)
            path = f"/files/{task_id}/新闻稿.docx"
            for suffix in ("", "?preview=true"):
                assert client.get(path + suffix, headers=other).status_code == 403
            downloaded = client.get(path, headers=owner)
            assert downloaded.status_code == 200
            assert downloaded.content == payload
            preview = client.get(path + "?preview=true", headers=owner)
            assert preview.status_code == 200
            assert "仅文件所属员工可访问。" in preview.json()["content"]

            if workspace_kind == "session":
                # Owning a local session must not authorize a different user's namespace.
                for forged_id in (
                    _runtime_session_id(other_id, session_id),
                    _runtime_session_id(owner_id + "-extra", session_id),
                    _runtime_session_id(owner_id, "chs_missing_session"),
                ):
                    for suffix in ("", "?preview=true"):
                        denied = client.get(
                            f"/files/{forged_id}/新闻稿.docx{suffix}", headers=owner
                        )
                        assert denied.status_code == 403
            if workspace_kind != "default":
                deleted = client.delete(f"/chat/sessions/{session_id}", headers=owner)
                assert deleted.status_code == 204
                for suffix in ("", "?preview=true"):
                    assert client.get(path + suffix, headers=owner).status_code == 403
    finally:
        _restore_database(original_path, original_url, database_path)
