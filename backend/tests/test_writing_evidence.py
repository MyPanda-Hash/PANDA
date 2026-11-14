import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Base, Document, DocumentVersion
from app.models.auth import User
from app.services.evidence_inventory import inventory_evidence, is_inventory_request, merge_inventory_pages
from app.services.evidence_snapshots import make_citation_id, parse_citation_id, read_snapshot
from app.services.retrieval_contract import build_evidence_location

from app.services.hybrid_retrieval import RetrievalBundle
from app.services.writing_evidence import (
    expand_citation_for_project,
    normalize_citation_id,
    search,
    split_citation_span,
    verified_citation_ids,
)


@pytest.fixture(autouse=True)
def _isolated_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _seed_document(db, tmp_path, key, text, *, stage="ready", space_id="space_1"):
    stamp = datetime.now(timezone.utc)
    document = Document(
        id=f"doc_{key}", name=f"证书 {key}", space_id=space_id,
        status="active", created_by_user_id="user",
        created_at=stamp, updated_at=stamp,
    )
    path = tmp_path / f"{key}.txt"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    version = DocumentVersion(
        id=f"ver_{key}", document_id=document.id, version_number=1,
        processing_stage=stage, queryable=stage == "ready",
        is_current_published=stage == "ready",
        file_path=str(path) if text is not None else None,
        created_at=stamp, updated_at=stamp,
    )
    db.add_all([document, version])
    db.flush()
    return document, version


@pytest.mark.parametrize(
    ("document_ids", "expected"),
    [
        (None, {"doc_knowledge", "doc_other", "doc_attachment"}),
        ([], {"doc_attachment"}),
        (["doc_knowledge"], {"doc_knowledge", "doc_attachment"}),
        (
            ["doc_knowledge", "doc_private", "doc_unlisted"],
            {"doc_knowledge", "doc_attachment"},
        ),
    ],
)
def test_retrieval_and_inventory_union_authorized_attachments(
    evidence_db, tmp_path, monkeypatch, document_ids, expected,
):
    from app.services.hybrid_retrieval import retrieve_for_qa

    _seed_document(evidence_db, tmp_path, "knowledge", "证书知识")
    _seed_document(evidence_db, tmp_path, "other", "证书其他知识")
    attachment, _ = _seed_document(
        evidence_db, tmp_path, "attachment", "证书附件", space_id=None,
    )
    attachment.is_temporary = True
    attachment.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    _seed_document(evidence_db, tmp_path, "private", "证书私有", space_id="private")
    _seed_document(evidence_db, tmp_path, "orphan", "证书无空间", space_id=None)
    moved, _ = _seed_document(
        evidence_db, tmp_path, "moved", "证书已移入空间", space_id="private",
    )
    moved.is_temporary = True
    expired, _ = _seed_document(
        evidence_db, tmp_path, "expired", "证书过期附件", space_id=None,
    )
    expired.is_temporary = True
    expired.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    unlisted, _ = _seed_document(
        evidence_db, tmp_path, "unlisted", "证书未授权附件", space_id=None,
    )
    unlisted.is_temporary = True
    evidence_db.flush()
    attachments = [
        attachment.id, "doc_private", "doc_orphan", moved.id, expired.id,
    ]
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._graph_branch",
        lambda *args, **kwargs: (
            {"doc_private": 100.0, "doc_attachment": 1.0},
            [], "", True, {"doc_private", "doc_attachment"},
        ),
    )
    bundle = retrieve_for_qa(
        evidence_db, "证书", ["space_1"], document_ids=document_ids,
        attachment_document_ids=attachments, top_k=20,
    )
    assert {document.id for document, _ in bundle.documents} == expected
    assert set(bundle.meta["graph_boosted_doc_ids"]) == {"doc_attachment"}
    inventory = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=document_ids,
        attachment_document_ids=attachments, query="证书清单",
    )
    assert {item["document_id"] for item in inventory["evidence"]} == expected


