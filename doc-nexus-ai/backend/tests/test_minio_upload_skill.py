from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SKILL_DIR = (
    Path(__file__).parents[1] / "app" / "builtin_skills" / "minio-upload"
)


def _load_skill_module():
    spec = importlib.util.spec_from_file_location(
        "minio_upload_skill",
        _SKILL_DIR / "minio_upload.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def test_minio_skill_delegates_to_fixed_harness_capability(monkeypatch):
    module = _load_skill_module()
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"file": {"path": "report.txt", "url": "/api/files/task/report.txt"}}
            ).encode()

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode())
        captured["authorization"] = request.headers["Authorization"]
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    monkeypatch.setenv("HARNESS_GATEWAY_URL", "http://harness.test")
    monkeypatch.setenv("HARNESS_GATEWAY_TOKEN", "gateway-token")
    monkeypatch.setenv("HARNESS_TASK_ID", "task-1")
    monkeypatch.setenv("HARNESS_TENANT_ID", "_system")
    monkeypatch.setenv("HARNESS_USER_ID", "doc-nexus-pi")

    result = module._upload("report.txt")

    assert result["file"]["path"] == "report.txt"
    assert captured == {
        "url": "http://harness.test/admin/harness/files/upload",
        "timeout": 30,
        "body": {
            "task_id": "task-1",
            "tenant_id": "_system",
            "user_id": "doc-nexus-pi",
            "path": "report.txt",
        },
        "authorization": "Bearer gateway-token",
    }


def test_minio_skill_normalizes_workspace_absolute_path(tmp_path, monkeypatch):
    module = _load_skill_module()
    workspace = tmp_path / "workspace"
    report = workspace / "exports" / "report.xlsx"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"xlsx")
    monkeypatch.setenv("HARNESS_WORKSPACE_ROOT", str(workspace))

    assert module._workspace_relative(str(report)) == "exports/report.xlsx"
    assert module._workspace_relative("exports/report.xlsx") == "exports/report.xlsx"


def test_minio_skill_batch_accepts_workspace_paths(
    tmp_path, monkeypatch, capsys
):
    module = _load_skill_module()
    workspace = tmp_path / "workspace"
    exports = workspace / "exports"
    exports.mkdir(parents=True)
    (exports / "a.xlsx").write_bytes(b"a")
    (exports / "b.xlsx").write_bytes(b"b")
    uploaded: list[str] = []

    def fake_upload(path: str):
        relative = module._workspace_relative(path)
        uploaded.append(relative)
        return {"file": {"path": relative}}

    monkeypatch.setattr(module, "_upload", fake_upload)
    monkeypatch.setenv("HARNESS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(sys, "argv", ["minio_upload.py", "exports", "--batch"])

    assert module.main() == 0
    assert uploaded == ["exports/a.xlsx", "exports/b.xlsx"]
    assert json.loads(capsys.readouterr().out) == {
        "files": [
            {"path": "exports/a.xlsx"},
            {"path": "exports/b.xlsx"},
        ]
    }


def test_minio_skill_rejects_workspace_escapes(tmp_path, monkeypatch):
    module = _load_skill_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.xlsx").write_bytes(b"secret")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("HARNESS_WORKSPACE_ROOT", str(workspace))

    with pytest.raises(ValueError, match="safe workspace-relative"):
        module._workspace_relative("../outside/secret.xlsx")
    with pytest.raises(ValueError, match="inside the Harness workspace"):
        module._workspace_relative(str(workspace / "escape" / "secret.xlsx"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["minio_upload.py", str(workspace / "escape"), "--batch"],
    )
    with pytest.raises(ValueError, match="inside the Harness workspace"):
        module.main()


def test_minio_skill_rejects_non_http_gateway(monkeypatch):
    module = _load_skill_module()
    called = False

    def fake_urlopen(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    monkeypatch.setenv("HARNESS_GATEWAY_URL", "file:///tmp/agent-controlled")
    monkeypatch.setenv("HARNESS_GATEWAY_TOKEN", "gateway-token")
    monkeypatch.setenv("HARNESS_TASK_ID", "task-1")
    monkeypatch.setenv("HARNESS_TENANT_ID", "_system")
    monkeypatch.setenv("HARNESS_USER_ID", "doc-nexus-pi")

    with pytest.raises(RuntimeError, match="HTTP"):
        module._upload("report.txt")
    assert called is False


def test_builtin_installer_copies_the_complete_minio_skill(tmp_path):
    root = Path(__file__).parents[2]
    target = tmp_path / "skills"
    completed = subprocess.run(
        ["bash", str(root / "scripts" / "ensure-writing-skills.sh"), str(root)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DOC_NEXUS_PI_SKILLS_DIR": str(target)},
    )

    assert completed.returncode == 0, completed.stderr
    installed = target / "minio-upload"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "minio_upload.py").is_file()
    script = (installed / "minio_upload.py").read_text(encoding="utf-8")
    assert "MINIO_SECRET" not in script
    assert "from minio import Minio" not in script


def test_builtin_installer_snapshots_only_skills_it_mutates(tmp_path):
    root = Path(__file__).parents[2]
    target = tmp_path / "skills"
    backup = tmp_path / "backup"
    managed = target / "writing-bid"
    unmanaged = target / "the-news"
    managed.mkdir(parents=True)
    unmanaged.mkdir(parents=True)
    previous_managed = (
        "---\n"
        "# doc-nexus-managed-writing-skill:v1\n"
        "---\n"
        "Previous managed content\n"
    )
    (managed / "SKILL.md").write_text(previous_managed, encoding="utf-8")
    (unmanaged / "SKILL.md").write_text("User-owned content\n", encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(root / "scripts" / "ensure-writing-skills.sh"), str(root)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DOC_NEXUS_PI_SKILLS_DIR": str(target),
            "DOC_NEXUS_MANAGED_SKILL_BACKUP_DIR": str(backup),
        },
    )

    assert completed.returncode == 0, completed.stderr
    manifest = set((backup / "manifest").read_text(encoding="utf-8").splitlines())
    assert "present writing-bid" in manifest
    assert "absent writing-proposal" in manifest
    assert not any(line.endswith(" the-news") for line in manifest)
    assert not (backup / "the-news.tgz").exists()
    assert (unmanaged / "SKILL.md").read_text(encoding="utf-8") == (
        "User-owned content\n"
    )

    restored = tmp_path / "restored"
    restored.mkdir()
    extracted = subprocess.run(
        ["tar", "-xzf", str(backup / "writing-bid.tgz"), "-C", str(restored)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert extracted.returncode == 0, extracted.stderr
    assert (restored / "writing-bid" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == previous_managed
