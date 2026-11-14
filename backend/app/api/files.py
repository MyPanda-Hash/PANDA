"""Files API — public file download endpoint for generated artifacts."""

from __future__ import annotations

import io
import logging
import mimetypes
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy import select

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.models.chat_deck_render import ChatDeckRender
from app.repositories import chat as chat_repo
from app.repositories import harness as harness_repo
from app.repositories import usage as usage_repo
from app.services import minio_storage
from app.services.document_processor import extract_docx_text
from app.services.doc_preview import (
    DocumentConversionError,
    LibreOfficeUnavailable,
    render_office_preview_pdf,
    render_presentation_preview_pdf,
    render_spreadsheet_preview_pdf,
)
from app.services.harness.file_storage import (
    base_generated_file_task_id,
    resolve_workspace,
    resolve_workspace_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

MAX_DOCUMENT_PREVIEW_BYTES = 10 * 1024 * 1024
# A deck carries its slides as images, so the same bound as a Word document
# would reject an ordinary 20-slide export. The rendered PDF is bounded again
# downstream by the converter and by the client's embed limit.
MAX_PRESENTATION_PREVIEW_BYTES = 40 * 1024 * 1024
# A workbook is compressed XML: data-heavy books outgrow the Word bound but
# never need the deck bound (pptx balloons on images). 20MB still converts
# inside the process timeout with headroom to spare.
MAX_SPREADSHEET_PREVIEW_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_PREVIEW_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCUMENT_PREVIEW_CHARS = 200_000

# Only DOCX gets the extracted-text preview; a presentation's text is not a
# usable substitute for its layout.
_DOCX_SUFFIXES = frozenset({".docx"})
_PRESENTATION_SUFFIXES = frozenset({".pptx", ".potx"})
# Only XLSX is rendered; macro-enabled and legacy binary workbooks stay
# download-only.
_SPREADSHEET_SUFFIXES = frozenset({".xlsx"})


def _preview_limit(filename: str) -> tuple[int, str]:
    """Byte bound for a preview, with the noun used in the message."""
    if PurePosixPath(filename).suffix.lower() in _PRESENTATION_SUFFIXES:
        return MAX_PRESENTATION_PREVIEW_BYTES, "演示文稿"
    if PurePosixPath(filename).suffix.lower() in _SPREADSHEET_SUFFIXES:
        return MAX_SPREADSHEET_PREVIEW_BYTES, "表格"
    return MAX_DOCUMENT_PREVIEW_BYTES, "文稿"


def _preview_too_large(limit: int, label: str) -> HTTPException:
    return HTTPException(
        status_code=413, detail=f"{label}超过 {limit // (1024 * 1024)} MB，请下载查看"
    )


def _validate_office_package(
    content: bytes,
    filename: str,
    *,
    suffixes: frozenset[str],
    max_bytes: int,
    label: str,
) -> None:
    """Reject a file that must not be previewed, before any conversion.

    Text and rendered previews share these bounds: an accepted suffix, stored
    size, a real zip package, and its declared uncompressed size. Checking the
    package here keeps a zip bomb away from the LibreOffice process too.
    """
    if PurePosixPath(filename).suffix.lower() not in suffixes:
        raise HTTPException(status_code=415, detail="该格式不支持内容预览，请下载查看")
    if len(content) > max_bytes:
        raise _preview_too_large(max_bytes, label)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total_uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=422, detail=f"{label}无法解析，请下载后检查文件"
        )
    if total_uncompressed > MAX_DOCUMENT_PREVIEW_UNCOMPRESSED_BYTES:
        raise HTTPException(status_code=413, detail=f"{label}内容过大，请下载查看")


def _validate_docx_preview(content: bytes, filename: str) -> None:
    _validate_office_package(
        content,
        filename,
        suffixes=_DOCX_SUFFIXES,
        max_bytes=MAX_DOCUMENT_PREVIEW_BYTES,
        label="文稿",
    )


def _document_preview(content: bytes, filename: str) -> Response:
    """Return bounded plain text, never executable Office or HTML content."""
    _validate_docx_preview(content, filename)
    try:
        text = extract_docx_text(content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="文稿无法解析，请下载后检查文件") from exc
    return JSONResponse(
        content={
            "kind": "text",
            "content": text[:MAX_DOCUMENT_PREVIEW_CHARS],
            "truncated": len(text) > MAX_DOCUMENT_PREVIEW_CHARS,
        },
        headers={"Cache-Control": "no-store"},
    )


