"""Render Office documents (.doc/.docx) to PDF for faithful previews.

Word files are converted by a headless LibreOffice process into a PDF byte
stream. The PDF is rendered page-by-page through the existing PDFium path so
the document preview shows the original layout and embedded images instead of
a flat text dump. Conversion is bounded (process timeout, output size) and
runs in a private temporary directory so a malformed file cannot touch other
data. When LibreOffice is unavailable or the conversion fails, callers fall
back to the extracted-text preview rather than failing the request.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as parse_untrusted_xml
from defusedxml.common import DefusedXmlException

from app.core.config import settings
from app.services.markdown_docx import table_column_widths

logger = logging.getLogger(__name__)

# Security / resource bounds for the external converter.
_CONVERT_TIMEOUT_SECONDS = 60
_MAX_CONVERTED_PDF_BYTES = 200 * 1024 * 1024

_OFFICE_SUFFIXES = frozenset({".doc", ".docx"})
# Presentations use the same LibreOffice harness but their own Impress filter
# path, so they never widen the Word API's accepted suffixes.
_PRESENTATION_SUFFIXES = frozenset({".ppt", ".pptx", ".potx"})
# Spreadsheets likewise ride the shared harness with the Calc filter path and
# never widen the Word API's accepted suffixes. Macro-enabled workbooks and
# the legacy binary format stay download-only.
_SPREADSHEET_SUFFIXES = frozenset({".xlsx"})

_CACHE_ROOT_NAME = "_office_pdf_cache"
# The cache name carries the render pipeline version: a repaired or upgraded
# converter must never reuse a PDF written by the previous pipeline.
OFFICE_PREVIEW_RENDERER_VERSION = "office-pdf-v3"
# Presentations carry their own version tag so a Word renderer change never
# invalidates cached slide PDFs, or the reverse.
PRESENTATION_PREVIEW_RENDERER_VERSION = "impress-pdf-v1"
# Spreadsheets get their own tag for the same reason, on all three paths.
SPREADSHEET_PREVIEW_RENDERER_VERSION = "calc-pdf-v1"

# Writer documents with an outline (headings, TOC) are exported with
# `/PageMode /UseOutlines` in the PDF catalog, which makes viewers that honour
# it (pdf.js, i.e. Firefox and other embedded readers) open the document outline
# over the page on every preview. `InitialView=0` asks for "page only" instead;
# the bookmarks themselves are still exported, so the outline stays available
# when a reader opens it deliberately.
_WRITER_PDF_FILTER = (
    'pdf:writer_pdf_Export:{"InitialView":{"type":"long","value":"0"}}'
)

# `SinglePageSheets` scales each worksheet onto one page, so a wide grid is not
# sliced into print pages. LibreOffice ignores unknown filter keys, degrading
# to plain print pagination; if a much older build rejects the JSON option
# syntax outright and conversion fails, downgrade this constant to "pdf" (the
# extension then selects the Calc filter automatically).
_CALC_PDF_FILTER = (
    'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'
)


def _cache_root() -> Path:
    root = settings.upload_dir / _CACHE_ROOT_NAME
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("office preview cache dir unavailable: %s", root)
    return root


def _cached_pdf_path(
    digest: str, renderer_version: str = OFFICE_PREVIEW_RENDERER_VERSION
) -> Path:
    return _cache_root() / f"{digest}-{renderer_version}.pdf"


def render_office_preview_pdf(content: bytes, filename: str) -> bytes:
    """Return cached PDF bytes for an Office file, converting on first use.

    The cache key is the SHA-256 of the source bytes plus the render pipeline
    version, so an identical version renders once, later previews are served
    from disk, and a PDF from an older pipeline is never reused. The write is
    atomic (temp file + os.replace) so concurrent readers never observe a
    partial PDF. Callers must treat LibreOfficeUnavailable /
    DocumentConversionError as safe, user-facing failures and fall back to the
    extracted-text preview.
    """
    digest = hashlib.sha256(content).hexdigest()
    cached = _cached_pdf_path(digest)
    try:
        if _is_complete_pdf(cached):
            return cached.read_bytes()
    except OSError:
        logger.warning("office preview cache read failed for sha=%s", digest)

    pdf_bytes = convert_office_to_pdf(content, filename)
    try:
        if cached.parent.is_dir():
            _atomic_write(cached, pdf_bytes)
    except OSError:
        logger.warning("office preview cache write failed for sha=%s", digest)
    return pdf_bytes


def office_preview_cached(content: bytes) -> bool:
    """True when a previous render already cached a complete PDF."""
    digest = hashlib.sha256(content).hexdigest()
    try:
        return _is_complete_pdf(_cached_pdf_path(digest))
    except OSError:
        return False


def render_presentation_preview_pdf(content: bytes, filename: str) -> bytes:
    """Return cached PDF bytes for a presentation, converting on first use.

    Same cache discipline as the Word path (SHA-256 of the source bytes plus a
    renderer version, atomic write) under its own version tag. The conversion
    downsamples oversized photos, because a deck built from full-bleed images
    would otherwise convert to a PDF far larger than the client will embed;
    text and vectors stay intact and the stored file is never modified.
    Callers must treat LibreOfficeUnavailable / DocumentConversionError as
    safe, user-facing failures.
    """
    digest = hashlib.sha256(content).hexdigest()
    cached = _cached_pdf_path(digest, PRESENTATION_PREVIEW_RENDERER_VERSION)
    try:
        if _is_complete_pdf(cached):
            return cached.read_bytes()
    except OSError:
        logger.warning("presentation preview cache read failed for sha=%s", digest)

    pdf_bytes = convert_presentation_to_pdf(content, filename, original_preview=True)
    try:
        if cached.parent.is_dir():
            _atomic_write(cached, pdf_bytes)
    except OSError:
        logger.warning("presentation preview cache write failed for sha=%s", digest)
    return pdf_bytes


def render_spreadsheet_preview_pdf(content: bytes, filename: str) -> bytes:
    """Return cached PDF bytes for a workbook, converting on first use.

    Same cache discipline as the Word path (SHA-256 of the source bytes plus a
    renderer version, atomic write) under its own version tag. Callers must
    treat LibreOfficeUnavailable / DocumentConversionError as safe, user-facing
    failures and leave the download as the way to see the file.
    """
    digest = hashlib.sha256(content).hexdigest()
    cached = _cached_pdf_path(digest, SPREADSHEET_PREVIEW_RENDERER_VERSION)
    try:
        if _is_complete_pdf(cached):
            return cached.read_bytes()
    except OSError:
        logger.warning("spreadsheet preview cache read failed for sha=%s", digest)

    pdf_bytes = convert_spreadsheet_to_pdf(content, filename)
    try:
        if cached.parent.is_dir():
            _atomic_write(cached, pdf_bytes)
    except OSError:
        logger.warning("spreadsheet preview cache write failed for sha=%s", digest)
    return pdf_bytes


def _is_complete_pdf(path: Path) -> bool:
    """True only for a non-empty file whose header is a real PDF."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a same-directory temp file and atomically replace the target."""
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".office-cache-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class LibreOfficeUnavailable(RuntimeError):
    """Safe, user-facing failure when the document renderer is not installed."""


