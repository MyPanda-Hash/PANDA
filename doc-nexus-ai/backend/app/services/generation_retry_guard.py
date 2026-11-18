from __future__ import annotations

from threading import Lock

_lock = Lock()
_active: set[tuple[str, str]] = set()


def claim(kind: str, task_id: str) -> bool:
    key = (kind, task_id)
    with _lock:
        if key in _active:
            return False
        _active.add(key)
        return True


def release(kind: str, task_id: str) -> None:
    with _lock:
        _active.discard((kind, task_id))
