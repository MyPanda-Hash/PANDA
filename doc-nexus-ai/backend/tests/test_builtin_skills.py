import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.harness import builtin_skills
from app.services.harness.builtin_skills import (
    ensure_builtin_skills,
    ensure_configured_builtin_skills,
    parse_builtin_skill_allowlist,
)
from app.services.harness.skill_installation import (
    InstallState,
    SkillInstaller,
    SkillInstallError,
)
from app.services.pi_runtime.skills import list_agent_skills

def test_writing_skill_registry_includes_report_and_presentation_contract():
    from app.services.writing_skills_registry import writing_skill_definition

    report = writing_skill_definition("report")
    proposal = writing_skill_definition("proposal")
    assert report is not None
    assert proposal is not None
    assert "pptx" in report.supported_formats
    assert "pptx" in proposal.supported_formats
    assert "architecture" in proposal.presentation_roles
    assert (
        Path(__file__).resolve().parents[2]
        / "scripts/writing-skills/writing-report.SKILL.md"
    ).is_file()

def _write_skill(root, skill_id: str) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: built-in test skill\n---\n",
        encoding="utf-8",
    )


def test_builtin_skill_bootstrap_is_idempotent(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    _write_skill(source_root, "writing-bid")
    _write_skill(source_root, "find-skills")

    first = ensure_builtin_skills(runtime_root, "writing-bid,find-skills,the-news")
    assert first["enabled"] == ["writing-bid", "find-skills"]
    assert first["missing"] == ["the-news"]

    second = ensure_builtin_skills(runtime_root, "writing-bid,find-skills,the-news")
    assert second["enabled"] == []
    assert second["skipped"] == ["writing-bid", "find-skills"]
    assert second["missing"] == ["the-news"]

    installer = SkillInstaller(runtime_root / "skill-installations" / "_system")
    assert [record.state for record in installer.list()] == [
        InstallState.ENABLED,
        InstallState.ENABLED,
    ]
    agent = SimpleNamespace(id="doc-nexus-pi", tenant_id="_system")
    settings = SimpleNamespace(root=runtime_root, pi_root=runtime_root)
    listed = list_agent_skills(agent, settings)
    assert [item["skill_id"] for item in listed] == ["find-skills", "writing-bid"]


def test_default_builtin_delivery_bootstraps_and_lists_every_skill(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    completed = subprocess.run(
        ["bash", str(repo_root / "scripts" / "ensure-writing-skills.sh"), str(repo_root)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DOC_NEXUS_PI_SKILLS_DIR": str(source_root)},
    )
    assert completed.returncode == 0, completed.stderr

    result = ensure_builtin_skills(runtime_root)

    assert result == {
        "enabled": list(builtin_skills.DEFAULT_BUILTIN_SKILLS),
        "skipped": [],
        "missing": [],
        "failed": [],
    }
    agent = SimpleNamespace(id="doc-nexus-pi", tenant_id="_system")
    settings = SimpleNamespace(root=runtime_root, pi_root=runtime_root)
    assert {item["skill_id"] for item in list_agent_skills(agent, settings)} == set(
        builtin_skills.DEFAULT_BUILTIN_SKILLS
    )


def test_explicit_empty_allowlist_keeps_all_builtins_disabled(tmp_path):
    result = ensure_builtin_skills(tmp_path / "runtime", "")

    assert result == {
        "enabled": [],
        "skipped": [],
        "missing": [],
        "failed": [],
    }


def test_packaged_builtin_does_not_admit_same_name_private_runtime_skill(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    runtime_sources = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    packaged_sources = tmp_path / "packaged"
    _write_skill(runtime_sources, "writing-bid")
    _write_skill(packaged_sources, "writing-bid")
    private_definition = runtime_sources / "writing-bid" / "SKILL.md"
    private_definition.write_text(
        "---\nname: writing-bid\ndescription: private user skill\n---\n",
        encoding="utf-8",
    )
    packaged_definition = packaged_sources / "writing-bid" / "SKILL.md"
    packaged_definition.write_text(
        "---\nname: writing-bid\ndescription: trusted platform skill\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        builtin_skills,
        "_packaged_source_root",
        lambda: packaged_sources,
    )

    result = ensure_builtin_skills(runtime_root, "writing-bid")

    assert result["enabled"] == ["writing-bid"]
    record = SkillInstaller(
        runtime_root / "skill-installations" / "_system"
    ).list()[0]
    installed_definition = Path(record.installed_path) / "SKILL.md"
    assert "trusted platform skill" in installed_definition.read_text(encoding="utf-8")
    assert "private user skill" in private_definition.read_text(encoding="utf-8")


def test_configured_builtin_bootstrap_uses_runtime_settings(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    _write_skill(
        runtime_root / "skills" / "_system" / "doc-nexus-pi",
        "writing-bid",
    )
    monkeypatch.setattr(
        builtin_skills.PiRuntimeSettings,
        "from_env",
        classmethod(lambda cls: SimpleNamespace(root=runtime_root)),
    )

    result = ensure_configured_builtin_skills("writing-bid")

    assert result["enabled"] == ["writing-bid"]


def test_builtin_skill_bootstrap_repairs_tampered_installed_copy(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    _write_skill(source_root, "writing-bid")
    assert ensure_builtin_skills(runtime_root, "writing-bid")["enabled"] == [
        "writing-bid"
    ]

    installer = SkillInstaller(runtime_root / "skill-installations" / "_system")
    original = installer.list()[-1]
    installed_skill = Path(original.installed_path) / "SKILL.md"
    installed_skill.write_text("tampered", encoding="utf-8")

    repaired = ensure_builtin_skills(runtime_root, "writing-bid")

    assert repaired["enabled"] == ["writing-bid"]
    records = SkillInstaller(
        runtime_root / "skill-installations" / "_system"
    ).list()
    assert next(
        record for record in records if record.request_id == original.request_id
    ).state == InstallState.REVOKED
    replacement = next(
        record for record in records if record.request_id != original.request_id
    )
    assert replacement.state == InstallState.ENABLED
    assert (Path(replacement.installed_path) / "SKILL.md").read_text(
        encoding="utf-8"
    ).startswith("---")


def test_builtin_skill_bootstrap_upgrades_changed_shipped_source(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    _write_skill(source_root, "writing-bid")
    ensure_builtin_skills(runtime_root, "writing-bid")
    original = SkillInstaller(
        runtime_root / "skill-installations" / "_system"
    ).list()[0]
    (source_root / "writing-bid" / "SKILL.md").write_text(
        "---\nname: writing-bid\ndescription: updated built-in skill\n---\n",
        encoding="utf-8",
    )

    upgraded = ensure_builtin_skills(runtime_root, "writing-bid")

    assert upgraded["enabled"] == ["writing-bid"]
    records = SkillInstaller(
        runtime_root / "skill-installations" / "_system"
    ).list()
    previous = next(
        record for record in records if record.request_id == original.request_id
    )
    replacement = next(
        record for record in records if record.request_id != original.request_id
    )
    assert previous.state == InstallState.REVOKED
    assert replacement.state == InstallState.ENABLED
    assert previous.version != replacement.version


def test_browser_builtin_explicitly_replaces_stub_without_changing_other_skills(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    sources = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    _write_skill(sources, "agent-browser")
    _write_skill(sources, "writing-bid")
    with monkeypatch.context() as patch:
        patch.setattr(
            builtin_skills, "_source_for", lambda _root, skill_id: sources / skill_id
        )
        assert ensure_builtin_skills(
            runtime_root, "agent-browser,writing-bid"
        )["enabled"] == ["writing-bid", "agent-browser"]
    installer = SkillInstaller(runtime_root / "skill-installations" / "_system")
    before = {record.skill_id: record for record in installer.list()}
    old = before["agent-browser"]
    old_bytes = (Path(old.installed_path) / "SKILL.md").read_bytes()

    result = ensure_builtin_skills(runtime_root, "agent-browser")

    assert result == {
        "enabled": ["agent-browser"], "skipped": [], "missing": [], "failed": []
    }
    records = SkillInstaller(runtime_root / "skill-installations" / "_system").list()
    assert next(r for r in records if r.skill_id == "writing-bid") == before["writing-bid"]
    assert next(r for r in records if r.request_id == old.request_id).state == InstallState.REVOKED
    current = next(
        r for r in records
        if r.skill_id == "agent-browser" and r.state == InstallState.ENABLED
    )
    assert current.version != old.version
    assert current.installed_path != old.installed_path
    assert (Path(old.installed_path) / "SKILL.md").read_bytes() == old_bytes
    assert ensure_builtin_skills(runtime_root, "agent-browser")["skipped"] == ["agent-browser"]
    assert SkillInstaller(runtime_root / "skill-installations" / "_system").list() == records

    # A fresh default bootstrap must not activate browser automation implicitly.
    fresh_root = tmp_path / "fresh-runtime"
    with monkeypatch.context() as patch:
        patch.setattr(builtin_skills, "_packaged_source_root", lambda: sources)
        ensure_builtin_skills(fresh_root)
    assert not any(
        r.skill_id == "agent-browser"
        for r in SkillInstaller(fresh_root / "skill-installations" / "_system").list()
    )



def test_weather_geocoding_resolves_chinese_and_preserves_country_boundary(monkeypatch):
    import runpy

    script = (
        Path(builtin_skills.__file__).resolve().parents[2]
        / "builtin_skills/open-meteo-api/scripts/get_weather.py"
    )
    geocode = runpy.run_path(str(script))["geocode"]
    # 上游实测：武汉在英文索引无结果，在中文索引命中同一城市。
    def geocoding_response(_url, params):
        if (params["name"], params["language"]) not in {
            ("武汉", "zh"), ("Wuhan", "en")
        }:
            return {}
        return {"results": [{
            "id": 1791247, "name": "武汉", "country_code": "CN",
            "latitude": 30.58333, "longitude": 114.26667,
        }]}

    monkeypatch.setitem(geocode.__globals__, "fetch_json", geocoding_response)
    assert geocode("武汉", "CN")["id"] == 1791247
    assert geocode("Wuhan", "CN")["id"] == 1791247
    with pytest.raises(SystemExit) as wrong_country:
        geocode("武汉", "US")
    assert wrong_country.value.code == 1
    with pytest.raises(SystemExit) as missing:
        geocode("不存在的城市", "CN")
    assert missing.value.code == 1

def test_minio_upload_is_a_supported_builtin_skill() -> None:
    assert parse_builtin_skill_allowlist("minio-upload") == ("minio-upload",)


def test_packaged_minio_skill_bootstraps_without_runtime_source(tmp_path):
    runtime_root = tmp_path / "runtime"

    result = ensure_builtin_skills(runtime_root, "minio-upload")

    assert result["enabled"] == ["minio-upload"]
    record = SkillInstaller(
        runtime_root / "skill-installations" / "_system"
    ).list()[0]
    installed = Path(record.installed_path)
    assert {path.name for path in installed.iterdir()} == {
        "SKILL.md",
        "minio_upload.py",
    }
    script = installed / "minio_upload.py"
    assert script.is_file()
    assert "MINIO_SECRET" not in script.read_text(encoding="utf-8")


def test_packaged_presentation_skills_override_runtime_sources(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
    _write_skill(source_root, "dashi-ppt")
    _write_skill(source_root, "pptx")
    packaged_root = Path(builtin_skills.__file__).resolve().parents[2] / "builtin_skills"

    assert parse_builtin_skill_allowlist("pptx,dashi-ppt") == (
        "dashi-ppt",
        "pptx",
    )
    for skill_id in ("dashi-ppt", "pptx"):
        source = builtin_skills._source_for(runtime_root, skill_id)
        assert source == packaged_root / skill_id
        assert (source / "SKILL.md").is_file()


def test_packaged_presentation_skills_upgrade_incomplete_runtime_shell(
    monkeypatch,
    tmp_path,
):
    required_by_skill = {
        "dashi-ppt": (
            "LICENSE",
            "SKILL.md",
            "UPSTREAM.json",
            "scripts/prepare_runtime.py",
            "scripts/render_goal_deck.sh",
            "project/package.json",
            "project/package-lock.json",
            "project/layout-manifest.json",
            "references/goal-spec.schema.json",
            "assets/skill/theme-style-grid.png",
        ),
        "pptx": (
            "LICENSE",
            "SKILL.md",
            "scripts/pptx_tool.py",
        ),
    }

    for skill_id, required_paths in required_by_skill.items():
        runtime_root = tmp_path / skill_id / "runtime"
        runtime_source_root = runtime_root / "skills" / "_system" / "doc-nexus-pi"
        _write_skill(runtime_source_root, skill_id)
        old_source = runtime_source_root / skill_id

        with monkeypatch.context() as patch:
            patch.setattr(
                builtin_skills,
                "_source_for",
                lambda _runtime_root, _skill_id: old_source,
            )
            assert ensure_builtin_skills(runtime_root, skill_id)["enabled"] == [
                skill_id
            ]

        original = SkillInstaller(
            runtime_root / "skill-installations" / "_system"
        ).list()[0]
        upgraded = ensure_builtin_skills(runtime_root, skill_id)

        assert upgraded["enabled"] == [skill_id]
        records = SkillInstaller(
            runtime_root / "skill-installations" / "_system"
        ).list()
        previous = next(
            record for record in records if record.request_id == original.request_id
        )
        replacement = next(
            record for record in records if record.request_id != original.request_id
        )
        assert previous.state == InstallState.REVOKED
        assert replacement.state == InstallState.ENABLED
        assert previous.version != replacement.version

        installed = Path(replacement.installed_path)
        assert all(
            (installed / relative_path).is_file()
            for relative_path in required_paths
        )


@pytest.mark.parametrize("overflow", [0, 1], ids=["at-limit", "over-limit"])
def test_regular_skill_keeps_50_mib_size_limit(tmp_path, overflow):
    _write_skill(tmp_path / "source", "dashi-ppt")
    source = tmp_path / "source" / "dashi-ppt"
    with (source / "asset.bin").open("wb") as stream:
        stream.truncate(50 * 1024 * 1024 + overflow - (source / "SKILL.md").stat().st_size)
    installer = SkillInstaller(tmp_path / "ordinary-installations")
    record = installer.request("tenant-a", source)

    if overflow:
        with pytest.raises(SkillInstallError, match="size limit"):
            installer.scan(record.request_id)
    else:
        assert installer.scan(record.request_id).state == InstallState.SCANNED


@pytest.mark.parametrize("overflow", [0, 1], ids=["at-limit", "over-limit"])
def test_trusted_builtin_has_bounded_64_mib_size_limit(monkeypatch, tmp_path, overflow):
    _write_skill(tmp_path / "source", "dashi-ppt")
    source = tmp_path / "source" / "dashi-ppt"
    with (source / "asset.bin").open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + overflow - (source / "SKILL.md").stat().st_size)
    monkeypatch.setattr(builtin_skills, "_source_for", lambda _root, _skill_id: source)
    runtime_root = tmp_path / "runtime"
    result = ensure_builtin_skills(runtime_root, "dashi-ppt")

    if overflow:
        assert result["enabled"] == []
        assert len(result["failed"]) == 1
        assert "size limit" in result["failed"][0]
    else:
        assert result["failed"] == []
        assert result["enabled"] == ["dashi-ppt"]
        agent = SimpleNamespace(id="doc-nexus-pi", tenant_id="_system")
        settings = SimpleNamespace(root=runtime_root, pi_root=runtime_root)
        assert [item["skill_id"] for item in list_agent_skills(agent, settings)] == [
            "dashi-ppt"
        ]
