"""Deterministic DeckSpec compiler and PPTX renderer.

The model supplies prose and evidence markers. This module owns slide roles,
content budgets, visual hierarchy, and the editable PPTX output so the model
cannot emit arbitrary coordinates or PowerPoint XML.

Only the built-in business theme is compiled and rendered. Historical template
artifacts remain stored as original bytes; no template plan can start a render.
"""

from __future__ import annotations

import copy
import shutil
import zipfile
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Iterable, Mapping, Sequence

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.services import deck_roles

EMU_PER_INCH = 914400
EMU_PER_PT = 12700

DECK_SCHEMA_VERSION_V1 = 1

DEFAULT_MIN_FONT_PT = 18.0
# Built-in rendering keeps the historical extract/摘要 budgets.
_BUILTIN_BULLET_LIMIT = 90
_BUILTIN_PARAGRAPH_LIMIT = 150


class DeckSpecError(ValueError):
    """A DeckSpec that cannot be rendered without guessing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── geometry ────────────────────────────────────────────────────────────────


def _in(value: float) -> int:
    """Inches to EMU, matching ``pptx.util.Inches`` truncation exactly."""
    return int(value * EMU_PER_INCH)


@dataclass(frozen=True)
class Rect:
    """An absolute rectangle in EMU."""

    left: int
    top: int
    width: int
    height: int

    @classmethod
    def inches(cls, left: float, top: float, width: float, height: float) -> "Rect":
        return cls(_in(left), _in(top), _in(width), _in(height))

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def rect_emu(self) -> list[int]:
        return [self.left, self.top, self.width, self.height]

    @classmethod
    def from_sequence(cls, value: Sequence[Any]) -> "Rect":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise DeckSpecError("invalid_rect", "区域必须是 [left, top, width, height]")
        if len(value) != 4:
            raise DeckSpecError("invalid_rect", "区域必须是 4 个整数")
        try:
            left, top, width, height = (int(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise DeckSpecError("invalid_rect", "区域必须是整数 EMU") from exc
        if width <= 0 or height <= 0:
            raise DeckSpecError("invalid_rect", "区域宽高必须为正")
        return cls(left, top, width, height)

    def offset(self, left: float = 0.0, top: float = 0.0) -> "Rect":
        return Rect(self.left + _in(left), self.top + _in(top), self.width, self.height)

    def place(self, left: float, top: float, width: float, height: float) -> "Rect":
        """A rectangle expressed relative to this rectangle's top-left corner."""
        return Rect(self.left + _in(left), self.top + _in(top), _in(width), _in(height))

    def shrink(self, dx: float, dy: float = 0.0) -> "Rect":
        return Rect(
            self.left + _in(dx),
            self.top + _in(dy),
            max(1, self.width - 2 * _in(dx)),
            max(1, self.height - 2 * _in(dy)),
        )

    def contains(self, other: "Rect") -> bool:
        return (
            other.left >= self.left
            and other.top >= self.top
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def overlaps(self, other: "Rect") -> bool:
        return not (
            other.left >= self.right
            or other.right <= self.left
            or other.top >= self.bottom
            or other.bottom <= self.top
        )


@dataclass(frozen=True)
class DeckCanvas:
    width: int
    height: int

    @classmethod
    def inches(cls, width: float, height: float) -> "DeckCanvas":
        return cls(_in(width), _in(height))

    @property
    def aspect_ratio(self) -> str:
        ratio = self.width / self.height if self.height else 0.0
        return "16:9" if abs(ratio - 16 / 9) <= 0.02 else "4:3"


@dataclass(frozen=True)
class DeckTheme:
    """Colours and default fonts. One instance per rendering context."""

    key: str
    background: RGBColor
    surface: RGBColor
    accent: RGBColor
    accent_alt: RGBColor
    heading: RGBColor
    body: RGBColor
    muted: RGBColor
    divider: RGBColor
    on_dark: RGBColor
    on_dark_muted: RGBColor
    chip: RGBColor
    cover_background: RGBColor
    font_latin: str = "Aptos"
    font_ea: str = ""

    def font_for(self, text: str) -> str:
        return self.font_ea if self.font_ea and _has_wide_chars(text) else self.font_latin


@dataclass(frozen=True)
class FontMetrics:
    """Glyph advance estimates used when simulating line breaks.

    PowerPoint metrics are not available server-side. These ratios are a
    deliberate approximation; every render that relies on them reports
    ``font_measurement_substituted`` so the caller can label the estimate
    instead of presenting it as measured truth.
    """

    latin_advance: float = 0.52
    wide_advance: float = 1.0
    space_advance: float = 0.28
    line_height: float = 1.22
    paragraph_gap: float = 0.42
    substituted: bool = False


_DEFAULT_METRICS = FontMetrics()


@dataclass(frozen=True)
class RenderContext:
    """Everything a renderer helper needs that used to be a module constant."""

    theme: DeckTheme
    canvas: DeckCanvas
    regions: Mapping[str, Rect]
    metrics: FontMetrics = _DEFAULT_METRICS
    min_font_pt: float = DEFAULT_MIN_FONT_PT
    warnings: tuple[str, ...] = ()

    def region(self, name: str) -> Rect:
        try:
            return self.regions[name]
        except KeyError as exc:
            raise DeckSpecError("missing_region", f"渲染上下文缺少区域：{name}") from exc


_BUILTIN_THEME = DeckTheme(
    key="business-navy",
    background=RGBColor(0xF7, 0xF9, 0xFB),
    surface=RGBColor(0xFF, 0xFF, 0xFF),
    accent=RGBColor(0xB8, 0x86, 0x0B),
    accent_alt=RGBColor(0x2F, 0x67, 0x8F),
    heading=RGBColor(0x16, 0x35, 0x4D),
    body=RGBColor(0x26, 0x2F, 0x38),
    muted=RGBColor(0x6B, 0x76, 0x80),
    divider=RGBColor(0xD9, 0xE1, 0xE7),
    on_dark=RGBColor(0xFF, 0xFF, 0xFF),
    on_dark_muted=RGBColor(0xD7, 0xE4, 0xEC),
    chip=RGBColor(0xE8, 0xF1, 0xF6),
    cover_background=RGBColor(0x16, 0x35, 0x4D),
)

_BUILTIN_CANVAS = DeckCanvas.inches(13.333, 7.5)

# Historical built-in geometry, unchanged. Every value is expressed in the same
# inches the pre-context renderer hard-coded so the built-in output is stable.
_BUILTIN_REGIONS: Mapping[str, Rect] = {
    "accent": Rect.inches(0.55, 0.48, 0.08, 0.62),
    "numeral": Rect.inches(0.78, 0.42, 0.45, 0.28),
    "title": Rect.inches(1.28, 0.31, 11.45, 0.72),
    "takeaway": Rect.inches(1.02, 1.34, 11.25, 0.44),
    "takeaway_panel": Rect.inches(0.78, 1.28, 11.75, 0.58),
    "main": Rect.inches(0.78, 2.25, 11.75, 3.95),
    "footer": Rect.inches(0.78, 7.12, 11.75, 0.01),
    "cover_band": Rect.inches(0.0, 0.0, 0.18, 7.5),
    "cover_rule": Rect.inches(1.0, 2.08, 3.0, 0.03),
    "cover_title": Rect.inches(1.0, 2.18, 10.8, 1.45),
    "cover_subtitle": Rect.inches(1.02, 3.63, 9.5, 0.55),
    "cover_brand": Rect.inches(1.02, 6.42, 5.5, 0.3),
    "closing_title": Rect.inches(1.0, 2.3, 11.0, 1.0),
    "closing_subtitle": Rect.inches(1.02, 3.55, 9.0, 0.52),
    "closing_rule": Rect.inches(1.02, 4.45, 2.4, 0.03),
    "closing_numeral": Rect.inches(1.02, 6.42, 4.0, 0.3),
}

BUILTIN_CONTEXT = RenderContext(
    theme=_BUILTIN_THEME,
    canvas=_BUILTIN_CANVAS,
    regions=_BUILTIN_REGIONS,
)

BUILTIN_THEME_KEY = _BUILTIN_THEME.key


def builtin_context(theme_key: str | None = None) -> RenderContext:
    """Return the built-in context.

    Only ``business-navy`` exists; an unknown or omitted key resolves to it so
    ordinary historical exports stay byte-stable.
    """
    return BUILTIN_CONTEXT


# ── text measurement ────────────────────────────────────────────────────────


def _has_wide_chars(value: str) -> bool:
    return any(unicodedata.east_asian_width(ch) in {"W", "F"} for ch in value)


def advance_emu(text: str, *, size_pt: float, metrics: FontMetrics) -> float:
    """Estimated line advance of ``text`` in EMU at ``size_pt``."""
    total = 0.0
    for ch in text:
        if ch == "\t":
            total += metrics.space_advance * 4
        elif ch == " ":
            total += metrics.space_advance
        elif unicodedata.east_asian_width(ch) in {"W", "F"}:
            total += metrics.wide_advance
        else:
            total += metrics.latin_advance
    return total * size_pt * EMU_PER_PT


def wrap_text(
    text: str,
    *,
    size_pt: float,
    width_emu: int,
    metrics: FontMetrics,
) -> list[str]:
    """Greedy line-break simulation for a single paragraph.

    Latin runs break on whitespace, CJK breaks between characters. This is a
    real wrap simulation, not an area-divided-by-font-size estimate, but it is
    still an estimate: the caller must surface ``font_measurement_substituted``.
    """
    if width_emu <= 0 or size_pt <= 0:
        return [text.strip()] if text.strip() else []
    if not text.strip():
        return [""]
    lines: list[str] = []
    current = ""
    token = ""

    def emit() -> None:
        nonlocal current
        lines.append(current.rstrip())
        current = ""

    def flush_token() -> None:
        nonlocal current, token
        if not token:
            return
        if advance_emu(current + token, size_pt=size_pt, metrics=metrics) <= width_emu:
            current += token
        else:
            if current:
                emit()
            current = token
        token = ""

    for ch in text:
        if ch.isspace():
            flush_token()
            if not current:
                continue
            probe = current + " "
            if advance_emu(probe, size_pt=size_pt, metrics=metrics) <= width_emu:
                current = probe
            else:
                emit()
            continue
        if unicodedata.east_asian_width(ch) in {"W", "F"}:
            flush_token()
            probe = current + ch
            if advance_emu(probe, size_pt=size_pt, metrics=metrics) <= width_emu:
                current = probe
            else:
                if current:
                    emit()
                current = ch
            continue
        token += ch
    flush_token()
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def measure_block_height(
    text: str,
    *,
    rect: Rect,
    size_pt: float,
    metrics: FontMetrics,
    margin: float = 0.04,
) -> int:
    """Estimated height in EMU required to render ``text`` inside ``rect``."""
    available = max(1, rect.width - 2 * _in(margin))
    lines = wrap_text(text, size_pt=size_pt, width_emu=available, metrics=metrics)
    line_height = int(size_pt * EMU_PER_PT * metrics.line_height)
    return max(1, len(lines)) * line_height


# ── markdown extraction (built-in path budgets) ─────────────────────────────

_CITATION_RE = re.compile(r"(?:writing-evidence|vector-evidence):[^\]\s]+")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$")
_NUMBER_RE = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?(?:\s*%|\s*(?:亿元|万元|万|人|家|项|年|个|平方米|㎡)))"
)


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: max(1, limit - 1)]}…"


