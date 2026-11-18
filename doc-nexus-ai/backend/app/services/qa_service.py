"""LLM-powered QA generation with evidence retrieval, citation, and quality verification.

This module replaces the deterministic stub with a real RAG pipeline:
1. Resolve the configured chat model endpoint (same as Pi Chat).
2. Retrieve document content from ready, queryable versions.
3. Build a prompt with context blocks + citation markers.
4. Call the LLM (OpenAI-compatible chat completions API).
5. Parse the response into answer text, citations, and usage metrics.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

import httpx
from sqlalchemy.orm import Session

from app.core.assistant_identity import QA_PERSONA_PREAMBLE
from app.core.config import settings
from app.models.documents import Document, DocumentVersion
from app.models.model_endpoints import ModelEndpoint
from app.repositories import qa as qa_repo
from app.services import data_guard, linkmind
from app.services.anthropic_http import (
    anthropic_headers,
    anthropic_messages_url,
)
from app.services.evidence_snapshots import freeze_text, make_citation_id
from app.services.image_documents import image_source_text, is_image_version
from app.services.model_endpoint_config import (
    PROTOCOL_MESSAGES,
    resolve_endpoint_configuration,
)
from app.services.model_routing import resolve_model_endpoint
from app.services.pi_runtime.persona import qa_system_prompt
from app.services.usage_normalize import reported_total_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTEXT_CHARS = 12000  # conservative: leave room for prompt + answer


def _safe_float(value: object, default: float) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


CITATION_RE = re.compile(
    r"\[((?:cit|CIT)[_-][a-zA-Z0-9_-]+|"
    r"(?:writing|vector)-evidence:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}:"
    r"(?:[0-9a-f]{64}:)?[0-9]{1,20}-[0-9]{1,20})\]"
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

QA_SYSTEM_RULES = """你的回答必须严格基于提供的证据材料。

规则：
1. 只使用证据材料中给出的引用 ID，格式必须为 [cit_xxx]，不得编造。
2. 如果证据不足以回答问题，明确说明"当前知识库暂无足够依据"。
3. 使用中文回答，保持专业、简洁、亲切。
4. 每个关键事实后标注对应的 [cit_xxx] 引用标记。
5. 引用标记只能使用提供的证据中已有的 ID，不得自行编造新 ID。"""

# Kept for callers that import the historical module constant.
QA_SYSTEM_PROMPT = f"""\
{QA_PERSONA_PREAMBLE}
{QA_SYSTEM_RULES}"""


def _current_qa_system_prompt() -> str:
    return f"{qa_system_prompt().rstrip()}\n\n{QA_SYSTEM_RULES}"


QA_USER_TEMPLATE = """请根据以下证据材料回答问题。

## 证据材料

{context}

{graph_section}## 问题

{question}

请用中文回答，并在每个关键事实后标注对应的引用标记。"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class QaServiceError(RuntimeError):
    """Raised when the QA pipeline encounters a non-recoverable error."""


class LlmUnavailableError(QaServiceError):
    """No suitable LLM endpoint is configured or reachable."""


def _resolve_qa_endpoint(db: Session) -> ModelEndpoint | None:
    """Reuse the configured Pi Chat model endpoint for knowledge QA."""
    return resolve_model_endpoint(db, "chat")


def _chat_completions_url(endpoint_url: str) -> str:
    """Build an OpenAI-compatible chat completions URL from a base or full endpoint."""
    raw = (endpoint_url or "").strip()
    if not raw:
        return raw
    value = raw.split("?")[0].rstrip("/")
    for suffix in (
        "/chat/completions",
        "/completions",
        "/responses",
        "/embeddings",
        "/rerank",
        "/openai/v1",
        "/openai",
    ):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    base = value.rstrip("/")
    if not base:
        return raw
    return f"{base}/chat/completions"


