from __future__ import annotations

import pytest

from app.services.harness.credentials import (
    CredentialError,
    CredentialProvider,
    CredentialRegistry,
)


def _provider(credential_id: str = "minio", values: dict[str, str] | None = None):
    source = values or {
        "MINIO_ENDPOINT": "https://minio.example",
        "MINIO_ACCESS_KEY": "ak",
        "MINIO_SECRET_KEY": "sk",
    }
    return CredentialProvider(
        credential_id=credential_id,
        required_keys=("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ENDPOINT"),
        env_keys=("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_SHOW_URL"),
        source=lambda: source,
    )


def test_registry_supports_registered_group_only():
    registry = CredentialRegistry([_provider()])
    assert registry.supports("minio")
    assert not registry.supports("database")
    assert registry.unknown(("minio", "database")) == ("database",)


def test_registry_resolves_declared_env_keys():
    registry = CredentialRegistry([_provider()])
    injected = registry.resolve_all(("minio",))
    assert injected == {
        "MINIO_ENDPOINT": "https://minio.example",
        "MINIO_ACCESS_KEY": "ak",
        "MINIO_SECRET_KEY": "sk",
    }


def test_registry_rejects_missing_required_key():
    registry = CredentialRegistry(
        [
            CredentialProvider(
                credential_id="minio",
                required_keys=("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ENDPOINT"),
                env_keys=("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"),
                source=lambda: {
                    "MINIO_ENDPOINT": "https://minio.example",
                    "MINIO_ACCESS_KEY": "ak",
                },
            )
        ]
    )
    with pytest.raises(CredentialError, match="MINIO_SECRET_KEY"):
        registry.resolve("minio")


def test_registry_rejects_unknown_group():
    registry = CredentialRegistry([])
    with pytest.raises(CredentialError, match="unknown credential group"):
        registry.resolve("database")


def test_registry_describe_never_leaks_values():
    registry = CredentialRegistry([_provider()])
    description = registry.describe()
    assert description["minio"]["injected_keys"] == [
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_SHOW_URL",
    ]
    assert "ak" not in str(description)
    assert "sk" not in str(description)


def test_harness_registers_dingtalk_credentials_without_exposing_values(monkeypatch):
    from app.api import harness

    webhook = "https://oapi.dingtalk.com/robot/send?access_token=test-token"
    secret = "test-secret"
    monkeypatch.setattr(harness.settings, "dingtalk_webhook", webhook)
    monkeypatch.setattr(harness.settings, "dingtalk_secret", secret)

    registry = harness._build_credential_registry()

    assert registry.resolve("dingtalk") == {
        "DINGTALK_WEBHOOK": webhook,
        "DINGTALK_SECRET": secret,
    }
    assert secret not in str(registry.describe())


def test_native_skill_enablement_rejects_long_lived_credential_injection():
    from app.api import harness
    from app.services.harness.skill_runner import SkillManifest, SkillSecurityError

    manifest = SkillManifest.from_dict(
        {
            "skill_id": "dingtalk",
            "version": "1.0.0",
            "runtime": "python",
            "entrypoint": "scripts/main.py",
            "capabilities": {
                "filesystem": "workspace-rw",
                "network": [],
                "credentials": ["dingtalk"],
            },
        }
    )

    with pytest.raises(SkillSecurityError, match="brokered capability"):
        harness._validate_manifest_credentials(manifest)
