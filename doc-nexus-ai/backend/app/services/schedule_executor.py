from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy import or_, select, update

from app.core.database import transaction
from app.core.security import now as utc_now
from app.models.auth import User
from app.models.schedules import Schedule, ScheduleRun, ScheduleRunAttempt
from app.repositories import harness as harness_repo
from app.repositories import schedules as schedule_repo
from app.services.agent_capability import (
    WRITING_CAPABILITY_TOOLS,
    create_capability_token,
)
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskLimits,
    TaskPermissions,
)
from app.services.pi_local import resolve_local_pi_endpoint
from app.services.pi_runtime.config import (
    PI_RUNTIME_AGENT_ID,
    PiRuntimeSettings,
    resolve_pi_model,
)
from app.services.pi_runtime.runner import PiRpcCancelledError, PiRpcError, PiRpcRunner
from app.services.pi_runtime.skills import (
    SkillNotFoundError,
    workspace_dir_for_agent,
)
from app.services.schedule_access import (
    ScheduleAccessError,
    resolve_schedule_skill,
    schedule_scope_space_ids,
    validate_schedule_scope,
)

logger = logging.getLogger(__name__)
_MAX_BODY_CHARS = 200_000
_MAX_PROGRESS_ITEMS = 100
_BLOCKING_ERROR_CODES = frozenset(
    {
        "owner_unavailable",
        "scope_invalid",
        "scope_unavailable",
        "temporary_source",
        "skill_required",
        "skill_unavailable",
        "skill_changed",
        "model_binding_missing",
        "model_unavailable",
        "runtime_binding_invalid",
        "schedule_sandbox_required",
        "budget_exceeded",
        "usage_unknown",
        "preflight_failed",
    }
)


@dataclass(frozen=True, slots=True)
class ClaimedScheduleRun:
    run_id: str
    claim_epoch: int
    attempt_no: int
    owner_user_id: str


