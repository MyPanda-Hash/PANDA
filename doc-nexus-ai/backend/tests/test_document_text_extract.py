import base64
import sys
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from app.services import document_processor, pdf_ocr
from app.services.document_processor import (
    decode_text_bytes,
    extract_docx_text,
    extract_pdf_text,
    extract_pptx_text,
    extract_spreadsheet_text,
    extract_text_from_task_snapshot,
    extract_xlsx_text,
    validate_file,
    validate_pptx_archive,
)
from app.services.vision_fallback import VisionParseResult


def test_extract_pptx_text_reads_titles_and_body():
    """Titles, body paragraphs, and notes slide should all be captured."""
    from io import BytesIO

    from pptx import Presentation

    presentation = Presentation()
    title_layout = presentation.slide_layouts[1]  # title + content layout
    slide1 = presentation.slides.add_slide(title_layout)
    slide1.shapes.title.text = "项目概述"
    slide1.placeholders[1].text = "这是项目的第一段说明"

    slide2 = presentation.slides.add_slide(title_layout)
    slide2.shapes.title.text = "市场分析"
    slide2.placeholders[1].text = "市场规模达到 100 亿元"
    notes = slide2.notes_slide
    notes.notes_text_frame.text = "需要补充同业对比数据"

    buffer = BytesIO()
    presentation.save(buffer)
    text = extract_pptx_text(buffer.getvalue())

    assert "项目概述" in text
    assert "这是项目的第一段说明" in text
    assert "市场分析" in text
    assert "市场规模达到 100 亿元" in text
    assert "需要补充同业对比数据" in text


def test_extract_pptx_text_empty_returns_empty():
    assert extract_pptx_text(b"") == ""


def test_extract_pptx_text_invalid_raises_value_error():
    with pytest.raises(ValueError, match="无法解析 PPTX"):
        extract_pptx_text(b"not a real pptx file")


_PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _minimal_pptx_package(
    *,
    media: bytes | None = None,
    rooted_targets: bool = False,
) -> bytes:
    content_types = (
        f'<Types xmlns="{_CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/ppt/slides/slide1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        '<Override PartName="/ppt/notesSlides/notesSlide1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
        "</Types>"
    )
    package_target = (
        "/ppt/presentation.xml" if rooted_targets else "ppt/presentation.xml"
    )
    slide_target = "/ppt/slides/slide1.xml" if rooted_targets else "slides/slide1.xml"
    notes_target = (
        "/ppt/notesSlides/notesSlide1.xml"
        if rooted_targets
        else "../notesSlides/notesSlide1.xml"
    )
    package_rels = (
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_REL_NS}/officeDocument" '
        f'Target="{package_target}"/>'
        "</Relationships>"
    )
    presentation = (
        f'<p:presentation xmlns:p="{_PML_NS}" xmlns:r="{_OFFICE_REL_NS}">'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
        "</p:presentation>"
    )
    presentation_rels = (
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_REL_NS}/slide" '
        f'Target="{slide_target}"/>'
        "</Relationships>"
    )
    slide = (
        f'<p:sld xmlns:p="{_PML_NS}" xmlns:a="{_DRAWINGML_NS}">'
        "<p:cSld><p:spTree>"
        "<p:sp><p:txBody><a:bodyPr/><a:lstStyle/>"
        "<a:p><a:r><a:t>标题</a:t></a:r></a:p>"
        "<a:p><a:r><a:t>正文</a:t></a:r></a:p>"
        "</p:txBody></p:sp>"
        "<p:grpSp><p:sp><p:txBody><a:bodyPr/><a:lstStyle/>"
        "<a:p><a:r><a:t>组内文字</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:grpSp>"
        "<p:graphicFrame><a:graphic><a:graphicData>"
        "<a:tbl><a:tr><a:tc><a:txBody><a:bodyPr/><a:lstStyle/>"
        "<a:p><a:r><a:t>表格单元格</a:t></a:r></a:p>"
        "</a:txBody></a:tc></a:tr></a:tbl>"
        "</a:graphicData></a:graphic></p:graphicFrame>"
        "</p:spTree></p:cSld></p:sld>"
    )
    slide_rels = (
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_REL_NS}/notesSlide" '
        f'Target="{notes_target}"/>'
        "</Relationships>"
    )
    notes = (
        f'<p:notes xmlns:p="{_PML_NS}" xmlns:a="{_DRAWINGML_NS}">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/>"
        "<a:p><a:r><a:t>演讲备注</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)
        if media is not None:
            media_info = ZipInfo("ppt/media/embedded-video.mp4")
            media_info.compress_type = ZIP_STORED
            archive.writestr(media_info, media)
    return buffer.getvalue()


def test_extract_pptx_text_accepts_path_and_seekable_stream(tmp_path):
    payload = _minimal_pptx_package()
    path = tmp_path / "deck.pptx"
    path.write_bytes(payload)
    stream = BytesIO(payload)
    stream.seek(len(payload))

    path_text = extract_pptx_text(path)
    stream_text = extract_pptx_text(stream)

    assert stream_text == path_text
    assert stream.tell() == len(payload)
    assert path_text.index("标题") < path_text.index("正文")
    assert path_text.index("正文") < path_text.index("组内文字")
    assert path_text.index("组内文字") < path_text.index("表格单元格")
    assert "[备注] 演讲备注" in path_text


def test_extract_pptx_text_resolves_rooted_relationship_targets():
    payload = _minimal_pptx_package(rooted_targets=True)

    validate_pptx_archive(payload)
    text = extract_pptx_text(payload)

    assert "标题" in text
    assert "[备注] 演讲备注" in text


def test_extract_pptx_text_does_not_open_large_media(monkeypatch):
    payload = _minimal_pptx_package(media=b"video bytes")
    real_infolist = ZipFile.infolist
    real_open = ZipFile.open

    def fake_infolist(archive):
        infos = real_infolist(archive)
        for info in infos:
            if info.filename == "ppt/media/embedded-video.mp4":
                info.file_size = 250 * 1024 * 1024
        return infos

    def guarded_open(archive, name, *args, **kwargs):
        member_name = name.filename if isinstance(name, ZipInfo) else name
        if str(member_name).startswith("ppt/media/"):
            raise AssertionError("PPTX media must not be opened")
        return real_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "infolist", fake_infolist)
    monkeypatch.setattr(ZipFile, "open", guarded_open)

    text = extract_pptx_text(payload)

    assert "标题" in text
    assert "演讲备注" in text


