"""Pi skill discovery and safe workspace mounting."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_SHARED_SKILL_TENANT = "_system"


class SkillOperationError(RuntimeError):
    """Skill filesystem operation failed."""


class SkillBusyError(SkillOperationError):
    """Another operation owns the same agent skill lock."""


class SkillNotFoundError(SkillOperationError):
    """Requested skill does not exist."""


class SkillPathError(SkillOperationError):
    """Skill path is invalid or unsafe."""


def _safe(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if raw not in {"", ".", ".."} and len(raw) <= 128 and re.fullmatch(
        r"[A-Za-z0-9._-]+", raw
    ):
        return raw
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    prefix = re.sub(r"[^A-Za-z0-9._-]", "-", raw).strip(".-")[:48]
    return f"{prefix or fallback}-{digest}"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _get_root(settings: Any) -> Path:
    """Return the Pi runtime root from either settings shape."""
    configured = getattr(settings, "pi_root", None)
    if configured:
        return Path(configured)
    return Path(settings.root)


def skills_dir_for_agent(agent: object, settings: Any) -> Path:
    return (
        _get_root(settings)
        / "skills"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
    )


def workspace_dir_for_agent(settings: Any, agent: object, session_id: str) -> Path:
    return (
        _get_root(settings)
        / "workspaces"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
        / _safe(session_id, "session")
    )


def agent_workspace_root(settings: Any, agent: object) -> Path:
    return (
        _get_root(settings)
        / "workspaces"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
    )


def published_skills_dir(agent: object, session_id: str, settings: Any) -> Path:
    return (
        _get_root(settings)
        / "session-skills"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
        / _safe(session_id, "session")
        / "skills"
    )


def _verified_enabled_skills(settings: Any) -> list[tuple[Any, Path, Path]]:
    """Return enabled shared Skills whose complete approved tree is intact."""
    from app.services.harness.skill_installation import InstallState, SkillInstaller
    from app.services.harness.skill_definition import (
        SkillDefinitionError,
        load_skill_definition,
    )
    from app.services.harness.skill_integrity import SkillIntegrityError, hash_skill_tree

    runtime_root = _get_root(settings).resolve()
    try:
        records = SkillInstaller(
            runtime_root / "skill-installations" / _SHARED_SKILL_TENANT
        ).list()
    except (OSError, ValueError, RuntimeError):
        return []

    verified: list[tuple[Any, Path, Path]] = []
    seen_ids: set[str] = set()
    for record in records:
        skill_id = str(record.skill_id or "")
        if (
            record.state != InstallState.ENABLED
            or not skill_id
            or skill_id in seen_ids
            or not record.version
            or not record.approved_sha256
            or not record.installed_path
        ):
            continue
        raw_path = Path(record.installed_path)
        if raw_path.is_symlink():
            continue
        try:
            installed = raw_path.resolve(strict=True)
            installed.relative_to(runtime_root)
            digest = hash_skill_tree(installed)
        except (OSError, ValueError, SkillIntegrityError):
            continue
        if digest != record.approved_sha256:
            continue
        markers = [
            marker
            for marker in installed.rglob("SKILL.md")
            if marker.is_file() and not marker.is_symlink()
        ]
        if len(markers) != 1:
            continue
        try:
            definition = load_skill_definition(markers[0])
        except SkillDefinitionError:
            continue
        if definition.name != skill_id:
            continue
        seen_ids.add(skill_id)
        verified.append((record, installed, markers[0]))
    return verified


def list_agent_skills(agent: object, settings: Any) -> list[dict[str, Any]]:
    """List only shared Skills eligible for a new Pi session publication."""
    _ = agent
    result: list[dict[str, Any]] = []
    for record, installed, marker in _verified_enabled_skills(settings):
        from app.services.harness.skill_definition import load_skill_definition

        definition = load_skill_definition(marker)
        marker_relative = marker.relative_to(installed).as_posix()
        result.append(
            {
                "name": definition.name,
                "description": definition.description,
                "relative_path": record.skill_id,
                "path": f"/skills/{record.skill_id}/{marker_relative}",
                "label": definition.metadata.get("display_name")
                or definition.metadata.get("label"),
                "group": definition.metadata.get("group")
                or definition.metadata.get("group_label"),
                "source": "approved-installation",
                "mounted": True,
                "approved": True,
                "approval_type": "native",
                "skill_id": record.skill_id,
                "version": record.version,
                "approved_sha256": record.approved_sha256,
            }
        )
    return sorted(result, key=lambda item: str(item["skill_id"]))


def _validate_relative(relative_path: str) -> str:
    raw = (relative_path or "").replace("\\", "/").strip("/")
    if (
        not raw
        or raw in {".", ".."}
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise SkillPathError("skill path is invalid")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", raw):
        raise SkillPathError("skill path contains unsupported characters")
    return raw


def _validated_skill_target(root: Path, relative_path: str) -> Path | None:
    target = root.joinpath(*relative_path.split("/"))
    if not _is_within(target, root):
        raise SkillPathError("skill path escapes root")
    current = target
    while current != root:
        if current.is_symlink():
            raise SkillPathError("skill path cannot contain symlinks")
        current = current.parent
    marker = target / "SKILL.md"
    if not target.exists():
        return None
    if (
        not target.is_dir()
        or target.is_symlink()
        or marker.is_symlink()
        or not marker.is_file()
    ):
        raise SkillPathError("target is not a valid skill directory")
    return target


@contextlib.contextmanager
def _agent_skills_lock(root: Path, *, blocking: bool) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(
                lock.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            )
        except BlockingIOError as exc:
            raise SkillBusyError("skill operation is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def prepare_agent_skills(
    agent: object,
    workspace: Path,
    settings: Any,
    *,
    include_enabled: bool = True,
    required_skill: tuple[str, str, str] | None = None,
) -> Path:
    runtime_root = _get_root(settings).resolve()
    expected_workspace_root = agent_workspace_root(settings, agent).resolve()
    workspace = workspace.resolve()
    if workspace.parent != expected_workspace_root:
        raise SkillPathError("workspace does not belong to the Pi agent")
    workspace.mkdir(parents=True, exist_ok=True)
    target = published_skills_dir(agent, workspace.name, settings)
    publication_root = target.parent
    try:
        publication_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillPathError("workspace Skill publication root is invalid") from exc
    if (
        publication_root.is_symlink()
        or not publication_root.is_dir()
        or not _is_within(publication_root, runtime_root / "session-skills")
    ):
        raise SkillPathError("workspace Skill publication root is invalid")
    lock_root = (
        runtime_root
        / "locks"
        / "skill-publication"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
    )
    suffix = uuid.uuid4().hex
    staged = target.parent / f".skills-staged-{suffix}"
    previous = target.parent / f".skills-previous-{suffix}"

    with _agent_skills_lock(lock_root, blocking=True):
        staged.mkdir()
        try:
            from app.services.harness.skill_integrity import (
                SkillIntegrityError,
                hash_skill_tree,
            )

            enabled_skills = (
                _verified_enabled_skills(settings) if include_enabled else []
            )
            if required_skill is not None:
                required_id, required_version, required_digest = required_skill
                enabled_skills = [
                    item
                    for item in enabled_skills
                    if item[0].skill_id == required_id
                    and item[0].version == required_version
                    and item[0].approved_sha256 == required_digest
                ]
                if len(enabled_skills) != 1:
                    raise SkillNotFoundError(
                        "required approved Skill version is not enabled or intact"
                    )
            for record, installed, _ in enabled_skills:
                destination = staged / record.skill_id
                try:
                    shutil.copytree(installed, destination, symlinks=True)
                    if hash_skill_tree(destination) != record.approved_sha256:
                        raise SkillIntegrityError("published Skill digest changed")
                except (OSError, SkillIntegrityError) as exc:
                    if destination.exists() or destination.is_symlink():
                        shutil.rmtree(destination, ignore_errors=True)
                    if required_skill is not None:
                        raise SkillNotFoundError(
                            "required approved Skill snapshot could not be published"
                        ) from exc

            if target.exists() or target.is_symlink():
                target.replace(previous)
            try:
                staged.replace(target)
            except Exception:
                if previous.exists() or previous.is_symlink():
                    previous.replace(target)
                raise
            if previous.is_symlink() or previous.is_file():
                previous.unlink()
            elif previous.is_dir():
                shutil.rmtree(previous)
            elif previous.exists():
                previous.unlink()
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
    return target.resolve()


def delete_agent_skill(
    agent: object, relative_path: str, settings: Any
) -> dict[str, str]:
    normalized = _validate_relative(relative_path)
    canonical = skills_dir_for_agent(agent, settings)
    target = _validated_skill_target(canonical, normalized)
    if target is None:
        raise SkillNotFoundError("skill not found")
    recycle = (
        _get_root(settings)
        / "recycle"
        / _safe(str(getattr(agent, "tenant_id", "")), "tenant")
        / _safe(str(getattr(agent, "id", "")), "agent")
        / f"{int(time.time())}-{normalized}"
    )
    with _agent_skills_lock(canonical, blocking=False):
        recycle.parent.mkdir(parents=True, exist_ok=True)
        target.replace(recycle)
    return {"id": normalized, "relative_path": normalized}
