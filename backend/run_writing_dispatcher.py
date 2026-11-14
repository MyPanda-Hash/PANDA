from __future__ import annotations

import signal
import time

from app.core.config import settings
from app.services.writing_dispatcher import WritingDispatcher


def main() -> None:
    # 建表和迁移由 Backend 负责，避免抢先生成没有 Alembic 版本的空库。
    stopping = False

    def stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    dispatcher = WritingDispatcher()
    while not stopping:
        if not dispatcher.run_once():
            time.sleep(max(0.1, settings.writing_dispatch_poll_seconds))


if __name__ == "__main__":
    main()