def test_inventory_attachment_cursor_binds_scope_and_original_snapshot(
    evidence_db, tmp_path,
):
    knowledge, _ = _seed_document(evidence_db, tmp_path, "knowledge", "知识" * 3_000)
    attachment, version = _seed_document(
        evidence_db, tmp_path, "attachment", "附件" * 3_000, space_id=None,
    )
    attachment.is_temporary = True
    evidence_db.flush()
    scope = {
        "space_ids": ["space_1"], "document_ids": None,
        "attachment_document_ids": [attachment.id], "query": "全部证书",
        "max_chars": 4_000,
    }
    first = inventory_evidence(evidence_db, **scope)
    cursor = first["next_cursor"]
    assert cursor is not None
    for changed_ids in ([], None, ["doc_other"]):
        with pytest.raises(ValueError, match="scope changed"):
            inventory_evidence(
                evidence_db, **{**scope, "attachment_document_ids": changed_ids},
                cursor=cursor,
            )
    Path(version.file_path).write_text("不得替换原附件", encoding="utf-8")
    _seed_document(evidence_db, tmp_path, "later", "不应进入旧快照")
    pages = [first]
    while cursor is not None:
        page = inventory_evidence(evidence_db, **scope, cursor=cursor)
        pages.append(page)
        cursor = page["next_cursor"]
        assert len(pages) <= 4
    items = [item for page in pages for item in page["evidence"]]
    assert {item["document_id"] for item in items} == {knowledge.id, attachment.id}
    for document_id, original in (
        (knowledge.id, "知识" * 3_000), (attachment.id, "附件" * 3_000),
    ):
        assert "".join(
            item["excerpt"] for item in items if item["document_id"] == document_id
        ) == original
    assert pages[-1]["coverage"]["complete"] is True
    attachment.space_id = "private"
    evidence_db.flush()
    with pytest.raises(ValueError, match="scope is no longer available"):
        inventory_evidence(evidence_db, **scope, cursor=first["next_cursor"])


@pytest.mark.parametrize("invalidated_field", ["expires_at", "temporary_cleanup_at"])
def test_new_inventory_scope_rejects_invalidated_knowledge_snapshot(
    evidence_db, tmp_path, invalidated_field,
):
    from app.repositories.qa import ready_documents_for_scope

    document, _ = _seed_document(evidence_db, tmp_path, "knowledge", "知识" * 3_000)
    scope = {
        "space_ids": ["space_1"], "document_ids": None,
        "attachment_document_ids": [], "query": "全部证书", "max_chars": 4_000,
    }
    first = inventory_evidence(evidence_db, **scope)
    assert first["next_cursor"] is not None
    setattr(document, invalidated_field, datetime.now(timezone.utc) - timedelta(hours=1))
    evidence_db.flush()
    with pytest.raises(ValueError, match="scope is no longer available"):
        inventory_evidence(evidence_db, **scope, cursor=first["next_cursor"])
    fresh = inventory_evidence(evidence_db, **scope)
    assert fresh["coverage"]["expected"] == 0
    assert fresh["evidence"] == []
    assert ready_documents_for_scope(
        evidence_db, ["space_1"], attachment_document_ids=[],
    ) == []
    assert [
        item.id for item, _ in ready_documents_for_scope(evidence_db, ["space_1"])
    ] == [document.id]


def test_normalize_citation_id_unwraps_editor_and_legacy_markers():
    assert (
        normalize_citation_id("^[writing-evidence:ver_1:10-20]")
        == "writing-evidence:ver_1:10-20"
    )
    assert (
        normalize_citation_id("[citation:writing-evidence:ver_1:14-900]")
        == "writing-evidence:ver_1:14-900"
    )
    assert normalize_citation_id("[citation:0]") == "citation:0"
    assert (
        normalize_citation_id("citation:writing-evidence:ver_1:0-1")
        == "writing-evidence:ver_1:0-1"
    )


