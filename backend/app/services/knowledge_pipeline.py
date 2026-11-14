"""Local knowledge pipeline: chunk → embed → store in pgvector or sqlite-vec.

Runs document ingestion locally inside the backend.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.services.pgvector_store import PgVectorStore

logger = logging.getLogger(__name__)

# DashScope text-embedding-v2 has a 2048-token limit per input.
# With CJK text, ~1 char ≈ 1 token; leave headroom.
_CHUNK_MAX_CHARS = 1800
_CHUNK_OVERLAP_CHARS = 200
_WRITE_CONCURRENCY = 5

# Per-chunk retry policy. Transient embedding-API failures are retried before
# the document is marked failed; permanent errors (dimension mismatch, schema
# violations, etc.) fail immediately and bubble up.
_RETRY_MAX_ATTEMPTS = 3  # 1 initial + 2 retries
_RETRY_BACKOFF_BASE_SECONDS = 1.0  # doubles each attempt (1s, 2s)
_MAX_PAGE_BLOCK_IDS = 32
_MAX_PAGE_METADATA_CHARS = 2048


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    text: str
    char_start: int
    char_end: int
    page_start: int | None = None
    page_end: int | None = None
    page_block_ids: tuple[str, ...] = ()


def _clean_text(text: str) -> str:
    """Normalize whitespace and remove control characters."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_retryable(exc: BaseException) -> bool:
    """Return True when a chunk failure may succeed on a subsequent attempt.

    Transient causes (network blips, embedding-API 5xx, restarts) are retried.
    Permanent causes (dimension mismatch, validation errors, schema violations)
    fail immediately so the document is not held up indefinitely.
    """
    if isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError)):
        return True
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _write_concurrency() -> int:
    if settings.database_url.startswith("sqlite"):
        return 1
    return _WRITE_CONCURRENCY


def _split_chunks(
    text: str, *, max_chars: int = _CHUNK_MAX_CHARS, overlap: int = _CHUNK_OVERLAP_CHARS
) -> list[str]:
    """Split text into overlapping chunks respecting a character budget.

    Strategy:
    1. Split on paragraph boundaries (double newline)
    2. If a paragraph exceeds max_chars, split on sentence boundaries
    3. Merge small consecutive paragraphs until max_chars
    4. Apply overlap from the tail of each chunk
    """
    if not text.strip():
        return []

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) > max_chars:
            # Flush current buffer
            if current.strip():
                chunks.append(current.strip())
                current = ""
            # Split oversized paragraph on sentences
            sentences = re.split(r"(?<=[。！？.!?\n])\s*", para)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(current) + len(sentence) + 1 > max_chars:
                    if current.strip():
                        chunks.append(current.strip())
                    current = sentence
                else:
                    current = f"{current}\n{sentence}" if current else sentence
        elif len(current) + len(para) + 2 > max_chars:
            if current.strip():
                chunks.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current.strip():
        chunks.append(current.strip())

    # Apply overlap: prepend the tail of the previous chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(f"{prev_tail}\n\n{chunks[i]}")
        chunks = overlapped

    return [c for c in chunks if c.strip()]


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _split_chunks_with_spans(
    text: str,
    *,
    page_spans: list[Any] | tuple[Any, ...] | None = None,
    page_blocks: list[Any] | tuple[Any, ...] | None = None,
    max_chars: int = _CHUNK_MAX_CHARS,
    overlap: int = _CHUNK_OVERLAP_CHARS,
) -> list[ChunkRecord]:
    """Return legacy chunks plus bounded document/page character offsets."""
    raw_chunks = _split_chunks(text, max_chars=max_chars, overlap=overlap)
    records: list[ChunkRecord] = []
    cursor = 0
    for raw in raw_chunks:
        start = text.find(raw, max(0, cursor - max(0, overlap)))
        if start < 0:
            start = text.find(raw)
        if start < 0:
            start = cursor
        end = min(len(text), start + len(raw))
        page_start = page_end = None
        ids: list[str] = []
        if page_spans:
            intersecting = [
                item for item in page_spans
                if int(_field(item, "char_end", 0)) > start
                and int(_field(item, "char_start", 0)) < end
            ]
            if intersecting:
                page_start = int(_field(intersecting[0], "page_number"))
                page_end = int(_field(intersecting[-1], "page_number"))
        if page_blocks:
            for block in page_blocks:
                if int(_field(block, "char_end", 0)) > start and int(_field(block, "char_start", 0)) < end:
                    block_id = _field(block, "id")
                    if block_id:
                        ids.append(str(block_id))
            ids = ids[:_MAX_PAGE_BLOCK_IDS]
        records.append(ChunkRecord(raw, start, end, page_start, page_end, tuple(ids)))
        cursor = max(cursor, end - max(0, overlap))
    return records


def _clean_chunk(text: str) -> str:
    """Post-process a single chunk: normalize whitespace."""
    return re.sub(r"\s+", " ", text).strip()


class KnowledgePipelineProgress:
    """Tracks pipeline progress for a single document version."""

    def __init__(self) -> None:
        self.stage = "scanning"
        self.chunks_total = 0
        self.chunks_written = 0
        self.errors: list[str] = []

    def update(
        self, stage: str, *, chunks_total: int = 0, chunks_written: int = 0
    ) -> None:
        self.stage = stage
        if chunks_total:
            self.chunks_total = chunks_total
        if chunks_written:
            self.chunks_written = chunks_written


ProgressCallback = Callable[[KnowledgePipelineProgress], None]


