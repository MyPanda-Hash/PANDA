"""Helpers to upload files generated in a sandbox workspace to MinIO.

When MinIO is unavailable, files are served via a local HTTP endpoint.
"""

from __future__ import annotations

import io
import json
import mimetypes
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Callable

from app.services import minio_storage
from app.services.document_processor import decode_text_bytes
from app.services.markdown_docx import markdown_to_docx_bytes

logger = logging.getLogger(__name__)

_EXCLUDED_DIRS = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    "docx_build",
    "docx_tmp",
    "word_temp",
    ".next",
    "data",
    ".pi",
    ".generated-files",
})
_OOXML_STAGING_DIRS = ("docx_tmp", "docx_build", "word_temp")
_OOXML_REQUIRED = ("[Content_Types].xml", "_rels/.rels", "word/document.xml")
_OOXML_PART_NAMES = frozenset({
    "[Content_Types].xml",
    ".rels",
    "document.xml",
    "document.xml.rels",
    "styles.xml",
    "settings.xml",
    "fontTable.xml",
    "core.xml",
    "app.xml",
})
_DELIVERABLE_SUFFIXES = frozenset({
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".doc",
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".zip",
})
_DELIVERABLE_COMPOUND_SUFFIXES = (".deck.json",)
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"
_MAX_FILES = 100
_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
_INTERNAL_WORKSPACE_DELIVERY_SUFFIX = re.compile(
    r"(?:^|[，,]\s*)(?:\*\*)?(?:(?:文件|保存|输出)?路径|文件位置|成稿已存)"
    r"(?:\*\*)?\s*[:：]?(?:\*\*)?\s*"
    r"(?:`/workspace/[^`\n]+`|/workspace/[^\s<>\"'`，。；]+)\s*[。.]?$"
)
_DELIVERY_TASK_RE = re.compile(r"^(?P<base>.+)-turn-[0-9a-f]{32}$")
_FENCE_MARKER = re.compile(r"^\s*(`{3,}|~{3,})")


def base_generated_file_task_id(task_id: str) -> str:
    """解析每轮成果命名空间对应的稳定任务 ID。"""
    match = _DELIVERY_TASK_RE.fullmatch(task_id.strip())
    return match.group("base") if match else task_id


def _delivery_snapshot_workspace(workspace: Path, task_id: str) -> Path:
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id).strip("._") or "delivery"
    return workspace / ".generated-files" / safe_task_id


# ---------------------------------------------------------------------------
# Workspace registry – used when MinIO is off to serve local file URLs
# ---------------------------------------------------------------------------

# Maps task_id → workspace Path for local file serving.
_workspace_registry: dict[str, Path] = {}

_LOCAL_URL_PREFIX = "/api/files"


def register_workspace(task_id: str, workspace: Path) -> None:
    """Register a workspace path so local file URLs can be resolved."""
    _workspace_registry[task_id] = workspace.resolve()


def get_workspace(task_id: str) -> Path | None:
    """Return the registered workspace path for *task_id*."""
    return _workspace_registry.get(task_id)


def resolve_workspace(task_id: str) -> Path | None:
    """Resolve a task workspace from memory, Harness DB, or the default Pi layout."""
    registered = get_workspace(task_id)
    if registered is not None and registered.is_dir():
        return registered
    base_task_id = base_generated_file_task_id(task_id)
    try:
        from app.core.database import transaction
        from app.repositories import harness as harness_repo

        with transaction() as db:
            task = harness_repo.get_task(db, task_id)
            if task is None and base_task_id != task_id:
                task = harness_repo.get_task(db, base_task_id)
        if task and task.workspace_path:
            path = Path(task.workspace_path)
            if base_task_id != task_id:
                path = _delivery_snapshot_workspace(path, task_id)
            if path.is_dir():
                register_workspace(task_id, path)
                return path
    except Exception:
        logger.exception("Failed to resolve workspace for %s", task_id)
    try:
        from types import SimpleNamespace

        from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID, PiRuntimeSettings
        from app.services.pi_runtime.skills import workspace_dir_for_agent

        runtime = PiRuntimeSettings.from_env()
        agent = SimpleNamespace(id=PI_RUNTIME_AGENT_ID, tenant_id="_system")
        candidate = workspace_dir_for_agent(runtime, agent, base_task_id)
        if base_task_id != task_id:
            candidate = _delivery_snapshot_workspace(candidate, task_id)
        if candidate.is_dir():
            register_workspace(task_id, candidate)
            return candidate
    except Exception:
        logger.exception("Failed to resolve default Pi workspace for %s", task_id)
    return None


