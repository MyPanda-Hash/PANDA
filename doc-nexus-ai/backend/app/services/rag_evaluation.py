"""Deterministic, privacy-safe RAG evaluation helpers.

The evaluator operates on stable source references and metadata only. It never
needs the question, answer, or source body after a case has been validated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable

KNOWN_TAGS = frozenset(
    {
        "exact",
        "semantic",
        "cross-doc",
        "no-answer",
        "conflict",
        "permission",
        "scan",
        "table",
        "date",
        "relation",
    }
)
ANSWERABILITY = frozenset({"answerable", "no-answer"})


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset would make a run ambiguous."""


def _as_refs(value: object, *, field: str, case_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise DatasetValidationError(f"{case_id}: {field} must be a list of non-empty strings")
    refs = [item.strip() for item in value]
    if len(set(refs)) != len(refs):
        raise DatasetValidationError(f"{case_id}: {field} contains duplicate refs")
    return refs


def validate_dataset(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize dataset records without reading any corpus text."""

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise DatasetValidationError("each dataset record must be an object")
        case_id = str(raw.get("id") or "").strip()
        if not case_id:
            raise DatasetValidationError("case id is required")
        if case_id in seen:
            raise DatasetValidationError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise DatasetValidationError(f"{case_id}: question is required")
        answerability = raw.get("answerability")
        if answerability not in ANSWERABILITY:
            raise DatasetValidationError(f"{case_id}: unknown answerability")
        scope_refs = _as_refs(raw.get("scope_refs"), field="scope_refs", case_id=case_id)
        if not scope_refs:
            raise DatasetValidationError(f"{case_id}: scope_refs is required")
        expected = _as_refs(
            raw.get("expected_source_refs"), field="expected_source_refs", case_id=case_id
        )
        forbidden = _as_refs(
            raw.get("forbidden_source_refs"), field="forbidden_source_refs", case_id=case_id
        )
        if set(expected) & set(forbidden):
            raise DatasetValidationError(f"{case_id}: expected/forbidden refs overlap")
        if answerability == "answerable" and not expected:
            raise DatasetValidationError(f"{case_id}: answerable case needs expected_source_refs")
        if answerability == "no-answer" and expected:
            raise DatasetValidationError(f"{case_id}: no-answer case cannot have expected_source_refs")
        tags = _as_refs(raw.get("tags"), field="tags", case_id=case_id)
        unknown_tags = set(tags) - KNOWN_TAGS
        if unknown_tags:
            raise DatasetValidationError(f"{case_id}: unknown tag {sorted(unknown_tags)[0]}")
        required_facts = raw.get("required_facts") or []
        if not isinstance(required_facts, list):
            raise DatasetValidationError(f"{case_id}: required_facts must be a list")
        for fact in required_facts:
            if not isinstance(fact, dict) or not str(fact.get("id") or "").strip():
                raise DatasetValidationError(f"{case_id}: required_facts entries need id")
            acceptable = _as_refs(
                fact.get("acceptable_source_refs"),
                field="required_facts.acceptable_source_refs",
                case_id=case_id,
            )
            if answerability == "answerable" and not acceptable:
                raise DatasetValidationError(f"{case_id}: required fact needs acceptable refs")
            if not set(acceptable).issubset(set(expected)):
                raise DatasetValidationError(f"{case_id}: required fact ref is not expected")
        user_ref = raw.get("permission_user_ref")
        if not isinstance(user_ref, str) or not user_ref.strip():
            raise DatasetValidationError(f"{case_id}: permission_user_ref is required")
        normalized.append(
            {
                **raw,
                "id": case_id,
                "question": question.strip(),
                "scope_refs": scope_refs,
                "expected_source_refs": expected,
                "forbidden_source_refs": forbidden,
                "answerability": answerability,
                "required_facts": required_facts,
                "tags": tags,
                "permission_user_ref": user_ref.strip(),
            }
        )
    if not normalized:
        raise DatasetValidationError("dataset must contain at least one case")
    return normalized


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(f"line {line_number}: invalid JSON") from exc
        records.append(item)
    return validate_dataset(records)


def _candidate_refs(case: dict[str, Any]) -> list[str]:
    for key in ("ranked_source_refs", "returned_source_refs", "candidates"):
        value = case.get(key)
        if isinstance(value, list):
            refs: list[str] = []
            for item in value:
                if isinstance(item, str):
                    refs.append(item)
                elif isinstance(item, dict):
                    ref = item.get("source_ref") or item.get("ref") or item.get("id")
                    if isinstance(ref, str):
                        refs.append(ref)
            return refs
    return []


def _dcg(relevances: list[int]) -> float:
    import math

    return sum(value / math.log2(index + 2) for index, value in enumerate(relevances))


def calculate_retrieval_metrics(cases: Iterable[dict[str, Any]], *, k: int = 10) -> dict[str, Any]:
    """Calculate source-reference metrics from deterministic case results."""

    if k <= 0:
        raise ValueError("k must be positive")
    rows = list(cases)
    answerable = [row for row in rows if row.get("answerability") == "answerable"]
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    leakage = 0
    false_positive = 0
    for row in rows:
        candidates = _candidate_refs(row)[:k]
        expected = set(row.get("expected_source_refs") or [])
        forbidden = set(row.get("forbidden_source_refs") or [])
        leakage += len(set(candidates) & forbidden)
        authorized = row.get("authorized_source_refs")
        if isinstance(authorized, list):
            leakage += len(set(candidates) - set(authorized))
        if row.get("answerability") == "no-answer":
            if candidates:
                false_positive += 1
            continue
        if not expected:
            continue
        hit_count = len(set(candidates) & expected)
        recalls.append(hit_count / len(expected))
        reciprocal = 0.0
        for index, ref in enumerate(candidates, 1):
            if ref in expected:
                reciprocal = 1 / index
                break
        mrrs.append(reciprocal)
        graded = [int(row.get("relevance", {}).get(ref, 1)) if isinstance(row.get("relevance"), dict) else (1 if ref in expected else 0) for ref in candidates]
        ideal = sorted([int(value) for value in (row.get("relevance") or {}).values()], reverse=True)[:k] if isinstance(row.get("relevance"), dict) else [1] * min(k, len(expected))
        ndcgs.append(_dcg(graded) / _dcg(ideal) if _dcg(ideal) else 0.0)
    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0

    latencies = sorted(
        float(row.get("latency_ms")) for row in rows if isinstance(row.get("latency_ms"), (int, float))
    )
    p95 = 0.0
    if latencies:
        p95 = latencies[min(len(latencies) - 1, max(0, int(len(latencies) * 0.95) - 1))]
    return {
        f"recall_at_{k}": _avg(recalls),
        f"mrr_at_{k}": _avg(mrrs),
        f"ndcg_at_{k}": _avg(ndcgs),
        "permission_leakage": leakage,
        "no_answer_false_positive": round(false_positive / max(1, sum(row.get("answerability") == "no-answer" for row in rows)), 6),
        "case_count": len(rows),
        "answerable_case_count": len(answerable),
        "p95_latency_ms": p95,
    }


def compare_modes(
    legacy: dict[str, Any],
    auto: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Apply the release gate without changing production routing."""

    limits = {
        "min_recall_at_10": 0.85,
        "min_citation_resolvable_rate": 1.0,
        "min_fact_coverage": 0.95,
        "min_support_accuracy": 0.90,
        "max_no_answer_false_positive": 0.05,
    }
    if thresholds:
        limits.update(thresholds)
    reasons: list[str] = []
    auto_recall = float(auto.get("recall_at_10") or 0)
    legacy_recall = float(legacy.get("recall_at_10") or 0)
    auto_mrr = float(auto.get("mrr_at_10") or 0)
    legacy_mrr = float(legacy.get("mrr_at_10") or 0)
    if auto_recall < limits["min_recall_at_10"]:
        reasons.append("auto_recall_below_threshold")
    if auto_recall < legacy_recall:
        reasons.append("auto_recall_below_legacy")
    if auto_mrr < legacy_mrr:
        reasons.append("auto_mrr_below_legacy")
    if int(auto.get("permission_leakage") or 0) > 0:
        reasons.append("permission_leakage")
    if float(auto.get("citation_resolvable_rate", 1.0)) < limits["min_citation_resolvable_rate"]:
        reasons.append("citation_resolution_below_threshold")
    if float(auto.get("fact_coverage", 1.0)) < limits["min_fact_coverage"]:
        reasons.append("fact_coverage_below_threshold")
    if float(auto.get("support_accuracy", 1.0)) < limits["min_support_accuracy"]:
        reasons.append("support_accuracy_below_threshold")
    if float(auto.get("no_answer_false_positive") or 0) > limits["max_no_answer_false_positive"]:
        reasons.append("no_answer_false_positive_above_threshold")
    return {
        "release_decision": "eligible" if not reasons else "blocked",
        "reasons": reasons,
        "thresholds": limits,
    }


def hash_response(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def evaluate_modes(
    cases: Iterable[dict[str, Any]],
    retrieve: Callable[[dict[str, Any], str], dict[str, Any]],
) -> dict[str, Any]:
    """Run legacy/auto through a supplied retrieval adapter.

    The callback returns metadata such as ``ranked_source_refs`` and may add
    latency or a safe error. Its body is never persisted by this helper.
    """

    validated = validate_dataset(cases)
    reports: dict[str, list[dict[str, Any]]] = {"legacy": [], "auto": []}
    for mode in reports:
        for case in validated:
            result = retrieve(case, mode)
            reports[mode].append({**case, **{key: value for key, value in result.items() if key not in {"question", "answer", "excerpt", "content"}}})
    metrics = {mode: calculate_retrieval_metrics(rows) for mode, rows in reports.items()}
    decision = compare_modes(metrics["legacy"], metrics["auto"])
    return {"metrics": metrics, "release_decision": decision, "cases": reports}