def test_hybrid_search_returns_exact_snapshot_spans(monkeypatch):
    document = SimpleNamespace(id="doc_1", name="产品手册")
    version = SimpleNamespace(id="ver_1", version_number=3)
    bundle = RetrievalBundle(documents=cast(Any, [(document, version)]))
    body = "  第一段讲产品定位。\n\n第二段讲围栏策略与合规要求。\n"
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa", lambda *args, **kwargs: bundle
    )
    monkeypatch.setattr(
        "app.services.writing_evidence._resolve_version_text", lambda item: body
    )
    result = search(
        cast(Any, None), space_ids=["space_1"], chapter_id="wch_1",
        query="围栏策略", purpose="outline",
    )
    top = result["evidence"][0]
    parsed = parse_citation_id(top["id"])
    assert parsed == ("ver_1", top["text_sha256"], 0, len(body))
    assert read_snapshot("ver_1", top["text_sha256"]) == body
    assert "围栏策略与合规要求" in top["excerpt"]
    assert result["retrieval"]["exhaustive"] is False


def test_hybrid_search_preserves_short_certificate_fields(monkeypatch):
    document = SimpleNamespace(id="doc_1", name="软件著作权证书")
    version = SimpleNamespace(id="ver_1", version_number=1)
    body = (
        "计算机软件著作权登记证书\n\n软件名称：知识协作系统 V2.0\n\n"
        "著作权人：示例科技有限公司\n\n登记号：2026SR012345\n\n"
        "首次发表日期：2025年12月10日\n\n发证日期：2026年01月20日"
    )
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa",
        lambda *args, **kwargs: RetrievalBundle(documents=cast(Any, [(document, version)])),
    )
    monkeypatch.setattr(
        "app.services.writing_evidence._resolve_version_text", lambda item: body
    )
    result = search(
        cast(Any, None), space_ids=["space_1"], chapter_id="wch_1",
        query="软件名称", purpose="document", top_k=1,
    )
    assert result["evidence"][0]["excerpt"] == body
    assert split_citation_span(result["evidence"][0]["id"]) == ("ver_1", 0, len(body))


def test_hybrid_search_reports_knowledge_gap_without_documents(monkeypatch):
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa",
        lambda *args, **kwargs: RetrievalBundle(
            documents=[], meta={"retrieval_method": "pool_fallback"}
        ),
    )

    result = search(
        cast(Any, None),
        space_ids=["space_1"],
        chapter_id="wch_1",
        query="missing",
        purpose="document",
    )

    assert result["evidence"] == []
    assert result["knowledge_gaps"] == ["no_queryable_evidence"]


def test_hybrid_search_marks_visual_version_uncertain_without_page_lookup(monkeypatch):
    document = SimpleNamespace(id="doc_vision", name="扫描资料")
    version = SimpleNamespace(id="ver_vision", version_number=1, extraction_method="vision")
    bundle = RetrievalBundle(
        documents=cast(Any, [(document, version)]),
        meta={"retrieval_method": "lexical_relevance"},
    )
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa", lambda *args, **kwargs: bundle
    )
    monkeypatch.setattr(
        "app.services.writing_evidence._resolve_version_text",
        lambda _item: "视觉识别内容：编号 A-001",
    )
    result = search(
        cast(Any, None),
        space_ids=["space_1"],
        chapter_id="wch_1",
        query="编号",
        purpose="document",
    )
    assert result["evidence"][0]["support_state"] == "uncertain"


def test_verified_citation_ids_intersects_text_markers_with_recorded_ids():
    recorded = {"writing-evidence:ver_1:10-20", "writing-evidence:ver_1:30-40"}
    text = (
        "正文 ^[writing-evidence:ver_1:10-20] 与 "
        "[citation:writing-evidence:ver_1:30-40] 并存"
    )
    assert verified_citation_ids([text], recorded) == recorded


def test_verified_citation_ids_drops_recorded_ids_that_left_the_text():
    """Retrieved evidence that never made it into the text is not a citation here."""
    text = "只保留一条 ^[writing-evidence:ver_1:10-20]"
    assert verified_citation_ids(
        [text], ["writing-evidence:ver_1:10-20", "writing-evidence:ver_1:99-100"]
    ) == {"writing-evidence:ver_1:10-20"}


