"""Runtime resolution for backend Pi writing execution."""

from __future__ import annotations

from app.core.config import settings
from app.core.database import transaction
from app.repositories import system_config

ALLOWED_WRITING_PROVIDERS = frozenset({"pi_chat"})


def load_writing_overrides() -> dict[str, str]:
    try:
        with transaction() as db:
            return system_config.get_all(db)
    except Exception:
        return {}


def normalize_writing_provider(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value if value in ALLOWED_WRITING_PROVIDERS else "pi_chat"


def resolve_writing_provider(stored: dict[str, str] | None = None) -> str:
    """Resolve the only supported writing provider: backend Pi Runtime."""
    values = stored if stored is not None else load_writing_overrides()
    return normalize_writing_provider(
        values.get("writing_provider") or settings.writing_provider
    )


def resolve_pi_agent_url(stored: dict[str, str] | None = None) -> str:
    values = stored if stored is not None else load_writing_overrides()
    return (values.get("pi_agent_url") or settings.pi_agent_url).strip().rstrip("/")


def resolve_pi_agent_api_key(stored: dict[str, str] | None = None) -> str:
    values = stored if stored is not None else load_writing_overrides()
    return (values.get("pi_agent_api_key") or settings.pi_agent_api_key).strip()


def resolve_pi_agent_id(stored: dict[str, str] | None = None) -> str:
    values = stored if stored is not None else load_writing_overrides()
    return (values.get("pi_agent_id") or settings.pi_agent_id).strip()


def resolve_writing_scope_secret(stored: dict[str, str] | None = None) -> str:
    values = stored if stored is not None else load_writing_overrides()
    return (values.get("writing_scope_secret") or settings.writing_scope_secret).strip()
