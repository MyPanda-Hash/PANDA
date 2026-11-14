"""图片资料直接视觉入库：保留原件、逐帧验证、可追溯分区，不调用 OCR。"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.services.pdf_ocr import ExtractedPdfPage, PdfExtractionResult


_FORMAT_EXTENSIONS = {
    "JPEG": {".jpg", ".jpeg", ".jpe", ".jfif"},
    "PNG": {".png", ".apng"},
    "WEBP": {".webp"}, "GIF": {".gif"}, "BMP": {".bmp"}, "DIB": {".dib"},
    "TIFF": {".tif", ".tiff"}, "AVIF": {".avif", ".avifs"},
    "JPEG2000": {".jp2", ".j2k", ".jpc", ".jpf", ".jpx", ".j2c"},
    "ICO": {".ico"}, "PCX": {".pcx"}, "TGA": {".tga"},
    "PPM": {".ppm", ".pgm", ".pbm", ".pnm"},
    "HEIF": {".heic", ".heif", ".hif"},
}
IMAGE_EXTENSIONS = frozenset(ext for extensions in _FORMAT_EXTENSIONS.values() for ext in extensions)
IMAGE_PARSER_VERSION = "direct-vision-1"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_IMAGE_PIXELS = 80_000_000
MAX_IMAGE_PAGES = 8
MAX_VISION_CALLS = 8
_TILE_CORE = 1536
_TILE_OVERLAP = 64
_COMPLETED_PAGE_WARNINGS = {None, "image_vision_unclear", "image_vision_uncertain", "image_vision_no_content"}
_LEGACY_IMAGE_LABEL = re.compile(
    r"(?:图片分区 [1-9]\d*｜)?视觉模型解析，未经核验"
    r"(?=\n\n【(?:可见文字|可见表格|视觉描述（非原文）|不确定性（不可作确定事实）)】\n)"
)


def is_image_version(version: Any) -> bool:
    """依据原件格式识别图片资料，不把 PDF 的视觉辅助等同于图片。"""
    path = getattr(version, "file_path", None) or getattr(version, "source_relative_path", None) or ""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def image_source_text(
    version: Any, text: str, *, db: Session | None = None, start: int = 0, end: int | None = None,
) -> str:
    """仅屏蔽系统块起点的旧标签；正文同名文字、原快照与字符定位保持不变。"""
    start, stop, _ = slice(start, end).indices(len(text))
    window = text[start:stop]
    if not is_image_version(version) or "视觉模型解析，未经核验" not in text:
        return window
    # 首块起点固定为 0；其他起点只信与原快照完整匹配的持久化块，不能从正文猜测。
    block_starts = {0}
    if db is not None:
        from sqlalchemy import select
        from app.models.document_pages import DocumentPage, DocumentPageBlock

        blocks = db.execute(
            select(DocumentPageBlock.char_start, DocumentPageBlock.text)
            .join(DocumentPage, DocumentPage.id == DocumentPageBlock.page_id)
            .where(
                DocumentPage.document_version_id == version.id,
                DocumentPageBlock.source == "vision",
                DocumentPageBlock.char_start < stop,
                DocumentPageBlock.char_end > start,
            )
        )
        block_starts.update(
            offset for offset, block_text in blocks
            if text.startswith(block_text.strip(), offset)
        )
    parts: list[str] = []
    cursor = start
    for offset in sorted(block_starts):
        match = _LEGACY_IMAGE_LABEL.match(text, offset)
        if match is None:
            continue
        left, right = max(start, match.start()), min(stop, match.end())
        if left >= right:
            continue
        parts.extend((text[cursor:left], " " * (right - left)))
        cursor = right
    if not parts:
        return window
    parts.append(text[cursor:stop])
    return "".join(parts)


class ImageDocumentError(ValueError):
    """可安全展示的图片输入错误，不携带解析器或文件系统细节。"""

    def __init__(self, code: str, message: str = "图片文件无效或超过安全限制") -> None:
        self.code = code
        super().__init__(message)


@lru_cache(maxsize=1)
def _register_codecs() -> None:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    Image.init()


def _source_bytes(content: bytes | Path) -> bytes:
    if isinstance(content, Path):
        try:
            if content.stat().st_size > MAX_IMAGE_BYTES:
                raise ImageDocumentError("image_too_large", "图片原件不能超过 32 MiB")
            with content.open("rb") as source:
                content = source.read(MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise ImageDocumentError("image_invalid", "无法读取图片原件") from exc
    if not isinstance(content, bytes) or not content:
        raise ImageDocumentError("image_invalid", "图片文件为空或无效")
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageDocumentError("image_too_large", "图片原件不能超过 32 MiB")
    return content


def _open_image(content: bytes, filename: str) -> Image.Image:
    extension = Path(filename).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise ImageDocumentError("image_format_unsupported", "不支持这种图片格式")
    _register_codecs()
    try:
        image = Image.open(io.BytesIO(content), formats=list(_FORMAT_EXTENSIONS))
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageDocumentError("image_invalid", "图片文件损坏或无法解码") from exc
    if extension not in _FORMAT_EXTENSIONS.get(image.format or "", set()):
        image.close()
        raise ImageDocumentError("image_format_mismatch", "图片内容与文件扩展名不一致")
    return image


def _scan(content: bytes | Path, filename: str) -> tuple[bytes, list[tuple[int, int]]]:
    content = _source_bytes(content)
    sizes: list[tuple[int, int]] = []
    total_pixels = 0
    try:
        with _open_image(content, filename) as image:
            # 检查 PNG CRC 等完整性；随后重新打开，逐帧真正解码。
            image.verify()
        with _open_image(content, filename) as image:
            for index in range(MAX_IMAGE_PAGES + 1):
                try:
                    image.seek(index)
                except EOFError:
                    break
                if index >= MAX_IMAGE_PAGES:
                    raise ImageDocumentError("image_page_limit_exceeded", "图片最多支持 8 页或 8 帧，不能只保存首帧")
                width, height = image.size
                pixels = width * height
                total_pixels += pixels
                if width <= 0 or height <= 0 or pixels > MAX_IMAGE_PIXELS or total_pixels > MAX_TOTAL_IMAGE_PIXELS:
                    raise ImageDocumentError("image_pixel_limit_exceeded", "图片像素超过单页 4000 万或合计 8000 万的限制")
                image.load()
                if image.getexif().get(274) in {5, 6, 7, 8}:
                    width, height = height, width
                sizes.append((width, height))
                # ICO 内部只是同一图标的替代分辨率，Pillow 选择最大的表示。
                if image.format == "ICO":
                    break
    except ImageDocumentError:
        raise
    except (OSError, ValueError, SyntaxError, EOFError, OverflowError, Image.DecompressionBombError) as exc:
        raise ImageDocumentError("image_invalid", "图片文件损坏或无法完整解码") from exc
    if not sizes:
        raise ImageDocumentError("image_invalid", "图片没有可解码页面")
    return content, sizes


def validate_image_document(content: bytes | Path, filename: str) -> int:
    """字节和分段上传临时路径使用同一个完整解码校验。"""
    return len(_scan(content, filename)[1])


def _normalized_frame(image: Image.Image, page_number: int) -> Image.Image:
    image.seek(page_number - 1)
    normalized = ImageOps.exif_transpose(image)
    mode = "RGBA" if "A" in normalized.getbands() or "transparency" in normalized.info else "RGB"
    if normalized.mode != mode:
        converted = normalized.convert(mode)
        normalized.close()
        normalized = converted
    normalized.info.clear()
    return normalized


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_image_page(content: bytes, page_number: int, *, filename: str) -> bytes:
    """返回不含原始元数据的完整分辨率 PNG；调用方负责版本与 ACL。"""
    content, sizes = _scan(content, filename)
    if type(page_number) is not int or not 1 <= page_number <= len(sizes):
        raise ImageDocumentError("image_page_not_found", "图片页码不存在")
    try:
        with _open_image(content, filename) as source:
            with _normalized_frame(source, page_number) as image:
                return _png(image)
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise ImageDocumentError("image_invalid", "无法生成图片预览") from exc


def _tiles(width: int, height: int) -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
    count = ((width + _TILE_CORE - 1) // _TILE_CORE) * ((height + _TILE_CORE - 1) // _TILE_CORE)
    if count > MAX_VISION_CALLS:
        return []
    return [
        ((max(0, x - _TILE_OVERLAP), max(0, y - _TILE_OVERLAP),
          min(width, x + _TILE_CORE + _TILE_OVERLAP), min(height, y + _TILE_CORE + _TILE_OVERLAP)),
         (x, y, min(width, x + _TILE_CORE), min(height, y + _TILE_CORE)))
        for y in range(0, height, _TILE_CORE) for x in range(0, width, _TILE_CORE)
    ]


def _model_image(image: Image.Image) -> dict[str, str]:
    from app.services.vision_fallback import chat_image_limits

    payload = _png(image)
    mime = "image/png"
    limit = chat_image_limits()["max_bytes"]
    if len(payload) > limit:
        # 不缩小文字：仅对过大的无损编码使用全分辨率高质量 JPEG。
        with Image.new("RGB", image.size, "white") as background:
            background.paste(image, mask=image.getchannel("A") if image.mode == "RGBA" else None)
            output = io.BytesIO()
            background.save(output, format="JPEG", quality=90, subsampling=0)
            payload, mime = output.getvalue(), "image/jpeg"
    if len(payload) > limit:
        raise ImageDocumentError("image_vision_payload_limit", "图片分区仍超过视觉输入限制，未截断处理")
    return {"data": base64.b64encode(payload).decode("ascii"), "mimeType": mime}


def _page_text(items: list[dict[str, str]]) -> tuple[str, tuple[Any, ...]]:
    from app.services.pdf_ocr import PdfTextBlock

    blocks: list[PdfTextBlock] = []
    for index, item in enumerate(items, start=1):
        # 仅不确定性、空白不能被标签包装成可检索的正文。
        if not any(item.get(key, "").strip() for key in ("visible_text", "tables", "observations")):
            continue
        sections = [f"图片分区 {index}"] if len(items) > 1 else []
        for key, label in (
            ("visible_text", "可见文字"), ("tables", "可见表格"),
            ("observations", "视觉描述（非原文）"), ("uncertainty", "不确定性（不可作确定事实）"),
        ):
            value = item.get(key, "").strip()
            if value:
                sections.append(f"【{label}】\n{value}")
        blocks.append(PdfTextBlock(len(blocks), "\n\n".join(sections), "vision", None, None))
    return "\n\n".join(block.text for block in blocks), tuple(blocks)


def _assemble(pages: list[ExtractedPdfPage], calls: list[dict[str, Any]]) -> PdfExtractionResult:
    from app.services.pdf_ocr import PageTextSpan, PdfExtractionResult

    text_parts, spans = [], []
    cursor = 0
    for page in pages:
        if text_parts:
            cursor += 2
        start = cursor
        text_parts.append(page.text)
        cursor += len(page.text)
        spans.append(PageTextSpan(page.page_number, start, cursor))
    has_content = any(page.text.strip() for page in pages)
    incomplete = any(page.warning_code not in _COMPLETED_PAGE_WARNINGS for page in pages)
    if not has_content:
        warning = next((
            page.warning_code for page in pages
            if page.warning_code not in _COMPLETED_PAGE_WARNINGS | {"image_vision_pending"}
        ), "image_vision_no_content")
    else:
        warning = (
            "image_vision_partial" if incomplete or any(not page.text.strip() for page in pages)
            else "image_vision_unclear" if any(page.warning_code == "image_vision_unclear" for page in pages)
            else None
        )
    return PdfExtractionResult(
        pages=tuple(pages), text="\n\n".join(text_parts), page_spans=tuple(spans),
        extraction_method="vision", ocr_page_count=0, warning_code=warning,
        vision_page_count=sum(bool(page.text.strip()) for page in pages),
        vision_calls=tuple(calls),
        vision_reservation_ids=tuple(dict.fromkeys(
            str(call["reservation_id"]) for call in calls if call.get("reservation_id")
        )),
    )


def extract_image_pages(
    content: bytes, *, filename: str, vision_endpoint: Any, user_id: str,
    operation_id: str, space_ids: Sequence[str] = (),
    cached: PdfExtractionResult | None = None,
    checkpoint: Callable[[PdfExtractionResult], None] | None = None,
) -> PdfExtractionResult:
    """直接视觉逐页/分区解析；检查点失败立即停止，不发起下一笔模型调用。"""
    from app.services.pdf_ocr import ExtractedPdfPage
    from app.services.vision_fallback import VisionFallbackError, parse_images_with_vision

    content, sizes = _scan(content, filename)
    cached_pages = {page.page_number: page for page in cached.pages} if cached else {}
    calls = [dict(call) for call in cached.vision_calls] if cached else []
    pages = [
        ExtractedPdfPage(number, float(width), float(height), 0, "vision", "", 0.0, None, (), "image_vision_pending")
        for number, (width, height) in enumerate(sizes, start=1)
    ]
    for number, page in cached_pages.items():
        if 1 <= number <= len(pages) and page.warning_code in _COMPLETED_PAGE_WARNINGS:
            if page.warning_code == "image_vision_uncertain":
                # 旧码曾用于全部图片；复用付费结果，只保留原文中的具体疑点。
                page = replace(
                    page, warning_code="image_vision_unclear"
                    if "【不确定性（不可作确定事实）】\n" in page.text else None,
                )
            pages[number - 1] = page

    def save() -> PdfExtractionResult:
        result = _assemble(pages, calls)
        if checkpoint is not None:
            checkpoint(result)
        return result

    def budget_exhausted() -> bool:
        return (
            len(calls) >= MAX_VISION_CALLS
            or sum(int(call.get("total_tokens") or 0) for call in calls) >= int(settings.vision_max_total_tokens)
            or sum((Decimal(str(call.get("cost") or "0")) for call in calls), Decimal("0")) >= Decimal(str(settings.vision_max_cost_cny))
        )

    with _open_image(content, filename) as source:
        for number, (width, height) in enumerate(sizes, start=1):
            if pages[number - 1].warning_code in _COMPLETED_PAGE_WARNINGS:
                continue
            tiles = _tiles(width, height)
            if not tiles:
                pages[number - 1] = replace(pages[number - 1], warning_code="image_vision_partial")
                save()
                continue
            items: list[dict[str, str]] = []
            failure: str | None = None
            try:
                normalized = _normalized_frame(source, number)
            except (OSError, ValueError, SyntaxError, EOFError):
                pages[number - 1] = replace(pages[number - 1], warning_code="image_invalid")
                save()
                continue
            with normalized as frame:
                for tile_index, (crop, core) in enumerate(tiles, start=1):
                    metadata = {
                        "page_number": number, "tile_index": tile_index, "tile_count": len(tiles),
                        "crop_bounds": list(crop), "core_bounds": list(core),
                    }
                    prior = next((call for call in reversed(calls) if
                        call.get("page_number") == number and call.get("tile_index") == tile_index
                        and call.get("crop_bounds") == list(crop) and call.get("status") == "completed"
                        and isinstance(call.get("document_items"), list)), None)
                    if prior is not None:
                        items.extend(prior["document_items"])
                        continue
                    if budget_exhausted():
                        failure = "vision_budget_exceeded"
                        break
                    if vision_endpoint is None:
                        failure = "vision_endpoint_unavailable"
                        break
                    try:
                        with frame.crop(crop) as tile:
                            encoded = _model_image(tile)
                        relative_core = (core[0] - crop[0], core[1] - crop[1], core[2] - crop[0], core[3] - crop[1])
                        context = (
                            f" 本图来自原图第 {number} 页（{width}×{height}），分区 {tile_index}/{len(tiles)}，"
                            f"裁剪在原图中的边界为 {crop}。本分区核心区域在当前图片的像素边界为 {relative_core}。"
                            "核心区域之外仅用于阅读衔接，不重复转写；跨边界文字/表格行由起始位置所在的核心区域负责。"
                            "不推断相邻分区中未显示的内容，无法完整辨认的跨界内容写入不确定性。"
                        )
                        result = parse_images_with_vision(
                            vision_endpoint, [encoded], user_id=user_id,
                            operation_id=f"{operation_id}:page:{number}:tile:{tile_index}:attempt:{len(calls) + 1}",
                            space_ids=space_ids, document_mode=True, document_context=context,
                        )
                        call = {**result.usage, **metadata, "purpose": "vision_parse", "status": "completed",
                                "reservation_id": result.reservation_id,
                                "document_items": [dict(item) for item in result.items]}
                        calls.append(call)
                        items.extend(call["document_items"])
                        if (
                            sum(int(value.get("total_tokens") or 0) for value in calls) > int(settings.vision_max_total_tokens)
                            or sum((Decimal(str(value.get("cost") or "0")) for value in calls), Decimal("0")) > Decimal(str(settings.vision_max_cost_cny))
                        ):
                            failure = "vision_budget_exceeded"
                            call["status"] = "failed"
                            call["error_code"] = failure
                    except VisionFallbackError as exc:
                        failure = exc.code
                        calls.append({
                            **(exc.usage or {}), **metadata, "purpose": "vision_parse", "status": "failed",
                            "error_code": exc.code, "reservation_id": exc.reservation_id,
                            "usage_unverified": exc.usage is None or bool(exc.usage.get("usage_unverified")),
                        })
                    except ImageDocumentError as exc:
                        failure = exc.code
                    if not failure and tile_index == len(tiles):
                        text, blocks = _page_text(items)
                        pages[number - 1] = replace(
                            pages[number - 1], text=text, blocks=blocks,
                            warning_code=(
                                "image_vision_no_content" if not text else "image_vision_unclear"
                                if any(item.get("uncertainty", "").strip() for item in items) else None
                            ),
                            vision_usage=calls[-1], vision_reservation_id=calls[-1].get("reservation_id"),
                        )
                    else:
                        pages[number - 1] = replace(pages[number - 1], warning_code=failure or "image_vision_pending")
                    save()
                    if failure:
                        break
            if failure and pages[number - 1].warning_code != failure:
                # 预算/端点预检没有模型调用，仍保存明确的页面失败状态。
                pages[number - 1] = replace(pages[number - 1], warning_code=failure)
                save()
            elif not failure and pages[number - 1].warning_code == "image_vision_pending":
                text, blocks = _page_text(items)
                pages[number - 1] = replace(
                    pages[number - 1], text=text, blocks=blocks,
                    warning_code=(
                        "image_vision_no_content" if not text else "image_vision_unclear"
                        if any(item.get("uncertainty", "").strip() for item in items) else None
                    ),
                )
                save()
    return _assemble(pages, calls)