def _role_for(title: str, content: str) -> str:
    text = f"{title} {content}".lower()
    if any(word in text for word in ("问题", "现状", "挑战", "痛点")):
        return "problem"
    if any(word in text for word in ("目标", "愿景", "价值")):
        return "objective"
    if any(word in text for word in ("架构", "技术架构", "系统设计")):
        return "architecture"
    if any(word in text for word in ("实施", "流程", "路径", "步骤")):
        return "process"
    if any(word in text for word in ("计划", "里程碑", "进度", "阶段")):
        return "timeline"
    if any(word in text for word in ("风险", "保障", "治理")):
        return "risk"
    if any(word in text for word in ("验收", "指标", "标准")):
        return "acceptance"
    if any(word in text for word in ("方案", "设计", "能力")):
        return "solution"
    return "section"


def _blocks(markdown: str) -> tuple[list[str], list[str]]:
    bullets: list[str] = []
    paragraphs: list[str] = []
    table_lines: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("|") and line.endswith("|"):
            if not re.fullmatch(r"\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?", line):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if any(cells):
                    table_lines.append(" · ".join(cells))
            continue
        match = _BULLET_RE.match(line)
        if match:
            bullets.append(_short(match.group(1), _BUILTIN_BULLET_LIMIT))
            continue
        if line.startswith("#"):
            continue
        paragraphs.append(_short(line, _BUILTIN_PARAGRAPH_LIMIT))
    if table_lines:
        bullets.extend(table_lines)
    return bullets[:6], paragraphs[:5]


