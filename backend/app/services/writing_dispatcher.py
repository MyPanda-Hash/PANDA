from __future__ import annotations

import logging
import math
import os
import socket
import uuid

from app.core.config import settings
from app.core.database import transaction
from app.services.chat_history_indexer import ChatHistoryIndexDispatcher
from app.services.pi_writing_provider import PiWritingProvider, operation_attempt_cap
from app.repositories import writing

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    configured = os.getenv("DOC_NEXUS_WRITING_DISPATCH_WORKER_ID", "").strip()
    return configured or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _failure_message(exc: BaseException) -> str:
    """End-user wording for a failed attempt; the exception name is a last resort."""
    public = getattr(exc, "public_message", "")
    if isinstance(public, str) and public.strip():
        return public.strip()
    return type(exc).__name__


def _attempt_limit(
    exc: BaseException, *, default: int, previous_code: str | None = None
) -> int:
    """Cap attempts when the failure itself says a long retry chain is pointless.

    A post-generation contract violation costs a full model call per attempt, so a
    deterministic one (e.g. citations outside the supplied evidence) is capped
    instead of burning the worker's whole retry budget. The cap applies to the
    operation: a code recorded by an earlier attempt keeps bounding it even when
    the current failure is a transient one that carries no hint of its own.
    """
    hints = [
        hint
        for hint in (
            getattr(exc, "max_attempts", None),
            operation_attempt_cap(previous_code),
        )
        if isinstance(hint, int) and hint > 0
    ]
    return min([default, *hints])


class WritingDispatcher:
    """Claims durable dispatch intents and executes them with backend Pi Runtime."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        pi_provider: PiWritingProvider | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
    ):
        self.worker_id = worker_id or default_worker_id()
        self.pi_provider = pi_provider or PiWritingProvider()
        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else settings.writing_dispatch_lease_seconds
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.writing_dispatch_max_attempts
        )
        self.history_dispatcher = ChatHistoryIndexDispatcher(
            max_attempts=self.max_attempts
        )

    def effective_lease_seconds(self, *, chapter_count: int = 1) -> int:
        required = (
            math.ceil(settings.pi_writing_timeout_seconds)
            * max(1, chapter_count)
            + 30
        )
        return max(self.lease_seconds, required)

    def run_once(self) -> bool:
        with transaction() as db:
            outbox = writing.claim_dispatch(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.effective_lease_seconds(chapter_count=8),
                max_attempts=self.max_attempts,
                serialize_by_project=True,
            )
            if outbox is None:
                outbox_id = None
                operation_id = None
            else:
                outbox_id = outbox.id
                operation_id = outbox.operation_id

        if outbox_id is None or operation_id is None:
            return self.history_dispatcher.run_once()

        try:
            self.pi_provider.execute(
                operation_id=operation_id,
                outbox_id=outbox_id,
                worker_id=self.worker_id,
            )
            return True
        except Exception as exc:
            logger.exception(
                "Writing dispatch attempt failed: operation=%s error=%s",
                operation_id,
                type(exc).__name__,
            )
            with transaction() as db:
                outbox = writing.dispatch_by_id(db, outbox_id)
                if (
                    outbox is not None
                    and outbox.status == "claimed"
                    and outbox.lease_owner == self.worker_id
                ):
                    operation = writing.operation_by_id(db, outbox.operation_id)
                    writing.mark_dispatch_retry(
                        db,
                        outbox,
                        worker_id=self.worker_id,
                        error_message_safe=_failure_message(exc),
                        max_attempts=_attempt_limit(
                            exc,
                            default=self.max_attempts,
                            previous_code=(
                                operation.error_code if operation is not None else None
                            ),
                        ),
                        error_code=str(getattr(exc, "code", "") or "") or None,
                    )
        # Keep history indexing moving even while the writing queue is busy.
        # The indexer owns its transaction and lease, so a failure here must
        # not change the outcome of the writing dispatch attempt.
        try:
            self.history_dispatcher.run_once()
        except Exception as exc:
            logger.warning(
                "Chat history indexing attempt failed after writing dispatch: %s",
                type(exc).__name__,
            )
        return True
