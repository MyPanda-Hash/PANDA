from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.database import transaction
from app.repositories import system_config

DEFAULT_CHAT_WELCOME_SUGGESTIONS: tuple[str, ...] = ()


def default_chat_welcome_suggestions_text() -> str:
    return "\n".join(DEFAULT_CHAT_WELCOME_SUGGESTIONS)


def parse_chat_welcome_suggestions(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def load_chat_welcome_suggestions() -> list[str]:
    raw = system_config.resolve(
        "chat_welcome_suggestions",
        default_chat_welcome_suggestions_text(),
    )
    return parse_chat_welcome_suggestions(raw)


@dataclass(frozen=True)
class ChatAgentConfig:
    base_url: str
    api_key: str
    agent_id: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.agent_id)


def load_chat_agent_config() -> ChatAgentConfig:
    try:
        with transaction() as db:
            stored = system_config.get_all(db)
    except Exception:
        stored = {}

    return ChatAgentConfig(
        base_url=(stored.get("agent_chat_base_url") or settings.agent_chat_base_url)
        .strip()
        .rstrip("/"),
        api_key=(
            stored.get("agent_chat_api_key") or settings.agent_chat_api_key
        ).strip(),
        agent_id=(
            stored.get("agent_chat_agent_id") or settings.agent_chat_agent_id
        ).strip(),
    )
