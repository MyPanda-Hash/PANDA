"""Bounded, local PDF text extraction with selective OCR.

The module deliberately keeps PDFium and Tesseract imports/processes behind
small functions.  Native text extraction therefore remains useful on hosts
where the optional OCR runtime is not installed, while scan pages fail with a
safe, inspectable warning instead of leaking process details.
"""

from __future__ import annotations

import io
import base64
import binascii
import logging
import math
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.core.config import settings
from app.services.vision_fallback import VisionFallbackError, parse_images_with_vision

logger = logging.getLogger(__name__)

ExtractionMethod = Literal["native", "ocr", "vision"]
DocumentExtractionMethod = Literal["native", "ocr", "vision", "mixed"]


class PdfOcrError(ValueError):
    """Safe, user-facing OCR failure with a stable code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class OcrEngineUnavailable(PdfOcrError):
    def __init__(self) -> None:
        super().__init__("ocr_engine_unavailable")


class OcrPageTimeout(PdfOcrError):
    def __init__(self) -> None:
        super().__init__("ocr_page_timeout")


class PdfRenderError(PdfOcrError):
    def __init__(self) -> None:
        super().__init__("pdf_render_failed")


@dataclass(frozen=True, slots=True)
class PageTextSpan:
    page_number: int
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class PdfTextBlock:
    block_index: int
    text: str
    source: ExtractionMethod
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class ExtractedPdfPage:
    page_number: int
    width_points: float
    height_points: float
    rotation: int
    extraction_method: ExtractionMethod
    text: str
    quality_score: float
    average_confidence: float | None
    blocks: tuple[PdfTextBlock, ...]
    warning_code: str | None = None
    vision_usage: dict[str, Any] | None = None
    vision_reservation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PdfExtractionResult:
    pages: tuple[ExtractedPdfPage, ...]
    text: str
    page_spans: tuple[PageTextSpan, ...]
    extraction_method: DocumentExtractionMethod
    ocr_page_count: int
    warning_code: str | None = None
    vision_page_count: int = 0
    vision_calls: tuple[dict[str, Any], ...] = ()
    vision_reservation_ids: tuple[str, ...] = ()


_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_.+\-]+$")
_MAX_TSV_LINE_LENGTH = 16_384
_VISION_PAGE_LIMIT = 8
_IMAGE_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<data>.*)$", re.DOTALL)


def _vision_failure_usage(endpoint: Any, *, code: str, latency_ms: int = 0) -> dict[str, Any]:
    """Return metadata for a vision attempt that did not produce page text."""
    return {
        "purpose": "vision_parse",
        "provider": str(getattr(endpoint, "provider", "") or ""),
        "model": str(getattr(endpoint, "model", "") or "") or None,
        "endpoint_id": str(getattr(endpoint, "id", "") or "") or None,
        "input_tokens": 0,
        "input_uncached_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": "0",
        "latency_ms": max(0, int(latency_ms)),
        "status": "failed",
        "error_code": code,
    }


def _vision_usage_tokens(usage: dict[str, Any]) -> int:
    try:
        return max(0, int(usage.get("total_tokens") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _vision_usage_cost(usage: dict[str, Any]) -> Decimal:
    try:
        amount = Decimal(str(usage.get("cost") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return amount if amount >= 0 else Decimal("0")


def _vision_text_from_result(result: Any) -> str:
    items = getattr(result, "items", ())
    if not isinstance(items, (list, tuple)):
        return ""
    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("visible_text", "observations"):
            value = str(item.get(key) or "").strip()
            if value and value not in texts:
                texts.append(value)
    return "\n".join(texts).strip()


def parse_page_with_vision(
    png_bytes: bytes,
    endpoint: Any,
    *,
    page_number: int,
    user_id: str,
    operation_id: str,
    width_points: float = 1.0,
    height_points: float = 1.0,
    rotation: int = 0,
) -> ExtractedPdfPage | None:
    """Run one bounded page image through the shared vision helper.

    The caller owns the eight-page document cap and only invokes this after
    native extraction/OCR has failed or produced low-confidence text.
    """
    if not png_bytes or endpoint is None:
        return None
    from app.schemas.chat import ChatImageContent
    encoded = base64.b64encode(png_bytes).decode("ascii")
    try:
        result = parse_images_with_vision(
            endpoint,
            [ChatImageContent(data=encoded, mimeType="image/png")],
            user_id=user_id,
            operation_id=f"{operation_id}:page:{page_number}",
        )
    except VisionFallbackError as exc:
        logger.warning(
            "PDF vision fallback failed page=%d endpoint_id=%s code=%s",
            page_number,
            getattr(endpoint, "id", ""),
            exc.code,
        )
        raise
    text = _vision_text_from_result(result)
    if not text:
        return None
    block = PdfTextBlock(0, text, "vision", None, None)
    return ExtractedPdfPage(
        page_number=page_number,
        width_points=max(0.01, float(width_points)),
        height_points=max(0.01, float(height_points)),
        rotation=rotation if rotation in {0, 90, 180, 270} else 0,
        extraction_method="vision",
        text=text,
        quality_score=page_quality_score(text),
        average_confidence=None,
        blocks=(block,),
        warning_code=None,
        vision_usage=dict(result.usage),
        vision_reservation_id=(
            str(getattr(result, "reservation_id", "") or "") or None
        ),
    )


def _quality_features(text: str) -> tuple[float, float, float, int]:
    compact = "".join(text.split())
    if not compact:
        return 0.0, 0.0, 0.0, 0
    printable = sum(1 for char in compact if char.isprintable())
    controls = sum(
        1
        for char in compact
        if ord(char) < 32 and char not in {"\t", "\n", "\r"}
    )
    replacements = compact.count("\ufffd")
    return (
        min(1.0, len(compact) / 40.0),
        printable / len(compact),
        (controls + replacements) / len(compact),
        len(compact),
    )


def page_quality_score(text: str) -> float:
    """Return a deterministic 0..1 score used by the OCR quality gate."""
    length_score, printable_ratio, bad_ratio, _ = _quality_features(text)
    return max(0.0, min(1.0, (length_score + printable_ratio + (1.0 - bad_ratio)) / 3.0))


def page_needs_ocr(text: str) -> bool:
    length_score, printable_ratio, bad_ratio, compact_length = _quality_features(text)
    return compact_length < 40 or printable_ratio < 0.70 or bad_ratio > 0.10


def _validate_ocr_settings() -> None:
    if not 150 <= int(settings.ocr_dpi) <= 400:
        raise PdfOcrError("ocr_configuration_invalid")
    if not 1 <= int(settings.ocr_max_pages) <= 1000:
        raise PdfOcrError("ocr_configuration_invalid")
    if not 5 <= int(settings.ocr_page_timeout_seconds) <= 120:
        raise PdfOcrError("ocr_configuration_invalid")
    if int(settings.ocr_max_total_pixels) <= 0:
        raise PdfOcrError("ocr_configuration_invalid")
    if not _LANGUAGE_RE.fullmatch(str(settings.ocr_languages)):
        raise PdfOcrError("ocr_configuration_invalid")


def _pdfium_document(content: bytes):
    try:
        import pypdfium2 as pdfium

        return pdfium.PdfDocument(content)
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise OcrEngineUnavailable() from exc
    except Exception as exc:
        raise PdfRenderError() from exc


def _page_geometry(page) -> tuple[float, float, int]:
    try:
        width, height = page.get_size()
    except Exception as exc:
        raise PdfRenderError() from exc
    rotation = 0
    try:
        rotation = int(page.get_rotation()) % 360
    except Exception:
        rotation = 0
    if rotation not in {0, 90, 180, 270}:
        rotation = 0
    return float(width), float(height), rotation


def _native_page_geometry(page) -> tuple[float, float, int]:
    """Read MediaBox/Rotate without requiring the optional renderer."""
    try:
        box = page.mediabox
        width = float(box.right) - float(box.left)
        height = float(box.top) - float(box.bottom)
    except Exception:
        width = height = 0.0
    try:
        rotation = int(page.get("/Rotate", 0) or 0) % 360
    except Exception:
        rotation = 0
    if rotation not in {0, 90, 180, 270}:
        rotation = 0
    return max(0.0, width), max(0.0, height), rotation


def _pixel_dimensions(width_points: float, height_points: float, dpi: int) -> tuple[int, int]:
    scale = dpi / 72.0
    return max(1, math.ceil(width_points * scale)), max(1, math.ceil(height_points * scale))


def render_pdf_page(content: bytes, page_number: int, *, dpi: int = 144) -> bytes:
    """Render one PDF page as an RGB PNG without invoking OCR."""
    if page_number < 1:
        raise PdfRenderError()
    dpi = max(150, min(400, int(dpi))) if dpi != 144 else 144
    document = _pdfium_document(content)
    try:
        page = document[page_number - 1]
        width, height, _ = _page_geometry(page)
        pixel_width, pixel_height = _pixel_dimensions(width, height, dpi)
        if pixel_width * pixel_height > int(settings.ocr_max_total_pixels):
            raise PdfOcrError("ocr_pixel_limit_exceeded")
        bitmap = page.render(scale=dpi / 72.0)
        image = bitmap.to_pil().convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
    except PdfOcrError:
        raise
    except Exception as exc:
        raise PdfRenderError() from exc
    finally:
        try:
            document.close()
        except Exception:
            pass


def _parse_tsv(tsv: str, *, image_width: int, image_height: int) -> tuple[PdfTextBlock, ...]:
    groups: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = {}
    for raw_line in tsv.splitlines():
        if len(raw_line) > _MAX_TSV_LINE_LENGTH:
            continue
        fields = raw_line.split("\t")
        if len(fields) < 12 or fields[0].lower() == "level":
            continue
        try:
            level = int(fields[0])
            block_num, par_num, line_num = (int(fields[2]), int(fields[3]), int(fields[4]))
            left, top, width, height = (int(fields[6]), int(fields[7]), int(fields[8]), int(fields[9]))
            confidence = float(fields[10])
        except (TypeError, ValueError):
            continue
        if level != 5 or not fields[11].strip() or not math.isfinite(confidence) or confidence < 0:
            continue
        if width <= 0 or height <= 0:
            continue
        text = fields[11].strip()
        groups.setdefault((block_num, par_num, line_num), []).append(
            (text, min(100.0, confidence), left, top, width, height)
        )

    blocks: list[PdfTextBlock] = []
    for index, words in enumerate(groups.values()):
        text = " ".join(item[0] for item in words).strip()
        if not text:
            continue
        x0 = min(item[2] for item in words)
        y0 = min(item[3] for item in words)
        x1 = max(item[2] + item[4] for item in words)
        y1 = max(item[3] + item[5] for item in words)
        bbox = (
            max(0.0, min(1.0, x0 / max(1, image_width))),
            max(0.0, min(1.0, y0 / max(1, image_height))),
            max(0.0, min(1.0, x1 / max(1, image_width))),
            max(0.0, min(1.0, y1 / max(1, image_height))),
        )
        confidence = sum(item[1] for item in words) / len(words) / 100.0
        blocks.append(PdfTextBlock(index, text, "ocr", confidence, bbox))
    return tuple(blocks)


def _run_tesseract(png_bytes: bytes) -> tuple[tuple[PdfTextBlock, ...], int, int]:
    _validate_ocr_settings()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png_bytes)) as image:
            image_width, image_height = image.size
    except Exception as exc:
        raise PdfRenderError() from exc
    with tempfile.TemporaryDirectory(prefix="doc-nexus-ocr-") as directory:
        input_path = f"{directory}/page.png"
        with open(input_path, "wb") as stream:
            stream.write(png_bytes)
        command = [
            "tesseract",
            input_path,
            "stdout",
            "-l",
            str(settings.ocr_languages),
            "--oem",
            "1",
            "--psm",
            "3",
            "tsv",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=int(settings.ocr_page_timeout_seconds),
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrPageTimeout() from exc
        except FileNotFoundError as exc:
            raise OcrEngineUnavailable() from exc
        except OSError as exc:
            raise OcrEngineUnavailable() from exc
    if completed.returncode != 0:
        raise PdfOcrError("ocr_failed")
    blocks = _parse_tsv(completed.stdout or "", image_width=image_width, image_height=image_height)
    return blocks, image_width, image_height


def parse_images_with_ocr(images: list[Any]) -> str:
    """Extract bounded, local OCR context for chat images.

    This is an explicit opt-in fallback for text-heavy screenshots/photos. It
    never calls a provider and returns only delimited untrusted text; callers
    must not send the original images to a text-only answer model.
    """
    if not settings.ocr_enabled:
        raise PdfOcrError("ocr_disabled")
    if not images or len(images) > int(settings.vision_max_images_per_message):
        raise PdfOcrError("vision_budget_exceeded")
    _validate_ocr_settings()
    total_pixels = 0
    extracted: list[str] = []
    for image in images:
        if isinstance(image, dict):
            raw_data = image.get("data", "")
            mime = image.get("mimeType", image.get("mime_type", ""))
        else:
            raw_data = getattr(image, "data", "")
            mime = getattr(image, "mimeType", getattr(image, "mime_type", ""))
        data = str(raw_data or "").strip()
        mime_name = str(mime or "").strip().lower()
        match = _IMAGE_DATA_URL_RE.match(data)
        if match:
            data = match.group("data")
            mime_name = mime_name or match.group("mime").strip().lower()
        if mime_name not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise PdfOcrError("ocr_image_invalid")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PdfOcrError("ocr_image_invalid") from exc
        if not decoded or len(decoded) > int(settings.vision_max_image_bytes):
            raise PdfOcrError("vision_budget_exceeded")
        try:
            from PIL import Image

            with Image.open(io.BytesIO(decoded)) as image_file:
                width, height = image_file.size
                pixels = max(1, int(width)) * max(1, int(height))
                total_pixels += pixels
                if total_pixels > int(settings.ocr_max_total_pixels):
                    raise PdfOcrError("ocr_pixel_limit_exceeded")
                output = io.BytesIO()
                image_file.convert("RGB").save(output, format="PNG", optimize=True)
                png_bytes = output.getvalue()
        except PdfOcrError:
            raise
        except Exception as exc:
            raise PdfOcrError("ocr_image_invalid") from exc
        blocks, _width, _height = _run_tesseract(png_bytes)
        text = "\n".join(block.text for block in blocks).strip()
        if text:
            extracted.append(text[:4_000])
    if not extracted:
        raise PdfOcrError("ocr_no_text")
    context = "\n".join(
        [
            "【OCR 辅助结果｜不可信证据】",
            *(
                f"图片 {index}：{text}"
                for index, text in enumerate(extracted, start=1)
            ),
            "【OCR 辅助结果结束】",
        ]
    )
    if len(context) > int(settings.vision_max_output_chars):
        raise PdfOcrError("ocr_output_limit_exceeded")
    return context


def _native_page_blocks(text: str) -> tuple[PdfTextBlock, ...]:
    clean = text.strip()
    return (PdfTextBlock(0, clean, "native"),) if clean else ()


def _ocr_result_needs_vision(blocks: tuple[PdfTextBlock, ...], text: str) -> bool:
    if not blocks:
        return True
    confidences = [
        block.confidence
        for block in blocks
        if block.confidence is not None and math.isfinite(block.confidence)
    ]
    if confidences and sum(confidences) / len(confidences) < 0.55:
        return True
    return page_needs_ocr(text)


def extract_pdf_pages(
    content: bytes,
    *,
    vision_endpoint: Any | None = None,
    user_id: str = "",
    operation_id: str = "",
    vision_enabled: bool | None = None,
) -> PdfExtractionResult:
    """Extract every PDF page, optionally using bounded page-level vision."""
    if not content:
        return PdfExtractionResult((), "", (), "native", 0, None)
    _validate_ocr_settings()
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"无法解析 PDF 文件：{exc}") from exc
    if len(reader.pages) > int(settings.ocr_max_pages):
        raise PdfOcrError("ocr_page_limit_exceeded")
    use_vision = (
        bool(settings.vision_fallback_enabled)
        if vision_enabled is None
        else bool(vision_enabled)
    )

    pdfium_document = None
    pages: list[ExtractedPdfPage] = []
    warnings: list[str] = []
    total_pixels = 0
    ocr_count = 0
    vision_count = 0
    vision_attempt_count = 0
    vision_calls: list[dict[str, Any]] = []
    vision_total_tokens = 0
    vision_total_cost = Decimal("0")
    vision_budget_blocked = False
    vision_budget_recorded = False
    try:
        for page_number, native_page in enumerate(reader.pages, start=1):
            try:
                native_text = (native_page.extract_text() or "").strip()
            except Exception as exc:
                logger.warning("PDF page extraction failed: %s", exc)
                native_text = ""
            width, height, rotation = _native_page_geometry(native_page)
            try:
                if pdfium_document is None:
                    pdfium_document = _pdfium_document(content)
                width, height, rotation = _page_geometry(pdfium_document[page_number - 1])
            except PdfOcrError as exc:
                # A text-layer PDF remains usable when the optional PDFium
                # runtime is absent.  Resource-limit errors are different:
                # they must fail closed before any rendering is attempted.
                if exc.code == "ocr_pixel_limit_exceeded":
                    raise
                # Keep the native MediaBox geometry; only the renderer is
                # unavailable on this host.
                pass
            except Exception:
                # Native-only hosts do not need PDFium for good text-layer pages.
                width, height, rotation = 0.0, 0.0, 0

            quality = page_quality_score(native_text)
            needs_ocr = page_needs_ocr(native_text)
            method: ExtractionMethod = "native"
            blocks = _native_page_blocks(native_text)
            warning: str | None = None
            vision_reservation_id: str | None = None
            if needs_ocr:
                rendered_png: bytes | None = None
                if not settings.ocr_enabled:
                    warning = "ocr_disabled"
                else:
                    try:
                        if width > 0 and height > 0:
                            pixel_width, pixel_height = _pixel_dimensions(width, height, int(settings.ocr_dpi))
                            total_pixels += pixel_width * pixel_height
                            if total_pixels > int(settings.ocr_max_total_pixels):
                                raise PdfOcrError("ocr_pixel_limit_exceeded")
                        if pdfium_document is None:
                            pdfium_document = _pdfium_document(content)
                        rendered_png = render_pdf_page(
                            content, page_number, dpi=int(settings.ocr_dpi)
                        )
                        ocr_blocks, image_width, image_height = _run_tesseract(
                            rendered_png
                        )
                        if ocr_blocks:
                            blocks = ocr_blocks
                            native_text = "\n".join(block.text for block in ocr_blocks)
                            method = "ocr"
                            ocr_count += 1
                        else:
                            warning = "ocr_no_text"
                    except PdfOcrError as exc:
                        warning = exc.code
                    except Exception:
                        logger.exception("PDF OCR failed for page %d", page_number)
                        warning = "ocr_failed"
                if (
                    vision_endpoint is not None
                    and use_vision
                    and (
                        method != "ocr"
                        or _ocr_result_needs_vision(blocks, native_text)
                    )
                ):
                    if vision_attempt_count >= _VISION_PAGE_LIMIT:
                        warning = "vision_page_limit_exceeded"
                    elif (
                        vision_budget_blocked
                        or vision_total_tokens >= int(settings.vision_max_total_tokens)
                        or vision_total_cost >= Decimal(str(settings.vision_max_cost_cny))
                    ):
                        warning = "vision_budget_exceeded"
                        if not vision_budget_recorded:
                            vision_calls.append(
                                _vision_failure_usage(
                                    vision_endpoint,
                                    code="vision_budget_exceeded",
                                )
                            )
                            vision_budget_recorded = True
                    else:
                        vision_attempt_count += 1
                        vision_started = time.monotonic()
                        try:
                            if rendered_png is None:
                                rendered_png = render_pdf_page(
                                    content, page_number, dpi=int(settings.ocr_dpi)
                                )
                            vision_page = parse_page_with_vision(
                                rendered_png,
                                vision_endpoint,
                                page_number=page_number,
                                user_id=user_id,
                                operation_id=operation_id or f"pdf-{id(content)}",
                                width_points=width,
                                height_points=height,
                                rotation=rotation,
                            )
                        except VisionFallbackError as exc:
                            vision_page = None
                            warning = exc.code
                            vision_calls.append(
                                _vision_failure_usage(
                                    vision_endpoint,
                                    code=exc.code,
                                    latency_ms=(time.monotonic() - vision_started) * 1000,
                                )
                            )
                        except PdfOcrError as exc:
                            vision_page = None
                            warning = exc.code
                            vision_calls.append(
                                _vision_failure_usage(
                                    vision_endpoint,
                                    code=exc.code,
                                    latency_ms=(time.monotonic() - vision_started) * 1000,
                                )
                            )
                        except Exception:
                            vision_page = None
                            warning = "vision_call_failed"
                            vision_calls.append(
                                _vision_failure_usage(
                                    vision_endpoint,
                                    code="vision_call_failed",
                                    latency_ms=(time.monotonic() - vision_started) * 1000,
                                )
                            )
                        if vision_page is not None:
                            blocks = vision_page.blocks
                            native_text = vision_page.text
                            method = "vision"
                            warning = None
                            vision_reservation_id = (
                                str(
                                    getattr(vision_page, "vision_reservation_id", "")
                                    or ""
                                )
                                or None
                            )
                            vision_count += 1
                            if vision_page.vision_usage:
                                usage_call = {
                                    **vision_page.vision_usage,
                                    "purpose": "vision_parse",
                                    "status": "completed",
                                }
                                vision_calls.append(usage_call)
                                vision_total_tokens += _vision_usage_tokens(usage_call)
                                vision_total_cost += _vision_usage_cost(usage_call)
                                if (
                                    vision_total_tokens >= int(settings.vision_max_total_tokens)
                                    or vision_total_cost >= Decimal(str(settings.vision_max_cost_cny))
                                ):
                                    vision_budget_blocked = True
                        elif not warning:
                            warning = "vision_call_failed"
                            vision_calls.append(
                                _vision_failure_usage(
                                    vision_endpoint,
                                    code=warning,
                                    latency_ms=(time.monotonic() - vision_started) * 1000,
                                )
                            )
                if warning:
                    warnings.append(warning)
            quality = page_quality_score(native_text)
            pages.append(
                ExtractedPdfPage(
                    page_number=page_number,
                    width_points=width,
                    height_points=height,
                    rotation=rotation,
                    extraction_method=method,
                    text=native_text,
                    quality_score=quality,
                    average_confidence=(
                        sum(block.confidence or 0.0 for block in blocks) / len(blocks)
                        if blocks and method == "ocr"
                        else None
                    ),
                    blocks=blocks,
                    warning_code=warning,
                    vision_reservation_id=vision_reservation_id,
                )
            )
    finally:
        if pdfium_document is not None:
            try:
                pdfium_document.close()
            except Exception:
                pass

    text_parts: list[str] = []
    spans: list[PageTextSpan] = []
    cursor = 0
    for page in pages:
        if text_parts:
            cursor += 2
        start = cursor
        text_parts.append(page.text)
        cursor += len(page.text)
        spans.append(PageTextSpan(page.page_number, start, cursor))
    methods = {page.extraction_method for page in pages if page.text.strip()}
    extraction_method: DocumentExtractionMethod = (
        "mixed" if len(methods) > 1 else next(iter(methods), "native")
    )
    return PdfExtractionResult(
        pages=tuple(pages),
        text="\n\n".join(text_parts),
        page_spans=tuple(spans),
        extraction_method=extraction_method,
        ocr_page_count=ocr_count,
        warning_code=warnings[0] if warnings else None,
        vision_page_count=vision_count,
        vision_calls=tuple(vision_calls),
        vision_reservation_ids=tuple(
            page.vision_reservation_id
            for page in pages
            if page.vision_reservation_id
        ),
    )