def test_verified_citation_ids_requires_the_recorded_side():
    """A hand-typed marker is never a credential on its own."""
    handwritten = "[citation:writing-evidence:ver_forged:1-2]"
    assert verified_citation_ids([handwritten], []) == set()
    assert verified_citation_ids([handwritten], ["citation:0"]) == set()
    assert verified_citation_ids([handwritten], ["writing-evidence:ver_1:1-2"]) == set()


def test_verified_citation_ids_ignores_markers_it_cannot_normalize():
    assert verified_citation_ids(["[citation:0] 与普通文本"], []) == set()
    assert verified_citation_ids(["[citation:0]"], ["writing-evidence:ver_1:1-2"]) == set()


def test_verified_citation_ids_matches_across_marker_wrappings():
    recorded = {"writing-evidence:ver_1:10-20"}
    for marker in (
        "^[writing-evidence:ver_1:10-20]",
        "[writing-evidence:ver_1:10-20]",
        "[citation:writing-evidence:ver_1:10-20]",
        "^[citation:writing-evidence:ver_1:10-20]",
    ):
        assert verified_citation_ids([f"正文 {marker} 结尾"], recorded) == recorded


def test_verified_citation_ids_ignores_markers_inside_code_fences_text_only():
    """Extraction is textual: no document lookup happens here either way."""
    recorded = {"writing-evidence:ver_1:10-20"}
    assert verified_citation_ids([""], recorded) == set()
    assert verified_citation_ids([cast(Any, None)], recorded) == set()


def test_inventory_enumerates_beyond_relevance_and_database_page_limits(evidence_db, tmp_path):
    expected = set()
    for index in range(103):
        document, _version = _seed_document(
            evidence_db, tmp_path, f"{index:03d}",
            f"证书编号：CERT-{index:03d}\n\n持有人：示例企业\n\n有效期：2030年",
        )
        expected.add(document.id)
    _seed_document(evidence_db, tmp_path, "other_space", "不可见证书", space_id="space_other")
    current = evidence_db.get(DocumentVersion, "ver_000")
    evidence_db.add(DocumentVersion(
        id="ver_newer_unpublished", document_id="doc_000", version_number=2,
        processing_stage="queued", queryable=False, is_current_published=False,
        created_at=current.created_at, updated_at=current.updated_at,
    ))
    evidence_db.flush()
    result = search(
        evidence_db, space_ids=["space_1"], chapter_id="",
        query="列出全部资质证书", purpose="document", top_k=1,
    )
    assert {item["document_id"] for item in result["evidence"]} == expected
    assert [row["document_id"] for row in result["coverage"]["documents"]] == sorted(expected)
    assert result["coverage"]["expected"] == result["coverage"]["processed"] == 103
    assert result["coverage"]["complete"] is True
    assert next(item for item in result["evidence"] if item["document_id"] == "doc_000")[
        "document_version_id"
    ] == "ver_000"


def test_inventory_accounts_for_missing_failed_unprocessed_and_truncated_text(
    evidence_db, tmp_path,
):
    _seed_document(evidence_db, tmp_path, "ok", "完整证书：ABC-123")
    _seed_document(evidence_db, tmp_path, "missing", None)
    _seed_document(evidence_db, tmp_path, "failed", None, stage="failed")
    _seed_document(evidence_db, tmp_path, "queued", None, stage="queued")
    long_text = "长文原始正文。" * 4_000
    _seed_document(evidence_db, tmp_path, "long", long_text)
    result = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None, query="资料清单",
    )
    coverage = result["coverage"]
    assert {key: coverage[key] for key in (
        "expected", "processed", "missing", "failed", "unprocessed", "truncated", "complete"
    )} == {
        "expected": 5, "processed": 1, "missing": 1, "failed": 1,
        "unprocessed": 1, "truncated": 1, "complete": False,
    }
    row = next(row for row in coverage["documents"] if row["document_id"] == "doc_long")
    pages = [item for item in result["evidence"] if item["document_id"] == "doc_long"]
    assert "".join(item["excerpt"] for item in pages) == long_text[:row["covered_chars"]]
    assert row["unprocessed_ranges"] == [[row["covered_chars"], len(long_text)]]
    assert row["covered_chars"] < len(long_text)
    assert "inventory_incomplete" in result["knowledge_gaps"]


