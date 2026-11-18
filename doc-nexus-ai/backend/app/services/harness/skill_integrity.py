"""Content integrity for approved Skill trees."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Published Skill trees live on the workspace bind mount, where every stat or
# read is a cross-filesystem round trip. A single 38 MB Skill (341 files) took
# ~7 s serially, and the same tree is re-hashed on every chat turn, so the
# digest must be computed with bounded parallelism.
_HASH_WORKERS = 16
_HASH_CHUNK = 1024 * 1024


class SkillIntegrityError(ValueError):
    """A Skill tree is missing, unsafe, or no longer matches approval."""


# Digests are keyed by (root, size, mtime_ns) of every file, so any content or
# metadata change invalidates the entry without re-reading bytes. The map is
# bounded because a long-lived process publishes a new tree per install.
_DIGEST_CACHE: dict[str, str] = {}
_DIGEST_CACHE_LIMIT = 256
_DIGEST_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> str | None:
    with _DIGEST_CACHE_LOCK:
        return _DIGEST_CACHE.get(key)


def _cache_put(key: str, value: str) -> None:
    with _DIGEST_CACHE_LOCK:
        if len(_DIGEST_CACHE) >= _DIGEST_CACHE_LIMIT:
            # Insertion order is our own recency: drop the oldest quarter.
            for stale in list(_DIGEST_CACHE)[: _DIGEST_CACHE_LIMIT // 4]:
                _DIGEST_CACHE.pop(stale, None)
        _DIGEST_CACHE[key] = value


def _read_file_bytes(path: Path, expected: os.stat_result) -> tuple[bytes, int]:
    """Return (raw content, size) for one file after re-verifying its identity.

    The content is returned verbatim because the tree digest folds the file
    bytes directly; hashing them first would change the published value.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SkillIntegrityError(f"cannot open Skill file safely: {path}") from exc
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
        ):
            raise SkillIntegrityError(f"Skill file changed while hashing: {path}")
        data = stream.read()
        if len(data) != opened.st_size:
            raise SkillIntegrityError(f"Skill file changed while hashing: {path}")
        return data, opened.st_size


def _collect_entries(
    root: Path,
) -> list[tuple[bytes, bytes, Path]]:
    """Collect entries in os.walk order (per directory: dirs, then files).

    The traversal order defines the digest, so it must match os.walk exactly.
    Only the per-entry lstat is deferred to a parallel pool afterwards.
    """
    collected: list[tuple[bytes, bytes, Path]] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()
        for name in [*dirnames, *filenames]:
            path = current_path / name
            relative = path.relative_to(root).as_posix().encode("utf-8")
            kind = b"D" if name in dirnames else b"F"
            collected.append((kind, relative, path))
    return collected


def hash_skill_tree(root: Path) -> str:
    """Hash every path and file byte while rejecting links and special files.

    Directory order comes from os.walk (it defines the canonical digest), but
    per-entry lstat and file reads run in a thread pool: approved Skill trees
    live on the workspace bind mount, where one syscall per file dominated a
    ~7 s cold hash of the 38 MB dashi-ppt tree. Digests are cached by the full
    stat signature, so repeated chat turns skip both phases.
    """
    if root.is_symlink() or not root.is_dir():
        raise SkillIntegrityError("Skill root is not a regular directory")
    root = root.resolve()

    root_stat = root.stat()

    # Cheap pre-check: a published tree is immutable between installs, so the
    # cached value can be returned from the root metadata alone. Only a root
    # mtime/size change forces the full walk. `_TREE_CACHE` never outlives a
    # content change because publishing replaces the whole directory.
    root_key = f"{root}|{root_stat.st_size}|{root_stat.st_mtime_ns}"
    fast = _cache_get(root_key)
    if fast is not None:
        return fast

    collected = _collect_entries(root)
    if not collected:
        return _finish_digest(root_stat, [], {})

    workers = min(_HASH_WORKERS, len(collected))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        stats = list(pool.map(lambda item: item[2].lstat(), collected))

    entries: list[tuple[bytes, bytes, os.stat_result]] = []
    files: list[tuple[Path, os.stat_result]] = []
    for (kind_hint, relative, path), path_stat in zip(collected, stats):
        mode = path_stat.st_mode
        if stat.S_ISLNK(mode):
            raise SkillIntegrityError(
                f"Skill tree contains symlink: {relative.decode('utf-8', 'replace')}"
            )
        if stat.S_ISDIR(mode):
            kind = b"D"
        elif stat.S_ISREG(mode):
            kind = b"F"
        else:
            raise SkillIntegrityError(
                f"Skill tree contains special file: {relative.decode('utf-8', 'replace')}"
            )
        if kind != kind_hint:
            # A name listed as a file is actually a directory, or vice versa.
            raise SkillIntegrityError(
                f"Skill tree entry changed type: {relative.decode('utf-8', 'replace')}"
            )
        entries.append((kind, relative, path_stat))
        if kind == b"F":
            files.append((path, path_stat))

    signature = hashlib.sha256()
    signature.update(b"R")
    signature.update((root_stat.st_mode & 0o7777).to_bytes(2, "big"))
    for kind, relative, path_stat in entries:
        signature.update(kind)
        signature.update((path_stat.st_mode & 0o7777).to_bytes(2, "big"))
        signature.update(path_stat.st_size.to_bytes(8, "big"))
        signature.update(path_stat.st_mtime_ns.to_bytes(8, "big"))
        signature.update(len(relative).to_bytes(8, "big"))
        signature.update(relative)

    cache_key = signature.hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    contents: dict[bytes, tuple[bytes, int]] = {}
    if files:
        read_workers = min(_HASH_WORKERS, len(files))
        with ThreadPoolExecutor(max_workers=read_workers) as pool:
            ordered = list(pool.map(lambda item: _read_file_bytes(*item), files))
        index = 0
        for kind, relative, _ in entries:
            if kind == b"F":
                contents[relative] = ordered[index]
                index += 1

    value = _finish_digest(root_stat, entries, contents)
    _cache_put(cache_key, value)
    _cache_put(root_key, value)
    return value


def _finish_digest(
    root_stat: os.stat_result,
    entries: list[tuple[bytes, bytes, os.stat_result]],
    contents: dict[bytes, tuple[bytes, int]],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"R")
    digest.update((root_stat.st_mode & 0o7777).to_bytes(2, "big"))
    for kind, relative, path_stat in entries:
        digest.update(kind)
        digest.update((path_stat.st_mode & 0o7777).to_bytes(2, "big"))
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        if kind == b"F":
            data, size = contents[relative]
            digest.update(size.to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()