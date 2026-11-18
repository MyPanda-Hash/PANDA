"""Quarantine, scan, approval, and installation for external Skills."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import stat
import tarfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from app.services.harness.skill_integrity import (
    SkillIntegrityError,
    hash_skill_tree,
)
from app.services.harness.skill_definition import (
    SkillDefinitionError,
    load_skill_definition,
)


class SkillInstallError(RuntimeError):
    """Skill artifact failed lifecycle or safety validation."""


class SkillInstallNotFoundError(SkillInstallError):
    """当前租户不存在指定安装申请。"""


class SkillInstallConflictError(SkillInstallError):
    """当前生命周期状态不允许该操作。"""


def _utc_now() -> str:
    """使用带 UTC 时区的 ISO 8601 格式保存真实操作时间。"""
    return datetime.now(timezone.utc).isoformat()


def _optional_timestamp(value: Any) -> str | None:
    """读取历史时间字段，不为旧记录补造时间。"""
    if value is None:
        return None
    text = str(value)
    return text or None


@contextlib.contextmanager
def installation_state_lock(root: Path):
    """Serialize lifecycle mutations and runtime publication across processes."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".install-state.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _locked_mutation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with installation_state_lock(self.root):
            self._load()
            return method(self, *args, **kwargs)

    return wrapped


class InstallState(str, Enum):
    REQUESTED = "requested"
    QUARANTINED = "quarantined"
    SCANNED = "scanned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    INSTALLED = "installed"
    MOUNTED = "mounted"
    ENABLED = "enabled"
    REVOKED = "revoked"
    FAILED = "failed"


@dataclass(slots=True)
class SkillInstallRecord:
    request_id: str
    tenant_id: str
    source_name: str
    state: InstallState
    quarantine_path: str
    installed_path: str = ""
    scan_report: dict[str, Any] = field(default_factory=dict)
    skill_id: str = ""
    version: str = ""
    scanned_sha256: str = ""
    approved_sha256: str = ""
    created_at: str | None = None
    state_changed_at: str | None = None
    approved_at: str | None = None
    enabled_at: str | None = None
    revoked_at: str | None = None
    archived_at: str | None = None
    pending_audits: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self, *, include_pending_audits: bool = False) -> dict[str, Any]:
        result = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "source_name": self.source_name,
            "state": self.state.value,
            "quarantine_path": self.quarantine_path,
            "installed_path": self.installed_path,
            "scan_report": self.scan_report,
            "skill_id": self.skill_id,
            "version": self.version,
            "scanned_sha256": self.scanned_sha256,
            "approved_sha256": self.approved_sha256,
            "created_at": self.created_at,
            "state_changed_at": self.state_changed_at,
            "approved_at": self.approved_at,
            "enabled_at": self.enabled_at,
            "revoked_at": self.revoked_at,
            "archived_at": self.archived_at,
        }
        if include_pending_audits:
            result["pending_audits"] = self.pending_audits
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SkillInstallRecord:
        return cls(
            request_id=str(raw["request_id"]),
            tenant_id=str(raw["tenant_id"]),
            source_name=str(raw["source_name"]),
            state=InstallState(str(raw["state"])),
            quarantine_path=str(raw["quarantine_path"]),
            installed_path=str(raw.get("installed_path", "")),
            scan_report=dict(raw.get("scan_report") or {}),
            skill_id=str(raw.get("skill_id", "")),
            version=str(raw.get("version", "")),
            scanned_sha256=str(raw.get("scanned_sha256", "")),
            approved_sha256=str(raw.get("approved_sha256", "")),
            created_at=_optional_timestamp(raw.get("created_at")),
            state_changed_at=_optional_timestamp(raw.get("state_changed_at")),
            approved_at=_optional_timestamp(raw.get("approved_at")),
            enabled_at=_optional_timestamp(raw.get("enabled_at")),
            revoked_at=_optional_timestamp(raw.get("revoked_at")),
            archived_at=_optional_timestamp(raw.get("archived_at")),
            pending_audits=list(raw.get("pending_audits") or []),
        )