def test_inventory_explicit_empty_scope_never_falls_back_to_spaces(evidence_db, tmp_path):
    _seed_document(evidence_db, tmp_path, "visible", "证书内容")
    result = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=[], query="全部证书",
    )
    assert result["evidence"] == []
    assert result["coverage"]["expected"] == 0
    unavailable = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=["doc_absent"], query="全部证书",
    )
    assert unavailable["coverage"]["missing"] == 1
    assert unavailable["coverage"]["complete"] is False


def test_hash_bound_expansion_survives_changed_source_and_rejects_unbound_text(
    evidence_db, tmp_path, monkeypatch,
):
    original = "  软件名称：协作平台\n\n登记号：2026SR12345\n"
    evidence_db.add(User(
        id="user", username="uploader", display_name="资料上传者",
        password_hash="unused", created_at=datetime.now(timezone.utc),
    ))
    document, version = _seed_document(evidence_db, tmp_path, "stable", original)
    document.created_at = datetime(2024, 1, 2, 3, 4, 5)
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa",
        lambda *args, **kwargs: RetrievalBundle(documents=[(document, version)]),
    )
    relevance = search(
        evidence_db, space_ids=["space_1"], document_ids=[document.id],
        chapter_id="", query="登记号", purpose="document",
    )
    result = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=[document.id], query="证书清单",
    )
    citation_id = result["evidence"][0]["id"]
    Path(version.file_path).write_text("重提取后增加的页首\n完全不同的正文", encoding="utf-8")
    expanded = expand_citation_for_project(
        evidence_db, space_ids=["space_1"], document_ids=[document.id],
        citation_id=citation_id, before=0, after=0,
    )
    assert expanded["highlighted"] == original
    assert expanded["binding_state"] == "verified"
    assert expanded["char_start"] == 0 and expanded["char_end"] == len(original)
    for source in (relevance["evidence"][0], result["evidence"][0], expanded):
        assert source["created_by_user_id"] == "user"
        assert source["created_by_display_name"] == "资料上传者"
        assert source["created_at"] == "2024-01-02T03:04:05+00:00"
    for unresolved in (
        make_citation_id(version.id, "0" * 64, 0, len(original)),
        f"writing-evidence:{version.id}:0-{len(original)}",
    ):
        stale = expand_citation_for_project(
            evidence_db, space_ids=["space_1"], document_ids=[document.id],
            citation_id=unresolved,
        )
        assert stale["binding_state"] == "stale"
        assert stale["excerpt"] == stale["highlighted"] == ""
        assert stale["support_state"] == "uncertain"
    assert expand_citation_for_project(
        evidence_db, space_ids=["space_other"], document_ids=None,
        citation_id=citation_id,
    ) is None
    assert expand_citation_for_project(
        evidence_db, space_ids=["space_1"], document_ids=[], citation_id=citation_id,
    ) is None


