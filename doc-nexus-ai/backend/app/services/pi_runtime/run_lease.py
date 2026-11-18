"""Activity leases that keep one running turn's short-lived credentials valid.

A Pi turn hands its child process two credentials with a fixed lifetime: the
Harness task gateway token and the Nexus capability token. Both are minted once,
before the child starts, and cannot be replaced while it runs. A chat turn whose
wall-clock budget was replaced by a token budget can outlive either lifetime, at
which point the child keeps answering but every tool call starts failing.

A lease makes the credential's real lifetime "as long as this turn is still
running": the runner opens one per credential, renews it on model activity, and
drops it when the turn ends. Verification still checks the signature and the
static lifetime first, and only consults the lease once that lifetime is spent.
A lease is keyed by the credential's own digest, so it can extend exactly the
credential it was opened for and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

_LEASE_DIRNAME = "run-leases"
_MAX_LEASE_SECONDS = 86_400


def lease_id(secret: str) -> str:
    """Address a lease by its credential without storing the credential."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _lease_dir(root: Path) -> Path:
    return root / _LEASE_DIRNAME


def _lease_path(root: Path, identifier: str) -> Path:
    return _lease_dir(root) / f"{identifier}.json"


def open_run_lease(root: Path, secret: str, *, lease_seconds: float) -> str | None:
    """Start (or restart) the lease for one credential and drop stale ones."""
    if not secret:
        return None
    identifier = lease_id(secret)
    directory = _lease_dir(root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        _prune_expired(directory)
        _write(_lease_path(root, identifier), lease_seconds)
    except OSError:
        return None
    return identifier


def renew_run_lease(root: Path, identifier: str, *, lease_seconds: float) -> bool:
    """Extend a lease that is still live; never resurrect an expired one."""
    if not identifier or not run_lease_active(root, identifier):
        return False
    try:
        _write(_lease_path(root, identifier), lease_seconds)
    except OSError:
        return False
    return True


def close_run_lease(root: Path, identifier: str) -> None:
    if not identifier:
        return
    _lease_path(root, identifier).unlink(missing_ok=True)


def run_lease_active(
    root: Path, identifier: str, *, now: float | None = None
) -> bool:
    if not identifier:
        return False
    try:
        with _lease_path(root, identifier).open("r", encoding="utf-8") as stream:
            expires_at = float(json.load(stream)["expires_at"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    return expires_at > (time.time() if now is None else now)


def secret_lease_active(
    root: Path, secret: str, *, now: float | None = None
) -> bool:
    """Answer whether the turn holding this exact credential is still running."""
    if not secret:
        return False
    return run_lease_active(root, lease_id(secret), now=now)


def _write(path: Path, lease_seconds: float) -> None:
    bounded = max(1.0, min(float(lease_seconds), _MAX_LEASE_SECONDS))
    payload = json.dumps(
        {"expires_at": time.time() + bounded}, separators=(",", ":")
    )
    handle, raw = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.chmod(raw, 0o600)
        os.replace(raw, path)
    except OSError:
        Path(raw).unlink(missing_ok=True)
        raise


def _prune_expired(directory: Path) -> None:
    """A crashed run leaves its lease behind; it must not linger forever."""
    current_time = time.time()
    for entry in directory.glob("*.json"):
        try:
            with entry.open("r", encoding="utf-8") as stream:
                expires_at = float(json.load(stream)["expires_at"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            entry.unlink(missing_ok=True)
            continue
        if expires_at <= current_time:
            entry.unlink(missing_ok=True)
