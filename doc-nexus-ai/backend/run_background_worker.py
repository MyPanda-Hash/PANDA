from __future__ import annotations

import asyncio
import signal
from typing import cast

from app.core.config import settings
from app.core.logging import setup_logging
from app.services.background_workers import BackgroundWorkerService
from app.services.execution_resources import WorkerRole


async def run() -> None:
    setup_logging()
    role = cast(WorkerRole, settings.worker_role)
    if role not in {"all", "documents", "background"}:
        raise RuntimeError(
            "run_background_worker requires worker_role=documents, background, or all"
        )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    service = BackgroundWorkerService(role)
    await service.start()
    try:
        await stopping.wait()
    finally:
        await service.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
