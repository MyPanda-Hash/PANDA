from __future__ import annotations

import pytest

from app.services.harness.command_registry import (
    CommandNotRegisteredError,
    CommandProfile,
    VerificationCommandRegistry,
)


def test_registry_resolves_fixed_command_profile():
    registry = VerificationCommandRegistry(
        profiles={
            "unit": CommandProfile("unit", ("/bin/echo", "ok")),
        }
    )
    assert registry.resolve("unit").argv == ("/bin/echo", "ok")


def test_registry_rejects_unknown_command():
    with pytest.raises(CommandNotRegisteredError):
        VerificationCommandRegistry().resolve("missing")


def test_profile_rejects_shell_metacharacters():
    with pytest.raises(ValueError):
        CommandProfile("bad", ("/bin/echo", "ok && rm -rf /"))
