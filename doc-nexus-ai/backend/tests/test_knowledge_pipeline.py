"""Tests for the local knowledge pipeline (chunking + text extraction)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import knowledge_pipeline as kp
from app.services.knowledge_pipeline import (
    _clean_chunk,
    _clean_text,
    _split_chunks,
    _split_chunks_with_spans,
)


def test_split_chunks_respects_max_chars():
    # Each paragraph ~10 chars, max_chars=12 → multiple chunks
    text = "段落一内容填充。\n\n段落二内容填充。\n\n段落三内容填充。"
    chunks = _split_chunks(text, max_chars=12, overlap=0)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 12 + 8  # paragraph boundary tolerance


def test_split_chunks_applies_overlap():
    text = "A" * 50 + "\n\n" + "B" * 50
    chunks = _split_chunks(text, max_chars=40, overlap=10)
    assert len(chunks) >= 2
    # Second chunk should contain tail of first
    assert "A" * 10 in chunks[1]


def test_split_chunks_empty_text():
    assert _split_chunks("") == []
    assert _split_chunks("   \n\n  ") == []


def test_split_chunks_with_spans_maps_pages_and_bounds_block_metadata():
    text = "第一页中的编号 REC-00046\n\n第二页中的编号 REC-00047"
    spans = [
        {"page_number": 1, "char_start": 0, "char_end": 23},
        {"page_number": 2, "char_start": 25, "char_end": len(text)},
    ]
    blocks = [
        {"id": f"block-{i}", "char_start": 0, "char_end": len(text)}
        for i in range(40)
    ]
    records = _split_chunks_with_spans(
        text, page_spans=spans, page_blocks=blocks, max_chars=30, overlap=5
    )
    assert records
    assert records[0].char_start >= 0
    assert records[0].char_end <= len(text)
    assert records[0].page_start == 1
    assert len(records[0].page_block_ids) <= 32


def test_split_chunks_single_paragraph():
    text = "这是一段短文本。"
    chunks = _split_chunks(text, max_chars=100, overlap=0)
    assert chunks == [text]


def test_split_chunks_oversized_paragraph_with_sentences():
    # Paragraph with sentence punctuation → splits on sentences
    text = "第一句。" * 100
    chunks = _split_chunks(text, max_chars=50, overlap=0)
    assert len(chunks) >= 10
    for chunk in chunks:
        assert len(chunk) <= 50 + 10  # sentence boundary tolerance


def test_split_chunks_merges_small_paragraphs():
    text = "短\n\n短\n\n短\n\n短"
    chunks = _split_chunks(text, max_chars=100, overlap=0)
    # All fit in one chunk
    assert len(chunks) == 1
    assert "短" in chunks[0]


def test_clean_text_removes_control_chars():
    text = "hello\x00world\x01test"
    assert _clean_text(text) == "helloworldtest"


def test_clean_text_normalizes_newlines():
    text = "a\n\n\n\n\nb"
    assert _clean_text(text) == "a\n\nb"


def test_clean_chunk_collapses_whitespace():
    assert _clean_chunk("hello   world\n\ntest") == "hello world test"


# ---------------------------------------------------------------------------
# Retry policy for chunk embedding
# ---------------------------------------------------------------------------


class _StubStore:
    """Minimal PgVectorStore stand-in for retry tests."""

    def __init__(
        self,
        behaviors: list[Exception | str] | dict[int, list[Exception | str]],
    ) -> None:
        # behaviors can be:
        #   - a flat list: each chunk consumes the next outcome in order
        #   - a dict {chunk_index: [outcomes]}: each chunk consumes from its own list
        self._behaviors = behaviors
        self._cursor = 0
        self.delete_calls: list[dict[str, Any]] = []
        self.stored_ids: set[str] = {"old-vector"}
        self.calls: list[dict[str, Any]] = []

    def delete_by_file(
        self,
        category: str,
        file_id: str,
        *,
        strict: bool = False,
        only_ids: list[str] | None = None,
        exclude_ids: list[str] | None = None,
    ) -> int:
        self.delete_calls.append(
            {
                "category": category,
                "file_id": file_id,
                "strict": strict,
                "only_ids": only_ids,
                "exclude_ids": exclude_ids,
            }
        )
        if only_ids is not None:
            self.stored_ids.difference_update(only_ids)
        elif exclude_ids:
            self.stored_ids.intersection_update(exclude_ids)
        else:
            self.stored_ids.clear()
        return 0

    def chunk_add(
        self,
        category: str,
        *,
        file_id: str,
        document: str,
        vector_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            {
                "file_id": file_id,
                "document": document,
                "vector_id": vector_id,
                "metadata": metadata,
            }
        )
        chunk_idx = (metadata or {}).get("chunk_index", -1)
        if isinstance(self._behaviors, dict):
            outcome = self._behaviors[chunk_idx].pop(0)
        else:
            outcome = self._behaviors[self._cursor]
            self._cursor += 1
        if isinstance(outcome, Exception):
            raise outcome
        self.stored_ids.add(str(outcome))
        return outcome


def _multi_chunk_text(n: int = 4) -> str:
    """Generate text that splits into exactly n chunks under default settings."""
    # Each paragraph ~1750 chars (just under the 1800-char max), separated by
    # blank lines. With _CHUNK_MAX_CHARS=1800 and _CHUNK_OVERLAP_CHARS=200,
    # each paragraph becomes its own chunk.
    paragraph = ("段落填充内容。" * 250).strip()
    return "\n\n".join([paragraph] * n)


def test_transient_http_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConnectError is transient; the chunk should be retried."""
    monkeypatch.setattr(kp, "time", MagicMock())
    store = _StubStore(
        [
            httpx.ConnectError("network blip"),
            "ok-vector-id",
        ]
    )
    monkeypatch.setattr(kp, "PgVectorStore", lambda: store)
    result = kp.process_document(
        text=_multi_chunk_text(1),
        category="test-cat",
        file_id="file-1",
    )
    assert result["chunk_count"] == 1
    assert result["vector_ids"] == ["ok-vector-id"]
    assert len(store.calls) == 2
    assert store.delete_calls[-1]["exclude_ids"] == ["ok-vector-id"]
    assert store.stored_ids == {"ok-vector-id"}