def _read_document_text(file_path: str | None) -> str | None:
    """Read plain-text content from a local file or extracted sidecar.

    Supports: .txt / .md / .markdown / .csv / .xlsx / .xls / *.extracted.txt.
    Spreadsheets are parsed from the original file so GBK/HTML .xls exports and
    binary workbooks are not shown as UTF-8 mojibake from an old sidecar.
    """
    if not file_path:
        return None
    from app.services import minio_storage
    from app.services.document_processor import (
        decode_text_bytes,
        extract_spreadsheet_text,
        extracted_sidecar_path,
    )
    from app.services.image_documents import IMAGE_EXTENSIONS

    is_remote = minio_storage.is_minio_uri(file_path)
    p = Path(file_path)
    suffix = p.suffix.lower()
    if not is_remote and suffix in {".xlsx", ".xls"} and p.is_file():
        try:
            text = extract_spreadsheet_text(p.read_bytes(), suffix)
            if text.strip():
                return text
        except Exception:
            logger.exception(
                "document.extract.failed",
                extra={"file_path": str(file_path), "error": "spreadsheet extraction failed"},
            )

    sidecar = extracted_sidecar_path(file_path)
    if sidecar and sidecar.is_file() and (is_remote or suffix not in {".xlsx", ".xls"}):
        try:
            text = sidecar.read_bytes().decode("utf-8")
            if text.strip():
                return text
        except Exception:
            logger.exception(
                "document.sidecar.read.failed",
                extra={"file_path": str(sidecar), "error": "sidecar read failed"},
            )

    # Object-backed originals are never downloaded in this synchronous reader.
    if is_remote:
        return None

    if not p.is_file():
        logger.warning(
            "document.file.not_found",
            extra={"file_path": str(file_path)},
        )
        return None
    if suffix in {".txt", ".md", ".markdown", ".csv"}:
        try:
            return decode_text_bytes(p.read_bytes())
        except Exception:
            logger.exception(
                "document.read.failed",
                extra={"file_path": str(file_path), "error": "document read failed"},
            )
            return None
    if suffix in IMAGE_EXTENSIONS or suffix in {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".potx", ".xlsx", ".xls"}:
        return None
    try:
        return decode_text_bytes(p.read_bytes())
    except Exception:
        logger.warning(
            "document.read.unsupported",
            extra={"file_path": str(file_path), "error": "cannot read as text"},
        )
        return None


def _resolve_version_text(version: DocumentVersion) -> str | None:
    """Resolve the best available text for a published document version."""
    return _read_document_text(version.file_path)


def _tokenize_for_retrieval(text: str) -> Counter[str]:
    """Lightweight Chinese/English tokens for lexical relevance (no external NLP deps)."""
    tokens: list[str] = []
    lowered = (text or "").lower()
    for match in _LATIN_TOKEN_RE.finditer(lowered):
        token = match.group()
        if len(token) >= 2 or token.isdigit():
            tokens.append(token)
    for match in _CJK_RUN_RE.finditer(text or ""):
        run = match.group()
        tokens.append(run)
        if len(run) >= 2:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        if len(run) >= 3:
            tokens.extend(run[i : i + 3] for i in range(len(run) - 2))
    return Counter(tokens)


def _lexical_relevance_score(
    query: str, *, title: str, body: str, summary: str = ""
) -> float:
    query_tokens = _tokenize_for_retrieval(query)
    if not query_tokens:
        return 0.0
    title_tokens = _tokenize_for_retrieval(title)
    summary_tokens = _tokenize_for_retrieval(summary)
    body_tokens = _tokenize_for_retrieval(body[:12000])
    title_hit = sum(min(query_tokens[t], title_tokens[t]) for t in query_tokens)
    summary_hit = sum(min(query_tokens[t], summary_tokens[t]) for t in query_tokens)
    body_hit = sum(min(query_tokens[t], body_tokens[t]) for t in query_tokens)
    query_weight = _safe_float(sum(query_tokens.values()), 1.0)
    return (4.0 * title_hit + 2.0 * summary_hit + body_hit) / query_weight


def rank_documents_for_query(
    question: str,
    candidates: list[tuple[Document, DocumentVersion]],
    *,
    top_k: int | None = None,
) -> list[tuple[Document, DocumentVersion, float]]:
    """Rank ready documents by lexical overlap with the question; keep positive matches.

    Returns (document, version, score) sorted by score desc. Documents with score 0
    are omitted so recent-but-irrelevant docs are not injected into context.
    """
    limit = (
        top_k
        if top_k is not None
        else max(1, _safe_int(settings.qa_retrieval_top_k, 8))
    )
    scored: list[tuple[Document, DocumentVersion, float]] = []
    for document, version in candidates:
        body = _resolve_version_text(version) or ""
        score = _lexical_relevance_score(
            question,
            title=document.name or "",
            body=body,
            summary=version.summary or "",
        )
        if score > 0:
            scored.append((document, version, score))
    scored.sort(key=lambda item: (-item[2], item[0].id))
    return scored[:limit]