def resolve_workspace_file(workspace: Path, file_path: str) -> Path | None:
    """Resolve a workspace-relative file, accepting a legacy extra ``files/`` prefix."""
    relative = Path(file_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    workspace_resolved = workspace.resolve()
    candidates = [workspace_resolved / relative]
    if relative.parts and relative.parts[0] == "files":
        candidates.append(workspace_resolved / Path(*relative.parts[1:]))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(workspace_resolved)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def workspace_delivery_rules() -> dict[str, Any]:
    """向 Pi 声明工具提供与最终扫描器同源的交付规则。"""
    return {
        "excluded_dirs": sorted(_EXCLUDED_DIRS),
        "excluded_names": sorted(_OOXML_PART_NAMES),
        "suffixes": sorted(_DELIVERABLE_SUFFIXES),
        "compound_suffixes": list(_DELIVERABLE_COMPOUND_SUFFIXES),
        "max_size": _MAX_SIZE,
        "max_files": _MAX_FILES,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_excluded(path: Path, workspace: Path) -> bool:
    """Return whether *path* is excluded relative to the workspace root."""
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except (OSError, ValueError):
        return True
    return any(part in _EXCLUDED_DIRS for part in relative.parts)


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name, strict=False)[0] or "application/octet-stream"


def _is_valid_docx(path: Path, data: bytes) -> bool:
    if path.suffix.lower() != ".docx":
        return True
    required = set(_OOXML_REQUIRED)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.testzip() is None and required.issubset(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _is_fake_word_doc(path: Path, data: bytes) -> bool:
    if path.suffix.lower() != ".doc":
        return False
    return not data.startswith(_OLE_MAGIC)


def _is_deliverable(path: Path) -> bool:
    if path.name in _OOXML_PART_NAMES:
        return False
    return (
        path.name.lower().endswith(_DELIVERABLE_COMPOUND_SUFFIXES)
        or path.suffix.lower() in _DELIVERABLE_SUFFIXES
    )


def _staging_has_ooxml(staging: Path) -> bool:
    names = {
        item.relative_to(staging).as_posix()
        for item in staging.rglob("*")
        if item.is_file()
    }
    return set(_OOXML_REQUIRED).issubset(names)


def _infer_docx_output(workspace: Path) -> Path:
    for suffix in (".docx", ".doc", ".md"):
        matches = sorted(
            path
            for path in workspace.iterdir()
            if path.is_file() and path.suffix.lower() == suffix
        )
        if matches:
            return workspace / f"{matches[0].stem}.docx"
    return workspace / "document.docx"


def _zip_ooxml_staging(staging: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, path.relative_to(staging).as_posix())


def package_ooxml_staging(workspace: Path) -> Path | None:
    """Zip an unpackaged OOXML directory into a .docx when no valid package exists."""
    if not workspace.is_dir():
        return None
    existing = [
        path
        for path in workspace.iterdir()
        if path.is_file() and path.suffix.lower() == ".docx"
    ]
    for path in existing:
        try:
            if _is_valid_docx(path, path.read_bytes()):
                return None
        except OSError:
            continue
    for name in _OOXML_STAGING_DIRS:
        staging = workspace / name
        if not staging.is_dir() or not _staging_has_ooxml(staging):
            continue
        output = _infer_docx_output(workspace)
        try:
            _zip_ooxml_staging(staging, output)
        except OSError:
            logger.exception("Failed to package OOXML staging directory %s", staging)
            return None
        if _is_valid_docx(output, output.read_bytes()):
            return output
        output.unlink(missing_ok=True)
    return None


def _compute_relative(path: Path, workspace: Path) -> Path:
    try:
        return path.resolve().relative_to(workspace.resolve())
    except (ValueError, OSError):
        return Path(path.name)


def _build_local_url(task_id: str, relative: Path) -> str:
    return f"{_LOCAL_URL_PREFIX}/{task_id}/{relative.as_posix()}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upload_workspace_file(
    *,
    task_id: str,
    workspace: Path,
    path: Path,
    content: bytes | None = None,
) -> dict[str, Any] | None:
    """Upload a single workspace file to MinIO and return a download URL.

    Returns a same-origin HTTP URL served by the backend; the backend reads
    MinIO server-side when configured. Returns ``None`` when the file is missing, too large,
    or an invalid DOCX.
    """
    register_workspace(task_id, workspace)

    resolved = path.resolve()
    if not resolved.is_file():
        return None

    relative = _compute_relative(path, workspace)

    data: bytes | None = content
    size = 0
    if data is not None:
        size = len(data)
    else:
        try:
            data = resolved.read_bytes()
            size = len(data)
        except OSError:
            logger.exception("Failed to read file %s", path)
            return None

    if size > _MAX_SIZE:
        logger.warning("Skipping upload of %s: size %s exceeds limit", path, size)
        return None
    if data is None or not _is_valid_docx(path, data) or _is_fake_word_doc(path, data):
        logger.warning("Skipping invalid Word upload: %s", path)
        return None

    if minio_storage.is_minio_enabled():
        try:
            object_key = f"harness/{task_id}/{relative.as_posix()}"
            minio_storage.upload_bytes(
                content=data,
                object_key=object_key,
                content_type=_content_type(path),
            )
        except Exception:
            logger.exception(
                "Failed to upload workspace file %s to MinIO; falling back to local URL",
                path,
            )
    url = _build_local_url(task_id, relative)

    return {
        "name": path.name,
        "path": str(relative),
        "size": size,
        "url": url,
    }


def snapshot_deliverable_files(workspace: Path) -> dict[str, tuple[int, int]]:
    """Capture relative_path -> (mtime_ns, size) for deliverable workspace files.

    Used as a pre-turn baseline so later scans only report files created or
    modified during the current agent turn.
    """
    if not workspace.is_dir():
        return {}

    snapshot: dict[str, tuple[int, int]] = {}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded(path, workspace) or not _is_deliverable(path):
            continue
        relative = _compute_relative(path, workspace).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[relative] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


_SKILL_UPLOAD_MANIFEST_NAME = "skill-uploads.jsonl"


def _skill_upload_manifest_path(workspace: Path) -> Path:
    return workspace / ".generated-files" / _SKILL_UPLOAD_MANIFEST_NAME


def snapshot_skill_uploads(workspace: Path) -> set[tuple[str, int, int]]:
    """Read the skill-upload manifest as (relative_path, mtime_ns, size) entries.

    Used as a pre-turn baseline so scans only treat uploads recorded during
    the current turn as deliverable.
    """
    try:
        raw = _skill_upload_manifest_path(workspace).read_text(encoding="utf-8")
    except OSError:
        return set()
    entries: set[tuple[str, int, int]] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            entries.add(
                (str(item["path"]), int(item["mtime_ns"]), int(item["size"]))
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return entries


def record_skill_upload(workspace: Path, path: Path) -> None:
    """Record an explicit skill upload in the per-task delivery manifest.

    The end-of-turn scanner keys off the mtime baseline, so re-uploading an
    unchanged file (e.g. one produced by an earlier failed turn) would never
    be delivered; manifest entries let the scanner include them anyway.
    """
    try:
        stat = path.stat()
        entry = json.dumps(
            {
                "path": _compute_relative(path, workspace).as_posix(),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            },
            ensure_ascii=False,
        )
        manifest = _skill_upload_manifest_path(workspace)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(entry + "\n")
    except OSError:
        logger.exception("Failed to record skill upload: %s", path)


def _is_own_rendered_docx(path: Path) -> bool:
    """Whether *path* is a .docx produced by our minimal Markdown renderer."""
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        return False
    # Our renderer emits this exact root element; docx-js and other real
    # Word producers declare extra namespaces and part relationships.
    return document.startswith(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    )


def convert_markdown_deliverables(
    workspace: Path, since: dict[str, tuple[int, int]] | None, *,
    evidence_sources: list[dict[str, Any]] | None = None,
    evidence_source_resolver: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    selected_paths: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert this turn's ``.md`` deliverables into ``.docx`` next to them.

    Chat sessions produce Markdown files, but users expect Word documents.
    The renderer is pure stdlib, so conversion happens here instead of inside
    the sandbox (no pandoc/npm available). Sources stay in the workspace; a
    failed conversion still uploads the original ``.md`` unchanged. Existing
    ``.docx`` files are only regenerated when this renderer produced them;
    foreign Word files (e.g. created by Pi) are never overwritten. Returns each
    converted Markdown path with its validated source records; the scanner keeps
    those records with the delivered file instead of reconstructing them later.
    显式交付时只转换登记的 Markdown，允许重新交付未改动的原有文稿。
    """
    if not workspace.is_dir():
        return {}
    converted: dict[str, list[dict[str, Any]]] = {}
    for path in list(workspace.rglob("*.md")):
        if not path.is_file() or path.is_symlink() or _is_excluded(path, workspace):
            continue
        relative = _compute_relative(path, workspace).as_posix()
        if selected_paths is not None and relative not in selected_paths:
            continue
        if since is not None and selected_paths is None:
            try:
                stat = path.stat()
            except OSError:
                continue
            if since.get(relative) == (stat.st_mtime_ns, stat.st_size):
                continue
        output = path.with_suffix(".docx")
        if output.exists() and not _is_own_rendered_docx(output):
            continue
        try:
            markdown = decode_text_bytes(path.read_bytes())
            sources = evidence_source_resolver([markdown]) if evidence_source_resolver else evidence_sources
            output.write_bytes(markdown_to_docx_bytes(markdown))
        except (OSError, ValueError):
            logger.exception("Failed to convert Markdown deliverable %s", path)
        else:
            converted[relative] = sources or []
    return converted


def _strip_internal_workspace_delivery_narration(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    fence: str | None = None
    cleaned: list[str] = []
    for line in lines:
        marker = _FENCE_MARKER.match(line)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            cleaned.append(line)
            continue
        cleaned.append(
            line
            if fence is not None
            else _INTERNAL_WORKSPACE_DELIVERY_SUFFIX.sub("", line).rstrip()
        )
    return "\n".join(cleaned)


def rewrite_markdown_paths_to_docx(
    reply: str, generated_files: list[dict[str, Any]] | None
) -> str:
    """Align reply text with the delivered files after md→docx conversion.

    Pi narrates the workspace paths it wrote (e.g. ``/workspace/a/报告.md``),
    but the delivery replaced those Markdown files with ``.docx``. Rewrite the
    mentioned ``.md`` paths of generated ``.docx`` files so the reply matches
    what the user can actually download. Full relative-path mentions are
    rewritten first; a basename fallback covers bare ``报告.md`` mentions.
    """
    text = str(reply or "")
    files = generated_files if isinstance(generated_files, list) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name.lower().endswith(".docx"):
            continue
        stem = name[:-5]
        relative = str(item.get("path") or "").strip()
        if relative.lower().endswith(".docx"):
            relative_md = relative[:-5] + ".md"
            # Exact relative-path mention first (handles nested directories
            # and same-stem files in different folders).
            for prefix in ("", "/workspace/", "/"):
                pattern = re.escape(prefix + relative_md) if prefix else re.escape(relative_md)
                if re.search(pattern, text, flags=re.IGNORECASE):
                    text = re.sub(
                        pattern,
                        (prefix if prefix else "") + relative[:-5] + ".docx",
                        text,
                        flags=re.IGNORECASE,
                    )
                    break
        if not stem:
            continue
        # Basename fallback for bare mentions only. A slash before the stem
        # means a path-prefixed mention of some other file in a sibling
        # directory (e.g. ``/workspace/b/报告.md`` when only ``a/报告.docx``
        # was delivered); path mentions are handled by the exact branch above.
        text = re.sub(
            rf"(?<![\w/]){re.escape(stem)}\.md",
            f"{stem}.docx",
            text,
            flags=re.IGNORECASE,
        )
    return _strip_internal_workspace_delivery_narration(text)


def scan_and_upload_workspace(
    task_id: str,
    workspace: Path,
    *,
    uploaded_paths: set[str] | None = None,
    since: dict[str, tuple[int, int]] | None = None,
    skill_uploads_since: set[tuple[str, int, int]] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
    evidence_source_resolver: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    storage_task_id: str | None = None,
    selected_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """扫描工作区并上传成果，返回同源下载 URL。

    跳过依赖和内部目录；提供 ``since`` 时只交付本轮新增或变更的文件。
    ``skill_uploads_since`` 为轮前 Skill 上传 manifest 基线：本轮内 Skill
    显式上传过、但 mtime 未变的文件也会被交付。
    ``storage_task_id`` 为本轮提供独立快照和对象命名空间，
    ``task_id`` 仍用于稳定的运行时工作区与下载授权。
    ``selected_paths`` 只交付明确登记的文件（含主动重新交付的既有成果）；
    空集合表示不交付，``None`` 保留普通聊天的本轮变更扫描行为。
    """
    if selected_paths is not None and not selected_paths:
        return []
    if not workspace.is_dir():
        return []
    upload_task_id = storage_task_id or task_id
    snapshot_workspace = (
        _delivery_snapshot_workspace(workspace, upload_task_id)
        if storage_task_id and storage_task_id != task_id
        else None
    )
    upload_workspace = snapshot_workspace if snapshot_workspace is not None else workspace
    register_workspace(upload_task_id, upload_workspace)

    package_ooxml_staging(workspace)
    converted_markdown = convert_markdown_deliverables(
        workspace, since, evidence_sources=evidence_sources,
        evidence_source_resolver=evidence_source_resolver,
        selected_paths=selected_paths,
    )

    uploaded_paths = uploaded_paths or set()
    skill_uploads_since = skill_uploads_since or set()
    skill_uploads_new = snapshot_skill_uploads(workspace) - skill_uploads_since
    results: list[dict[str, Any]] = []
    count = 0

    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded(path, workspace) or not _is_deliverable(path):
            continue

        relative = _compute_relative(path, workspace).as_posix()
        if selected_paths is not None and relative not in selected_paths:
            markdown_source = relative[:-5] + ".md" if relative.endswith(".docx") else None
            if markdown_source not in selected_paths or markdown_source not in converted_markdown:
                continue
        if path.suffix.lower() == ".md" and relative in converted_markdown:
            # The rendered .docx replaces this Markdown in the delivery.
            continue
        if since is not None and selected_paths is None:
            try:
                stat = path.stat()
            except OSError:
                continue
            if since.get(relative) == (stat.st_mtime_ns, stat.st_size):
                key = (relative, stat.st_mtime_ns, stat.st_size)
                if key not in skill_uploads_new:
                    continue

        resolved_path = str(path.resolve())
        if resolved_path in uploaded_paths:
            continue
        if count >= _MAX_FILES:
            logger.warning("Workspace scan reached max file limit (%s)", _MAX_FILES)
            break

        upload_path = path
        if snapshot_workspace is not None:
            upload_path = snapshot_workspace / relative
            try:
                upload_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, upload_path)
            except OSError:
                logger.exception("Failed to snapshot generated file: %s", path)
                continue
            if upload_path.suffix.lower() == ".pptx":
                from app.services.presentation_deck import normalize_presentation_package

                try:
                    data = upload_path.read_bytes()
                    if len(data) > _MAX_SIZE:
                        continue
                    normalized = normalize_presentation_package(data)
                    if normalized is not data:
                        # 仅修改本轮交付副本，通用上传入口与原件保持只读。
                        upload_path.write_bytes(normalized)
                except Exception:
                    logger.exception("Skipping invalid presentation snapshot: %s", path)
                    continue
        info = upload_workspace_file(
            task_id=upload_task_id,
            workspace=upload_workspace,
            path=upload_path,
        )
        if info:
            if path.suffix.lower() == ".docx":
                source_path = relative[:-5] + ".md"
                if source_path in converted_markdown:
                    info["evidence_sources"] = converted_markdown[source_path]
            results.append(info)
            uploaded_paths.add(resolved_path)
            count += 1

    return results
