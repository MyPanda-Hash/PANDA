"""Fixed command profiles used by Harness verification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class CommandNotRegisteredError(KeyError):
    """Verification referenced an unknown command profile."""


@dataclass(frozen=True, slots=True)
class CommandProfile:
    command_id: str
    argv: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.command_id):
            raise ValueError("invalid command id")
        if not self.argv or any(not item or "\x00" in item for item in self.argv):
            raise ValueError("command profile argv is invalid")
        if any(re.search(r"(?:&&|\|\||;|`|\$\(|>\s*[/~])", item) for item in self.argv):
            raise ValueError("command profile cannot contain shell syntax")


@dataclass(slots=True)
class VerificationCommandRegistry:
    profiles: dict[str, CommandProfile] = field(default_factory=dict)

    def register(self, profile: CommandProfile) -> None:
        self.profiles[profile.command_id] = profile

    def resolve(self, command_id: str) -> CommandProfile:
        profile = self.profiles.get(command_id)
        if profile is None:
            raise CommandNotRegisteredError(command_id)
        return profile