def test_validate_pptx_archive_rejects_random_zip_and_corrupt_archive():
    random_buffer = BytesIO()
    with ZipFile(random_buffer, "w") as archive:
        archive.writestr("not-a-presentation.txt", b"random")
    with pytest.raises(ValueError, match="PPTX"):
        validate_pptx_archive(random_buffer.getvalue())

    payload = _minimal_pptx_package()
    with pytest.raises(ValueError, match="PPTX"):
        validate_pptx_archive(payload[:-12])


def test_validate_pptx_archive_rejects_unsafe_duplicate_and_bomb_members(
    monkeypatch,
):
    unsafe_buffer = BytesIO()
    with ZipFile(unsafe_buffer, "w") as archive:
        archive.writestr("../escape.txt", b"unsafe")
    with pytest.raises(ValueError, match="不安全"):
        validate_pptx_archive(unsafe_buffer.getvalue())

    duplicate_buffer = BytesIO()
    with ZipFile(duplicate_buffer, "w") as archive:
        archive.writestr("duplicate.txt", b"one")
        archive.writestr("duplicate.txt", b"two")
    with pytest.raises(ValueError, match="重复"):
        validate_pptx_archive(duplicate_buffer.getvalue())

    payload = _minimal_pptx_package()
    real_infolist = ZipFile.infolist

    def bomb_infolist(archive):
        infos = real_infolist(archive)
        for info in infos:
            if info.filename == "ppt/presentation.xml":
                info.file_size = 10 * 1024 * 1024
                info.compress_size = 1
        return infos

    monkeypatch.setattr(ZipFile, "infolist", bomb_infolist)
    with pytest.raises(ValueError, match="压缩比例"):
        validate_pptx_archive(payload)


@pytest.mark.parametrize("suffix", [".ppt", ".pptx", ".potx"])
def test_presentation_attachment_validation_preserves_upload_scope_and_size(suffix):
    filename = f"template{suffix}"
    assert (validate_file(filename, 1) is None) is (suffix == ".pptx")
    assert validate_file(
        filename, 100, max_bytes=100, allow_presentation_templates=True
    ) is None
    assert validate_file(
        filename, 101, max_bytes=100, allow_presentation_templates=True
    ) is not None


def test_potx_attachment_reader_requires_extracted_text(tmp_path):
    from app.services import qa_service

    source = tmp_path / "theme.potx"
    source.write_bytes(_minimal_pptx_package())
    assert qa_service._read_document_text(str(source)) is None
    document_processor.write_extracted_sidecar(str(source), "经过提取的演示文字")
    assert qa_service._read_document_text(str(source)) == "经过提取的演示文字"


@pytest.mark.parametrize("suffix", [".pptx", ".xls"])
def test_minio_cached_text_is_read_offline_without_changing_offsets(monkeypatch, tmp_path, suffix):
    from app.services import minio_storage, qa_service

    monkeypatch.setattr(document_processor.settings, "upload_dir", tmp_path)
    uri = f"minio://dgmeta/document-spaces/space-1/source{suffix}"
    text = "  证书编号：A123\r\n有效期：2028年  \n"
    document_processor.write_extracted_sidecar(uri, text)

    def network_forbidden(*args, **kwargs):
        raise AssertionError("The shared reader must not connect to object storage")

    monkeypatch.setattr(minio_storage, "load_minio_settings", network_forbidden)
    assert qa_service._read_document_text(uri) == text
    assert qa_service._read_document_text(uri + ".missing") is None


def test_snapshot_citations_survive_sidecar_refresh_without_promoting_legacy(monkeypatch, tmp_path):
    from app.models.documents import Document, DocumentVersion
    from app.services import evidence_snapshots, qa_service

    monkeypatch.setattr(document_processor.settings, "upload_dir", tmp_path)
    source = tmp_path / "certificate.pdf"
    source.write_bytes(b"unchanged original document bytes")
    old_text = "  证书编号：A123\r\n有效期：2028年  \n"
    new_text = "新增抬头\n证书编号：A123\n有效期：2028年\n"
    version = DocumentVersion(id="dv_snapshot", file_path=str(source), version_number=1)
    document = Document(id="doc_snapshot", name="资格证书")
    document_processor.write_extracted_sidecar(str(source), old_text, version_id=version.id)
    _, old_evidence = qa_service._build_evidence_blocks([(document, version)])
    citation = old_evidence[0]

    document_processor.write_extracted_sidecar(str(source), new_text, version_id=version.id)
    _, new_evidence = qa_service._build_evidence_blocks([(document, version)])
    parsed = evidence_snapshots.parse_citation_id(citation["id"])
    assert parsed is not None
    version_id, digest, start, end = parsed
    assert digest is not None
    original = evidence_snapshots.read_snapshot(version_id, digest)
    assert original == old_text
    assert original[start:end] == citation["excerpt"]
    assert new_evidence[0]["id"] != citation["id"]
    assert qa_service._read_document_text(str(source)) == new_text
    assert source.read_bytes() == b"unchanged original document bytes"
    assert evidence_snapshots.parse_citation_id(f"writing-evidence:{version.id}:0-8") == (version.id, None, 0, 8)
    assert evidence_snapshots.parse_citation_id(f"vector-evidence:{version.id}:0-8") == (version.id, None, 0, 8)
    assert qa_service._parse_citation_coverage(f"证书信息[{citation['id']}]", {citation["id"]})["cited_in_answer"] == 1
    document_processor.write_extracted_sidecar(str(source), "不得替换无版本缓存")
    assert qa_service._read_document_text(str(source)) == new_text


