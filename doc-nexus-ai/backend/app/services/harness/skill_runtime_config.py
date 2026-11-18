"""Fixed container profile for production Skill execution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class SkillRuntimeConfigError(ValueError):
    """Container or dependency configuration is unsafe."""


@dataclass(frozen=True, slots=True)
class ContainerSkillProfile:
    image: str
    network_mode: str = "none"
    memory: str = "512m"
    cpus: str = "1"
    pids_limit: int = 128
    dependency_lock_sha256: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:@_-]{0,255}", self.image):
            raise SkillRuntimeConfigError("invalid Skill container image")
        if self.network_mode in {"host", "bridge", "default"} or (
            self.network_mode not in {"none", "broker"}
            and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", self.network_mode
            )
        ):
            raise SkillRuntimeConfigError("unsupported Skill network mode")
        if self.pids_limit < 16 or self.pids_limit > 4096:
            raise SkillRuntimeConfigError("invalid Skill process limit")
        if self.dependency_lock_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.dependency_lock_sha256):
            raise SkillRuntimeConfigError("invalid dependency lock hash")


def build_container_argv(
    profile: ContainerSkillProfile,
    *,
    skill_root: Path,
    workspace: Path,
    entrypoint: str,
    runtime: Literal["python", "node", "bash"],
    filesystem: Literal["workspace-ro", "workspace-rw"] = "workspace-rw",
    environment: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
    host_runtime_root: Path | None = None,
) -> tuple[str, ...]:
    if not skill_root.is_absolute() or not workspace.is_absolute():
        raise SkillRuntimeConfigError("container mounts must be absolute")
    if filesystem not in {"workspace-ro", "workspace-rw"}:
        raise SkillRuntimeConfigError("unsupported Skill filesystem capability")
    workspace_mount = ",readonly" if filesystem == "workspace-ro" else ""
    normalized = entrypoint.replace("\\", "/")
    if profile.network_mode == "broker":
        raise SkillRuntimeConfigError("broker network requires a configured network sidecar")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise SkillRuntimeConfigError("entrypoint escapes the Skill mount")
    runtime_binary = {"python": "python3", "node": "node", "bash": "/bin/bash"}[runtime]
    network = "none" if profile.network_mode in {"none", "broker"} else profile.network_mode
    container_environment: list[str] = []
    for name, value in sorted((environment or {}).items()):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or "\x00" in value:
            raise SkillRuntimeConfigError("invalid Skill environment")
        if name == "TZ":
            continue
        container_environment.extend(("--env", f"{name}={value}"))
    container_environment.extend(("--env", "TZ=Asia/Shanghai"))
    skill_source = _host_mount_source(
        skill_root,
        runtime_root=runtime_root,
        host_runtime_root=host_runtime_root,
    )
    workspace_source = _host_mount_source(
        workspace,
        runtime_root=runtime_root,
        host_runtime_root=host_runtime_root,
    )
    return (
        "docker",
        "run",
        "--rm",
        "--init",
        *container_environment,
        "-i",
        f"--network={network}",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={profile.pids_limit}",
        f"--memory={profile.memory}",
        f"--cpus={profile.cpus}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={skill_source},dst=/skills,readonly",
        "--mount",
        f"type=bind,src={workspace_source},dst=/workspace{workspace_mount}",
        "--workdir",
        "/workspace",
        profile.image,
        runtime_binary,
        f"/skills/{normalized}",
    )


def _host_mount_source(
    path: Path,
    *,
    runtime_root: Path | None,
    host_runtime_root: Path | None,
) -> Path:
    resolved = path.resolve()
    if runtime_root is None and host_runtime_root is None:
        return resolved
    if runtime_root is None or host_runtime_root is None:
        raise SkillRuntimeConfigError(
            "runtime_root and host_runtime_root must be configured together"
        )
    root = runtime_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SkillRuntimeConfigError(
            "container mount path escapes the runtime root"
        ) from exc
    # The backend process may run inside a container while Docker Desktop
    # resolves this source on the Mac host.  It is therefore intentionally not
    # checked with Path.exists() here; Docker is the authority for host mount
    # existence when it creates the nested Skill container.
    return host_runtime_root.resolve() / relative


def verify_dependency_lock(path: Path, expected_sha256: str) -> None:
    if not path.is_file() or not expected_sha256:
        raise SkillRuntimeConfigError("dependency lockfile is required")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise SkillRuntimeConfigError("dependency lock hash changed")
