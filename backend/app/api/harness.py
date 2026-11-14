"""Harness API — REST endpoints for Enterprise Harness management.

Exposes task management, approval workflows, policy configuration,
verification, and audit through the admin API.

All endpoints require admin authentication.  All data is persisted to
the database (SQLite in dev, PostgreSQL in production).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.types import Receive, Scope, Send

from app.api.deps import admin_user
from app.core.config import settings
from app.core.database import transaction
from app.models.auth import User
from app.repositories import harness as harness_repo
from app.repositories import system_config as system_config_repo
from app.services.harness.audit import HarnessAuditLogger, safe_tool_arguments
from app.services.harness.credentials import (
    CredentialProvider,
    CredentialRegistry,
)
from app.services.harness.events import EventBridge
from app.services.harness.file_storage import (
    record_skill_upload,
    upload_workspace_file,
)
from app.services.harness.gateway import SandboxExecutor, ToolGateway
from app.services.harness.gateway_auth import verify_task_gateway_token
from app.services.harness.network import NetworkBroker, NetworkSecurityError
from app.services.harness.policy import DecisionKind, PolicyEngine, PolicyRule
from app.services.harness.sandbox import SandboxManager
from app.services.harness.skill_adaptation import analyze_skill
from app.services.harness.skill_catalog import (
    SkillCatalogError,
    catalog_unavailable,
    search_skill_catalog,
)
from app.services.harness.skill_definition import (
    SkillDefinitionError,
    load_skill_definition,
)
from app.services.harness.skill_fetch import (
    SkillSourceError,
    fetch_git_archive,
    fetch_url_archive,
    select_subpath,
)
from app.services.harness.skill_installation import (
    SkillInstaller,
    SkillInstallConflictError,
    SkillInstallError,
    SkillInstallNotFoundError,
    prepare_install_for_review,
    promote_install_to_enabled,
    sort_install_records_newest_first,
)
from app.services.harness.skill_integrity import SkillIntegrityError, hash_skill_tree
from app.services.harness.skill_runner import (
    SkillManifest,
    SkillSecurityError,
)
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskLimits,
    TaskPermissions,
    TaskVerification,
)
from app.services.harness.verification import VerificationEngine
from app.services.anthropic_http import anthropic_headers
from app.services.model_endpoint_config import (
    ADAPTER_AZURE_OPENAI,
    PROTOCOL_MESSAGES,
)
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID, PiRuntimeSettings
from app.services.data_guard import CloudEgressBlocked, MaskSession
from app.services.pi_runtime.model_proxy import (
    ModelProxyBudgetError,
    ModelProxyUsage,
    acquire_model_proxy_grant,
    prepare_model_proxy_request,
    record_model_proxy_usage,
    record_model_proxy_retry_after,
    release_model_proxy_grant,
)
from app.services.pi_runtime.skills import skills_dir_for_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/harness", tags=["admin-harness"])

# ---------------------------------------------------------------------------
# Singleton services
# ---------------------------------------------------------------------------

_HARNESS_WORKSPACE_BASE = Path(
    os.getenv("DOC_NEXUS_HARNESS_WORKSPACE_DIR", "/tmp/doc-nexus-harness-workspaces")
)
_MODEL_PROXY_MAX_BODY_BYTES = 10 * 1024 * 1024

_policy_engine = PolicyEngine()
_audit_logger = HarnessAuditLogger()
_event_bridge = EventBridge()
_sandbox_manager = SandboxManager(base_dir=_HARNESS_WORKSPACE_BASE)
_verification_engine = VerificationEngine()


async def _read_model_request_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="invalid Content-Length"
            ) from None
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if declared_size > _MODEL_PROXY_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="model request is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MODEL_PROXY_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="model request is too large")
        body.extend(chunk)
    return bytes(body)


def _load_policy_rules() -> None:
    """Load persisted policy rules from DB, merging with defaults."""
    try:
        with transaction() as db:
            persisted = harness_repo.load_persisted_rules(db)
        for rule in persisted:
            # Only add if not already present (default rules take precedence)
            if not any(r.id == rule.id for r in _policy_engine.rules):
                _policy_engine.add_rule(rule)
    except Exception:
        logger.warning("Failed to load persisted policy rules, using defaults")


def _minio_value(key: str, fallback: str = "") -> str:
    """Prefer Compose-owned MinIO values over legacy database overrides."""
    return (
        os.environ.get(f"DOC_NEXUS_{key.upper()}", "").strip()
        or fallback
        or system_config_repo.resolve(key)
    ).strip()


def _build_credential_registry() -> CredentialRegistry:
    """Register every server-supported credential group.

    Adding a group is a one-line provider registration; Skill manifests,
    the runner, and the API stay generic.
    """
    return CredentialRegistry(
        [
            CredentialProvider(
                credential_id="minio",
                required_keys=(
                    "MINIO_ACCESS_KEY",
                    "MINIO_SECRET_KEY",
                    "MINIO_ENDPOINT",
                ),
                env_keys=(
                    "MINIO_ENDPOINT",
                    "MINIO_ACCESS_KEY",
                    "MINIO_SECRET_KEY",
                    "MINIO_SHOW_URL",
                    "MINIO_BUCKET",
                    "MINIO_TEMP_BUCKET",
                    "MINIO_SECURE",
                ),
                source=lambda: {
                    # MinIO SDK contract: endpoint is host[:port] without
                    # scheme; the protocol is carried by MINIO_SECURE.
                    # Compose-owned values take precedence over legacy DB values.
                    "MINIO_ENDPOINT": (
                        urlsplit(_minio_value("minio_url", settings.minio_url)).netloc
                        or _minio_value("minio_url", settings.minio_url).rstrip("/")
                    ),
                    "MINIO_ACCESS_KEY": _minio_value(
                        "minio_access_key", settings.minio_access_key
                    ),
                    "MINIO_SECRET_KEY": _minio_value(
                        "minio_secret_key", settings.minio_secret_key
                    ),
                    "MINIO_SHOW_URL": _minio_value(
                        "minio_show_url", settings.minio_show_url
                    ),
                    "MINIO_BUCKET": _minio_value("minio_bucket", settings.minio_bucket),
                    "MINIO_TEMP_BUCKET": _minio_value(
                        "minio_temp_bucket", settings.minio_temp_bucket
                    ),
                    "MINIO_SECURE": "1",
                },
            ),
            CredentialProvider(
                credential_id="dingtalk",
                required_keys=("DINGTALK_WEBHOOK", "DINGTALK_SECRET"),
                env_keys=("DINGTALK_WEBHOOK", "DINGTALK_SECRET"),
                source=lambda: {
                    "DINGTALK_WEBHOOK": settings.dingtalk_webhook,
                    "DINGTALK_SECRET": settings.dingtalk_secret,
                },
            ),
        ]
    )


_credential_registry = _build_credential_registry()


def _validate_manifest_credentials(manifest: SkillManifest) -> None:
    if manifest.credentials:
        raise SkillSecurityError(
            "native sandbox Skills cannot receive long-lived credentials; "
            "use a task-scoped brokered capability"
        )


_SYSTEM_SKILL_TENANT = "_system"


# Load on module import
_load_policy_rules()


def _check_gateway_token(authorization: str | None, *, required: bool = False) -> None:
    expected = (settings.pi_harness_gateway_token or "").strip()
    if not expected:
        if required:
            raise HTTPException(
                status_code=503, detail="Harness gateway token is not configured"
            )
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="invalid Harness gateway token")


def _check_task_gateway_token(
    authorization: str | None,
    task: AgentTask,
) -> None:
    """Authenticate a sandbox broker call against persisted task identity.

    The global token remains accepted for trusted backend/admin compatibility,
    but it is never passed into a Pi sandbox. Sandboxes receive an expiring
    HMAC token tied to this exact task, tenant, and user instead.
    """
    master = (settings.pi_harness_gateway_token or "").strip()
    if not master:
        raise HTTPException(
            status_code=503, detail="Harness gateway token is not configured"
        )
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not verify_task_gateway_token(
        supplied,
        master,
        task.task_id,
        task.tenant_id,
        task.user_id,
        lease_root=PiRuntimeSettings.from_env().root,
    ):
        raise HTTPException(status_code=401, detail="invalid Harness task token")


async def _close_model_proxy_request(
    client: httpx.AsyncClient | None,
    *,
    response: httpx.Response | None,
    token: str,
    grant_root: Path,
) -> None:
    # StreamingResponse cancels its task on disconnect; cleanup must still run.
    with anyio.CancelScope(shield=True):
        try:
            if response is not None:
                await response.aclose()
        finally:
            try:
                if client is not None:
                    await client.aclose()
            finally:
                release_model_proxy_grant(token, grant_root)


class _ModelProxyResponse(StreamingResponse):
    def __init__(
        self,
        response: httpx.Response,
        client: httpx.AsyncClient,
        *,
        token: str,
        grant_root: Path,
        provider: str,
        model_name: str,
        upstream_path: str,
        usage: ModelProxyUsage | None,
        mask_session: MaskSession | None = None,
    ):
        self._upstream = response
        self._client = client
        self._token = token
        self._grant_root = grant_root
        self._provider = provider
        self._model_name = model_name
        self._upstream_path = upstream_path
        self._usage = usage
        # 请求侧脱敏过就把流里的占位符换回真实值（见 _restore_chunks）
        self._mask_session = mask_session
        self._recorded = False
        self._restore_buffer = ""
        super().__init__(
            self._stream_body(),
            status_code=response.status_code,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in {"content-type", "cache-control", "retry-after", "x-request-id"}
            },
        )

    def _record_usage(self, usage: dict[str, int] | None) -> None:
        record_model_proxy_usage(self._token, self._grant_root, usage)
        self._recorded = True

    async def _stream_body(self):
        response = self._upstream
        if response.status_code >= 400:
            error_body = await response.aread()
            error_text = (
                error_body.decode("utf-8", errors="replace")[:2000]
                if error_body
                else ""
            )
            logger.warning(
                "Upstream model endpoint returned status=%s for provider=%s "
                "model=%s path=%s: %s",
                response.status_code,
                self._provider,
                self._model_name,
                self._upstream_path,
                error_text,
            )
            if error_body:
                yield error_body
                return
        async for chunk in response.aiter_raw():
            # 用量统计必须吃原始 chunk（还原后再解析会让 token 计数失真）
            if self._usage is not None and not self._recorded:
                self._usage.feed(chunk)
                if self._usage.complete:
                    # The child can start its next call as soon as it receives
                    # this event, before the stream resumes or closes.
                    self._record_usage(self._usage.finish())
            for restored in self._restore_chunks(chunk):
                yield restored
        for restored in self._flush_restore():
            yield restored
        if self._usage is not None and not self._recorded:
            self._record_usage(self._usage.finish(stream_ended=True))

    def _restore_chunks(self, chunk: bytes):
        """把流里的占位符换回真实值。

        占位符可能被切在两个 chunk 之间，所以末尾若出现半个「〔」就留在缓冲里
        等下一块；其余部分立刻放行，避免破坏流式体验。
        """
        session = self._mask_session
        if session is None:
            yield chunk
            return
        try:
            text = self._restore_buffer + chunk.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - 解码失败时按原样放行，绝不阻断
            yield chunk
            return
        hold = text.rfind("〔")
        if hold >= 0 and "〕" not in text[hold:]:
            head, self._restore_buffer = text[:hold], text[hold:]
        else:
            head, self._restore_buffer = text, ""
        if head:
            yield session.restore_text(head).encode()
        return

    def _flush_restore(self):
        session = self._mask_session
        if session is None or not self._restore_buffer:
            return
        tail, self._restore_buffer = self._restore_buffer, ""
        yield session.restore_text(tail).encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Own cleanup outside the iterator as disconnect can happen while
            # sending headers, before the body generator is even started.
            try:
                if self._usage is not None and not self._recorded:
                    self._record_usage(None)
            finally:
                await _close_model_proxy_request(
                    self._client,
                    response=self._upstream,
                    token=self._token,
                    grant_root=self._grant_root,
                )


@router.post("/model-proxy/{token}/{upstream_path:path}")
async def proxy_pi_model_request(
    token: str,
    upstream_path: str,
    request: Request,
) -> Response:
    """Forward one model request without exposing the long-lived provider key."""
    supplied = (
        request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        or request.headers.get("api-key", "").strip()
        or request.headers.get("x-api-key", "").strip()
    )
    grant_root = PiRuntimeSettings.from_env().root / "model-grants"
    try:
        grant = acquire_model_proxy_grant(
            token,
            supplied,
            settings.pi_harness_gateway_token,
            grant_root,
        )
    except ModelProxyBudgetError as exc:
        # Provider SDKs retry 429/5xx; this is a final per-turn admission denial.
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": exc.code,
                    "message": f"[{exc.code}] {exc}",
                }
            },
        )
    if grant is None:
        raise HTTPException(status_code=401, detail="invalid or expired model grant")
    client = None
    response = None
    streaming = False
    usage = None
    try:
        body = await _read_model_request_body(request)
        model = grant.model
        try:
            prepared = prepare_model_proxy_request(
                model,
                upstream_path,
                list(request.query_params.multi_items()),
                body,
                require_usage=grant.track_usage,
            )
        except CloudEgressBlocked as exc:
            # 脱敏策略=block：宁可拒绝上云，也不能把敏感数据发出去
            logger.warning(
                "Model proxy blocked by data policy provider=%s model=%s reason=%s",
                model.provider_name,
                model.model,
                exc,
            )
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        logger.info(
            "Model proxy forwarding provider=%s model=%s upstream_host=%s path=%s api_key_present=%s",
            model.provider_name,
            model.model,
            urlsplit(prepared.url).netloc,
            upstream_path,
            bool(model.api_key),
        )
        headers = {
            "content-type": request.headers.get("content-type", "application/json"),
            "accept": request.headers.get("accept", "text/event-stream, application/json"),
            # The body is relayed with aiter_raw() and content-encoding is not
            # forwarded, so the upstream must not compress it.
            "accept-encoding": "identity",
        }
        if model.api_key:
            if model.adapter == ADAPTER_AZURE_OPENAI:
                headers["api-key"] = model.api_key
            elif model.api_protocol == PROTOCOL_MESSAGES:
                headers.update(anthropic_headers(model.api_key, model.base_url))
            else:
                headers["authorization"] = f"Bearer {model.api_key}"
        # read=None used to let a stalled upstream SSE stream hang the proxy
        # forever: pi-runtime then aborts the turn and classifies the failure as
        # the non-retryable "model_failed" catch-all, which surfaces to the user
        # as "本轮已暂停". A bounded read timeout turns that into a fast,
        # retryable error instead. Periodic SSE comments/keepalives from the
        # provider reset it, so 180s only fires on a genuine stall.
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
        )
        try:
            upstream_request = client.build_request(
                "POST",
                prepared.url,
                params=prepared.query,
                headers=headers,
                content=prepared.body,
            )
            response = await client.send(upstream_request, stream=True)
            if response.status_code >= 400:
                record_model_proxy_retry_after(
                    token,
                    grant_root,
                    response.headers.get("retry-after"),
                )
        except httpx.RequestError as exc:
            logger.warning(
                "Model endpoint request failed for provider=%s model=%s: %s",
                model.provider_name,
                model.model,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail="模型端点不可达，请检查 endpoint_url、网络和服务状态",
            ) from exc

        if grant.track_usage and 200 <= response.status_code < 300:
            usage = ModelProxyUsage(model.api_protocol)
        result = _ModelProxyResponse(
            response,
            client,
            token=token,
            grant_root=grant_root,
            provider=model.provider_name,
            model_name=model.model,
            upstream_path=upstream_path,
            usage=usage,
            mask_session=prepared.mask_session,
        )
        streaming = True
        return result
    finally:
        if not streaming:
            try:
                if usage is not None:
                    record_model_proxy_usage(token, grant_root, None)
            finally:
                await _close_model_proxy_request(
                    client, response=response, token=token, grant_root=grant_root
                )


def _skill_context(tenant_id: str):
    runtime = PiRuntimeSettings.from_env()
    agent = SimpleNamespace(id=PI_RUNTIME_AGENT_ID, tenant_id=tenant_id)
    skills_root = skills_dir_for_agent(agent, runtime)
    return runtime, agent, skills_root


def _installer_context(tenant_id: str) -> tuple[SkillInstaller, Path]:
    """Return the tenant-scoped installer and its upload-only artifact root."""
    if (
        not tenant_id
        or tenant_id in {".", ".."}
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for char in tenant_id
        )
    ):
        raise HTTPException(status_code=422, detail="invalid tenant_id")
    runtime = PiRuntimeSettings.from_env()
    installer = SkillInstaller(runtime.root / "skill-installations" / tenant_id)
    return installer, (settings.upload_dir / "skill-artifacts" / tenant_id).resolve()


def _artifact_path(artifact_root: Path, artifact_name: str) -> Path:
    normalized = artifact_name.replace("\\", "/")
    if normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in normalized.split("/")
    ):
        raise HTTPException(status_code=422, detail="invalid Skill artifact name")
    candidate = (artifact_root / normalized).resolve()
    try:
        candidate.relative_to(artifact_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Skill artifact escapes upload root"
        ) from exc
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Skill artifact not found")
    return candidate


def _existing_system_skill_path(relative_path: str, skills_root: Path) -> Path:
    """Resolve a shipped runtime Skill without allowing path traversal."""
    normalized = relative_path.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if (
        not normalized
        or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
            for char in normalized
        )
    ):
        raise HTTPException(status_code=422, detail="invalid system Skill path")

    root = skills_root.resolve()
    raw_target = root.joinpath(*parts)
    current = raw_target
    while current != root:
        if current.is_symlink():
            raise HTTPException(
                status_code=422, detail="system Skill path cannot contain symlinks"
            )
        current = current.parent
    target = raw_target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="system Skill path escapes root"
        ) from exc
    if not target.is_dir() or not (target / "SKILL.md").is_file():
        raise HTTPException(status_code=422, detail="system Skill directory is invalid")
    return target


def _skill_audit_intent(
    event_type: str,
    user: User,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "actor_id": str(user.id),
        "details": details or {},
    }


def _prepare_install_for_review(installer: SkillInstaller, request_id: str, user: User):
    return prepare_install_for_review(
        installer,
        request_id,
        scan_audit=_skill_audit_intent("skill_install_scanned", user),
        adapt_audit=_skill_audit_intent("skill_adapted", user),
    )


def _promote_install_to_enabled(installer: SkillInstaller, request_id: str, user: User):
    runtime = PiRuntimeSettings.from_env()
    return promote_install_to_enabled(
        installer,
        request_id,
        runtime.root,
        approve_audit=_skill_audit_intent("skill_install_approved", user),
        install_audit=_skill_audit_intent("skill_installed", user),
        mount_audit=_skill_audit_intent("skill_mounted", user),
        enable_audit=_skill_audit_intent("skill_enabled", user),
    )


def _flush_skill_audits(installer: SkillInstaller) -> bool:
    def deliver(event: dict[str, Any]) -> None:
        with transaction() as db:
            harness_repo.record_audit(
                db,
                event["event_type"],
                event["task_id"],
                actor_id=event["actor_id"],
                details=event["details"],
                event_id=event["id"],
            )

    try:
        installer.flush_pending_audits(deliver)
    except Exception:
        logger.exception("Skill lifecycle audit delivery is pending")
        return False
    return True


def _skill_installation_view(record: Any) -> dict[str, Any]:
    """返回生命周期与即时完整性结果，原因说明不暴露内部路径和异常。"""
    result = record.as_dict()
    result["audit_pending"] = len(getattr(record, "pending_audits", ()))
    result["integrity_message"] = None
    state = record.state.value
    if state not in {"approved", "installed", "mounted", "enabled", "revoked"}:
        result["integrity_state"] = "not_applicable"
        return result
    if not record.approved_sha256:
        result["integrity_state"] = "unverified"
        result["integrity_message"] = "缺少批准摘要，无法校验内容完整性"
        return result
    if state == "approved":
        path = Path(record.quarantine_path).parent / "materialized"
    else:
        if not record.installed_path:
            result["integrity_state"] = "unverified"
            result["integrity_message"] = "安装路径缺失，无法校验内容完整性"
            return result
        path = Path(record.installed_path)
    try:
        actual = hash_skill_tree(path)
    except (OSError, SkillIntegrityError):
        result["integrity_state"] = "tampered"
        result["integrity_message"] = "文件或目录不可用，无法安全完成完整性校验"
    else:
        if actual == record.approved_sha256:
            result["integrity_state"] = "intact"
        else:
            result["integrity_state"] = "tampered"
            result["integrity_message"] = "内容摘要与批准版本不一致"
    return result


def _get_gateway(task_id: str | None = None) -> ToolGateway:
    """Build a ToolGateway with a sandbox for the given task."""
    sandbox = None
    if task_id:
        try:
            ws = _sandbox_manager.get_workspace(task_id)
            sandbox = SandboxExecutor(
                workspace=ws,
                shell_timeout=max(1, int(settings.pi_tool_timeout_seconds)),
            )
        except Exception:
            pass
    return ToolGateway(policy=_policy_engine, sandbox=sandbox)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="default", max_length=64)
    user_id: str = Field(default="", max_length=64)
    project_id: str = Field(default="default", max_length=128)
    objective: str = Field(min_length=1)
    permissions: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    clone_repo: bool = False


class PolicyRuleCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    tool_pattern: str = Field(min_length=1, max_length=128)
    arg_pattern: str = Field(default="", max_length=512)
    decision: str = Field(pattern="^(ALLOW|DENY|ASK|REWRITE)$")
    reason: str = Field(default="", max_length=256)
    priority: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True


class ApprovalResolveRequest(BaseModel):
    approved: bool
    approver_id: str = Field(default="admin", max_length=64)
    notes: str = Field(default="", max_length=512)


class ToolExecuteRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SkillApproveRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=512)
    manifest: dict[str, Any]


class WorkspaceFileUploadRequest(BaseModel):
    """Request issued by a broker-client Skill's fixed Harness capability."""

    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default="", max_length=128)
    path: str = Field(min_length=1, max_length=1024)


