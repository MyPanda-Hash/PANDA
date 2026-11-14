"""Approved Skill registry and fixed-entrypoint execution.

Executable Skills are deliberately separate from the generic Bash tool.  A
record is approved outside the workspace, binds a fixed manifest and script
hash, and is executed with a fixed interpreter, cwd, and JSON stdin contract.
Network-capable Skills use the configured isolated container runtime; a
manifest capability is a request, never an implicit grant.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from app.services.harness.credentials import (
    CredentialError,
    CredentialRegistry,
)
from app.services.harness.skill_integrity import SkillIntegrityError, hash_skill_tree
from app.services.harness.skill_runtime_config import (
    ContainerSkillProfile,
    SkillRuntimeConfigError,
    build_container_argv,
)


class SkillSecurityError(RuntimeError):
    """Skill failed an integrity, path, or capability security check."""


class SkillNotApprovedError(SkillSecurityError):
    """Skill is not present in the approved registry."""


SkillRuntime = Literal["python", "node", "bash"]


def _safe_segment(value: str, label: str) -> str:
    if not value or len(value) > 128 or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for char in value
    ):
        raise SkillSecurityError(f"invalid {label}")
    return value


def _relative_path(value: str, label: str) -> str:
    raw = (value or "").replace("\\", "/")
    if (
        not raw
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in raw.split("/"))
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-" for char in raw)
    ):
        raise SkillSecurityError(f"invalid {label}")
    return raw


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_target(root: Path, relative: str, *, require_file: bool = False) -> Path:
    root = root.resolve()
    target = root.joinpath(*relative.split("/"))
    if target.is_symlink():
        # Installer-created mount points (.harness/<request_id>) are the only
        # allowed symlinks; the entrypoint is validated on the resolved
        # directory inside the controlled installation area.
        mount_root = (root / ".harness").resolve()
        if not _within(target.parent, mount_root):
            raise SkillSecurityError("skill path escapes root")
        resolved = target.resolve()
        if not resolved.is_dir() or resolved.is_symlink():
            raise SkillSecurityError("skill mount target is invalid")
        target = resolved
    else:
        if not _within(target, root):
            raise SkillSecurityError("skill path escapes root")
        current = target
        while current != root:
            if current.is_symlink():
                raise SkillSecurityError("skill path cannot contain symlinks")
            current = current.parent
    if require_file and (not target.is_file() or target.is_symlink()):
        raise SkillSecurityError("skill entrypoint is not a regular file")
    return target


@dataclass(frozen=True, slots=True)
class SkillManifest:
    skill_id: str
    version: str
    runtime: SkillRuntime
    entrypoint: str
    filesystem: Literal["workspace-ro", "workspace-rw"] = "workspace-ro"
    network: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    # "broker-client": the Skill was adapted to route network through the
    # Harness broker env contract (safe in the local executor).
    # "container": the Skill makes direct network calls and MUST run inside
    # the network-isolated container; the local executor refuses it.
    network_execution: Literal["broker-client", "container"] = "container"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SkillManifest:
        skill_id = _safe_segment(str(raw.get("skill_id", "")), "skill_id")
        version = _safe_segment(str(raw.get("version", "")), "version")
        runtime = str(raw.get("runtime", ""))
        if runtime not in {"python", "node", "bash"}:
            raise SkillSecurityError("unsupported Skill runtime")
        entrypoint = _relative_path(str(raw.get("entrypoint", "")), "entrypoint")
        capabilities = raw.get("capabilities") or {}
        if not isinstance(capabilities, dict):
            raise SkillSecurityError("capabilities must be an object")
        filesystem = str(capabilities.get("filesystem", "workspace-ro"))
        if filesystem not in {"workspace-ro", "workspace-rw"}:
            raise SkillSecurityError("unsupported filesystem capability")
        network_raw = capabilities.get("network", [])
        if not isinstance(network_raw, list) or any(
            not isinstance(host, str) or not host or "/" in host or ":" in host
            for host in network_raw
        ):
            raise SkillSecurityError("network capability must contain host names")
        credentials_raw = capabilities.get("credentials", [])
        if not isinstance(credentials_raw, list) or any(
            not isinstance(name, str)
            or not name
            or not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", name)
            for name in credentials_raw
        ):
            raise SkillSecurityError("invalid credential capability declaration")
        network_execution = str(capabilities.get("network_execution", "container"))
        if network_execution not in {"broker-client", "container"}:
            raise SkillSecurityError("invalid network_execution capability")
        return cls(
            skill_id=skill_id,
            version=version,
            runtime=runtime,  # type: ignore[arg-type]
            entrypoint=entrypoint,
            filesystem=filesystem,  # type: ignore[arg-type]
            network=tuple(sorted(set(network_raw))),
            credentials=tuple(sorted(set(credentials_raw))),
            network_execution=network_execution,  # type: ignore[arg-type]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "capabilities": {
                "filesystem": self.filesystem,
                "network": list(self.network),
                "credentials": list(self.credentials),
                "network_execution": self.network_execution,
            },
        }


def _normalize_cli_input(
    skill_id: str,
    manifest: SkillManifest,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Accept the historical ``argv`` spelling for generated CLI wrappers."""
    entrypoint = Path(manifest.entrypoint)
    if entrypoint.stem != f"{skill_id}_entrypoint" or entrypoint.suffix not in {".py", ".js"}:
        return input_data
    if "args" in input_data and "argv" in input_data:
        raise SkillSecurityError("Skill input cannot contain both args and argv")
    if "argv" not in input_data:
        return input_data
    args = input_data["argv"]
    if not isinstance(args, list) or len(args) > 32 or any(
        not isinstance(item, str) for item in args
    ):
        raise SkillSecurityError("input.argv must be a list of at most 32 strings")
    normalized = dict(input_data)
    normalized.pop("argv")
    normalized["args"] = args
    return normalized


