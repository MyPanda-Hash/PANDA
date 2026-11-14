from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import now
from app.models.system_config import SystemConfig


def get_all(db: Session) -> dict[str, str]:
    """Return all config key-value pairs."""
    rows = list(db.scalars(select(SystemConfig)))
    return {row.key: row.value for row in rows}


def get_value(db: Session, key: str, default: str = "") -> str:
    """Get a single config value, or default if not set."""
    row = db.get(SystemConfig, key)
    return row.value if row else default


def upsert(db: Session, key: str, value: str, description: str = "") -> SystemConfig:
    """Insert or update a config entry."""
    row = db.get(SystemConfig, key)
    if row:
        row.value = value
        row.description = description or row.description
        row.updated_at = now()
    else:
        row = SystemConfig(
            key=key,
            value=value,
            description=description,
            updated_at=now(),
        )
        db.add(row)
    db.flush()
    return row


def resolve(key: str, default: str = "") -> str:
    """Resolve a config value from DB, env var, or default. Safe for import-time use."""
    import os

    try:
        from app.core.database import transaction

        with transaction() as db:
            row = db.get(SystemConfig, key)
            if row is not None:
                return row.value
    except Exception:
        pass
    env_key = f"DOC_NEXUS_{key.upper()}"
    env_value = os.environ.get(env_key, "")
    return env_value if env_value else default
