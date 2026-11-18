from __future__ import annotations

import tarfile
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app.services.harness.skill_installation import (
    InstallState,
    SkillInstallConflictError,
    SkillInstallError,
    SkillInstallNotFoundError,
    SkillInstallRecord,
    SkillInstaller,
    prepare_install_for_review,
    promote_install_to_enabled,
    sort_install_records_newest_first,
)
from app.services.harness.skill_integrity import SkillIntegrityError, hash_skill_tree


def _skill_md(name: str, title: str = "Demo") -> str:
    return (
        f"---\nname: {name}\ndescription: Test Skill {name}\n---\n"
        f"# {title}\n"
    )


def test_directory_skill_moves_through_approval_and_install(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    contract = _skill_md("demo-skill")
    (source / "SKILL.md").write_text(contract, encoding="utf-8")
    (source / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")

    (source / "skill.manifest.json").write_text(
        '{"skill_id":"demo-skill","version":"1.0.0","runtime":"python","entrypoint":"scripts/main.py"}',
        encoding="utf-8",
    )
    request = installer.request("tenant-a", source)
    assert request.state == InstallState.QUARANTINED
    scanned = installer.scan(request.request_id)
    assert scanned.state == InstallState.SCANNED
    assert scanned.scan_report["adaptation"]["runtime"] == "python"
    installer.adapt(request.request_id, "demo-skill", "1.0.0")
    approved = installer.approve(request.request_id)
    assert approved.state == InstallState.APPROVED
    assert approved.skill_id == "demo-skill"
    assert approved.version == "1.0.0"
    assert len(approved.approved_sha256) == 64
    installed = installer.install(request.request_id)
    assert installed.state == InstallState.INSTALLED
    mount_path = tmp_path / "mount"
    mounted = installer.mount(request.request_id, mount_path)
    assert mounted.state == InstallState.MOUNTED
    assert not mount_path.exists()
    assert not mount_path.is_symlink()
    enabled = installer.enable(request.request_id)
    assert enabled.state == InstallState.ENABLED
    installed_contract = (Path(enabled.installed_path) / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "doc-nexus-harness-execution-contract" not in installed_contract
    assert installed_contract == contract


def test_prepare_and_promote_collapse_mechanical_admission_steps(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    (source / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"demo-skill","version":"1.0.0","runtime":"python","entrypoint":"scripts/main.py"}',
        encoding="utf-8",
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)

    reviewed = prepare_install_for_review(installer, request.request_id)
    assert reviewed.state == InstallState.AWAITING_APPROVAL
    assert reviewed.skill_id == "demo-skill"
    assert reviewed.version == "1.0.0"
    assert reviewed.scan_report["adaptation"]["runtime"] == "python"

    enabled = promote_install_to_enabled(
        installer, request.request_id, tmp_path / "mount"
    )
    assert enabled.state == InstallState.ENABLED
    assert len(enabled.approved_sha256) == 64
    assert (Path(enabled.installed_path) / "SKILL.md").is_file()



def test_remote_request_is_quarantined_then_stops_for_approval(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("weather"), encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")

    request = installer.request_remote(
        "_system",
        source_name="weather",
        skill_id="weather",
        source_metadata={
            "git_url": "https://github.com/openclaw/openclaw.git",
            "subpath": "weather",
        },
    )

    assert request.state == InstallState.REQUESTED
    assert not Path(request.quarantine_path).exists()
    staged = installer.stage_remote_source(request.request_id, source)
    assert staged.state == InstallState.QUARANTINED
    reviewed = prepare_install_for_review(installer, request.request_id)

    assert reviewed.state == InstallState.AWAITING_APPROVAL
    assert reviewed.skill_id == "weather"
    assert reviewed.installed_path == ""

def test_install_rejects_any_change_after_approval(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    (source / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "assets.txt").write_text("approved\n", encoding="utf-8")
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"demo-skill","version":"1.0.0","runtime":"python","entrypoint":"scripts/main.py"}',
        encoding="utf-8",
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "demo-skill", "1.0.0")
    installer.approve(request.request_id)

    materialized = Path(request.quarantine_path).parent / "materialized"
    (materialized / "assets.txt").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(SkillInstallError, match="approved Skill content changed"):
        installer.install(request.request_id)


def test_install_rejects_symlink_added_after_approval(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("prompt-skill", "Prompt"), encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "prompt-skill", "1.0.0")
    installer.approve(request.request_id)

    materialized = Path(request.quarantine_path).parent / "materialized"
    (materialized / "escape").symlink_to(tmp_path / "outside")

    with pytest.raises(SkillInstallError, match="symlink"):
        installer.install(request.request_id)


def test_install_rejects_permission_change_after_approval(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    script = source / "scripts" / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request(
        "tenant-a", source, skill_id="demo-skill", version="1.0.0"
    )
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "demo-skill", "1.0.0")
    materialized_script = (
        Path(request.quarantine_path).parent / "materialized" / "scripts" / "run.sh"
    )
    assert materialized_script.stat().st_mode & 0o777 == 0o755
    installer.approve(request.request_id)
    materialized_script.chmod(0o644)

    with pytest.raises(SkillInstallError, match="approved Skill content changed"):
        installer.install(request.request_id)


def test_legacy_install_record_requires_new_approval_digest():
    record = SkillInstallRecord.from_dict(
        {
            "request_id": "skill-legacy",
            "tenant_id": "_system",
            "source_name": "legacy",
            "state": "enabled",
            "quarantine_path": "/tmp/legacy",
        }
    )

    assert record.skill_id == ""
    assert record.version == ""
    assert record.approved_sha256 == ""


def test_enable_rejects_a_second_active_version_of_same_skill(tmp_path):
    installer = SkillInstaller(tmp_path / "runtime")

    def prepare(version: str) -> str:
        source = tmp_path / f"source-{version}"
        source.mkdir()
        (source / "SKILL.md").write_text(
            _skill_md("demo-skill", f"Demo {version}"), encoding="utf-8"
        )
        request = installer.request(
            "tenant-a", source, skill_id="demo-skill", version=version
        )
        installer.scan(request.request_id)
        installer.adapt(request.request_id, "demo-skill", version)
        installer.approve(request.request_id)
        installer.install(request.request_id)
        installer.mount(request.request_id, tmp_path / "unused")
        return request.request_id

    installer.enable(prepare("1.0.0"))

    with pytest.raises(SkillInstallError, match="already enabled"):
        installer.enable(prepare("2.0.0"))


def test_enable_enforces_shared_publication_quota(tmp_path):
    installer = SkillInstaller(
        tmp_path / "runtime",
        max_enabled_bytes=220,
        max_enabled_files=10,
    )

    def prepare(name: str) -> str:
        source = tmp_path / name
        source.mkdir()
        (source / "SKILL.md").write_text(_skill_md(name) + ("x" * 80))
        request = installer.request(
            "tenant-a", source, skill_id=name, version="1.0.0"
        )
        installer.scan(request.request_id)
        installer.adapt(request.request_id, name, "1.0.0")
        installer.approve(request.request_id)
        installer.install(request.request_id)
        installer.mount(request.request_id, tmp_path / "unused")
        return request.request_id

    installer.enable(prepare("first-skill"))
    with pytest.raises(SkillInstallError, match="shared byte quota"):
        installer.enable(prepare("second-skill"))


def test_enable_counts_existing_admitted_skills_against_shared_not_upload_quota(tmp_path):
    root = tmp_path / "runtime"
    bootstrap = SkillInstaller(root, max_bytes=1024, max_enabled_bytes=2048)
    source = tmp_path / "large"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("large-skill") + "x" * 512)
    large = bootstrap.request(
        "tenant-a", source, skill_id="large-skill", version="1.0.0"
    )
    prepare_install_for_review(bootstrap, large.request_id)
    promote_install_to_enabled(bootstrap, large.request_id, tmp_path / "unused")

    # 已按其他准入额度批准的库存不能套用本次上传限制，仍须受共享总额度约束。
    installer = SkillInstaller(root, max_bytes=256, max_enabled_bytes=2048)
    small_id = _admit_skill(installer, tmp_path, name="small-skill")
    assert installer.enable(small_id).state == InstallState.ENABLED
    another = installer.request(
        "tenant-a", source, skill_id="large-skill", version="2.0.0"
    )
    with pytest.raises(SkillInstallError, match="size limit"):
        installer.scan(another.request_id)


def test_revoked_skill_can_reenable_only_while_approved_tree_is_intact(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("demo-skill"))
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request(
        "tenant-a", source, skill_id="demo-skill", version="1.0.0"
    )
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "demo-skill", "1.0.0")
    installer.approve(request.request_id)
    installer.install(request.request_id)
    installer.mount(request.request_id, tmp_path / "unused")
    installer.enable(request.request_id)
    installer.revoke("demo-skill", "1.0.0")

    assert installer.enable(request.request_id).state == InstallState.ENABLED

    installer.revoke("demo-skill", "1.0.0")
    Path(installer.get(request.request_id).installed_path, "SKILL.md").write_text(
        "# changed\n"
    )
    with pytest.raises(SkillInstallError, match="approved Skill content changed"):
        installer.enable(request.request_id)


def test_concurrent_installer_instances_do_not_rollback_revocation(tmp_path):
    root = tmp_path / "runtime"
    installer = SkillInstaller(root)
    enabled_source = tmp_path / "enabled"
    enabled_source.mkdir()
    (enabled_source / "SKILL.md").write_text(_skill_md("enabled", "Enabled"))
    enabled = installer.request(
        "tenant-a", enabled_source, skill_id="enabled", version="1.0.0"
    )
    installer.scan(enabled.request_id)
    installer.adapt(enabled.request_id, "enabled", "1.0.0")
    installer.approve(enabled.request_id)
    installer.install(enabled.request_id)
    installer.mount(enabled.request_id, tmp_path / "unused")
    installer.enable(enabled.request_id)

    pending_source = tmp_path / "pending"
    pending_source.mkdir()
    (pending_source / "SKILL.md").write_text(_skill_md("pending", "Pending"))
    pending = installer.request(
        "tenant-a", pending_source, skill_id="pending", version="1.0.0"
    )

    revoker = SkillInstaller(root)
    stale_scanner = SkillInstaller(root)
    revoker.revoke("enabled", "1.0.0")
    stale_scanner.scan(pending.request_id)

    final = SkillInstaller(root)
    assert final.get(enabled.request_id).state == InstallState.REVOKED
    assert final.get(pending.request_id).state == InstallState.SCANNED


def test_skill_tree_hash_rejects_a_symlink_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(SkillIntegrityError, match="regular directory"):
        hash_skill_tree(linked)


def test_skill_tree_hash_encodes_file_lengths(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    payload = b"approved"
    encoded_empty_b_header = (
        b"F" + (0o644).to_bytes(2, "big") + (1).to_bytes(8, "big") + b"b"
    )
    (one / "a").write_bytes(payload + encoded_empty_b_header)
    (two / "a").write_bytes(payload)
    (two / "b").write_bytes(b"")
    for path in (one / "a", two / "a", two / "b"):
        path.chmod(0o644)

    assert hash_skill_tree(one) != hash_skill_tree(two)


def test_skill_tree_hash_covers_special_permission_bits(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a").write_text("same\n")
    (two / "a").write_text("same\n")
    (one / "a").chmod(0o644)
    (two / "a").chmod(0o4644)

    assert hash_skill_tree(one) != hash_skill_tree(two)


def test_install_request_rejects_a_symlink_source_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("# Demo\n")
    linked = tmp_path / "linked"
    linked.symlink_to(source, target_is_directory=True)
    installer = SkillInstaller(tmp_path / "runtime")

    with pytest.raises(SkillInstallError, match="symlink"):
        installer.request("tenant-a", linked)


@pytest.mark.parametrize("segment", [".", ".."])
def test_install_request_rejects_path_semantic_identity_segments(tmp_path, segment):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("demo-skill"))
    installer = SkillInstaller(tmp_path / "runtime")

    with pytest.raises(SkillInstallError, match="invalid"):
        installer.request(
            "tenant-a", source, skill_id="demo-skill", version=segment
        )


def test_zip_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../escape.txt", "bad")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", archive)
    with pytest.raises(SkillInstallError):
        installer.scan(request.request_id)


def test_zip_with_directory_entries_scans_cleanly(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("demo-skill/", "")
        stream.writestr("demo-skill/scripts/", "")
        stream.writestr("demo-skill/SKILL.md", _skill_md("demo-skill"))
        stream.writestr("demo-skill/scripts/main.py", "print('ok')\n")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", archive)
    scanned = installer.scan(request.request_id)
    assert scanned.scan_report["files"] == 2
    assert scanned.state == InstallState.SCANNED


def test_adapt_reports_pure_python_without_generating_files(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    (source / "scripts" / "main.py").write_text(
        "import sys\nprint(sys.argv[1:])\n", encoding="utf-8"
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "demo-skill", "1.0.0")

    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert result["manifest"] is None
    assert result["report"]["entrypoint"] == "scripts/main.py"
    assert result["report"]["requires_review"] is False
    materialized = installer._materialize(
        Path(adapted.quarantine_path), request.request_id
    )
    assert not (materialized / "skill.manifest.json").exists()
    assert not any(path.name.endswith("_entrypoint.py") for path in materialized.iterdir())


def test_adapt_flags_network_and_dependencies_for_review(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        _skill_md("demo-skill", "Network Skill"), encoding="utf-8"
    )
    (source / "scripts" / "main.py").write_text(
        "import requests\nrequests.get('https://api.example.com/data')\n",
        encoding="utf-8",
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "demo-skill", "1.0.0")

    assert result["report"]["requires_review"] is True
    assert "requests" in result["report"]["dependencies"]
    assert "api.example.com" in result["report"]["network_calls"]
    assert adapted.state == InstallState.AWAITING_APPROVAL


def test_adapt_flags_bash_skill_for_review_without_blocking_it(tmp_path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        _skill_md("demo-skill", "Bash Skill"), encoding="utf-8"
    )
    (source / "run.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "demo-skill", "1.0.0")

    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert result["report"]["runtime"] == "bash"
    assert result["report"]["requires_review"] is True


def test_scan_analyzes_scripts_even_when_manifest_exists(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("network-skill", "Network Skill"))
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"network-skill","version":"1.0.0"}'
    )
    (source / "scripts" / "main.py").write_text(
        "import requests\nrequests.get('https://api.example.com/data')\n"
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)

    report = installer.scan(request.request_id).scan_report["adaptation"]

    assert report["requires_review"] is True
    assert report["network_calls"] == ["api.example.com"]


def test_scan_flags_mixed_and_unrecognized_scripts_for_review(tmp_path):
    mixed = tmp_path / "mixed"
    (mixed / "scripts").mkdir(parents=True)
    (mixed / "SKILL.md").write_text(_skill_md("mixed-skill", "Mixed Skill"))
    (mixed / "scripts" / "main.py").write_text("print('python')\n")
    (mixed / "scripts" / "index.js").write_text("console.log('node')\n")
    installer = SkillInstaller(tmp_path / "runtime")
    mixed_request = installer.request("tenant-a", mixed)

    mixed_report = installer.scan(mixed_request.request_id).scan_report["adaptation"]

    assert mixed_report["requires_review"] is True
    assert mixed_report["prompt_only"] is False

    unknown = tmp_path / "unknown"
    (unknown / "scripts").mkdir(parents=True)
    (unknown / "SKILL.md").write_text(
        _skill_md("unknown-script", "Unknown Script")
    )
    script = unknown / "scripts" / "run"
    script.write_text("#!/usr/bin/env ruby\nputs 'ok'\n")
    script.chmod(0o755)
    unknown_request = installer.request("tenant-a", unknown)

    unknown_report = installer.scan(unknown_request.request_id).scan_report[
        "adaptation"
    ]

    assert unknown_report["requires_review"] is True
    assert unknown_report["prompt_only"] is False


def test_scan_requires_exactly_one_standard_skill_definition(tmp_path):
    installer = SkillInstaller(tmp_path / "runtime")
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "run.py").write_text("print('no definition')\n")
    missing_request = installer.request("tenant-a", missing)

    with pytest.raises(SkillInstallError, match="exactly one SKILL.md"):
        installer.scan(missing_request.request_id)

    bundled = tmp_path / "bundled"
    (bundled / "one").mkdir(parents=True)
    (bundled / "two").mkdir()
    (bundled / "one" / "SKILL.md").write_text("# One\n")
    (bundled / "two" / "SKILL.md").write_text("# Two\n")
    bundled_request = installer.request("tenant-a", bundled)

    with pytest.raises(SkillInstallError, match="exactly one SKILL.md"):
        installer.scan(bundled_request.request_id)


def test_scan_requires_pi_loadable_frontmatter_and_bound_name(tmp_path):
    installer = SkillInstaller(tmp_path / "runtime")
    missing_description = tmp_path / "missing-description"
    missing_description.mkdir()
    (missing_description / "SKILL.md").write_text(
        "---\nname: demo-skill\n---\n# Demo\n"
    )
    request = installer.request("tenant-a", missing_description)
    with pytest.raises(SkillInstallError, match="description is required"):
        installer.scan(request.request_id)

    mismatched = tmp_path / "mismatched"
    mismatched.mkdir()
    (mismatched / "SKILL.md").write_text(_skill_md("actual-name"))
    request = installer.request(
        "tenant-a", mismatched, skill_id="requested-name", version="1.0.0"
    )
    with pytest.raises(SkillInstallError, match="does not match"):
        installer.scan(request.request_id)


def test_scanned_skill_cannot_skip_analysis_registration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("demo-skill"))
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request(
        "tenant-a", source, skill_id="demo-skill", version="1.0.0"
    )
    installer.scan(request.request_id)

    with pytest.raises(SkillInstallError, match="invalid Skill state transition"):
        installer.approve(request.request_id)


