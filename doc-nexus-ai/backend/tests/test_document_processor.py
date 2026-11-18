"""Unit tests for document processing helpers."""

from app.services.document_processor import derive_category, stable_knowledge_id


def test_stable_knowledge_id_is_positive_and_stable():
    a = stable_knowledge_id("doc_abc")
    b = stable_knowledge_id("doc_abc")
    c = stable_knowledge_id("doc_xyz")
    assert a == b
    assert a > 0
    assert a != c


def test_derive_category_from_space():
    assert derive_category("space-1.2") == "docnexus_space_1_2"