def _document_preview_pdf(content: bytes, filename: str) -> Response:
    """Return a rendered PDF of a DOCX so previews keep the original layout.

    Rendering goes through the shared LibreOffice path, which caches by content
    hash and bounds the conversion (process timeout, output size). Callers fall
    back to the bounded text preview when the renderer is unavailable.
    """
    _validate_docx_preview(content, filename)
    try:
        pdf = render_office_preview_pdf(content, filename)
    except (LibreOfficeUnavailable, DocumentConversionError) as exc:
        logger.warning("office pdf preview unavailable for %s: %s", filename, exc)
        raise HTTPException(
            status_code=422, detail="文稿暂无法在线预览，请下载原文件查看"
        ) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
        },
    )


def _presentation_preview_pdf(content: bytes, filename: str) -> Response:
    """Return a rendered PDF of a PPTX so a deck previews with its layout.

    Same contract as the Word preview: bounded, cached by content hash, and a
    loud 422 when the renderer is unavailable. There is no text fallback — a
    slide's meaning lives in its layout, so the caller reports "preview
    unavailable" and leaves the download as the way to see the file.
    """
    _validate_office_package(
        content,
        filename,
        suffixes=_PRESENTATION_SUFFIXES,
        max_bytes=MAX_PRESENTATION_PREVIEW_BYTES,
        label="演示文稿",
    )
    try:
        pdf = render_presentation_preview_pdf(content, filename)
    except (LibreOfficeUnavailable, DocumentConversionError) as exc:
        logger.warning("presentation pdf preview unavailable for %s: %s", filename, exc)
        raise HTTPException(
            status_code=422, detail="演示文稿暂无法在线预览，请下载原文件查看"
        ) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
        },
    )


def _spreadsheet_preview_pdf(content: bytes, filename: str) -> Response:
    """Return a rendered PDF of an XLSX so a workbook previews with its grid.

    Same contract as the Word preview: bounded, cached by content hash, and a
    loud 422 when the renderer is unavailable. There is no text fallback — a
    workbook's meaning lives in its grid and layout — so the caller reports
    "preview unavailable" and leaves the download as the way to see the file.
    """
    _validate_office_package(
        content,
        filename,
        suffixes=_SPREADSHEET_SUFFIXES,
        max_bytes=MAX_SPREADSHEET_PREVIEW_BYTES,
        label="表格",
    )
    try:
        pdf = render_spreadsheet_preview_pdf(content, filename)
    except (LibreOfficeUnavailable, DocumentConversionError) as exc:
        logger.warning("spreadsheet pdf preview unavailable for %s: %s", filename, exc)
        raise HTTPException(
            status_code=422, detail="表格暂无法在线预览，请下载原文件查看"
        ) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
        },
    )


def _rendered_office_preview(content: bytes, filename: str) -> Response:
    """Route a ``preview=pdf`` request to the matching LibreOffice filter.

    Dispatch is by suffix and each branch re-validates the package itself, so a
    presentation can never reach the Word converter and a document can never
    reach the text extractor.
    """
    if PurePosixPath(filename).suffix.lower() in _PRESENTATION_SUFFIXES:
        return _presentation_preview_pdf(content, filename)
    if PurePosixPath(filename).suffix.lower() in _SPREADSHEET_SUFFIXES:
        return _spreadsheet_preview_pdf(content, filename)
    return _document_preview_pdf(content, filename)


# ``preview`` stays a string so the historical boolean values keep working while
# a client can also ask for the rendered document (``preview=pdf``).
_PREVIEW_MODES = {
    "pdf": "pdf",
    "1": "text",
    "true": "text",
    "yes": "text",
    "on": "text",
    "": "off",
    "0": "off",
    "false": "off",
    "no": "off",
    "off": "off",
}


def _preview_mode(value: str) -> str:
    """Map the ``preview`` query value onto off / text / pdf."""
    normalized = (value or "").strip().lower()
    mode = _PREVIEW_MODES.get(normalized)
    if mode is None:
        raise HTTPException(status_code=400, detail="preview 仅支持 true、false 或 pdf")
    return mode


