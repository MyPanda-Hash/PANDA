from __future__ import annotations

import pytest

from app.services.harness.skill_runtime_config import (
    ContainerSkillProfile,
    SkillRuntimeConfigError,
    build_container_argv,
)


def test_container_argv_uses_fixed_mounts_and_no_network(tmp_path):
    argv = build_container_argv(
        ContainerSkillProfile(image="registry.example/skill:1.0", network_mode="none"),
        skill_root=tmp_path / "skills",
        workspace=tmp_path / "workspace",
        entrypoint="scripts/main.py",
        runtime="python",
    )
    assert "--network=none" in argv
    assert "-i" in argv
    assert "--cap-drop=ALL" in argv
    assert any(str(tmp_path / "skills") in item for item in argv)
    assert argv[-2:] == ("python3", "/skills/scripts/main.py")


def test_container_argv_can_make_workspace_read_only(tmp_path):
    argv = build_container_argv(
        ContainerSkillProfile(image="skill:1"),
        skill_root=tmp_path / "skills",
        workspace=tmp_path / "workspace",
        entrypoint="main.py",
        runtime="python",
        filesystem="workspace-ro",
    )
    assert any("dst=/workspace,readonly" in item for item in argv)


def test_container_argv_passes_fixed_environment(tmp_path):
    argv = build_container_argv(
        ContainerSkillProfile(image="skill:1"),
        skill_root=tmp_path / "skills",
        workspace=tmp_path / "workspace",
        entrypoint="main.py",
        runtime="python",
        environment={"HOME": "/tmp/skill-home", "SKILL_ID": "demo", "TZ": "UTC"},
    )

    assert "HOME=/tmp/skill-home" in argv
    assert "TZ=Asia/Shanghai" in argv
    assert "TZ=UTC" not in argv
    assert "SKILL_ID=demo" in argv


def test_container_argv_uses_configured_isolated_network(tmp_path):
    argv = build_container_argv(
        ContainerSkillProfile(
            image="registry.example/skill:1.0",
            network_mode="doc-nexus-ai_skill-egress",
        ),
        skill_root=tmp_path / "skills",
        workspace=tmp_path / "workspace",
        entrypoint="main.py",
        runtime="python",
    )

    assert "--network=doc-nexus-ai_skill-egress" in argv


def test_container_argv_maps_runtime_mounts_to_docker_host(tmp_path):
    runtime_root = tmp_path / "runtime"
    host_root = tmp_path / "host-runtime"
    skills_root = runtime_root / "skills" / "agent"
    workspace = runtime_root / "workspaces" / "task"
    skills_root.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (host_root / "skills" / "agent").mkdir(parents=True)
    (host_root / "workspaces" / "task").mkdir(parents=True)

    argv = build_container_argv(
        ContainerSkillProfile(image="registry.example/skill:1.0"),
        skill_root=skills_root,
        workspace=workspace,
        entrypoint="main.py",
        runtime="python",
        runtime_root=runtime_root,
        host_runtime_root=host_root,
    )

    assert any(
        f"src={host_root / 'skills' / 'agent'},dst=/skills,readonly" in item
        for item in argv
    )
    assert any(
        f"src={host_root / 'workspaces' / 'task'},dst=/workspace" in item
        for item in argv
    )


def test_container_argv_does_not_require_host_mounts_visible_to_backend(tmp_path):
    runtime_root = tmp_path / "runtime"
    host_root = tmp_path / "docker-desktop-host-runtime"
    skills_root = runtime_root / "skills" / "agent"
    workspace = runtime_root / "workspaces" / "task"
    skills_root.mkdir(parents=True)
    workspace.mkdir(parents=True)

    argv = build_container_argv(
        ContainerSkillProfile(image="registry.example/skill:1.0"),
        skill_root=skills_root,
        workspace=workspace,
        entrypoint="main.py",
        runtime="python",
        runtime_root=runtime_root,
        host_runtime_root=host_root,
    )

    assert any(f"src={host_root / 'skills' / 'agent'}" in item for item in argv)

def test_container_profile_rejects_uncontrolled_network_mode():
    with pytest.raises(SkillRuntimeConfigError):
        ContainerSkillProfile(image="skill:1", network_mode="host")
