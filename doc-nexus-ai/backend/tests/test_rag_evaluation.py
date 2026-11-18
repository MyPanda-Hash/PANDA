from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services.rag_evaluation import (
    DatasetValidationError,
    calculate_retrieval_metrics,
    compare_modes,
    hash_response,
    validate_dataset,
)


def _case(case_id: str = "case-1", **overrides: object) -> dict:
    value: dict[str, object] = {
        "id": case_id,
        "question": "合成问题",
        "scope_refs": ["space:demo"],
        "expected_source_refs": ["doc:expected"],
        "forbidden_source_refs": ["doc:private"],
        "answerability": "answerable",
        "required_facts": [],
        "tags": ["exact"],
        "permission_user_ref": "user:evaluator",
        "ranked_source_refs": ["doc:expected"],
    }
    value.update(overrides)
    return value


def test_validate_dataset_rejects_invalid_answerability_and_duplicate_ids() -> None:
    with pytest.raises(DatasetValidationError, match="expected_source_refs"):
        validate_dataset([_case(answerability="no-answer", expected_source_refs=["doc:x"])])
    with pytest.raises(DatasetValidationError, match="duplicate"):
        validate_dataset([_case(), _case()])
    with pytest.raises(DatasetValidationError, match="expected_source_refs"):
        validate_dataset([_case(answerability="answerable", expected_source_refs=[])])
    with pytest.raises(DatasetValidationError, match="overlap"):
        validate_dataset([_case(forbidden_source_refs=["doc:expected"])])


def test_retrieval_metrics_are_deterministic() -> None:
    cases = [
        _case("hit", ranked_source_refs=["doc:other", "doc:expected"]),
        _case("miss", ranked_source_refs=["doc:other"]),
        _case(
            "none",
            answerability="no-answer",
            expected_source_refs=[],
            forbidden_source_refs=[],
            ranked_source_refs=[],
        ),
    ]
    metrics = calculate_retrieval_metrics(cases, k=10)
    assert metrics["recall_at_10"] == pytest.approx(0.5)
    assert metrics["mrr_at_10"] == pytest.approx(0.25)
    assert metrics["permission_leakage"] == 0
    assert metrics["no_answer_false_positive"] == pytest.approx(0.0)


def test_metrics_count_forbidden_and_no_answer_false_positive() -> None:
    metrics = calculate_retrieval_metrics(
        [
            _case("leak", ranked_source_refs=["doc:private"]),
            _case(
                "false-positive",
                answerability="no-answer",
                expected_source_refs=[],
                forbidden_source_refs=[],
                ranked_source_refs=["doc:hallucinated"],
            ),
        ],
        k=10,
    )
    assert metrics["permission_leakage"] == 1
    assert metrics["no_answer_false_positive"] == pytest.approx(1.0)


def test_compare_modes_blocks_regression_and_accepts_eligible_run() -> None:
    legacy = {"recall_at_10": 0.8, "mrr_at_10": 0.6, "permission_leakage": 0}
    auto = {"recall_at_10": 0.84, "mrr_at_10": 0.6, "permission_leakage": 0}
    blocked = compare_modes(legacy, auto)
    assert blocked["release_decision"] == "blocked"
    assert blocked["reasons"]

    eligible = compare_modes(
        {"recall_at_10": 0.8, "mrr_at_10": 0.6, "permission_leakage": 0},
        {"recall_at_10": 0.9, "mrr_at_10": 0.61, "permission_leakage": 0},
        thresholds={"min_recall_at_10": 0.85},
    )
    assert eligible["release_decision"] == "eligible"


def test_hash_response_is_stable_and_judgment_payload_has_no_body() -> None:
    assert hash_response("答案") == hash_response("答案")
    assert hash_response("答案").startswith("sha256:")
    assert "答案" not in json.dumps(
        {"response_hash": hash_response("答案"), "created_at": datetime.now(timezone.utc).isoformat()}
    )