class NetworkRequest(BaseModel):
    method: str = Field(min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=1_000_000)
    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default="", max_length=128)


class SkillCatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=8, ge=1, le=8)
    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default="", max_length=128)


class SkillInstallCatalogRequest(BaseModel):
    skill_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    source: str = Field(
        min_length=3,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    subpath: str = Field(min_length=1, max_length=512)
    version: str = Field(default="", max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default="", max_length=128)


class SkillRevokeRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    skill_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)


class SkillInstallRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    artifact_name: str = Field(min_length=1, max_length=512)


class SkillSourceFetchRequest(BaseModel):
    source_type: str = Field(pattern="^(git|url)$")
    url: str = Field(min_length=1, max_length=2048)
    ref: str = Field(default="", max_length=128)
    # Mirrors `npx skills add <repo> --skill <name>`: select one skill
    # directory inside the downloaded repository.
    subpath: str = Field(default="", max_length=512)
    skill_id: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=128)


class SkillAdaptRequest(BaseModel):
    skill_id: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0.0", max_length=128)


class ExistingSkillAdmissionRequest(BaseModel):
    relative_path: str = Field(min_length=1, max_length=512)


class SkillArchiveRequest(BaseModel):
    """批量归档或恢复安装记录。"""

    request_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=100
    )
    archived: bool


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


