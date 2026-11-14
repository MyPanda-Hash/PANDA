"""Document processing utilities.

Provides file validation, text extraction, download token management,
and document processing pipeline helpers.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import posixpath
import re
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from defusedxml import ElementTree as ET

from app.core.config import settings
from app.services.image_documents import IMAGE_EXTENSIONS, MAX_IMAGE_BYTES

logger = logging.getLogger(__name__)

ACCEPTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".txt",
        ".md",
        ".markdown",
        ".csv",
    }
) | IMAGE_EXTENSIONS
_MAX_SOURCE_PATH_LENGTH = 1024
_MAX_SOURCE_PATH_DEPTH = 32
_MAX_SOURCE_PATH_COMPONENT_LENGTH = 255
_MAX_OFFICE_ARCHIVE_FILES = 10_000
_MAX_OFFICE_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_OFFICE_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_OFFICE_XML_BYTES = 64 * 1024 * 1024
_MAX_OFFICE_COMPRESSION_RATIO = 100
_MAX_OFFICE_COMPRESSION_CHECK_BYTES = 64 * 1024
_MAX_XLSX_DENSE_CELLS = 100_000

_PACKAGE_CONTENT_TYPES_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/content-types",
        "http://purl.oclc.org/ooxml/package/content-types",
    }
)
_PACKAGE_RELATIONSHIPS_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships",
        "http://purl.oclc.org/ooxml/package/relationships",
    }
)
_PRESENTATION_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/presentationml/2006/main",
        "http://purl.oclc.org/ooxml/presentationml/main",
    }
)
_DRAWINGML_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/drawingml/2006/main",
        "http://purl.oclc.org/ooxml/drawingml/main",
    }
)
_OFFICE_RELATIONSHIPS_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
)


def _xml_namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _archive_member_key(name: str) -> str:
    """Return a safe canonical ZIP member name or raise for unsafe paths."""
    if not isinstance(name, str) or not name:
        raise ValueError("Office 文件包含无效的内部路径")
    if (
        "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError("Office 文件包含不安全的内部路径")
    components = name.split("/")
    if any(not component for component in components[:-1]):
        raise ValueError("Office 文件包含不安全的内部路径")
    if any(component in {".", ".."} for component in components):
        raise ValueError("Office 文件包含不安全的内部路径")
    canonical = posixpath.normpath(name)
    if canonical in {"", ".", ".."} or canonical.startswith("../"):
        raise ValueError("Office 文件包含不安全的内部路径")
    return canonical.rstrip("/") if name.endswith("/") else canonical


def _is_pptx_media_member(name: str) -> bool:
    return name.lower().startswith("ppt/media/") and not name.endswith("/")


def _validate_office_archive(
    archive: Any,
    *,
    allow_large_presentation_media: bool = False,
) -> None:
    """Reject unsafe Office ZIP metadata before opening any member payload."""
    infos = archive.infolist()
    if len(infos) > _MAX_OFFICE_ARCHIVE_FILES:
        raise ValueError("Office 文件包含过多内部文件")
    seen: set[str] = set()
    total = 0
    supported_compression = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    for info in infos:
        member_name = _archive_member_key(info.filename)
        if member_name in seen:
            raise ValueError("Office 文件包含重复的内部文件")
        seen.add(member_name)
        if info.flag_bits & 0x1:
            raise ValueError("不支持加密的 Office 文件")
        if info.compress_type not in supported_compression:
            raise ValueError("Office 文件使用不支持的压缩格式")

        is_media = allow_large_presentation_media and _is_pptx_media_member(member_name)
        member_limit = (
            None
            if is_media
            else (
                _MAX_OFFICE_XML_BYTES
                if member_name.lower().endswith((".xml", ".rels"))
                else _MAX_OFFICE_ARCHIVE_MEMBER_BYTES
            )
        )
        if member_limit is not None and info.file_size > member_limit:
            raise ValueError("Office 文件内部数据过大")
        if not is_media:
            total += info.file_size
            if total > _MAX_OFFICE_ARCHIVE_BYTES:
                raise ValueError("Office 文件解压后超过安全限制")

        if (
            info.compress_type != zipfile.ZIP_STORED
            and info.file_size >= _MAX_OFFICE_COMPRESSION_CHECK_BYTES
            and info.file_size
            > max(info.compress_size, 1) * _MAX_OFFICE_COMPRESSION_RATIO
        ):
            raise ValueError("Office 文件压缩比例异常")


def normalize_source_relative_path(
    source_relative_path: str | None,
    filename: str,
) -> str:
    """Normalize and validate a browser-provided relative upload path."""
    normalized_filename = unicodedata.normalize("NFC", filename.strip())
    if (
        not normalized_filename
        or "/" in normalized_filename
        or "\\" in normalized_filename
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized_filename)
    ):
        raise ValueError("文件名无效")

    raw_path = (source_relative_path or normalized_filename).strip()
    normalized_path = unicodedata.normalize("NFC", raw_path).replace("\\", "/")
    if (
        not normalized_path
        or len(normalized_path) > _MAX_SOURCE_PATH_LENGTH
        or normalized_path.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized_path)
    ):
        raise ValueError("来源路径必须是安全的相对路径")

    parts = normalized_path.split("/")
    if (
        len(parts) > _MAX_SOURCE_PATH_DEPTH
        or any(
            not part
            or part in {".", ".."}
            or len(part) > _MAX_SOURCE_PATH_COMPONENT_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in part)
            for part in parts
        )
        or parts[-1] != normalized_filename
    ):
        raise ValueError("来源路径无效，必须以当前文件名结尾")
    return "/".join(parts)


def validate_file(
    filename: str,
    size: int,
    *,
    max_bytes: int | None = None,
    allow_presentation_templates: bool = False,
) -> str | None:
    """Return a localized error message if a browser upload is invalid."""
    ext = Path(filename).suffix.lower()
    if ext == ".ppt" and not allow_presentation_templates:
        return "暂不支持旧版 PPT 文件，请先另存为 PPTX 后再上传。"
    if ext not in ACCEPTED_EXTENSIONS and not (
        allow_presentation_templates and ext == ".potx"
    ):
        return f"不支持的文件格式：{ext}。支持 PDF、Word、PPTX、Excel、文本，以及 JPG、PNG、WebP、GIF、BMP、TIFF、HEIC、AVIF 等图片。"
    limit = settings.max_upload_mb * 1024 * 1024 if max_bytes is None else max_bytes
    if ext in IMAGE_EXTENSIONS and size > MAX_IMAGE_BYTES:
        return f"图片大小超过限制（{MAX_IMAGE_BYTES // (1024 * 1024)} MB）。"
    if size > limit:
        if max_bytes is None:
            return f"文件大小超过限制（{settings.max_upload_mb} MB）。"
        return "文件大小超过当前上传限制。"
    return None


def stable_knowledge_id(entity_id: str) -> int:
    """Map string entity id to a positive 63-bit int."""
    digest = hashlib.sha256(entity_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1) or 1


def derive_category(space_id: str) -> str:
    """Category for a knowledge space."""
    clean = space_id.replace("-", "_").replace(".", "_")
    return f"docnexus_{clean}"


def derive_namespace(space_id: str, document_id: str) -> str:
    """Legacy helper kept for callers."""
    clean_space = space_id.replace("-", "_").replace(".", "_")
    clean_doc = document_id.replace("-", "_").replace(".", "_")
    return f"docnexus_{clean_space}_{clean_doc}"


_XML_ENCODING_RE = re.compile(rb"<\?xml[^>]*encoding=['\"]([\w.\-]+)['\"]", re.I)
_HTML_CHARSET_RE = re.compile(rb"charset\s*=\s*['\"]?([\w.\-]+)", re.I)
_ENCODING_ALIASES = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "gbk": "gb18030",
    "gb2312": "gb18030",
    "gbk2312": "gb18030",
    "gb18030": "gb18030",
}


def _normalize_extension(extension: str) -> str:
    ext = (extension or "").strip().lower()
    if not ext:
        return ""
    if "/" in ext or "\\" in ext:
        ext = Path(ext).suffix.lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return ext


def _local_xml_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_attr(element: Any, name: str) -> str | None:
    for key, value in element.attrib.items():
        if key == name or _local_xml_tag(key) == name:
            return value
    return None


def _declared_text_encoding(content: bytes) -> str | None:
    head = content[:2048]
    match = _XML_ENCODING_RE.search(head) or _HTML_CHARSET_RE.search(head)
    if not match:
        return None
    raw = match.group(1).decode("ascii", errors="ignore").strip().lower()
    return _ENCODING_ALIASES.get(raw, raw) or None


def decode_text_bytes(content: bytes) -> str:
    """Decode CSV/HTML/text bytes; prefer declared charset, then UTF-8 / GB18030."""
    if not content:
        return ""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", errors="replace")
    if content.startswith(b"\xff\xfe"):
        return content.decode("utf-16-le", errors="replace")
    if content.startswith(b"\xfe\xff"):
        return content.decode("utf-16-be", errors="replace")

    candidates: list[str] = []
    declared = _declared_text_encoding(content)
    if declared:
        candidates.append(declared)
    for encoding in ("utf-8", "gb18030"):
        if encoding not in candidates:
            candidates.append(encoding)
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _escape_md_cell(value: str) -> str:
    return (
        (value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", " ")
        .replace("|", "\\|")
        .strip()
    )


def _rows_to_markdown(sheet_name: str, rows: list[list[str]]) -> str:
    normalized: list[list[str]] = []
    for row in rows:
        cells = [str(cell).strip() if cell is not None else "" for cell in row]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            normalized.append(cells)
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    padded = [row + [""] * (width - len(row)) for row in normalized]
    header = padded[0]
    lines = [
        f"## {sheet_name}".strip(),
        "",
        "| " + " | ".join(_escape_md_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(_escape_md_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _xlsx_column_name(index: int) -> str:
    name = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _sparse_cells_to_markdown(
    sheet_name: str,
    values: dict[tuple[int, int], str],
) -> str:
    lines = [
        f"## {sheet_name}".strip(),
        "",
        "| 单元格 | 内容 |",
        "| --- | --- |",
    ]
    for (row_idx, col_idx), value in sorted(values.items()):
        ref = f"{_xlsx_column_name(col_idx)}{row_idx + 1}"
        lines.append(f"| {ref} | {_escape_md_cell(value)} |")
    return "\n".join(lines)


def _col_index_from_ref(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return max(0, index - 1)


def _row_index_from_ref(cell_ref: str) -> int:
    digits = "".join(char for char in cell_ref if char.isdigit())
    if not digits:
        return 0
    try:
        return int(digits) - 1
    except ValueError:
        return 0


def _xlsx_shared_strings(archive: Any) -> list[str]:
    try:
        with archive.open("xl/sharedStrings.xml") as handle:
            tree = ET.parse(handle)
    except KeyError:
        return []
    strings: list[str] = []
    for si_el in tree.getroot().iter():
        if _local_xml_tag(si_el.tag) != "si":
            continue
        texts = [
            node.text or "" for node in si_el.iter() if _local_xml_tag(node.tag) == "t"
        ]
        strings.append("".join(texts))
    return strings


def _xlsx_sheet_targets(archive: Any) -> list[tuple[str, str]]:
    from posixpath import dirname, join, normpath

    try:
        with archive.open("xl/workbook.xml") as handle:
            workbook = ET.parse(handle).getroot()
        with archive.open("xl/_rels/workbook.xml.rels") as handle:
            rels_root = ET.parse(handle).getroot()
    except KeyError as exc:
        raise ValueError("XLSX 缺少 workbook 关系文件") from exc

    rels: dict[str, str] = {}
    for rel in rels_root:
        if _local_xml_tag(rel.tag) != "Relationship":
            continue
        rel_id = rel.attrib.get("Id") or ""
        target = (rel.attrib.get("Target") or "").replace("\\", "/")
        if not rel_id or not target:
            continue
        if target.startswith("/"):
            rels[rel_id] = target.lstrip("/")
        else:
            rels[rel_id] = normpath(join(dirname("xl/workbook.xml"), target))

    sheets: list[tuple[str, str]] = []
    for sheet in workbook.iter():
        if _local_xml_tag(sheet.tag) != "sheet":
            continue
        name = _xml_attr(sheet, "name") or f"Sheet{len(sheets) + 1}"
        rel_id = _xml_attr(sheet, "id") or ""
        target = rels.get(rel_id, "")
        if target:
            sheets.append((name, target))
    return sheets


def _xlsx_cell_value(cell: Any, shared_strings: list[str]) -> str:
    cell_type = (_xml_attr(cell, "t") or "").strip()
    if cell_type == "s":
        raw = ""
        for node in cell.iter():
            if _local_xml_tag(node.tag) == "v" and node.text:
                raw = node.text
                break
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter() if _local_xml_tag(node.tag) == "t"
        )
    if cell_type == "b":
        raw = ""
        for node in cell.iter():
            if _local_xml_tag(node.tag) == "v" and node.text is not None:
                raw = node.text
                break
        return "TRUE" if raw.strip() in {"1", "true", "TRUE"} else "FALSE"
    value = ""
    for node in cell.iter():
        if _local_xml_tag(node.tag) == "v" and node.text is not None:
            value = node.text
            break
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        try:
            return str(int(number))
        except (OverflowError, ValueError):
            return value
    return value


def extract_xlsx_text(content: bytes) -> str:
    """Extract worksheet tables from an XLSX package as Markdown."""
    import zipfile

    if not content:
        return ""
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        _validate_office_archive(archive)
        shared_strings = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_targets(archive)
        if not sheets:
            sheet_names = [
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet")
            ]
            sheets = [
                (f"Sheet{index}", name)
                for index, name in enumerate(sheet_names, start=1)
            ]
        for sheet_name, target in sheets:
            try:
                with archive.open(target) as handle:
                    tree = ET.parse(handle)
            except KeyError:
                continue
            values: dict[tuple[int, int], str] = {}
            max_row = -1
            max_col = -1
            for cell in tree.getroot().iter():
                if _local_xml_tag(cell.tag) != "c":
                    continue
                ref = _xml_attr(cell, "r") or ""
                if not ref:
                    continue
                row_idx = _row_index_from_ref(ref)
                col_idx = _col_index_from_ref(ref)
                text = _xlsx_cell_value(cell, shared_strings)
                if not text:
                    continue
                values[(row_idx, col_idx)] = text
                max_row = max(max_row, row_idx)
                max_col = max(max_col, col_idx)
            if max_row < 0:
                continue
            dense_cell_count = (max_row + 1) * (max_col + 1)
            if dense_cell_count > _MAX_XLSX_DENSE_CELLS:
                rendered = _sparse_cells_to_markdown(sheet_name, values)
            else:
                rows = [
                    [
                        values.get((row_idx, col_idx), "")
                        for col_idx in range(max_col + 1)
                    ]
                    for row_idx in range(max_row + 1)
                ]
                rendered = _rows_to_markdown(sheet_name, rows)
            if rendered:
                parts.append(rendered)
    return "\n\n".join(parts)


def extract_xls_text(content: bytes) -> str:
    """Extract worksheet tables from legacy BIFF .xls via xlrd."""
    import xlrd

    if not content:
        return ""
    try:
        workbook = xlrd.open_workbook(file_contents=content, formatting_info=False)
    except Exception as exc:
        raise ValueError(f"无法解析 XLS 文件：{exc}") from exc

    parts: list[str] = []
    for sheet in workbook.sheets():
        if sheet.nrows == 0 or sheet.ncols == 0:
            continue
        rows: list[list[str]] = []
        for row_idx in range(sheet.nrows):
            row: list[str] = []
            for col_idx in range(sheet.ncols):
                cell = sheet.cell(row_idx, col_idx)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    if not isinstance(cell.value, (int, float)):
                        row.append(str(cell.value))
                        continue
                    try:
                        dt = xlrd.xldate_as_datetime(
                            float(cell.value), workbook.datemode
                        )
                        row.append(
                            dt.date().isoformat()
                            if dt.hour == 0
                            and dt.minute == 0
                            and dt.second == 0
                            and dt.microsecond == 0
                            else dt.isoformat(sep=" ", timespec="seconds")
                        )
                    except Exception:
                        row.append(str(cell.value))
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    row.append("TRUE" if cell.value else "FALSE")
                elif cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                    row.append("")
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    value = cell.value
                    if isinstance(value, float) and value.is_integer():
                        try:
                            row.append(str(int(value)))
                        except (OverflowError, ValueError):
                            row.append(str(value))
                    else:
                        row.append(str(value))
                else:
                    row.append(str(cell.value).strip())
            rows.append(row)
        rendered = _rows_to_markdown(sheet.name or f"Sheet{sheet.number + 1}", rows)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _html_tables_to_markdown(text: str) -> str:
    from html import unescape
    from html.parser import HTMLParser

    class _HTMLTableParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.tables: list[list[list[str]]] = []
            self._table: list[list[str]] | None = None
            self._row: list[str] | None = None
            self._cell: list[str] | None = None

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            tag = tag.lower()
            if tag == "table":
                self._table = []
            elif tag == "tr" and self._table is not None:
                self._row = []
            elif tag in {"td", "th"} and self._row is not None:
                self._cell = []

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag in {"td", "th"} and self._cell is not None and self._row is not None:
                self._row.append(
                    unescape("".join(self._cell)).replace("\xa0", " ").strip()
                )
                self._cell = None
            elif tag == "tr" and self._row is not None and self._table is not None:
                if any(self._row):
                    self._table.append(self._row)
                self._row = None
            elif tag == "table" and self._table is not None:
                if self._table:
                    self.tables.append(self._table)
                self._table = None

        def handle_data(self, data: str) -> None:
            if self._cell is not None:
                self._cell.append(data)

    parser = _HTMLTableParser()
    parser.feed(text)
    parser.close()
    parts = [
        _rows_to_markdown(f"Sheet{index}", rows)
        for index, rows in enumerate(parser.tables, start=1)
    ]
    return "\n\n".join(part for part in parts if part)


def _spreadsheetml_to_markdown(text: str) -> str:
    xml_text = re.sub(r"<\?[^?]+\?>", "", text).strip()
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        del exc
        return ""

    parts: list[str] = []
    for worksheet in root.iter():
        if _local_xml_tag(worksheet.tag) != "Worksheet":
            continue
        sheet_name = _xml_attr(worksheet, "Name") or f"Sheet{len(parts) + 1}"
        rows: list[list[str]] = []
        for row_el in worksheet.iter():
            if _local_xml_tag(row_el.tag) != "Row":
                continue
            cells: list[str] = []
            next_index = 1
            for cell_el in list(row_el):
                if _local_xml_tag(cell_el.tag) != "Cell":
                    continue
                declared_index = _xml_attr(cell_el, "Index")
                try:
                    index = (
                        int(declared_index)
                        if declared_index and declared_index.isdigit()
                        else next_index
                    )
                except (TypeError, ValueError):
                    index = next_index
                while len(cells) < index - 1:
                    cells.append("")
                data_text = ""
                for data_el in cell_el.iter():
                    if _local_xml_tag(data_el.tag) == "Data":
                        data_text = "".join(data_el.itertext()).strip()
                        break
                cells.append(data_text)
                next_index = index + 1
            if any(cells):
                rows.append(cells)
        rendered = _rows_to_markdown(sheet_name, rows)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def extract_spreadsheet_text(content: bytes, extension: str = "") -> str:
    """Extract readable tables from .xlsx / .xls, including HTML/XML Excel exports."""
    if not content:
        return ""
    ext = _normalize_extension(extension)
    head = content[:8]
    if head.startswith(b"PK"):
        return extract_xlsx_text(content)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return extract_xls_text(content)

    decoded = decode_text_bytes(content)
    preview = decoded.lstrip()[:8000].lower()
    if (
        "urn:schemas-microsoft-com:office:spreadsheet" in preview
        or "excel.sheet" in preview
    ):
        rendered = _spreadsheetml_to_markdown(decoded)
        if rendered.strip():
            return rendered
    if "<table" in preview or preview.lstrip().startswith("<html"):
        rendered = _html_tables_to_markdown(decoded)
        if rendered.strip():
            return rendered
    if ext in {".xls", ".xlsx"}:
        try:
            return extract_xls_text(content)
        except Exception:
            logger.exception("failed to parse spreadsheet as BIFF xls")
        rendered = _html_tables_to_markdown(decoded)
        if rendered.strip():
            return rendered
    return decoded.strip()


def extract_docx_text(content: bytes) -> str:
    """Extract plain text from a DOCX package (paragraphs, including table cells)."""
    import zipfile

    if not content:
        return ""

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        _validate_office_archive(archive)
        try:
            with archive.open("word/document.xml") as handle:
                tree = ET.parse(handle)
        except KeyError as exc:
            raise ValueError("DOCX 缺少 word/document.xml") from exc

    for para in tree.findall(".//w:p", ns):
        # Join all text runs in the paragraph; do not treat the joined string as an XML node.
        para_text = "".join(node.text or "" for node in para.findall(".//w:t", ns))
        if para_text.strip():
            paragraphs.append(para_text)

    if paragraphs:
        return "\n\n".join(paragraphs)

    # Fallback: some odd packages still expose bare w:t nodes outside expected structure.
    texts = [
        (node.text or "").strip()
        for node in tree.findall(".//w:t", ns)
        if (node.text or "").strip()
    ]
    return "\n".join(texts)


_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAX_DOC_PIECES = 4096
_MAX_DOC_TEXT_BYTES = 64 * 1024 * 1024
_MAX_DOC_ANSI_PIECE_CHARS = 4 * 1024 * 1024


def _is_ole2_doc(content: bytes) -> bool:
    """True when bytes look like a legacy binary Word .doc (OLE2 compound file)."""
    return content.startswith(_OLE2_MAGIC)


def extract_doc_text(content: bytes) -> str:
    """Extract plain text from a legacy binary Word .doc (OLE2 compound file).

    Reads the FIB's fcClx/lcbClx (via the fibRgFcLcbBlob, which stays valid
    across Word versions), walks the CLX piece table and decodes each piece
    as UTF-16LE (or ANSI/CP1252 when the compressed flag is set). The text
    stream may live in either the WordDocument stream or the 0Table/1Table
    stream; the piece offset selects the correct one.

    Raises ValueError for malformed or hostile inputs.
    """
    import struct

    import olefile

    if not content:
        return ""
    if not _is_ole2_doc(content):
        raise ValueError("不是有效的 Word 97-2003 (.doc) 文件")

    try:
        ole = olefile.OleFileIO(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"无法解析 DOC 文件：{exc}") from exc
    try:
        for name in ("WordDocument",):
            if not ole.exists(name):
                raise ValueError("DOC 缺少 WordDocument 流")
        wd = ole.openstream("WordDocument").read()
        if len(wd) > _MAX_DOC_TEXT_BYTES:
            raise ValueError("DOC 文件过大")

        table_name = "1Table" if ole.exists("1Table") else "0Table"
        table = ole.openstream(table_name).read()

        # FIB header navigation (see [MS-DOC] 2.5.1 FibBase / FibRgFcLcbBlob).
        wIdent, nFib = struct.unpack_from("<HH", wd, 0)
        del nFib
        if wIdent != 0xA5EC:
            raise ValueError("DOC 文件头无效")
        csw = struct.unpack_from("<H", wd, 0x20)[0]
        cslw_off = 0x22 + csw * 2
        cslw = struct.unpack_from("<H", wd, cslw_off)[0]
        cbRgFcLcb_off = cslw_off + 2 + cslw * 4
        blob_off = cbRgFcLcb_off + 2
        # fcClx/lcbClx live at pair index 33 of fibRgFcLcbBlob.
        if blob_off + 33 * 8 + 8 > len(wd):
            raise ValueError("DOC 文件结构不完整")
        fcClx, lcbClx = struct.unpack_from("<II", wd, blob_off + 33 * 8)
        if lcbClx <= 0 or fcClx + lcbClx > len(table):
            raise ValueError("DOC 文本索引缺失")

        pieces: list[tuple[int, int, int]] = []
        pos, end = fcClx, fcClx + lcbClx
        while pos < end:
            kind = table[pos]
            if kind == 0x01:  # Prc: nested Clx
                cb = struct.unpack_from("<H", table, pos + 1)[0]
                pos += 3
                sub_end = min(pos + cb, end)
                while pos < sub_end:
                    sub = table[pos]
                    if sub == 0x02:  # Pcdt
                        lcb = struct.unpack_from("<I", table, pos + 1)[0]
                        plc = table[pos + 5 : pos + 5 + lcb]
                        n = (lcb - 4) // 12
                        cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
                        pcds = plc[(n + 1) * 4 :]
                        for i in range(n):
                            fc_raw = struct.unpack_from("<I", pcds, i * 8 + 2)[0]
                            pieces.append((cps[i], cps[i + 1], fc_raw))
                        pos = sub_end
                    else:
                        pos += 1
            elif kind == 0x02:  # Pcdt at top level
                lcb = struct.unpack_from("<I", table, pos + 1)[0]
                plc = table[pos + 5 : pos + 5 + lcb]
                n = (lcb - 4) // 12
                cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
                pcds = plc[(n + 1) * 4 :]
                for i in range(n):
                    fc_raw = struct.unpack_from("<I", pcds, i * 8 + 2)[0]
                    pieces.append((cps[i], cps[i + 1], fc_raw))
                pos += 5 + lcb
            else:
                pos += 1

        if not pieces:
            return ""
        if len(pieces) > _MAX_DOC_PIECES:
            raise ValueError("DOC 文本分片过多")

        texts: list[str] = []
        total_chars = 0
        for cp0, cp1, fc_raw in pieces:
            if cp1 < cp0:
                continue
            nchars = cp1 - cp0
            if nchars <= 0:
                continue
            compressed = bool(fc_raw & 0x40000000)
            fc = fc_raw & 0x3FFFFFFF
            stream = wd if fc < len(wd) else table
            off = fc if fc < len(wd) else fc - len(wd)
            if compressed:
                if nchars > _MAX_DOC_ANSI_PIECE_CHARS:
                    raise ValueError("DOC 文本分片过大")
                data = stream[off : off + nchars]
                texts.append(data.decode("cp1252", errors="replace"))
            else:
                data = stream[off : off + nchars * 2]
                texts.append(data.decode("utf-16-le", errors="replace"))
            total_chars += nchars
            if total_chars > _MAX_DOC_ANSI_PIECE_CHARS:
                raise ValueError("DOC 文本过大")

        if not texts:
            return ""
        text = "".join(texts).replace("\r", "\n")
        return "\n".join(line.rstrip() for line in text.split("\n")).strip()
    finally:
        ole.close()


@contextmanager
def _open_pptx_source(
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO,
) -> Iterator[BinaryIO]:
    """Open a PPTX source without copying path-backed or caller-owned streams."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        stream = io.BytesIO(bytes(source))
        try:
            yield stream
        finally:
            stream.close()
        return

    if isinstance(source, (str, os.PathLike)):
        try:
            with open(os.fspath(source), "rb") as stream:
                yield stream
        except OSError as exc:
            raise ValueError(f"PPTX 文件无法读取：{exc}") from exc
        return

    if not hasattr(source, "read"):
        raise TypeError("PPTX 输入必须是 bytes、路径或二进制流")
    try:
        original_position = source.tell()
        source.seek(0)
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        io.UnsupportedOperation,
    ) as exc:
        raise ValueError("PPTX 输入必须是可定位的二进制流") from exc
    try:
        yield source
    finally:
        try:
            source.seek(original_position)
        except (
            AttributeError,
            OSError,
            TypeError,
            ValueError,
            io.UnsupportedOperation,
        ):
            pass