def test_corrupt_snapshot_is_unavailable_and_cannot_be_overwritten(monkeypatch, tmp_path):
    from app.services import evidence_snapshots

    monkeypatch.setattr(document_processor.settings, "upload_dir", tmp_path)
    digest = evidence_snapshots.freeze_text("dv_integrity", "历史正文\r\n")
    target = tmp_path / "_evidence_snapshots" / "dv_integrity" / f"{digest}.txt"
    target.write_bytes("另一份正文".encode("utf-8"))

    assert evidence_snapshots.read_snapshot("dv_integrity", digest) is None
    with pytest.raises(OSError):
        evidence_snapshots.freeze_text("dv_integrity", "历史正文\r\n")
    assert target.read_bytes() == "另一份正文".encode("utf-8")
    assert evidence_snapshots.read_snapshot("../outside", digest) is None
    with pytest.raises(ValueError):
        evidence_snapshots.freeze_text("../outside", "任意正文")


def test_evidence_audit_is_read_only_until_explicit_apply(monkeypatch, tmp_path, capsys):
    import json
    import runpy
    import sqlite3
    from pathlib import Path

    monkeypatch.setattr(document_processor.settings, "upload_dir", tmp_path)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"original pdf bytes")
    text = "未绑定历史引用的当前提取文本\r\n"
    sidecar = document_processor.write_extracted_sidecar(str(source), text)
    database = tmp_path / "offline-copy.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE document_versions (id TEXT, file_path TEXT, processing_stage TEXT, queryable INTEGER)")
        db.execute("INSERT INTO document_versions VALUES (?, ?, 'ready', 1)", ("dv_audit", str(source)))
    before_database = database.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_evidence_snapshots.py"
    main = runpy.run_path(str(script))["main"]
    argv = ["--database-copy", str(database), "--upload-dir", str(tmp_path)]

    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[0])
    assert report["read_state"] == "readable"
    assert report["legacy_binding_state"] == "unverified"
    assert report["snapshots"] == []
    assert not (tmp_path / "_evidence_snapshots").exists()
    assert database.read_bytes() == before_database

    assert main([*argv, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out.splitlines()[0])
    assert applied["snapshots"] == [{"text_sha256": report["current_text_sha256"], "state": "verified"}]
    assert applied["legacy_binding_state"] == "unverified"
    assert sidecar.read_bytes() == text.encode("utf-8")
    assert source.read_bytes() == b"original pdf bytes"
    assert database.read_bytes() == before_database
    monkeypatch.setattr(document_processor.settings, "database_url", f"sqlite:///{database}")
    with pytest.raises(SystemExit) as refusal:
        main(["--configured-database", "--upload-dir", str(tmp_path)])
    assert refusal.value.code == 2
    assert database.read_bytes() == before_database


def test_evidence_source_fetch_requires_opt_in_and_bounds_stream(monkeypatch, tmp_path):
    import runpy
    import tempfile
    from pathlib import Path
    from app.services import minio_storage

    monkeypatch.setattr(document_processor.settings, "upload_dir", tmp_path)
    monkeypatch.setattr(document_processor.settings, "minio_enabled", "1")
    monkeypatch.setattr(document_processor.settings, "minio_url", "http://example.invalid")
    monkeypatch.setattr(document_processor.settings, "minio_access_key", "test-key")
    monkeypatch.setattr(document_processor.settings, "minio_secret_key", "test-secret")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_evidence_snapshots.py"
    audit = runpy.run_path(str(script))["_audit_version"]
    uri = "minio://private-bucket/secret-object.pdf"
    old_text = "原先提取文本"
    sidecar = document_processor.write_extracted_sidecar(uri, old_text)
    row = {"id": "dv_fetch", "file_path": uri, "processing_stage": "ready", "queryable": True}
    options = {"upload_dir": tmp_path, "source_root": tmp_path, "apply": False, "reextract": True, "max_source_bytes": 4}

    def network_forbidden(*args, **kwargs):
        raise AssertionError("Fetching requires an explicit flag")

    monkeypatch.setattr(minio_storage, "_client", network_forbidden)
    denied = audit(row, **options)
    assert denied["refresh_state"] == "source_fetch_not_authorized"
    assert "private-bucket" not in denied["path"]

    class Response(BytesIO):
        released = False

        def release_conn(self):
            self.released = True

    response = Response(b"12345")
    client = SimpleNamespace(
        stat_object=lambda *args: SimpleNamespace(size=4),
        get_object=lambda *args: response,
    )
    monkeypatch.setattr(minio_storage, "_client", lambda _: client)
    files_before = set(tmp_path.iterdir())
    bounded = audit(row, fetch_source=True, **options)
    assert bounded["refresh_state"] == "source_too_large"
    assert response.closed and response.released
    assert set(tmp_path.iterdir()) == files_before
    assert sidecar.read_bytes() == old_text.encode("utf-8")


def test_extract_pdf_text_reads_text_layer():
    """A minimal hand-crafted PDF with text should be parsed back to that text."""
    import zlib

    text_payload = b"BT /F1 12 Tf 100 700 Td (Hello PDF World) Tj ET"
    compressed = zlib.compress(text_payload)

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")  # 1
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")  # 2
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )  # 3
    objects.append(
        b"<< /Length "
        + str(len(compressed)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + compressed
        + b"\nendstream"
    )  # 4
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # 5

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets: list[int] = []
    body = b""
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(header) + len(body))
        body += f"{index} 0 obj\n".encode() + payload + b"\nendobj\n"

    xref_offset = len(header) + len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    )

    pdf_bytes = header + body + xref.encode() + trailer.encode()
    text = extract_pdf_text(pdf_bytes)
    assert "Hello PDF World" in text


def test_extract_pdf_text_empty_returns_empty():
    assert extract_pdf_text(b"") == ""


def test_extract_pdf_text_invalid_raises_value_error():
    with pytest.raises(ValueError, match="无法解析 PDF"):
        extract_pdf_text(b"not a real pdf file")