def retrieve_relevant_documents(
    db: Session,
    question: str,
    space_ids: list[str],
    *,
    top_k: int | None = None,
) -> tuple[list[tuple[Document, DocumentVersion]], dict[str, Any]]:
    """Backward-compatible wrapper around hybrid lexical + graph retrieval."""
    from app.services.hybrid_retrieval import retrieve_for_qa

    bundle = retrieve_for_qa(db, question, space_ids, top_k=top_k)
    return bundle.documents, bundle.meta


def _default_evidence_support_state(version: DocumentVersion) -> str:
    """图片按实际提取问题判定；PDF 视觉辅助保留原有质量边界。"""
    if is_image_version(version):
        warning = str(getattr(version, "extraction_warning_code", "") or "").strip()
        return "uncertain" if warning and warning != "image_vision_uncertain" else "supported"
    return (
        "uncertain"
        if str(getattr(version, "extraction_method", "") or "").strip().lower()
        in {"vision", "mixed"}
        else "supported"
    )


def _evidence_support_state(
    version: DocumentVersion,
    location: Any | None,
    item: dict[str, Any] | None = None,
    *,
    snapshot_text: str | None = None,
) -> str:
    default_state = _default_evidence_support_state(version)
    item_state = str((item or {}).get("support_state") or "").strip().lower()
    if item_state in {"unsupported", "stale", "unavailable", "unlinked"}:
        return item_state
    image = is_image_version(version)
    if image and snapshot_text and "\n\n【不确定性（不可作确定事实）】\n" in snapshot_text:
        return "uncertain"
    if item is not None and not image and str(item.get("source") or "").strip().lower() == "vision":
        return "uncertain"
    if item_state == "uncertain":
        return "uncertain"
    if location is not None:
        location_state = str(
            getattr(location, "support_state", "") or ""
        ).strip().lower()
        if location_state == "uncertain" or default_state == "uncertain":
            return "uncertain"
        if location_state:
            return location_state
    return default_state


