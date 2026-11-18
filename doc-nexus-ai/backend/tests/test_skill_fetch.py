from __future__ import annotations

import subprocess
import urllib.error
from pathlib import Path

import pytest

from app.services.harness import skill_fetch
from app.services.harness.network import NetworkSecurityError
from app.services.harness.skill_fetch import (
    SkillSourceError,
    fetch_git_archive,
    fetch_url_archive,
    normalize_git_source,
    select_subpath,
)


def _git_verb(args: list[str]) -> str:
    index = 1
    while index < len(args):
        if args[index] in {"-C", "-c"}:
            index += 2
            continue
        return args[index]
    return ""


def _ok(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def _allow_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.harness.skill_fetch.reject_private_host",
        lambda host, port=None: host,
    )


def test_fetch_git_rejects_non_https():
    with pytest.raises(SkillSourceError, match="https"):
        fetch_git_archive("http://example.com/repo.git", "v1.0.0", Path("/tmp/x"))


def test_fetch_git_rejects_invalid_ref():
    with pytest.raises(SkillSourceError, match="ref"):
        fetch_git_archive("https://example.com/repo.git", "../evil", Path("/tmp/x"))


def test_fetch_git_rejects_private_host():
    with pytest.raises(NetworkSecurityError):
        fetch_git_archive("https://127.0.0.1/repo.git", "v1.0.0", Path("/tmp/x"))


def test_fetch_url_rejects_non_archive_suffix():
    with pytest.raises(SkillSourceError, match="归档"):
        fetch_url_archive("https://example.com/skill.pdf", Path("/tmp/x"))


def test_fetch_url_revalidates_redirect_before_following(tmp_path, monkeypatch):
    checked_hosts: list[str] = []

    def reject_private(host: str, port=None):  # type: ignore[no-untyped-def]
        del port
        checked_hosts.append(host)
        if host == "127.0.0.1":
            raise NetworkSecurityError("private redirect")
        return host

    def no_redirect_open(request, *, timeout):  # type: ignore[no-untyped-def]
        del timeout
        raise urllib.error.HTTPError(
            request.full_url,
            302,
            "Found",
            {"Location": "https://127.0.0.1/private.zip"},
            None,
        )

    class AutoFollowedResponse:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def read(self, size: int) -> bytes:
            del size
            return b""

    monkeypatch.setattr(skill_fetch, "reject_private_host", reject_private)
    monkeypatch.setattr(
        skill_fetch, "_open_url_no_redirect", no_redirect_open, raising=False
    )
    monkeypatch.setattr(
        skill_fetch.urllib.request,
        "urlopen",
        lambda *args, **kwargs: AutoFollowedResponse(),
    )

    with pytest.raises(NetworkSecurityError, match="private redirect"):
        fetch_url_archive("https://example.com/skill.zip", tmp_path / "skill.zip")

    assert checked_hosts == ["example.com", "127.0.0.1"]


def test_normalize_github_tree_url():
    url, ref, subpath = normalize_git_source(
        "https://github.com/openclaw/openclaw/tree/main/skills/weather",
        "",
        "",
    )
    assert url == "https://github.com/openclaw/openclaw.git"
    assert ref == "main"
    assert subpath == "skills/weather"


def test_normalize_github_repo_url_keeps_explicit_ref():
    url, ref, subpath = normalize_git_source(
        "https://github.com/openclaw/openclaw",
        "v2026.3.28",
        "skills/weather",
    )
    assert url == "https://github.com/openclaw/openclaw.git"
    assert ref == "v2026.3.28"
    assert subpath == "skills/weather"