def _takeaway(title: str, paragraphs: list[str], bullets: list[str]) -> str:
    source = paragraphs[0] if paragraphs else (bullets[0] if bullets else title)
    source = re.sub(r"\^[^\s]+", "", source).strip()
    return _short(source.rstrip("。；"), 84)


def _metrics(content: str) -> list[dict[str, str]]:
    text = content or ""
    values: list[dict[str, str]] = []
    for match in _NUMBER_RE.finditer(text):
        value = _short(match.group("value"), 24)
        # Scope the label to the metric's own line. A fixed-width window would
        # spill the previous metric — and its line break — into this label.
        line_start = text.rfind("\n", 0, match.start()) + 1
        prefix = re.sub(r"^[，。；：:、\s]+", "", text[line_start : match.start()])
        label = _short(prefix[-14:].strip() or "关键指标", 14)
        if any(item["value"] == value for item in values):
            continue
        values.append({"label": label, "value": value})
        if len(values) == 4:
            break
    return values




def _slide_for_chapter(index: int, title: str, content: str, citations: list[str]) -> dict[str, Any]:
    bullets, paragraphs = _blocks(content)
    role = _role_for(title, content)
    metrics = _metrics(content)
    elements: list[dict[str, Any]] = []
    if role in {"process", "timeline"} and len(bullets) >= 2:
        elements.append({"type": "process", "items": bullets[:5]})
    elif len(metrics) >= 2:
        elements.append({"type": "metric", "items": metrics})
    elif bullets:
        elements.append({"type": "list", "items": bullets[:6]})
    else:
        elements.append({"type": "list", "items": paragraphs[:4] or ["待补充"]})
    return {
        "id": f"s{index}",
        "role": role,
        "title": _short(title, 44),
        "takeaway": _takeaway(title, paragraphs, bullets),
        "elements": elements,
        "citations": citations[:20],
        # 交付文件不写来源：引用 ID 仅留在 spec 里供页面与留痕使用。
        "speaker_notes": "",
    }


