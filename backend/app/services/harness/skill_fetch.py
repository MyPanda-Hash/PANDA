"""Server-side fetching of third-party Skill sources.

Downloads run inside the backend process — never through the Pi session — so
the Pi tool policy (curl/python/package-install bans) does not apply to the
pipeline itself. The SSRF guard does: every hop must be public HTTPS. Git
sources are pinned to a fixed ref resolved to a commit, and the downloaded
artifact is handed to the installer quarantine unchanged.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.services.harness.network import NetworkSecurityError, reject_private_host

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_GIT_REF = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_SUFFIX = {".zip", ".tar", ".tgz", ".gz"}
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_TIMEOUT_MESSAGE = (
    "连接 Git 仓库超时。请确认服务器能访问该地址；"
    "大型仓库请填写 Skill 子路径，或改用 ZIP 上传。"
)


class SkillSourceError(ValueError):
    """Skill source download failed a safety or reachability check."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _open_url_no_redirect(
    request: urllib.request.Request, *, timeout: int
):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _require_https(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise SkillSourceError("Skill 来源必须使用 https://")
    if parsed.username or parsed.password:
        raise SkillSourceError("URL 不允许包含账号密码")


def _normalized_subpath(subpath: str) -> str:
    normalized = subpath.replace("\\", "/").strip("/")
    if (
        not normalized
        or normalized in {".", ".."}
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise SkillSourceError("子路径无效")
    return normalized


def normalize_git_source(url: str, ref: str = "", subpath: str = "") -> tuple[str, str, str]:
    """Normalize a clone URL and infer ref/subpath from GitHub tree/blob links."""
    trimmed = url.strip()
    parsed = urllib.parse.urlsplit(trimmed)
    host = (parsed.hostname or "").lower()
    if host not in _GITHUB_HOSTS:
        return trimmed.rstrip("/"), ref, subpath
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return trimmed.rstrip("/"), ref, subpath
    owner, repo = parts[0], parts[1].removesuffix(".git")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    inferred_ref = ref
    inferred_subpath = subpath
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        if not inferred_ref:
            inferred_ref = parts[3]
        rest = parts[4:]
        if parts[2] == "blob" and rest:
            rest = rest[:-1]
        if rest and not inferred_subpath:
            inferred_subpath = "/".join(rest)
    return clone_url, inferred_ref, inferred_subpath


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _run_git(
    git: str,
    args: list[str],
    *,
    timeout: int,
    on_fail: str,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        git,
        "-c",
        "credential.helper=",
        "-c",
        "http.lowSpeedLimit=1000",
        "-c",
        "http.lowSpeedTime=20",
        *args,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillSourceError(_TIMEOUT_MESSAGE) from exc
    except OSError as exc:
        raise SkillSourceError(on_fail) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = (detail[-1] if detail else on_fail)[:200]
        raise SkillSourceError(f"{on_fail}: {suffix}" if suffix else on_fail)
    return result


def _resolve_commit(git: str, url: str, ref: str) -> tuple[str, str]:
    if ref and _COMMIT_SHA.match(ref.lower()):
        return ref.lower(), ref
    if ref:
        listing = _run_git(
            git,
            ["ls-remote", url, ref, f"refs/tags/{ref}", f"refs/heads/{ref}"],
            timeout=45,
            on_fail="无法查询远程 Git 仓库",
        )
    else:
        listing = _run_git(
            git,
            ["ls-remote", url, "HEAD"],
            timeout=45,
            on_fail="无法查询远程 Git 仓库",
        )
    if not listing.stdout.strip():
        raise SkillSourceError(
            f"远程仓库中找不到 Git ref「{ref}」。请确认 tag/branch 名称，或留空使用默认分支。"
            if ref
            else "无法解析远程仓库的默认分支。"
        )
    commit = listing.stdout.splitlines()[0].split("\t")[0].strip().lower()
    if not _COMMIT_SHA.match(commit):
        raise SkillSourceError("Git ref 未能解析为固定 commit")
    return commit, ref or "HEAD"


def fetch_git_archive(
    url: str, ref: str, dest: Path, subpath: str = ""
) -> dict[str, object]:
    """Fetch a public Git repository pinned to *ref*, stripped of history.

    *ref* may be a tag, branch, or commit; when empty the remote default
    branch HEAD is resolved and pinned. When *subpath* is set, only that
    directory is checked out (sparse clone) so large monorepos stay tractable.
    """
    url, ref, subpath = normalize_git_source(url, ref, subpath)
    _require_https(url)
    if ref and (not _GIT_REF.match(ref) or ".." in ref):
        raise SkillSourceError("Git ref 格式无效")
    if subpath:
        subpath = _normalized_subpath(subpath)
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        reject_private_host(host)
    except NetworkSecurityError as exc:
        if "cannot resolve" in str(exc).lower():
            raise SkillSourceError("无法解析 Git 主机，请确认服务器能访问该地址") from exc
        raise
    git = shutil.which("git")
    if not git:
        raise SkillSourceError("服务器未安装 git，无法从仓库下载")

    commit, pinned_ref = _resolve_commit(git, url, ref)
    dest.mkdir(parents=True, exist_ok=True)
    _run_git(git, ["init", str(dest)], timeout=30, on_fail="Git 初始化失败")
    _run_git(
        git,
        ["-C", str(dest), "remote", "add", "origin", url],
        timeout=30,
        on_fail="Git 初始化失败",
    )
    if subpath:
        _run_git(
            git,
            ["-C", str(dest), "sparse-checkout", "init", "--cone"],
            timeout=30,
            on_fail="Git 稀疏检出失败",
        )
        _run_git(
            git,
            ["-C", str(dest), "sparse-checkout", "set", "--cone", subpath],
            timeout=30,
            on_fail="Git 稀疏检出失败",
        )

    fetch_args = ["-C", str(dest), "fetch", "--depth", "1"]
    if subpath:
        fetch_args.append("--filter=blob:none")
    fetch_args.extend(["origin", commit])
    try:
        _run_git(git, fetch_args, timeout=120, on_fail="Git 下载失败")
    except SkillSourceError as exc:
        if "--filter=blob:none" not in fetch_args or _TIMEOUT_MESSAGE in str(exc):
            raise
        _run_git(
            git,
            ["-C", str(dest), "fetch", "--depth", "1", "origin", commit],
            timeout=120,
            on_fail="Git 下载失败",
        )
    _run_git(
        git,
        ["-C", str(dest), "checkout", "--detach", commit],
        timeout=60,
        on_fail="Git 检出失败",
    )
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return {
        "source_type": "git",
        "url": url,
        "ref": pinned_ref,
        "commit": commit,
        "subpath": subpath,
    }


def select_subpath(src: Path, subpath: str, dest: Path) -> None:
    """Copy a single skill directory out of a downloaded repository.

    Mirrors ``npx skills add <repo> --skill <name>``: only the selected
    subdirectory becomes the quarantined artifact, so the adapter never scans
    unrelated repository content. Symlinks are rejected (the quarantine scan
    must still be able to see them).
    """
    normalized = _normalized_subpath(subpath)
    src_root = src.resolve()
    selected = (src / normalized).resolve()
    if not selected.is_relative_to(src_root) or not selected.is_dir():
        raise SkillSourceError("子路径不是仓库内的目录，请确认路径与 Git ref")
    dest.mkdir(parents=True)
    for path in selected.rglob("*"):
        if path.is_symlink():
            raise SkillSourceError(f"子路径包含符号链接: {path.name}")
        relative = path.relative_to(selected)
        target = dest / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())


def fetch_url_archive(url: str, dest: Path) -> dict[str, object]:
    """Download a public HTTPS archive with redirect revalidation and caps."""
    _require_https(url)
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if suffix not in _ALLOWED_SUFFIX:
        raise SkillSourceError("归档 URL 必须以 .zip/.tar/.tgz/.gz 结尾")
    current = url
    size = 0
    for _ in range(6):
        host = reject_private_host(urllib.parse.urlsplit(current).hostname or "")
        request = urllib.request.Request(current, method="GET")
        try:
            with _open_url_no_redirect(request, timeout=30) as response:
                if response.status in _REDIRECT_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        raise SkillSourceError("redirect has no Location header")
                    current = urllib.parse.urljoin(current, location)
                    _require_https(current)
                    continue
                with dest.open("wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            dest.unlink(missing_ok=True)
                            raise SkillSourceError("Skill archive exceeds 50MB")
                        stream.write(chunk)
                break
        except TimeoutError as exc:
            raise SkillSourceError("下载归档超时，请稍后重试或改用较小的文件") from exc
        except urllib.error.HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                location = exc.headers.get("Location")
                if not location:
                    raise SkillSourceError("redirect has no Location header") from exc
                current = urllib.parse.urljoin(current, location)
                _require_https(current)
                continue
            raise SkillSourceError(f"download failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise SkillSourceError(f"download failed: {exc.reason}") from exc
    else:
        raise SkillSourceError("too many redirects")
    return {"source_type": "url", "url": url, "size": size, "host": host}