def test_fetch_git_pins_commit_and_strips_history(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if _git_verb(list(args)) == "ls-remote":
            return _ok(list(args), "a" * 40 + "\trefs/tags/v1.0.0\n")
        return _ok(list(args))

    _allow_public_host(monkeypatch)
    monkeypatch.setattr("app.services.harness.skill_fetch.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("app.services.harness.skill_fetch.subprocess.run", fake_run)
    dest = tmp_path / "src"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir(parents=True)

    meta = fetch_git_archive("https://example.com/repo.git", "v1.0.0", dest)

    assert meta["commit"] == "a" * 40
    assert any(_git_verb(call) == "fetch" for call in calls)
    assert any(_git_verb(call) == "checkout" for call in calls)
    assert not any(_git_verb(call) == "clone" for call in calls)
    assert not (dest / ".git").exists()


def test_fetch_git_defaults_to_remote_head(tmp_path, monkeypatch):
    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        if _git_verb(list(args)) == "ls-remote":
            return _ok(list(args), "b" * 40 + "\tHEAD\n")
        return _ok(list(args))

    _allow_public_host(monkeypatch)
    monkeypatch.setattr("app.services.harness.skill_fetch.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("app.services.harness.skill_fetch.subprocess.run", fake_run)
    dest = tmp_path / "src"
    dest.mkdir(parents=True)

    meta = fetch_git_archive("https://example.com/repo.git", "", dest)

    assert meta["commit"] == "b" * 40
    assert meta["ref"] == "HEAD"


def test_fetch_git_sparse_checkouts_subpath(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if _git_verb(list(args)) == "ls-remote":
            return _ok(list(args), "c" * 40 + "\tHEAD\n")
        return _ok(list(args))

    _allow_public_host(monkeypatch)
    monkeypatch.setattr("app.services.harness.skill_fetch.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("app.services.harness.skill_fetch.subprocess.run", fake_run)
    dest = tmp_path / "src"

    meta = fetch_git_archive(
        "https://example.com/repo.git",
        "",
        dest,
        subpath="skills/weather",
    )

    assert meta["url"] == "https://example.com/repo.git"
    assert meta["subpath"] == "skills/weather"
    assert any(_git_verb(call) == "sparse-checkout" for call in calls)
    assert any("--filter=blob:none" in call for call in calls)


def test_fetch_git_missing_ref_does_not_clone(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if _git_verb(list(args)) == "ls-remote":
            return _ok(list(args), "")
        return _ok(list(args))

    _allow_public_host(monkeypatch)
    monkeypatch.setattr("app.services.harness.skill_fetch.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("app.services.harness.skill_fetch.subprocess.run", fake_run)

    with pytest.raises(SkillSourceError, match="找不到 Git ref"):
        fetch_git_archive("https://example.com/repo.git", "v1.0.0", tmp_path / "src")
    assert not any(_git_verb(call) == "fetch" for call in calls)


def test_fetch_git_timeout_is_user_facing(tmp_path, monkeypatch):
    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(args, 45)

    _allow_public_host(monkeypatch)
    monkeypatch.setattr("app.services.harness.skill_fetch.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("app.services.harness.skill_fetch.subprocess.run", fake_run)

    with pytest.raises(SkillSourceError, match="超时"):
        fetch_git_archive("https://example.com/repo.git", "v1.0.0", tmp_path / "src")


def test_select_subpath_copies_only_the_chosen_skill(tmp_path):
    repo = tmp_path / "repo"
    skill = repo / "skills" / "engineering" / "grill-with-docs"
    skill.mkdir(parents=True)
    (repo / "README.md").write_text("repo readme\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("# grill\n", encoding="utf-8")
    (skill / "main.py").write_text("print('ok')\n", encoding="utf-8")

    selected = tmp_path / "selected"
    select_subpath(repo, "skills/engineering/grill-with-docs", selected)

    assert (selected / "SKILL.md").is_file()
    assert (selected / "main.py").is_file()
    assert not (selected / "README.md").exists()


def test_select_subpath_rejects_escape_and_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(SkillSourceError, match="子路径"):
        select_subpath(repo, "../outside", tmp_path / "out")
    with pytest.raises(SkillSourceError, match="子路径"):
        select_subpath(repo, "README.md", tmp_path / "out")