def test_pdf_page_vision_fallback_returns_untrusted_text(monkeypatch):
    endpoint = object()
    monkeypatch.setattr(pdf_ocr.settings, "vision_fallback_enabled", True)

    def fake_parse_images_with_vision(*_args, **_kwargs):
        return VisionParseResult(
            endpoint_id="vision-endpoint",
            provider="vision-provider",
            model="vision-model",
            context="【视觉辅助结果｜不可信证据】",
            usage={"total_tokens": 5, "cost": "0"},
            latency_ms=10,
            content_hash="hash",
            items=(
                {
                    "index": "1",
                    "visible_text": "发票号 A-001",
                    "observations": "页面中有一张发票",
                    "uncertainty": "金额不确定",
                },
            ),
            reservation_id="bres-pdf-page",
        )

    monkeypatch.setattr(
        pdf_ocr, "parse_images_with_vision", fake_parse_images_with_vision
    )

    page = pdf_ocr.parse_page_with_vision(
        b"rendered-page",
        endpoint,
        page_number=3,
        user_id="user-1",
        operation_id="version-1",
    )

    assert page is not None
    assert page.extraction_method == "vision"
    assert page.blocks[0].source == "vision"
    assert "发票号 A-001" in page.text
    assert page.vision_reservation_id == "bres-pdf-page"


def test_chat_image_ocr_fallback_returns_bounded_untrusted_context(monkeypatch):
    from PIL import Image

    image_buffer = BytesIO()
    Image.new("RGB", (20, 20), "white").save(image_buffer, format="PNG")
    monkeypatch.setattr(pdf_ocr.settings, "ocr_enabled", True)
    monkeypatch.setattr(pdf_ocr.settings, "vision_max_images_per_message", 4)
    monkeypatch.setattr(
        pdf_ocr,
        "_run_tesseract",
        lambda _png: (
            (pdf_ocr.PdfTextBlock(0, "编号 A-001", "ocr", 0.9, (0, 0, 1, 1)),),
            20,
            20,
        ),
    )

    context = pdf_ocr.parse_images_with_ocr(
        [
            {
                "data": base64.b64encode(image_buffer.getvalue()).decode("ascii"),
                "mimeType": "image/png",
            }
        ]
    )

    assert "编号 A-001" in context
    assert "不可信证据" in context


def test_extract_pdf_pages_caps_vision_fallback_at_eight_pages(monkeypatch):
    pages = [SimpleNamespace(extract_text=lambda: "") for _ in range(9)]
    fake_pdf = SimpleNamespace(PdfReader=lambda _stream: SimpleNamespace(pages=pages))
    monkeypatch.setitem(sys.modules, "pypdf", fake_pdf)
    monkeypatch.setattr(pdf_ocr.settings, "ocr_enabled", True)
    monkeypatch.setattr(pdf_ocr.settings, "vision_fallback_enabled", True)
    monkeypatch.setattr(pdf_ocr.settings, "ocr_max_pages", 20)

    class FakePdfium:
        def __getitem__(self, _index):
            return object()

        def close(self):
            return None

    monkeypatch.setattr(pdf_ocr, "_pdfium_document", lambda _content: FakePdfium())
    monkeypatch.setattr(pdf_ocr, "_page_geometry", lambda _page: (612.0, 792.0, 0))
    monkeypatch.setattr(
        pdf_ocr, "render_pdf_page", lambda *_args, **_kwargs: b"rendered"
    )
    monkeypatch.setattr(pdf_ocr, "_run_tesseract", lambda _png: ((), 100, 100))
    calls: list[int] = []

    def fake_page(*_args, **kwargs):
        page_number = int(kwargs["page_number"])
        calls.append(page_number)
        return pdf_ocr.ExtractedPdfPage(
            page_number=page_number,
            width_points=612,
            height_points=792,
            rotation=0,
            extraction_method="vision",
            text=f"视觉页 {page_number}",
            quality_score=0.5,
            average_confidence=None,
            blocks=(pdf_ocr.PdfTextBlock(0, f"视觉页 {page_number}", "vision"),),
        )

    monkeypatch.setattr(pdf_ocr, "parse_page_with_vision", fake_page)
    result = pdf_ocr.extract_pdf_pages(
        b"pdf",
        vision_endpoint=object(),
        user_id="user-1",
        operation_id="version-1",
    )
    assert calls == list(range(1, 9))
    assert result.vision_page_count == 8
    assert result.pages[7].extraction_method == "vision"
    assert result.pages[8].warning_code == "vision_page_limit_exceeded"
    snapshot = {
        "status": "COMPLETED",
        "stages": [
            {
                "name": "知识片段入库",
                "data": [
                    {"index": 0, "content": "第一段公司介绍", "status": "COMPLETED"},
                    {"index": 1, "content": "第二段案例说明", "status": "FAILED"},
                    {"index": 2, "content": "第一段公司介绍", "status": "COMPLETED"},
                ],
            }
        ],
    }
    text = extract_text_from_task_snapshot(snapshot)
    assert "第一段公司介绍" in text
    assert "第二段案例说明" in text
    assert text.count("第一段公司介绍") == 1


def test_extract_pdf_pages_shares_vision_token_budget_across_pages(monkeypatch):
    pages = [SimpleNamespace(extract_text=lambda: "") for _ in range(3)]
    fake_pdf = SimpleNamespace(PdfReader=lambda _stream: SimpleNamespace(pages=pages))
    monkeypatch.setitem(sys.modules, "pypdf", fake_pdf)
    monkeypatch.setattr(pdf_ocr.settings, "ocr_enabled", True)
    monkeypatch.setattr(pdf_ocr.settings, "vision_fallback_enabled", True)
    monkeypatch.setattr(pdf_ocr.settings, "ocr_max_pages", 20)
    monkeypatch.setattr(pdf_ocr.settings, "vision_max_total_tokens", 5)

    class FakePdfium:
        def __getitem__(self, _index):
            return object()

        def close(self):
            return None

    monkeypatch.setattr(pdf_ocr, "_pdfium_document", lambda _content: FakePdfium())
    monkeypatch.setattr(pdf_ocr, "_page_geometry", lambda _page: (612.0, 792.0, 0))
    monkeypatch.setattr(
        pdf_ocr, "render_pdf_page", lambda *_args, **_kwargs: b"rendered"
    )
    monkeypatch.setattr(pdf_ocr, "_run_tesseract", lambda _png: ((), 100, 100))
    calls: list[int] = []

    def fake_page(*_args, **kwargs):
        page_number = int(kwargs["page_number"])
        calls.append(page_number)
        return pdf_ocr.ExtractedPdfPage(
            page_number=page_number,
            width_points=612,
            height_points=792,
            rotation=0,
            extraction_method="vision",
            text=f"视觉页 {page_number}",
            quality_score=0.5,
            average_confidence=None,
            blocks=(pdf_ocr.PdfTextBlock(0, f"视觉页 {page_number}", "vision"),),
            vision_usage={"total_tokens": 5, "cost": "0"},
        )

    monkeypatch.setattr(pdf_ocr, "parse_page_with_vision", fake_page)
    result = pdf_ocr.extract_pdf_pages(
        b"pdf",
        vision_endpoint=object(),
        user_id="user-1",
        operation_id="version-1",
    )

    assert calls == [1]
    assert result.vision_page_count == 1
    assert result.pages[1].warning_code == "vision_budget_exceeded"
    assert result.vision_calls[-1]["error_code"] == "vision_budget_exceeded"