def _build_evidence_blocks(
    candidates: list[tuple[Document, DocumentVersion]],
    *,
    db: Session | None = None,
    snapshot_bound: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Build context text and evidence records from candidate documents.

    Returns (context_text, evidence_list).
    evidence_list items match the shape expected by qa_repo.create_turn.
    Legacy QA retains per-turn cit_* primary keys via snapshot_bound=False.
    Both modes freeze exact source text and carry its snapshot citation binding.
    """
    blocks: list[str] = []
    evidence: list[dict[str, Any]] = []
    remaining = MAX_CONTEXT_CHARS

    for doc, version in candidates:
        if remaining <= 200:
            break
        content = _resolve_version_text(version)
        citation_id = qa_repo.next_citation_id()
        page_or_section = f"文档《{doc.name}》v{version.version_number}"

        if content:
            # Truncate each document to a reasonable size within remaining budget
            per_doc_cap = min(2000, remaining - 80)
            text_sha256 = freeze_text(version.id, content)
            excerpt = image_source_text(version, content, db=db, end=max(0, per_doc_cap))
            snapshot_citation_id = make_citation_id(version.id, text_sha256, 0, len(excerpt))
            if snapshot_bound:
                citation_id = snapshot_citation_id
            location = None
            if db is not None:
                try:
                    from app.services.evidence_snapshots import snapshot_page_location

                    location = snapshot_page_location(db, version.id, content, 0, len(excerpt))
                except Exception:
                    logger.warning("document page location unavailable", exc_info=True)
            if location is not None:
                page_or_section = f"{page_or_section} · {location.location_label}"
            block = f"[{citation_id}] {page_or_section}\n内容：\n{excerpt}"
            blocks.append(block)
            item = {
                "id": citation_id,
                "document_id": doc.id,
                "document_version_id": version.id,
                "snapshot_citation_id": snapshot_citation_id,
                "text_sha256": text_sha256,
                "char_start": 0,
                "char_end": len(excerpt),
                "page_or_section": page_or_section,
                "excerpt": excerpt,
                "support_state": _evidence_support_state(version, location, snapshot_text=content),
            }
            if location is not None:
                item.update(
                    {
                        "page_number": location.page_number,
                        "page_block_id": location.page_block_id,
                        "bbox": location.bbox,
                    }
                )
            evidence.append(item)
            remaining -= len(block)
        else:
            # No extractable text content; include metadata only.
            excerpt = f"文档《{doc.name}》的第 {version.version_number} 版当前已发布且可检索，但暂无可提取的文本内容。"
            block = f"[{citation_id}] {excerpt}"
            blocks.append(block)
            evidence.append(
                {
                    "id": citation_id,
                    "document_id": doc.id,
                    "document_version_id": version.id,
                    "page_or_section": page_or_section,
                    "excerpt": excerpt,
                    "support_state": "uncertain",
                }
            )
            remaining -= len(block)

    context_text = "\n\n---\n\n".join(blocks)
    return context_text, evidence


def _build_messages(
    context_text: str,
    question: str,
    *,
    enrichment_text: str = "",
) -> list[dict[str, str]]:
    graph_section = ""
    if enrichment_text.strip():
        graph_section = f"## 知识图谱要点\n\n{enrichment_text.strip()}\n\n"
    return [
        {"role": "system", "content": _current_qa_system_prompt()},
        {
            "role": "user",
            "content": QA_USER_TEMPLATE.format(
                context=context_text,
                graph_section=graph_section,
                question=question,
            ),
        },
    ]


def _call_llm(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    user_id: str | None = None,
    timeout_seconds: float = 60.0,
) -> tuple[str, dict[str, Any]]:
    """Call the LLM via the endpoint's configured API protocol.

    Returns (answer_text, usage_dict).
    """
    configuration = resolve_endpoint_configuration(
        endpoint.provider, endpoint.adapter, endpoint.api_protocol
    )
    if configuration.api_protocol == PROTOCOL_MESSAGES:
        return _call_anthropic_llm(
            endpoint,
            messages,
            user_id=user_id,
            timeout_seconds=timeout_seconds,
        )
    t0 = time.monotonic()
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": False,
    }
    # 云端端点出网前脱敏；本地回环端点 session 为空操作。
    mask_session = data_guard.session_for_endpoint(endpoint.endpoint_url)
    payload = mask_session.mask_payload(payload)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if endpoint.secret_ref:
        secret = _resolve_endpoint_secret(endpoint.secret_ref, user_id=user_id)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

    request_url = _chat_completions_url(endpoint.endpoint_url)
    logger.info(
        "llm.call.start",
        extra={
            "endpoint_id": endpoint.id,
            "model": endpoint.model,
            "provider": endpoint.provider,
            "url": request_url.split("?")[0],
            "span": "qa.llm_call",
        },
    )
    try:
        response = httpx.post(
            request_url,
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error(
            "llm.call.failed",
            extra={
                "endpoint_id": endpoint.id,
                "model": endpoint.model,
                "provider": endpoint.provider,
                "url": request_url.split("?")[0],
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": str(exc),
                "span": "qa.llm_call",
            },
        )
        raise QaServiceError(f"LLM 调用失败：{exc}") from exc

    latency_ms = _safe_int((time.monotonic() - t0) * 1000, 0)
    body = response.json()
    choices = body.get("choices", [])
    if not choices:
        raise QaServiceError("LLM 返回了空的 choices 列表")
    answer = choices[0].get("message", {}).get("content", "")
    if isinstance(answer, str):
        answer = mask_session.restore_text(answer)
    usage_raw = body.get("usage", {})
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    from app.services.usage_normalize import normalize_usage_buckets

    buckets = normalize_usage_buckets(usage_raw)
    usage: dict[str, Any] = {
        "provider": endpoint.provider,
        "model": endpoint.model,
        **buckets,
        "reported_total_tokens": reported_total_tokens(usage_raw),
        "cost": "0",
        "latency_ms": latency_ms,
    }
    logger.info(
        "llm.call.end",
        extra={
            "endpoint_id": endpoint.id,
            "model": endpoint.model,
            "provider": endpoint.provider,
            "latency_ms": latency_ms,
            "span": "qa.llm_call",
        },
    )
    return answer, usage


def _call_anthropic_llm(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    user_id: str | None = None,
    timeout_seconds: float = 60.0,
) -> tuple[str, dict[str, Any]]:
    """Call an Anthropic Messages API endpoint directly.

    ``messages`` use the shared OpenAI-shaped role/content pairs; the
    system prompt moves to Anthropic's top-level ``system`` field and the
    remaining turns map to ``messages`` verbatim. Response text comes from
    ``content[].text`` blocks and usage from the Anthropic token fields.
    """
    t0 = time.monotonic()
    system_parts: list[str] = []
    turns: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            system_parts.append(content)
        else:
            turns.append({"role": role, "content": content})
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": turns,
        "max_tokens": 2048,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if not turns:
        raise QaServiceError("QA 没有可发送的用户消息")

    mask_session = data_guard.session_for_endpoint(endpoint.endpoint_url)
    payload = mask_session.mask_payload(payload)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if endpoint.secret_ref:
        secret = _resolve_endpoint_secret(endpoint.secret_ref, user_id=user_id)
        if secret:
            headers.update(anthropic_headers(secret, endpoint.endpoint_url))

    request_url = anthropic_messages_url(endpoint.endpoint_url)
    logger.info(
        "llm.call.start",
        extra={
            "endpoint_id": endpoint.id,
            "model": endpoint.model,
            "provider": endpoint.provider,
            "url": request_url,
            "span": "qa.llm_call",
        },
    )
    try:
        response = httpx.post(
            request_url,
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error(
            "llm.call.failed",
            extra={
                "endpoint_id": endpoint.id,
                "model": endpoint.model,
                "provider": endpoint.provider,
                "url": request_url,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": str(exc),
                "span": "qa.llm_call",
            },
        )
        raise QaServiceError(f"LLM 调用失败：{exc}") from exc

    latency_ms = _safe_int((time.monotonic() - t0) * 1000, 0)
    body = response.json()
    content_blocks = body.get("content", [])
    if not isinstance(content_blocks, list) or not content_blocks:
        raise QaServiceError("LLM 返回了空的 content 列表")
    chunks: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    answer = "".join(chunks).strip()
    if not answer:
        raise QaServiceError("LLM 返回了空的文本内容")
    answer = mask_session.restore_text(answer)
    usage_raw = body.get("usage", {})
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    from app.services.usage_normalize import normalize_usage_buckets

    buckets = normalize_usage_buckets(usage_raw)
    usage: dict[str, Any] = {
        "provider": endpoint.provider,
        "model": endpoint.model,
        **buckets,
        "reported_total_tokens": reported_total_tokens(
            usage_raw, input_includes_cache=False
        ),
        "cost": "0",
        "latency_ms": latency_ms,
    }
    logger.info(
        "llm.call.end",
        extra={
            "endpoint_id": endpoint.id,
            "model": endpoint.model,
            "provider": endpoint.provider,
            "latency_ms": latency_ms,
            "span": "qa.llm_call",
        },
    )
    return answer, usage


def _resolve_endpoint_secret(
    secret_ref: str,
    *,
    user_id: str | None = None,
) -> str | None:
    """Resolve environment or per-user LinkMind endpoint credentials."""
    if secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF:
        if not user_id:
            raise QaServiceError("LinkMind 模型端点需要当前用户身份")
        try:
            return linkmind.ensure_user_key(user_id)
        except linkmind.LinkMindError as exc:
            raise QaServiceError("当前用户的 LinkMind Key 不可用") from exc
    if secret_ref.startswith("env://"):
        import os

        return os.environ.get(secret_ref[6:])
    return secret_ref


def _normalize_citation_id(raw: str) -> str:
    value = raw.strip()
    if value.lower().startswith("cit-"):
        return "cit_" + value[4:]
    if value.lower().startswith("cit_"):
        return "cit_" + value[4:]
    return value


def _parse_citation_coverage(answer: str, evidence_ids: set[str]) -> dict[str, Any]:
    """Analyse which citation IDs appear in the answer text.

    Returns a verification summary dict.
    """
    found_ids: set[str] = set()
    for m in CITATION_RE.finditer(answer):
        found_ids.add(_normalize_citation_id(m.group(1)))

    # Also accept bare evidence ids if the model omitted brackets inconsistently.
    evidence_lower = {item.lower(): item for item in evidence_ids}
    normalized_found = {evidence_lower.get(item.lower(), item) for item in found_ids}

    valid_ids = normalized_found & evidence_ids
    unknown_ids = normalized_found - evidence_ids
    unused_ids = evidence_ids - normalized_found

    if unknown_ids:
        logger.warning(
            "qa.citation.unknown",
            extra={
                "unknown_citations": sorted(unknown_ids),
                "citation_count": len(unknown_ids),
                "span": "qa.citation_coverage",
            },
        )

    return {
        "state": "supported" if valid_ids and not unknown_ids else "partial",
        "total_evidence": len(evidence_ids),
        "cited_in_answer": len(valid_ids),
        "unused_evidence": len(unused_ids),
        "unknown_citations": len(unknown_ids),
        "citation_ids": sorted(valid_ids),
    }


GenerateAnswerResult = tuple[
    str | None,
    str,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]


def generate_answer(
    db: Session,
    question: str,
    space_ids: list[str],
    *,
    user_id: str | None = None,
) -> GenerateAnswerResult:
    """Generate an LLM-polished answer from the knowledge base.

    Args:
        db: Database session.
        question: User's question.
        space_ids: Authorized knowledge space IDs.

    Returns:
        (answer, status, retrieval_summary, verification_summary, usage, events, evidence)
        - answer: The LLM-generated answer text, or None.
        - status: 'completed', 'knowledge_gap', or 'llm_unavailable'.
        - retrieval_summary: Retrieval metadata.
        - verification_summary: Citation coverage analysis.
        - usage: Token/cost/latency metrics.
        - events: Ordered event list for the turn.
        - evidence: Citation evidence records for persistence.
    """
    from app.services.hybrid_retrieval import retrieve_for_qa

    bundle = retrieve_for_qa(db, question, space_ids)
    candidates = bundle.documents
    retrieval_meta = bundle.meta

    retrieval_summary: dict[str, Any] = {
        "provider": "llm_qa_pipeline",
        "selected_space_ids": space_ids,
        "candidate_count": retrieval_meta["pool_size"],
        "selected_count": retrieval_meta["selected_count"],
        "retrieval_method": retrieval_meta["retrieval_method"],
        "top_k": retrieval_meta["top_k"],
        "graph_available": retrieval_meta.get("graph_available", False),
        "graph_entity_count": retrieval_meta.get("graph_entity_count", 0),
        "graph_boosted_doc_ids": retrieval_meta.get("graph_boosted_doc_ids", []),
    }

    if not candidates:
        verification_summary = {
            "state": "knowledge_gap",
            "reason": "no_queryable_evidence",
        }
        from app.services.retrieval_contract import build_retrieval_trace

        retrieval_summary["retrieval_trace"] = build_retrieval_trace(
            bundle, citation_count=0
        ).model_dump()
        usage = _zero_usage("no_candidates")
        events = _events_knowledge_gap(retrieval_summary, verification_summary, usage)
        return (
            None,
            "knowledge_gap",
            retrieval_summary,
            verification_summary,
            usage,
            events,
            [],
        )

    # Build evidence blocks from document content
    context_text, evidence = _build_evidence_blocks(candidates, db=db, snapshot_bound=False)
    from app.services.retrieval_contract import build_retrieval_trace

    retrieval_summary["retrieval_trace"] = build_retrieval_trace(
        bundle,
        citation_count=len(evidence),
        support_states=(item.get("support_state", "uncertain") for item in evidence),
    ).model_dump()
    evidence_ids = {item["id"] for item in evidence}

    # Resolve LLM endpoint
    endpoint = _resolve_qa_endpoint(db)
    if not endpoint:
        # Fallback: metadata-only deterministic response
        verification_summary = {
            "state": "supported",
            "reason": "llm_unavailable_metadata_only",
        }
        usage = _zero_usage("llm_unavailable")
        answer_text = "已找到可核验的授权文档版本。当前未配置 Chat 模型端点，请先完成模型连接后再提问，或通过 Citation 查看对应版本和证据位置。"
        events = _events_completed_metadata(
            retrieval_summary, answer_text, evidence, verification_summary, usage
        )
        return (
            answer_text,
            "completed",
            retrieval_summary,
            verification_summary,
            usage,
            events,
            evidence,
        )

    # Call LLM
    try:
        messages = _build_messages(
            context_text,
            question,
            enrichment_text=bundle.enrichment_text,
        )
        answer_text, usage = _call_llm(endpoint, messages, user_id=user_id)
        from app.services.pricing import fill_usage_cost

        fill_usage_cost(db, usage)
    except QaServiceError:
        # LLM call failed — still return metadata citations so user sees evidence
        verification_summary = {
            "state": "supported",
            "reason": "llm_call_failed_metadata_fallback",
        }
        usage = _zero_usage("llm_call_failed")
        answer_text = "已找到相关文档，但问答模型调用失败。请通过 Citation 查看对应版本和证据位置。"
        events = _events_completed_metadata(
            retrieval_summary, answer_text, evidence, verification_summary, usage
        )
        return (
            answer_text,
            "completed",
            retrieval_summary,
            verification_summary,
            usage,
            events,
            evidence,
        )

    # Parse citation coverage
    verification_summary = _parse_citation_coverage(answer_text, evidence_ids)

    # Build events
    events = _events_completed_llm(
        retrieval_summary, answer_text, evidence, verification_summary, usage
    )

    return (
        answer_text,
        "completed",
        retrieval_summary,
        verification_summary,
        usage,
        events,
        evidence,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zero_usage(reason: str) -> dict[str, Any]:
    return {
        "provider": "none",
        "model": "",
        "input_tokens": 0,
        "input_uncached_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": "0",
        "latency_ms": 0,
        "reason": reason,
    }


def _events_knowledge_gap(
    retrieval_summary: dict[str, Any],
    verification_summary: dict[str, Any],
    usage: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"type": "status", "status": "retrieving"},
        {"type": "retrieval_summary", "retrieval_summary": retrieval_summary},
        {"type": "verification_summary", "verification_summary": verification_summary},
        {"type": "usage", "usage": usage},
        {"type": "status", "status": "knowledge_gap"},
    ]


def _events_completed_metadata(
    retrieval_summary: dict[str, Any],
    answer_text: str,
    evidence: list[dict[str, Any]],
    verification_summary: dict[str, Any],
    usage: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "status", "status": "retrieving"},
        {"type": "retrieval_summary", "retrieval_summary": retrieval_summary},
        {"type": "answer_delta", "answer_delta": answer_text},
    ]
    for item in evidence:
        events.append({
            "type": "citation",
            "citation_id": item["id"],
            **{key: item[key] for key in ("snapshot_citation_id", "text_sha256", "char_start", "char_end") if key in item},
        })
    events.extend(
        [
            {
                "type": "verification_summary",
                "verification_summary": verification_summary,
            },
            {"type": "usage", "usage": usage},
            {"type": "status", "status": "completed"},
        ]
    )
    return events


def _events_completed_llm(
    retrieval_summary: dict[str, Any],
    answer_text: str,
    evidence: list[dict[str, Any]],
    verification_summary: dict[str, Any],
    usage: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "status", "status": "retrieving"},
        {"type": "retrieval_summary", "retrieval_summary": retrieval_summary},
    ]
    # Split answer into deltas for streaming-compatible event log
    words = answer_text.split()
    delta_size = max(1, len(words) // 5)
    for i in range(0, len(words), delta_size):
        chunk = " ".join(words[i : i + delta_size])
        events.append(
            {
                "type": "answer_delta",
                "answer_delta": chunk + (" " if i + delta_size < len(words) else ""),
            }
        )
    for item in evidence:
        events.append({
            "type": "citation",
            "citation_id": item["id"],
            **{key: item[key] for key in ("snapshot_citation_id", "text_sha256", "char_start", "char_end") if key in item},
        })
    events.extend(
        [
            {
                "type": "verification_summary",
                "verification_summary": verification_summary,
            },
            {"type": "usage", "usage": usage},
            {"type": "status", "status": "completed"},
        ]
    )
    return events
