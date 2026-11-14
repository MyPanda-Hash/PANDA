"""Task-scoped authentication tokens for sandbox-to-backend broker calls."""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path


def issue_task_gateway_token(
    master: str,
    task_id: str,
    tenant_id: str,
    user_id: str,
    *,
    issued_at: int | None = None,
) -> str:
    if not master:
        return ""
    timestamp = int(time.time() if issued_at is None else issued_at)
    message = "\0".join((str(timestamp), task_id, tenant_id, user_id)).encode()
    signature = hmac.new(master.encode(), message, hashlib.sha256).hexdigest()
    return f"task.{timestamp}.{signature}"


def verify_task_gateway_token(
    token: str,
    master: str,
    task_id: str,
    tenant_id: str,
    user_id: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 3_600,
    lease_root: Path | None = None,
) -> bool:
    """Authenticate one task-scoped broker call.

    The token is replay-bounded by ``max_age_seconds``. A turn that runs longer
    than that cannot be handed a new token, so once the static window is spent
    the token stays usable only while ``lease_root`` still holds a live lease
    for this exact token — that is, while the turn that was issued it runs.
    """
    if not token or not master:
        return False
    if hmac.compare_digest(token, master):
        return True
    prefix, separator, remainder = token.partition(".")
    timestamp_raw, separator2, _ = remainder.partition(".")
    if prefix != "task" or not separator or not separator2:
        return False
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False
    current = int(time.time() if now is None else now)
    if timestamp > current + 30:
        return False
    expected = issue_task_gateway_token(
        master,
        task_id,
        tenant_id,
        user_id,
        issued_at=timestamp,
    )
    if not hmac.compare_digest(token, expected):
        return False
    if current - timestamp <= max_age_seconds:
        return True
    if lease_root is None:
        return False
    # Imported here: the Pi runtime package imports this module, so binding it
    # at module scope would make the cycle depend on which side loads first.
    from app.services.pi_runtime.run_lease import secret_lease_active

    return secret_lease_active(lease_root, token, now=current)