def test_scan_rejects_manifest_identity_different_from_requested_identity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("expected"))
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"different","version":"9.9.9"}'
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request(
        "tenant-a", source, skill_id="expected", version="1.0.0"
    )

    with pytest.raises(SkillInstallError, match="identity does not match"):
        installer.scan(request.request_id)


def test_adapt_rejects_identity_different_from_scanned_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("manifest-skill"))
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"manifest-skill","version":"1.0.0"}'
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    with pytest.raises(SkillInstallError, match="identity does not match"):
        installer.adapt(request.request_id, "different", "9.9.9")


def test_approve_rejects_manifest_changed_after_scan(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("manifest-skill"))
    (source / "skill.manifest.json").write_text(
        '{"skill_id":"manifest-skill","version":"1.0.0"}'
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "manifest-skill", "1.0.0")
    materialized = Path(request.quarantine_path).parent / "materialized"
    (materialized / "skill.manifest.json").write_text(
        '{"skill_id":"changed","version":"2.0.0"}'
    )

    with pytest.raises(SkillInstallError, match="changed after scanning"):
        installer.approve(request.request_id)


def test_adapt_marks_prompt_only_skill(tmp_path):
    source = tmp_path / "source"
    (source / "agents").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        _skill_md("prompt-skill", "Prompt skill"), encoding="utf-8"
    )
    (source / "agents" / "agent.yaml").write_text("name: x\n", encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "prompt-skill", "1.0.0")

    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert result["report"]["prompt_only"] is True
    # No pseudo-manifest is generated: a prompt-only Skill must never be
    # registered as executable.
    assert result["manifest"] is None
    installer.approve(request.request_id)
    installed = installer.install(request.request_id)
    assert installed.state == InstallState.INSTALLED
    assert installed.scan_report.get("prompt_only") is True