@pytest.mark.parametrize(
    ("warning", "uncertainty", "expected_state"),
    [
        (None, "", "supported"),
        ("image_vision_uncertain", "", "supported"),
        ("image_vision_uncertain", "\n\n【不确定性（不可作确定事实）】\n编号末位模糊", "uncertain"),
        ("image_vision_unclear", "\n\n【不确定性（不可作确定事实）】\n编号末位模糊", "uncertain"),
        ("image_vision_partial", "", "uncertain"),
    ],
)
def test_image_inventory_and_expansion_keep_raw_snapshot_identity(
    evidence_db, tmp_path, monkeypatch, warning, uncertainty, expected_state,
):
    from app.services.document_processor import write_extracted_sidecar

    header = "视觉模型解析，未经核验" if warning else ""
    body = header + "\n\n【可见文字】\n编号 A-001\n原文：未经核验" + uncertainty
    document, version = _seed_document(evidence_db, tmp_path, "photo", body)
    version.source_relative_path = "photo.jpg"
    version.file_path = str(tmp_path / "photo.jpg")
    write_extracted_sidecar(version.file_path, body, version_id=version.id)
    version.extraction_method = "vision"
    version.extraction_warning_code = warning
    evidence_db.flush()
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa",
        lambda *args, **kwargs: RetrievalBundle(documents=[(document, version)]),
    )
    relevance = search(
        evidence_db, space_ids=["space_1"], document_ids=[document.id],
        chapter_id="", query="编号", purpose="document",
    )
    inventory = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=[document.id], query="全部资料",
    )
    source = inventory["evidence"][0]
    assert source["id"] == relevance["evidence"][0]["id"]
    assert read_snapshot(version.id, source["text_sha256"]) == body
    assert (source["char_start"], source["char_end"]) == (0, len(body))
    write_extracted_sidecar(
        version.file_path, "新的解析正文，不应替代冻结来源", version_id=version.id,
    )
    expanded = expand_citation_for_project(
        evidence_db, space_ids=["space_1"], document_ids=[document.id],
        citation_id=source["id"], before=0, after=0,
    )
    for item in (source, relevance["evidence"][0], expanded):
        assert item["support_state"] == expected_state
        assert "视觉模型解析，未经核验" not in item["excerpt"]
        assert "编号 A-001" in item["excerpt"]
        assert "原文：未经核验" in item["excerpt"]
        if uncertainty:
            assert "编号末位模糊" in item["excerpt"]
    assert expanded["highlighted"] == expanded["excerpt"]
    assert expanded["binding_state"] == "verified"
    if warning == "image_vision_uncertain":
        assert inventory["coverage"]["documents"][0]["extraction_warning_code"] is None
    if header:
        inner_id = make_citation_id(version.id, source["text_sha256"], 3, len(body))
        inner = expand_citation_for_project(
            evidence_db, space_ids=["space_1"], document_ids=[document.id],
            citation_id=inner_id, before=0, after=0,
        )
        assert inner["highlighted"].startswith(" " * (len(header) - 3))
        assert inner["char_start"] == 3
    denied = expand_citation_for_project(
        evidence_db, space_ids=["private"], document_ids=None, citation_id=source["id"],
    )
    assert denied is None


def test_citation_verification_compares_full_hash_identity():
    original = make_citation_id("ver_1", "a" * 64, 0, 100)
    changed = make_citation_id("ver_1", "b" * 64, 0, 100)
    assert split_citation_span(original) == split_citation_span(changed) == ("ver_1", 0, 100)
    assert verified_citation_ids([f"正文 ^[{changed}]"], [original]) == set()
    assert verified_citation_ids([f"正文 [citation:{original}]"], [original]) == {original}
    assert verified_citation_ids(["[writing-evidence:ver_1:0-0]"], ["writing-evidence:ver_1:0-0"]) == set()


def test_evidence_adapter_preserves_complete_opaque_identity():
    citation_id = make_citation_id("v" * 64, "a" * 64, 10, 100)
    item = {"id": citation_id, "document_id": "doc_1", "document_version_id": "v" * 64}
    assert build_evidence_location(item).citation_id == citation_id
    with pytest.raises(ValidationError):
        build_evidence_location({**item, "id": "a" * 513})


@pytest.mark.parametrize(("query", "expected"), [
    ("请完整解释这一条合规要求", False),
    ("列出全部软件著作权证书及登记号", True),
    ("List all certificates and expiry dates", True),
    ("What does this certificate say?", False),
])
def test_inventory_intent_requires_enumeration(query, expected):
    assert is_inventory_request(query) is expected


