from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

hasher = PasswordHasher()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    """Serialize datetime as UTC ISO-8601.

    Naive values from SQLite are treated as already-UTC (see as_utc), not local time.
    """
    return as_utc(value).isoformat()


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def new_api_key() -> str:
    """Generate a random API key for a newly registered user."""
    return f"dnx_{new_token(32)}"


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def create_access_token(
    user_id: str, role: str, auth_version: int = 0
) -> str:
    issued = now()
    payload = {
        "sub": user_id,
        "role": role,
        "auth_version": auth_version,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=settings.access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