def _read_pptx_xml(
    archive: zipfile.ZipFile,
    member_name: str,
    label: str,
) -> Any:
    try:
        info = archive.getinfo(member_name)
    except KeyError as exc:
        raise ValueError(f"PPTX 缺少 {label}") from exc
    if info.file_size > _MAX_OFFICE_XML_BYTES:
        raise ValueError(f"PPTX {label} XML 过大")
    try:
        with archive.open(info) as handle:
            return ET.parse(handle).getroot()
    except Exception as exc:
        raise ValueError(f"PPTX {label} XML 无法解析") from exc


def _resolve_pptx_target(source_part: str, target: str) -> str:
    target = (target or "").strip()
    if (
        not target
        or "\\" in target
        or "\x00" in target
        or target.startswith("//")
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target)
    ):
        raise ValueError("PPTX 关系包含不安全的目标路径")
    rooted = target.startswith("/")
    if rooted:
        target = target[1:]
    candidate = (
        target
        if rooted or not source_part
        else posixpath.join(posixpath.dirname(source_part), target)
    )
    normalized = posixpath.normpath(candidate)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise ValueError("PPTX 关系包含不安全的目标路径")
    return normalized


def _read_pptx_relationships(
    archive: zipfile.ZipFile,
    relationships_name: str,
    source_part: str,
    label: str,
) -> dict[str, tuple[str, str | None, bool]]:
    root = _read_pptx_xml(archive, relationships_name, label)
    if (
        _local_xml_tag(root.tag) != "Relationships"
        or _xml_namespace(root.tag) not in _PACKAGE_RELATIONSHIPS_NAMESPACES
    ):
        raise ValueError(f"PPTX {label}关系文件无效")

    relationships: dict[str, tuple[str, str | None, bool]] = {}
    for relation in root:
        if _local_xml_tag(relation.tag) != "Relationship":
            continue
        relation_id = (relation.attrib.get("Id") or "").strip()
        relation_type = (relation.attrib.get("Type") or "").strip()
        target = (relation.attrib.get("Target") or "").strip()
        if not relation_id or not relation_type or not target:
            raise ValueError(f"PPTX {label}关系缺少必要属性")
        if relation_id in relationships:
            raise ValueError(f"PPTX {label}关系包含重复 ID")
        external = (
            relation.attrib.get("TargetMode") or ""
        ).strip().lower() == "external"
        relationships[relation_id] = (
            relation_type,
            None if external else _resolve_pptx_target(source_part, target),
            external,
        )
    return relationships