@dataclass(slots=True)
class _ExecutionCapture:
    body: str = ""
    progress: list[dict[str, str]] = field(default_factory=list)
    awaiting_user: bool = False
    dirty_chars: int = 0

    def consume(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "message_update":
            delta = event.get("assistantMessageEvent")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = str(delta.get("delta") or "")
                if text:
                    self.body = (self.body + text)[-_MAX_BODY_CHARS:]
                    self.dirty_chars += len(text)
        elif event_type == "extension_ui_request":
            if (
                event.get("method") in {"confirm", "select", "input", "editor"}
                or event.get("message") == "schedule_permission_required"
            ):
                self.awaiting_user = True
                self.progress.append({"message": "执行需要用户确认，已安全暂停"})
        elif event_type == "runtime":
            status = str(event.get("status") or "")
            if status in {"waiting", "running"}:
                self.progress.append({"message": f"执行资源状态：{status}"})
        if len(self.progress) > _MAX_PROGRESS_ITEMS:
            self.progress = self.progress[-_MAX_PROGRESS_ITEMS:]


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("运行配置无效")
    return parsed


def _safe_files(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    files: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        href = str(item.get("url") or item.get("href") or "").strip()
        name = str(item.get("name") or item.get("path") or "文件").strip()[:240]
        if not href:
            continue
        projected: dict[str, Any] = {"name": name or "文件", "href": href}
        if isinstance(item.get("size"), int) and item["size"] >= 0:
            projected["size"] = item["size"]
        mime_type = str(item.get("mime_type") or item.get("content_type") or "").strip()
        if mime_type:
            projected["mime_type"] = mime_type[:160]
        files.append(projected)
    return files


def _usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    total = value.get("total_tokens")
    if type(total) is not int or total < 0:
        return None
    return {
        str(key): int(amount)
        for key, amount in value.items()
        if isinstance(key, str) and type(amount) is int and amount >= 0
    }


def _reserve_schedule_budget(
    *,
    user_id: str,
    operation_id: str,
    provider: str,
    model: str,
    config: dict[str, Any],
) -> str | None:
    """预算硬停预检：超限抛出可操作的 ScheduleAccessError，否则返回预留 id。"""
    from app.services.budget import ScheduleBudgetExceeded, reserve_schedule_budget

    with transaction() as db:
        space_ids = schedule_scope_space_ids(db, config)
    try:
        handle = reserve_schedule_budget(
            user_id=user_id,
            operation_id=operation_id,
            provider=provider,
            model=model,
            budget_tokens=int(config.get("budget_tokens") or 500_000),
            space_ids=space_ids,
            timeout_seconds=float(int(config.get("timeout_seconds") or 600)),
        )
    except ScheduleBudgetExceeded as exc:
        raise ScheduleAccessError(
            "budget_exceeded",
            "定时任务已达到累计预算上限，请联系管理员调整预算规则",
        ) from exc
    return handle.id if handle is not None else None


def _recordable_usage(
    provider: str, model: str, token_usage: object
) -> dict[str, Any] | None:
    """把运行器 token 用量转成账本形态，并按现行价格补全费用快照。"""
    if not isinstance(token_usage, dict):
        return None
    total = token_usage.get("total_tokens")
    if type(total) is not int or total < 0:
        return None

    def _count(*keys: str) -> int:
        for key in keys:
            value = token_usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return 0

    usage: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "input_tokens": _count("prompt_tokens", "input_tokens"),
        "input_uncached_tokens": _count("input_uncached_tokens", "input_tokens"),
        "cache_read_tokens": _count("cache_read_tokens"),
        "cache_write_tokens": _count("cache_write_tokens"),
        "output_tokens": _count("completion_tokens", "output_tokens"),
        "total_tokens": total,
        "cost": "0",
    }
    from app.services.pricing import fill_usage_cost

    with transaction() as db:
        fill_usage_cost(db, usage)
    return usage


class _ScheduleCredentialStore:
    def __init__(self, credential: object) -> None:
        self.credential = credential

    def get_credential(self, credential_id: str) -> object | None:
        return self.credential if credential_id == "schedule" else None


def _resolved_pi_model(requested_model: str, owner_user_id: str, agent: object):
    try:
        endpoint = resolve_local_pi_endpoint(
            requested_model,
            capability="chat",
            user_id=owner_user_id,
        )
    except Exception as exc:
        raise ScheduleAccessError(
            "model_unavailable", "计划绑定的模型当前不可用，请重新确认任务"
        ) from exc
    if endpoint.model != requested_model:
        raise ScheduleAccessError(
            "model_unavailable", "计划绑定的模型当前不可用，请重新确认任务"
        )
    credential = SimpleNamespace(
        provider=endpoint.provider,
        adapter=endpoint.adapter,
        api_protocol=endpoint.api_protocol,
        api_key=endpoint.api_key,
        allow_anonymous_api_key=endpoint.allow_anonymous_api_key,
        base_url=endpoint.base_url,
        default_model=endpoint.model,
        azure_endpoint=endpoint.base_url if endpoint.adapter == "azure" else "",
        azure_deployment=endpoint.model if endpoint.adapter == "azure" else "",
        azure_api_version=endpoint.api_version,
        supports_image_input=endpoint.supports_image_input,
        max_context=endpoint.max_context,
    )
    return resolve_pi_model(agent, cast(Any, _ScheduleCredentialStore(credential))), str(
        getattr(endpoint, "provider", "") or ""
    )


def _schedule_agent(*, model: str, timeout_seconds: int, enable_skills: bool) -> object:
    # 工具白名单与持久 HarnessTask 的权限开关共同生效；提示词不是权限边界。
    # enable_skills 只在计划绑定了 Skill 时为真：无绑定的提示词任务不得发布或加载任何已启用 Skill。
    return SimpleNamespace(
        id=PI_RUNTIME_AGENT_ID,
        name="doc-nexus-schedule",
        tenant_id="_system",
        system_message=(
            "你正在执行已确认的无人值守定时任务。只能读取已授权资料、分析内容，并在当前工作区生成新的成果文件。"
            "不得发送消息、修改外部系统、删除或覆盖业务数据、申请或安装能力，也不得要求扩大资料范围。"
            "需要新的外部写入或人工批准时立即停止并明确说明需要用户处理。"
        ),
        model_ref=SimpleNamespace(credential_id="schedule", model=model),
        pi_config=SimpleNamespace(
            thinking_level="medium",
            tools=("read", "write", "edit", "grep", "find"),
            timeout_sec=timeout_seconds,
            idle_timeout_sec=min(timeout_seconds, 600),
            enable_skills=enable_skills,
        ),
    )


def _ensure_restricted_harness_task(
    runtime: PiRuntimeSettings,
    *,
    runtime_id: str,
    owner_user_id: str,
    instruction: str,
    budget_tokens: int,
    timeout_seconds: int,
    agent: object,
) -> Path:
    workspace = workspace_dir_for_agent(runtime, agent, runtime_id).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    permissions = TaskPermissions(
        file_read=True,
        file_write=True,
        shell=False,
        git_commit=False,
        git_push=False,
        network=False,
        public_web_read=False,
        install_packages=False,
        sandbox=True,
        approved_skill_execution=True,
        skill_install_request=False,
        workspace_file_upload=False,
        database_write=False,
    )
    task = AgentTask(
        task_id=runtime_id,
        tenant_id="_system",
        user_id=owner_user_id,
        project=ProjectContext(project_id=runtime_id, workspace=str(workspace)),
        objective=instruction,
        permissions=permissions,
        limits=TaskLimits(max_tokens=budget_tokens, timeout_seconds=timeout_seconds),
    )
    with transaction() as db:
        existing = harness_repo.get_task(db, runtime_id)
        if existing is None:
            harness_repo.create_task(db, task, workspace_path=str(workspace))
            harness_repo.update_task_status(db, runtime_id, "running")
        else:
            restored = harness_repo.row_to_agent_task(existing)
            if (
                restored.user_id != owner_user_id
                or restored.tenant_id != "_system"
                or restored.permissions != permissions
                or Path(existing.workspace_path).resolve() != workspace
            ):
                raise ScheduleAccessError(
                    "runtime_binding_invalid", "定时任务执行身份校验失败"
                )
            harness_repo.update_task_status(db, runtime_id, "running")
    return workspace


class ScheduleExecutor:
    def __init__(self, *, worker_id: str, lease_seconds: int = 90) -> None:
        self.worker_id = worker_id
        self.lease_seconds = max(30, lease_seconds)

    def _owned(
        self, db, claim: ClaimedScheduleRun, *, for_update: bool = False
    ) -> ScheduleRun | None:
        conditions = (
            ScheduleRun.id == claim.run_id,
            ScheduleRun.status == "running",
            ScheduleRun.lease_owner == self.worker_id,
            ScheduleRun.claim_epoch == claim.claim_epoch,
            ScheduleRun.attempt_no == claim.attempt_no,
            ScheduleRun.lease_until > utc_now(),
        )
        if for_update:
            # 两种数据库都先以 fencing 条件取得写锁；持锁期间才能做 ORM 更新。
            locked = db.execute(
                update(ScheduleRun)
                .where(*conditions)
                .values(claim_epoch=ScheduleRun.claim_epoch)
                .execution_options(synchronize_session=False)
            )
            if getattr(locked, "rowcount", 0) != 1:
                return None
        return db.scalar(
            select(ScheduleRun)
            .where(*conditions)
            .execution_options(populate_existing=True)
        )

    def _set_phase(self, claim: ClaimedScheduleRun, phase: str) -> bool:
        with transaction() as db:
            run = self._owned(db, claim, for_update=True)
            if run is None:
                return False
            attempt = db.scalar(
                select(ScheduleRunAttempt).where(
                    ScheduleRunAttempt.run_id == run.id,
                    ScheduleRunAttempt.attempt_no == claim.attempt_no,
                    ScheduleRunAttempt.claim_epoch == claim.claim_epoch,
                )
            )
            if attempt is None:
                return False
            attempt.phase = phase
            run.updated_at = utc_now()
            db.flush()
            return True

    def renew_lease(self, claim: ClaimedScheduleRun) -> bool:
        stamp = utc_now()
        with transaction() as db:
            result = db.execute(
                update(ScheduleRun)
                .where(
                    ScheduleRun.id == claim.run_id,
                    ScheduleRun.status == "running",
                    ScheduleRun.lease_owner == self.worker_id,
                    ScheduleRun.claim_epoch == claim.claim_epoch,
                    ScheduleRun.attempt_no == claim.attempt_no,
                    ScheduleRun.lease_until > stamp,
                )
                .values(
                    lease_until=stamp + timedelta(seconds=self.lease_seconds),
                    updated_at=stamp,
                )
            )
            return bool(getattr(result, "rowcount", 0))

    def _set_runtime_id(self, claim: ClaimedScheduleRun, runtime_id: str) -> bool:
        stamp = utc_now()
        with transaction() as db:
            result = db.execute(
                update(ScheduleRun)
                .where(
                    ScheduleRun.id == claim.run_id,
                    ScheduleRun.status == "running",
                    ScheduleRun.lease_owner == self.worker_id,
                    ScheduleRun.claim_epoch == claim.claim_epoch,
                    ScheduleRun.attempt_no == claim.attempt_no,
                    or_(
                        ScheduleRun.runtime_id.is_(None),
                        ScheduleRun.runtime_id == runtime_id,
                    ),
                )
                .values(runtime_id=runtime_id, updated_at=stamp)
            )
            return getattr(result, "rowcount", 0) == 1

    def cancel_requested(self, claim: ClaimedScheduleRun) -> bool:
        with transaction() as db:
            run = self._owned(db, claim)
            return run is None or bool(run.cancel_requested)

    def _persist_progress(
        self, claim: ClaimedScheduleRun, capture: _ExecutionCapture
    ) -> bool:
        with transaction() as db:
            run = self._owned(db, claim, for_update=True)
            if run is None:
                return False
            run.body = capture.body or run.body
            run.progress_json = json.dumps(capture.progress, ensure_ascii=False)
            run.updated_at = utc_now()
            db.flush()
            capture.dirty_chars = 0
            return True

    def _preflight(
        self, claim: ClaimedScheduleRun
    ) -> tuple[dict[str, Any], object, str]:
        if PiRuntimeSettings.from_env().execution_mode != "sandbox":
            raise ScheduleAccessError(
                "schedule_sandbox_required",
                "后台任务必须在隔离沙箱中执行，请联系管理员检查配置",
            )
        with transaction() as db:
            run = self._owned(db, claim)
            if run is None:
                raise ScheduleAccessError("claim_lost", "执行租约已失效")
            schedule = db.get(Schedule, run.schedule_id, populate_existing=True)
            if schedule is None or schedule.owner_user_id != run.owner_user_id:
                raise ScheduleAccessError("schedule_unavailable", "定时任务已不可用")
            if schedule.status not in {"enabled", "completed"} or run.cancel_requested:
                raise ScheduleAccessError("schedule_stopped", "定时任务已暂停或取消")
            config = _json_object(run.config_json)
            validate_schedule_scope(db, run.owner_user_id, config)
            resolved_config = resolve_schedule_skill(config)
            for key in ("skill_id", "skill_version", "skill_digest"):
                if resolved_config.get(key) != config.get(key):
                    raise ScheduleAccessError(
                        "skill_changed", "所选能力版本已改变，请重新确认任务"
                    )
            requested_model = str(config.get("model") or "").strip()
            if not requested_model:
                raise ScheduleAccessError(
                    "model_binding_missing", "计划缺少已确认的模型绑定，请重新确认任务"
                )
            owner = db.get(User, run.owner_user_id, populate_existing=True)
            if owner is None or owner.status != "active":
                raise ScheduleAccessError("owner_unavailable", "任务所有者当前不可用")
            timeout_seconds = int(config.get("timeout_seconds") or 600)
            agent = _schedule_agent(
                model=requested_model,
                timeout_seconds=timeout_seconds,
                enable_skills=bool(config.get("skill_id")),
            )
            selected_document_ids = list(config.get("document_ids") or [])
            selected_space_ids = schedule_scope_space_ids(db, config)
            if selected_document_ids:
                capability_document_ids: list[str] | None = selected_document_ids
            elif selected_space_ids:
                capability_document_ids = None
            else:
                capability_document_ids = []
            capability = create_capability_token(
                user_id=run.owner_user_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                space_ids=selected_space_ids,
                document_ids=capability_document_ids,
                space_mode="selected",
                personal_data_scope="none",
                memory_scope="none",
                allowed_tools=list(WRITING_CAPABILITY_TOOLS),
            )
            return config, agent, capability

    def _retry_pre_model_failure(
        self,
        claim: ClaimedScheduleRun,
        *,
        capture: _ExecutionCapture,
        error_message: str,
    ) -> bool:
        stamp = utc_now()
        with transaction() as db:
            run = self._owned(db, claim, for_update=True)
            if run is None:
                return False
            try:
                config = _json_object(run.config_json)
                max_attempts = max(1, min(3, int(config.get("max_attempts") or 1)))
            except (TypeError, ValueError):
                max_attempts = 1
            if claim.attempt_no >= max_attempts:
                return False
            run.status = "queued"
            run.status_revision += 1
            run.body = capture.body or run.body
            run.progress_json = json.dumps(capture.progress, ensure_ascii=False)
            run.error_code = "preflight_failed"
            run.error_message_safe = error_message
            run.retryable = True
            run.lease_owner = None
            run.lease_until = None
            run.next_attempt_at = stamp + timedelta(
                seconds=min(60, 2**claim.attempt_no)
            )
            run.updated_at = stamp
            attempt = db.scalar(
                select(ScheduleRunAttempt).where(
                    ScheduleRunAttempt.run_id == run.id,
                    ScheduleRunAttempt.attempt_no == claim.attempt_no,
                    ScheduleRunAttempt.claim_epoch == claim.claim_epoch,
                )
            )
            if attempt is not None:
                attempt.status = "failed"
                attempt.completed_at = stamp
                attempt.error_code = "preflight_failed"
            db.flush()
            return True

    def _settle_budget_reservation(
        self,
        run_id: str,
        reservation_id: str | None,
        *,
        usage: dict[str, Any] | None,
        bill_committed: bool,
        model_started: bool,
    ) -> None:
        """按结果落账结算预算预留；失败时保守持有，等待 hold 过期。"""
        if not reservation_id:
            return
        try:
            from app.services.budget import (
                finalize_schedule_budget_for_operation,
                release_schedule_budget,
                settle_schedule_budget,
            )

            if usage is not None and bill_committed:
                settle_schedule_budget(
                    reservation_id, actual_cost=str(usage.get("cost") or "0")
                )
                finalize_schedule_budget_for_operation(run_id)
            elif model_started:
                settle_schedule_budget(reservation_id, uncertain=True)
            else:
                release_schedule_budget(reservation_id)
        except Exception:
            logger.exception("schedule budget settlement failed: run=%s", run_id)

    def _finalize(
        self,
        claim: ClaimedScheduleRun,
        *,
        status: str,
        capture: _ExecutionCapture,
        files: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
    ) -> bool:
        stamp = utc_now()
        with transaction() as db:
            run = self._owned(db, claim, for_update=True)
            if run is None:
                return False
            run.status = status
            run.status_revision += 1
            run.body = capture.body or run.body
            run.progress_json = json.dumps(capture.progress, ensure_ascii=False)
            run.result_files_json = json.dumps(files or [], ensure_ascii=False)
            run.usage_json = (
                json.dumps(usage, ensure_ascii=False) if usage is not None else None
            )
            run.error_code = error_code
            run.error_message_safe = error_message
            run.retryable = retryable
            run.completed_at = stamp if status != "awaiting_user" else None
            run.lease_owner = None
            run.lease_until = None
            run.next_attempt_at = None
            run.updated_at = stamp
            attempt = db.scalar(
                select(ScheduleRunAttempt).where(
                    ScheduleRunAttempt.run_id == run.id,
                    ScheduleRunAttempt.attempt_no == claim.attempt_no,
                    ScheduleRunAttempt.claim_epoch == claim.claim_epoch,
                )
            )
            if attempt is not None:
                attempt.status = status
                attempt.completed_at = stamp
                attempt.error_code = error_code
                attempt.usage_json = run.usage_json
                # phase 保留最后一个真实副作用边界，终态由 status 单独记录。
            if (
                status in {"outcome_unknown", "awaiting_user"}
                or error_code in _BLOCKING_ERROR_CODES
            ):
                block_schedule = update(Schedule).where(
                    Schedule.id == run.schedule_id,
                    Schedule.status != "archived",
                )
                # 配置依赖只阻塞对应版本；改配置不等于确认未决执行或未知用量。
                if (
                    status not in {"outcome_unknown", "awaiting_user"}
                    and error_code != "usage_unknown"
                ):
                    block_schedule = block_schedule.where(
                        Schedule.revision == run.schedule_revision
                    )
                db.execute(
                    block_schedule.values(
                        status="blocked",
                        blocked_reason=error_code or status,
                        updated_at=stamp,
                    )
                )
            elif status in {"completed", "failed", "cancelled"}:
                try:
                    run_config = _json_object(run.config_json)
                except (TypeError, ValueError):
                    run_config = {}
                rule = run_config.get("rule")
                if (
                    run.trigger_kind == "scheduled"
                    and isinstance(rule, dict)
                    and rule.get("kind") == "once"
                ):
                    db.execute(
                        update(Schedule)
                        .where(
                            Schedule.id == run.schedule_id,
                            Schedule.revision == run.schedule_revision,
                            Schedule.status == "enabled",
                            Schedule.next_run_at.is_(None),
                        )
                        .values(
                            status="completed",
                            next_run_at=None,
                            blocked_reason=None,
                            updated_at=stamp,
                        )
                    )
            if run.runtime_id:
                harness_repo.update_task_status(
                    db,
                    run.runtime_id,
                    status,
                    completed=status != "awaiting_user",
                )
            schedule_repo.emit_notification_safely(db, run, now=stamp)
            db.flush()
            return True

    async def execute(self, claim: ClaimedScheduleRun) -> None:
        capture = _ExecutionCapture()
        heartbeat_stop = asyncio.Event()

        async def heartbeat() -> None:
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(
                        heartbeat_stop.wait(), timeout=self.lease_seconds / 3
                    )
                except TimeoutError:
                    if not await asyncio.to_thread(self.renew_lease, claim):
                        return
                    if capture.dirty_chars:
                        await asyncio.to_thread(self._persist_progress, claim, capture)

        heartbeat_task = asyncio.create_task(
            heartbeat(), name=f"schedule-lease-{claim.run_id}"
        )
        generated_files: list[dict[str, Any]] = []
        model_started = False
        reservation_id: str | None = None
        resolved_model: Any = None
        endpoint_provider = ""
        try:
            if not await asyncio.to_thread(self._set_phase, claim, "preflight"):
                return
            try:
                config, agent, capability = await asyncio.to_thread(
                    self._preflight, claim
                )
                resolved_model, endpoint_provider = await asyncio.to_thread(
                    _resolved_pi_model,
                    str(config["model"]),
                    claim.owner_user_id,
                    agent,
                )
                reservation_id = await asyncio.to_thread(
                    _reserve_schedule_budget,
                    user_id=claim.owner_user_id,
                    operation_id=claim.run_id,
                    provider=endpoint_provider,
                    model=str(resolved_model.model),
                    config=config,
                )
            except ScheduleAccessError:
                raise
            except Exception as exc:
                raise ScheduleAccessError(
                    "preflight_failed", "执行前安全校验暂时无法完成"
                ) from exc
            runtime_id = f"pi-schedule-{claim.run_id}"
            try:
                await asyncio.to_thread(
                    _ensure_restricted_harness_task,
                    PiRuntimeSettings.from_env(),
                    runtime_id=runtime_id,
                    owner_user_id=claim.owner_user_id,
                    instruction=str(config["instruction"]),
                    budget_tokens=int(config.get("budget_tokens") or 500_000),
                    timeout_seconds=int(config.get("timeout_seconds") or 600),
                    agent=agent,
                )
            except ScheduleAccessError:
                raise
            except Exception as exc:
                raise ScheduleAccessError(
                    "runtime_binding_invalid", "定时任务执行环境当前不可用"
                ) from exc
            if not await asyncio.to_thread(self._set_runtime_id, claim, runtime_id):
                return
            if not await asyncio.to_thread(self._set_phase, claim, "model_started"):
                return
            model_started = True

            async def on_event(event: dict[str, Any]) -> None:
                capture.consume(event)
                if capture.dirty_chars >= 4096 or capture.awaiting_user:
                    await asyncio.to_thread(self._persist_progress, claim, capture)

            async def cancelled() -> bool:
                return capture.awaiting_user or await asyncio.to_thread(
                    self.cancel_requested, claim
                )

            result = await PiRpcRunner(PiRuntimeSettings.from_env()).run(
                agent=agent,
                resolved_model=resolved_model,
                session_id=runtime_id,
                prompt=(
                    f"/skill:{config['skill_id']}\n{config['instruction']}"
                    if config.get("skill_id")
                    else str(config["instruction"])
                ),
                on_event=on_event,
                cancel_check=cancelled,
                nexus_capability_token=capability,
                task_id=runtime_id,
                gateway_tenant_id="_system",
                gateway_user_id=claim.owner_user_id,
                admission_user_id=claim.owner_user_id,
                collect_generated_files=True,
                enforce_wall_clock_timeout=True,
                track_model_usage=True,
                max_tokens=int(config.get("budget_tokens") or 500_000),
                initial_tokens=0,
                required_skill=(
                    (
                        str(config["skill_id"]),
                        str(config["skill_version"]),
                        str(config["skill_digest"]),
                    )
                    if config.get("skill_id")
                    else None
                ),
                require_verified_usage=True,
                unattended_restricted=True,
            )
            capture.body = (result.reply or capture.body)[-_MAX_BODY_CHARS:]
            generated_files = _safe_files(result.generated_files)
            usage = await asyncio.to_thread(
                _recordable_usage,
                endpoint_provider,
                str(resolved_model.model),
                result.token_usage,
            )
            if usage is None:
                self._settle_budget_reservation(
                    claim.run_id,
                    reservation_id,
                    usage=None,
                    bill_committed=False,
                    model_started=True,
                )
                self._finalize(
                    claim,
                    status="failed",
                    capture=capture,
                    files=generated_files,
                    error_code="usage_unknown",
                    error_message="本次执行用量无法核实，已停止后续调用",
                )
                return
            bill_committed = self._finalize(
                claim,
                status="awaiting_user" if capture.awaiting_user else "completed",
                capture=capture,
                files=generated_files,
                usage=usage,
                error_code="approval_required" if capture.awaiting_user else None,
                error_message="执行需要用户确认" if capture.awaiting_user else None,
            )
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=usage,
                bill_committed=bill_committed,
                model_started=True,
            )
        except SkillNotFoundError:
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=None,
                bill_committed=False,
                model_started=model_started,
            )
            self._finalize(
                claim,
                status="failed",
                capture=capture,
                files=generated_files,
                error_code="skill_changed",
                error_message="所选能力版本已改变，请重新确认任务",
            )
        except PiRpcCancelledError as exc:
            status = "awaiting_user" if capture.awaiting_user else "cancelled"
            generated_files = _safe_files(getattr(exc, "generated_files", []))
            usage = await asyncio.to_thread(
                _recordable_usage,
                endpoint_provider,
                str(resolved_model.model),
                getattr(exc, "token_usage", None),
            )
            bill_committed = self._finalize(
                claim,
                status=status,
                capture=capture,
                files=generated_files,
                usage=usage,
                error_code="approval_required"
                if capture.awaiting_user
                else "cancelled",
                error_message="执行需要用户确认"
                if capture.awaiting_user
                else "执行已取消",
            )
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=usage,
                bill_committed=bill_committed,
                model_started=True,
            )
        except ScheduleAccessError as exc:
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=None,
                bill_committed=False,
                model_started=model_started,
            )
            if (
                exc.code == "preflight_failed"
                and not model_started
                and self._retry_pre_model_failure(
                    claim,
                    capture=capture,
                    error_message=exc.message,
                )
            ):
                return
            self._finalize(
                claim,
                status="failed",
                capture=capture,
                error_code=exc.code,
                error_message=exc.message,
            )
        except PiRpcError as exc:
            capture.body = str(getattr(exc, "partial_reply", "") or capture.body)[
                -_MAX_BODY_CHARS:
            ]
            generated_files = _safe_files(getattr(exc, "generated_files", []))
            usage = await asyncio.to_thread(
                _recordable_usage,
                endpoint_provider,
                str(resolved_model.model),
                getattr(exc, "token_usage", None),
            )
            code = str(getattr(exc, "code", "") or "execution_outcome_unknown")
            deterministic = code in {
                "usage_unknown",
                "pi_token_budget_exceeded",
                "pi_output_limit",
            }
            bill_committed = self._finalize(
                claim,
                status="failed" if deterministic else "outcome_unknown",
                capture=capture,
                files=generated_files,
                usage=usage,
                error_code=code,
                error_message=(
                    "本次执行用量无法核实，已停止后续调用"
                    if code == "usage_unknown"
                    else "执行结果无法确认，已暂停后续自动运行"
                ),
            )
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=usage,
                bill_committed=bill_committed,
                model_started=True,
            )
        except Exception:
            logger.exception("schedule execution failed: run=%s", claim.run_id)
            self._settle_budget_reservation(
                claim.run_id,
                reservation_id,
                usage=None,
                bill_committed=False,
                model_started=model_started,
            )
            if not model_started and self._retry_pre_model_failure(
                claim,
                capture=capture,
                error_message="执行前安全校验暂时无法完成",
            ):
                return
            self._finalize(
                claim,
                status="outcome_unknown" if model_started else "failed",
                capture=capture,
                files=generated_files,
                error_code=(
                    "execution_outcome_unknown" if model_started else "preflight_failed"
                ),
                error_message=(
                    "执行结果无法确认，已暂停后续自动运行"
                    if model_started
                    else "执行前安全校验暂时无法完成"
                ),
            )
        finally:
            heartbeat_stop.set()
            await heartbeat_task