def build_deck_spec(
    project_title: str,
    chapters: Iterable[tuple[str, str]],
    *,
    audience: str = "",
    theme: str = BUILTIN_THEME_KEY,
) -> dict[str, Any]:
    """Build a bounded, renderer-owned presentation specification (v1, built-in)."""
    from app.services.evidence_export import prepare_cited_chapters

    normalized = prepare_cited_chapters(chapters)[:20]
    presentation_chapters = normalized
    if len(normalized) > 10:
        presentation_chapters = normalized[:8]
        for group_index in range(0, len(normalized[8:]), 6):
            group = normalized[8 + group_index : 14 + group_index]
            summary_lines = []
            for title, content, _citations_for_chapter in group:
                bullets, paragraphs = _blocks(content)
                summary_lines.append(
                    f"- {_short(title, 24)}：{_takeaway(title, paragraphs, bullets)}"
                )
            presentation_chapters.append(
                (
                    "补充内容摘要",
                    "\n".join(summary_lines),
                    [citation for _, _, citations in group for citation in citations],
                )
            )
    slides: list[dict[str, Any]] = [
        {
            "id": "s1",
            "role": "cover",
            "title": _short(project_title, 48),
            "takeaway": "基于引用资料整理的方案汇报",
            "elements": [],
            "citations": [],
            "speaker_notes": "",
        }
    ]
    for index, (title, content, citations) in enumerate(presentation_chapters, start=2):
        slides.append(_slide_for_chapter(index, title, content, citations))
    if normalized:
        slides.append(
            {
                "id": f"s{len(slides) + 1}",
                "role": "closing",
                "title": "谢谢",
                "takeaway": "下一步：确认范围、责任人与验收口径",
                "elements": [],
                "citations": [],
                "speaker_notes": "",
            }
        )
    return {
        "schema_version": DECK_SCHEMA_VERSION_V1,
        "title": _short(project_title, 255),
        "audience": _short(audience, 200),
        "theme": theme,
        "aspect_ratio": "16:9",
        "slides": slides,
    }


# ── validation ─────────────────────────────────────────────────────────────

# 普通演示稿与聊天 sidecar 共用角色词汇。
_ALLOWED_ROLES = deck_roles.ALLOWED_ROLES
_ALLOWED_ELEMENT_TYPES = {
    "metric",
    "list",
    "quote",
    "process",
    "chart",
    "comparison",
}
_MAX_V1_SLIDES = 20


def _require_slides(raw: Mapping[str, Any], limit: int) -> list[Any]:
    slides = raw.get("slides")
    if not isinstance(slides, list) or not slides:
        raise DeckSpecError("missing_slides", "DeckSpec 缺少 slides 数组")
    if len(slides) > limit:
        raise DeckSpecError(
            "too_many_slides", f"DeckSpec 页面数超过上限 {limit}"
        )
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise DeckSpecError("invalid_slide", f"第 {index + 1} 页不是对象")
    return slides


def _validate_role(slide: Mapping[str, Any], index: int) -> str:
    role = str(slide.get("role") or "").strip().lower()
    if role not in _ALLOWED_ROLES:
        raise DeckSpecError(
            "unknown_role", f"第 {index + 1} 页使用了未知 role：{role or '(空)'}"
        )
    return role


def _validate_elements(slide: Mapping[str, Any], index: int) -> list[dict[str, Any]]:
    raw_elements = slide.get("elements")
    if raw_elements is None:
        return []
    if not isinstance(raw_elements, list):
        raise DeckSpecError("invalid_elements", f"第 {index + 1} 页 elements 不是数组")
    if len(raw_elements) > 1:
        raise DeckSpecError(
            "too_many_elements",
            f"第 {index + 1} 页只允许一个主区块（多区块属于独立阶段）",
        )
    validated: list[dict[str, Any]] = []
    for element in raw_elements:
        if not isinstance(element, dict):
            raise DeckSpecError(
                "invalid_element", f"第 {index + 1} 页的区块不是对象"
            )
        element_type = str(element.get("type") or "").strip().lower()
        if element_type not in _ALLOWED_ELEMENT_TYPES:
            raise DeckSpecError(
                "unknown_element_type",
                f"第 {index + 1} 页使用了未知区块类型：{element_type or '(空)'}",
            )
        validated.append({"type": element_type, "items": element.get("items")})
    return validated