def skill_entrypoint_hash(
    skills_root: Path, relative_path: str, manifest: SkillManifest
) -> str:
    """Hash the complete candidate Skill without registering it.

    The fixed wrapper can load sibling scripts and resources, so hashing only
    the wrapper would allow an approved Skill to change after approval.
    """
    skill_dir = _safe_target(skills_root, relative_path)
    _safe_target(skill_dir, manifest.entrypoint, require_file=True)
    try:
        return hash_skill_tree(skill_dir)
    except SkillIntegrityError as exc:
        raise SkillSecurityError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class SkillRecord:
    skill_id: str
    version: str
    relative_path: str
    sha256: str
    manifest: SkillManifest
    status: Literal["approved", "revoked"] = "approved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "manifest": self.manifest.as_dict(),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SkillRecord:
        manifest = SkillManifest.from_dict(raw.get("manifest") or {})
        skill_id = _safe_segment(str(raw.get("skill_id", "")), "skill_id")
        version = _safe_segment(str(raw.get("version", "")), "version")
        if skill_id != manifest.skill_id or version != manifest.version:
            raise SkillSecurityError("registry identity does not match manifest")
        relative_path = _relative_path(str(raw.get("relative_path", "")), "skill path")
        digest = str(raw.get("sha256", ""))
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise SkillSecurityError("invalid Skill hash")
        status = str(raw.get("status", ""))
        if status not in {"approved", "revoked"}:
            raise SkillSecurityError("invalid Skill registry status")
        return cls(skill_id, version, relative_path, digest, manifest, status)  # type: ignore[arg-type]