def test_permanent_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """ValueError is permanent; chunk fails on first attempt and document fails."""
    monkeypatch.setattr(kp, "time", MagicMock())
    store = _StubStore(
        [
            ValueError("embedding 维度不匹配"),
        ]
    )
    monkeypatch.setattr(kp, "PgVectorStore", lambda: store)
    with pytest.raises(RuntimeError, match="chunks failed after"):
        kp.process_document(text=_multi_chunk_text(1), category="cat", file_id="file-2")
    assert len(store.calls) == 1
    assert store.delete_calls == []
    assert "old-vector" in store.stored_ids


def test_exhausted_retries_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """All transient attempts fail; chunk still surfaces as an error after retries."""
    sleep_mock = MagicMock()
    monkeypatch.setattr(kp, "time", MagicMock(sleep=sleep_mock))
    store = _StubStore(
        [
            httpx.ConnectError("blip 1"),
            httpx.ReadTimeout("blip 2"),
            httpx.ConnectError("blip 3"),
        ]
    )
    monkeypatch.setattr(kp, "PgVectorStore", lambda: store)
    with pytest.raises(RuntimeError, match="1/1 chunks failed after 3 attempts"):
        kp.process_document(text=_multi_chunk_text(1), category="cat", file_id="file-3")
    assert len(store.calls) == kp._RETRY_MAX_ATTEMPTS
    # Backoff called once between attempts (between first→second, second→third)
    assert sleep_mock.call_count == kp._RETRY_MAX_ATTEMPTS - 1
    assert store.delete_calls == []
    assert store.stored_ids == {"old-vector"}


def test_partial_failure_marks_document_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If one chunk in many fails, the document is marked failed (strict policy)."""
    monkeypatch.setattr(kp, "time", MagicMock())
    # 4 chunks; chunk #1 fails 3 times then gives up; others succeed on first try.
    store = _StubStore(
        {
            0: ["ok-1"],
            1: [
                httpx.ConnectError("500-1"),
                httpx.ReadTimeout("500-2"),
                httpx.ConnectError("500-3"),
            ],
            2: ["ok-3"],
            3: ["ok-4"],
        }
    )
    monkeypatch.setattr(kp, "PgVectorStore", lambda: store)
    with pytest.raises(RuntimeError, match="1/4 chunks failed after"):
        kp.process_document(
            text=_multi_chunk_text(4),
            category="cat",
            file_id="file-4",
        )
    # 3 successful + 3 attempts for the failed one
    assert len(store.calls) == 6
    assert len(store.delete_calls) == 1
    assert set(store.delete_calls[0]["only_ids"]) == {"ok-1", "ok-3", "ok-4"}
    assert store.stored_ids == {"old-vector"}


def test_successful_first_attempt_does_not_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_mock = MagicMock()
    monkeypatch.setattr(kp, "time", MagicMock(sleep=sleep_mock))
    store = _StubStore(["ok-1", "ok-2", "ok-3", "ok-4"])
    monkeypatch.setattr(kp, "PgVectorStore", lambda: store)
    kp.process_document(text=_multi_chunk_text(4), category="cat", file_id="file-5")
    sleep_mock.assert_not_called()
    assert len(store.calls) == 4
    assert store.delete_calls[-1]["strict"] is True
    assert set(store.delete_calls[-1]["exclude_ids"]) == {
        "ok-1",
        "ok-2",
        "ok-3",
        "ok-4",
    }
    assert store.stored_ids == {"ok-1", "ok-2", "ok-3", "ok-4"}


def test_is_retryable_filters_correctly() -> None:
    """Only transient network/HTTP errors should be retried."""
    assert kp._is_retryable(httpx.ConnectError("x")) is True
    assert kp._is_retryable(httpx.ReadTimeout("x")) is True
    assert kp._is_retryable(TimeoutError("x")) is True
    assert kp._is_retryable(ValueError("bad dim")) == False  # noqa: E712
    assert kp._is_retryable(RuntimeError("bad data")) == False  # noqa: E712
    assert kp._is_retryable(KeyError("x")) == False  # noqa: E712