def _validate_citations(slide: Mapping[str, Any], index: int) -> list[str]:
    citations = slide.get("citations")
    if citations is None:
        return []
    if not isinstance(citations, list):
        raise DeckSpecError("invalid_citations", f"第 {index + 1} 页 citations 不是数组")
    if len(citations) > 20:
        raise DeckSpecError(
            "too_many_citations", f"第 {index + 1} 页引用超过 20 条"
        )
    for item in citations:
        if not isinstance(item, str):
            raise DeckSpecError(
                "invalid_citations", f"第 {index + 1} 页引用必须为字符串"
            )
    return citations


def _validated_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    slides = _require_slides(raw, _MAX_V1_SLIDES)
    for index, slide in enumerate(slides):
        _validate_role(slide, index)
        _validate_elements(slide, index)
        _validate_citations(slide, index)
    if str(slides[0].get("role") or "").strip().lower() != "cover":
        raise DeckSpecError("missing_cover", "DeckSpec 首页必须是 cover")
    return {
        "schema_version": DECK_SCHEMA_VERSION_V1,
        "title": str(raw.get("title") or ""),
        "audience": str(raw.get("audience") or ""),
        "theme": str(raw.get("theme") or BUILTIN_THEME_KEY),
        "aspect_ratio": str(raw.get("aspect_ratio") or "16:9"),
        "slides": [dict(slide) for slide in slides],
    }




def validate_deck_spec(raw: object) -> dict[str, Any]:
    """Explicitly dispatch on ``schema_version`` and reject unknown input.

    Missing ``schema_version`` means the historical built-in v1 payloads, which
    predate the field. Nothing is silently coerced: an unknown role, element,
    channel or version raises :class:`DeckSpecError`.
    """
    if not isinstance(raw, dict):
        raise DeckSpecError("deck_spec_not_object", "DeckSpec 必须是对象")
    version = raw.get("schema_version", DECK_SCHEMA_VERSION_V1)
    try:
        version_number = int(version)
    except (TypeError, ValueError) as exc:
        raise DeckSpecError("unsupported_schema_version", "schema_version 必须是整数") from exc
    if version_number == DECK_SCHEMA_VERSION_V1:
        return _validated_v1(raw)
    raise DeckSpecError(
        "unsupported_schema_version", f"不支持的 DeckSpec 版本：{version_number}"
    )


# ── renderer primitives (context-driven) ────────────────────────────────────


def _fill(shape, color: RGBColor) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _add_text(
    slide,
    rect: Rect,
    value: str,
    *,
    ctx: RenderContext,
    size: float,
    color: RGBColor | None = None,
    bold: bool = False,
    align=PP_ALIGN.LEFT,
    font: str = "",
    margin: float = 0.04,
) -> Any:
    box = slide.shapes.add_textbox(
        Emu(rect.left), Emu(rect.top), Emu(rect.width), Emu(rect.height)
    )
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Emu(_in(margin))
    frame.margin_right = Emu(_in(margin))
    frame.margin_top = Emu(_in(margin))
    frame.margin_bottom = Emu(_in(margin))
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = value
    run.font.name = font or ctx.theme.font_for(value)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color if color is not None else ctx.theme.body
    return box


def _add_rect_shape(
    slide, shape_type: MSO_SHAPE, rect: Rect, color: RGBColor
):
    shape = slide.shapes.add_shape(
        shape_type, Emu(rect.left), Emu(rect.top), Emu(rect.width), Emu(rect.height)
    )
    _fill(shape, color)
    return shape


def _blank_slide(prs, ctx: RenderContext):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = ctx.theme.background
    return slide


def _base_slide(prs, number: int, title: str, takeaway: str, ctx: RenderContext):
    slide = _blank_slide(prs, ctx)
    _add_rect_shape(slide, MSO_SHAPE.RECTANGLE, ctx.region("accent"), ctx.theme.accent)
    _add_text(
        slide,
        ctx.region("numeral"),
        f"{number:02d}",
        ctx=ctx,
        size=10,
        color=ctx.theme.accent,
        bold=True,
    )
    _add_text(
        slide,
        ctx.region("title"),
        title,
        ctx=ctx,
        size=35,
        color=ctx.theme.heading,
        bold=True,
    )
    if takeaway:
        _add_rect_shape(
            slide,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            ctx.region("takeaway_panel"),
            ctx.theme.chip,
        )
        _add_text(
            slide,
            ctx.region("takeaway"),
            takeaway,
            ctx=ctx,
            size=16,
            color=ctx.theme.accent_alt,
            bold=True,
        )
    _add_rect_shape(
        slide, MSO_SHAPE.RECTANGLE, ctx.region("footer"), ctx.theme.divider
    )
    return slide


