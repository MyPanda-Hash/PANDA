from __future__ import annotations

from pathlib import Path

import pytest

from app.services.pi_runtime.config import (
    PI_API_KEY_ENV,
    PI_AZURE_API_KEY_ENV,
    PI_AZURE_API_VERSION_ENV,
    PI_AZURE_BASE_URL_ENV,
    PI_AZURE_DEPLOYMENT_MAP_ENV,
    PiRuntimeSettings,
)
from app.services.pi_runtime.sandbox import PiSandboxError, build_sandbox_command


def _settings(tmp_path: Path) -> PiRuntimeSettings:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    return PiRuntimeSettings(
        executable="pi",
        root=runtime_root,
        max_stderr_chars=1024,
        default_timeout_sec=60,
        execution_mode="sandbox",
        sandbox_image="doc-nexus-ai-backend",
        sandbox_network="doc-nexus-ai_local",
        sandbox_host_runtime_dir=Path("/Users/test/doc-nexus/pi-runtime"),
    )


def test_build_sandbox_command_maps_runtime_and_hides_secret_values(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = settings.root / "workspaces" / "session-1"
    session_dir = settings.root / "sessions" / "tenant" / "agent"
    skills_dir = settings.root / "skills" / "tenant" / "agent"
    config_dir = settings.root / "config" / "run-1"
    for path in (workspace, session_dir, skills_dir, config_dir):
        path.mkdir(parents=True)

    command = build_sandbox_command(
        settings,
        pi_command=["pi", "--mode", "rpc", "--session-dir", "/session"],
        config_dir=config_dir,
        session_dir=session_dir,
        skills_dir=skills_dir,
        workspace=workspace,
        container_name="doc-nexus-pi-session-1",
        environment={
            PI_API_KEY_ENV: "secret-value",
            "HARNESS_GATEWAY_URL": "http://backend:8000",
            "HARNESS_WORKSPACE_ROOT": "/host/runtime/workspaces/session-1",
            "NEXUS_HISTORY_RECALL": "1",
            "NEXUS_DELIVERY_RULES": '{"max_size": 10485760}',
        },
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert "--interactive" in command
    assert "--read-only" in command
    harness_root_entries = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--env"
        and index + 1 < len(command)
        and command[index + 1].startswith("HARNESS_WORKSPACE_ROOT")
    ]
    assert harness_root_entries == ["HARNESS_WORKSPACE_ROOT=/workspace"]
    assert "NEXUS_HISTORY_RECALL" in command
    assert "NEXUS_HISTORY_RECALL=1" not in command
    assert "NEXUS_DELIVERY_RULES" in command
    assert "doc-nexus-ai-backend" in command
    assert "--network" in command
    assert "doc-nexus-ai_local" in command
    assert PI_API_KEY_ENV in command
    assert "secret-value" not in command
    assert "PI_CODING_AGENT_DIR=/pi-config" in command
    assert "TZ=Asia/Shanghai" in command
    assert "--offline" not in command
    assert command[command.index("--ulimit") + 1] == (
        f"fsize={settings.sandbox_workspace_max_bytes}:"
        f"{settings.sandbox_workspace_max_bytes}"
    )
    assert any("dst=/workspace" in item and not item.endswith(",readonly") for item in command)
    assert any("dst=/session" in item and not item.endswith(",readonly") for item in command)
def test_build_sandbox_command_forwards_azure_env_names(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = settings.root / "workspaces" / "session-1"
    session_dir = settings.root / "sessions" / "tenant" / "agent"
    skills_dir = settings.root / "skills" / "tenant" / "agent"
    config_dir = settings.root / "config" / "run-1"
    for path in (workspace, session_dir, skills_dir, config_dir):
        path.mkdir(parents=True)

    command = build_sandbox_command(
        settings,
        pi_command=["pi", "--mode", "rpc", "--session-dir", "/session"],
        config_dir=config_dir,
        session_dir=session_dir,
        skills_dir=skills_dir,
        workspace=workspace,
        container_name="doc-nexus-pi-session-1",
        environment={
            PI_API_KEY_ENV: "qwen-secret",
            PI_AZURE_API_KEY_ENV: "azure-secret",
            PI_AZURE_BASE_URL_ENV: "https://example.openai.azure.com/openai",
            PI_AZURE_API_VERSION_ENV: "v1",
            PI_AZURE_DEPLOYMENT_MAP_ENV: "gpt-5.6-luna=deployment-name",
        },
    )

    assert PI_AZURE_API_KEY_ENV in command
    assert PI_AZURE_BASE_URL_ENV in command
    assert PI_AZURE_API_VERSION_ENV in command
    assert PI_AZURE_DEPLOYMENT_MAP_ENV in command
    # The old PI_AZURE_* names must never be forwarded: Pi reads
    # AZURE_OPENAI_API_KEY and the runner writes AZURE_OPENAI_* env vars.
    assert "PI_AZURE_API_KEY" not in command
    # No secret value may be placed into argv.
    assert "azure-secret" not in command
    assert "qwen-secret" not in command


def test_build_sandbox_command_requires_host_runtime_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings = PiRuntimeSettings(
        executable=settings.executable,
        root=settings.root,
        max_stderr_chars=settings.max_stderr_chars,
        default_timeout_sec=settings.default_timeout_sec,
        execution_mode="sandbox",
        sandbox_image=settings.sandbox_image,
        sandbox_network=settings.sandbox_network,
    )
    workspace = settings.root / "workspace"
    workspace.mkdir()

    with pytest.raises(PiSandboxError, match="HOST_RUNTIME_DIR"):
        build_sandbox_command(
            settings,
            pi_command=["pi"],
            config_dir=workspace,
            session_dir=workspace,
            skills_dir=workspace,
            workspace=workspace,
            container_name="doc-nexus-pi-session-1",
            environment={},
        )


