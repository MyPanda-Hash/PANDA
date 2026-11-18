from app.services.retrieval_router import (
    RetrievalMode,
    is_non_retrieval_request,
    plan_retrieval,
    should_escalate_to_vector,
)


def test_pure_chat_does_not_request_retrieval():
    assert is_non_retrieval_request("你好") is True
    assert plan_retrieval("你好").mode == RetrievalMode.NONE


def test_factual_question_with_exact_identifier_stays_lexical():
    plan = plan_retrieval("REC-00046 是什么？")
    assert plan.mode == RetrievalMode.EXACT
    assert plan.use_lexical is True
    assert plan.use_vector is False
    assert plan.use_graph is False


def test_relation_question_uses_graph_before_vector_fallback():
    plan = plan_retrieval("公司和清华团队是什么关系？")
    assert plan.mode == RetrievalMode.RELATION
    assert plan.use_graph is True
    assert plan.vector_fallback is True


def test_ambiguous_lexical_results_escalate_to_vector():
    assert should_escalate_to_vector(
        {"doc_a": 0.4, "doc_b": 0.35},
        minimum_score=0.8,
        minimum_margin=0.15,
    ) is True
    assert should_escalate_to_vector(
        {"doc_a": 2.0, "doc_b": 0.1},
        minimum_score=0.8,
        minimum_margin=0.15,
    ) is False


def test_rewrite_purpose_can_disable_retrieval():
    plan = plan_retrieval("把这段话改得更正式", purpose="rewrite_text")
    assert plan.mode == RetrievalMode.NONE
