from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Literal, TypeVar

from app.core.config import settings

WorkerRole = Literal["all", "api", "documents", "background"]
T = TypeVar("T")


def role_runs_documents(role: WorkerRole) -> bool:
    return role in {"all", "documents"}


def role_runs_background(role: WorkerRole) -> bool:
    return role in {"all", "background"}


class WorkerExecutionResources:
    """Dedicated executors keep durable worker jobs off the API default pool."""

    def __init__(self, role: WorkerRole) -> None:
        self.role = role
        self._document_pool = (
            ThreadPoolExecutor(
                max_workers=settings.document_worker_threads,
                thread_name_prefix="doc-worker",
            )
            if role_runs_documents(role)
            else None
        )
        self._background_pool = (
            ThreadPoolExecutor(
                max_workers=settings.background_worker_threads,
                thread_name_prefix="background-worker",
            )
            if role_runs_background(role)
            else None
        )
        self._closed = False

    async def run_document(
        self, function: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> T:
        pool = self._document_pool
        if self._closed or pool is None:
            raise RuntimeError("document worker executor is not available")
        return await asyncio.get_running_loop().run_in_executor(
            pool, partial(function, *args, **kwargs)
        )

    async def run_background(
        self, function: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> T:
        pool = self._background_pool
        if self._closed or pool is None:
            raise RuntimeError("background worker executor is not available")
        return await asyncio.get_running_loop().run_in_executor(
            pool, partial(function, *args, **kwargs)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pool in (self._document_pool, self._background_pool):
            if pool is not None:
                # Durable leases recover unfinished synchronous work. Waiting here
                # could make ASGI or worker shutdown unbounded.
                pool.shutdown(wait=False, cancel_futures=True)
