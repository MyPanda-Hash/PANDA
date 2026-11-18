"""Convert writing Markdown into OOXML paragraphs for a minimal .docx package."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE_RE = re.compile(r"^```")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{1,}:?$")
# Recognize both canonical markers and legacy/model variants before DOCX export.
# Examples:
#   ^[writing-evidence:ver:0-10]
#   [writing-evidence:ver:0-10]
#   [citation:writing-evidence:ver:0-10]
#   [citation:0]
_EVIDENCE_MARKER_RE = re.compile(
    r"\^?\[(?:citation:)?(?P<evidence>(?:writing-evidence|vector-evidence):[^\]\r\n]+)\]"
    r"|\^?\[citation:(?P<legacy>[^\]\r\n]+)\]"
)
_INLINE_TOKEN_RE = re.compile(
    r"==(.+?)==|"
    r"\*\*\*(.+?)\*\*\*|___(.+?)___|"
    r"\*\*(.+?)\*\*|__(.+?)__|"
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|"
    r"`([^`]+)`|"
    r"\[([^\]]+)\]\(([^)]+)\)"
)


def _text_run(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    code: bool = False,
    highlight: bool = False,
    color: str | None = None,
) -> str:
    """Emit one run verbatim.

    交付清洗（引用标记/展示标签）只发生在 prose 路径：这里同时服务代码块与
    行内代码，若在此剥标记，代码示例会被一起改掉。
    """
    if text == "":
        return ""
    ascii_font = "Consolas" if code else "Noto Sans SC"
    props: list[str] = [
        f'<w:rFonts w:ascii="{ascii_font}" w:hAnsi="{ascii_font}" '
        'w:eastAsia="Noto Sans SC" w:cs="Noto Sans SC" w:hint="eastAsia"/>',
        '<w:lang w:val="zh-CN" w:eastAsia="zh-CN"/>',
    ]
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if highlight:
        props.append('<w:highlight w:val="yellow"/>')
    if code:
        props.append('<w:sz w:val="20"/><w:szCs w:val="20"/>')
    r_pr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{r_pr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _inline_runs(text: str) -> str:
    # 只在 prose 上剥引用标记（行内代码片段保留字面量），再解析行内 Markdown：
    # 先剥标记可避免 citation id 里的下划线被当成斜体。
    text = _strip_markers_outside_code(text or "")
    if not text:
        return _text_run("")
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            parts.append(_text_run(text[cursor : match.start()]))
        highlight = match.group(1)
        bold_italic = match.group(2) or match.group(3)
        bold = match.group(4) or match.group(5)
        italic = match.group(6) or match.group(7)
        code = match.group(8)
        link_label = match.group(9)
        link_url = match.group(10)
        if highlight is not None:
            parts.append(_text_run(highlight, highlight=True))
        elif bold_italic is not None:
            parts.append(_text_run(bold_italic, bold=True, italic=True))
        elif bold is not None:
            parts.append(_text_run(bold, bold=True))
        elif italic is not None:
            parts.append(_text_run(italic, italic=True))
        elif code is not None:
            parts.append(_text_run(code, code=True))
        elif link_label is not None:
            parts.append(_text_run(link_label))
            if link_url and link_url != link_label:
                parts.append(_text_run(f" ({link_url})", italic=True))
        cursor = match.end()
    if cursor < len(text):
        parts.append(_text_run(text[cursor:]))
    return "".join(parts) or _text_run("")


# 交付文件不展示引用：除机器 citation ID 外，还要去掉应用约定的展示型标签。
# `^[…]` 是写作约定的行内引用写法；`[标签#序号]` 是展示型序号标签。
# 普通 Markdown 链接 [文字](地址) 与嵌套 `[[…]]` 不在此列，保留原样。
_DISPLAY_MARKER_RE = re.compile(
    r"\^\[[^\[\]\r\n]*\]"
    r"|(?<!\^)\[[^\[\]#\r\n]+#\d+\](?![\](])"
)
# 与 markdown_to_paragraphs 的 _FENCE_RE 保持一致：仅反引号围栏算代码块。
_FENCE_LINE_RE = re.compile(r"^(`{3,})")
_INLINE_CODE_SPAN_RE = re.compile(r"(`+)[^`]*?\1")
# 删除引用标记后可能留下「500ms 。」或「95%  |」这类多余空格，交付文件里收掉。
_SPACE_BEFORE_CJK_PUNCT_RE = re.compile(r"[ \t]+(?=[，。；：、！？…）】」』》”“’])")
_SPACE_BEFORE_TABLE_PIPE_RE = re.compile(r"[ \t]+(?=\|)")


def _strip_markers_outside_code(line: str) -> str:
    """只清洗行内代码之外的引用标记；代码里的字面示例必须原样保留。"""
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_SPAN_RE.finditer(line):
        parts.append(_strip_markers(line[cursor:match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_strip_markers(line[cursor:]))
    return "".join(parts)


def _strip_markers(text: str) -> str:
    if not (
        _EVIDENCE_MARKER_RE.search(text) or _DISPLAY_MARKER_RE.search(text)
    ):
        return text
    stripped = _DISPLAY_MARKER_RE.sub("", _EVIDENCE_MARKER_RE.sub("", text))
    stripped = _SPACE_BEFORE_TABLE_PIPE_RE.sub(" ", stripped)
    return _SPACE_BEFORE_CJK_PUNCT_RE.sub("", stripped)


def strip_evidence_markers(markdown: str) -> str:
    """交付正文不展示引用：标记只用于核验与留痕，不写进导出文件。

    围栏代码块与行内代码里的字面 `^[...]` 是示例内容，不参与清洗。
    """
    lines: list[str] = []
    fence: str | None = None
    for line in (markdown or "").split("\n"):
        marker = _FENCE_LINE_RE.match(line.strip())
        closing = (
            fence is not None
            and marker is not None
            and marker.group(1)[0] == fence[0]
            and len(marker.group(1)) >= len(fence)
        )
        if fence is not None:
            if closing:
                fence = None
            lines.append(line)
            continue
        if marker is not None:
            fence = marker.group(1)
            lines.append(line)
            continue
        lines.append(_strip_markers_outside_code(line))
    return "\n".join(lines)


def _split_table_row(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", text)]


def _is_table_separator(raw: str) -> bool:
    cells = _split_table_row(raw)
    return len(cells) >= 2 and all(
        _TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells
    )


# A4 两侧页边距各 1 英寸；两条 DOCX 打包路径都显式设置该值。
# 表格网格宽度与可用版心保持一致。
TABLE_TEXT_WIDTH_TWIPS = 9026
_MIN_COLUMN_SHARE = 0.12
_MAX_COLUMN_SHARE = 0.55


def _display_width(text: str) -> int:
    """Approximate column width in characters, counting CJK as double."""
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text
    )


def table_column_widths(rows: list[list[str]]) -> list[int]:
    """Column widths in twips, sized from the table's own cell contents.

    The longest cell sets a column's weight, compressed so one wide column
    cannot starve the others, and clamped so narrow columns stay usable.
    """
    columns = max(len(row) for row in rows)
    padded = [row + [""] * (columns - len(row)) for row in rows]
    weights = [
        max(1.0, float(max(_display_width(row[index]) for row in padded)) ** 0.6)
        for index in range(columns)
    ]
    total_weight = sum(weights)
    shares = [
        min(max(weight / total_weight, _MIN_COLUMN_SHARE), _MAX_COLUMN_SHARE)
        for weight in weights
    ]
    scale = sum(shares)
    widths = [int(TABLE_TEXT_WIDTH_TWIPS * share / scale) for share in shares]
    widths[0] += TABLE_TEXT_WIDTH_TWIPS - sum(widths)
    return widths


def _table_cell_xml(
    text: str,
    width: int,
    *,
    header: bool = False,
    alternate: bool = False,
) -> str:
    # 表头直接走 _text_run（verbatim），因此这里必须自己做 prose 清洗：
    # 否则直接调用 markdown_to_paragraphs 的入口会把引用标记带进表头。
    runs = (
        _text_run(_strip_markers_outside_code(text), bold=True, color="FFFFFF")
        if header
        else _inline_runs(text)
    )
    fill = "234B69" if header else "F3F7FA" if alternate else None
    shading = f'<w:shd w:fill="{fill}"/>' if fill else ""
    return (
        "<w:tc>"
        f'<w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}'
        '<w:tcMar><w:top w:w="110" w:type="dxa"/>'
        '<w:bottom w:w="110" w:type="dxa"/>'
        '<w:left w:w="140" w:type="dxa"/>'
        '<w:right w:w="140" w:type="dxa"/></w:tcMar></w:tcPr>'
        f"{_paragraph_xml(runs, spacing_after=0)}"
        "</w:tc>"
    )


def _table_xml(rows: list[list[str]]) -> str:
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    column_widths = table_column_widths(rows)
    borders = (
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="4" w:color="D8E2EC"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="D8E2EC"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D8E2EC"/>'
        "</w:tblBorders>"
    )
    table_rows: list[str] = []
    for row_index, row in enumerate(normalized_rows):
        header = row_index == 0
        header_pr = "<w:tblHeader/>" if header else ""
        cells = "".join(
            _table_cell_xml(
                cell,
                column_widths[index],
                header=header,
                alternate=row_index > 0 and row_index % 2 == 0,
            )
            for index, cell in enumerate(row)
        )
        table_rows.append(
            f"<w:tr><w:trPr><w:cantSplit/>{header_pr}</w:trPr>{cells}</w:tr>"
        )
    grid = (
        "<w:tblGrid>"
        + "".join(f'<w:gridCol w:w="{width}"/>' for width in column_widths)
        + "</w:tblGrid>"
    )
    return (
        "<w:tbl>"
        f'<w:tblPr><w:tblW w:w="{TABLE_TEXT_WIDTH_TWIPS}" w:type="dxa"/>'
        f'<w:tblLayout w:type="fixed"/>{borders}</w:tblPr>'
        f"{grid}"
        f"{''.join(table_rows)}"
        "</w:tbl>"
    )


def _collect_table_blocks(
    lines: list[str],
) -> tuple[list[str], dict[str, list[list[str]]]]:
    normalized: list[str] = []
    blocks: dict[str, list[list[str]]] = {}
    in_code = False
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            in_code = not in_code
            normalized.append(raw)
            index += 1
            continue
        if (
            not in_code
            and index + 1 < len(lines)
            and "|" in raw
            and _is_table_separator(lines[index + 1])
        ):
            rows = [_split_table_row(raw)]
            index += 2
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip() or "|" not in candidate:
                    break
                rows.append(_split_table_row(candidate))
                index += 1
            if len(rows[0]) >= 2:
                marker = f"__DOCX_TABLE_{len(blocks)}__"
                blocks[marker] = rows
                normalized.append(marker)
                continue
            index -= 2
        normalized.append(raw)
        index += 1
    return normalized, blocks


def _paragraph_xml(
    runs_xml: str,
    *,
    style: str | None = None,
    indent_twips: int = 0,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
) -> str:
    p_pr_parts: list[str] = []
    if style:
        p_pr_parts.append(f'<w:pStyle w:val="{style}"/>')
    if indent_twips:
        p_pr_parts.append(f'<w:ind w:left="{indent_twips}"/>')
    if spacing_before is not None or spacing_after is not None:
        before = spacing_before if spacing_before is not None else 0
        after = spacing_after if spacing_after is not None else 120
        p_pr_parts.append(f'<w:spacing w:before="{before}" w:after="{after}"/>')
    p_pr = f"<w:pPr>{''.join(p_pr_parts)}</w:pPr>" if p_pr_parts else ""
    return f"<w:p>{p_pr}{runs_xml}</w:p>"


def heading_paragraph(text: str, level: int) -> str:
    level = max(1, min(level, 6))
    return _paragraph_xml(_inline_runs(text.strip()), style=f"Heading{level}")


def plain_paragraph(text: str) -> str:
    return _paragraph_xml(_inline_runs(text))


def _strip_leading_heading_if_matches(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return markdown
    match = _HEADING_RE.match(lines[index].strip())
    if not match:
        return markdown
    if match.group(2).strip() != title.strip():
        return markdown
    del lines[index]
    while index < len(lines) and not lines[index].strip():
        del lines[index]
    return "\n".join(lines)


def _table_gap_paragraph(height_twips: int) -> str:
    """用精确行高承载表格的块间距，避免 Word 默认空段落变成整行。"""
    return (
        '<w:p><w:pPr><w:spacing w:before="0" w:after="0" '
        f'w:line="{height_twips}" w:lineRule="exact"/>'
        "<w:keepNext/></w:pPr></w:p>"
    )


def markdown_to_paragraphs(markdown: str) -> list[str]:
    """Return OOXML <w:p>…</w:p> fragments for the given Markdown body."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines, table_blocks = _collect_table_blocks(lines)
    last_content_index = (
        next(
            (index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()),
            -1,
        )
        if table_blocks
        else -1
    )
    paragraphs: list[str] = []
    paragraph_buf: list[str] = []
    in_code = False
    code_buf: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_buf
        if not paragraph_buf:
            return
        text = " ".join(part.strip() for part in paragraph_buf).strip()
        paragraph_buf = []
        if text:
            paragraphs.append(plain_paragraph(text))

    def flush_code() -> None:
        nonlocal code_buf
        body = "\n".join(code_buf)
        code_buf = []
        if not body:
            paragraphs.append(
                _paragraph_xml(_text_run("", code=True), spacing_after=120)
            )
            return
        for line in body.split("\n"):
            paragraphs.append(
                _paragraph_xml(
                    _text_run(line if line else " ", code=True),
                    indent_twips=200,
                    spacing_before=0,
                    spacing_after=0,
                )
            )
        # trailing spacing after code block
        paragraphs.append(_paragraph_xml("", spacing_after=120))

    for line_index, raw in enumerate(lines):
        if raw in table_blocks and not in_code:
            flush_paragraph()
            if paragraphs:
                paragraphs.append(_table_gap_paragraph(120))
            paragraphs.append(_table_xml(table_blocks[raw]))
            if line_index < last_content_index:
                paragraphs.append(_table_gap_paragraph(160))
            continue

        if in_code:
            if _FENCE_RE.match(raw.strip()):
                in_code = False
                flush_code()
            else:
                code_buf.append(raw)
            continue

        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            flush_paragraph()
            in_code = True
            code_buf = []
            continue

        if not stripped:
            flush_paragraph()
            continue

        if _HR_RE.match(stripped):
            flush_paragraph()
            paragraphs.append(
                _paragraph_xml(
                    _text_run("─" * 24),
                    spacing_before=120,
                    spacing_after=120,
                )
            )
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            paragraphs.append(
                heading_paragraph(heading.group(2), len(heading.group(1)))
            )
            continue

        quote = _BLOCKQUOTE_RE.match(stripped)
        if quote:
            flush_paragraph()
            paragraphs.append(
                _paragraph_xml(
                    _inline_runs(quote.group(1)),
                    indent_twips=420,
                    spacing_after=80,
                )
            )
            continue

        unordered = _UL_RE.match(raw)
        if unordered:
            flush_paragraph()
            depth = len(unordered.group(1).replace("\t", "    ")) // 2
            paragraphs.append(
                _paragraph_xml(
                    _inline_runs(f"• {unordered.group(3)}"),
                    indent_twips=420 + depth * 210,
                    spacing_after=60,
                )
            )
            continue

        ordered = _OL_RE.match(raw)
        if ordered:
            flush_paragraph()
            depth = len(ordered.group(1).replace("\t", "    ")) // 2
            paragraphs.append(
                _paragraph_xml(
                    _inline_runs(f"{ordered.group(2)}. {ordered.group(3)}"),
                    indent_twips=420 + depth * 210,
                    spacing_after=60,
                )
            )
            continue

        paragraph_buf.append(stripped)

    if in_code:
        flush_code()
    else:
        flush_paragraph()
    return paragraphs


