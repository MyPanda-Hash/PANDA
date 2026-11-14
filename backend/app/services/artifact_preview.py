"""First-pages preview of a finished export Artifact.

Why this exists: until now the only way to find out whether a template
actually produced a good-looking deck was to download the .pptx and open
PowerPoint. If it looked wrong the author had to come back, change the
template and run the whole export again — and "does it look right" is the
entire point of the template feature.

Everything here reuses the preview plumbing already built for template
samples (LibreOffice Impress → PDF → pdfium → PNG, in an isolated temp dir
with a hard timeout). It is strictly read-only: it previews bytes that were
already generated, never renders a new artifact, and never widens who may
see one — callers resolve the artifact through the same authorisation they
use for download.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services import doc_preview

logger = logging.getLogger(__name__)

PREVIEW_VERSION = "artifact-v1"
# Enough to judge cover, a content page and the template's body treatment.
MAX_PREVIEW_PAGES = 3
_CACHE_DIR = "_artifact_preview"

STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"

PREVIEWABLE_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class ArtifactPreviewUnavailable(RuntimeError):
    """The environment cannot render a preview; the download still works."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _cache_root() -> Path:
    root = settings.upload_dir / _CACHE_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("artifact preview cache dir unavailable: %s", root)
    return root


def _atomic_write(path: Path, data: bytes) -> None:
    """Write the whole image or nothing: a half-written PNG must never cache."""
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _page_path(digest: str, page: int) -> Path:
    return _cache_root() / f"{digest}-{PREVIEW_VERSION}-{page:02d}.png"


def is_previewable(content_type: str | None) -> bool:
    return (content_type or "") in PREVIEWABLE_TYPES


def cache_key(content: bytes) -> str:
    """Keyed by the artifact's own bytes: unchanged output, no reconversion."""
    return hashlib.sha256(content).hexdigest()


def cached_pages(digest: str) -> int:
    """How many pages are already rendered for this artifact."""
    total = 0
    for page in range(1, MAX_PREVIEW_PAGES + 1):
        path = _page_path(digest, page)
        try:
            if path.is_file() and path.stat().st_size > 0:
                total += 1
                continue
        except OSError:
            pass
        break
    return total


def read_page(digest: str, page: int) -> bytes | None:
    if page < 1 or page > MAX_PREVIEW_PAGES:
        return None
    path = _page_path(digest, page)
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        logger.warning("artifact preview read failed for %s p%s", digest, page)
    return None


def generate(content: bytes, filename: str) -> int:
    """Render and cache the first pages. Returns how many pages are available.

    Raises :class:`ArtifactPreviewUnavailable` when the environment has no
    Impress filter — the caller reports that and leaves the download alone.
    """
    digest = cache_key(content)
    existing = cached_pages(digest)
    if existing:
        return existing
    if not doc_preview.presentation_renderer_available():
        raise ArtifactPreviewUnavailable(
            "当前环境缺少演示文稿渲染组件，无法生成预览，可直接下载查看"
        )
    try:
        pdf_bytes = doc_preview.convert_presentation_to_pdf(content, filename)
        pages = doc_preview.render_pdf_pages_to_png(
            pdf_bytes, max_pages=MAX_PREVIEW_PAGES
        )
    except doc_preview.LibreOfficeUnavailable as exc:
        raise ArtifactPreviewUnavailable(
            "预览转换组件暂时不可用，可直接下载查看"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never fail an export over a preview
        logger.warning("artifact preview conversion failed: %s", type(exc).__name__)
        raise ArtifactPreviewUnavailable("预览生成失败，可直接下载查看") from exc

    root = _cache_root()
    written = 0
    for index, image in enumerate(pages, start=1):
        try:
            if root.is_dir():
                _atomic_write(_page_path(digest, index), image)
                written += 1
        except OSError:
            logger.warning("artifact preview cache write failed for %s", digest)
            break
    return written or len(pages)
