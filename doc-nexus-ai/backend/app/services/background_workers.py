from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime
import logging
from typing import cast

from app.api import documents
from app.core.config import settings
from app.core.database import SessionLocal, transaction
from app.services import (
    external_object_cleanup,
    media_task_dispatch,
    schedule_retention,
)
from sqlalchemy import select

from app.models.auth import User
from app.services.execution_resources import (
    WorkerExecutionResources,
    WorkerRole,
    role_runs_background,
    role_runs_documents,
)
from app.services.notification_dispatcher import NotificationDispatcher
from app.services.schedule_dispatcher import ScheduleDispatcher
from app.services.user_memory_dispatcher import UserMemoryDispatcher

logger = logging.getLogger(__name__)

from app.services import assistant_watch, doc_watch  # noqa: E402  (延迟导入避免循环)
from app.services import todo_followup  # noqa: E402

_last_archive_date: str = ""
_last_digest_date: str = ""
_last_brief_date: str = ""
_last_report_key: str = ""
_last_profile_key: str = ""


def _maybe_archive(db, *, force: bool = False) -> None:
    """每天归档一次；用日期做去重，进程重启也不会漏。"""
    global _last_archive_date
    today = date.today().isoformat()
    if not force and _last_archive_date == today:
        return
    from app.services import assistant_watch, doc_watch

    try:
        count = assistant_watch.archive_old_events(db)
        _last_archive_date = today
        if count:
            logger.info("assistant events archived", extra={"count": count})
    except Exception:
        logger.exception("assistant archive failed")


def due_reports(
    local_now, *, hour: int, last_key: str = ""
) -> list[str]:
    """判断这个时刻该发哪些报表。纯函数，便于测试。"""
    if local_now.hour < max(0, min(23, hour)):
        return []
    today = local_now.date().isoformat()
    due: list[str] = []
    if last_key != f"daily:{today}":
        due.append("daily")
    if local_now.weekday() == 0 and last_key != f"weekly:{today}":
        due.append("weekly")
    if local_now.day == 1 and last_key != f"monthly:{today}":
        due.append("monthly")
    return due


async def _maybe_distill_profiles(db) -> None:
    """每周一生成一次全员的职能卡片（上一完整周）。"""
    global _last_profile_key
    from datetime import datetime as _dt

    from app.services import employee_profiles

    local_now = _dt.now()
    if local_now.weekday() != 0:          # 只在周一
        return
    hour = int(settings.assistant_report_hour or 9)
    if local_now.hour < hour:
        return
    today = local_now.date().isoformat()
    if _last_profile_key == today:
        return

    with transaction() as inner:
        users = [
            row
            for row in inner.scalars(select(User).where(User.status == "active"))
        ]
        made = 0
        for user in users:
            try:
                await employee_profiles.distill_user_async(inner, user=user)
                made += 1
            except Exception:
                logger.exception("distill failed", extra={"user_id": user.id})
            finally:
                # 每个用户单独提交：下一个用户要调模型，写锁必须先放掉
                inner.commit()
    _last_profile_key = today
    logger.info("employee profiles distilled", extra={"watch": {"count": made}})

    # 顺带把上周有结论的群讨论沉淀成项目经验
    try:
        from app.services.employee_profiles import week_period

        period_start, period_end = week_period()
        experiences = await employee_profiles.extract_from_groups(
            inner, period_start=period_start, period_end=period_end
        )
        logger.info(
            "project experiences extracted",
            extra={"watch": {"count": len(experiences)}},
        )
    except Exception:
        logger.exception("experience extraction failed")


async def _maybe_report_async(db=None) -> None:
    """到点发报表：日报每天、周报每周一、月报每月 1 号。

    用 (类型, 日期) 做进程内去重，重启后同一天不会重复发。

    事务被刻意切成三段：算数字（短读）→ 调模型（**不在事务里**）→ 投递（短写）。
    历史上整段放在一个 ``with transaction()`` 里，模型一慢就把 SQLite 的写锁
    攥上几十秒，别人连登录都写不进去（表现为 database is locked）。
    """
    global _last_report_key
    from datetime import datetime as _dt

    from app.services import assistant_reports, assistant_report_insight

    if not settings.assistant_report_enabled:
        return
    local_now = _dt.now()
    due = due_reports(
        local_now,
        hour=int(settings.assistant_report_hour or 9),
        last_key=_last_report_key,
    )
    today = local_now.date().isoformat()

    for kind in due:
        try:
            # ① 只读：把数字和标题算出来
            with transaction() as read_db:
                ctx = assistant_reports.build_context(read_db, kind=kind)
                body = assistant_reports.render_report(ctx)
                title = assistant_reports._report_title(kind, ctx)
                created = ctx.created
            # ② 模型解读：可能几十秒，绝不能占着写锁。
            # 单独给模型路由开一个只读会话（模型配置要查库），用完就关。
            llm_db = SessionLocal()
            try:
                section = await assistant_report_insight.generate_insight(ctx, llm_db)
            finally:
                llm_db.close()
            if section:
                body = f"{body}\n\n{section}"
            # ③ 短事务投递
            with transaction() as write_db:
                user = assistant_reports.admin_user(write_db)
                if user is not None:
                    assistant_reports.deliver_to_chat(
                        write_db, user=user, title=title, content=body
                    )
            _last_report_key = f"{kind}:{today}"
            logger.info(
                "assistant report generated",
                extra={"watch": {"kind": kind, "created": created}},
            )
        except Exception:
            logger.exception("assistant report failed", extra={"kind": kind})


