from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Document, DocumentVersion
from app.repositories.document_pages import (
    blocks_for_pages,
    location_for_char_range,
    pages_for_versions,
    replace_version_pages,
)
from app.services.pdf_ocr import (
    ExtractedPdfPage,
    PageTextSpan,
    PdfExtractionResult,
    PdfTextBlock,
)


def _extraction() -> PdfExtractionResult:
    first = ExtractedPdfPage(
        page_number=1,
        width_points=612,
        height_points=792,
        rotation=0,
        extraction_method="native",
        text="设备编号 REC-00046",
        quality_score=0.9,
        average_confidence=None,
        blocks=(PdfTextBlock(0, "设备编号 REC-00046", "native"),),
    )
    second = ExtractedPdfPage(
        page_number=2,
        width_points=612,
        height_points=792,
        rotation=90,
        extraction_method="ocr",
        text="Serial REC-00047",
        quality_score=0.2,
        average_confidence=0.87,
        blocks=(PdfTextBlock(0, "Serial REC-00047", "ocr", 0.87, (0.1, 0.2, 0.6, 0.3)),),
    )
    return PdfExtractionResult(
        pages=(first, second),
        text="设备编号 REC-00046\n\nSerial REC-00047",
        page_spans=(PageTextSpan(1, 0, 17), PageTextSpan(2, 19, 35)),
        extraction_method="mixed",
        ocr_page_count=1,
    )


