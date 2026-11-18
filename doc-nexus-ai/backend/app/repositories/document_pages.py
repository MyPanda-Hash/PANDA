from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.document_pages import DocumentPage, DocumentPageBlock
from app.models.documents import DocumentVersion
from app.services.image_documents import is_image_version
from app.services.pdf_ocr import (
    ExtractedPdfPage, PageTextSpan, PdfExtractionResult, PdfTextBlock,
)


@dataclass(frozen=True, slots=True)
class PageLocation:
    page_number: int
    page_block_id: str | None
    bbox: tuple[float, float, float, float] | None
    location_label: str
    support_state: str


def _block_offsets(page_text: str, page_start: int, block_text: str, cursor: int) -> tuple[int, int, int]:
    clean = block_text.strip()
    if not clean:
        return page_start, page_start, cursor
    found = page_text.find(clean, cursor)
    if found < 0:
        found = page_text.find(clean)
    if found < 0:
        found = cursor
    start = page_start + found
    end = start + len(clean)
    return start, end, max(cursor, found + len(clean))


def replace_version_pages(
    db: Session, *, version: DocumentVersion, extraction: PdfExtractionResult
) -> list[DocumentPage]:
    """Atomically replace page/block rows and update version extraction summary."""
    old_pages = list(db.scalars(select(DocumentPage).where(DocumentPage.document_version_id == version.id)))
    for page in old_pages:
        db.delete(page)
    db.flush()
    created_at = now()
    pages: list[DocumentPage] = []
    for extracted, span in zip(extraction.pages, extraction.page_spans, strict=False):
        page = DocumentPage(
            id=f"pg_{new_token(10)}",
            document_version_id=version.id,
            page_number=extracted.page_number,
            width_points=max(extracted.width_points, 0.01),
            height_points=max(extracted.height_points, 0.01),
            rotation=extracted.rotation,
            extraction_method=extracted.extraction_method,
            text=extracted.text,
            quality_score=extracted.quality_score,
            average_confidence=extracted.average_confidence,
            warning_code=extracted.warning_code,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(page)
        db.flush()
        cursor = 0
        for block in extracted.blocks:
            start, end, cursor = _block_offsets(extracted.text, span.char_start, block.text, cursor)
            bbox = block.bbox
            db.add(
                DocumentPageBlock(
                    id=f"pgb_{new_token(10)}",
                    page_id=page.id,
                    block_index=block.block_index,
                    source=block.source,
                    text=block.text,
                    confidence=block.confidence,
                    bbox_x0=bbox[0] if bbox else None,
                    bbox_y0=bbox[1] if bbox else None,
                    bbox_x1=bbox[2] if bbox else None,
                    bbox_y1=bbox[3] if bbox else None,
                    char_start=start,
                    char_end=end,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        pages.append(page)
    version.extraction_method = extraction.extraction_method
    version.page_count = len(extraction.pages)
    version.ocr_page_count = extraction.ocr_page_count
    version.vision_page_count = extraction.vision_page_count
    if extraction.vision_calls:
        from app.services.pricing import fill_usage_cost

        priced_calls: list[dict] = []
        for raw_call in extraction.vision_calls:
            call = dict(raw_call)
            fill_usage_cost(db, call)
            priced_calls.append(call)
        version.vision_usage_json = json.dumps(
            {"calls": priced_calls},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        version.vision_usage_json = "{}"
    version.extraction_warning_code = extraction.warning_code
    version.updated_at = created_at
    db.flush()
    return pages


def pages_for_versions(db: Session, version_ids: list[str]) -> dict[str, list[DocumentPage]]:
    if not version_ids:
        return {}
    result: dict[str, list[DocumentPage]] = {item: [] for item in version_ids}
    rows = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_version_id.in_(version_ids))
        .order_by(DocumentPage.document_version_id, DocumentPage.page_number)
    ).all()
    for page in rows:
        result.setdefault(page.document_version_id, []).append(page)
    return result


def blocks_for_pages(db: Session, page_ids: list[str]) -> dict[str, list[DocumentPageBlock]]:
    if not page_ids:
        return {}
    result: dict[str, list[DocumentPageBlock]] = {item: [] for item in page_ids}
    rows = db.scalars(
        select(DocumentPageBlock)
        .where(DocumentPageBlock.page_id.in_(page_ids))
        .order_by(DocumentPageBlock.page_id, DocumentPageBlock.block_index)
    ).all()
    for block in rows:
        result.setdefault(block.page_id, []).append(block)
    return result


def cache_image_extraction(
    db: Session, *, version: DocumentVersion, extraction: PdfExtractionResult,
    source_sha256: str, parser_version: str,
) -> None:
    """原件指纹、识别页和费用同事务保存，索引重试不重新调用模型。"""
    replace_version_pages(db, version=version, extraction=extraction)
    usage = json.loads(version.vision_usage_json or "{}")
    # 重试版本可复用旧页，但旧版本已记账的调用不能再次计入用量。
    calls = usage.get("calls", [])
    usage["image_cached_calls"] = [call for call in calls if call.get("image_reused")]
    usage["calls"] = [call for call in calls if not call.get("image_reused")]
    usage["image_source_sha256"] = source_sha256
    usage["image_parser_version"] = parser_version
    version.vision_usage_json = json.dumps(usage, ensure_ascii=False, separators=(",", ":"))
    db.flush()


def load_image_extraction(
    db: Session, *, version: DocumentVersion, source_sha256: str, parser_version: str,
) -> PdfExtractionResult | None:
    seen = set()
    reused = False
    while version.id not in seen:
        seen.add(version.id)
        try:
            usage = json.loads(version.vision_usage_json or "{}")
        except (TypeError, ValueError):
            usage = {}
        if (
            isinstance(usage, dict)
            and usage.get("image_source_sha256") == source_sha256
            and usage.get("image_parser_version") == parser_version
        ):
            break
        previous = db.get(DocumentVersion, version.retry_of_version_id) if version.retry_of_version_id else None
        if previous is None or previous.document_id != version.document_id:
            return None
        version = previous
        reused = True
    else:
        return None
    stored_pages = pages_for_versions(db, [version.id])[version.id]
    if not stored_pages:
        return None
    stored_blocks = blocks_for_pages(db, [page.id for page in stored_pages])
    pages = []
    spans = []
    cursor = 0
    for page in stored_pages:
        blocks = []
        for block in stored_blocks[page.id]:
            bbox = (
                (block.bbox_x0, block.bbox_y0, block.bbox_x1, block.bbox_y1)
                if block.bbox_x0 is not None else None
            )
            blocks.append(PdfTextBlock(
                block.block_index, block.text, "vision", None, bbox,
            ))
        if pages:
            cursor += 2
        spans.append(PageTextSpan(page.page_number, cursor, cursor + len(page.text)))
        cursor += len(page.text)
        pages.append(ExtractedPdfPage(
            page_number=page.page_number,
            width_points=page.width_points, height_points=page.height_points,
            rotation=page.rotation, extraction_method="vision", text=page.text,
            quality_score=page.quality_score, average_confidence=None,
            blocks=tuple(blocks), warning_code=page.warning_code,
        ))
    calls = tuple(
        ({**call, "image_reused": True} if reused else call)
        for call in [*usage.get("image_cached_calls", []), *usage.get("calls", [])]
        if isinstance(call, dict)
    )
    return PdfExtractionResult(
        pages=tuple(pages), text="\n\n".join(page.text for page in pages),
        page_spans=tuple(spans), extraction_method="vision", ocr_page_count=0,
        warning_code=version.extraction_warning_code,
        vision_page_count=version.vision_page_count, vision_calls=calls,
    )


def location_for_char_range(db: Session, version_id: str, start: int, end: int) -> PageLocation | None:
    if start < 0 or end < start:
        return None
    rows = db.execute(
        select(DocumentPage, DocumentPageBlock)
        .join(DocumentPageBlock, DocumentPageBlock.page_id == DocumentPage.id)
        .where(DocumentPage.document_version_id == version_id)
        .order_by(DocumentPage.page_number, DocumentPageBlock.block_index)
    ).all()
    matched: list[tuple[DocumentPage, DocumentPageBlock]] = []
    for page, block in rows:
        if block.char_end > start and block.char_start < max(end, start + 1):
            matched.append((page, block))
    if not matched:
        return None
    first_page = matched[0][0].page_number
    last_page = matched[-1][0].page_number
    label = f"第 {first_page} 页" if first_page == last_page else f"第 {first_page}–{last_page} 页"
    version = db.get(DocumentVersion, version_id)
    if is_image_version(version):
        support_state = (
            "uncertain"
            if any(
                (page.warning_code and page.warning_code != "image_vision_uncertain")
                or "\n\n【不确定性（不可作确定事实）】\n" in block.text
                for page, block in matched
            )
            else "supported"
        )
    else:
        support_state = (
            "uncertain"
            if any(block.source == "vision" for _page, block in matched)
            else "supported"
        )
    bbox = None
    block_id = None
    if len(matched) == 1:
        block = matched[0][1]
        block_id = block.id
        if None not in (block.bbox_x0, block.bbox_y0, block.bbox_x1, block.bbox_y1):
            bbox = (block.bbox_x0, block.bbox_y0, block.bbox_x1, block.bbox_y1)
    return PageLocation(first_page, block_id, bbox, label, support_state)