def _maybe_report(db) -> None:
    """兼容同步调用点（不使用）。"""
    return None


def _scan_doc_watches_once() -> dict:
    """盯单扫描：分批开关事务，供线程池调用（SQLite 会话不跨线程）。

    顺序刻意是「读订阅 → 事务外查 MES → 短事务写回」：追踪要走 HTTP，
    放在写事务里会把 SQLite 写锁一直占着，别人连登录都写不进去。
    """
    try:
        with transaction() as db:
            doc_nos = sorted({w.doc_no for w in doc_watch.active_watches(db)})
    except Exception:
        logger.exception("doc watch listing failed")
        return {"checked": 0, "hit": 0, "expired": 0, "failed": 0}

    traces: dict[str, dict] = {}
    for doc_no in doc_nos:
        try:
            traces[doc_no] = doc_watch._default_trace(doc_no)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其他订阅
            logger.warning("doc watch prefetch failed doc=%s: %s", doc_no, exc)

    with transaction() as db:
        return doc_watch.scan(db, trace_fn=lambda doc_no: traces.get(doc_no) or {})


def _maybe_brief_push_once() -> None:
    """到点推今日简报到 AI 数字员工（默认 08:00）。

    为什么按「小时到达 + 当天未推」而不是精确到 08:00:00：
    扫描循环本身有间隔，精确对齐既做不到也没意义；只要当天第一次扫描
    跨过 08:00 就推，晚几分钟对「早会前看到」没有影响。
    """
    global _last_brief_date
    hour = int(getattr(settings, "brief_push_hour", 0) or 0)
    if hour <= 0:
        return
    now = datetime.now()
    today = now.date().isoformat()
    if _last_brief_date == today or now.hour < hour:
        return
    from app.services import brief_push

    try:
        with transaction() as db:
            result = brief_push.push_all(db, day=today)
        _last_brief_date = today
        logger.info("daily brief pushed", extra={"brief": result})
    except Exception:
        logger.exception("daily brief push failed")


def _maybe_digest_once() -> None:
    """每天发一次汇总 + 今日计划（默认每早第一轮扫描后）。

    生产概况要连 MES 取数，先在没有事务的情况下算好，再开短事务写入。
    """
    global _last_digest_date
    today = date.today().isoformat()
    if _last_digest_date == today:
        return
    from app.services import assistant_watch, production_briefing

    briefings: dict[str, str] = {}
    try:
        with transaction() as db:
            user_ids = list(db.scalars(select(User.id)))
        for user_id in user_ids:
            try:
                briefing = production_briefing.for_user(user_id)
            except Exception:  # noqa: BLE001 - 单人失败不影响其他人
                logger.exception("morning briefing failed", extra={"user_id": user_id})
                continue
            if briefing.ok:
                briefings[user_id] = briefing.speak()
    except Exception:
        logger.exception("morning briefing prefetch failed")

    try:
        with transaction() as db:
            content = assistant_watch.post_digest(db, days=1)
        _last_digest_date = today
        if content:
            logger.info("assistant digest posted")
    except Exception:
        logger.exception("assistant digest failed")
    # 汇总之后紧接着推今日计划：给每个人推他自己的 top N，群里发一条总览。
    # 和汇总共用「一天一次」的开关，不会变成每轮扫描都刷一遍。
    if settings.mes_morning_plan_enabled:
        try:
            with transaction() as db:
                result = assistant_watch.post_morning_plan(db, briefings=briefings)
            logger.info("morning plan pushed=%s posted=%s", result.get("pushed"), result.get("posted"))
        except Exception:
            logger.exception("morning plan failed")


_MEDIA_RECONCILIATION_INTERVAL_SECONDS = 30.0


