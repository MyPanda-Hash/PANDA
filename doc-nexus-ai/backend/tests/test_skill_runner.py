from __future__ import annotations

import json
import sys

import pytest

from app.services.harness.skill_runner import (
    ApprovedSkillRunner,
    SkillManifest,
    SkillNotApprovedError,
    SkillRegistry,
    SkillSecurityError,
)


def _manifest(**overrides):
    value = {
        "skill_id": "demo-skill",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "scripts/main.py",
        "capabilities": {"filesystem": "workspace-rw", "network": []},
    }
    value.update(overrides)
    return value


def test_manifest_rejects_absolute_entrypoint():
    with pytest.raises(SkillSecurityError):
        SkillManifest.from_dict(_manifest(entrypoint="/tmp/main.py"))


def test_manifest_rejects_path_traversal():
    with pytest.raises(SkillSecurityError):
        SkillManifest.from_dict(_manifest(entrypoint="../main.py"))


def test_registry_approval_records_script_hash(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")

    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    assert record.sha256
    assert record.status == "approved"
    assert registry.get("tenant-a", "agent-a", "demo-skill", "1.0.0") == record


def test_runner_executes_only_approved_fixed_entrypoint(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, sys\nprint(json.load(sys.stdin)['value'])\n",
        encoding="utf-8",
    )
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(registry).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={"value": "hello"},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_runner_maps_argv_to_args_for_adapted_cli_entrypoint(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    entrypoint = skill / "demo-skill_entrypoint.py"
    skill.mkdir(parents=True)
    entrypoint.write_text(
        "import json, sys\nprint(json.dumps(json.load(sys.stdin)['args']))\n",
        encoding="utf-8",
    )
    manifest = SkillManifest.from_dict(
        _manifest(entrypoint="demo-skill_entrypoint.py")
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(registry).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={"argv": ["query", "北京"]},
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == ["query", "北京"]


def test_runner_rejects_hash_changes(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)
    script.write_text("print('changed')\n", encoding="utf-8")

    with pytest.raises(SkillSecurityError, match="hash"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_rejects_approved_skill_resource_changes(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("# original\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)
    (skill / "SKILL.md").write_text("# changed\n", encoding="utf-8")

    with pytest.raises(SkillSecurityError, match="artifact hash"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_rejects_symlink_added_to_approved_skill(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)
    (skill / "escape").symlink_to(tmp_path / "outside")

    with pytest.raises(SkillSecurityError, match="symlink"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )




def test_runner_rejects_revoked_approval(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)
    revoked = registry.revoke("tenant-a", "agent-a", "demo-skill", "1.0.0")

    assert revoked.status == "revoked"
    with pytest.raises(SkillNotApprovedError, match="not approved"):
        ApprovedSkillRunner(registry).run(
            revoked,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_manifest_rejects_unsupported_credential():
    with pytest.raises(SkillSecurityError):
        SkillManifest.from_dict(
            _manifest(capabilities={"filesystem": "workspace-ro", "credentials": [1]})
        )


def _minio_registry():
    from app.services.harness.credentials import CredentialProvider, CredentialRegistry

    return CredentialRegistry(
        [
            CredentialProvider(
                credential_id="minio",
                required_keys=("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ENDPOINT"),
                env_keys=("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_SHOW_URL"),
                source=lambda: {
                    "MINIO_ENDPOINT": "https://minio.example",
                    "MINIO_ACCESS_KEY": "ak",
                    "MINIO_SECRET_KEY": "sk",
                    "MINIO_SHOW_URL": "https://minio.example",
                },
            )
        ]
    )


def test_runner_injects_declared_minio_credentials(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({k: os.environ.get(k, '') for k in "
        "('MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'MINIO_ENDPOINT')}))\n",
        encoding="utf-8",
    )
    manifest = SkillManifest.from_dict(
        _manifest(capabilities={"filesystem": "workspace-ro", "credentials": ["minio"]})
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(registry, credential_registry=_minio_registry()).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={},
    )

    assert result.returncode == 0
    injected = json.loads(result.stdout)
    assert injected == {
        "MINIO_ACCESS_KEY": "ak",
        "MINIO_SECRET_KEY": "sk",
        "MINIO_ENDPOINT": "https://minio.example",
    }


def test_runner_rejects_minio_skill_without_configured_credentials(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(
        _manifest(capabilities={"filesystem": "workspace-ro", "credentials": ["minio"]})
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    with pytest.raises(SkillSecurityError, match="credential"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_rejects_unknown_credential_group(tmp_path):
    from app.services.harness.credentials import (
        CredentialProvider,
        CredentialRegistry,
    )

    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(
        _manifest(capabilities={"filesystem": "workspace-ro", "credentials": ["database"]})
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)
    runner = ApprovedSkillRunner(
        registry,
        credential_registry=CredentialRegistry(
            [
                CredentialProvider(
                    credential_id="minio",
                    required_keys=("MINIO_ACCESS_KEY",),
                    env_keys=("MINIO_ACCESS_KEY",),
                    source=lambda: {"MINIO_ACCESS_KEY": "ak"},
                )
            ]
        ),
    )

    with pytest.raises(SkillSecurityError, match="unknown credential"):
        runner.run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_does_not_inject_credentials_without_declaration(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import os, sys\nprint('MINIO_ACCESS_KEY' in os.environ)\n",
        encoding="utf-8",
    )
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(registry, credential_registry=_minio_registry()).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={},
    )

    assert result.stdout.strip() == "False"


def test_runner_rejects_container_network_skill_in_local_mode(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(
        _manifest(
            capabilities={
                "filesystem": "workspace-ro",
                "network": ["api.example.com"],
                "network_execution": "container",
            }
        )
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    with pytest.raises(SkillSecurityError, match="isolated container"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_allows_broker_client_network_skill_locally(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("import os, sys\nprint(os.environ.get('HARNESS_TASK_ID', ''))\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(
        _manifest(
            capabilities={
                "filesystem": "workspace-ro",
                "network": ["www.nmc.cn"],
                "network_execution": "broker-client",
            }
        )
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(
        registry,
        broker_url="http://127.0.0.1:8000",
        broker_token="tok",
        broker_task_id="task-1",
        broker_tenant_id="_system",
        broker_user_id="doc-nexus-pi",
    ).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "task-1"



def test_broker_client_uses_fixed_gateway_with_container_profile(tmp_path):
    """A broker-client Skill stays on the Harness gateway path."""
    from app.services.harness.skill_runtime_config import ContainerSkillProfile

    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, os\n"
        "print(json.dumps({k: os.environ.get(k, '') for k in "
        "('HARNESS_GATEWAY_URL', 'HARNESS_NETWORK_BROKER_URL')}))\n",
        encoding="utf-8",
    )
    manifest = SkillManifest.from_dict(
        _manifest(
            capabilities={
                "filesystem": "workspace-ro",
                "network": ["harness-gateway"],
                "network_execution": "broker-client",
            }
        )
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    result = ApprovedSkillRunner(
        registry,
        container_profile=ContainerSkillProfile(image="example/skill-runtime:1"),
        broker_url="http://127.0.0.1:8000",
        broker_token="tok",
        broker_task_id="task-1",
        broker_tenant_id="_system",
        broker_user_id="doc-nexus-pi",
    ).run(
        record,
        tenant_id="tenant-a",
        agent_id="agent-a",
        skills_root=skills_root,
        workspace=tmp_path / "workspace",
        input_data={},
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "HARNESS_GATEWAY_URL": "http://127.0.0.1:8000",
        "HARNESS_NETWORK_BROKER_URL": "http://127.0.0.1:8000",
    }

def test_runner_rejects_broker_client_without_broker(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(
        _manifest(
            capabilities={
                "filesystem": "workspace-ro",
                "network": ["www.nmc.cn"],
                "network_execution": "broker-client",
            }
        )
    )
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    with pytest.raises(SkillSecurityError, match="network broker"):
        ApprovedSkillRunner(registry).run(
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
        )


def test_runner_does_not_accept_arbitrary_argv(tmp_path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo-skill"
    script = skill / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    manifest = SkillManifest.from_dict(_manifest())
    registry = SkillRegistry(tmp_path / "registry")
    record = registry.approve("tenant-a", "agent-a", skills_root, "demo-skill", manifest)

    with pytest.raises(TypeError):
        ApprovedSkillRunner(registry).run(  # type: ignore[call-arg]
            record,
            tenant_id="tenant-a",
            agent_id="agent-a",
            skills_root=skills_root,
            workspace=tmp_path / "workspace",
            input_data={},
            argv=["--unsafe"],
        )