@dataclass(slots=True)
class SkillInstaller:
    root: Path
    max_files: int = 2_000
    max_bytes: int = 50 * 1024 * 1024
    max_enabled_files: int = 20_000
    max_enabled_bytes: int = 500 * 1024 * 1024
    quarantine_root: Path = field(init=False)
    install_root: Path = field(init=False)
    state_path: Path = field(init=False)
    _records: dict[str, SkillInstallRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.quarantine_root = self.root / "quarantine"
        self.install_root = self.root / "installed"
        self.state_path = self.root / "install-state.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    @_locked_mutation
    def request(
        self,
        tenant_id: str,
        source: Path,
        *,
        skill_id: str = "",
        version: str = "",
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        if source.is_symlink():
            raise SkillInstallError("Skill source does not exist or is a symlink")
        source = source.resolve()
        if not source.exists():
            raise SkillInstallError("Skill source does not exist or is a symlink")
        request_id = f"skill-{uuid.uuid4().hex[:16]}"
        target = self.quarantine_root / request_id / source.name
        target.parent.mkdir(parents=True, exist_ok=False)
        if source.is_dir():
            self._copy_tree_without_links(source, target)
        elif source.is_file() and source.suffix.lower() in {".zip", ".tar", ".tgz", ".gz"}:
            shutil.copyfile(source, target)
        else:
            raise SkillInstallError("Skill source must be a directory or ZIP/TAR archive")
        now = _utc_now()
        record = SkillInstallRecord(
            request_id=request_id,
            tenant_id=self._safe_segment(tenant_id, "tenant_id"),
            source_name=source.name,
            state=InstallState.QUARANTINED,
            quarantine_path=str(target),
            skill_id=(self._safe_segment(skill_id, "skill_id") if skill_id else ""),
            version=(self._safe_segment(version, "version") if version else ""),
            created_at=now,
            state_changed_at=now,
        )
        self._records[request_id] = record
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def request_remote(
        self,
        tenant_id: str,
        *,
        source_name: str,
        skill_id: str,
        version: str = "",
        source_metadata: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        """Reserve a request before a backend fetch begins.

        The request is persisted before any network work so an Agent can
        receive a durable request id without receiving installation rights.
        """
        safe_tenant = self._safe_segment(tenant_id, "tenant_id")
        safe_source_name = self._safe_segment(source_name, "source_name")
        safe_skill_id = self._safe_segment(skill_id, "skill_id")
        safe_version = self._safe_segment(version, "version") if version else ""
        request_id = f"skill-{uuid.uuid4().hex[:16]}"
        quarantine_path = self.quarantine_root / request_id / "source"
        quarantine_path.parent.mkdir(parents=True, exist_ok=False)
        now = _utc_now()
        record = SkillInstallRecord(
            request_id=request_id,
            tenant_id=safe_tenant,
            source_name=safe_source_name,
            state=InstallState.REQUESTED,
            quarantine_path=str(quarantine_path),
            scan_report={"source": dict(source_metadata or {})},
            skill_id=safe_skill_id,
            version=safe_version,
            created_at=now,
            state_changed_at=now,
        )
        self._records[request_id] = record
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def stage_remote_source(
        self,
        request_id: str,
        source: Path,
        *,
        source_metadata: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        """Copy a fetched source into the reserved quarantine path."""
        record = self.get(request_id)
        self._require_state(record, InstallState.REQUESTED)
        if source.is_symlink():
            raise SkillInstallError("Skill source does not exist or is a symlink")
        source = source.resolve()
        if not source.exists():
            raise SkillInstallError("Skill source does not exist or is a symlink")
        target = Path(record.quarantine_path)
        if target.exists():
            raise SkillInstallError("Skill quarantine target already exists")
        if source.is_dir():
            self._copy_tree_without_links(source, target)
        elif source.is_file() and source.suffix.lower() in {".zip", ".tar", ".tgz", ".gz"}:
            shutil.copyfile(source, target)
        else:
            raise SkillInstallError("Skill source must be a directory or ZIP/TAR archive")
        record.scan_report = {
            **record.scan_report,
            "source": {**record.scan_report.get("source", {}), **(source_metadata or {})},
        }
        self._transition(record, InstallState.QUARANTINED)
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def fail_request(
        self,
        request_id: str,
        message: str,
        *,
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        """Persist a bounded fetch/scan failure for administrator visibility."""
        record = self.get(request_id)
        if record.state not in {InstallState.REQUESTED, InstallState.QUARANTINED}:
            raise SkillInstallError(
                f"cannot fail Skill request in state {record.state.value}"
            )
        record.scan_report = {
            **record.scan_report,
            "error": str(message)[:500],
        }
        self._transition(record, InstallState.FAILED)
        self._save_record(record, audit)
        return record

    def get(self, request_id: str) -> SkillInstallRecord:
        try:
            return self._records[request_id]
        except KeyError as exc:
            raise SkillInstallError("unknown Skill installation request") from exc

    def list(self) -> list[SkillInstallRecord]:
        """Return installation records in stable request order."""
        return sorted(self._records.values(), key=lambda record: record.request_id)

    @_locked_mutation
    def scan(
        self, request_id: str, *, audit: dict[str, Any] | None = None
    ) -> SkillInstallRecord:
        record = self.get(request_id)
        self._require_state(record, InstallState.QUARANTINED)
        source = Path(record.quarantine_path)
        report = self._scan(source)
        content = self._materialize(source, record.request_id)
        skill_definitions = [
            path
            for path in content.rglob("SKILL.md")
            if path.is_file() and not path.is_symlink()
        ]
        if len(skill_definitions) != 1:
            raise SkillInstallError("Skill artifact must contain exactly one SKILL.md")
        try:
            definition = load_skill_definition(skill_definitions[0])
        except SkillDefinitionError as exc:
            raise SkillInstallError(str(exc)) from exc
        if record.skill_id and record.skill_id != definition.name:
            raise SkillInstallError(
                "SKILL.md name does not match the requested Skill identity"
            )
        from app.services.harness.skill_adaptation import analyze_skill

        report["adaptation"] = analyze_skill(content).as_dict()
        report["definition"] = definition.as_dict()
        manifest = self._optional_manifest(content)
        if manifest is not None:
            self._validate_manifest_identity(record, manifest, definition.name)
            report["manifest"] = manifest
        try:
            record.scanned_sha256 = hash_skill_tree(content)
        except SkillIntegrityError as exc:
            raise SkillInstallError(str(exc)) from exc
        source_metadata = record.scan_report.get("source")
        record.scan_report = {
            **report,
            **({"source": source_metadata} if isinstance(source_metadata, dict) else {}),
        }
        self._transition(record, InstallState.SCANNED)
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def adapt(
        self,
        request_id: str,
        skill_id: str,
        version: str,
        *,
        audit: dict[str, Any] | None = None,
    ) -> tuple[SkillInstallRecord, dict[str, Any]]:
        """Analyze a scanned Skill and assign its immutable identity.

        Admission never rewrites standard Skill content. The analysis is review
        metadata only; approval hashes the exact tree supplied by the author.
        """
        from app.services.harness.skill_adaptation import analyze_skill

        record = self.get(request_id)
        self._require_state(record, InstallState.SCANNED)
        content = self._materialize(Path(record.quarantine_path), record.request_id)
        self._verify_scanned_content(record, content)
        requested_skill_id = self._safe_segment(skill_id, "skill_id")
        requested_version = self._safe_segment(version, "version")
        definition = self._definition(content)
        if requested_skill_id != definition.name:
            raise SkillInstallError(
                "requested Skill identity does not match SKILL.md frontmatter name"
            )
        if (
            (record.skill_id and record.skill_id != requested_skill_id)
            or (record.version and record.version != requested_version)
        ):
            raise SkillInstallError(
                "requested Skill identity does not match the installation request"
            )
        report = analyze_skill(content).as_dict()
        manifest = self._optional_manifest(content)
        if manifest is not None:
            manifest_skill_id = self._safe_segment(
                str(manifest["skill_id"]), "skill_id"
            )
            manifest_version = self._safe_segment(
                str(manifest["version"]), "version"
            )
            if (
                requested_skill_id != manifest_skill_id
                or requested_version != manifest_version
            ):
                raise SkillInstallError(
                    "Skill manifest identity does not match the requested identity"
                )
        record.skill_id = requested_skill_id
        record.version = requested_version
        result = {"report": report, "manifest": manifest}
        record.scan_report = {
            **record.scan_report,
            "adaptation_request": {
                "skill_id": skill_id,
                "version": version,
            },
            "adaptation": report,
        }
        self._transition(record, InstallState.AWAITING_APPROVAL)
        self._save_record(record, audit)
        return record, result

    @_locked_mutation
    def approve(
        self, request_id: str, *, audit: dict[str, Any] | None = None
    ) -> SkillInstallRecord:
        record = self.get(request_id)
        self._require_state(record, InstallState.AWAITING_APPROVAL)
        content = self._materialize(Path(record.quarantine_path), record.request_id)
        scanned_sha256 = self._verify_scanned_content(record, content)
        definition = self._definition(content)
        if record.skill_id != definition.name:
            raise SkillInstallError(
                "approved Skill identity does not match SKILL.md frontmatter name"
            )
        manifest = self._optional_manifest(content)
        if manifest is not None:
            self._validate_manifest_identity(record, manifest, definition.name)
        if not record.skill_id or not record.version:
            raise SkillInstallError("Skill identity is missing; adapt before approval")
        record.approved_sha256 = scanned_sha256
        self._transition(record, InstallState.APPROVED, approved=True)
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def install(
        self, request_id: str, *, audit: dict[str, Any] | None = None
    ) -> SkillInstallRecord:
        record = self.get(request_id)
        self._require_state(record, InstallState.APPROVED)
        source = Path(record.quarantine_path)
        content = self._materialize(source, record.request_id)
        self._verify_approved_content(record, content)
        manifest = self._optional_manifest(content)
        if manifest is None:
            analysis = record.scan_report.get("adaptation") or {}
            record.scan_report = {
                **record.scan_report,
                "prompt_only": bool(analysis.get("prompt_only")),
            }
        target = (
            self.install_root / record.tenant_id / record.skill_id / record.version
        )
        if target.exists():
            raise SkillInstallError("Skill version is already installed")
        target.parent.mkdir(parents=True, exist_ok=True)
        content.replace(target)
        record.installed_path = str(target)
        self._transition(record, InstallState.INSTALLED)
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def mount(
        self,
        request_id: str,
        _mount_path: Path,
        *,
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        record = self.get(request_id)
        self._require_state(record, InstallState.INSTALLED)
        source = Path(record.installed_path)
        if source.is_symlink():
            raise SkillInstallError("invalid installed Skill")
        self._verify_approved_content(record, source)
        if not source.is_dir():
            raise SkillInstallError("invalid installed Skill")
        self._transition(record, InstallState.MOUNTED)
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def enable(
        self, request_id: str, *, audit: dict[str, Any] | None = None
    ) -> SkillInstallRecord:
        record = self.get(request_id)
        if record.state not in {InstallState.MOUNTED, InstallState.REVOKED}:
            raise SkillInstallError(
                "invalid Skill state transition: "
                f"{record.state.value} → {InstallState.ENABLED.value}"
            )
        self._verify_approved_content(record, Path(record.installed_path))
        manifest = self._optional_manifest(Path(record.installed_path))
        if manifest is not None:
            capabilities = manifest.get("capabilities") or {}
            if not isinstance(capabilities, dict):
                raise SkillInstallError("Skill manifest capabilities must be an object")
            credentials = capabilities.get("credentials") or manifest.get(
                "credentials"
            )
            if credentials:
                raise SkillInstallError(
                    "native sandbox Skills cannot receive long-lived credentials; "
                    "use a task-scoped brokered capability"
                )
        enabled_files = 0
        enabled_bytes = 0
        for active in self._records.values():
            if active.request_id != request_id and active.state != InstallState.ENABLED:
                continue
            files, size = self._scan_tree(
                Path(active.installed_path), shared_inventory=True
            )
            enabled_files += files
            enabled_bytes += size
        if enabled_files > self.max_enabled_files:
            raise SkillInstallError("enabled Skills exceed the shared file quota")
        if enabled_bytes > self.max_enabled_bytes:
            raise SkillInstallError("enabled Skills exceed the shared byte quota")
        for other in self._records.values():
            if (
                other.request_id != request_id
                and other.state == InstallState.ENABLED
                and other.skill_id == record.skill_id
            ):
                raise SkillInstallError(
                    f"Skill is already enabled: {record.skill_id}@{other.version}"
                )
        self._transition(record, InstallState.ENABLED, enabled=True)
        # 重新启用时恢复默认列表可见性，不删除既有生命周期时间。
        record.archived_at = None
        self._save_record(record, audit)
        return record

    @_locked_mutation
    def revoke(
        self,
        skill_id: str,
        version: str,
        *,
        audit: dict[str, Any] | None = None,
    ) -> SkillInstallRecord:
        for record in self._records.values():
            if (
                record.skill_id == skill_id
                and record.version == version
                and record.state == InstallState.ENABLED
            ):
                self._transition(record, InstallState.REVOKED, revoked=True)
                self._save_record(record, audit)
                return record
        raise SkillInstallError(f"Skill is not enabled: {skill_id}@{version}")

    @_locked_mutation
    def archive(
        self,
        request_ids: Sequence[str],
        archived: bool,
        *,
        audit: dict[str, Any] | None = None,
    ) -> list[SkillInstallRecord]:
        """整批校验后原子归档或恢复；仅已撤销、失败记录可归档。

        重复请求不改写归档时间，也不重复产生审计事件。
        """
        targets: list[SkillInstallRecord] = []
        seen: set[str] = set()
        for request_id in request_ids:
            if request_id in seen:
                continue
            seen.add(request_id)
            record = self._records.get(request_id)
            if record is None:
                raise SkillInstallNotFoundError(
                    "unknown Skill installation request"
                )
            targets.append(record)
        if archived:
            for record in targets:
                if record.state not in {
                    InstallState.REVOKED,
                    InstallState.FAILED,
                }:
                    raise SkillInstallConflictError(
                        "cannot archive Skill request in state "
                        f"{record.state.value}"
                    )
        changed: list[SkillInstallRecord] = []
        for record in targets:
            if archived:
                if record.archived_at is not None:
                    continue
                record.archived_at = _utc_now()
            else:
                if record.archived_at is None:
                    continue
                record.archived_at = None
            changed.append(record)
            if audit:
                self._append_audit(record, audit)
        if changed:
            self._save()
        return changed

    @staticmethod
    def _verify_approved_content(record: SkillInstallRecord, root: Path) -> None:
        if not record.approved_sha256:
            raise SkillInstallError("Skill requires approval with a content digest")
        try:
            actual = hash_skill_tree(root)
        except SkillIntegrityError as exc:
            raise SkillInstallError(str(exc)) from exc
        if actual != record.approved_sha256:
            raise SkillInstallError("approved Skill content changed")

    @staticmethod
    def _verify_scanned_content(record: SkillInstallRecord, root: Path) -> str:
        if not record.scanned_sha256:
            raise SkillInstallError("Skill requires a fresh scan with a content digest")
        try:
            actual = hash_skill_tree(root)
        except SkillIntegrityError as exc:
            raise SkillInstallError(str(exc)) from exc
        if actual != record.scanned_sha256:
            raise SkillInstallError("Skill content changed after scanning")
        return actual

    def _scan(self, source: Path) -> dict[str, Any]:
        if source.is_dir():
            files, total_bytes = self._scan_tree(source)
        elif zipfile.is_zipfile(source):
            files, total_bytes = self._scan_zip(source)
        elif tarfile.is_tarfile(source):
            files, total_bytes = self._scan_tar(source)
        else:
            raise SkillInstallError("unsupported Skill artifact")
        return {"files": files, "bytes": total_bytes, "clean": True}

    def _scan_tree(
        self, root: Path, *, shared_inventory: bool = False
    ) -> tuple[int, int]:
        count = 0
        total = 0
        for path in root.rglob("*"):
            self._validate_relative(root, path)
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise SkillInstallError("Skill contains symlink or special file")
            if path.is_file():
                self._validate_name(path.name)
                count += 1
                total += path.stat().st_size
                self._check_limits(count, total, shared_inventory=shared_inventory)
        return count, total

    def _scan_zip(self, source: Path) -> tuple[int, int]:
        count = 0
        total = 0
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                self._validate_archive_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO}:
                    raise SkillInstallError("archive contains symlink or device file")
                if not info.is_dir():
                    self._validate_name(Path(info.filename).name)
                    count += 1
                    total += info.file_size
                    self._check_limits(count, total)
        return count, total

    def _scan_tar(self, source: Path) -> tuple[int, int]:
        count = 0
        total = 0
        with tarfile.open(source) as archive:
            for info in archive.getmembers():
                self._validate_archive_name(info.name)
                if not (info.isfile() or info.isdir()):
                    raise SkillInstallError("archive contains symlink or special file")
                if info.isfile():
                    self._validate_name(Path(info.name).name)
                    count += 1
                    total += info.size
                    self._check_limits(count, total)
        return count, total

    def _materialize(self, source: Path, request_id: str) -> Path:
        target = self.quarantine_root / request_id / "materialized"
        if target.exists():
            return target
        if source.is_dir():
            self._copy_tree_without_links(source, target)
        elif zipfile.is_zipfile(source):
            target.mkdir(parents=True)
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    destination = self._safe_extract_target(target, info.filename)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(info))
                    mode = (info.external_attr >> 16) & 0o777
                    if mode:
                        destination.chmod(mode)
        else:
            target.mkdir(parents=True)
            with tarfile.open(source) as archive:
                for info in archive.getmembers():
                    if not info.isfile():
                        continue
                    destination = self._safe_extract_target(target, info.name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(info)
                    if stream is None:
                        raise SkillInstallError("cannot extract Skill file")
                    destination.write_bytes(stream.read())
                    destination.chmod(info.mode & 0o777)
        return target

    def _find_manifest(self, root: Path) -> dict[str, Any]:
        candidates = list(root.rglob("skill.manifest.json"))
        if len(candidates) != 1:
            raise SkillInstallError("Skill must contain exactly one skill.manifest.json")
        try:
            value = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillInstallError("invalid Skill manifest") from exc
        if not isinstance(value, dict) or not value.get("skill_id") or not value.get("version"):
            raise SkillInstallError("Skill manifest identity is incomplete")
        return value

    def _optional_manifest(self, root: Path) -> dict[str, Any] | None:
        if not list(root.rglob("skill.manifest.json")):
            return None
        return self._find_manifest(root)

    def _validate_manifest_identity(
        self,
        record: SkillInstallRecord,
        manifest: dict[str, Any],
        definition_name: str,
    ) -> None:
        manifest_skill_id = self._safe_segment(
            str(manifest["skill_id"]), "skill_id"
        )
        manifest_version = self._safe_segment(str(manifest["version"]), "version")
        if (
            (record.skill_id and record.skill_id != manifest_skill_id)
            or (record.version and record.version != manifest_version)
            or manifest_skill_id != definition_name
        ):
            raise SkillInstallError(
                "Skill manifest identity does not match the requested identity"
            )

    @staticmethod
    def _definition(root: Path):
        markers = [
            path
            for path in root.rglob("SKILL.md")
            if path.is_file() and not path.is_symlink()
        ]
        if len(markers) != 1:
            raise SkillInstallError("Skill artifact must contain exactly one SKILL.md")
        try:
            return load_skill_definition(markers[0])
        except SkillDefinitionError as exc:
            raise SkillInstallError(str(exc)) from exc

    def _copy_tree_without_links(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        target.chmod(source.stat().st_mode & 0o777)
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            destination = target / relative
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise SkillInstallError("Skill contains symlink or special file")
            if path.is_dir():
                destination.mkdir()
                destination.chmod(path.stat().st_mode & 0o777)
            else:
                destination.write_bytes(path.read_bytes())
                destination.chmod(path.stat().st_mode & 0o777)

    def _safe_extract_target(self, root: Path, raw: str) -> Path:
        relative = self._validate_archive_name(raw)
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise SkillInstallError("archive path escapes quarantine") from exc
        return target

    @staticmethod
    def _validate_archive_name(raw: str) -> str:
        normalized = raw.replace("\\", "/").rstrip("/")
        if normalized.startswith("/") or any(part in {"", ".", ".."} for part in normalized.split("/")):
            raise SkillInstallError("archive contains path traversal")
        return normalized

    @staticmethod
    def _validate_relative(root: Path, path: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SkillInstallError("Skill path escapes quarantine") from exc

    @staticmethod
    def _validate_name(name: str) -> None:
        if name.lower() in {".env", "credentials", "secret", "id_rsa"} or name.lower().endswith((".pem", ".key", ".p12", ".pfx")):
            raise SkillInstallError("Skill contains sensitive file")

    def _check_limits(
        self, count: int, total: int, *, shared_inventory: bool = False
    ) -> None:
        # 已准入的内置能力可能采用不同上传额度；库存只套用共享配额。
        max_files = self.max_enabled_files if shared_inventory else self.max_files
        max_bytes = self.max_enabled_bytes if shared_inventory else self.max_bytes
        if count > max_files:
            raise SkillInstallError("Skill contains too many files")
        if total > max_bytes:
            raise SkillInstallError("Skill exceeds the size limit")

    @staticmethod
    def _safe_segment(value: str, label: str) -> str:
        if (
            not value
            or value in {".", ".."}
            or len(value) > 128
            or any(
                char
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for char in value
            )
        ):
            raise SkillInstallError(f"invalid {label}")
        return value

    def _transition(
        self,
        record: SkillInstallRecord,
        state: InstallState,
        *,
        approved: bool = False,
        enabled: bool = False,
        revoked: bool = False,
    ) -> None:
        """记录真实状态变更时间；归档不属于生命周期状态变更。"""
        now = _utc_now()
        record.state = state
        record.state_changed_at = now
        if approved:
            record.approved_at = now
        if enabled:
            record.enabled_at = now
        if revoked:
            record.revoked_at = now

    @staticmethod
    def _require_state(record: SkillInstallRecord, expected: InstallState) -> None:
        if record.state != expected:
            raise SkillInstallError(
                f"invalid Skill state transition: {record.state.value} → {expected.value}"
            )

    def _save(self) -> None:
        payload = json.dumps(
            {
                request_id: record.as_dict(include_pending_audits=True)
                for request_id, record in self._records.items()
            },
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _append_audit(
        self, record: SkillInstallRecord, audit: dict[str, Any]
    ) -> None:
        event = {
            "id": str(audit.get("id") or f"haud_skill_{uuid.uuid4().hex}"),
            "event_type": str(audit["event_type"]),
            "task_id": f"skill-install:{record.request_id}",
            "actor_id": str(audit.get("actor_id") or ""),
            "details": dict(audit.get("details") or record.as_dict()),
        }
        record.pending_audits.append(event)

    def _save_record(
        self, record: SkillInstallRecord, audit: dict[str, Any] | None
    ) -> None:
        if audit:
            self._append_audit(record, audit)
        self._save()

    @_locked_mutation
    def flush_pending_audits(
        self, deliver: Callable[[dict[str, Any]], None]
    ) -> int:
        """Idempotently deliver durable audit intents, then acknowledge them."""
        delivered = 0
        for record in self._records.values():
            while record.pending_audits:
                deliver(dict(record.pending_audits[0]))
                record.pending_audits.pop(0)
                delivered += 1
        if delivered:
            self._save()
        return delivered

    def _load(self) -> None:
        self._records = {}
        if not self.state_path.is_file():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._records = {
                key: SkillInstallRecord.from_dict(value) for key, value in raw.items()
            }
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise SkillInstallError("cannot read Skill installation state") from exc


def sort_install_records_newest_first(
    records: Iterable[SkillInstallRecord],
) -> list[SkillInstallRecord]:
    """管理列表按提交时间倒序，未知时间最后、同时间按申请 ID 排序。

    内部 SkillInstaller.list 的顺序保持不变。
    """

    def key(record: SkillInstallRecord) -> tuple[int, float, str]:
        created = record.created_at
        if not created:
            return (1, 0.0, record.request_id)
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return (1, 0.0, record.request_id)
        return (0, -parsed.timestamp(), record.request_id)

    return sorted(records, key=key)


def review_identity(record: SkillInstallRecord) -> tuple[str, str]:
    """Resolve Skill identity from the request, SKILL.md, or optional manifest."""
    definition = record.scan_report.get("definition") or {}
    manifest = record.scan_report.get("manifest") or {}
    skill_id = record.skill_id or str(definition.get("name") or "")
    version = record.version or ""
    if not version and isinstance(manifest, dict):
        version = str(manifest.get("version") or "")
    if not version:
        version = "1.0.0"
    if not skill_id:
        raise SkillInstallError("Skill identity is missing after scan")
    return skill_id, version


def prepare_install_for_review(
    installer: SkillInstaller,
    request_id: str,
    *,
    scan_audit: dict[str, Any] | None = None,
    adapt_audit: dict[str, Any] | None = None,
) -> SkillInstallRecord:
    """Run scan + identity registration up to the human approval gate."""
    record = installer.get(request_id)
    if record.state == InstallState.QUARANTINED:
        record = installer.scan(request_id, audit=scan_audit)
    if record.state == InstallState.SCANNED:
        skill_id, version = review_identity(record)
        record, _ = installer.adapt(
            request_id, skill_id, version, audit=adapt_audit
        )
    if record.state != InstallState.AWAITING_APPROVAL:
        raise SkillInstallError(
            "invalid Skill state transition: "
            f"{record.state.value} → {InstallState.AWAITING_APPROVAL.value}"
        )
    return record


def promote_install_to_enabled(
    installer: SkillInstaller,
    request_id: str,
    mount_path: Path,
    *,
    approve_audit: dict[str, Any] | None = None,
    install_audit: dict[str, Any] | None = None,
    mount_audit: dict[str, Any] | None = None,
    enable_audit: dict[str, Any] | None = None,
) -> SkillInstallRecord:
    """Apply the remaining mechanical steps after (or including) approval."""
    record = installer.get(request_id)
    if record.state == InstallState.AWAITING_APPROVAL:
        record = installer.approve(request_id, audit=approve_audit)
    if record.state == InstallState.APPROVED:
        record = installer.install(request_id, audit=install_audit)
    if record.state == InstallState.INSTALLED:
        record = installer.mount(request_id, mount_path, audit=mount_audit)
    if record.state in {InstallState.MOUNTED, InstallState.REVOKED}:
        record = installer.enable(request_id, audit=enable_audit)
    if record.state != InstallState.ENABLED:
        raise SkillInstallError(
            "invalid Skill state transition: "
            f"{record.state.value} → {InstallState.ENABLED.value}"
        )
    return record
