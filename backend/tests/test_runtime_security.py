from __future__ import annotations

import pytest

from app.core.config import Settings, validate_runtime_security


def test_production_rejects_known_or_short_jwt_secrets() -> None:
    missing = Settings(_env_file=None, environment="production")
    with pytest.raises(RuntimeError, match="DOC_NEXUS_JWT_SECRET"):
        validate_runtime_security(missing)

    for secret in ("development-only-change-me", "too-short"):
        configured = Settings(
            _env_file=None,
            environment="production",
            jwt_secret=secret,
        )

        with pytest.raises(RuntimeError, match="DOC_NEXUS_JWT_SECRET"):
            validate_runtime_security(configured)


def test_development_keeps_zero_configuration_startup() -> None:
    configured = Settings(
        _env_file=None,
        environment="development",
        jwt_secret="development-only-change-me",
    )

    validate_runtime_security(configured)


def test_uninitialized_production_without_bootstrap_password_fails_closed() -> None:
    configured = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="j" * 32,
    )

    with pytest.raises(RuntimeError, match="DOC_NEXUS_BOOTSTRAP_PASSWORD"):
        validate_runtime_security(configured, require_bootstrap_password=True)


def test_production_configured_bootstrap_password_must_be_strong() -> None:
    configured = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="j" * 32,
        bootstrap_password="Install-password-123",
    )
    validate_runtime_security(configured, require_bootstrap_password=True)

    for secret in ("too-short", "replace-with-a-random-install-password"):
        configured = Settings(
            _env_file=None,
            environment="production",
            jwt_secret="j" * 32,
            bootstrap_password=secret,
        )
        with pytest.raises(RuntimeError, match="DOC_NEXUS_BOOTSTRAP_PASSWORD"):
            validate_runtime_security(configured, require_bootstrap_password=True)
