"""Admin visibility and safe operations for the backend-owned Pi runtime."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import admin_user
from app.core.database import transaction
from app.repositories import audit
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID, PiRuntimeSettings
from app.services.pi_runtime.skills import (
    SkillBusyError,
    SkillNotFoundError,
    SkillOperationError,
    SkillPathError,
    agent_workspace_root,
    delete_agent_skill,
    list_agent_skills,
    published_skills_dir,
    skills_dir_for_agent,
)
from app.models.auth import User

router = APIRouter(prefix="/admin/pi", tags=["admin-pi-runtime"])

# Chat, writing and Harness all persist Pi workspaces under this tenant.
# The admin viewer must not key off the logged-in user's id, or the page
# reports "no session" while `_system/doc-nexus-pi/` already has history.
_PI_TENANT_ID = "_system"


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id=PI_RUNTIME_AGENT_ID,
        tenant_id=_PI_TENANT_ID,
    )


def _mount_status(mount: Path) -> str:
    if not mount.exists() and not mount.is_symlink():
        return "idle"
    if mount.is_dir() and not mount.is_symlink():
        return "mounted"
    return "invalid"


def _session_sort_key(session: Path) -> tuple[float, str]:
    try:
        return (session.stat().st_mtime, session.name)
    except OSError:
        return (0.0, session.name)


def _mounts(agent: object, runtime: PiRuntimeSettings) -> list[dict[str, Any]]:
    root = agent_workspace_root(runtime, agent)
    if not root.is_dir() or root.is_symlink():
        return []
    result: list[dict[str, Any]] = []
    sessions = [
        session
        for session in root.iterdir()
        if session.is_dir() and not session.is_symlink()
    ]
    for session in sorted(sessions, key=_session_sort_key, reverse=True):
        mount = published_skills_dir(agent, session.name, runtime)
        result.append(
            {
                "session_id": session.name,
                "mount_path": str(mount),
                "target_path": "/skills (sandbox read-only)",
                "status": _mount_status(mount),
            }
        )
    return result


def _snapshot(user: User) -> dict[str, Any]:
    _ = user
    runtime = PiRuntimeSettings.from_env()
    agent = _agent()
    canonical = skills_dir_for_agent(agent, runtime)
    workspace_root = agent_workspace_root(runtime, agent)
    return {
        "enabled": True,
        "executable": runtime.executable,
        "agent_id": agent.id,
        "tenant_id": agent.tenant_id,
        "runtime_root": str(runtime.root),
        "skills_root": str(canonical),
        "workspace_root": str(workspace_root),
        "skills": list_agent_skills(agent, runtime),
        "mounts": _mounts(agent, runtime),
    }


@router.get("/runtime")
def get_runtime(user: User = Depends(admin_user)) -> dict[str, Any]:
    return _snapshot(user)


@router.delete("/skills/{relative_path:path}")
def remove_skill(
    relative_path: str, user: User = Depends(admin_user)
) -> dict[str, Any]:
    runtime = PiRuntimeSettings.from_env()
    agent = _agent()
    try:
        deleted = delete_agent_skill(agent, relative_path, runtime)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail="技能不存在") from exc
    except SkillBusyError as exc:
        raise HTTPException(status_code=409, detail="技能正在被其他操作使用") from exc
    except SkillPathError as exc:
        raise HTTPException(status_code=422, detail="技能路径无效") from exc
    except SkillOperationError as exc:
        raise HTTPException(status_code=500, detail="技能操作失败") from exc
    with transaction() as db:
        audit.record(db, user.id, "pi_skill_deleted", deleted["relative_path"], "success")
    return {"deleted": deleted, "runtime": _snapshot(user)}


@router.post("/mounts/repair")
def repair_mount(
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    _ = user
    raise HTTPException(
        status_code=410,
        detail="持久 Skill 挂载已退役；Skill 仅在 Pi sandbox 进程生命周期内临时发布",
    )