class DocumentConversionError(RuntimeError):
    """Safe, user-facing failure when a Word file could not be rendered."""


def _soffice_command() -> str:
    for candidate in ("soffice", "libreoffice", "lowriter"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise LibreOfficeUnavailable("文档渲染组件不可用，请下载原文件查看")


def _is_office_document(filename: str) -> bool:
    return Path(filename).suffix.lower() in _OFFICE_SUFFIXES


def _strip_body_sdt(content: bytes) -> bytes | None:
    """Return a DOCX with body ``<w:sdt>`` wrappers unwrapped, or None.

    LibreOffice 25.x refuses to load some real-world DOCX files whose body
    contains a structured-document tag (typically a table-of-contents field).
    Unwrapping the sdt keeps every paragraph and text run while dropping only
    the control wrapper, which lets the renderer proceed. Nested controls are
    unwrapped after their parent exposes them, and every other package part
    retains its exact bytes.
    """
    import zipfile

    if not content:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if "word/document.xml" not in archive.namelist():
                return None
            document = archive.read("word/document.xml").decode(
                "utf-8", errors="replace"
            )
            rest = {
                name: archive.read(name)
                for name in archive.namelist()
                if name != "word/document.xml"
            }
    except Exception:
        return None

    body_start = document.find("<w:body>")
    body_end = document.rfind("</w:body>")
    if body_start < 0 or body_end <= body_start:
        return None
    body = document[body_start + len("<w:body>") : body_end]

    def find_tag_start(segment: str, tag: str, start: int) -> int:
        """Find an exact XML start tag without matching longer tag names."""
        needle = f"<{tag}"
        index = segment.find(needle, start)
        while index >= 0:
            boundary = index + len(needle)
            if boundary < len(segment) and (
                segment[boundary] == ">" or segment[boundary].isspace()
            ):
                return index
            index = segment.find(needle, boundary)
        return -1

    def find_matching_close(
        segment: str, tag: str, content_start: int
    ) -> int:
        """Return the matching close-tag offset for one opened XML element."""
        close_tag = f"</{tag}>"
        depth = 1
        scan = content_start
        while depth:
            next_open = find_tag_start(segment, tag, scan)
            next_close = segment.find(close_tag, scan)
            if next_close < 0:
                return -1
            if next_open >= 0 and next_open < next_close:
                next_open_end = segment.find(">", next_open)
                if next_open_end < 0:
                    return -1
                depth += 1
                scan = next_open_end + 1
                continue
            depth -= 1
            if depth == 0:
                return next_close
            scan = next_close + len(close_tag)
        return -1

    def unwrap_once(segment: str) -> tuple[str, bool]:
        """Unwrap every outermost sdt encountered in one left-to-right pass."""
        out: list[str] = []
        changed = False
        pos = 0
        while True:
            open_idx = find_tag_start(segment, "w:sdt", pos)
            if open_idx < 0:
                out.append(segment[pos:])
                break
            open_end = segment.find(">", open_idx)
            if open_end < 0:
                out.append(segment[pos:])
                break
            close_idx = find_matching_close(
                segment, "w:sdt", open_end + 1
            )
            if close_idx < 0:
                out.append(segment[pos:])
                break

            inner = segment[open_end + 1 : close_idx]
            content_idx = find_tag_start(inner, "w:sdtContent", 0)
            if content_idx >= 0:
                content_open_end = inner.find(">", content_idx)
                if content_open_end >= 0:
                    content_close = find_matching_close(
                        inner, "w:sdtContent", content_open_end + 1
                    )
                    if content_close >= 0:
                        inner = inner[content_open_end + 1 : content_close]

            out.append(segment[pos:open_idx])
            out.append(inner)
            pos = close_idx + len("</w:sdt>")
            changed = True
        return "".join(out), changed

    changed = False
    new_body = body
    while True:
        next_body, pass_changed = unwrap_once(new_body)
        if not pass_changed:
            break
        changed = True
        new_body = next_body

    if not changed:
        return None
    new_document = (
        document[: body_start + len("<w:body>")] + new_body + document[body_end:]
    )
    try:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
            out.writestr("word/document.xml", new_document)
            for name, data in rest.items():
                out.writestr(name, data)
        return buffer.getvalue()
    except Exception:
        return None


_W_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XMLNS_RE = re.compile(rb'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"')
# Repairing copies the package, so a package declaring an outsized
# uncompressed size is left alone instead of being expanded in memory.
_MAX_REPAIR_PACKAGE_BYTES = 100 * 1024 * 1024


def _table_cell_text(cell: ElementTree.Element) -> str:
    """Text of one table cell, used only to size a repaired column."""
    return "".join(node.text or "" for node in cell.iter(f"{{{_W_NAMESPACE}}}t"))


def _write_table_grid(table: ElementTree.Element, widths: list[int]) -> None:
    """Give one table a ``w:tblGrid`` and explicit cell widths."""
    grid = ElementTree.Element(f"{{{_W_NAMESPACE}}}tblGrid")
    for width in widths:
        ElementTree.SubElement(
            grid, f"{{{_W_NAMESPACE}}}gridCol", {f"{{{_W_NAMESPACE}}}w": str(width)}
        )
    properties = table.find(f"{{{_W_NAMESPACE}}}tblPr")
    table.insert(0 if properties is None else list(table).index(properties) + 1, grid)
    for row in table.findall(f"{{{_W_NAMESPACE}}}tr"):
        for index, cell in enumerate(row.findall(f"{{{_W_NAMESPACE}}}tc")):
            if index >= len(widths):
                break
            cell_properties = cell.find(f"{{{_W_NAMESPACE}}}tcPr")
            if cell_properties is None:
                cell_properties = ElementTree.Element(f"{{{_W_NAMESPACE}}}tcPr")
                cell.insert(0, cell_properties)
            width = cell_properties.find(f"{{{_W_NAMESPACE}}}tcW")
            if width is None:
                width = ElementTree.Element(f"{{{_W_NAMESPACE}}}tcW")
                cell_properties.insert(0, width)
            width.set(f"{{{_W_NAMESPACE}}}w", str(widths[index]))
            width.set(f"{{{_W_NAMESPACE}}}type", "dxa")


def _ensure_table_grids(content: bytes) -> bytes | None:
    """Return a DOCX whose grid-less tables carry a computed ``w:tblGrid``.

    Word derives the columns of a table that has no grid; LibreOffice collapses
    it into a single column, so every cell after the first disappears from the
    render. Only plain tables (no ``w:tblGrid``, no ``w:gridSpan``/``w:vMerge``,
    not nested in another table) are repaired, every other package part keeps
    its bytes, and the result is a temporary copy: the stored file never
    changes. Widths assume the default A4 text column, which is what our own
    writer targets.
    """
    if not content:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if "word/document.xml" not in names:
                return None
            if (
                sum(info.file_size for info in archive.infolist())
                > _MAX_REPAIR_PACKAGE_BYTES
            ):
                logger.warning("skipping table grid repair for an oversized package")
                return None
            document = archive.read("word/document.xml")
    except Exception:
        return None
    try:
        # Uploaded DOCX is untrusted input: parsing must not expand entities.
        root = parse_untrusted_xml(document)
    except (ElementTree.ParseError, DefusedXmlException):
        return None
    # ElementTree renames namespaces it has not seen; keep the original prefixes.
    for prefix, uri in _XMLNS_RE.findall(document[:4096]):
        try:
            ElementTree.register_namespace(prefix.decode("ascii"), uri.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue

    parents = {child: parent for parent in root.iter() for child in parent}
    changed = False
    for table in root.iter(f"{{{_W_NAMESPACE}}}tbl"):
        if table.find(f"{{{_W_NAMESPACE}}}tblGrid") is not None:
            continue
        parent = parents.get(table)
        if parent is not None and parent.tag == f"{{{_W_NAMESPACE}}}tc":
            continue
        rows: list[list[str]] = []
        merged = False
        for row in table.findall(f"{{{_W_NAMESPACE}}}tr"):
            cells: list[str] = []
            for cell in row.findall(f"{{{_W_NAMESPACE}}}tc"):
                cell_properties = cell.find(f"{{{_W_NAMESPACE}}}tcPr")
                if cell_properties is not None and (
                    cell_properties.find(f"{{{_W_NAMESPACE}}}gridSpan") is not None
                    or cell_properties.find(f"{{{_W_NAMESPACE}}}vMerge") is not None
                ):
                    merged = True
                    break
                cells.append(_table_cell_text(cell))
            if merged or not cells:
                break
            rows.append(cells)
        if merged or not rows:
            continue
        widths = table_column_widths(rows)
        if len(widths) < 2:
            continue
        _write_table_grid(table, widths)
        changed = True

    if not changed:
        return None
    replacement = ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as repaired:
                for info in archive.infolist():
                    if info.filename == "word/document.xml":
                        repaired.writestr(info, replacement)
                        continue
                    # Stream the untouched parts so images never sit in memory.
                    with archive.open(info) as source, repaired.open(info, "w") as target:
                        shutil.copyfileobj(source, target)
    except Exception:
        return None
    return buffer.getvalue()


def convert_office_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert one Word .doc/.docx into PDF bytes via headless LibreOffice.

    Raises LibreOfficeUnavailable when the renderer is not installed and
    DocumentConversionError when conversion fails or exceeds resource bounds.
    """
    if not content:
        raise DocumentConversionError("文件为空，无法转换")
    if not _is_office_document(filename):
        raise DocumentConversionError(f"不支持转换的文件类型：{filename}")

    suffix = Path(filename).suffix.lower()
    source = content
    if suffix == ".docx":
        # A table without a grid renders as an empty single column.
        repaired = _ensure_table_grids(content)
        if repaired is not None:
            source = repaired
    pdf_bytes = _convert_bytes(source, suffix, conversion_filter=_WRITER_PDF_FILTER)
    # Some real DOCX files (e.g. with a body-level TOC sdt) make LibreOffice
    # report "source file could not be loaded". Unwrap body sdt wrappers and
    # retry once; the paragraphs/text are preserved.
    if pdf_bytes is None and suffix == ".docx":
        retried = _strip_body_sdt(source)
        if retried is not None and retried != source:
            pdf_bytes = _convert_bytes(
                retried, suffix, conversion_filter=_WRITER_PDF_FILTER
            )
            if pdf_bytes is not None:
                logger.info(
                    "office conversion succeeded after unwrapping body sdt for %s",
                    filename,
                )
    if pdf_bytes is None:
        raise DocumentConversionError("文档转换失败，请下载原文件查看")
    if len(pdf_bytes) > _MAX_CONVERTED_PDF_BYTES:
        raise DocumentConversionError("转换结果超过大小上限，请下载原文件查看")
    return pdf_bytes


def _secure_conversion_profile(profile: Path) -> None:
    """使用 LibreOffice 注册表配置禁宏与外链，不依赖伪环境变量。"""
    user = profile / "user"
    user.mkdir()
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '<prop oor:name="SecureURL" oor:op="fuse"><value/></prop>'
        '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>'
        '</item>'
        '<item oor:path="/org.openoffice.Office.Calc/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>'
        '</oor:items>',
        encoding="utf-8",
    )


def _convert_bytes(
    content: bytes,
    suffix: str,
    *,
    conversion_filter: str = "pdf",
    output_suffix: str = ".pdf",
    max_output_bytes: int | None = None,
) -> bytes | None:
    """Run LibreOffice once; return converted bytes or None when it produced none."""
    executable = _soffice_command()
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="doc-nexus-office-") as directory:
        source = Path(directory) / f"source{suffix}"
        source.write_bytes(content)
        out_dir = Path(directory) / "out"
        out_dir.mkdir()
        profile = Path(directory) / "profile"
        profile.mkdir()
        _secure_conversion_profile(profile)
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-env:UserInstallation=" + profile.as_uri(),
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--nodefault",
                    "--nologo",
                    "--convert-to",
                    conversion_filter,
                    "--outdir",
                    str(out_dir),
                    str(source),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_CONVERT_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("office conversion timed out (sha=%s)", digest)
            raise DocumentConversionError("文档转换超时，请下载原文件查看") from exc
        except OSError as exc:
            logger.warning("office conversion failed to launch: %s", exc)
            raise LibreOfficeUnavailable("文档渲染组件不可用，请下载原文件查看") from exc

        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            logger.warning(
                "office conversion failed (sha=%s): %s", digest, tail
            )
            return None
        candidates = sorted(out_dir.glob(f"*{output_suffix}"))
        if not candidates:
            logger.warning("office conversion produced no %s (sha=%s)", output_suffix, digest)
            return None
        output = candidates[0]
        limit = _MAX_CONVERTED_PDF_BYTES if max_output_bytes is None else max_output_bytes
        if output.stat().st_size > limit:
            raise DocumentConversionError("转换结果超过大小上限，请下载原文件查看")
        with output.open("rb") as stream:
            result = stream.read(limit + 1)
        if len(result) > limit:
            raise DocumentConversionError("转换结果超过大小上限，请下载原文件查看")
        return result


def presentation_renderer_available() -> bool:
    """True when the Impress filter can actually be invoked on this host."""
    try:
        _soffice_command()
    except LibreOfficeUnavailable:
        return False
    return True


def convert_presentation_to_pdf(
    content: bytes,
    filename: str = "template.pptx",
    *,
    original_preview: bool = False,
) -> bytes:
    """Convert one PPTX/POTX into PDF bytes via headless Impress.

    Kept separate from the Word entry point so a template preview can never
    silently widen what the document-preview API accepts. Raises
    LibreOfficeUnavailable / DocumentConversionError, both safe for the client.
    """
    if not content:
        raise DocumentConversionError("文件为空，无法转换")
    suffix = Path(filename).suffix.lower()
    if suffix not in _PRESENTATION_SUFFIXES:
        raise DocumentConversionError(f"不支持转换的文件类型：{filename}")
    if suffix == ".ppt":
        _check_legacy_presentation(content)
    if original_preview:
        # Show every original slide, but downsample oversized photos for an
        # online preview. Text and vectors remain intact; the source is untouched.
        # Word and neutral sample previews retain their existing defaults.
        pdf_bytes = _convert_bytes(
            content,
            suffix,
            conversion_filter=(
                'pdf:impress_pdf_Export:'
                '{"ExportHiddenSlides":{"type":"boolean","value":"true"},'
                '"ReduceImageResolution":{"type":"boolean","value":"true"},'
                '"MaxImageResolution":{"type":"long","value":"150"},'
                '"UseLosslessCompression":{"type":"boolean","value":"false"},'
                '"Quality":{"type":"long","value":"85"}}'
            ),
        )
    else:
        pdf_bytes = _convert_bytes(content, suffix)
    if pdf_bytes is None:
        raise DocumentConversionError("演示文稿转换失败，请下载原文件查看")
    if len(pdf_bytes) > _MAX_CONVERTED_PDF_BYTES:
        raise DocumentConversionError("转换结果超过大小上限，请下载原文件查看")
    return pdf_bytes


_PPTX_CONVERSION_FILTER = "pptx:Impress MS PowerPoint 2007 XML"
_MAX_CONVERTED_PPTX_BYTES = 512 * 1024 * 1024


def _check_legacy_presentation(content: bytes) -> None:
    """检查 OLE 与 MS-PPT 记录，拒绝宏、ActiveX、可执行嵌入及外部对象。"""
    import olefile

    signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    remaining = _MAX_CONVERTED_PPTX_BYTES

    def scan_ole(data: bytes, depth: int = 0, *, presentation: bool = False) -> None:
        nonlocal remaining
        remaining -= len(data)
        if depth > 16 or remaining < 0 or not data.startswith(signature):
            raise DocumentConversionError("旧版 PPT 的嵌入对象无效或超过安全解析上限")
        with olefile.OleFileIO(io.BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT) as ole:
            streams = ole.listdir()
            if presentation and not ole.exists("PowerPoint Document"):
                raise DocumentConversionError("文件不是有效的旧版 PPT")
            for parts in streams:
                names = {part.casefold() for part in parts}
                if any("vba" in name or name == "macros" for name in names):
                    raise DocumentConversionError("旧版 PPT 包含宏或 VBA，请另存为无宏 PPTX")
                if names & {"package", "\x01ole10native", "contents", "activex"}:
                    # 任意文件封装、原生程序和 ActiveX 不交给外部转换进程激活。
                    raise DocumentConversionError("旧版 PPT 包含不能安全转换的嵌入对象，请另存为无嵌入对象的 PPTX")
                if "encryptedpackage" in names or "encryptioninfo" in names:
                    raise DocumentConversionError("加密 PPT 不能安全转换，请先解除密码保护")
                with ole.openstream(parts) as stream:
                    prefix = stream.read(8)
                    if prefix.startswith((b"MZ", b"\x7fELF", b"#!")):
                        raise DocumentConversionError("旧版 PPT 包含可执行嵌入内容")
                    if parts[-1] == "PowerPoint Document":
                        stream.seek(0)
                        scan_records(stream.read(), depth)
                    elif prefix == signature:
                        stream.seek(0)
                        scan_ole(stream.read(), depth + 1)

    def scan_records(data: bytes, depth: int) -> None:
        # 记录编号依据 MS-PPT RecordType；不把普通无宏 VBAInfoAtom 当宏。
        pending = [(0, len(data), 0)]
        records = 0
        while pending:
            offset, end, nesting = pending.pop()
            if nesting > 64:
                raise DocumentConversionError("旧版 PPT 记录嵌套过深")
            while offset < end:
                records += 1
                if records > 1_000_000 or end - offset < 8:
                    raise DocumentConversionError("旧版 PPT 记录结构损坏或过大")
                options, kind, length = struct.unpack_from("<HHI", data, offset)
                start = offset + 8
                stop = start + length
                if stop > end:
                    raise DocumentConversionError("旧版 PPT 记录长度无效")
                if kind == 0x0400 and (length < 12 or struct.unpack_from("<I", data, start + 4)[0]):
                    raise DocumentConversionError("旧版 PPT 包含宏或 VBA，请另存为无宏 PPTX")
                if kind in {0x0FEE, 0x0FFB, 0x0FCE, 0x0FD1, 0x1005, 0x1006, 0x1007, 0x100D, 0x100E, 0x1010, 0x2F14}:
                    raise DocumentConversionError("旧版 PPT 包含 ActiveX、外链对象或加密内容，不能安全转换")
                if kind == 0x0FF3 and (length != 16 or data[start + 8] in {1, 2, 5}):
                    raise DocumentConversionError("旧版 PPT 包含宏、运行程序或激活嵌入对象的动作")
                if kind == 0x1011:
                    payload = memoryview(data)[start:stop]
                    if options >> 4 == 1:
                        if length < 4:
                            raise DocumentConversionError("旧版 PPT 嵌入对象损坏")
                        expected = struct.unpack_from("<I", payload)[0]
                        if expected > remaining:
                            raise DocumentConversionError("旧版 PPT 嵌入对象超过安全解析上限")
                        decompressor = zlib.decompressobj()
                        embedded = decompressor.decompress(payload[4:], expected + 1)
                        if len(embedded) != expected or not decompressor.eof or decompressor.unused_data:
                            raise DocumentConversionError("旧版 PPT 嵌入对象压缩数据无效")
                    elif options >> 4 == 0:
                        embedded = bytes(payload)
                    else:
                        raise DocumentConversionError("旧版 PPT 嵌入对象编码无效")
                    scan_ole(embedded, depth + 1)
                elif options & 0xF == 0xF:
                    pending.append((start, stop, nesting + 1))
                offset = stop

    try:
        scan_ole(content, presentation=True)
    except DocumentConversionError:
        raise
    except (OSError, ValueError, EOFError, struct.error, zlib.error) as error:
        raise DocumentConversionError("旧版 PPT 文件结构损坏，无法安全转换") from error


def convert_presentation_to_pptx(content: bytes, filename: str) -> bytes:
    """Convert one legacy .ppt into PPTX bytes via headless Impress.

    Uses the same isolated profile / timeout / no-shell harness as the PDF
    path. The source is never overwritten and the output is bounded. Raises
    LibreOfficeUnavailable / DocumentConversionError, both safe for the
    client. Conversion is lossy by design; callers must not claim fidelity.

    在共享入口检查 OLE/VBA/嵌入对象；隔离 profile 禁宏并阻止外链更新。
    """
    if not content:
        raise DocumentConversionError("文件为空，无法转换")
    suffix = Path(filename).suffix.lower()
    if suffix != ".ppt":
        raise DocumentConversionError(f"仅支持转换旧版 .ppt：{filename}")

    _check_legacy_presentation(content)

    pptx_bytes = _convert_bytes(
        content,
        suffix,
        conversion_filter=_PPTX_CONVERSION_FILTER,
        output_suffix=".pptx",
        max_output_bytes=_MAX_CONVERTED_PPTX_BYTES,
    )
    if pptx_bytes is None:
        raise DocumentConversionError("旧版 .ppt 转换失败，请下载原文件查看")
    return pptx_bytes




def convert_spreadsheet_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert one XLSX workbook into PDF bytes via headless Calc.

    Kept separate from the Word entry point so a spreadsheet preview can never
    silently widen what the document-preview API accepts. Raises
    LibreOfficeUnavailable / DocumentConversionError, both safe for the client.
    """
    if not content:
        raise DocumentConversionError("文件为空，无法转换")
    suffix = Path(filename).suffix.lower()
    if suffix not in _SPREADSHEET_SUFFIXES:
        raise DocumentConversionError(f"不支持转换的文件类型：{filename}")
    pdf_bytes = _convert_bytes(content, suffix, conversion_filter=_CALC_PDF_FILTER)
    if pdf_bytes is None:
        raise DocumentConversionError("表格转换失败，请下载原文件查看")
    if len(pdf_bytes) > _MAX_CONVERTED_PDF_BYTES:
        raise DocumentConversionError("转换结果超过大小上限，请下载原文件查看")
    return pdf_bytes


_MAX_PREVIEW_PAGES = 64
_PREVIEW_RENDER_SCALE = 2.0


def render_pdf_pages_to_png(
    pdf_bytes: bytes, *, max_pages: int = _MAX_PREVIEW_PAGES, scale: float = _PREVIEW_RENDER_SCALE
) -> list[bytes]:
    """Rasterize the first ``max_pages`` PDF pages to PNG bytes, in order."""
    import pypdfium2 as pdfium

    images: list[bytes] = []
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        count = min(len(document), max(1, int(max_pages)))
        for index in range(count):
            page = document[index]
            try:
                bitmap = page.render(scale=max(0.5, float(scale)))
                try:
                    image = bitmap.to_pil()
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG", optimize=True)
                    images.append(buffer.getvalue())
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()
    return images