def test_extract_text_from_task_snapshot_empty():
    assert extract_text_from_task_snapshot(None) == ""
    assert extract_text_from_task_snapshot({"stages": []}) == ""


def _minimal_docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        "<w:p>"
        + "".join(f"<w:r><w:t>{text}</w:t></w:r>" for text in [paragraph])
        + "</w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_extract_docx_text_joins_paragraph_runs():
    payload = _minimal_docx(["问：居民户口登记分为哪两类？", "答：家庭户和集体户。"])
    text = extract_docx_text(payload)
    assert "问：居民户口登记分为哪两类？" in text
    assert "答：家庭户和集体户。" in text


def test_extract_docx_text_rejects_xml_entities():
    payload = _minimal_docx(["&unsafe;"])
    source = ZipFile(BytesIO(payload))
    entries = {name: source.read(name) for name in source.namelist()}
    source.close()
    document = entries["word/document.xml"].decode()
    document = document.replace(
        "<w:document",
        '<!DOCTYPE w:document [<!ENTITY unsafe "expanded">]><w:document',
        1,
    )
    entries["word/document.xml"] = document.encode()
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)

    with pytest.raises(ValueError):
        extract_docx_text(buffer.getvalue())


def test_extract_docx_text_rejects_excessive_expanded_archive(monkeypatch):
    monkeypatch.setattr(document_processor, "_MAX_OFFICE_ARCHIVE_BYTES", 1)

    with pytest.raises(ValueError, match="解压后超过安全限制"):
        extract_docx_text(_minimal_docx(["正常内容"]))


def test_extract_docx_text_supports_real_world_sample():
    from pathlib import Path

    sample = Path("/Users/yychang/Documents/项目配置/天津/天津问答对.docx")
    if not sample.exists():
        return
    text = extract_docx_text(sample.read_bytes())
    assert "居民户口登记" in text
    assert len(text) > 1000


def _minimal_xlsx(*, sheet_name: str, rows: list[list[str]]) -> bytes:
    strings: list[str] = []
    sheet_cells: list[list[str]] = []
    for row in rows:
        refs: list[str] = []
        for value in row:
            refs.append(str(len(strings)))
            strings.append(value)
        sheet_cells.append(refs)

    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    sheet_rows = []
    for row_idx, refs in enumerate(sheet_cells, start=1):
        cells = "".join(
            f'<c r="{chr(65 + col_idx)}{row_idx}" t="s"><v>{ref}</v></c>'
            for col_idx, ref in enumerate(refs)
        )
        sheet_rows.append(f'<row r="{row_idx}">{cells}</row>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{shared}</sst>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def test_extract_xlsx_text_renders_chinese_sheet_as_markdown():
    payload = _minimal_xlsx(
        sheet_name="人员",
        rows=[["姓名", "部门"], ["张三", "研发"]],
    )
    text = extract_xlsx_text(payload)
    assert "人员" in text
    assert "张三" in text
    assert "研发" in text
    assert "\ufffd" not in text


def test_extract_xlsx_text_renders_far_sparse_cells_without_dense_allocation():
    payload = _minimal_xlsx(sheet_name="稀疏表", rows=[["起点"]])
    source = ZipFile(BytesIO(payload))
    entries = {name: source.read(name) for name in source.namelist()}
    source.close()
    sheet = entries["xl/worksheets/sheet1.xml"].decode()
    sheet = sheet.replace(
        "</sheetData>",
        '<row r="1000"><c r="ZZ1000" t="inlineStr"><is><t>终点</t></is></c></row>'
        "</sheetData>",
    )
    entries["xl/worksheets/sheet1.xml"] = sheet.encode()
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)

    text = extract_xlsx_text(buffer.getvalue())

    assert "起点" in text
    assert "ZZ1000" in text
    assert "终点" in text
    assert len(text) < 10_000


def test_extract_spreadsheet_text_decodes_gbk_html_xls_export():
    html = (
        "<html><head><meta http-equiv='Content-Type' content='text/html; charset=gb2312'>"
        "</head><body><table><tr><td>姓名</td><td>部门</td></tr>"
        "<tr><td>张三</td><td>研发</td></tr></table></body></html>"
    )
    payload = html.encode("gb2312")
    garbled = payload.decode("utf-8", errors="replace")
    assert "张三" not in garbled or "\ufffd" in garbled
    text = extract_spreadsheet_text(payload, ".xls")
    assert "张三" in text
    assert "研发" in text
    assert "\ufffd" not in text


def test_decode_text_bytes_supports_gbk_csv():
    payload = "姓名,部门\n张三,研发\n".encode("gbk")
    assert "张三" not in payload.decode(
        "utf-8", errors="replace"
    ) or "\ufffd" in payload.decode("utf-8", errors="replace")
    assert decode_text_bytes(payload) == "姓名,部门\n张三,研发\n"


def test_extract_spreadsheet_text_reads_spreadsheetml_xml():
    xml = (
        '<?xml version="1.0" encoding="GB2312"?>'
        '<?mso-application progid="Excel.Sheet"?>'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet ss:Name="清单"><Table>'
        '<Row><Cell><Data ss:Type="String">项目</Data></Cell>'
        '<Cell><Data ss:Type="String">金额</Data></Cell></Row>'
        '<Row><Cell><Data ss:Type="String">合同</Data></Cell>'
        '<Cell><Data ss:Type="Number">100</Data></Cell></Row>'
        "</Table></Worksheet></Workbook>"
    )
    payload = xml.encode("gb2312")
    text = extract_spreadsheet_text(payload, ".xls")
    assert "合同" in text
    assert "金额" in text
    assert "\ufffd" not in text