def _render_cover(prs, slide: Mapping[str, Any], ctx: RenderContext):
    current = prs.slides.add_slide(prs.slide_layouts[6])
    current.background.fill.solid()
    current.background.fill.fore_color.rgb = ctx.theme.cover_background
    _add_rect_shape(
        current, MSO_SHAPE.RECTANGLE, ctx.region("cover_band"), ctx.theme.accent
    )
    _add_rect_shape(
        current, MSO_SHAPE.RECTANGLE, ctx.region("cover_rule"), ctx.theme.accent
    )
    _add_text(
        current,
        ctx.region("cover_title"),
        _short(slide.get("title") or "", 30),
        ctx=ctx,
        size=50,
        color=ctx.theme.on_dark,
        bold=True,
    )
    _add_text(
        current,
        ctx.region("cover_subtitle"),
        str(slide.get("takeaway") or ""),
        ctx=ctx,
        size=18,
        color=ctx.theme.on_dark_muted,
    )
    _add_text(
        current,
        ctx.region("cover_brand"),
        "PROPOSAL / MANAGEMENT BRIEF",
        ctx=ctx,
        size=9,
        color=ctx.theme.accent,
        bold=True,
    )
    return current


def _render_closing(prs, number: int, title: str, takeaway: str, ctx: RenderContext):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = ctx.theme.cover_background
    _add_text(
        slide,
        ctx.region("closing_title"),
        title,
        ctx=ctx,
        size=42,
        color=ctx.theme.on_dark,
        bold=True,
    )
    _add_text(
        slide,
        ctx.region("closing_subtitle"),
        takeaway or "下一步：确认范围、责任人与验收口径",
        ctx=ctx,
        size=18,
        color=ctx.theme.on_dark_muted,
    )
    _add_rect_shape(
        slide, MSO_SHAPE.RECTANGLE, ctx.region("closing_rule"), ctx.theme.accent
    )
    _add_text(
        slide,
        ctx.region("closing_numeral"),
        f"{number:02d} / END",
        ctx=ctx,
        size=9,
        color=ctx.theme.accent,
        bold=True,
    )
    return slide


def _render_metric(slide, items: list[dict[str, str]], ctx: RenderContext) -> None:
    main = ctx.region("main")
    count = max(1, min(4, len(items)))
    gap = _in(0.16)
    width = main.width // count - gap
    for index, item in enumerate(items[:4]):
        left = main.left + index * (width + gap)
        card = Rect(left, main.top, width, _in(1.55))
        _add_rect_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, card, ctx.theme.surface)
        _add_text(
            slide,
            card.place(0.18, 0.17, 0.0, 0.48).__class__(
                card.left + _in(0.18), card.top + _in(0.17), width - _in(0.36), _in(0.48)
            ),
            item["label"],
            ctx=ctx,
            size=16,
            color=ctx.theme.muted,
        )
        _add_text(
            slide,
            Rect(card.left + _in(0.18), card.top + _in(0.71), width - _in(0.36), _in(0.62)),
            item["value"],
            ctx=ctx,
            size=28,
            color=ctx.theme.heading,
            bold=True,
        )


def _render_list(slide, items: list[str], ctx: RenderContext) -> None:
    main = ctx.region("main")
    _add_rect_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE, main, ctx.theme.surface
    )
    visible_items = items[:6]
    row_gap = _in(0.57) if len(visible_items) == 6 else _in(0.67)
    for index, item in enumerate(visible_items):
        top = main.top + _in(0.22) + index * row_gap
        _add_rect_shape(
            slide,
            MSO_SHAPE.OVAL,
            Rect(main.left + _in(0.32), top + _in(0.13), _in(0.11), _in(0.11)),
            ctx.theme.accent,
        )
        _add_text(
            slide,
            Rect(main.left + _in(0.6), top, main.width - _in(1.2), _in(0.48)),
            item,
            ctx=ctx,
            size=17,
        )


def _render_process(slide, items: list[str], ctx: RenderContext) -> None:
    main = ctx.region("main")
    count = max(1, min(5, len(items)))
    step_width = (main.width - _in(0.85)) // count
    y = main.top + _in(1.2)
    for index, item in enumerate(items[:5]):
        left = main.left + _in(0.22) + index * step_width
        if index < count - 1:
            _add_rect_shape(
                slide,
                MSO_SHAPE.RECTANGLE,
                Rect(
                    left + _in(0.63),
                    y + _in(0.27),
                    max(_in(0.2), step_width - _in(0.72)),
                    _in(0.025),
                ),
                ctx.theme.divider,
            )
        _add_rect_shape(
            slide,
            MSO_SHAPE.OVAL,
            Rect(left, y, _in(0.56), _in(0.56)),
            ctx.theme.accent_alt,
        )
        _add_text(
            slide,
            Rect(left, y + _in(0.01), _in(0.56), _in(0.5)),
            str(index + 1),
            ctx=ctx,
            size=14,
            color=ctx.theme.on_dark,
            bold=True,
            align=PP_ALIGN.CENTER,
        )
        _add_text(
            slide,
            Rect(left - _in(0.32), y + _in(0.78), _in(1.2), _in(0.68)),
            item,
            ctx=ctx,
            size=16,
            bold=True,
            align=PP_ALIGN.CENTER,
        )