def test_adapt_reports_prompt_cli_and_network_dependencies(tmp_path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        """---
name: web-search
description: Web search
allowed-tools: Bash(belt *)
---

Install with `npx skills add belt-sh/cli`, then run `belt app run`.
See https://inference.sh/docs/agents/adding-tools.
""",
        encoding="utf-8",
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "web-search", "1.0.0")

    report = result["report"]
    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert report["prompt_only"] is True
    assert report["requires_review"] is True
    assert report["dependencies"] == ["belt", "npx"]
    assert report["network_calls"] == ["inference.sh"]
    assert result["manifest"] is None

def test_adapt_analyzes_identity_without_rewriting_standard_skill(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "SKILL.md").write_text(
        _skill_md("native-skill", "Native Skill"), encoding="utf-8"
    )
    (source / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "assets" / "template.txt").write_text("keep me\n", encoding="utf-8")
    original = hash_skill_tree(source)
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "native-skill", "1.0.0")
    materialized = Path(request.quarantine_path).parent / "materialized"

    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert adapted.skill_id == "native-skill"
    assert adapted.version == "1.0.0"
    assert result["manifest"] is None
    assert result["report"]["runtime"] == "python"
    assert hash_skill_tree(materialized) == original


def test_adapt_pdf_skill_stays_native_prompt_skill(tmp_path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("pdf", "PDF skill"), encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)

    adapted, result = installer.adapt(request.request_id, "pdf", "latest")

    assert adapted.state == InstallState.AWAITING_APPROVAL
    assert result["report"]["prompt_only"] is True
    assert result["manifest"] is None
    entrypoint = Path(request.quarantine_path).parent / "materialized" / "pdf_entrypoint.py"
    assert not entrypoint.exists()