def _reconcile_stale_media_submissions() -> dict[str, int]:
    with transaction() as db:
        return media_task_dispatch.reconcile_stale_submissions(db)


class BackgroundWorkerService:
    """Runs only the durable consumers selected by one explicit worker role."""
    def __init__(self, role: WorkerRole | None = None) -> None:
        self.role: WorkerRole = cast(WorkerRole, role or settings.worker_role)
        self.resources = WorkerExecutionResources(self.role)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._memory_dispatcher: UserMemoryDispatcher | None = None
        self._memory_started = False
        self._schedule_dispatcher: ScheduleDispatcher | None = None
        self._notification_dispatcher: NotificationDispatcher | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        if settings.mes_watch_enabled:
            self._tasks.append(
                asyncio.create_task(self._mes_watch_loop(), name="mes-watch-worker")
            )
        if role_runs_documents(self.role):
            try:
                await self.resources.run_document(documents.recover_stuck_processing)
            except Exception:
                logger.exception("document processing recovery failed")
            self._tasks.append(
                asyncio.create_task(
                    self._document_upload_loop(), name="document-upload-worker"
                )
            )
            if settings.temporary_attachment_cleanup_interval_seconds > 0:
                self._tasks.append(
                    asyncio.create_task(
                        self._temporary_attachment_cleanup_loop(),
                        name="temporary-attachment-cleanup",
                    )
                )
        if role_runs_background(self.role):
            try:
                media_recovery = await self.resources.run_background(
                    _reconcile_stale_media_submissions
                )
                if any(media_recovery.values()):
                    logger.warning(
                        "reconciled stale media submissions: %s", media_recovery
                    )
            except Exception:
                logger.exception("media submission recovery failed")
            self._memory_dispatcher = UserMemoryDispatcher(
                execution_resources=self.resources
            )
            self._memory_started = self._memory_dispatcher.start() is not None
            self._schedule_dispatcher = ScheduleDispatcher(
                scan_interval_seconds=settings.schedule_scan_interval_seconds,
                lease_seconds=settings.schedule_lease_seconds,
                execution_concurrency=settings.schedule_worker_concurrency,
                per_owner_concurrency=settings.schedule_worker_concurrency_per_user,
            )
            await self._schedule_dispatcher.start()
            self._notification_dispatcher = NotificationDispatcher()
            self._notification_dispatcher.start(self.resources.run_background)
            if settings.schedule_cleanup_interval_seconds > 0:
                self._tasks.append(
                    asyncio.create_task(
                        self._schedule_retention_loop(),
                        name="schedule-retention-worker",
                    )
                )
            self._tasks.extend(
                (
                    asyncio.create_task(
                        self._media_reconciliation_loop(),
                        name="media-reconciliation-worker",
                    ),
                    asyncio.create_task(
                        self._external_object_cleanup_loop(),
                        name="external-object-cleanup-worker",
                    ),
                )
            )
        task_names = [task.get_name() for task in self._tasks]
        if self._memory_started:
            task_names.append("personal-memory")
        logger.info(
            "worker role started: role=%s tasks=%s",
            self.role,
            ",".join(task_names) or "none",
        )

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    async def _mes_watch_loop(self) -> None:
        """定期读 MES，把待处理事实派给对应的人。"""
        interval = max(15.0, float(settings.mes_watch_interval_seconds or 120.0))
        # 启动时先等一个短周期，避免和迁移/首个请求抢资源
        await asyncio.sleep(min(10.0, interval))
        while not self._stop.is_set():
            try:
                # 扫描是同步阻塞的 HTTP + DB，放后台线程池，不占事件循环
                if role_runs_background(self.role):
                    await self.resources.run_background(self._scan_mes_once)
                else:
                    await asyncio.to_thread(self._scan_mes_once)
                # 扫描完成后再判断要不要出报表（内部按小时/星期/日去重）
                await self._run_reports()
                # 订阅式盯单：只读追踪，命中就推个人通知。
                # 事务在子线程里开：SQLite 会话不跨线程，scan 里的 HTTP 也不能卡事件循环
                try:
                    result = await asyncio.to_thread(_scan_doc_watches_once)
                    if result.get("hit"):
                        logger.info("doc watches signalled", extra={"watch": result})
                except Exception:
                    logger.exception("doc watch scan failed")
                # 周一生成员工职能卡片
                try:
                    with transaction() as profile_db:
                        await _maybe_distill_profiles(profile_db)
                except Exception:
                    logger.exception("profile distillation step failed")
            except Exception as exc:
                logger.exception("mes watch scan failed")
                # 失败也要留痕：健康度里能看出「跑了但没跑成」
                try:
                    with transaction() as db:
                        assistant_watch.record_failed_run(
                            db, error=str(exc), trigger="timer"
                        )
                except Exception:
                    logger.exception("record failed run failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _run_reports(self) -> None:
        """报表要调模型，必须异步；单独一步，失败不影响扫描。

        不能用 with transaction() 包起来：模型调用会把写锁占住。
        """
        try:
            await _maybe_report_async()
        except Exception:
            logger.exception("assistant report step failed")

    def _scan_mes_once(self) -> None:
        with transaction() as db:
            summary = assistant_watch.scan(db)
            # 每天顺手归档一次旧事件（幂等，条件不满足就是 0）
            _maybe_archive(db)
            # 汇总与今日计划改成事务外再跑（见 _maybe_digest_once）
            # 待办状态回流：该催的催、催够的升级。失败不能拖垮整轮扫描。
            try:
                followup = todo_followup.sweep(db)
                if followup.get("nudged") or followup.get("escalated"):
                    logger.info("todo followup sweep", extra={"followup": followup})
            except Exception:
                logger.exception("todo followup sweep failed")
        # 汇总/今日计划会连 MES（生产概况）并写库：必须放在上面的事务之外，
        # 否则 MES 一慢就把 SQLite 写锁攥住，别人连登录都写不进去。
        _maybe_digest_once()
        # 简报推送独立于扫描汇总：汇总发群、简报发个人，互不阻塞
        _maybe_brief_push_once()
        if any(summary.values()):
            # 不能直接 extra=summary：里面的 created 等键与 LogRecord 内置属性冲突，
            # 会抛 "Attempt to overwrite 'created' in LogRecord"，把整轮扫描判成失败。
            logger.info(
                "mes watch scan",
                extra={"watch": {k: v for k, v in summary.items()}},
            )

    async def _document_upload_loop(self) -> None:
        from app.services.document_uploads import (
            DocumentUploadDispatcher,
            cleanup_expired_uploads,
        )

        dispatcher = DocumentUploadDispatcher()
        next_cleanup = 0.0
        while not self._stop.is_set():
            worked = False
            try:
                loop = asyncio.get_running_loop()
                if loop.time() >= next_cleanup:
                    await self.resources.run_document(cleanup_expired_uploads)
                    next_cleanup = loop.time() + max(
                        1, settings.document_upload_cleanup_interval_seconds
                    )
                worked = await self.resources.run_document(dispatcher.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("document upload worker failed")
            await self._sleep(0.1 if worked else 1.0)

    async def _temporary_attachment_cleanup_loop(self) -> None:
        interval = float(settings.temporary_attachment_cleanup_interval_seconds)
        while not self._stop.is_set():
            try:
                stats = await self.resources.run_document(
                    documents.cleanup_expired_temporary_documents
                )
                if stats["cleaned"] or stats["failed"]:
                    logger.info("temporary attachment cleanup: %s", stats)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("temporary attachment cleanup loop failed")
            await self._sleep(max(0.1, interval))

    async def _media_reconciliation_loop(self) -> None:
        while not self._stop.is_set():
            try:
                stats = await self.resources.run_background(
                    _reconcile_stale_media_submissions
                )
                if any(stats.values()):
                    logger.info("reconciled stale media submissions: %s", stats)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("media reconciliation loop failed")
            await self._sleep(_MEDIA_RECONCILIATION_INTERVAL_SECONDS)

    async def _external_object_cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await external_object_cleanup.process_pending_once(
                    run_sync=self.resources.run_background
                )
                if processed:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("external object cleanup loop failed")
            await self._sleep(external_object_cleanup.WORKER_INTERVAL_SECONDS)

    async def _schedule_retention_loop(self) -> None:
        interval = max(60.0, float(settings.schedule_cleanup_interval_seconds))
        while not self._stop.is_set():
            try:
                stats = await self.resources.run_background(
                    schedule_retention.cleanup_once
                )
                if any(stats.values()):
                    logger.info("schedule retention cleanup: %s", stats)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("schedule retention loop failed")
            await self._sleep(interval)

    async def close(self) -> None:
        if not self._started:
            self.resources.close()
            return
        self._stop.set()
        if self._memory_dispatcher is not None:
            await self._memory_dispatcher.close()
        if self._notification_dispatcher is not None:
            await self._notification_dispatcher.close()
        if self._schedule_dispatcher is not None:
            await self._schedule_dispatcher.close()
        aggregate = asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(
                asyncio.shield(aggregate),
                timeout=float(settings.worker_shutdown_grace_seconds),
            )
        except asyncio.TimeoutError:
            for task in self._tasks:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aggregate
        finally:
            self.resources.close()