def test_relevance_search_is_document_diverse_before_second_chunks(monkeypatch):
    first = SimpleNamespace(id="doc_a", name="合规文档")
    second = SimpleNamespace(id="doc_b", name="补充合规文档")
    versions = [
        SimpleNamespace(id="ver_a", version_number=1),
        SimpleNamespace(id="ver_b", version_number=1),
    ]
    body = ("合规要求与产品能力。" * 100 + "\n\n") * 6
    monkeypatch.setattr(
        "app.services.writing_evidence.retrieve_for_qa",
        lambda *args, **kwargs: RetrievalBundle(documents=cast(
            Any, [(first, versions[0]), (second, versions[1])]
        )),
    )
    monkeypatch.setattr(
        "app.services.writing_evidence._resolve_version_text", lambda _version: body
    )
    result = search(
        cast(Any, None), space_ids=["space_1"], chapter_id="",
        query="合规要求", purpose="document", top_k=2,
    )
    assert {item["document_id"] for item in result["evidence"]} == {"doc_a", "doc_b"}
    assert result["retrieval"]["exhaustive"] is False


def test_inventory_continuation_finishes_original_snapshot_without_version_substitution(
    evidence_db, tmp_path,
):
    original_a = "甲" * 9_000 + "甲证书结束"
    original_b = "乙" * 7_000 + "乙证书结束"
    document_a, version_a = _seed_document(evidence_db, tmp_path, "a", original_a)
    document_b, version_b = _seed_document(evidence_db, tmp_path, "b", original_b)
    first = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None,
        query="全部证书", max_chars=4_000,
    )
    assert first["coverage"]["processed"] == 0
    assert first["coverage"]["truncated"] == 1
    assert first["coverage"]["unprocessed"] == 1
    first_cursor = first["next_cursor"]
    assert first_cursor is not None
    Path(version_a.file_path).write_text("后来重新提取的甲", encoding="utf-8")
    Path(version_b.file_path).write_text("后来重新提取的乙", encoding="utf-8")
    version_a.is_current_published = False
    evidence_db.flush()
    evidence_db.add(DocumentVersion(
        id="ver_a_new", document_id=document_a.id, version_number=2,
        processing_stage="ready", queryable=True, is_current_published=True,
        created_at=version_a.created_at, updated_at=version_a.updated_at,
    ))
    _seed_document(evidence_db, tmp_path, "later", "不得插入旧清单的新文档")
    evidence_db.flush()
    pages = [first]
    current_cursor = first_cursor
    for _ in range(10):
        page = inventory_evidence(
            evidence_db, space_ids=["space_1"], document_ids=None,
            query="全部证书", cursor=current_cursor, max_chars=4_000,
        )
        pages.append(page)
        current_cursor = page["next_cursor"]
        if current_cursor is None:
            break
    assert current_cursor is None
    final_coverage = pages[-1]["coverage"]
    assert final_coverage["processed"] == final_coverage["expected"] == 2
    assert final_coverage["complete"] is True
    all_evidence = [item for page in pages for item in page["evidence"]]
    assert {item["document_version_id"] for item in all_evidence} == {version_a.id, version_b.id}
    for document, original in ((document_a, original_a), (document_b, original_b)):
        spans = [item for item in all_evidence if item["document_id"] == document.id]
        assert "".join(item["excerpt"] for item in spans) == original
        hashes = {item["text_sha256"] for item in spans}
        assert len(hashes) == 1
        assert read_snapshot(spans[0]["document_version_id"], hashes.pop()) == original
    replay = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None,
        query="全部证书", cursor=first_cursor, max_chars=4_000,
    )
    assert [item["id"] for item in replay["evidence"]] == [
        item["id"] for item in pages[1]["evidence"]
    ]
    skipped_middle = merge_inventory_pages(first, pages[-1])
    assert skipped_middle["coverage"]["complete"] is False
    assert skipped_middle["coverage"]["processed"] == 0
    assert skipped_middle["next_cursor"] == first_cursor
    assert any(row["unprocessed_ranges"] for row in skipped_middle["coverage"]["documents"])
    received = None
    for page in pages:
        received = merge_inventory_pages(received, page)
    assert received["coverage"]["processed"] == 2
    assert received["coverage"]["complete"] is True
    assert received["next_cursor"] is None