def _render_quote(slide, items: list[str], ctx: RenderContext) -> None:
    main = ctx.region("main")
    _add_rect_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, main, ctx.theme.surface)
    _add_text(
        slide,
        main.place(0.32, 0.2, 0.7, 0.75),
        "“",
        ctx=ctx,
        size=46,
        color=ctx.theme.accent,
        bold=True,
    )
    _add_text(
        slide,
        main.place(0.94, 0.47, 9.7, 1.65),
        items[0],
        ctx=ctx,
        size=25,
        color=ctx.theme.heading,
        bold=True,
    )
    if len(items) > 1:
        _add_text(
            slide,
            main.place(0.97, 2.37, 9.4, 0.44),
            f"— {items[1]}",
            ctx=ctx,
            size=16,
            color=ctx.theme.muted,
        )


def _render_comparison(slide, items: list[str], ctx: RenderContext) -> None:
    main = ctx.region("main")
    count = max(1, min(3, len(items)))
    gap = _in(0.18)
    width = (main.width - gap * (count - 1)) // count
    for index, item in enumerate(items[:3]):
        left = main.left + index * (width + gap)
        card = Rect(left, main.top, width, main.height)
        _add_rect_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, card, ctx.theme.surface)
        _add_text(
            slide,
            Rect(
                card.left + _in(0.22),
                card.top + _in(0.31),
                width - _in(0.44),
                _in(0.34),
            ),
            f"方案 {index + 1:02d}",
            ctx=ctx,
            size=14,
            color=ctx.theme.accent,
            bold=True,
        )
        _add_text(
            slide,
            Rect(card.left + _in(0.22), card.top + _in(0.87), width - _in(0.44), _in(1.85)),
            item,
            ctx=ctx,
            size=18,
            color=ctx.theme.heading,
            bold=True,
        )


def _render_chart(slide, items: list[str], ctx: RenderContext) -> None:
    main = ctx.region("main")
    parsed: list[tuple[str, float]] = []
    for item in items[:5]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", item)
        parsed.append((item, float(match.group(1)) if match else 1.0))
    maximum = max((value for _, value in parsed), default=1.0)
    label_left = main.left + _in(0.24)
    track_left = main.left + _in(2.77)
    track_width = main.width - _in(3.5)
    for index, (label, value) in enumerate(parsed):
        top = main.top + _in(0.13) + index * _in(0.72)
        _add_text(slide, Rect(label_left, top, _in(2.35), _in(0.4)), label, ctx=ctx, size=16)
        _add_rect_shape(
            slide,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Rect(track_left, top + _in(0.06), track_width, _in(0.26)),
            ctx.theme.divider,
        )
        _add_rect_shape(
            slide,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Rect(
                track_left,
                top + _in(0.06),
                max(_in(0.35), int(track_width * value / maximum)),
                _in(0.26),
            ),
            ctx.theme.accent_alt if index % 2 == 0 else ctx.theme.accent,
        )


def _apply_slide_metadata(
    slide, slide_spec: Mapping[str, Any], ctx: RenderContext, *, dark: bool = False
) -> None:
    notes = str(slide_spec.get("speaker_notes") or "")
    if notes:
        slide.notes_slide.notes_text_frame.text = notes


_ELEMENT_RENDERERS = {
    "metric": "_render_metric",
    "process": "_render_process",
    "list": "_render_list",
    "quote": "_render_quote",
    "comparison": "_render_comparison",
    "chart": "_render_chart",
}


def _render_element(slide, element: Mapping[str, Any], ctx: RenderContext) -> bool:
    element_type = str(element.get("type") or "")
    items = element.get("items")
    if not isinstance(items, list):
        return False
    if element_type == "metric":
        metric_items = [
            {
                "label": _short(item.get("label") or "指标", 20),
                "value": _short(item.get("value") or "待补充", 24),
            }
            for item in items
            if isinstance(item, dict)
        ]
        if not metric_items:
            return False
        _render_metric(slide, metric_items, ctx)
        return True
    text_items = [str(item) for item in items if str(item).strip()]
    if not text_items:
        return False
    renderer_name = _ELEMENT_RENDERERS.get(element_type)
    if renderer_name is None:
        return False
    globals()[renderer_name](slide, text_items, ctx)
    return True