@router.post("/tasks", status_code=201)
def create_task(
    payload: TaskCreateRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Create a new agent task with an isolated workspace."""
    perms = TaskPermissions(**(payload.permissions or {}))
    limits = TaskLimits(**(payload.limits or {})) if payload.limits else TaskLimits()
    verification = (
        TaskVerification(**(payload.verification or {}))
        if payload.verification
        else TaskVerification()
    )

    task = AgentTask(
        task_id=payload.task_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id or str(user.id),
        project=ProjectContext(project_id=payload.project_id),
        objective=payload.objective,
        permissions=perms,
        limits=limits,
        verification=verification,
    )

    info = _sandbox_manager.create_workspace(task, clone=payload.clone_repo)

    with transaction() as db:
        harness_repo.create_task(db, task, workspace_path=str(info.path))
        harness_repo.record_audit(db, "task_start", task.task_id, actor_id=str(user.id))

    return {
        "task": task.as_dict(),
        "workspace": info.as_dict(),
    }


@router.get("/tasks")
def list_tasks(user: User = Depends(admin_user)) -> dict[str, Any]:
    """List all tasks from DB."""
    with transaction() as db:
        rows = harness_repo.list_tasks(db, limit=200)
        return {
            "items": [
                {
                    "task_id": r.id,
                    "tenant_id": r.tenant_id,
                    "status": r.status,
                    "objective": r.objective[:100],
                    "path": r.workspace_path,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Destroy a task's workspace and remove from DB."""
    _sandbox_manager.destroy_workspace(task_id)
    with transaction() as db:
        harness_repo.record_audit(db, "task_complete", task_id, actor_id=str(user.id))
        harness_repo.delete_task(db, task_id)
    return {"deleted": task_id}


# ---------------------------------------------------------------------------
# Approved Skill endpoints
# ---------------------------------------------------------------------------


@router.post("/skills/sources/fetch")
def fetch_skill_source(
    payload: SkillSourceFetchRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Download a third-party Skill into quarantine via the server pipeline.

    The backend performs the download (git pinned to a fixed ref, or an HTTPS
    archive), so the Pi session never touches network tooling. The artifact
    is scanned and registered automatically; the administrator then reviews
    and enables it.
    """
    import shutil
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="skill-fetch-")
    try:
        if payload.source_type == "git":
            source_path = Path(tmpdir) / "src"
            logger.info(
                "Fetching git skill source url=%s ref=%s subpath=%s",
                payload.url,
                payload.ref,
                payload.subpath,
            )
            meta = fetch_git_archive(
                payload.url, payload.ref, source_path, subpath=payload.subpath
            )
            selected_subpath = str(meta.get("subpath") or payload.subpath or "")
            if selected_subpath:
                selected = Path(tmpdir) / "selected"
                select_subpath(source_path, selected_subpath, selected)
                source_path = selected
        else:
            suffix = Path(payload.url.split("?")[0]).suffix.lower()
            source_path = Path(tmpdir) / f"archive{suffix}"
            meta = fetch_url_archive(payload.url, source_path)
        installer, _ = _installer_context(_SYSTEM_SKILL_TENANT)
        record = installer.request(
            _SYSTEM_SKILL_TENANT,
            source_path,
            skill_id=payload.skill_id,
            version=payload.version,
            audit=_skill_audit_intent(
                "skill_source_fetched",
                user,
                details={**meta, "skill_id": payload.skill_id},
            ),
        )
        record = _prepare_install_for_review(installer, record.request_id, user)
    except (SkillSourceError, SkillInstallError, NetworkSecurityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    _flush_skill_audits(installer)
    return {
        "installation": _skill_installation_view(record),
        "source": meta,
        "skill_id": record.skill_id,
        "version": record.version,
    }


@router.post("/skills/install/{tenant_id}/{request_id}/adapt")
def adapt_skill_install(
    tenant_id: str,
    request_id: str,
    payload: SkillAdaptRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Analyze a scanned Skill and record its administrator-chosen identity.

    The Skill tree is not rewritten. The report is review metadata only.
    """
    installer, _ = _installer_context(tenant_id)
    try:
        record, result = installer.adapt(
            request_id,
            payload.skill_id,
            payload.version,
            audit=_skill_audit_intent("skill_adapted", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {
        "installation": _skill_installation_view(record),
        "adaptation": result["report"],
        "manifest": result["manifest"],
    }


@router.post("/skills/install/upload")
async def upload_skill_artifact(
    file: Annotated[UploadFile, File(description="Skill 产物 ZIP/TAR")],
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Upload a Skill artifact into the shared quarantine upload area.

    The artifact lands under ``uploads/skill-artifacts/_system/``; the name
    is sanitized so the install request can only reference server-controlled
    files. Size is capped at the installer limit.
    """
    raw_name = (file.filename or "").strip()
    suffix = Path(raw_name).suffix.lower()
    if suffix not in {".zip", ".tar", ".tgz", ".gz"}:
        raise HTTPException(status_code=422, detail="Skill artifact must be ZIP/TAR")
    stem = Path(raw_name).name[: len(Path(raw_name).name) - len(Path(raw_name).suffix)]
    safe_name = (re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:96] or "artifact") + suffix
    artifact_root = (
        settings.upload_dir / "skill-artifacts" / _SYSTEM_SKILL_TENANT
    ).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    target = artifact_root / safe_name
    if target.exists():
        raise HTTPException(status_code=409, detail="artifact already exists")
    size = 0
    try:
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(
                        status_code=422, detail="Skill artifact exceeds 50MB"
                    )
                stream.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    with transaction() as db:
        harness_repo.record_audit(
            db,
            "skill_artifact_uploaded",
            f"artifact:{safe_name}",
            actor_id=str(user.id),
            details={"size": size},
        )
    return {"artifact_name": safe_name, "size": size, "upload_dir": str(artifact_root)}


@router.get("/skills/candidates")
def list_skill_candidates(user: User = Depends(admin_user)) -> dict[str, Any]:
    """List shipped Skills and their unified installation admission state."""
    runtime, _, skills_root = _skill_context(_SYSTEM_SKILL_TENANT)
    installer, _ = _installer_context(_SYSTEM_SKILL_TENANT)
    install_records = installer.list()
    candidates: list[dict[str, Any]] = []

    def matching_install(skill_dir: Path):
        matches = [
            record
            for record in install_records
            if record.source_name == skill_dir.name or record.skill_id == skill_dir.name
        ]
        return next(
            (
                record
                for state in (
                    "enabled",
                    "mounted",
                    "installed",
                    "approved",
                    "awaiting_approval",
                    "scanned",
                    "quarantined",
                    "revoked",
                )
                for record in reversed(matches)
                if record.state.value == state
            ),
            None,
        )

    if not skills_root.is_dir():
        skills_root.mkdir(parents=True, exist_ok=True)

    skill_markers = sorted(
        (
            marker
            for marker in skills_root.rglob("SKILL.md")
            if marker.is_file()
            and not marker.is_symlink()
            and not any(
                part.startswith(".") for part in marker.relative_to(skills_root).parts
            )
        ),
        key=lambda marker: marker.parent.relative_to(skills_root).as_posix(),
    )
    for marker in skill_markers:
        source_dir = marker.parent
        relative = source_dir.relative_to(skills_root).as_posix()
        install_record = matching_install(source_dir)
        admission_state = (
            "approved"
            if install_record and install_record.state.value == "enabled"
            else "revoked"
            if install_record and install_record.state.value == "revoked"
            else "unregistered"
        )
        skill_dir = source_dir
        if install_record and install_record.installed_path:
            installed_dir = Path(install_record.installed_path)
            if installed_dir.is_dir():
                skill_dir = installed_dir

        if install_record and install_record.state.value in {
            "approved",
            "installed",
            "mounted",
            "enabled",
            "revoked",
        }:
            integrity = _skill_installation_view(install_record)["integrity_state"]
            if integrity != "intact":
                candidates.append(
                    {
                        "relative_path": relative,
                        "skill_id": install_record.skill_id or source_dir.name,
                        "version": install_record.version or "latest",
                        "manifest": None,
                        "entrypoint_sha256": install_record.approved_sha256 or None,
                        "state": "invalid",
                        "kind": "invalid",
                        "stale": True,
                        "install_state": install_record.state.value,
                        "integrity_state": integrity,
                        "error": "已批准 Skill 的完整内容摘要缺失或不匹配",
                        "request_id": install_record.request_id,
                    }
                )
                continue

        manifest_path = skill_dir / "skill.manifest.json"
        if not manifest_path.is_file():
            try:
                report = analyze_skill(skill_dir)
            except (OSError, UnicodeError, ValueError) as exc:
                candidates.append(
                    {
                        "relative_path": relative,
                        "skill_id": skill_dir.name,
                        "version": "latest",
                        "manifest": None,
                        "entrypoint_sha256": None,
                        "state": "invalid",
                        "kind": "invalid",
                        "error": f"技能分析失败：{exc}",
                        "request_id": install_record.request_id
                        if install_record
                        else None,
                    }
                )
                continue
            adaptation = report.as_dict()
            prompt_only = report.prompt_only
            candidates.append(
                {
                    "relative_path": relative,
                    "skill_id": (
                        install_record.skill_id
                        if install_record and install_record.skill_id
                        else source_dir.name
                    ),
                    "version": (
                        install_record.version
                        if install_record and install_record.version
                        else "latest"
                    ),
                    "manifest": None,
                    "entrypoint_sha256": (
                        install_record.approved_sha256
                        if install_record and install_record.approved_sha256
                        else None
                    ),
                    "state": admission_state,
                    "kind": (
                        "prompt_only"
                        if prompt_only
                        else "native"
                        if admission_state == "approved"
                        else "needs_adaptation"
                    ),
                    "adaptation": adaptation,
                    "install_state": (
                        install_record.state.value if install_record else None
                    ),
                    "request_id": install_record.request_id if install_record else None,
                    "pi_available": admission_state == "approved",
                }
            )
            continue
        try:
            manifest = SkillManifest.from_dict(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            digest = (
                install_record.approved_sha256
                if install_record and install_record.approved_sha256
                else hash_skill_tree(skill_dir)
            )
        except (OSError, ValueError, SkillIntegrityError, SkillSecurityError) as exc:
            candidates.append(
                {
                    "relative_path": relative,
                    "skill_id": skill_dir.name,
                    "version": None,
                    "manifest": None,
                    "entrypoint_sha256": None,
                    "state": "invalid",
                    "kind": "invalid",
                    "error": str(exc),
                    "request_id": install_record.request_id if install_record else None,
                }
            )
            continue
        candidates.append(
            {
                "relative_path": relative,
                "skill_id": manifest.skill_id,
                "version": manifest.version,
                "manifest": manifest.as_dict(),
                "entrypoint_sha256": digest,
                "state": admission_state,
                "kind": "executable",
                "stale": False,
                "install_state": install_record.state.value if install_record else None,
                "request_id": install_record.request_id if install_record else None,
            }
        )
    return {
        "items": candidates,
        "tenant_id": _SYSTEM_SKILL_TENANT,
        "runtime_root": str(runtime.root),
    }


@router.post("/skills/admit-existing")
def admit_existing_skill(
    payload: ExistingSkillAdmissionRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Copy a runtime Skill into quarantine, then scan and register it for review."""
    runtime, _, skills_root = _skill_context(_SYSTEM_SKILL_TENANT)
    source = _existing_system_skill_path(payload.relative_path, skills_root)
    installer, _ = _installer_context(_SYSTEM_SKILL_TENANT)
    try:
        record = installer.request(
            _SYSTEM_SKILL_TENANT,
            source,
            audit=_skill_audit_intent("skill_admission_requested", user),
        )
        record = _prepare_install_for_review(installer, record.request_id, user)
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {
        "installation": _skill_installation_view(record),
        "runtime_root": str(runtime.root),
        "relative_path": payload.relative_path.replace("\\", "/").strip("/"),
    }


@router.get("/credentials")
def list_credential_groups(user: User = Depends(admin_user)) -> dict[str, Any]:
    """Credential group configuration status (names only, never values).

    Values are managed on the system-config page; this endpoint tells the
    admission page which groups are ready and which keys are missing.
    """
    return {"groups": _credential_registry.status()}


@router.post("/skills/install/request")
def request_skill_install(
    payload: SkillInstallRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Quarantine an uploaded artifact, then scan and register it for review."""
    installer, artifact_root = _installer_context(payload.tenant_id)
    source = _artifact_path(artifact_root, payload.artifact_name)
    try:
        record = installer.request(
            payload.tenant_id,
            source,
            audit=_skill_audit_intent("skill_install_requested", user),
        )
        record = _prepare_install_for_review(installer, record.request_id, user)
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.get("/skills/install/{tenant_id}/{request_id}")
def get_skill_install(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    _flush_skill_audits(installer)
    try:
        record = installer.get(request_id)
    except SkillInstallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"installation": _skill_installation_view(record)}


@router.get("/skills/install/{tenant_id}")
def list_skill_installs(
    tenant_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """管理列表按提交时间倒序，含已归档记录；不改变内部调用方顺序。"""
    installer, _ = _installer_context(tenant_id)
    _flush_skill_audits(installer)
    return {
        "items": [
            _skill_installation_view(record)
            for record in sort_install_records_newest_first(installer.list())
        ]
    }


@router.post("/skills/install/{tenant_id}/archive")
def archive_skill_installs(
    tenant_id: str,
    payload: SkillArchiveRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """原子归档或恢复，保留安装产物、批准记录和审计。"""
    installer, _ = _installer_context(tenant_id)
    try:
        records = installer.archive(
            payload.request_ids,
            payload.archived,
            audit=_skill_audit_intent(
                "skill_install_archived"
                if payload.archived
                else "skill_install_restored",
                user,
            ),
        )
    except SkillInstallNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillInstallConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {
        "items": [
            _skill_installation_view(installer.get(record.request_id))
            for record in records
        ]
    }


@router.post("/skills/install/{tenant_id}/{request_id}/scan")
def scan_skill_install(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    try:
        record = installer.scan(
            request_id,
            audit=_skill_audit_intent("skill_install_scanned", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/install/{tenant_id}/{request_id}/approve")
def approve_skill_install(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    try:
        record = installer.approve(
            request_id,
            audit=_skill_audit_intent("skill_install_approved", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/install/{tenant_id}/{request_id}/install")
def install_skill(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    try:
        record = installer.install(
            request_id,
            audit=_skill_audit_intent("skill_installed", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/install/{tenant_id}/{request_id}/mount")
def mount_skill(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    runtime = PiRuntimeSettings.from_env()
    try:
        record = installer.mount(
            request_id,
            runtime.root,
            audit=_skill_audit_intent("skill_mounted", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {
        "installation": _skill_installation_view(record),
        "runtime_root": str(runtime.root),
    }


@router.post("/skills/install/{tenant_id}/{request_id}/enable")
def enable_skill(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    installer, _ = _installer_context(tenant_id)
    try:
        record = installer.enable(
            request_id,
            audit=_skill_audit_intent("skill_enabled", user),
        )
    except (OSError, ValueError, SkillInstallError, SkillSecurityError) as exc:
        raise HTTPException(status_code=422, detail=f"Skill 启用失败：{exc}") from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/install/{tenant_id}/{request_id}/prepare")
def prepare_skill_install(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Scan and register identity so the Skill is ready for human approval."""
    installer, _ = _installer_context(tenant_id)
    try:
        record = _prepare_install_for_review(installer, request_id, user)
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/install/{tenant_id}/{request_id}/approve-enable")
def approve_and_enable_skill(
    tenant_id: str,
    request_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Approve, install, preflight, and enable in one administrator action."""
    installer, _ = _installer_context(tenant_id)
    try:
        record = _promote_install_to_enabled(installer, request_id, user)
    except SkillInstallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, ValueError, SkillSecurityError) as exc:
        raise HTTPException(status_code=422, detail=f"Skill 启用失败：{exc}") from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/approve")
def approve_skill(
    payload: SkillApproveRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Reject the retired direct-approval bypass."""
    raise HTTPException(
        status_code=410,
        detail="direct Skill approval is retired; use the quarantine installation flow",
    )


@router.get("/skills")
def list_approved_skills(
    tenant_id: str | None = None,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """List immutable Skill approval records for a tenant."""
    tenant = tenant_id or _SYSTEM_SKILL_TENANT
    installer, _ = _installer_context(tenant)
    _flush_skill_audits(installer)
    return {
        "items": [
            _skill_installation_view(record)
            for record in installer.list()
            if record.state.value in {"enabled", "revoked"}
        ]
    }


@router.post("/skills/revoke")
def revoke_skill(
    payload: SkillRevokeRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Revoke a Skill version without deleting its audit or artifact."""
    installer, _ = _installer_context(payload.tenant_id)
    try:
        record = installer.revoke(
            payload.skill_id,
            payload.version,
            audit=_skill_audit_intent("skill_revoked", user),
        )
    except SkillInstallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _flush_skill_audits(installer)
    return {"installation": _skill_installation_view(record)}


@router.post("/skills/execute")
def execute_approved_skill(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Reject the retired fixed-entrypoint execution path."""
    _check_gateway_token(authorization, required=True)
    raise HTTPException(
        status_code=410,
        detail="fixed-entrypoint Skill execution is retired; enabled Skills run natively in the sandbox",
    )


@router.post("/files/upload")
def upload_workspace_file_from_skill(
    payload: WorkspaceFileUploadRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Upload one task-workspace file through a fixed Harness capability.

    Broker-client Skills do not receive MinIO credentials and cannot select a
    storage client or an arbitrary URL.  The backend resolves the task-owned
    workspace, validates the relative path, and delegates to the same
    managed artifact uploader used by automatic workspace collection.
    """
    with transaction() as db:
        row = harness_repo.get_task(db, payload.task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Harness task not found")
    task = harness_repo.row_to_agent_task(row)
    _check_task_gateway_token(authorization, task)
    if task.tenant_id != payload.tenant_id or task.user_id != payload.user_id:
        raise HTTPException(status_code=403, detail="task identity mismatch")
    if not task.permissions.workspace_file_upload:
        raise HTTPException(status_code=403, detail="workspace file upload is disabled")

    task_workspace = task.workspace.resolve()
    resolved = SandboxExecutor(workspace=task_workspace).resolve_path(payload.path)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path outside task workspace")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="workspace file not found")

    info = upload_workspace_file(
        task_id=task.task_id,
        workspace=task_workspace,
        path=resolved,
    )
    if info is None:
        raise HTTPException(
            status_code=422,
            detail="workspace file is too large or invalid",
        )
    # Mark the file as explicitly delivered this turn so the end-of-turn
    # scan reports it even when its mtime predates the turn baseline.
    record_skill_upload(task_workspace, resolved)
    _audit_logger.log_tool_call(
        "upload_workspace_file",
        {"path": info["path"], "size": info["size"]},
        "ALLOW",
        task.task_id,
    )
    return {"file": info}


@router.post("/network/request")
def proxy_network_request(
    payload: NetworkRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Proxy an allowlisted request through the Harness SSRF guard."""
    with transaction() as db:
        row = harness_repo.get_task(db, payload.task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Harness task not found")
    task = harness_repo.row_to_agent_task(row)
    _check_task_gateway_token(authorization, task)
    if task.tenant_id != payload.tenant_id or task.user_id != payload.user_id:
        raise HTTPException(status_code=403, detail="task identity mismatch")
    if not task.permissions.network:
        raise HTTPException(status_code=403, detail="network capability is disabled")
    try:
        response = NetworkBroker().request(
            payload.method,
            payload.url,
            allowlist=list(task.permissions.network_allowlist),
            allow_public_read=task.permissions.public_web_read,
            headers=payload.headers,
            body=payload.body,
        )
    except NetworkSecurityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    _audit_logger.log_tool_call(
        "http_request",
        {"method": payload.method, "url": payload.url},
        "ALLOW",
        task.task_id,
    )
    return response.as_model_dict()


@router.post("/skills/catalog/search")
def search_skill_catalog_proxy(
    payload: SkillCatalogSearchRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Search the official Skill catalog from the backend, not the Pi session."""
    with transaction() as db:
        row = harness_repo.get_task(db, payload.task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Harness task not found")
    task = harness_repo.row_to_agent_task(row)
    _check_task_gateway_token(authorization, task)
    if task.tenant_id != payload.tenant_id or task.user_id != payload.user_id:
        raise HTTPException(status_code=403, detail="task identity mismatch")
    try:
        result = search_skill_catalog(payload.query, limit=payload.limit)
    except SkillCatalogError as exc:
        result = catalog_unavailable(payload.query, str(exc))
    _audit_logger.log_tool_call(
        "search_skill_catalog",
        {"query": result.get("query"), "count": len(result.get("items") or [])},
        "ALLOW",
        task.task_id,
    )
    return result


def _task_skill_audit_intent(
    event_type: str,
    *,
    task_id: str,
    tenant_id: str,
    user_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "actor_id": user_id,
        "details": {
            "task_id": task_id,
            "tenant_id": tenant_id,
            **(details or {}),
        },
    }


def _catalog_install_item(payload: SkillInstallCatalogRequest) -> dict[str, Any]:
    try:
        result = search_skill_catalog(payload.skill_id, limit=8)
    except SkillCatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for raw in result.get("items") or []:
        if not isinstance(raw, dict):
            continue
        if (
            raw.get("skill_id") == payload.skill_id
            and raw.get("source") == payload.source
            and raw.get("subpath") == payload.subpath
            and isinstance(raw.get("git_url"), str)
        ):
            return raw
    raise HTTPException(
        status_code=422,
        detail="Skill 必须来自当前官方目录结果，且 source/subpath 必须完全匹配",
    )


def _catalog_skill_source_path(
    source_root: Path,
    selected_subpath: str,
    selected: Path,
    *,
    skill_id: str = "",
) -> Path | None:
    """Select a catalog subpath when the sparse checkout contains it.

    The official catalog exposes a logical Skill id, not necessarily the
    repository directory.  Returning ``None`` lets the caller retry with a
    full checkout and discover the directory from ``SKILL.md``.
    """
    if not selected_subpath:
        return source_root
    candidate = source_root.joinpath(*selected_subpath.split("/"))
    if candidate.is_dir():
        select_subpath(source_root, selected_subpath, selected)
        return selected
    root_definition = source_root / "SKILL.md"
    if root_definition.is_file():
        if not skill_id:
            return source_root
        try:
            definition = load_skill_definition(root_definition)
        except SkillDefinitionError:
            return None
        if definition.name == skill_id:
            logger.info(
                "Catalog subpath %s is absent; using repository-root SKILL.md",
                selected_subpath,
            )
            return source_root
    return None


def _discover_catalog_skill_subpath(source_root: Path, skill_id: str) -> str:
    """Find the unique repository directory whose SKILL.md names *skill_id*."""
    matches: list[str] = []
    for definition_path in source_root.rglob("SKILL.md"):
        if definition_path.is_symlink() or not definition_path.is_file():
            continue
        try:
            definition = load_skill_definition(definition_path)
        except SkillDefinitionError:
            continue
        if definition.name != skill_id:
            continue
        relative = definition_path.parent.relative_to(source_root)
        matches.append(relative.as_posix() if relative.parts else "")
    if not matches:
        raise SkillSourceError(f"仓库中找不到 name={skill_id!r} 的 SKILL.md")
    if len(matches) > 1:
        raise SkillSourceError(
            f"仓库中找到多个 name={skill_id!r} 的 SKILL.md，无法安全选择"
        )
    return matches[0]


def _complete_catalog_skill_install(
    request_id: str,
    *,
    task_id: str,
    tenant_id: str,
    user_id: str,
    item: dict[str, Any],
) -> None:
    import shutil
    import tempfile

    installer, _ = _installer_context(_SYSTEM_SKILL_TENANT)
    temporary_root = Path(tempfile.mkdtemp(prefix="skill-fetch-"))
    try:
        source_root = temporary_root / "src"
        requested_subpath = str(item.get("subpath") or "")
        meta = fetch_git_archive(
            str(item["git_url"]),
            "",
            source_root,
            subpath=requested_subpath,
        )
        selected_subpath = str(meta.get("subpath") or "")
        skill_id = str(item["skill_id"])
        if selected_subpath:
            selected = temporary_root / "selected"
            resolved = _catalog_skill_source_path(
                source_root,
                selected_subpath,
                selected,
                skill_id=skill_id,
            )
            if resolved is None:
                logger.info(
                    "Catalog subpath %s is absent for %s; retrying full checkout",
                    selected_subpath,
                    skill_id,
                )
                fallback_root = temporary_root / "full-source"
                fallback_meta = fetch_git_archive(
                    str(item["git_url"]),
                    "",
                    fallback_root,
                    subpath="",
                )
                discovered_subpath = _discover_catalog_skill_subpath(
                    fallback_root, skill_id
                )
                if discovered_subpath:
                    select_subpath(fallback_root, discovered_subpath, selected)
                    source_root = selected
                else:
                    source_root = fallback_root
                meta = {
                    **meta,
                    **fallback_meta,
                    "catalog_subpath": selected_subpath,
                    "subpath": discovered_subpath,
                }
            else:
                source_root = resolved
        elif not (source_root / "SKILL.md").is_file():
            selected = temporary_root / "selected"
            discovered_subpath = _discover_catalog_skill_subpath(source_root, skill_id)
            if discovered_subpath:
                select_subpath(source_root, discovered_subpath, selected)
                source_root = selected
                meta = {**meta, "subpath": discovered_subpath}
        installer.stage_remote_source(
            request_id,
            source_root,
            source_metadata=meta,
            audit=_task_skill_audit_intent(
                "skill_source_fetched",
                task_id=task_id,
                tenant_id=tenant_id,
                user_id=user_id,
                details=meta,
            ),
        )
        prepare_install_for_review(
            installer,
            request_id,
            scan_audit=_task_skill_audit_intent(
                "skill_install_scanned",
                task_id=task_id,
                tenant_id=tenant_id,
                user_id=user_id,
            ),
            adapt_audit=_task_skill_audit_intent(
                "skill_adapted",
                task_id=task_id,
                tenant_id=tenant_id,
                user_id=user_id,
            ),
        )
    except Exception as exc:
        logger.exception("Agent Skill install request failed: %s", request_id)
        try:
            installer.fail_request(
                request_id,
                str(exc),
                audit=_task_skill_audit_intent(
                    "skill_install_failed",
                    task_id=task_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                ),
            )
        except Exception:
            logger.exception("Failed to persist Skill install failure: %s", request_id)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
        _flush_skill_audits(installer)


@router.post("/skills/catalog/request-install")
def request_skill_install_proxy(
    payload: SkillInstallCatalogRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Queue a catalog Skill for quarantine and administrator review."""
    with transaction() as db:
        row = harness_repo.get_task(db, payload.task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Harness task not found")
    task = harness_repo.row_to_agent_task(row)
    _check_task_gateway_token(authorization, task)
    if task.tenant_id != payload.tenant_id or task.user_id != payload.user_id:
        raise HTTPException(status_code=403, detail="task identity mismatch")
    if not task.permissions.skill_install_request:
        raise HTTPException(
            status_code=403,
            detail="Skill install requests are disabled for this task",
        )
    item = _catalog_install_item(payload)
    installer, _ = _installer_context(_SYSTEM_SKILL_TENANT)
    source_metadata = {
        "catalog_url": item.get("catalog_url", ""),
        "source": payload.source,
        "subpath": payload.subpath,
        "git_url": item["git_url"],
    }
    record = installer.request_remote(
        _SYSTEM_SKILL_TENANT,
        source_name=payload.skill_id,
        skill_id=payload.skill_id,
        version=payload.version,
        source_metadata=source_metadata,
        audit=_task_skill_audit_intent(
            "skill_install_requested",
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            details=source_metadata,
        ),
    )
    _audit_logger.log_tool_call(
        "request_skill_install",
        {"skill_id": payload.skill_id, "request_id": record.request_id},
        "ALLOW",
        task.task_id,
    )
    _flush_skill_audits(installer)
    background_tasks.add_task(
        _complete_catalog_skill_install,
        record.request_id,
        task_id=task.task_id,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        item=item,
    )
    return {
        "installation": _skill_installation_view(record),
        "message": "Skill 已进入隔离区，后台扫描完成后等待管理员审核",
    }


# ---------------------------------------------------------------------------
# Approval endpoints
# ---------------------------------------------------------------------------


@router.get("/approvals")
def list_approvals(
    task_id: str | None = None,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """List all pending approval requests."""
    with transaction() as db:
        return {
            "items": harness_repo.list_approvals(db, task_id=task_id, pending_only=True)
        }


@router.get("/approvals/all")
def list_all_approvals(
    task_id: str | None = None,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """List all approval requests (any status)."""
    with transaction() as db:
        return {
            "items": harness_repo.list_approvals(
                db, task_id=task_id, pending_only=False
            )
        }


@router.post("/approvals/{request_id}/resolve")
def resolve_approval(
    request_id: str,
    payload: ApprovalResolveRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Approve or reject a pending request."""
    with transaction() as db:
        req = harness_repo.resolve_approval(
            db,
            request_id,
            approved=payload.approved,
            approver_id=payload.approver_id,
            notes=payload.notes,
        )
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    _audit_logger.log_approval_resolved(
        req.task_id,
        request_id,
        approved=payload.approved,
        approver_id=payload.approver_id,
    )

    # If approved, also resolve in the gateway
    if payload.approved:
        gw = _get_gateway(req.task_id)
        gw.resolve_approval(request_id, approved=True)

    return {
        "request_id": req.id,
        "task_id": req.task_id,
        "status": req.status,
        "approver_id": req.approver_id,
    }


# ---------------------------------------------------------------------------
# Policy endpoints
# ---------------------------------------------------------------------------


@router.get("/policies")
def list_policies(user: User = Depends(admin_user)) -> dict[str, Any]:
    """List all policy rules (in-memory + DB)."""
    return {"items": _policy_engine.list_rules()}


@router.post("/policies", status_code=201)
def add_policy(
    payload: PolicyRuleCreateRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Add a new policy rule (persisted to DB + loaded into engine)."""
    try:
        decision = DecisionKind(payload.decision)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid decision: {payload.decision}"
        ) from exc

    rule = PolicyRule(
        id=payload.id,
        tool_pattern=payload.tool_pattern,
        arg_pattern=payload.arg_pattern,
        decision=decision,
        reason=payload.reason,
        priority=payload.priority,
        enabled=payload.enabled,
    )

    # Persist to DB
    with transaction() as db:
        harness_repo.upsert_policy_rule(db, rule)

    # Add to in-memory engine
    _policy_engine.remove_rule(rule.id)
    _policy_engine.add_rule(rule)

    return {"added": rule.id, "rules_count": len(_policy_engine.rules)}


@router.delete("/policies/{rule_id}")
def remove_policy(
    rule_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Remove a policy rule by ID (from DB + engine)."""
    with transaction() as db:
        removed = harness_repo.delete_policy_rule(db, rule_id)
    _policy_engine.remove_rule(rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return {"removed": rule_id}


# ---------------------------------------------------------------------------
# Verification endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/verify")
async def verify_task(
    task_id: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Run verification for a task (loads config from DB)."""
    with transaction() as db:
        row = harness_repo.get_task(db, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    task = harness_repo.row_to_agent_task(row)
    result = await _verification_engine.verify(task)
    _audit_logger.log_verification(task_id, result.status.value, attempt=result.attempt)
    return result.as_dict()


# ---------------------------------------------------------------------------
# Tool execution endpoint (for testing/debugging)
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/execute")
def execute_tool(
    task_id: str,
    payload: ToolExecuteRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """Execute a tool call through the Gateway (for testing)."""
    # Load task from DB for permission checking
    with transaction() as db:
        row = harness_repo.get_task(db, task_id)
    task = (
        harness_repo.row_to_agent_task(row)
        if row
        else AgentTask(
            task_id=task_id,
            tenant_id="",
            user_id=str(user.id),
            project=ProjectContext(project_id="", workspace=""),
            objective="",
        )
    )

    gw = _get_gateway(task_id)
    result = gw.execute(payload.tool_name, payload.arguments, task)

    # Persist ASK decisions to DB
    if result.approval_required and result.approval_request_id:
        with transaction() as db:
            harness_repo.create_approval(
                db,
                request_id=result.approval_request_id,
                task_id=task_id,
                tool_name=payload.tool_name,
                arguments=safe_tool_arguments(
                    payload.arguments, tool_name=payload.tool_name, truncate=False
                ),
                reason=result.error,
                rule_id=result.policy_decision.rule_id
                if result.policy_decision
                else "",
            )

    _audit_logger.log_tool_call(
        payload.tool_name,
        payload.arguments,
        "ALLOW"
        if result.success
        else (
            "DENY"
            if result.is_denied
            else "ASK"
            if result.approval_required
            else "FAIL"
        ),
        task_id,
    )

    return result.as_dict()


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------


@router.get("/audit")
def list_audit(
    task_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    """List audit entries from DB."""
    entries = _audit_logger.list_entries(
        task_id=task_id, event_type=event_type, limit=limit
    )
    return {"items": entries, "total": _audit_logger.count()}


# ---------------------------------------------------------------------------
# Runtime status endpoint
# ---------------------------------------------------------------------------


@router.get("/runtime/status")
def runtime_status(user: User = Depends(admin_user)) -> dict[str, Any]:
    """Get Harness runtime status."""
    with transaction() as db:
        pending = harness_repo.count_pending_approvals(db)
    return {
        "harness": "active",
        "policy_rules": len(_policy_engine.rules),
        "pending_approvals": pending,
        "active_workspaces": len(_sandbox_manager.list_active()),
        "audit_entries": _audit_logger.count(),
    }


# ---------------------------------------------------------------------------
# Tool evaluate endpoint (called by Pi extensions)
# ---------------------------------------------------------------------------


class ToolEvaluateRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str = Field(default="", max_length=128)
    workspace: str = Field(default="", max_length=512)
    tenant_id: str = Field(default="default", max_length=64)
    user_id: str = Field(default="", max_length=64)


@router.post("/tools/evaluate")
def evaluate_tool_call(
    payload: ToolEvaluateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Evaluate a tool call via the Policy Engine without executing it."""
    task: AgentTask | None = None
    if payload.task_id:
        with transaction() as db:
            row = harness_repo.get_task(db, payload.task_id)
        if row is not None:
            task = harness_repo.row_to_agent_task(row)
        else:
            task = AgentTask(
                task_id=payload.task_id,
                tenant_id=payload.tenant_id,
                user_id=payload.user_id,
                project=ProjectContext(
                    project_id="",
                    workspace=payload.workspace,
                ),
                objective="",
            )

    if task is not None:
        _check_task_gateway_token(authorization, task)
    else:
        _check_gateway_token(authorization)

    decision = _policy_engine.evaluate(payload.tool_name, payload.arguments, task)

    # Build result based on policy decision only (no execution)
    from app.services.harness.gateway import ToolResult

    if decision.kind == DecisionKind.DENY:
        result = ToolResult.denied(decision.reason, decision=decision)
    elif decision.kind == DecisionKind.ASK:
        import uuid as _uuid

        request_id = f"apr-{_uuid.uuid4().hex[:12]}"
        result = ToolResult.approval_pending(
            request_id, decision.reason, decision=decision
        )
    elif decision.kind == DecisionKind.REWRITE:
        result = ToolResult(
            success=True,
            rewritten=True,
            output=f"rewrite: {decision.reason}",
            policy_decision=decision,
        )
    else:
        result = ToolResult.ok("", policy_decision=decision)

    # Build result based on policy decision only (no execution)
    decision_kind = (
        "ALLOW"
        if result.success
        else (
            "DENY"
            if result.is_denied
            else "ASK"
            if result.approval_required
            else "FAIL"
        )
    )
    logger.info(
        "[Harness] evaluate tool=%s args=%s → %s rule=%s reason=%s",
        payload.tool_name,
        {
            k: str(v)[:80]
            for k, v in safe_tool_arguments(
                payload.arguments, tool_name=payload.tool_name
            ).items()
        },
        decision_kind,
        decision.rule_id or "-",
        decision.reason or "-",
    )
    _audit_logger.log_tool_call(
        payload.tool_name,
        payload.arguments,
        decision_kind,
        payload.task_id or "anonymous",
    )

    # Persist ASK decisions
    if result.approval_required and result.approval_request_id:
        try:
            with transaction() as db:
                harness_repo.create_approval(
                    db,
                    request_id=result.approval_request_id,
                    task_id=payload.task_id or "anonymous",
                    tool_name=payload.tool_name,
                    arguments=safe_tool_arguments(
                        payload.arguments, tool_name=payload.tool_name, truncate=False
                    ),
                    reason=result.error,
                    rule_id=(
                        result.policy_decision.rule_id if result.policy_decision else ""
                    ),
                )
        except Exception:
            logger.exception("Failed to persist tool approval request")

    return result.as_dict()