def test_inventory_continuation_fails_closed_on_changed_scope(evidence_db, tmp_path):
    document, _version = _seed_document(evidence_db, tmp_path, "a", "甲" * 9_000)
    first = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None,
        query="全部证书", max_chars=4_000,
    )
    with pytest.raises(ValueError):
        inventory_evidence(
            evidence_db, space_ids=["space_other"], document_ids=None,
            query="全部证书", cursor=first["next_cursor"], max_chars=4_000,
        )
    document.space_id = "space_other"
    evidence_db.flush()
    with pytest.raises(ValueError):
        inventory_evidence(
            evidence_db, space_ids=["space_1"], document_ids=None,
            query="全部证书", cursor=first["next_cursor"], max_chars=4_000,
        )


def test_inventory_byte_admission_keeps_omitted_documents_pending(evidence_db, tmp_path):
    for index in range(80):
        _seed_document(
            evidence_db, tmp_path, f"{index:03d}",
            f"软件名称：软件{index:03d}\n著作权人：示例公司\n登记号：2026SR{index:06d}",
        )
    first = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None,
        query="全部证书", max_bytes=12_000,
    )
    assert len(json.dumps(first, ensure_ascii=False).encode("utf-8")) <= 12_000
    first_ids = {item["document_id"] for item in first["evidence"]}
    assert 0 < len(first_ids) < 80
    assert first["coverage"]["processed"] == len(first_ids)
    assert first["coverage"]["unprocessed"] == 80 - len(first_ids)
    assert {row["document_id"] for row in first["coverage"]["documents"]} == first_ids
    assert first["coverage"]["unreported_document_count"] == 80 - len(first_ids)
    second = inventory_evidence(
        evidence_db, space_ids=["space_1"], document_ids=None,
        query="全部证书", max_bytes=12_000, cursor=first["next_cursor"],
    )
    second_ids = {item["document_id"] for item in second["evidence"]}
    assert first_ids.isdisjoint(second_ids)
    assert second["coverage"]["processed"] == len(first_ids | second_ids)
    assert len(json.dumps(second, ensure_ascii=False).encode("utf-8")) <= 12_000
    assert second["coverage"]["complete"] is False
    for page in (first, second):
        admitted_ids = {item["id"] for item in page["evidence"]}
        assert {record["document_id"] for record in page["inventory_records"]} == {
            item["document_id"] for item in page["evidence"]
        }
        for record in page["inventory_records"]:
            index = int(record["document_id"].rsplit("_", 1)[1])
            assert record["identifier"] == f"2026SR{index:06d}"
            assert record["field_values"]["软件名称"] == [f"软件{index:03d}"]
            assert set(record["field_evidence"]["软件名称"]).issubset(admitted_ids)


def test_expansion_accepts_authorized_temporary_selection_and_rejects_expiry(
    evidence_db, tmp_path,
):
    document, _version = _seed_document(
        evidence_db, tmp_path, "temporary", "临时会话证书", space_id=None,
    )
    document.is_temporary = True
    document.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    evidence_db.flush()
    result = inventory_evidence(
        evidence_db, space_ids=[], document_ids=[document.id], query="证书清单",
    )
    citation_id = result["evidence"][0]["id"]
    expanded = expand_citation_for_project(
        evidence_db, space_ids=[], document_ids=[document.id], citation_id=citation_id,
    )
    assert expanded["highlighted"] == "临时会话证书"
    assert expanded["binding_state"] == "verified"
    assert expand_citation_for_project(
        evidence_db, space_ids=[], document_ids=None, citation_id=citation_id,
    ) is None
    document.expires_at = datetime(2020, 1, 1)
    evidence_db.flush()
    assert expand_citation_for_project(
        evidence_db, space_ids=[], document_ids=[document.id], citation_id=citation_id,
    ) is None