def test_utf8_fallback_does_not_pass_as_spreadsheet_preview():
    payload = _minimal_xlsx(sheet_name="清单", rows=[["项目"], ["合同金额"]])
    garbled = payload.decode("utf-8", errors="replace")
    assert "合同金额" not in garbled
    text = extract_spreadsheet_text(payload, ".xlsx")
    assert "合同金额" in text


def test_extract_xls_text_reads_biff_chinese():
    from pathlib import Path

    from app.services.document_processor import extract_xls_text

    sample = Path(__file__).parent / "fixtures" / "chinese_sheet.xls"
    payload = sample.read_bytes()
    garbled = payload.decode("utf-8", errors="replace")
    assert "张三" not in garbled
    text = extract_xls_text(payload)
    assert "人员" in text
    assert "张三" in text
    assert "研发" in text
    assert "\ufffd" not in text


def test_extract_doc_text_reads_legacy_binary_chinese():
    """A real Word 97-2003 .doc (OLE2) should decode to its visible text."""
    from pathlib import Path

    from app.services.document_processor import extract_doc_text

    sample = Path(__file__).parent / "fixtures" / "chinese_doc.doc"
    payload = sample.read_bytes()
    garbled = payload.decode("utf-8", errors="replace")
    assert "75寸立式" not in garbled
    text = extract_doc_text(payload)
    assert "75寸立式红外触摸x86一体机规格书" in text
    assert "3840×2160" in text
    assert "触控方式" in text
    assert "\ufffd" not in text


def test_extract_doc_text_empty_returns_empty():
    from app.services.document_processor import extract_doc_text

    assert extract_doc_text(b"") == ""


def test_extract_doc_text_invalid_raises_value_error():
    from app.services.document_processor import extract_doc_text

    with pytest.raises(ValueError, match="不是有效的 Word"):
        extract_doc_text(b"this is plain text, not an OLE file")


def test_extract_doc_text_accepts_libreoffice_generated_doc():
    """Non-Microsoft writers still produce valid OLE2 .doc that must parse."""
    from pathlib import Path

    from app.services.document_processor import extract_doc_text

    sample = Path(__file__).parent / "fixtures" / "libreoffice_doc.doc"
    text = extract_doc_text(sample.read_bytes())
    assert "Simple doc from Libre Writer" in text
    assert "\ufffd" not in text


def _raster_bytes(format="PNG", *, size=(40, 24), frames=1, **save_kwargs):
    from PIL import Image
    if format == "HEIF":
        from pillow_heif import register_heif_opener
        register_heif_opener()

    images = [Image.new("RGB", size, (index * 25, 90, 160)) for index in range(frames)]
    output = BytesIO()
    try:
        if frames > 1:
            save_kwargs.update(save_all=True, append_images=images[1:])
        images[0].save(output, format=format, **save_kwargs)
        return output.getvalue()
    finally:
        for image in images:
            image.close()