def _generated_file_candidates(file_path: str) -> tuple[str, ...] | None:
    raw = file_path.strip()
    if not raw or raw.startswith("/") or "\\" in raw:
        return None
    parts = raw.split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return None
    candidates = [PurePosixPath(*parts).as_posix()]
    if parts[0] == "files" and len(parts) > 1:
        candidates.append(PurePosixPath(*parts[1:]).as_posix())
    return tuple(candidates)

def _resolve_local_generated_file(
    task_id: str, file_path: str
) -> tuple[Path, str] | None:
    """Resolve a local snapshot without buffering it for a download."""
    candidates = _generated_file_candidates(file_path)
    if candidates is None:
        return None
    workspace = resolve_workspace(task_id)
    if workspace is None:
        return None
    filename = PurePosixPath(candidates[0]).name
    for relative in candidates:
        resolved = resolve_workspace_file(workspace, relative)
        if resolved is not None and resolved.is_file():
            return resolved, filename
    return None

def _download_bytes_response(content: bytes, filename: str, media_type: str) -> Response:
    """Stream an already-authorized database/object-store snapshot."""
    encoded_name = quote(filename, safe="")
    return StreamingResponse(
        iter((content,)),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "Content-Length": str(len(content)),
            "X-Filename": encoded_name,
        },
    )

def read_generated_file_bytes(
    task_id: str,
    file_path: str,
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, str]:
    """Read one immutable generated-file snapshot from MinIO or its local fallback.

    Authorization belongs to the caller: downloads check task ownership, while
    chat-deck rendering first proves that the exact task/path was attached to
    the owned assistant message. The active base workspace is never consulted
    when ``task_id`` names a per-turn snapshot.
    """
    candidates = _generated_file_candidates(file_path)
    if candidates is None:
        raise FileNotFoundError("文件不存在")
    filename = PurePosixPath(candidates[0]).name
    if minio_storage.is_minio_enabled():
        last_error: Exception | None = None
        for relative in candidates:
            try:
                content = minio_storage.download_bytes(
                    object_key=f"harness/{task_id}/{relative}"
                )
                if max_bytes is not None and len(content) > max_bytes:
                    raise ValueError("文件超过允许大小")
                return content, filename
            except ValueError:
                raise
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            logger.warning(
                "MinIO generated-file read failed, serving workspace fallback: "
                "task=%s path=%s error=%s",
                task_id,
                file_path,
                last_error,
            )
    workspace = resolve_workspace(task_id)
    if workspace is None:
        raise FileNotFoundError("任务工作区不存在或已过期")
    for relative in candidates:
        resolved = resolve_workspace_file(workspace, relative)
        if resolved is None:
            continue
        if max_bytes is not None and resolved.stat().st_size > max_bytes:
            raise ValueError("文件超过允许大小")
        return resolved.read_bytes(), filename
    raise FileNotFoundError("文件不存在")


def _can_access_generated_file(user: User, task_id: str) -> bool:
    base_task_id = base_generated_file_task_id(task_id)
    if base_task_id.startswith("pi-schedule-"):
        from app.models.schedules import ScheduleRun
        from app.services.schedule_access import can_access_schedule_run

        with transaction() as db:
            run = db.scalar(
                select(ScheduleRun).where(ScheduleRun.runtime_id == base_task_id)
            )
            return run is not None and can_access_schedule_run(db, run, str(user.id))
    if user.role == "admin":
        return True
    candidate_task_ids = (
        (task_id, base_task_id) if base_task_id != task_id else (task_id,)
    )
    with transaction() as db:
        for candidate_task_id in candidate_task_ids:
            task = harness_repo.get_task(db, candidate_task_id)
            if task and task.user_id == str(user.id):
                return True
            # Pi chat tasks run as a shared agent; authorize their user/session
            # namespace, including per-turn delivery namespaces.
            runtime_prefix = f"pi-chat-{user.id}"
            if candidate_task_id == runtime_prefix:
                return True
            local_id = candidate_task_id.removeprefix(f"{runtime_prefix}-")
            if local_id == candidate_task_id:
                # Existing files may still use the pre-user-namespace session layout.
                local_id = candidate_task_id.removeprefix("pi-chat-")
            if local_id != candidate_task_id and chat_repo.get_owned_session(
                db, local_id, user.id
            ):
                return True
    return False


