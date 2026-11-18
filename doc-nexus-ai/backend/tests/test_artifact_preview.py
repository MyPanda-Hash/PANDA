"""Export-result preview: caching, and never breaking the download."""

from __future__ import annotations

import io
import struct
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import artifact_preview, doc_preview

PPTX_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


@pytest.fixture()
def preview_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    yield tmp_path


def test_only_presentation_artifacts_are_previewable() -> None:
    assert artifact_preview.is_previewable(PPTX_TYPE) is True
    assert artifact_preview.is_previewable("application/pdf") is False
    assert artifact_preview.is_previewable(None) is False


def test_preview_converts_once_and_serves_from_cache(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache is keyed on the artifact's own bytes: same file, no re-render."""
    calls: list[str] = []

    monkeypatch.setattr(doc_preview, "presentation_renderer_available", lambda: True)
    monkeypatch.setattr(
        doc_preview,
        "convert_presentation_to_pdf",
        lambda content, filename: calls.append(filename) or b"%PDF-1.7 fake",
    )
    monkeypatch.setattr(
        doc_preview,
        "render_pdf_pages_to_png",
        lambda pdf, max_pages=3: [b"png-1", b"png-2", b"png-3"][:max_pages],
    )

    content = b"deck-bytes"
    assert artifact_preview.generate(content, "deck.pptx") == 3
    assert calls == ["deck.pptx"]

    # A second request must not convert again.
    assert artifact_preview.generate(content, "deck.pptx") == 3
    assert calls == ["deck.pptx"]

    digest = artifact_preview.cache_key(content)
    assert artifact_preview.read_page(digest, 1) == b"png-1"
    assert artifact_preview.read_page(digest, 3) == b"png-3"
    # Out of range is a miss, not an error.
    assert artifact_preview.read_page(digest, 0) is None
    assert artifact_preview.read_page(digest, 9) is None


def test_changed_output_gets_its_own_preview(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc_preview, "presentation_renderer_available", lambda: True)
    monkeypatch.setattr(
        doc_preview, "convert_presentation_to_pdf", lambda content, filename: content
    )
    monkeypatch.setattr(
        doc_preview,
        "render_pdf_pages_to_png",
        lambda pdf, max_pages=3: [b"page-of-" + pdf],
    )

    artifact_preview.generate(b"first", "a.pptx")
    artifact_preview.generate(b"second", "a.pptx")
    assert artifact_preview.read_page(artifact_preview.cache_key(b"first"), 1) == (
        b"page-of-first"
    )
    assert artifact_preview.read_page(artifact_preview.cache_key(b"second"), 1) == (
        b"page-of-second"
    )


def test_missing_renderer_is_reported_without_failing_the_export(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Impress filter must degrade to "download it", never to an error page."""
    monkeypatch.setattr(doc_preview, "presentation_renderer_available", lambda: False)

    with pytest.raises(artifact_preview.ArtifactPreviewUnavailable) as excinfo:
        artifact_preview.generate(b"deck", "deck.pptx")
    assert "下载" in excinfo.value.message


def test_conversion_failure_does_not_cache_a_broken_preview(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc_preview, "presentation_renderer_available", lambda: True)

    def explode(content: bytes, filename: str) -> bytes:
        raise RuntimeError("soffice died")

    monkeypatch.setattr(doc_preview, "convert_presentation_to_pdf", explode)

    with pytest.raises(artifact_preview.ArtifactPreviewUnavailable):
        artifact_preview.generate(b"deck", "deck.pptx")
    assert artifact_preview.cached_pages(artifact_preview.cache_key(b"deck")) == 0


def test_presentation_preview_pdf_converts_once_with_preview_export(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat preview renders a deck once, via the downsampling export."""
    calls: list[tuple[str, bool]] = []

    def fake(
        content: bytes, filename: str, *, original_preview: bool = False
    ) -> bytes:
        calls.append((filename, original_preview))
        return b"%PDF-1.7 slides"

    monkeypatch.setattr(doc_preview, "convert_presentation_to_pdf", fake)

    content = b"deck-bytes"
    assert doc_preview.render_presentation_preview_pdf(
        content, "deck.pptx"
    ) == b"%PDF-1.7 slides"
    # The same bytes are served from the cache, not converted twice.
    assert doc_preview.render_presentation_preview_pdf(
        content, "deck.pptx"
    ) == b"%PDF-1.7 slides"
    assert calls == [("deck.pptx", True)]


def test_presentation_preview_pdf_never_caches_a_failed_conversion(
    preview_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(
        content: bytes, filename: str, *, original_preview: bool = False
    ) -> bytes:
        raise doc_preview.DocumentConversionError("演示文稿转换失败，请下载原文件查看")

    monkeypatch.setattr(doc_preview, "convert_presentation_to_pdf", explode)

    for _ in range(2):
        with pytest.raises(doc_preview.DocumentConversionError):
            doc_preview.render_presentation_preview_pdf(b"deck-bytes", "deck.pptx")


def _legacy_ppt(records: bytes = b"", *, stream_name: str = "PowerPoint Document") -> bytes:
    """构造使用普通 FAT 流的 CFB，直接由真实 olefile 读取，不伪造解析结果。"""
    free, end, fat = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    sectors = max(8, (len(records) + 8 + 511) // 512)
    assert sectors <= 126
    allocation = [fat, end] + list(range(3, 2 + sectors)) + [end] + [free] * (126 - sectors)
    directory = bytearray(512)

    def entry(offset, name, kind, child, start, size):
        encoded = (name + "\0").encode("utf-16le")
        directory[offset : offset + len(encoded)] = encoded
        struct.pack_into("<HBBIII", directory, offset + 64, len(encoded), kind, 1, free, free, child)
        struct.pack_into("<IQ", directory, offset + 116, start, size)

    entry(0, "Root Entry", 5, 1, end, 0)
    entry(128, stream_name, 2, free, 2, sectors * 512)
    padding = sectors * 512 - len(records) - 8
    assert padding >= 0
    stream = records + struct.pack("<HHI", 0, 0x0FA8, padding) + b" " * padding
    return bytes(header) + struct.pack("<128I", *allocation) + bytes(directory) + stream


@pytest.mark.parametrize(
    "records",
    [
        struct.pack("<HHIIII", 0, 0x0400, 12, 1, 1, 2),
        struct.pack("<HHI", 0xF, 0x0FEE, 0),
        struct.pack("<HHI", 0xF, 0x0FCE, 0),
        struct.pack("<HHI", 0, 0x1011, 2) + b"MZ",
    ],
    ids=["vba", "activex", "external-ole", "executable-embedding"],
)
def test_legacy_ppt_rejects_unsafe_records_before_conversion(monkeypatch, records):
    def unexpected_launch():
        raise AssertionError("危险 OLE 不应启动转换进程")

    monkeypatch.setattr(doc_preview, "_soffice_command", unexpected_launch)
    with pytest.raises(doc_preview.DocumentConversionError):
        doc_preview.convert_presentation_to_pptx(_legacy_ppt(records), "theme.ppt")


def test_legacy_ppt_accepts_macro_free_ole_and_keeps_source(monkeypatch):
    from pptx import Presentation

    # 无宏 VBAInfoAtom 是正常 PPT 常见记录，不能一概拒绝。
    original = _legacy_ppt(struct.pack("<HHIIII", 0, 0x0400, 12, 0, 0, 2))
    original_copy = bytes(original)
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = "可编辑转换结果"
    buffer = io.BytesIO()
    deck.save(buffer)
    monkeypatch.setattr(doc_preview, "_soffice_command", lambda: "/usr/bin/soffice")

    def convert(command, **_kwargs):
        output = Path(command[command.index("--outdir") + 1])
        (output / "source.pptx").write_bytes(buffer.getvalue())
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(doc_preview.subprocess, "run", convert)
    result = doc_preview.convert_presentation_to_pptx(original, "theme.ppt")
    assert Presentation(io.BytesIO(result)).slides[0].shapes.title.text == "可编辑转换结果"
    assert original == original_copy
    assert zipfile.is_zipfile(io.BytesIO(result))


def test_converter_rejects_oversized_output_before_reading_it(monkeypatch):
    monkeypatch.setattr(doc_preview, "_soffice_command", lambda: "/usr/bin/soffice")

    def oversized(command, **_kwargs):
        output = Path(command[command.index("--outdir") + 1]) / "source.pdf"
        with output.open("wb") as stream:
            stream.truncate(65)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    original_open = Path.open

    def forbid_large_output_read(path, mode="r", *args, **kwargs):
        if path.suffix == ".pdf" and mode == "rb":
            raise AssertionError("超过上限的输出不能先读入内存")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_large_output_read)
    monkeypatch.setattr(doc_preview.subprocess, "run", oversized)
    monkeypatch.setattr(doc_preview, "_MAX_CONVERTED_PDF_BYTES", 64)
    with pytest.raises(doc_preview.DocumentConversionError, match="大小上限"):
        doc_preview.convert_presentation_to_pdf(b"input", "deck.pptx")


@pytest.mark.parametrize("compressed", [False, True])
def test_legacy_ppt_rejects_vba_inside_embedded_ole(monkeypatch, compressed):
    import zlib

    embedded = _legacy_ppt(stream_name="VBA")
    payload = struct.pack("<I", len(embedded)) + zlib.compress(embedded) if compressed else embedded
    record = struct.pack("<HHI", 0x10 if compressed else 0, 0x1011, len(payload)) + payload

    def unexpected_launch():
        raise AssertionError("嵌入 VBA 不应启动转换进程")

    monkeypatch.setattr(doc_preview, "_soffice_command", unexpected_launch)
    with pytest.raises(doc_preview.DocumentConversionError, match="VBA"):
        doc_preview.convert_presentation_to_pptx(_legacy_ppt(record), "theme.ppt")