@dataclass(slots=True)
class SkillRegistry:
    """Tenant/agent-scoped immutable approval records."""

    root: Path

    def _path(self, tenant_id: str, agent_id: str) -> Path:
        tenant = _safe_segment(tenant_id, "tenant_id")
        agent = _safe_segment(agent_id, "agent_id")
        return self.root / tenant / f"{agent}.json"

    def _load(self, tenant_id: str, agent_id: str) -> list[SkillRecord]:
        path = self._path(tenant_id, agent_id)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw.get("skills", [])
            if not isinstance(records, list):
                raise SkillSecurityError("invalid Skill registry")
            return [SkillRecord.from_dict(item) for item in records]
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillSecurityError("cannot read Skill registry") from exc

    def _save(self, tenant_id: str, agent_id: str, records: list[SkillRecord]) -> None:
        path = self._path(tenant_id, agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"format": 1, "skills": [record.as_dict() for record in records]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        os.replace(temporary, path)

    def list(self, tenant_id: str, agent_id: str) -> list[SkillRecord]:
        return self._load(tenant_id, agent_id)

    def get(
        self, tenant_id: str, agent_id: str, skill_id: str, version: str
    ) -> SkillRecord:
        for record in self._load(tenant_id, agent_id):
            if (
                record.skill_id == skill_id
                and record.version == version
                and record.status == "approved"
            ):
                return record
        raise SkillNotApprovedError(f"Skill is not approved: {skill_id}@{version}")

    def approve(
        self,
        tenant_id: str,
        agent_id: str,
        skills_root: Path,
        relative_path: str,
        manifest: SkillManifest,
    ) -> SkillRecord:
        relative_path = _relative_path(relative_path, "skill path")
        raw_target = skills_root.joinpath(*relative_path.split("/"))
        if raw_target.is_symlink():
            # Installer-created mount points (.harness/<request_id>) are the
            # only allowed symlinks; they must live under the mount root and
            # resolve to the controlled installation area.
            mount_root = (skills_root / ".harness").resolve()
            if not _within(raw_target.parent, mount_root):
                raise SkillSecurityError("Skill mount point is not under .harness")
            skill_dir = raw_target.resolve()
            if not skill_dir.is_dir():
                raise SkillSecurityError("Skill mount target is invalid")
        else:
            skill_dir = _safe_target(skills_root, relative_path)
        if not skill_dir.is_dir() or skill_dir.is_symlink():
            raise SkillSecurityError("Skill directory is invalid")
        _safe_target(skill_dir, manifest.entrypoint, require_file=True)
        record = SkillRecord(
            skill_id=manifest.skill_id,
            version=manifest.version,
            relative_path=relative_path,
            sha256=hash_skill_tree(skill_dir),
            manifest=manifest,
        )
        records = [
            item
            for item in self._load(tenant_id, agent_id)
            if not (item.skill_id == record.skill_id and item.version == record.version)
        ]
        self._save(tenant_id, agent_id, [*records, record])
        return record

    def revoke(self, tenant_id: str, agent_id: str, skill_id: str, version: str) -> SkillRecord:
        records = self._load(tenant_id, agent_id)
        updated: list[SkillRecord] = []
        found: SkillRecord | None = None
        for record in records:
            if record.skill_id == skill_id and record.version == version:
                found = SkillRecord(
                    record.skill_id,
                    record.version,
                    record.relative_path,
                    record.sha256,
                    record.manifest,
                    "revoked",
                )
                updated.append(found)
            else:
                updated.append(record)
        if found is None:
            raise SkillNotApprovedError(f"Skill is not registered: {skill_id}@{version}")
        self._save(tenant_id, agent_id, updated)
        return found


@dataclass(frozen=True, slots=True)
class SkillRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(slots=True)
class ApprovedSkillRunner:
    registry: SkillRegistry
    timeout_seconds: int = 120
    max_output_bytes: int = 200_000
    container_profile: ContainerSkillProfile | None = None
    broker_url: str = ""
    broker_token: str = ""
    broker_task_id: str = ""
    broker_tenant_id: str = ""
    broker_user_id: str = ""
    credential_registry: CredentialRegistry | None = None

    def run(
        self,
        record: SkillRecord,
        *,
        tenant_id: str,
        agent_id: str,
        skills_root: Path,
        workspace: Path,
        input_data: dict[str, Any],
        container_runtime_root: Path | None = None,
        container_host_runtime_root: Path | None = None,
    ) -> SkillRunResult:
        if record.status != "approved":
            raise SkillNotApprovedError(f"Skill is not approved: {record.skill_id}@{record.version}")
        input_data = _normalize_cli_input(record.skill_id, record.manifest, input_data)
        # broker-client Skills use the fixed Harness capability gateway from
        # the local executor.  A configured container profile is reserved for
        # direct-network Skills; mounting a broker Skill into that container
        # would hide the gateway environment and make the capability fail.
        uses_container = self.container_profile is not None and (
            not record.manifest.network
            or record.manifest.network_execution == "container"
        )
        if record.manifest.network:
            if record.manifest.network_execution == "container":
                # Direct network calls can only run inside the isolated
                # container; the local executor would let them bypass the
                # Harness network gate entirely.
                if self.container_profile is None:
                    raise SkillSecurityError(
                        "network-capable container Skills require the "
                        "isolated container runtime"
                    )
            else:
                broker_ready = bool(
                    all(
                        (
                            self.broker_url,
                            self.broker_token,
                            self.broker_task_id,
                            self.broker_tenant_id,
                            self.broker_user_id,
                        )
                    )
                )
                if not broker_ready:
                    raise SkillSecurityError(
                        "network-capable Skills require the Harness network broker"
                    )
        if record.manifest.credentials:
            registry = self.credential_registry
            if registry is None:
                raise SkillSecurityError(
                    "Skill credentials require a configured credential registry"
                )
            unknown = registry.unknown(record.manifest.credentials)
            if unknown:
                raise SkillSecurityError(
                    "Skill requests unknown credential groups: "
                    + ", ".join(unknown)
                )
        approved = self.registry.get(tenant_id, agent_id, record.skill_id, record.version)
        if approved != record:
            raise SkillSecurityError("Skill approval record changed")
        skill_dir = _safe_target(skills_root, record.relative_path)
        script = _safe_target(skill_dir, record.manifest.entrypoint, require_file=True)
        try:
            actual_sha256 = hash_skill_tree(skill_dir)
        except SkillIntegrityError as exc:
            raise SkillSecurityError(str(exc)) from exc
        if actual_sha256 != record.sha256:
            raise SkillSecurityError("Skill artifact hash changed")
        workspace = workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        if not _within(workspace, workspace):
            raise SkillSecurityError("invalid Skill workspace")
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": str(workspace / ".skill-home"),
            "PI_WORKSPACE_ROOT": str(workspace),
            "SKILL_ID": record.skill_id,
            "SKILL_VERSION": record.version,
        }
        if uses_container:
            env["HOME"] = "/tmp/skill-home"
            env["PI_WORKSPACE_ROOT"] = "/workspace"
        else:
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            if (
                record.manifest.network
                and record.manifest.network_execution == "broker-client"
            ):
                env.update(
                    {
                        "HARNESS_NETWORK_BROKER_URL": self.broker_url.rstrip("/"),
                        "HARNESS_GATEWAY_URL": self.broker_url.rstrip("/"),
                        "HARNESS_GATEWAY_TOKEN": self.broker_token,
                        "HARNESS_TASK_ID": self.broker_task_id,
                        "HARNESS_TENANT_ID": self.broker_tenant_id,
                        "HARNESS_USER_ID": self.broker_user_id,
                    }
                )
            if record.manifest.credentials:
                try:
                    env.update(
                        self.credential_registry.resolve_all(  # type: ignore[union-attr]
                            record.manifest.credentials
                        )
                    )
                except CredentialError as exc:
                    raise SkillSecurityError(str(exc)) from exc
        try:
            if uses_container:
                command = list(
                    build_container_argv(
                        self.container_profile,
                        # Mount the resolved installation itself.  The
                        # registry entry under `.harness` is a symlink in
                        # the backend container and is not meaningful inside
                        # the nested Docker container launched on the Mac
                        # host.
                        skill_root=skill_dir,
                        workspace=workspace,
                        entrypoint=record.manifest.entrypoint,
                        runtime=record.manifest.runtime,
                        filesystem=record.manifest.filesystem,
                        environment=env,
                        runtime_root=container_runtime_root,
                        host_runtime_root=container_host_runtime_root,
                    )
                )
            else:
                command = [*self._interpreter(record.manifest.runtime), str(script)]
        except SkillRuntimeConfigError as exc:
            raise SkillSecurityError(str(exc)) from exc
        try:
            process = subprocess.Popen(
                command,
                cwd=str(workspace),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                text=True,
            )
            stdout, stderr = process.communicate(
                json.dumps(input_data, ensure_ascii=False), timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise SkillSecurityError(f"Skill runtime is unavailable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            self._terminate(process)
            stdout, stderr = process.communicate()
            return SkillRunResult(
                returncode=process.returncode or -signal.SIGTERM,
                stdout=self._limit_output(stdout),
                stderr=self._limit_output(stderr or str(exc)),
                timed_out=True,
            )
        return SkillRunResult(
            returncode=process.returncode,
            stdout=self._limit_output(stdout),
            stderr=self._limit_output(stderr),
        )

    @staticmethod
    def _interpreter(runtime: SkillRuntime) -> list[str]:
        if runtime == "python":
            return [sys.executable]
        if runtime == "bash":
            return ["/bin/bash"]
        for candidate in ("/usr/local/bin/node", "/usr/bin/node", "/opt/homebrew/bin/node"):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return [candidate]
        raise SkillSecurityError("fixed Node interpreter is unavailable")

    def _limit_output(self, value: str) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output_bytes:
            return value
        return encoded[-self.max_output_bytes :].decode("utf-8", errors="replace")

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            process.kill()