def _download_chat_deck_render(user: User, file_path: str, *, mode: str) -> Response:
    parts = file_path.strip().split("/")
    if not parts or not parts[0]:
        raise HTTPException(status_code=404, detail="套版成果不存在")
    with transaction() as db:
        render = db.get(ChatDeckRender, parts[0])
        if render is None or render.owner_user_id != user.id:
            raise HTTPException(status_code=404, detail="套版成果不存在")
        if render.status != "ready" or render.output_content is None or not render.output_name:
            raise HTTPException(status_code=409, detail="套版成果当前不可下载")
        content = render.output_content
        filename = render.output_name
        media_type = render.output_mime or "application/octet-stream"
    if mode == "pdf":
        return _presentation_preview_pdf(content, filename)
    if mode == "text":
        raise HTTPException(status_code=415, detail="演示文稿不支持文本预览")
    return _download_bytes_response(content, filename, media_type)


def _download_writing_artifact(
    user: User, file_path: str, *, mode: str = "off", shared: bool = False
) -> Response:
    """Serve a writing DOCX/PPTX artifact body by id.

    Path shape: artifacts/{artifact_id}/{display_name}. The display name is
    cosmetic; ownership and artifact state are validated server-side.
    """
    parts = file_path.strip().split("/")
    if not parts or not parts[0]:
        raise HTTPException(status_code=404, detail="成果不存在")
    artifact_id = parts[0]
    with transaction() as db:
        artifact = usage_repo.shared_artifact(db, artifact_id, user) if shared else usage_repo.owned_artifact(db, artifact_id, user.id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="成果不存在")
        if artifact.status != "ready" or not usage_repo.has_downloadable_snapshot(
            artifact
        ):
            raise HTTPException(status_code=409, detail="成果当前不可下载，请重新生成")
        assert artifact.content is not None and artifact.content_type is not None
        if mode == "pdf":
            return _rendered_office_preview(artifact.content, artifact.name)
        if mode == "text":
            return _document_preview(artifact.content, artifact.name)
        return _download_bytes_response(artifact.content, artifact.name, artifact.content_type)


@router.get("/{task_id}/{file_path:path}")
def download_generated_file(
    task_id: str,
    file_path: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    preview: str = "false",
) -> Response:
    """Download a generated workspace file.

    MinIO is read server-side so browser downloads never depend on a
    rewritten S3 signature. The workspace file is a fallback when the
    object is unavailable. Access is restricted to the task owner or admin.
    With preview=true, DOCX files return bounded plain text; with
    preview=pdf they return the file rendered by LibreOffice (Writer for
    DOCX, Impress for PPTX) so the preview keeps the original layout.
    Neither mode modifies the stored file or its download.
    """
    mode = _preview_mode(preview)
    if task_id == "artifacts":
        return _download_writing_artifact(user, file_path, mode=mode)
    if task_id == "artifact-shares":
        return _download_writing_artifact(user, file_path, mode=mode, shared=True)
    if task_id == "chat-deck-renders":
        return _download_chat_deck_render(user, file_path, mode=mode)

    if not _can_access_generated_file(user, task_id):
        raise HTTPException(status_code=403, detail="无权访问该文件")
    if mode == "off" and not minio_storage.is_minio_enabled():
        local = _resolve_local_generated_file(task_id, file_path)
        if local is not None:
            resolved, filename = local
            encoded_name = quote(filename)
            return FileResponse(
                path=resolved,
                media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                filename=filename,
                headers={"X-Filename": encoded_name},
            )


    limit = _preview_limit(file_path)[0] if mode != "off" else None
    try:
        content, filename = read_generated_file_bytes(
            task_id,
            file_path,
            max_bytes=limit,
        )
    except ValueError:
        assert limit is not None
        _, label = _preview_limit(file_path)
        raise _preview_too_large(limit, label) from None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if mode == "pdf":
        return _rendered_office_preview(content, filename)
    if mode == "text":
        return _document_preview(content, filename)

    return _download_bytes_response(
        content,
        filename,
        mimetypes.guess_type(filename)[0] or "application/octet-stream",
    )