def process_document(
    *,
    text: str,
    category: str,
    file_id: str,
    on_progress: ProgressCallback | None = None,
    page_spans: list[Any] | tuple[Any, ...] | None = None,
    page_blocks: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """Run the local knowledge pipeline: chunk → embed → store.

    Args:
        text: Extracted document text.
        category: Knowledge-space category used to partition vectors.
        file_id: Unique identifier for this document version.
        on_progress: Optional progress callback.

    Returns:
        {"chunk_count": int, "vector_ids": list[str]}
    """
    t0 = time.monotonic()
    progress = KnowledgePipelineProgress()

    def _notify() -> None:
        if on_progress:
            on_progress(progress)

    # 1. Clean and chunk
    progress.stage = "chunking"
    _notify()
    cleaned = _clean_text(text)
    records = _split_chunks_with_spans(
        cleaned, page_spans=page_spans, page_blocks=page_blocks
    )
    chunks = [_clean_chunk(record.text) for record in records if record.text.strip()]
    progress.chunks_total = len(chunks)

    logger.info(
        "pipeline.ingest.start",
        extra={
            "document_id": file_id,
            "chunk_count": len(chunks),
            "span": "pipeline.ingest",
        },
    )
    if not chunks:
        progress.stage = "ready"
        _notify()
        logger.info(
            "pipeline.ingest.end",
            extra={
                "document_id": file_id,
                "chunks_embedded": 0,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
                "span": "pipeline.ingest",
            },
        )
        return {"chunk_count": 0, "vector_ids": []}
    store = PgVectorStore()
    # 2. Write chunks to the vector store with per-chunk retry.
    # SQLite serializes writers, so keep ingest single-threaded there.
    progress.stage = "indexing"
    _notify()
    vector_ids: list[str] = []
    errors: list[str] = []

    def _write_chunk(idx: int, record: ChunkRecord) -> str:
        chunk_text = _clean_chunk(record.text)
        vid = f"{file_id}_chunk_{idx}_{uuid.uuid4().hex[:8]}"
        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            if attempt > 1:
                backoff = _RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 2))
                logger.warning(
                    "pipeline.chunk.retry",
                    extra={
                        "document_id": file_id,
                        "chunk_index": idx,
                        "attempt": attempt,
                        "max_attempts": _RETRY_MAX_ATTEMPTS,
                        "backoff_seconds": backoff,
                        "reason": type(last_exc).__name__ if last_exc else "unknown",
                        "span": "pipeline.ingest",
                    },
                )
                time.sleep(backoff)
            try:
                metadata: dict[str, Any] = {
                    "chunk_index": idx,
                    "source": "knowledge_pipeline",
                }
                if page_spans:
                    metadata.update(
                        {
                            "char_start": record.char_start,
                            "char_end": record.char_end,
                            "page_start": record.page_start,
                            "page_end": record.page_end,
                            "page_block_ids": ",".join(record.page_block_ids),
                            "page_block_ids_truncated": len(record.page_block_ids) >= _MAX_PAGE_BLOCK_IDS,
                        }
                    )
                    if len(str(metadata)) > _MAX_PAGE_METADATA_CHARS:
                        metadata["page_block_ids"] = ",".join(record.page_block_ids[:8])
                        metadata["page_block_ids_truncated"] = True
                return store.chunk_add(
                    category,
                    file_id=file_id,
                    document=chunk_text,
                    vector_id=vid,
                    metadata=metadata,
                )
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    raise
        if last_exc is None:
            raise RuntimeError(f"chunk {idx}: retry loop exited without exception")
        raise last_exc

    with ThreadPoolExecutor(max_workers=_write_concurrency()) as pool:
        futures = {
            pool.submit(_write_chunk, i, record): i
            for i, record in enumerate(records)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                vid = future.result()
                vector_ids.append(vid)
            except Exception as exc:
                error_msg = f"chunk {idx} failed: {exc}"
                errors.append(error_msg)
                logger.error(
                    "pipeline.chunk.failed",
                    extra={
                        "document_id": file_id,
                        "chunk_index": idx,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "span": "pipeline.ingest",
                    },
                )
            progress.chunks_written = len(vector_ids)
            progress.errors = list(errors)
            _notify()

    if errors:
        # Keep the previous generation queryable. Only discard chunks written
        # by this failed attempt; the version will remain non-queryable.
        if vector_ids:
            try:
                store.delete_by_file(
                    category,
                    file_id,
                    only_ids=vector_ids,
                )
            except Exception:
                logger.exception(
                    "pipeline.cleanup.failed",
                    extra={
                        "document_id": file_id,
                        "chunk_count": len(vector_ids),
                        "error": "failed-generation cleanup",
                        "span": "pipeline.ingest",
                    },
                )
        failure_summary = "; ".join(errors[:5])
        if len(errors) > 5:
            failure_summary += f" (and {len(errors) - 5} more)"
        raise RuntimeError(
            f"{len(errors)}/{len(chunks)} chunks failed after "
            f"{_RETRY_MAX_ATTEMPTS} attempts: {failure_summary}"
        )

    # Atomically from the caller's perspective: old vectors are retained until
    # every new chunk has been persisted. If cleanup fails, strict mode surfaces
    # the error instead of claiming a clean replacement with stale duplicates.
    store.delete_by_file(
        category,
        file_id,
        strict=True,
        exclude_ids=vector_ids,
    )

    progress.stage = "ready"
    progress.errors = []
    _notify()

    logger.info(
        "pipeline.ingest.end",
        extra={
            "document_id": file_id,
            "chunks_embedded": len(vector_ids),
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            "span": "pipeline.ingest",
        },
    )

    return {"chunk_count": len(vector_ids), "vector_ids": vector_ids}
