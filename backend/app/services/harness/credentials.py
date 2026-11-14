"""Structured credential capabilities for approved Skills.

A Skill manifest declares the credential groups it needs (``credentials``
under ``capabilities``). The declaration is a pure request: whether a group
is known, where its values come from, and which environment keys are injected
are decided entirely by the server-side ``CredentialRegistry``. No Skill code
and no runner branch is allowed to special-case a concrete credential.

Adding a new credential group means registering a provider here — nothing in
the runner, manifest parsing, or API changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class CredentialError(ValueError):
    """A declared credential capability cannot be resolved."""


@dataclass(frozen=True, slots=True)
class CredentialProvider:
    """Server-side definition of one credential group.

    ``credential_id`` is the name a Skill manifest references.
    ``required_keys`` are the environment keys that must resolve to a
    non-empty value before a Skill execution may proceed.
    ``env_keys`` are the full set of environment keys injected into the
    Skill subprocess; only keys the source actually yields are injected.
    ``source`` reads the credential values from configuration (settings,
    secret store, env) — never from Skill input or files.
    """

    credential_id: str
    required_keys: tuple[str, ...]
    env_keys: tuple[str, ...]
    source: Callable[[], dict[str, str]]

    def resolve(self) -> dict[str, str]:
        values = {key: (self.source().get(key) or "").strip() for key in self.env_keys}
        missing = [key for key in self.required_keys if not values.get(key)]
        if missing:
            raise CredentialError(
                f"credential {self.credential_id} is not configured "
                f"(missing: {', '.join(missing)})"
            )
        return {key: value for key, value in values.items() if value}


class CredentialRegistry:
    """Resolves declared credential groups to injected environment values."""

    def __init__(self, providers: list[CredentialProvider] | None = None) -> None:
        self._providers = {
            provider.credential_id: provider for provider in (providers or [])
        }

    def supports(self, credential_id: str) -> bool:
        return credential_id in self._providers

    def unknown(self, credential_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            credential_id
            for credential_id in credential_ids
            if credential_id not in self._providers
        )

    def resolve(self, credential_id: str) -> dict[str, str]:
        provider = self._providers.get(credential_id)
        if provider is None:
            raise CredentialError(f"unknown credential group: {credential_id}")
        return provider.resolve()

    def resolve_all(self, credential_ids: tuple[str, ...]) -> dict[str, str]:
        injected: dict[str, str] = {}
        for credential_id in credential_ids:
            injected.update(self.resolve(credential_id))
        return injected

    def describe(self) -> dict[str, dict[str, object]]:
        """Credential metadata for admin surfaces (names only, never values)."""
        return {
            credential_id: {
                "injected_keys": list(provider.env_keys),
                "required_keys": list(provider.required_keys),
            }
            for credential_id, provider in sorted(self._providers.items())
        }

    def status(self) -> dict[str, dict[str, object]]:
        """Configuration status per group: whether it resolves and which
        required keys are missing. Never exposes values."""
        result: dict[str, dict[str, object]] = {}
        for credential_id, provider in sorted(self._providers.items()):
            values = {
                key: (provider.source().get(key) or "").strip()
                for key in provider.env_keys
            }
            missing = [key for key in provider.required_keys if not values.get(key)]
            result[credential_id] = {
                "configured": not missing,
                "missing_keys": missing,
                "injected_keys": list(provider.env_keys),
            }
        return result
