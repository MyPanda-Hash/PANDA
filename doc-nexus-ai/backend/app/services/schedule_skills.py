"""Chat-owned Skill candidates routed through the existing admission lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.harness.skill_installation import (
    InstallState,
    SkillInstallError,
    SkillInstaller,
    installation_state_lock,
    prepare_install_for_review,
)
from app.services.pi_runtime.config import PiRuntimeSettings

_SYSTEM_TENANT = "_system"
_MAX_FILES = 32
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*\Z")


class ScheduleSkillError(ValueError):
    """A safe, customer-displayable candidate validation failure."""


@dataclass(frozen=True, slots=True)
class ScheduleSkillApplication:
    id: str
    owner_user_id: str
    source_session_id: str
    source_message_id: str
    request_key: str
    install_request_id: str
    skill_id: str
    version: str
    description: str
    created_at: str
    candidate_sha256: str


@dataclass(frozen=True, slots=True)
class ScheduleSkillFile:
    path: str
    content: str


def _runtime() -> PiRuntimeSettings:
    return PiRuntimeSettings.from_env()


def _application_root(runtime: PiRuntimeSettings) -> Path:
    return runtime.root / "schedule-skill-applications"


def _state_path(runtime: PiRuntimeSettings) -> Path:
    return _application_root(runtime) / "applications.json"


def _read_state(runtime: PiRuntimeSettings) -> dict[str, dict[str, Any]]:
    path = _state_path(runtime)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScheduleSkillError("Skill 申请状态暂时不可用") from exc
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, dict) for key, value in raw.items()
    ):
        raise ScheduleSkillError("Skill 申请状态暂时不可用")
    return raw


def _write_state(runtime: PiRuntimeSettings, state: dict[str, dict[str, Any]]) -> None:
    root = _application_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    target = _state_path(runtime)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _application(raw: dict[str, Any]) -> ScheduleSkillApplication:
    try:
        return ScheduleSkillApplication(
            id=str(raw["id"]),
            owner_user_id=str(raw["owner_user_id"]),
            source_session_id=str(raw["source_session_id"]),
            source_message_id=str(raw["source_message_id"]),
            request_key=str(raw["request_key"]),
            install_request_id=str(raw["install_request_id"]),
            skill_id=str(raw["skill_id"]),
            version=str(raw["version"]),
            description=str(raw["description"]),
            created_at=str(raw["created_at"]),
            candidate_sha256=str(raw["candidate_sha256"]),
        )
    except KeyError as exc:
        raise ScheduleSkillError("Skill 申请状态暂时不可用") from exc


def _safe_candidate_files(files: list[ScheduleSkillFile]) -> list[ScheduleSkillFile]:
    if len(files) > _MAX_FILES:
        raise ScheduleSkillError("Skill 候选文件过多")
    seen: set[str] = set()
    safe: list[ScheduleSkillFile] = []
    total = 0
    for item in files:
        normalized = item.path.replace("\\", "/").strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or len(normalized) > 256
            or len(path.parts) > 16
            or path.is_absolute()
            or any(
                part in {"", ".", ".."} or part.startswith(".") for part in path.parts
            )
            or normalized == "SKILL.md"
        ):
            raise ScheduleSkillError("Skill 候选包含无效文件路径")
        if normalized in seen:
            raise ScheduleSkillError("Skill 候选包含重复文件路径")
        try:
            payload = item.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ScheduleSkillError("Skill 候选文件必须是 UTF-8 文本") from exc
        if len(payload) > _MAX_FILE_BYTES:
            raise ScheduleSkillError("Skill 候选单个文件过大")
        total += len(payload)
        if total > _MAX_TOTAL_BYTES:
            raise ScheduleSkillError("Skill 候选总大小过大")
        seen.add(normalized)
        safe.append(ScheduleSkillFile(normalized, item.content))
    return safe


def _skill_markdown(skill_id: str, description: str, instructions: str) -> str:
    safe_description = " ".join(description.strip().split())
    if not safe_description or len(safe_description) > 1024:
        raise ScheduleSkillError("Skill 用途说明无效")
    body = instructions.strip()
    if not body or len(body.encode("utf-8")) > _MAX_FILE_BYTES:
        raise ScheduleSkillError("Skill 行为说明无效")
    quoted = json.dumps(safe_description, ensure_ascii=False)
    return f"---\nname: {skill_id}\ndescription: {quoted}\n---\n\n{body}\n"


def _prepare_pending_review(installer: SkillInstaller, request_id: str) -> None:
    """中断后继续同一隔离申请，只推进到人工审核，绝不自动批准。"""
    record = installer.get(request_id)
    if record.state not in {InstallState.QUARANTINED, InstallState.SCANNED}:
        return
    try:
        prepare_install_for_review(installer, request_id)
    except (OSError, ValueError, SkillInstallError):
        try:
            installer.fail_request(request_id, "候选扫描或适配失败")
        except SkillInstallError:
            pass


def submit_schedule_skill_application(
    *,
    owner_user_id: str,
    source_session_id: str,
    source_message_id: str,
    request_key: str,
    skill_id: str,
    version: str,
    description: str,
    instructions: str,
    files: list[ScheduleSkillFile],
) -> ScheduleSkillApplication:
    """Create one idempotent candidate and stop at the human approval gate."""
    skill_id = skill_id.strip()
    version = version.strip()
    request_key = request_key.strip()
    if not _SKILL_NAME.fullmatch(skill_id) or len(skill_id) > 64:
        raise ScheduleSkillError("Skill 标识只能使用小写字母、数字和单个连字符")
    if not _VERSION.fullmatch(version) or len(version) > 64:
        raise ScheduleSkillError("Skill 版本格式无效")
    if not request_key or len(request_key) > 128:
        raise ScheduleSkillError("缺少有效的申请幂等标识")
    safe_files = _safe_candidate_files(files)
    skill_md = _skill_markdown(skill_id, description, instructions)
    total = len(skill_md.encode("utf-8")) + sum(
        len(item.content.encode("utf-8")) for item in safe_files
    )
    if total > _MAX_TOTAL_BYTES:
        raise ScheduleSkillError("Skill 候选总大小过大")
    candidate_payload = {
        "skill_md": skill_md,
        "files": [{"path": item.path, "content": item.content} for item in safe_files],
    }
    candidate_sha256 = hashlib.sha256(
        json.dumps(
            candidate_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()

    runtime = _runtime()
    root = _application_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    identity = "\0".join(
        (owner_user_id, source_session_id, source_message_id, request_key)
    )
    application_id = "ssa_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    installer = SkillInstaller(runtime.root / "skill-installations" / _SYSTEM_TENANT)

    with installation_state_lock(root):
        state = _read_state(runtime)
        existing = state.get(application_id)
        if existing is not None:
            application = _application(existing)
            if (
                application.owner_user_id != owner_user_id
                or application.source_session_id != source_session_id
                or application.source_message_id != source_message_id
                or application.skill_id != skill_id
                or application.version != version
                or application.candidate_sha256 != candidate_sha256
            ):
                raise ScheduleSkillError("幂等标识已用于另一项 Skill 申请")
            try:
                installer.get(application.install_request_id)
            except SkillInstallError as exc:
                raise ScheduleSkillError(
                    "Skill 申请记录不完整，已安全阻止继续"
                ) from exc
            _prepare_pending_review(installer, application.install_request_id)
            return application
        for recovered in installer.list():
            # pending_audits 是可清空的投递队列，不能承担崩溃恢复身份。
            # 源目录名由服务端身份与候选摘要生成，并随安装记录持久保存。
            matching_source = recovered.source_name.startswith(f"{application_id}-")
            if not matching_source:
                continue
            if (
                recovered.skill_id != skill_id
                or recovered.version != version
                or recovered.source_name != f"{application_id}-{candidate_sha256}"
            ):
                raise ScheduleSkillError("幂等标识已用于另一项 Skill 申请")
            application = ScheduleSkillApplication(
                id=application_id,
                owner_user_id=owner_user_id,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                request_key=request_key,
                install_request_id=recovered.request_id,
                skill_id=skill_id,
                version=version,
                description=" ".join(description.strip().split()),
                created_at=recovered.created_at
                or datetime.now(timezone.utc).isoformat(),
                candidate_sha256=candidate_sha256,
            )
            state[application_id] = asdict(application)
            _write_state(runtime, state)
            _prepare_pending_review(installer, application.install_request_id)
            return application

        temporary_root = Path(tempfile.mkdtemp(prefix="schedule-skill-", dir=root))
        candidate = temporary_root / f"{application_id}-{candidate_sha256}"
        candidate.mkdir(mode=0o700)
        try:
            (candidate / "SKILL.md").write_text(skill_md, encoding="utf-8")
            for item in safe_files:
                target = candidate.joinpath(*PurePosixPath(item.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.content, encoding="utf-8")
            record = installer.request(
                _SYSTEM_TENANT,
                candidate,
                skill_id=skill_id,
                version=version,
                audit={
                    "event_type": "schedule_skill_requested",
                    "actor_id": owner_user_id,
                    "details": {
                        "application_id": application_id,
                        "size": total,
                        "candidate_sha256": candidate_sha256,
                    },
                },
            )
            application = ScheduleSkillApplication(
                id=application_id,
                owner_user_id=owner_user_id,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                request_key=request_key,
                install_request_id=record.request_id,
                skill_id=skill_id,
                version=version,
                description=" ".join(description.strip().split()),
                created_at=datetime.now(timezone.utc).isoformat(),
                candidate_sha256=candidate_sha256,
            )
            # 先保存 owner 映射；即使扫描失败，申请人仍能回读安全阻塞状态。
            state[application_id] = asdict(application)
            _write_state(runtime, state)
            _prepare_pending_review(installer, record.request_id)
            return application
        finally:
            import shutil

            shutil.rmtree(temporary_root, ignore_errors=True)


def get_schedule_skill_application(
    application_id: str, *, owner_user_id: str
) -> ScheduleSkillApplication | None:
    runtime = _runtime()
    root = _application_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    with installation_state_lock(root):
        raw = _read_state(runtime).get(application_id)
    if raw is None:
        return None
    application = _application(raw)
    return application if application.owner_user_id == owner_user_id else None


def list_schedule_skill_applications(
    *, owner_user_id: str, source_session_id: str | None = None
) -> list[ScheduleSkillApplication]:
    runtime = _runtime()
    root = _application_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    with installation_state_lock(root):
        applications = [_application(raw) for raw in _read_state(runtime).values()]
    return sorted(
        (
            item
            for item in applications
            if item.owner_user_id == owner_user_id
            and (
                source_session_id is None or item.source_session_id == source_session_id
            )
        ),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )


def schedule_skill_application_view(
    application: ScheduleSkillApplication,
) -> dict[str, Any]:
    """Return owner-safe lifecycle data without host paths or scanner diagnostics."""
    runtime = _runtime()
    installer = SkillInstaller(runtime.root / "skill-installations" / _SYSTEM_TENANT)
    try:
        record = installer.get(application.install_request_id)
    except SkillInstallError as exc:
        raise ScheduleSkillError("Skill 申请记录不完整，已安全阻止继续") from exc
    state = record.state.value
    from app.services.schedule_access import list_schedule_skills

    enabled = next(
        (
            item
            for item in list_schedule_skills()
            if item["id"] == application.skill_id
            and item["version"] == application.version
            and item["digest"] == record.approved_sha256
        ),
        None,
    )
    ready = (
        state == InstallState.ENABLED.value
        and bool(record.approved_sha256)
        and enabled is not None
    )
    if state == InstallState.FAILED.value:
        blocker = "Skill 候选未通过安全检查"
    elif state == InstallState.REVOKED.value:
        blocker = "Skill 已被停用"
    elif state == InstallState.ENABLED.value and not ready:
        blocker = "Skill 完整性校验未通过，已安全阻止继续"
    elif ready:
        blocker = None
    else:
        blocker = "Skill 正在等待审核和启用"
    return {
        "id": application.id,
        "skill_id": application.skill_id,
        "version": application.version,
        "description": application.description,
        "state": state,
        "ready": ready,
        "blocked_reason": blocker,
        "created_at": application.created_at,
        "review_path": f"/admin/harness-skills?request_id={application.install_request_id}",
        "approved_digest": str(enabled["digest"]) if ready and enabled else None,
    }
