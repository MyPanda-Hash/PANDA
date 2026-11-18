"""定时任务终态产物的保留清理：运行、草案、通知与工作区。

只删除确定无副作用的终态数据；活动运行（含 awaiting_user 与
outcome_unknown）必须保留，等待用户或管理员显式处理。
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import transaction
from app.core.security import now as utc_now
from app.models.schedules import (
    ScheduleDraft,
    ScheduleNotification,
    ScheduleOperation,
    ScheduleRun,
)
from app.repositories import harness as harness_repo
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID, PiRuntimeSettings
from app.services.pi_runtime.skills import workspace_dir_for_agent

logger = logging.getLogger(__name__)

# awaiting_user / outcome_unknown 保留：它们仍是活动状态并占用 single-flight。
_TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "skipped")
_DRAFT_TTL = 86400 * 7
_NOTIFICATION_GRACE = 86400
_CLEANUP_BATCH = 500
_SCHEDULE_AGENT = SimpleNamespace(tenant_id="_system", id=PI_RUNTIME_AGENT_ID)


def _destroy_run_workspace(runtime_id: str) -> int:
    """删除一次执行的工作区目录与 harness 任务行；失败只记录不中断。"""
    try:
        workspace = workspace_dir_for_agent(
            PiRuntimeSettings.from_env(), _SCHEDULE_AGENT, runtime_id
        ).resolve()
        removed = 0
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
            removed = 1
        with transaction() as db:
            harness_repo.delete_task(db, runtime_id)
        return removed
    except Exception:
        logger.exception("schedule workspace cleanup failed: %s", runtime_id)
        return 0


def cleanup_once(*, at: datetime | None = None) -> dict[str, int]:
    """按保留策略清理终态产物，返回各类删除计数。"""
    from datetime import timedelta

    stamp = at or utc_now()
    cutoff = stamp - timedelta(days=settings.schedule_run_retention_days)
    stats = {
        "runs": 0,
        "workspaces": 0,
        "drafts": 0,
        "notifications": 0,
        "operations": 0,
    }
    workspaces: list[str] = []
    with transaction() as db:
        rows = list(
            db.execute(
                select(ScheduleRun.id, ScheduleRun.runtime_id)
                .where(
                    ScheduleRun.status.in_(_TERMINAL_RUN_STATUSES),
                    ScheduleRun.updated_at < cutoff,
                )
                .limit(_CLEANUP_BATCH)
            )
        )
        if rows:
            run_ids = [row.id for row in rows]
            workspaces = [
                str(row.runtime_id) for row in rows if row.runtime_id
            ]
            stats["runs"] = (
                db.execute(
                    delete(ScheduleRun).where(ScheduleRun.id.in_(run_ids))
                ).rowcount
                or 0
            )
        stats["drafts"] = (
            db.execute(
                delete(ScheduleDraft).where(
                    ScheduleDraft.status.in_(("expired", "cancelled", "confirmed")),
                    ScheduleDraft.updated_at < stamp - timedelta(seconds=_DRAFT_TTL),
                )
            ).rowcount
            or 0
        )
        stats["notifications"] = (
            db.execute(
                delete(ScheduleNotification).where(
                    ScheduleNotification.expires_at
                    < stamp - timedelta(seconds=_NOTIFICATION_GRACE)
                )
            ).rowcount
            or 0
        )
        stats["operations"] = (
            db.execute(
                delete(ScheduleOperation).where(
                    ScheduleOperation.created_at < cutoff
                )
            ).rowcount
            or 0
        )
    for runtime_id in workspaces:
        stats["workspaces"] += _destroy_run_workspace(runtime_id)
    if any(stats.values()):
        logger.info("schedule retention cleanup: %s", stats)
    return stats