def test_enable_rejects_native_skill_change_after_approval(tmp_path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("pdf", "PDF skill"), encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "pdf", "latest")

    installer._materialize(Path(request.quarantine_path), request.request_id)
    approved = installer.approve(request.request_id)
    assert approved.state == InstallState.APPROVED

    installed = installer.install(request.request_id)
    installer.mount(request.request_id, tmp_path / "mount")
    Path(installed.installed_path, "SKILL.md").write_text("# changed\n")
    with pytest.raises(SkillInstallError, match="approved Skill content changed"):
        installer.enable(request.request_id)


def test_enable_rejects_nested_manifest_long_lived_credentials(tmp_path):
    source = tmp_path / "wrapped" / "demo-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"))
    (source / "skill.manifest.json").write_text(
        json.dumps(
            {
                "skill_id": "demo-skill",
                "version": "1.0.0",
                "capabilities": {"credentials": ["minio"]},
            }
        )
    )
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source.parent)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "demo-skill", "1.0.0")
    installer.approve(request.request_id)
    installer.install(request.request_id)
    installer.mount(request.request_id, tmp_path / "mount")

    with pytest.raises(SkillInstallError, match="long-lived credentials"):
        installer.enable(request.request_id)


def test_approval_rejects_tree_changes_after_scan(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    (source / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", source)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, "demo-skill", "latest")

    materialized = installer._materialize(
        Path(request.quarantine_path), request.request_id
    )
    reviewed = materialized / "review-notes.txt"
    reviewed.write_text("administrator-approved addition\n")
    with pytest.raises(SkillInstallError, match="changed after scanning"):
        installer.approve(request.request_id)


def test_tar_symlink_is_rejected(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as stream:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        stream.addfile(info)
    installer = SkillInstaller(tmp_path / "runtime")
    request = installer.request("tenant-a", archive)
    with pytest.raises(SkillInstallError):
        installer.scan(request.request_id)


def _admit_skill(installer, tmp_path, name="demo-skill", version="1.0.0"):
    source = tmp_path / f"source-{name}-{version}"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md(name), encoding="utf-8")
    request = installer.request("tenant-a", source, skill_id=name, version=version)
    installer.scan(request.request_id)
    installer.adapt(request.request_id, name, version)
    installer.approve(request.request_id)
    installer.install(request.request_id)
    installer.mount(request.request_id, tmp_path / "unused")
    return request.request_id


def test_lifecycle_timestamps_are_recorded_and_survive_read_and_audit_flush(tmp_path):
    root = tmp_path / "runtime"
    installer = SkillInstaller(root)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")

    requested = installer.request(
        "tenant-a",
        source,
        audit={"event_type": "skill_install_requested", "actor_id": "admin"},
    )
    created_at = requested.created_at
    assert created_at and requested.state_changed_at == created_at

    installer.scan(requested.request_id)
    installer.adapt(requested.request_id, "demo-skill", "1.0.0")
    approved = installer.approve(requested.request_id)
    assert approved.approved_at == approved.state_changed_at
    installer.install(requested.request_id)
    installer.mount(requested.request_id, tmp_path / "unused")
    enabled = installer.enable(requested.request_id)

    assert enabled.enabled_at == enabled.state_changed_at
    assert enabled.approved_at == approved.approved_at
    assert enabled.revoked_at is None and enabled.archived_at is None
    assert datetime.fromisoformat(enabled.created_at) <= datetime.fromisoformat(
        enabled.state_changed_at
    )

    before_reload = enabled.as_dict()
    reopened = SkillInstaller(root)
    delivered: list[dict] = []
    assert reopened.flush_pending_audits(delivered.append) >= 1
    assert delivered[0]["event_type"] == "skill_install_requested"
    assert reopened.get(requested.request_id).as_dict() == before_reload


def test_legacy_record_without_timestamps_reads_as_unknown():
    record = SkillInstallRecord.from_dict(
        {
            "request_id": "skill-legacy",
            "tenant_id": "_system",
            "source_name": "legacy",
            "state": "revoked",
            "quarantine_path": "/tmp/legacy",
        }
    )

    fields = (
        "created_at",
        "state_changed_at",
        "approved_at",
        "enabled_at",
        "revoked_at",
        "archived_at",
    )
    assert all(getattr(record, field) is None for field in fields)
    assert all(record.as_dict()[field] is None for field in fields)


def test_archive_is_reversible_and_enable_clears_archive(tmp_path):
    installer = SkillInstaller(tmp_path / "runtime")
    request_id = _admit_skill(installer, tmp_path)
    installer.enable(request_id)
    installer.revoke("demo-skill", "1.0.0")

    revoked = installer.get(request_id)
    state_changed_at = revoked.state_changed_at
    artifact = Path(revoked.installed_path)
    assert artifact.is_dir()

    archive_audit = {"event_type": "skill_install_archived", "actor_id": "admin"}
    changed = installer.archive([request_id], True, audit=archive_audit)
    assert [record.request_id for record in changed] == [request_id]
    archived = installer.get(request_id)
    assert archived.archived_at
    assert archived.state == InstallState.REVOKED
    assert archived.state_changed_at == state_changed_at
    assert artifact.is_dir()
    assert (artifact / "SKILL.md").is_file()

    # 重试不修改时间，也不产生重复审计；重载后仍能投递已保存的事件。
    assert installer.archive([request_id], True, audit=archive_audit) == []
    assert installer.get(request_id).archived_at == archived.archived_at
    events = []
    reloaded = SkillInstaller(installer.root)
    reloaded.flush_pending_audits(events.append)
    assert [(event["event_type"], event["task_id"]) for event in events] == [
        ("skill_install_archived", f"skill-install:{request_id}")
    ]

    assert [record.request_id for record in installer.archive([request_id], False)] == [
        request_id
    ]
    assert installer.get(request_id).archived_at is None

    installer.archive([request_id], True)
    reenabled = installer.enable(request_id)
    assert reenabled.state == InstallState.ENABLED
    assert reenabled.archived_at is None
    assert reenabled.enabled_at
    assert reenabled.revoked_at


def test_archive_batch_validates_before_any_write(tmp_path):
    installer = SkillInstaller(tmp_path / "runtime")
    revoked_id = _admit_skill(installer, tmp_path, "revoked-skill")
    installer.enable(revoked_id)
    installer.revoke("revoked-skill", "1.0.0")

    pending_source = tmp_path / "pending-source"
    pending_source.mkdir()
    (pending_source / "SKILL.md").write_text(_skill_md("pending-skill"))
    pending = installer.request("tenant-a", pending_source, skill_id="pending-skill")
    installer.scan(pending.request_id)
    installer.adapt(pending.request_id, "pending-skill", "1.0.0")

    with pytest.raises(SkillInstallConflictError):
        installer.archive([revoked_id, pending.request_id], True)
    assert installer.get(revoked_id).archived_at is None

    with pytest.raises(SkillInstallNotFoundError):
        installer.archive([revoked_id, "skill-missing"], True)
    assert installer.get(revoked_id).archived_at is None

    changed = installer.archive([revoked_id, revoked_id], True)
    assert [record.request_id for record in changed] == [revoked_id]


def test_management_sort_puts_newest_first_and_unknown_times_last():
    def record(request_id: str, created_at: str | None) -> SkillInstallRecord:
        raw = {
            "request_id": request_id,
            "tenant_id": "_system",
            "source_name": "s",
            "state": "failed",
            "quarantine_path": "/tmp/q",
        }
        if created_at:
            raw["created_at"] = created_at
        return SkillInstallRecord.from_dict(raw)

    legacy = record("skill-aaa", None)
    older = record("skill-bbb", "2026-01-01T00:00:00+00:00")
    newer = record("skill-ccc", "2026-02-01T00:00:00+00:00")
    zulu = record("skill-ddd", "2026-03-01T00:00:00Z")

    ordered = sort_install_records_newest_first([legacy, older, newer, zulu])
    assert [record.request_id for record in ordered] == [
        "skill-ddd",
        "skill-ccc",
        "skill-bbb",
        "skill-aaa",
    ]
