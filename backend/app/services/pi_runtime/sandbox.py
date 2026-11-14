"""Disposable Docker sandbox helpers for the Pi RPC runtime.

Sandbox execution builds a fixed Docker command and maps only the current
session/runtime paths into the container; user input never becomes Docker argv.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Mapping

from app.services.pi_runtime.config import (
    PI_API_KEY_ENV,
    PI_AZURE_API_KEY_ENV,
    PI_AZURE_API_VERSION_ENV,
    PI_AZURE_BASE_URL_ENV,
    PI_AZURE_DEPLOYMENT_MAP_ENV,
    PiRuntimeSettings,
)

_SANDBOX_ENV_KEYS = (
    PI_API_KEY_ENV,
    PI_AZURE_API_KEY_ENV,
    PI_AZURE_BASE_URL_ENV,
    PI_AZURE_API_VERSION_ENV,
    PI_AZURE_DEPLOYMENT_MAP_ENV,
    "NEXUS_CAPABILITY_TOKEN",
    "NEXUS_CAPABILITY_BASE_URL",
    "NEXUS_CAPABILITY_AGENT_ID",
    "NEXUS_DELIVERY_RULES",
    "NEXUS_MEMORY_SCOPE",
    "NEXUS_HISTORY_RECALL",
    "NEXUS_TOOL_LOADING",
    "HARNESS_GATEWAY_URL",
    "HARNESS_GATEWAY_TOKEN",
    "HARNESS_TASK_ID",
    "HARNESS_TENANT_ID",
    "HARNESS_USER_ID",
    "PI_TELEMETRY",
    "PI_UNATTENDED_RESTRICTED",
    "PI_STRICT_BROWSER_SOURCE",
    "PI_SKIP_VERSION_CHECK",
)
_SAFE_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./:@_-]{0,255}\Z")
_SAFE_NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class PiSandboxError(RuntimeError):
    """Sandbox configuration or lifecycle error."""


def _runtime_host_path(settings: PiRuntimeSettings, path: Path) -> Path:
    host_root = settings.sandbox_host_runtime_dir
    if host_root is None:
        raise PiSandboxError(
            "Pi sandbox requires DOC_NEXUS_PI_SANDBOX_HOST_RUNTIME_DIR"
        )
    if not host_root.is_absolute():
        raise PiSandboxError("Pi sandbox host runtime directory must be absolute")
    runtime_root = settings.root.resolve()
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(runtime_root)
    except ValueError as exc:
        raise PiSandboxError(f"Pi sandbox path escapes runtime root: {path}") from exc
    return Path(host_root) / relative


def build_sandbox_command(
    settings: PiRuntimeSettings,
    *,
    pi_command: list[str],
    config_dir: Path,
    session_dir: Path,
    skills_dir: Path,
    workspace: Path,
    container_name: str,
    environment: Mapping[str, str],
) -> list[str]:
    """Build the fixed ``docker run`` argv for one Pi execution."""
    image = settings.sandbox_image.strip()
    network = settings.sandbox_network.strip()
    if not image or not _SAFE_IMAGE.fullmatch(image):
        raise PiSandboxError("invalid or missing Pi sandbox image")
    if not network or not _SAFE_NETWORK.fullmatch(network):
        raise PiSandboxError("invalid or missing Pi sandbox network")
    if not _SAFE_CONTAINER.fullmatch(container_name):
        raise PiSandboxError("invalid Pi sandbox container name")
    if not pi_command or pi_command[0] != "pi":
        raise PiSandboxError("sandbox Pi command must start with pi")

    mounts = (
        (_runtime_host_path(settings, workspace), "/workspace", "rw"),
        (_runtime_host_path(settings, session_dir), "/session", "rw"),
        (_runtime_host_path(settings, config_dir), "/pi-config", "ro"),
        (_runtime_host_path(settings, skills_dir), "/skills", "ro"),
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--init",
        "--read-only",
        "--name",
        container_name,
        "--label",
        "com.docnexus.pi-sandbox=true",
        "--label",
        f"com.docnexus.pi-task={container_name}",
        "--network",
        network,
        "--memory",
        settings.sandbox_memory,
        "--cpus",
        settings.sandbox_cpus,
        "--pids-limit",
        str(settings.sandbox_pids_limit),
        "--ulimit",
        f"fsize={settings.sandbox_workspace_max_bytes}:{settings.sandbox_workspace_max_bytes}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,size=256m",
    ]
    for source, target, mode in mounts:
        mount = f"type=bind,src={source},dst={target}"
        if mode == "ro":
            mount += ",readonly"
        elif mode != "rw":
            raise PiSandboxError(f"unsupported sandbox mount mode: {mode}")
        command.extend(["--mount", mount])
    command.extend(
        [
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp/pi-home",
            "--env",
            "USERPROFILE=/tmp/pi-home",
            "--env",
            "PI_CODING_AGENT_DIR=/pi-config",
            "--env",
            "PI_CODING_AGENT_SESSION_DIR=/session",
            "--env",
            "PI_WORKSPACE_ROOT=/workspace",
            # 容器内的 helper 只能以挂载后的本地路径做相对化；
            # Gateway 仍从任务记录读取并校验真实宿主工作区。
            "--env",
            "HARNESS_WORKSPACE_ROOT=/workspace",
            "--env",
            "PI_SHARED_SKILLS_ROOT=/skills",
            "--env",
            "PI_EXECUTION_PROFILE=sandbox",
            "--env",
            "TZ=Asia/Shanghai",
        ]
    )
    for key in _SANDBOX_ENV_KEYS:
        if key in environment:
            # No secret value is placed in argv; Docker copies it from the
            # launcher environment into the child container.
            command.extend(["--env", key])
    command.extend([image, *pi_command])
    return command


async def remove_sandbox_container(container_name: str) -> None:
    """Best-effort cleanup for a container left by a killed docker client."""
    if not _SAFE_CONTAINER.fullmatch(container_name):
        return
    process = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "--force",
        container_name,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
