"""Tests for cross-space fuzzy document search."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from tests.test_documents import (
    _admin_headers,
    _create_document,
    _create_space,
)


@pytest.fixture(autouse=True)
def _stub_document_processing(monkeypatch):
    """Avoid real MinIO / knowledge pipeline calls during document API tests."""
    monkeypatch.setattr(
        "app.api.documents.minio_storage.is_minio_enabled", lambda: False
    )

    def fake_run_knowledge_pipeline(*args, **kwargs):
        return {"chunk_count": 1, "vector_ids": ["vec-test"]}

    monkeypatch.setattr(
        "app.api.documents.run_knowledge_pipeline", fake_run_knowledge_pipeline
    )


@contextmanager
def _isolated_client(tmp_path: Path) -> Iterator[TestClient]:
    """Serve the app against an isolated sqlite DB for the whole client lifetime."""
    original_db_path = settings.db_path
    original_database_url = settings.database_url
    original_upload_dir = settings.upload_dir
    db_path = tmp_path / "search.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    settings.upload_dir = tmp_path / "uploads"
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.db_path = original_db_path
        settings.database_url = original_database_url
        settings.upload_dir = original_upload_dir
        database.reset_database(db_path)


def test_cross_space_search_finds_documents_and_reports_space_name(
    tmp_path: Path,
) -> None:
    with _isolated_client(tmp_path) as client:
        space_a, headers_a = _create_space(client, "销售资料库")
        _create_document(client, space_a["id"], headers_a, "Q3 客户报价单")
        space_b, headers_b = _create_space(client, "研发文档库")
        _create_document(client, space_b["id"], headers_b, "Q3 发布说明")
        _create_document(client, space_b["id"], headers_b, "架构设计评审")

        response = client.get("/documents/search?q=Q3", headers=headers_a)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        names = {(item["name"], item["space_name"]) for item in body["items"]}
        assert ("Q3 客户报价单", "销售资料库") in names
        assert ("Q3 发布说明", "研发文档库") in names

        # Substring search should match partial names.
        partial = client.get("/documents/search?q=发布", headers=headers_a)
        assert partial.status_code == 200
        assert partial.json()["total"] == 1
        assert partial.json()["items"][0]["name"] == "Q3 发布说明"


def test_queryable_cross_space_search_uses_published_version_and_paginates(
    tmp_path: Path,
) -> None:
    from tests.test_documents import _member_headers

    with _isolated_client(tmp_path) as client:
        visible_space, admin_headers = _create_space(client, "可检索空间")
        published = _create_document(client, visible_space["id"], admin_headers, "可检索资料")
        failed_latest = _create_document(
            client,
            visible_space["id"],
            admin_headers,
            "失败最新但仍可检索资料",
        )
        unqueryable = _create_document(
            client,
            visible_space["id"],
            admin_headers,
            "尚未索引资料",
        )
        hidden_space, _ = _create_space(client, "不可访问空间")
        hidden = _create_document(client, hidden_space["id"], admin_headers, "机密资料")
        recorded_at = datetime.now(timezone.utc).isoformat()
        with transaction() as db:
            for document_id in (
                published["document"]["id"],
                failed_latest["document"]["id"],
                hidden["document"]["id"],
            ):
                db.execute(
                    text(
                        "UPDATE document_versions SET processing_stage = 'ready', "
                        "queryable = 1, retryable = 0, is_current_published = 1 "
                        "WHERE document_id = :document_id"
                    ),
                    {"document_id": document_id},
                )
            db.execute(
                text(
                    "UPDATE document_versions SET processing_stage = 'failed', "
                    "queryable = 0, retryable = 1, is_current_published = 0 "
                    "WHERE document_id = :document_id"
                ),
                {"document_id": unqueryable["document"]["id"]},
            )
            db.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, document_id, version_number, processing_stage, queryable, "
                    "retryable, failure_code, failure_message, is_current_published, "
                    "created_at, updated_at) "
                    "VALUES ('ver_search_failed_latest', :document_id, 2, 'failed', "
                    "0, 1, 'parse_failed', '解析失败', 0, :recorded_at, :recorded_at)"
                ),
                {
                    "document_id": failed_latest["document"]["id"],
                    "recorded_at": recorded_at,
                },
            )

        reader_headers = _member_headers(
            client, visible_space["id"], "search-reader", "reader"
        )
        unfiltered = client.get(
            "/documents/search?q=资料", headers=reader_headers
        )
        assert unfiltered.status_code == 200
        assert unfiltered.json()["total"] == 3

        first_page = client.get(
            "/documents/search?q=资料&queryable=true&limit=1&offset=0",
            headers=reader_headers,
        )
        second_page = client.get(
            "/documents/search?q=资料&queryable=true&limit=1&offset=1",
            headers=reader_headers,
        )
        beyond_page = client.get(
            "/documents/search?q=资料&queryable=true&limit=1&offset=2",
            headers=reader_headers,
        )
        assert first_page.status_code == 200
        assert second_page.status_code == 200
        assert beyond_page.status_code == 200
        assert first_page.json()["total"] == 2
        assert second_page.json()["total"] == 2
        assert beyond_page.json()["items"] == []
        names = {
            item["name"]
            for page in (first_page.json(), second_page.json())
            for item in page["items"]
        }
        assert names == {"可检索资料", "失败最新但仍可检索资料"}
        assert all(
            item["space_name"] == "可检索空间"
            for page in (first_page.json(), second_page.json())
            for item in page["items"]
        )


def test_cross_space_search_excludes_spaces_user_cannot_read(
    tmp_path: Path,
) -> None:
    from tests.test_documents import _accept_and_login, _member_headers

    with _isolated_client(tmp_path) as client:
        space, admin_headers = _create_space(client, "机密空间")
        _create_document(client, space["id"], admin_headers, "内部薪酬方案")
        other_headers = _member_headers(client, space["id"], "outsider", "reader")

        # Readers can see documents in spaces they belong to.
        response = client.get("/documents/search?q=薪酬", headers=other_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 1

        # Non-members cannot see documents in that space.
        stranger = _accept_and_login(client, "stranger", "Member-password-123")
        stranger_headers = {"Authorization": f"Bearer {stranger['access_token']}"}
        hidden = client.get("/documents/search?q=薪酬", headers=stranger_headers)
        assert hidden.status_code == 200
        assert hidden.json()["total"] == 0


def test_cross_space_search_requires_query(tmp_path: Path) -> None:
    with _isolated_client(tmp_path) as client:
        headers = _admin_headers(client)
        response = client.get("/documents/search", headers=headers)
        assert response.status_code == 422


def test_cross_space_search_escapes_like_wildcards(tmp_path: Path) -> None:
    """``_`` / ``%`` in the query are matched literally, not as wildcards."""
    with _isolated_client(tmp_path) as client:
        space, headers = _create_space(client, "转义测试库")
        # Underscores are valid document-name characters and exercise literal matching.
        _create_document(client, space["id"], headers, "q3_plan 文档")
        _create_document(client, space["id"], headers, "普通命名文档")

        # Without escaping, a bare underscore would match every non-empty name.
        # Literal escaping restricts the result to the name containing "_".
        bare = client.get("/documents/search?q=_", headers=headers)
        assert bare.status_code == 200
        body = bare.json()
        assert body["total"] == 1, body
        assert body["items"][0]["name"] == "q3_plan 文档"

        # "命名文_" matches nothing when "_" is literal; as a SQL LIKE wildcard,
        # it would incorrectly match "普通命名文档".
        query_underscore = client.get(
            "/documents/search?q=%E5%91%BD%E5%90%8D%E6%96%87_",
            headers=headers,
        )
        assert query_underscore.status_code == 200
        assert query_underscore.json()["total"] == 0, query_underscore.json()

        # A complete substring containing an underscore still matches literally.
        underscore = client.get(
            "/documents/search?q=q3_plan",
            headers=headers,
        )
        assert underscore.status_code == 200
        assert underscore.json()["total"] == 1

        # A percent sign must also remain literal rather than match every row.
        percent = client.get("/documents/search?q=%25", headers=headers)
        assert percent.status_code == 200
        assert percent.json()["total"] == 0, percent.json()

        # Ordinary substring matching remains unchanged.
        plain = client.get(
            "/documents/search?q=文档",
            headers=headers,
        )
        assert plain.status_code == 200
        assert plain.json()["total"] == 2