def _pptx_relationship_id(element: Any) -> str | None:
    for key, value in element.attrib.items():
        if key == "r:id" or (
            _local_xml_tag(key) == "id"
            and _xml_namespace(key) in _OFFICE_RELATIONSHIPS_NAMESPACES
        ):
            return value
    return None


def _relationship_part_name(part_name: str) -> str:
    directory, basename = posixpath.split(part_name)
    return posixpath.join(directory, "_rels", f"{basename}.rels")


def _require_pptx_root(root: Any, local_name: str, label: str) -> None:
    if (
        _local_xml_tag(root.tag) != local_name
        or _xml_namespace(root.tag) not in _PRESENTATION_NAMESPACES
    ):
        raise ValueError(f"PPTX {label} XML 根节点无效")


def _prepare_pptx_archive(
    archive: zipfile.ZipFile,
) -> list[tuple[int, str, str | None]]:
    """Validate OPC/PML relationships and return ordered slide targets."""
    _validate_office_archive(archive, allow_large_presentation_media=True)
    member_names = set(archive.namelist())

    content_types = _read_pptx_xml(
        archive,
        "[Content_Types].xml",
        "内容类型",
    )
    if (
        _local_xml_tag(content_types.tag) != "Types"
        or _xml_namespace(content_types.tag) not in _PACKAGE_CONTENT_TYPES_NAMESPACES
    ):
        raise ValueError("PPTX 内容类型文件无效")
    presentation_content_type = {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "application/vnd.ms-powerpoint.presentationml.presentation.main+xml",
        # POTX 与 PPTX 共用包结构；模板编译和聊天临时附件均使用同一校验。
        "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    }
    if not any(
        _local_xml_tag(element.tag) == "Override"
        and (element.attrib.get("PartName") or "").lstrip("/") == "ppt/presentation.xml"
        and (element.attrib.get("ContentType") or "").strip().lower()
        in presentation_content_type
        for element in content_types
    ):
        raise ValueError("PPTX 缺少 presentation 内容类型声明")

    package_relationships = _read_pptx_relationships(
        archive,
        "_rels/.rels",
        "",
        "包",
    )
    office_document_targets = [
        target
        for relation_type, target, external in package_relationships.values()
        if not external and relation_type.rsplit("/", 1)[-1].lower() == "officedocument"
    ]
    if office_document_targets != ["ppt/presentation.xml"]:
        raise ValueError("PPTX 缺少有效的 presentation 包关系")

    presentation = _read_pptx_xml(
        archive,
        "ppt/presentation.xml",
        "presentation",
    )
    _require_pptx_root(presentation, "presentation", "presentation")
    slide_id_list = next(
        (
            element
            for element in presentation.iter()
            if _local_xml_tag(element.tag) == "sldIdLst"
        ),
        None,
    )
    if slide_id_list is None:
        raise ValueError("PPTX 缺少幻灯片列表")

    presentation_relationships = _read_pptx_relationships(
        archive,
        "ppt/_rels/presentation.xml.rels",
        "ppt/presentation.xml",
        "presentation",
    )
    slides: list[tuple[int, str, str | None]] = []
    seen_relationship_ids: set[str] = set()
    for slide_id in slide_id_list:
        if _local_xml_tag(slide_id.tag) != "sldId":
            continue
        relation_id = _pptx_relationship_id(slide_id)
        if not relation_id or relation_id in seen_relationship_ids:
            raise ValueError("PPTX 幻灯片关系引用无效")
        seen_relationship_ids.add(relation_id)
        relation = presentation_relationships.get(relation_id)
        if relation is None:
            raise ValueError("PPTX 幻灯片关系缺失")
        relation_type, slide_target, external = relation
        if (
            external
            or slide_target is None
            or not slide_target.lower().startswith("ppt/slides/")
            or not slide_target.lower().endswith(".xml")
            or relation_type.rsplit("/", 1)[-1].lower() != "slide"
            or slide_target not in member_names
        ):
            raise ValueError("PPTX 幻灯片目标无效")

        notes_target: str | None = None
        slide_relationships_name = _relationship_part_name(slide_target)
        if slide_relationships_name in member_names:
            slide_relationships = _read_pptx_relationships(
                archive,
                slide_relationships_name,
                slide_target,
                f"幻灯片 {len(slides) + 1}",
            )
            for relation_type, candidate, external in slide_relationships.values():
                if relation_type.rsplit("/", 1)[-1].lower() != "notesslide":
                    continue
                if (
                    external
                    or candidate is None
                    or not candidate.lower().startswith("ppt/notesslides/")
                    or not candidate.lower().endswith(".xml")
                    or candidate not in member_names
                ):
                    raise ValueError("PPTX 备注页目标无效")
                if notes_target is not None:
                    raise ValueError("PPTX 备注页关系重复")
                notes_target = candidate
        slides.append((len(slides) + 1, slide_target, notes_target))
    return slides


def _pptx_text_lines(root: Any) -> list[str]:
    lines: list[str] = []
    for paragraph in root.iter():
        if (
            _local_xml_tag(paragraph.tag) != "p"
            or _xml_namespace(paragraph.tag) not in _DRAWINGML_NAMESPACES
        ):
            continue
        chunks: list[str] = []
        for node in paragraph.iter():
            if _xml_namespace(node.tag) not in _DRAWINGML_NAMESPACES:
                continue
            local_name = _local_xml_tag(node.tag)
            if local_name == "t":
                chunks.append(node.text or "")
            elif local_name == "br":
                chunks.append("\n")
            elif local_name == "tab":
                chunks.append("\t")
        line = "".join(chunks).strip()
        if line:
            lines.append(line)
    return lines


def validate_pptx_archive(source: bytes | Path | BinaryIO) -> None:
    """Validate a PPTX package without reading or decompressing media members."""
    try:
        with _open_pptx_source(source) as stream:
            with zipfile.ZipFile(stream) as archive:
                slides = _prepare_pptx_archive(archive)
                for slide_index, slide_target, notes_target in slides:
                    slide = _read_pptx_xml(
                        archive,
                        slide_target,
                        f"幻灯片 {slide_index}",
                    )
                    _require_pptx_root(slide, "sld", f"幻灯片 {slide_index}")
                    if notes_target is not None:
                        notes = _read_pptx_xml(
                            archive,
                            notes_target,
                            f"幻灯片 {slide_index} 备注",
                        )
                        _require_pptx_root(
                            notes,
                            "notes",
                            f"幻灯片 {slide_index} 备注",
                        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"PPTX 文件无法验证：{exc}") from exc


def extract_pptx_text(content: bytes | Path | BinaryIO) -> str:
    """Extract ordered slide/body/table/group text and speaker notes from PPTX."""
    if isinstance(content, (bytes, bytearray, memoryview)) and not content:
        return ""

    try:
        with _open_pptx_source(content) as stream:
            with zipfile.ZipFile(stream) as archive:
                slides = _prepare_pptx_archive(archive)
                parts: list[str] = []
                for slide_index, slide_target, notes_target in slides:
                    slide = _read_pptx_xml(
                        archive,
                        slide_target,
                        f"幻灯片 {slide_index}",
                    )
                    _require_pptx_root(slide, "sld", f"幻灯片 {slide_index}")
                    slide_lines = _pptx_text_lines(slide)
                    notes_lines: list[str] = []
                    if notes_target is not None:
                        notes = _read_pptx_xml(
                            archive,
                            notes_target,
                            f"幻灯片 {slide_index} 备注",
                        )
                        _require_pptx_root(
                            notes,
                            "notes",
                            f"幻灯片 {slide_index} 备注",
                        )
                        notes_lines = _pptx_text_lines(notes)
                    if not slide_lines and not notes_lines:
                        continue
                    parts.append(f"--- Slide {slide_index} ---")
                    parts.extend(slide_lines)
                    if notes_lines:
                        parts.append("[备注] " + "\n".join(notes_lines))
                return "\n\n".join(parts)
    except Exception as exc:
        raise ValueError(f"无法解析 PPTX 文件：{exc}") from exc


def extract_pdf_text(content: bytes) -> str:
    """Extract plain text from a text-layer PDF.

    Scanned PDFs (image-only pages) yield empty text; callers should treat
    an empty result as a hint that OCR is required rather than a hard error.
    """
    from app.services.pdf_ocr import extract_pdf_pages

    return extract_pdf_pages(content).text


def extract_text_from_task_snapshot(snapshot: dict[str, Any] | None) -> str:
    """Collect human-readable slice text from a task snapshot."""
    if not snapshot:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    for stage in snapshot.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for item in stage.get("data") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content or content in seen:
                continue
            seen.add(content)
            parts.append(content)
    if parts:
        return "\n\n".join(parts)
    return ""


def extracted_sidecar_path(file_path: str | None) -> Path | None:
    """Return the local extracted-text cache path for a document reference."""
    if not file_path:
        return None
    if file_path.startswith("minio://"):
        # Object-backed originals use a deterministic local cache namespace.
        digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()
        return settings.upload_dir / "_extracted" / f"{digest}.txt"
    path = Path(file_path)
    return path.with_name(f"{path.name}.extracted.txt")


def write_extracted_sidecar(
    file_path: str | None,
    text: str,
    *,
    version_id: str | None = None,
) -> Path | None:
    """Refresh a cache only after retaining its exact old and new version text.

    Archived pre-existing text is an observation, not a retroactive binding for
    historical hashless citations. Without a version, existing caches are kept.
    """
    from app.services.evidence_snapshots import freeze_text

    target = extracted_sidecar_path(file_path)
    if not target or not text.strip():
        return None
    temporary: str | None = None
    try:
        if target.is_symlink():
            raise ValueError("Invalid extracted sidecar path")
        if target.exists():
            if version_id is None:
                return target
            previous = target.read_bytes().decode("utf-8")
            freeze_text(version_id, previous)
            if previous == text:
                return target
        if version_id is not None:
            freeze_text(version_id, text)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".extract-", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if version_id is None:
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        else:
            os.replace(temporary, target)
        return target
    except (OSError, ValueError, UnicodeError):
        logger.exception("failed to preserve extracted sidecar")
        return None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
