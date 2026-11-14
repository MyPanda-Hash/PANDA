from __future__ import annotations

import logging
from types import FrameType

import uvicorn

from app.api import chat

logger = logging.getLogger(__name__)


class ChatDrainingServer(uvicorn.Server):
    """Stop new chat generations as soon as graceful shutdown starts."""

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        logger.info("chat graceful shutdown started; signal=%s", sig)
        chat.set_chat_draining(True)
        super().handle_exit(sig, frame)


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        timeout_graceful_shutdown=960,
    )
    ChatDrainingServer(config).run()


if __name__ == "__main__":
    main()
