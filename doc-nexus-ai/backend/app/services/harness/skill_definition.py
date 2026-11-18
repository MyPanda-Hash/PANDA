"""Validation shared by Skill admission and Pi publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_VALID_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class SkillDefinitionError(RuntimeError):
    """A SKILL.md cannot be loaded by the supported Pi runtime."""


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    description: str
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


def load_skill_definition(path: Path) -> SkillDefinition:
    """Parse and validate required Agent Skills frontmatter.

    These checks intentionally match Pi's name/description constraints, while
    requiring an explicit name so the approved identity cannot depend on the
    publication directory chosen later.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillDefinitionError("SKILL.md must be readable UTF-8") from exc
    match = _FRONTMATTER.match(raw)
    if match is None:
        raise SkillDefinitionError("SKILL.md requires YAML frontmatter")
    try:
        parsed: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillDefinitionError("SKILL.md frontmatter is invalid YAML") from exc
    if not isinstance(parsed, dict):
        raise SkillDefinitionError("SKILL.md frontmatter must be an object")

    name = parsed.get("name")
    description = parsed.get("description")
    if not isinstance(name, str) or not name:
        raise SkillDefinitionError("SKILL.md frontmatter name is required")
    if len(name) > 64 or _VALID_NAME.fullmatch(name) is None:
        raise SkillDefinitionError(
            "SKILL.md name must be 1-64 lowercase letters, digits, or single hyphens"
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillDefinitionError("SKILL.md frontmatter description is required")
    if len(description) > 1024:
        raise SkillDefinitionError("SKILL.md description exceeds 1024 characters")
    return SkillDefinition(name=name, description=description, metadata=parsed)
