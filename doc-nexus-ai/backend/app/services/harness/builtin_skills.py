"""Idempotently admit trusted Skills shipped with the platform."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from app.services.harness.skill_installation import (
    InstallState,
    SkillInstaller,
    SkillInstallError,
    prepare_install_for_review,
)
from app.services.harness.skill_integrity import SkillIntegrityError, hash_skill_tree
from app.services.pi_runtime.config import PiRuntimeSettings

logger = logging.getLogger(__name__)

SYSTEM_SKILL_TENANT = "_system"
DEFAULT_BUILTIN_SKILLS = (
    "writing-bid",
    "writing-proposal",
    "writing-quote",
    "writing-report",
    "find-skills",
    "the-news",
    "minio-upload",
    "dashi-ppt",
    "pptx",
    "open-meteo-api",
)

# Browser automation is admitted explicitly after its execution image is ready,
# not by the default startup bootstrap.
_SUPPORTED_BUILTIN_SKILLS = (*DEFAULT_BUILTIN_SKILLS, "agent-browser")
_PACKAGED_BUILTIN_SKILLS = frozenset(_SUPPORTED_BUILTIN_SKILLS)
# 完整 Dashi 随包资源约 56 MiB；较大额度只用于固定白名单的可信 builtin。
_BUILTIN_SKILL_MAX_BYTES = 64 * 1024 * 1024


def parse_builtin_skill_allowlist(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    if raw is None:
        values: Iterable[str] = DEFAULT_BUILTIN_SKILLS
    elif isinstance(raw, str):
        values = raw.split(",")
    else:
        values = raw
    allowed = {
        item.strip().lower()
        for item in values
        if item and item.strip().lower() in _SUPPORTED_BUILTIN_SKILLS
    }
    return tuple(skill_id for skill_id in _SUPPORTED_BUILTIN_SKILLS if skill_id in allowed)


def _source_root(runtime_root: Path) -> Path:
    return runtime_root / "skills" / SYSTEM_SKILL_TENANT / "doc-nexus-pi"


def _packaged_source_root() -> Path:
    return Path(__file__).resolve().parents[2] / "builtin_skills"


def _source_for(runtime_root: Path, skill_id: str) -> Path:
    if skill_id in _PACKAGED_BUILTIN_SKILLS:
        packaged = _packaged_source_root() / skill_id
        if packaged.is_dir():
            return packaged
    return _source_root(runtime_root) / skill_id


def _enabled_record(installer: SkillInstaller, skill_id: str):
    matches = [
        record
        for record in installer.list()
        if record.skill_id == skill_id and record.state == InstallState.ENABLED
    ]
    return matches[-1] if matches else None


def _enabled_record_is_intact(record) -> bool:
    if record.state != InstallState.ENABLED or not record.installed_path:
        return False
    installed = Path(record.installed_path)
    if not installed.is_dir() or not record.approved_sha256:
        return False
    try:
        return hash_skill_tree(installed) == record.approved_sha256
    except (OSError, SkillIntegrityError):
        return False


def _next_builtin_version(
    installer: SkillInstaller,
    skill_id: str,
    source_digest: str,
) -> str:
    base = f"1.0.{source_digest[:12]}"
    versions = {
        record.version
        for record in installer.list()
        if record.skill_id == skill_id
    }
    if base not in versions:
        return base
    counter = 2
    while f"{base}.{counter}" in versions:
        counter += 1
    return f"{base}.{counter}"


def _prepare_installation(
    installer: SkillInstaller,
    source: Path,
    skill_id: str,
    source_digest: str,
    runtime_root: Path,
):
    record = installer.request(
        SYSTEM_SKILL_TENANT,
        source,
        skill_id=skill_id,
        version=_next_builtin_version(installer, skill_id, source_digest),
    )
    record = prepare_install_for_review(installer, record.request_id)
    record = installer.approve(record.request_id)
    record = installer.install(record.request_id)
    return installer.mount(record.request_id, runtime_root)


def ensure_builtin_skills(
    runtime_root: Path,
    allowlist: str | Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Enable shipped Skills through the normal quarantine/approval lifecycle.

    Existing enabled records are left untouched when their approved tree is intact.
    The operation is therefore safe to run at every application startup and after
    every deployment. Only IDs in the fixed built-in allowlist are eligible.
    """
    runtime_root = Path(runtime_root).resolve()
    installer = SkillInstaller(
        runtime_root / "skill-installations" / SYSTEM_SKILL_TENANT,
        max_bytes=_BUILTIN_SKILL_MAX_BYTES,
    )
    result = {"enabled": [], "skipped": [], "missing": [], "failed": []}

    for skill_id in parse_builtin_skill_allowlist(allowlist):
        source = _source_for(runtime_root, skill_id)
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            result["missing"].append(skill_id)
            continue
        try:
            source_digest = hash_skill_tree(source)
            enabled = _enabled_record(installer, skill_id)
            if (
                enabled is not None
                and _enabled_record_is_intact(enabled)
                and enabled.approved_sha256 == source_digest
            ):
                result["skipped"].append(skill_id)
                continue
            replacement = _prepare_installation(
                installer,
                source,
                skill_id,
                source_digest,
                runtime_root,
            )
            old_was_intact = enabled is not None and _enabled_record_is_intact(enabled)
            if enabled is not None:
                installer.revoke(enabled.skill_id, enabled.version)
            try:
                installer.enable(replacement.request_id)
            except Exception:
                if enabled is not None and old_was_intact:
                    installer.enable(enabled.request_id)
                raise
            result["enabled"].append(skill_id)
        except (OSError, SkillInstallError, ValueError) as exc:
            logger.exception("Built-in Skill bootstrap failed for %s", skill_id)
            result["failed"].append(f"{skill_id}: {exc}")
    return result


def ensure_configured_builtin_skills(
    allowlist: str | Iterable[str] | None = None,
) -> dict[str, list[str]]:
    return ensure_builtin_skills(PiRuntimeSettings.from_env().root, allowlist)