def render_project_docx_body(
    project_title: str, chapters: list[tuple[str, str]],
) -> str:
    """Build the inner <w:body> paragraph XML for a writing project export.

    交付文件不含引用编号与来源列表：证据只在页面展示，导出前核验仍然生效。
    """
    paragraphs: list[str] = [heading_paragraph(project_title, 1)] if project_title.strip() else []
    for title, content in chapters:
        body = strip_evidence_markers(content or "")
        if title.strip() and title.strip() != project_title.strip():
            paragraphs.append(heading_paragraph(title, 2))
        body = _strip_leading_heading_if_matches(body, project_title)
        body = _strip_leading_heading_if_matches(body, title)
        paragraphs.extend(markdown_to_paragraphs(body))
    return "".join(paragraphs)


FONT_TABLE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:fonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:font w:name="Noto Sans SC">
    <w:charset w:val="86"/>
    <w:family w:val="swiss"/>
    <w:pitch w:val="variable"/>
  </w:font>
</w:fonts>
"""


SETTINGS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:themeFontLang w:val="en-US" w:eastAsia="zh-CN"/>
</w:settings>
"""


STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="140" w:line="340" w:lineRule="auto"/><w:widowControl/></w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Noto Sans SC" w:hAnsi="Noto Sans SC" w:eastAsia="Noto Sans SC"/>
      <w:color w:val="263445"/>
      <w:sz w:val="22"/><w:szCs w:val="22"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:uiPriority w:val="9"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="360" w:after="260"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="1F3B5B"/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:uiPriority w:val="9"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="320" w:after="160"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="1"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="234B69"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:uiPriority w:val="9"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="240" w:after="120"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="2"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="355571"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading4">
    <w:name w:val="heading 4"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="200" w:after="100"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="3"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="355571"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading5">
    <w:name w:val="heading 5"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="180" w:after="100"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="4"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="355571"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading6">
    <w:name w:val="heading 6"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="160" w:after="100"/><w:keepNext/><w:keepLines/><w:outlineLvl w:val="5"/></w:pPr>
    <w:rPr><w:b/><w:i/><w:color w:val="355571"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
</w:styles>
"""


def markdown_to_docx_bytes(
    markdown: str, *, title: str | None = None,
) -> bytes:
    """Package Markdown as a delivery .docx without引用编号或来源列表。"""
    body = render_project_docx_body(title or "", [("", markdown)])
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
        "</Relationships>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
        archive.writestr("word/styles.xml", STYLES_XML)
        archive.writestr("word/fontTable.xml", FONT_TABLE_XML)
        archive.writestr("word/settings.xml", SETTINGS_XML)
    return output.getvalue()