@pytest.mark.parametrize("format,extension", [
    ("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("GIF", "gif"),
    ("BMP", "bmp"), ("TIFF", "tiff"), ("JPEG2000", "jp2"), ("ICO", "ico"),
    ("PCX", "pcx"), ("TGA", "tga"), ("PPM", "ppm"), ("AVIF", "avif"),
    ("HEIF", "heic"),
])
def test_image_document_decodes_common_formats_and_outputs_clean_png(format, extension, tmp_path):
    from PIL import Image
    from app.services.image_documents import render_image_page, validate_image_document

    original = _raster_bytes(format)
    path = tmp_path / f"original.{extension}"
    path.write_bytes(original)
    assert validate_image_document(path, path.name) == 1
    normalized = render_image_page(original, 1, filename=path.name)
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "PNG"
        assert not image.getexif()
    assert path.read_bytes() == original


def test_image_document_normalizes_exif_orientation_without_modifying_original():
    from PIL import Image
    from app.services.image_documents import render_image_page, validate_image_document

    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private original metadata"
    original = _raster_bytes("JPEG", size=(60, 24), exif=exif)
    assert validate_image_document(original, "rotated.jpeg") == 1
    with Image.open(BytesIO(render_image_page(original, 1, filename="rotated.jpeg"))) as preview:
        assert preview.size == (24, 60)
        assert not preview.getexif()
    with Image.open(BytesIO(original)) as source:
        assert source.size == (60, 24)
        assert source.getexif()[274] == 6


@pytest.mark.parametrize("format,extension", [("GIF", "gif"), ("TIFF", "tiff"), ("PNG", "apng"), ("WEBP", "webp")])
def test_image_document_handles_every_frame_and_rejects_excess(format, extension):
    from app.services.image_documents import (
        ImageDocumentError, render_image_page, validate_image_document,
    )

    original = _raster_bytes(format, frames=3)
    assert validate_image_document(original, f"animation.{extension}") == 3
    assert render_image_page(original, 1, filename=f"a.{extension}") != render_image_page(
        original, 3, filename=f"a.{extension}"
    )
    with pytest.raises(ImageDocumentError) as captured:
        validate_image_document(_raster_bytes(format, frames=9), f"large.{extension}")
    assert captured.value.code == "image_page_limit_exceeded"


@pytest.mark.parametrize("case,code", [
    ("mismatch", "image_format_mismatch"), ("corrupt", "image_invalid"),
    ("svg", "image_format_unsupported"), ("pixels", "image_pixel_limit_exceeded"),
    ("total_pixels", "image_pixel_limit_exceeded"), ("bytes", "image_too_large"),
])
def test_image_document_rejects_invalid_or_unbounded_sources(case, code, monkeypatch):
    from app.services import image_documents

    content, filename = _raster_bytes(), "source.png"
    if case == "mismatch":
        filename = "source.jpg"
    elif case == "corrupt":
        content = content[:50]
    elif case == "svg":
        filename, content = "source.svg", b"<svg/>"
    elif case == "pixels":
        monkeypatch.setattr(image_documents, "MAX_IMAGE_PIXELS", 100)
    elif case == "total_pixels":
        content, filename = _raster_bytes("TIFF", frames=2), "source.tiff"
        monkeypatch.setattr(image_documents, "MAX_TOTAL_IMAGE_PIXELS", 1_000)
    elif case == "bytes":
        monkeypatch.setattr(image_documents, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(image_documents.ImageDocumentError) as captured:
        image_documents.validate_image_document(content, filename)
    assert captured.value.code == code


def _image_vision_result(*, visible_text="合同金额：100元", observations="", uncertainty="", reservation="reservation"):
    return VisionParseResult(
        endpoint_id="vision", provider="test", model="visual", context="",
        usage={"total_tokens": 20, "input_tokens": 12, "output_tokens": 8, "cost": "0.01"},
        latency_ms=1, content_hash="test",
        items=({"index": "1", "visible_text": visible_text,
                "tables": "", "observations": observations, "uncertainty": uncertainty},),
        reservation_id=reservation,
    )


def test_image_document_extracts_every_page_with_source_linkage_without_ocr(monkeypatch):
    from app.services import image_documents, vision_fallback

    calls = []
    monkeypatch.setattr(pdf_ocr, "extract_pdf_pages", lambda *_a, **_k: pytest.fail("PDF processing is forbidden"))

    def parse(endpoint, images, **kwargs):
        calls.append(kwargs)
        assert kwargs["document_mode"] is True
        assert kwargs["space_ids"] == ("folder",)
        return _image_vision_result(
            observations="红色印章", uncertainty="印章文字无法确认",
            reservation=f"reservation-{len(calls)}",
        )

    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    result = image_documents.extract_image_pages(
        _raster_bytes("TIFF", frames=3), filename="three.tif",
        vision_endpoint=object(), user_id="owner", operation_id="operation", space_ids=("folder",),
    )
    assert len(calls) == result.vision_page_count == len(result.pages) == 3
    assert result.ocr_page_count == 0
    assert result.extraction_method == "vision"
    assert result.warning_code == "image_vision_unclear"
    assert "【可见文字】" in result.text and "【视觉描述（非原文）】" in result.text
    assert "【不确定性（不可作确定事实）】" in result.text
    assert [call["page_number"] for call in result.vision_calls] == [1, 2, 3]
    for page, span in zip(result.pages, result.page_spans):
        assert result.text[span.char_start:span.char_end] == page.text
        assert page.average_confidence is None
        assert all(block.bbox is None and block.source == "vision" for block in page.blocks)


def test_image_document_retains_invalid_page_usage_and_reuses_completed_pages(monkeypatch):
    from app.services import image_documents, vision_fallback
    from app.services.vision_fallback import VisionFallbackError

    calls, checkpoints = [], []

    def parse(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise VisionFallbackError(
                "vision_output_truncated", usage={"total_tokens": 31, "cost": "0.02"},
                reservation_id="reservation-failed",
            )
        return _image_vision_result(reservation=f"reservation-{len(calls)}")

    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    content = _raster_bytes("TIFF", frames=2)
    result = image_documents.extract_image_pages(
        content, filename="two.tiff", vision_endpoint=object(), user_id="owner",
        operation_id="operation", checkpoint=checkpoints.append,
    )
    assert result.warning_code == "image_vision_partial"
    assert result.pages[1].text == ""
    assert sum(call["total_tokens"] for call in result.vision_calls) == 51
    assert "reservation-failed" in result.vision_reservation_ids
    assert checkpoints[-1] == result
    recovered = image_documents.extract_image_pages(
        content, filename="two.tiff", vision_endpoint=object(), user_id="owner",
        operation_id="retry", cached=result,
    )
    assert len(calls) == 3
    assert recovered.vision_page_count == 2
    assert recovered.warning_code is None
    assert all(page.warning_code is None for page in recovered.pages)
    assert len(recovered.vision_calls) == 3
    assert recovered.pages[0].text == result.pages[0].text


@pytest.mark.parametrize("uncertainty,warning", [
    ("", None), ("印章文字无法辨认", "image_vision_unclear"),
])
def test_legacy_image_quality_cache_reuses_paid_result(monkeypatch, uncertainty, warning):
    from dataclasses import replace
    from app.services import image_documents, vision_fallback

    monkeypatch.setattr(
        vision_fallback, "parse_images_with_vision",
        lambda *_a, **_k: _image_vision_result(uncertainty=uncertainty),
    )
    content = _raster_bytes()
    result = image_documents.extract_image_pages(
        content, filename="source.png", vision_endpoint=object(), user_id="owner", operation_id="first",
    )
    cached = replace(
        result, warning_code="image_vision_uncertain",
        pages=(replace(result.pages[0], warning_code="image_vision_uncertain"),),
    )
    monkeypatch.setattr(
        vision_fallback, "parse_images_with_vision",
        lambda *_a, **_k: pytest.fail("旧质量标记不能触发重复付费识图"),
    )
    recovered = image_documents.extract_image_pages(
        content, filename="source.png", vision_endpoint=None,
        user_id="owner", operation_id="retry", cached=cached,
    )
    assert recovered.warning_code == recovered.pages[0].warning_code == warning
    assert recovered.text == result.text
    assert recovered.vision_calls == result.vision_calls


@pytest.mark.parametrize("uncertainty", ["", "全部文字模糊，无法确认"])
def test_image_document_blank_or_uncertain_only_is_not_searchable(monkeypatch, uncertainty):
    from app.services import image_documents, vision_fallback

    calls = []
    def parse(*_a, **_k):
        calls.append(1)
        return _image_vision_result(visible_text="", uncertainty=uncertainty)
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    content = _raster_bytes()
    result = image_documents.extract_image_pages(
        content, filename="blank.png", vision_endpoint=object(), user_id="owner", operation_id="blank",
    )
    assert not result.text.strip()
    assert result.warning_code == "image_vision_no_content"
    assert result.vision_calls[0]["total_tokens"] == 20
    image_documents.extract_image_pages(
        content, filename="blank.png", vision_endpoint=object(), user_id="owner",
        operation_id="blank-retry", cached=result,
    )
    assert calls == [1]


def test_image_document_tiles_long_page_with_overlap_and_checkpoint_before_next_call(monkeypatch):
    from app.services import image_documents, vision_fallback

    calls, checkpoints = [], []
    def parse(*_args, **kwargs):
        if calls:
            assert len(checkpoints) == len(calls)
        calls.append(kwargs)
        return _image_vision_result(visible_text=f"分区{len(calls)}完整内容")
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    result = image_documents.extract_image_pages(
        _raster_bytes(size=(200, 3300)), filename="long.png", vision_endpoint=object(),
        user_id="owner", operation_id="long", checkpoint=checkpoints.append,
    )
    assert len(calls) == 3
    assert all("核心区域" in call["document_context"] for call in calls)
    assert [call["crop_bounds"][1] for call in result.vision_calls] == [0, 1472, 3008]
    assert "分区1完整内容" in result.text and "分区3完整内容" in result.text


def test_image_document_checkpoint_failure_stops_before_next_paid_call(monkeypatch):
    from app.services import image_documents, vision_fallback

    calls = []
    def parse(*_a, **_k):
        calls.append(1)
        return _image_vision_result()
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    def checkpoint(_result):
        raise RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        image_documents.extract_image_pages(
            _raster_bytes("TIFF", frames=2), filename="two.tiff", vision_endpoint=object(),
            user_id="owner", operation_id="checkpoint", checkpoint=checkpoint,
        )
    assert calls == [1]


def test_image_document_rejects_corruption_in_later_frame_before_provider_call(monkeypatch):
    from app.services import image_documents, vision_fallback

    content = _raster_bytes("TIFF", frames=2)
    monkeypatch.setattr(
        vision_fallback, "parse_images_with_vision",
        lambda *_a, **_k: pytest.fail("corrupt source must not reach provider"),
    )
    with pytest.raises(image_documents.ImageDocumentError):
        image_documents.extract_image_pages(
            content[:-80], filename="broken.tiff", vision_endpoint=object(),
            user_id="owner", operation_id="corrupt",
        )


def test_image_document_cumulative_budget_keeps_usage_and_stops_subsequent_pages(monkeypatch):
    from app.services import image_documents, vision_fallback

    calls = []
    monkeypatch.setattr(image_documents.settings, "vision_max_total_tokens", 25)
    def parse(*_a, **_k):
        calls.append(1)
        return _image_vision_result()
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    result = image_documents.extract_image_pages(
        _raster_bytes("TIFF", frames=3), filename="three.tiff", vision_endpoint=object(),
        user_id="owner", operation_id="budget",
    )
    assert calls == [1, 1]
    assert sum(call["total_tokens"] for call in result.vision_calls) == 40
    assert result.pages[0].text and not result.pages[1].text and not result.pages[2].text
    assert result.warning_code == "image_vision_partial"


def test_image_document_too_many_tiles_never_indexes_only_the_head(monkeypatch):
    from app.services import image_documents, vision_fallback

    monkeypatch.setattr(
        vision_fallback, "parse_images_with_vision",
        lambda *_a, **_k: pytest.fail("oversize tile plan must fail before a provider call"),
    )
    result = image_documents.extract_image_pages(
        _raster_bytes(size=(100, 15_000)), filename="long.png", vision_endpoint=object(),
        user_id="owner", operation_id="long",
    )
    assert not result.text.strip()
    assert result.pages[0].warning_code == "image_vision_partial"


def test_image_document_resume_reuses_successful_tiles_without_endpoint(monkeypatch):
    from app.services import image_documents, vision_fallback

    checkpoints = []
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", lambda *_a, **_k: _image_vision_result())
    content = _raster_bytes(size=(100, 2000))
    def checkpoint(result):
        checkpoints.append(result)
        if len(result.vision_calls) == 2:
            raise RuntimeError("after durable commit")
    with pytest.raises(RuntimeError):
        image_documents.extract_image_pages(
            content, filename="long.png", vision_endpoint=object(), user_id="owner",
            operation_id="tiles", checkpoint=checkpoint,
        )
    monkeypatch.setattr(
        vision_fallback, "parse_images_with_vision",
        lambda *_a, **_k: pytest.fail("completed tiles must not be charged twice"),
    )
    recovered = image_documents.extract_image_pages(
        content, filename="long.png", vision_endpoint=None, user_id="owner",
        operation_id="tiles", cached=checkpoints[-1],
    )
    assert recovered.text
    assert len(recovered.vision_calls) == 2


def test_image_document_retry_reuses_completed_crop_but_retries_failed_crop(monkeypatch):
    from app.services import image_documents, vision_fallback
    from app.services.vision_fallback import VisionFallbackError

    calls = []
    def parse(*_a, **kwargs):
        calls.append(kwargs["document_context"])
        if len(calls) == 2:
            raise VisionFallbackError("vision_output_invalid", usage={"total_tokens": 3, "cost": "0"})
        return _image_vision_result(visible_text=f"分区正文{len(calls)}")
    monkeypatch.setattr(vision_fallback, "parse_images_with_vision", parse)
    content = _raster_bytes(size=(100, 2000))
    failed = image_documents.extract_image_pages(
        content, filename="long.png", vision_endpoint=object(), user_id="owner", operation_id="tiles",
    )
    assert not failed.text.strip()
    recovered = image_documents.extract_image_pages(
        content, filename="long.png", vision_endpoint=object(), user_id="owner",
        operation_id="tiles-retry", cached=failed,
    )
    assert len(calls) == 3
    assert calls[1] == calls[2]
    assert "分区正文1" in recovered.text and "分区正文3" in recovered.text
    assert sum(call["total_tokens"] for call in recovered.vision_calls) == 43


def test_image_document_missing_endpoint_preserves_pages_and_safe_warning():
    from app.services.image_documents import extract_image_pages

    result = extract_image_pages(
        _raster_bytes(), filename="image.png", vision_endpoint=None,
        user_id="owner", operation_id="missing-endpoint",
    )
    assert len(result.pages) == 1
    assert result.pages[0].warning_code == "vision_endpoint_unavailable"
    assert not result.text.strip()
    assert result.vision_calls == ()
