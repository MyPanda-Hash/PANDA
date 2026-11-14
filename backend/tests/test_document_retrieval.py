"""Unit tests for lexical document ranking used by QA / chat knowledge retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.qa_service import rank_documents_for_query


def _doc(name: str, *, days_ago: int, doc_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=doc_id,
        name=name,
        updated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def _ver(summary: str = "") -> SimpleNamespace:
    return SimpleNamespace(summary=summary, file_path=None, ext_task_id=None)


def test_rank_prefers_query_relevant_document_over_recent_unrelated(monkeypatch):
    recent = (_doc("Ubuntu 安装手册", days_ago=0, doc_id="doc_recent"), _ver())
    relevant = (_doc("安全围栏功能说明", days_ago=30, doc_id="doc_relevant"), _ver())
    other = (_doc("敏感词功能说明", days_ago=1, doc_id="doc_other"), _ver())

    def fake_text(version):
        mapping = {
            id(recent[1]): "如何在 Ubuntu 上安装依赖与启动服务。",
            id(relevant[1]): "安全围栏用于拦截 Prompt 攻击，包含 PromptAttackIntentClassifier。",
            id(other[1]): "敏感词词库表 dm_sensitive_words 的维护说明。",
        }
        return mapping[id(version)]

    monkeypatch.setattr("app.services.qa_service._resolve_version_text", fake_text)

    ranked = rank_documents_for_query(
        "安全围栏怎么配置？",
        [recent, other, relevant],
        top_k=2,
    )
    assert [item[0].id for item in ranked] == ["doc_relevant"]
    assert ranked[0][2] > 0


def test_rank_can_return_multiple_relevant_docs(monkeypatch):
    docs = [
        (_doc("2D数字人部署文档", days_ago=2, doc_id="doc_a"), _ver()),
        (_doc("安卓安装手册", days_ago=1, doc_id="doc_b"), _ver()),
        (_doc("安全围栏功能说明", days_ago=3, doc_id="doc_c"), _ver()),
    ]

    def fake_text(version):
        mapping = {
            id(docs[0][1]): "数字人服务部署需要 git clone 与 sudo chown。",
            id(docs[1][1]): "安卓安装包下载与权限配置。",
            id(docs[2][1]): "安全围栏策略与 Prompt 攻击分类。",
        }
        return mapping[id(version)]

    monkeypatch.setattr("app.services.qa_service._resolve_version_text", fake_text)
    ranked = rank_documents_for_query("数字人部署与安全围栏", docs, top_k=8)
    ids = {item[0].id for item in ranked}
    assert ids == {"doc_a", "doc_c"}


def test_hybrid_graph_can_surface_lexically_weaker_document(monkeypatch):
    """Graph-linked older doc must enter top results even when lexical prefers another."""
    from types import SimpleNamespace

    from app.services.hybrid_retrieval import retrieve_for_qa

    lex_favored = (
        _doc("通用运维手册", days_ago=0, doc_id="doc_lex"),
        _ver(),
    )
    graph_favored = (
        _doc("旧版围栏设计", days_ago=40, doc_id="doc_graph"),
        _ver(),
    )
    # Give versions distinct ids for clarity
    lex_favored[1].id = "ver_lex"
    graph_favored[1].id = "ver_graph"
    pool = [lex_favored, graph_favored]

    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        lambda db, space_ids, limit=None, document_ids=None, attachment_document_ids=None: pool,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._lexical_scores",
        lambda question, candidates: {"doc_lex": 1.0, "doc_graph": 0.05},
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._graph_branch",
        lambda db, question, space_ids: (
            {"doc_graph": 1.0},
            [SimpleNamespace(entity_id="ent_1", name="安全围栏", score=1.0)],
            "关键实体：安全围栏",
            True,
            {"doc_graph"},
        ),
    )
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_graph_weight", 0.45)
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_retrieval_top_k", 2)

    bundle = retrieve_for_qa(db=None, question="围栏策略", space_ids=["space_1"], top_k=2)
    ids = [document.id for document, _version in bundle.documents]
    assert "doc_graph" in ids
    assert bundle.meta["retrieval_method"] == "hybrid_lexical_graph"
    assert bundle.meta["graph_entity_count"] == 1
    assert "doc_graph" in bundle.meta["graph_boosted_doc_ids"]
    assert "安全围栏" in bundle.enrichment_text


def test_hybrid_without_graph_degrades_to_lexical(monkeypatch):
    from app.services.hybrid_retrieval import retrieve_for_qa

    docs = [
        (_doc("安全围栏功能说明", days_ago=1, doc_id="doc_a"), _ver()),
        (_doc("Ubuntu 手册", days_ago=0, doc_id="doc_b"), _ver()),
    ]
    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        lambda db, space_ids, limit=None, document_ids=None, attachment_document_ids=None: docs,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._lexical_scores",
        lambda question, candidates: {"doc_a": 0.9, "doc_b": 0.1},
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._graph_branch",
        lambda db, question, space_ids: ({}, [], "", False, set()),
    )
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_graph_weight", 0.45)

    bundle = retrieve_for_qa(db=None, question="安全围栏", space_ids=["space_1"], top_k=1)
    assert [document.id for document, _ in bundle.documents] == ["doc_a"]
    assert bundle.meta["retrieval_method"] == "lexical_relevance"
    assert bundle.meta["graph_available"] is False


def test_hybrid_respects_document_ids_filter(monkeypatch):
    from app.services.hybrid_retrieval import retrieve_for_qa

    docs = [
        (_doc("安全围栏功能说明", days_ago=1, doc_id="doc_a"), _ver()),
        (_doc("围栏运维手册", days_ago=0, doc_id="doc_b"), _ver()),
    ]
    captured: dict = {}

    def fake_ready(db, space_ids, limit=None, document_ids=None, attachment_document_ids=None):
        captured["document_ids"] = document_ids
        allowed = set(document_ids or [])
        return [item for item in docs if item[0].id in allowed]

    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        fake_ready,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._lexical_scores",
        lambda question, candidates: {document.id: 1.0 for document, _ in candidates},
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._graph_branch",
        lambda db, question, space_ids: (
            {"doc_a": 0.8, "doc_b": 1.0},
            [],
            "",
            False,
            {"doc_a", "doc_b"},
        ),
    )
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_graph_weight", 0.45)

    bundle = retrieve_for_qa(
        db=None,
        question="围栏",
        space_ids=["space_1"],
        top_k=5,
        document_ids=["doc_a"],
    )
    assert captured["document_ids"] == ["doc_a"]
    assert [document.id for document, _ in bundle.documents] == ["doc_a"]
    assert bundle.meta["graph_boosted_doc_ids"] == ["doc_a"]



def test_adaptive_exact_query_skips_graph_and_vector(monkeypatch):
    from app.services.hybrid_retrieval import retrieve_for_qa

    docs = [
        (_doc("REC-00046 说明", days_ago=1, doc_id="doc_a"), _ver()),
    ]
    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        lambda db, space_ids, limit=None, document_ids=None, attachment_document_ids=None: docs,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._lexical_scores",
        lambda question, candidates: {"doc_a": 2.0},
    )

    def fail_graph(*args, **kwargs):
        raise AssertionError("exact adaptive query should not invoke graph")

    def fail_vector(*args, **kwargs):
        raise AssertionError("exact adaptive query should not invoke vector")

    monkeypatch.setattr("app.services.hybrid_retrieval._graph_branch", fail_graph)
    monkeypatch.setattr("app.services.hybrid_retrieval._vector_branch", fail_vector)

    bundle = retrieve_for_qa(
        db=None,
        question="REC-00046 是什么？",
        space_ids=["space_1"],
        mode="auto",
        top_k=1,
    )

    assert [document.id for document, _ in bundle.documents] == ["doc_a"]
    assert bundle.meta["retrieval_mode"] == "exact"
    assert bundle.meta["vector_used"] is False


def test_adaptive_low_confidence_query_uses_vector_fallback(monkeypatch):
    from app.services.hybrid_retrieval import retrieve_for_qa

    docs = [
        (_doc("通用运维手册", days_ago=1, doc_id="doc_a"), _ver()),
        (_doc("围栏策略说明", days_ago=2, doc_id="doc_b"), _ver()),
    ]
    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        lambda db, space_ids, limit=None, document_ids=None, attachment_document_ids=None: docs,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._lexical_scores",
        lambda question, candidates: {"doc_a": 0.2, "doc_b": 0.1},
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._vector_branch",
        lambda question, space_ids, pool: (
            {"doc_b": 0.95},
            [
                {
                    "id": "vec_1",
                    "document_id": "doc_b",
                    "document_version_id": "ver_b",
                    "document": "围栏策略说明",
                    "score": 0.95,
                }
            ],
        ),
    )
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_vector_enabled", True)

    bundle = retrieve_for_qa(
        db=None,
        question="如何配置安全边界？",
        space_ids=["space_1"],
        mode="auto",
        top_k=1,
    )

    assert [document.id for document, _ in bundle.documents] == ["doc_b"]
    assert bundle.meta["retrieval_method"] == "adaptive_lexical_vector"
    assert bundle.meta["vector_used"] is True


def test_vector_branch_limits_search_to_authorized_versions(monkeypatch):
    from app.services.hybrid_retrieval import _vector_branch

    document = _doc("安全围栏", days_ago=1, doc_id="doc_vector_scope")
    document.space_id = "space_1"
    version = _ver()
    version.id = "ver_vector_scope"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.document_processor.derive_category",
        lambda _space_id: "space_category",
    )
    monkeypatch.setattr(
        "app.services.document_processor.stable_knowledge_id",
        lambda _version_id: 4242,
    )

    def fake_search(self, *, query, scopes, top_k):
        captured.update(query=query, scopes=scopes, top_k=top_k)
        return [
            {
                "id": "vec_scope_1",
                "category": "space_category",
                "file_id": "4242",
                "document": "安全围栏片段",
                "metadata": {"chunk_index": 0},
                "score": 0.88,
            }
        ]

    monkeypatch.setattr("app.services.pgvector_store.PgVectorStore.search", fake_search)
    monkeypatch.setattr("app.services.hybrid_retrieval.settings.qa_vector_enabled", True)

    scores, hits = _vector_branch("如何配置围栏？", ["space_1"], [(document, version)])

    assert captured == {
        "query": "如何配置围栏？",
        "scopes": [("space_category", ["4242"])],
        "top_k": 30,
    }
    assert scores == {"doc_vector_scope": 0.88}
    assert hits[0]["document_version_id"] == "ver_vector_scope"



def test_recommend_keeps_title_match_when_fused_score_is_rrf_scale():
    from app.services.hybrid_retrieval import select_recommended_documents
    from app.services.qa_service import _lexical_relevance_score

    goal = "写一篇关于缔智元的介绍"
    matched_lex = _lexical_relevance_score(
        goal,
        title="缔智元公司介绍-20260731072732",
        body="",
        summary="",
    )
    unrelated_lex = _lexical_relevance_score(
        goal,
        title="安全检查规定",
        body="",
        summary="",
    )
    assert matched_lex > 0
    assert matched_lex > unrelated_lex

    selected = select_recommended_documents(
        [
            {
                "document_id": "doc_match",
                "score": 0.0164,
                "lex_score": matched_lex,
                "graph_score": 0.0,
                "vector_score": 0.0,
            },
            {
                "document_id": "doc_unrelated",
                "score": 0.0161,
                "lex_score": unrelated_lex,
                "graph_score": 0.0,
                "vector_score": 0.0,
            },
            {
                "document_id": "doc_fallback",
                "score": 0.0,
                "lex_score": 0.0,
                "graph_score": 0.0,
                "vector_score": 0.0,
            },
        ],
        threshold=0.3,
    )
    assert [row["document_id"] for row in selected] == ["doc_match"]


def test_empty_document_ids_are_an_empty_scope_not_all_documents(monkeypatch):
    from app.services.hybrid_retrieval import retrieve_for_qa

    def fake_ready(_db, _space_ids, *, limit=None, document_ids=None, attachment_document_ids=None):
        if document_ids is not None and not document_ids:
            return []
        return [(_doc("缔智元公司介绍", days_ago=1, doc_id="doc_match"), _ver())]

    monkeypatch.setattr(
        "app.services.hybrid_retrieval.qa_repo.ready_documents_for_scope",
        fake_ready,
    )
    monkeypatch.setattr(
        "app.services.qa_service._resolve_version_text",
        lambda _version: "北京缔智元科技有限公司",
    )
    empty = retrieve_for_qa(
        db=None,
        question="写一篇关于缔智元的介绍",
        space_ids=["space_1"],
        document_ids=[],
        top_k=8,
        purpose="writing",
        mode="auto",
    )
    assert empty.meta["pool_size"] == 0
    assert empty.scores == []

    scoped = retrieve_for_qa(
        db=None,
        question="写一篇关于缔智元的介绍",
        space_ids=["space_1"],
        document_ids=None,
        top_k=8,
        purpose="writing",
        mode="auto",
    )
    assert scoped.meta["pool_size"] == 1
    assert scoped.scores[0]["document_id"] == "doc_match"


def test_recommend_ignores_empty_score_rows_alias():
    from app.services.hybrid_retrieval import select_recommended_documents

    assert select_recommended_documents([], threshold=0.3) == []
    assert (
        select_recommended_documents(
            [
                {
                    "document_id": "doc_a",
                    "score": 0.016,
                    "lex_score": 0.0,
                    "graph_score": 0.0,
                    "vector_score": 0.0,
                }
            ],
            threshold=0.3,
        )
        == []
    )