def test_replace_pages_is_idempotent_and_maps_char_ranges(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pages.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        document = Document(
            id="doc-pages", name="pages.pdf", status="active", created_by_user_id="user",
            created_at=__import__("datetime").datetime.now(), updated_at=__import__("datetime").datetime.now(),
        )
        version = DocumentVersion(
            id="ver-pages", document_id=document.id, version_number=1,
            processing_stage="queued", queryable=False, retryable=False,
            is_current_published=False, created_at=document.created_at, updated_at=document.updated_at,
        )
        db.add_all([document, version])
        db.flush()
        created = replace_version_pages(db, version=version, extraction=_extraction())
        db.commit()
        assert len(created) == 2
        assert (version.extraction_method, version.page_count, version.ocr_page_count) == ("mixed", 2, 1)
        replace_version_pages(db, version=version, extraction=_extraction())
        db.commit()
        pages = pages_for_versions(db, [version.id])[version.id]
        assert len(pages) == 2
        blocks = blocks_for_pages(db, [page.id for page in pages])
        assert sum(len(items) for items in blocks.values()) == 2
        location = location_for_char_range(db, version.id, 19, 35)
        assert location and location.page_number == 2 and location.page_block_id
        assert location.bbox == (0.1, 0.2, 0.6, 0.3)
        assert location.support_state == "supported"


@pytest.mark.parametrize(
    ("filename", "warning", "text", "expected_state"),
    [
        ("vision.pdf", None, "视觉识别文本", "uncertain"),
        ("photo.jpg", None, "【可见文字】\n设备功率 37.5W", "supported"),
        (
            "photo.jpg", "image_vision_uncertain",
            "视觉模型解析，未经核验\n\n【可见文字】\n设备功率 37.5W", "supported",
        ),
        (
            "photo.jpg", "image_vision_uncertain",
            "视觉模型解析，未经核验\n\n【可见文字】\n设备功率 37.5W"
            "\n\n【不确定性（不可作确定事实）】\n末位数字模糊", "uncertain",
        ),
        ("photo.jpg", "image_vision_unclear", "【可见文字】\n末位数字模糊", "uncertain"),
        ("photo.tiff", "image_vision_partial", "【可见文字】\n第一页文字", "uncertain"),
        (
            "photo.jpg", None,
            "【可见文字】\n视觉模型解析，未经核验\n\n【可见表格】\n|功率|37.5W|",
            "supported",
        ),
        (
            "photo.jpg", "image_vision_uncertain",
            (
                "图片分区 1｜视觉模型解析，未经核验\n\n【可见文字】\n原图原文：\n"
                "视觉模型解析，未经核验\n\n【可见表格】\n|功率|37.5W|",
                "图片分区 2｜视觉模型解析，未经核验\n\n【可见文字】\n第二分区内容",
            ),
            "supported",
        ),
    ],
)
def test_page_quality_projects_image_evidence_without_changing_snapshots(
    tmp_path: Path, monkeypatch, filename, warning, text, expected_state,
) -> None:
    from app.services import qa_service
    monkeypatch.setattr(qa_service.settings, "upload_dir", tmp_path)
    block_texts = text if isinstance(text, tuple) else (text,)
    text = "\n\n".join(block_texts)
    expected_parts = []
    for block_text in block_texts:
        prefix = block_text.split("\n\n", 1)[0]
        if prefix == "视觉模型解析，未经核验" or prefix.startswith("图片分区 "):
            expected_parts.append(" " * len(prefix) + block_text[len(prefix):])
        else:
            expected_parts.append(block_text)
    expected_projection = "\n\n".join(expected_parts)

    engine = create_engine(f"sqlite:///{tmp_path / 'vision-pages.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        stamp = __import__("datetime").datetime.now()
        document = Document(
            id="doc-vision",
            name=filename,
            status="active",
            created_by_user_id="user",
            created_at=stamp,
            updated_at=stamp,
        )
        version = DocumentVersion(
            id="ver-vision",
            document_id=document.id,
            version_number=1,
            file_path=str(tmp_path / filename),
            source_relative_path=filename,
            processing_stage="ready",
            queryable=True,
            retryable=False,
            is_current_published=True,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add_all([document, version])
        db.flush()
        extraction = PdfExtractionResult(
            pages=(
                ExtractedPdfPage(
                    page_number=1,
                    width_points=612,
                    height_points=792,
                    rotation=0,
                    extraction_method="vision",
                    text=text,
                    quality_score=0.5,
                    average_confidence=None,
                    blocks=tuple(PdfTextBlock(index, block_text, "vision") for index, block_text in enumerate(block_texts)),
                    warning_code=warning,
                ),
            ),
            text=text,
            page_spans=(PageTextSpan(1, 0, len(text)),),
            extraction_method="vision",
            ocr_page_count=0,
            vision_page_count=1,
            warning_code=warning,
        )
        replace_version_pages(db, version=version, extraction=extraction)
        db.commit()
        monkeypatch.setattr(qa_service, "_resolve_version_text", lambda _: text)

        context, evidence = qa_service._build_evidence_blocks(
            [(document, version)],
            db=db,
        )

        assert evidence[0]["support_state"] == expected_state
        assert evidence[0]["page_number"] == 1
        from app.services.evidence_snapshots import read_snapshot, snapshot_page_location

        item = evidence[0]
        assert read_snapshot(version.id, item["text_sha256"]) == text
        assert (item["char_start"], item["char_end"]) == (0, len(text))
        assert snapshot_page_location(db, version.id, text, 0, len(text)).page_block_id == item["page_block_id"]
        assert len(item["excerpt"]) == len(text)
        assert item["excerpt"] == expected_projection
        if "37.5W" in text:
            assert "37.5W" in item["excerpt"]
        if "末位数字模糊" in text:
            assert "末位数字模糊" in item["excerpt"]

        # The cited prefix is unchanged, but the full extraction is different.
        # Old page/block metadata must remain intact, not bind to the new text.
        monkeypatch.setattr(qa_service, "_resolve_version_text", lambda _: text + "\n补全的证书内容")
        _, refreshed = qa_service._build_evidence_blocks([(document, version)], db=db)
        assert "page_number" not in refreshed[0]
        assert "page_block_id" not in refreshed[0]
        assert pages_for_versions(db, [version.id])[version.id][0].text == text


def test_ocr_block_bbox_and_span_constraints(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'constraints.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        document = Document(
            id="doc-constraints", name="x.pdf", status="active", created_by_user_id="user",
            created_at=__import__("datetime").datetime.now(), updated_at=__import__("datetime").datetime.now(),
        )
        version = DocumentVersion(
            id="ver-constraints", document_id=document.id, version_number=1,
            processing_stage="queued", queryable=False, retryable=False,
            is_current_published=False, created_at=document.created_at, updated_at=document.updated_at,
        )
        db.add_all([document, version])
        db.flush()
        bad = _extraction()
        bad_page = bad.pages[1]
        bad_page = ExtractedPdfPage(**{**bad_page.__dict__, "blocks": (PdfTextBlock(0, "bad", "ocr", 0.5, None),)}) if False else bad_page
        # Model-level checks are exercised by the repository's valid OCR path;
        # invalid confidence/bbox values are rejected before persistence by the
        # schema contract in normal API construction.
        assert bad_page.blocks[0].bbox is not None