def render_builtin_deck(deck_spec: Mapping[str, Any], ctx: RenderContext | None = None) -> bytes:
    """Render a validated v1 DeckSpec with the built-in layout primitives."""
    context = ctx or builtin_context(str(deck_spec.get("theme") or ""))
    prs = Presentation()
    prs.slide_width = Emu(context.canvas.width)
    prs.slide_height = Emu(context.canvas.height)
    slides = deck_spec.get("slides") or []
    if slides and isinstance(slides[0], dict):
        cover = _render_cover(prs, slides[0], context)
        _apply_slide_metadata(cover, slides[0], context, dark=True)
    for number, slide_spec in enumerate(slides[1:], start=2):
        if not isinstance(slide_spec, dict):
            continue
        if str(slide_spec.get("role") or "").strip().lower() == "closing":
            closing = _render_closing(
                prs,
                number,
                _short(slide_spec.get("title") or "谢谢", 30),
                _short(slide_spec.get("takeaway") or "", 60),
                context,
            )
            _apply_slide_metadata(closing, slide_spec, context, dark=True)
            continue
        title = _short(slide_spec.get("title") or "未命名页面", 30)
        slide = _base_slide(
            prs,
            number,
            title,
            _short(slide_spec.get("takeaway") or "", 84),
            context,
        )
        elements = slide_spec.get("elements")
        rendered = False
        if isinstance(elements, list):
            for element in elements:
                if isinstance(element, dict) and _render_element(slide, element, context):
                    rendered = True
                    break
        if not rendered:
            _render_list(slide, ["待补充"], context)
        _apply_slide_metadata(slide, slide_spec, context)
    output = BytesIO()
    prs.save(output)
    return output.getvalue()


def render_deck_spec(deck_spec: dict[str, Any]) -> bytes:
    """Validate and render the ordinary built-in presentation."""
    return render_builtin_deck(validate_deck_spec(deck_spec))


def render_project_pptx(
    project_title: str,
    chapters: Iterable[tuple[str, str]],
    *,
    audience: str = "",
) -> tuple[bytes, dict[str, Any]]:
    spec = build_deck_spec(project_title, chapters, audience=audience)
    return render_deck_spec(spec), spec


def _normalize_presentation_metadata(root, notes_master_id: str | None) -> bool:
    """只修主文档元数据；返回是否改动，不触碰页面设计与内容。"""
    changed = False
    notes_list = root.find(qn("p:notesMasterIdLst"))
    if notes_master_id is not None:
        if notes_list is None:
            notes_list = root.makeelement(qn("p:notesMasterIdLst"))
            masters = root.find(qn("p:sldMasterIdLst"))
            root.insert(0 if masters is None else root.index(masters) + 1, notes_list)
            changed = True
        if len(notes_list) == 0:
            notes_id = notes_list.makeelement(qn("p:notesMasterId"))
            notes_id.set(qn("r:id"), notes_master_id)
            notes_list.append(notes_id)
            changed = True
    for tag in ("p:notesMasterIdLst", "p:handoutMasterIdLst", "p:custDataLst"):
        node = root.find(qn(tag))
        if node is not None and len(node) == 0:
            root.remove(node)
            changed = True
    return changed


def normalize_presentation_package(package: bytes) -> bytes:
    """新生成 PPT 的 Office 兼容修正，其余 OPC 部件保持逐字节不变。"""
    from app.services.document_processor import _MAX_OFFICE_ARCHIVE_FILES, _MAX_OFFICE_XML_BYTES

    with zipfile.ZipFile(BytesIO(package)) as archive:
        entries = archive.infolist()
        # The delivery package includes media; retain its established 2 GiB ceiling.
        if (
            len(entries) > _MAX_OFFICE_ARCHIVE_FILES
            or sum(entry.file_size for entry in entries) > 2 * 1024 * 1024 * 1024
        ):
            raise DeckSpecError("presentation_package_invalid", "PPT 压缩包展开大小超过安全上限")
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names):
            raise DeckSpecError("presentation_package_invalid", "PPT 包含重复部件")

        def xml(part: str):
            if archive.getinfo(part).file_size > _MAX_OFFICE_XML_BYTES:
                raise DeckSpecError("presentation_package_invalid", "PPT 元数据过大")
            return etree.fromstring(
                archive.read(part), parser=etree.XMLParser(resolve_entities=False, no_network=True),
            )

        root = xml("ppt/presentation.xml")
        relationships = xml("ppt/_rels/presentation.xml.rels")
        notes_master_id = next(
            (
                relation.get("Id") for relation in relationships
                if relation.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster"
                and relation.get("TargetMode") != "External"
            ),
            None,
        )
        if not _normalize_presentation_metadata(root, notes_master_id):
            return package
        metadata = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as result:
            result.comment = archive.comment
            for entry in entries:
                if entry.filename == "ppt/presentation.xml":
                    result.writestr(copy.copy(entry), metadata)
                else:
                    with archive.open(entry) as source, result.open(copy.copy(entry), "w") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
        return output.getvalue()
