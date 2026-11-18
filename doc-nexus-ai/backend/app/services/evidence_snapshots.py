"""Exact, content-addressed evidence text. Callers must authorize versions first.

The version directory and SHA256 filename durably bind text to a version without
changing the document schema. A snapshot records text observed now; freezing an
old sidecar cannot establish what a historical, hashless citation originally saw.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.repositories.document_pages import PageLocation

_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_CITATION_RE = re.compile(
    r"(?:writing|vector)-evidence:([A-Za-z0-9][A-Za-z0-9_.-]{0,63}):"
    r"(?:([0-9a-f]{64}):)?([0-9]{1,20})-([0-9]{1,20})"
)


def _snapshot_path(version_id: str, text_sha256: str) -> Path:
    if not _VERSION_RE.fullmatch(version_id) or not _HASH_RE.fullmatch(text_sha256):
        raise ValueError("Invalid evidence snapshot identifier")
    root = settings.upload_dir / "_evidence_snapshots"
    directory = root / version_id
    target = directory / f"{text_sha256}.txt"
    if root.is_symlink() or directory.is_symlink() or target.is_symlink():
        raise ValueError("Invalid evidence snapshot path")
    return target


def read_snapshot(version_id: str, text_sha256: str) -> str | None:
    """Return verified exact text, or unavailable; never fall back to a sidecar."""
    try:
        payload = _snapshot_path(version_id, text_sha256).read_bytes()
        if hashlib.sha256(payload).hexdigest() != text_sha256:
            return None
        return payload.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return None


def freeze_text(version_id: str, text: str) -> str:
    """Atomically retain exact text; an existing snapshot is never overwritten.

    Invalid IDs raise ValueError. Storage failures (including a corrupt existing
    snapshot) raise OSError so callers cannot emit a falsely bound citation.
    """
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    target = _snapshot_path(version_id, digest)
    if target.exists():
        if read_snapshot(version_id, digest) != text:
            raise OSError("Evidence snapshot integrity failure")
        return digest
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".freeze-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard link publishes a complete file without a replace race.
            os.link(temporary, target)
        except FileExistsError:
            if read_snapshot(version_id, digest) != text:
                raise OSError("Evidence snapshot integrity failure") from None
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)
    return digest


def make_citation_id(version_id: str, text_sha256: str, start: int, end: int) -> str:
    """Build an opaque, exact text-range binding (end is exclusive)."""
    if not _VERSION_RE.fullmatch(version_id) or not _HASH_RE.fullmatch(text_sha256):
        raise ValueError("Invalid evidence snapshot identifier")
    if type(start) is not int or type(end) is not int or not 0 <= start < end < 10**20:
        raise ValueError("Invalid evidence text range")
    return f"writing-evidence:{version_id}:{text_sha256}:{start}-{end}"


def parse_citation_id(raw: str) -> tuple[str, str | None, int, int] | None:
    """Parse new and legacy IDs; a missing hash explicitly remains unverified."""
    match = _CITATION_RE.fullmatch(raw)
    if match is None:
        return None
    version_id, digest, raw_start, raw_end = match.groups()
    start, end = int(raw_start), int(raw_end)
    if start >= end:
        return None
    return version_id, digest, start, end


def snapshot_page_location(
    db: Session | None, version_id: str, text: str, start: int, end: int,
) -> PageLocation | None:
    """Use page metadata only when its complete indexed text matches this snapshot."""
    from sqlalchemy import select
    from app.models.document_pages import DocumentPage, DocumentPageBlock
    from app.repositories.document_pages import location_for_char_range

    if db is None or not 0 <= start < end <= len(text):
        return None
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_version_id == version_id)
        .order_by(DocumentPage.page_number)
    ).all()
    if not pages:
        return None
    # extract_pdf_pages joins every page (including empty ones) with two newlines.
    # Match without allocating a second full document string.
    cursor = 0
    for index, page in enumerate(pages):
        if page.page_number != index + 1:
            return None
        if index:
            if not text.startswith("\n\n", cursor):
                return None
            cursor += 2
        if not text.startswith(page.text, cursor):
            return None
        cursor += len(page.text)
    if cursor != len(text):
        return None
    blocks = db.scalars(
        select(DocumentPageBlock)
        .join(DocumentPage, DocumentPage.id == DocumentPageBlock.page_id)
        .where(
            DocumentPage.document_version_id == version_id,
            DocumentPageBlock.char_end > start,
            DocumentPageBlock.char_start < end,
        )
    ).all()
    for block in blocks:
        clean = block.text.strip()
        if (
            not 0 <= block.char_start < block.char_end <= len(text)
            or len(clean) != block.char_end - block.char_start
            or not text.startswith(clean, block.char_start)
        ):
            return None
    return location_for_char_range(db, version_id, start, end)
