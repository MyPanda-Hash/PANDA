import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    assistant_rules,
    briefing,
    doc_watches,
    init_wizard,
    internal_mes,
    user_avatar,
    access_requests,
    admin_data_audit,
    admin_notifications,
    admin_roles,
    admin_users,
    agent_capabilities,
    asr,
    assistant_watch,
    auth,
    chat,
    document_uploads,
    documents,
    employee_profiles,
    files,
    governance,
    group_chat,
    harness,
    image_generation,
    inapp_notifications,
    knowledge_graph,
    mes_identity,
    model_endpoints,
    notification_channels,
    open_chat,
    onboarding,
    pi_runtime,
    provider_keys,
    qa,
    search,
    schedule_proposals,
    schedules,
    spaces,
    system_config,
    tasks,
    tts,
    usage,
    user_channel_bindings,
    user_memory,
    video_generation,
    voice,
    wecom,
    writing,
    writing_agent_tools,
    writing_collab,
)
from app.core.config import settings, validate_runtime_security
from app.core.database import initialize_database, transaction
from app.core.logging import setup_logging
from app.core.request_middleware import RequestTraceMiddleware
from app.core.security import hash_password, new_api_key, now, verify_password
from app.models import User
from app.models.access import Role, UserRole
from app.repositories import audit, users
from app.repositories import spaces as spaces_repo
from app.repositories.access import seed_system_access
from app.services import linkmind
from app.services.default_endpoints import (
    ensure_linkmind_default_endpoints,
    refresh_existing_default_chat_endpoint,
)
from app.services.background_workers import BackgroundWorkerService
from app.services.harness.builtin_skills import ensure_configured_builtin_skills
from app.services.pi_runtime.admission import shutdown_pi_admission


# Keep shutdown bounded while allowing an in-flight chat turn to persist.
_CHAT_DRAIN_TIMEOUT_SECONDS = 45.0

def _ensure_configured_buckets() -> list[str]:
    from app.services import minio_storage

    return minio_storage.ensure_configured_buckets()


@asynccontextmanager
async def lifespan(_: FastAPI):
    chat.set_chat_draining(False)
    setup_logging()
    log = logging.getLogger(__name__)
    log.info("application starting")
    validate_runtime_security(settings)
    if (
        settings.environment.lower() in {"production", "prod"}
        and settings.writing_evidence_provider == "mock"
    ):
        raise RuntimeError(
            "DOC_NEXUS_WRITING_EVIDENCE_PROVIDER=mock is forbidden in production"
        )
    if settings.database_url.startswith("sqlite"):
        initialize_database()
    with transaction() as db:
        seed_system_access(db)
        user = users.get_bootstrap_admin(db, settings.bootstrap_username)
        if user is not None and user.role != "admin":
            raise RuntimeError(
                "bootstrap username is already owned by a non-bootstrap user"
            )
        if not user:
            validate_runtime_security(settings, require_bootstrap_password=True)
            user_id = f"usr_{uuid4()}"
            user = User(
                id=user_id,
                username=settings.bootstrap_username,
                email=f"{settings.bootstrap_username}@local.example",
                display_name="系统管理员",
                password_hash=hash_password(settings.bootstrap_password),
                api_key=new_api_key(),
                role="admin",
                first_login=True,
                created_at=now(),
            )
            db.add(user)
            db.flush()
            audit.record(db, user_id, "bootstrap_admin_created", user_id, "success")
        if settings.linkmind_enabled and user.first_login:
            user.linkmind_login_status = "logged_out"
        users.remember_bootstrap_admin(db, user)
        if user.first_login:
            validate_runtime_security(settings, require_bootstrap_password=True)
            if not verify_password(user.password_hash, settings.bootstrap_password):
                user.password_hash = hash_password(settings.bootstrap_password)
                user.auth_version += 1
                users.revoke_sessions(db, user.id)
                audit.record(
                    db,
                    user.id,
                    "bootstrap_setup_secret_rotated",
                    user.id,
                    "success",
                )
        admin_role = db.query(Role).filter(Role.code == "system_admin").one()
        if (
            not db.query(UserRole)
            .filter(UserRole.user_id == user.id, UserRole.role_id == admin_role.id)
            .first()
        ):
            db.add(UserRole(user_id=user.id, role_id=admin_role.id))
        default_space = spaces_repo.ensure_default_space(db, user.id)
        if default_space is not None:
            audit.record(
                db,
                user.id,
                "bootstrap_default_space_created",
                default_space.id,
                "success",
            )
        if settings.linkmind_enabled:
            endpoint_ids = ensure_linkmind_default_endpoints(db)
            log.info("ensured LinkMind default model endpoints: %s", endpoint_ids)
        elif refresh_existing_default_chat_endpoint(db):
            log.info("refreshed existing local chat endpoint defaults")
    if linkmind.enabled() and not user.first_login:
        try:
            linkmind.sync_user_by_id(user.id)
            linkmind.ensure_user_key(user.id)
        except linkmind.LinkMindError as exc:
            logging.getLogger(__name__).warning(
                "bootstrap LinkMind User Key provisioning failed: %s", exc.detail
            )
    try:
        skill_bootstrap = ensure_configured_builtin_skills(
            settings.pi_builtin_skill_allowlist
        )
        if any(skill_bootstrap.values()):
            logging.getLogger(__name__).info(
                "builtin Skill bootstrap result: %s", skill_bootstrap
            )
    except Exception:
        logging.getLogger(__name__).exception("builtin Skill bootstrap failed")
    try:
        created = await asyncio.to_thread(_ensure_configured_buckets)
        if created:
            logging.getLogger(__name__).info("minio buckets created: %s", created)
    except Exception:
        logging.getLogger(__name__).exception("minio bucket ensure skipped")
    worker_service = BackgroundWorkerService()
    await worker_service.start()

    # ``all`` preserves the legacy single-process topology. Deployments may run
    # this API as role=api and start the explicit durable worker entrypoint.
    try:
        yield
    finally:
        try:
            chat.set_chat_draining(True)
            remaining_chat_tasks = await chat.drain_background_chat_tasks(
                timeout_seconds=_CHAT_DRAIN_TIMEOUT_SECONDS
            )
            log.info(
                "chat background task drain finished; remaining_tasks=%d",
                remaining_chat_tasks,
            )
            if remaining_chat_tasks:
                log.warning(
                    "chat background tasks remain after bounded drain: %d",
                    remaining_chat_tasks,
                )
        except Exception:
            log.exception("chat background task drain failed")
        finally:
            try:
                await worker_service.close()
            finally:
                try:
                    await shutdown_pi_admission(
                        float(settings.worker_shutdown_grace_seconds)
                    )
                except Exception:
                    log.exception("Pi execution admission shutdown failed")
                # The process exits after lifespan shutdown. Resetting keeps
                # in-process ASGI test clients isolated across lifespans.
                chat.set_chat_draining(False)


app = FastAPI(title="Doc Nexus AI API", version="0.1.0", lifespan=lifespan)


def _cors_origins(raw: str) -> list[str]:
    """Parse DOC_NEXUS_FRONTEND_ORIGIN (comma-separated) and add localhost aliases."""
    origins: list[str] = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    for origin in list(origins):
        match = re.match(r"^(https?)://[^:/]+:(\d+)$", origin)
        if not match:
            continue
        scheme, port = match.group(1), match.group(2)
        for host in ("localhost", "127.0.0.1"):
            alias = f"{scheme}://{host}:{port}"
            if alias not in origins:
                origins.append(alias)
    return origins or ["http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(settings.frontend_origin),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["Content-Disposition", "X-Filename"],
)
app.add_middleware(RequestTraceMiddleware)
app.include_router(auth.router)
app.include_router(mes_identity.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(voice.router)
app.include_router(inapp_notifications.router)
app.include_router(assistant_rules.router)
app.include_router(admin_data_audit.router)
app.include_router(admin_users.router)
app.include_router(model_endpoints.router)
app.include_router(admin_roles.router)
app.include_router(governance.router)
app.include_router(spaces.router)
app.include_router(group_chat.router)
app.include_router(assistant_watch.router)
app.include_router(employee_profiles.router)
app.include_router(group_chat.todo_router)
app.include_router(access_requests.router)
app.include_router(documents.router)
app.include_router(document_uploads.router)
app.include_router(knowledge_graph.router)
app.include_router(qa.router)
app.include_router(chat.router)
app.include_router(video_generation.router)
app.include_router(image_generation.router)
app.include_router(pi_runtime.router)
app.include_router(onboarding.router)
app.include_router(agent_capabilities.router)
app.include_router(user_memory.router)
app.include_router(user_memory.internal_router)
app.include_router(init_wizard.router)
app.include_router(internal_mes.router)
app.include_router(user_avatar.router)
app.include_router(briefing.router)
app.include_router(doc_watches.router)
app.include_router(user_channel_bindings.router)
app.include_router(writing.router)
app.include_router(writing_collab.router)
app.include_router(writing_agent_tools.router)
app.include_router(usage.router)
app.include_router(wecom.router)
app.include_router(system_config.router)
app.include_router(admin_notifications.router)
app.include_router(notification_channels.router)
app.include_router(schedules.router)
app.include_router(schedule_proposals.router)
app.include_router(tasks.router)
app.include_router(harness.router)
app.include_router(files.router)
app.include_router(open_chat.router)
app.include_router(provider_keys.router)
app.include_router(search.router)


# 构建标记：/health 用它区分当前实际运行的镜像版本，升级验收按此判断新镜像是否生效。
# 离线交付构建由 package-offline-amd64.py 注入 --build-arg DOC_NEXUS_BUILD_ID=<版本>；
# 源码部署没有该变量时回退为 dev，避免把上一次发布的版本号当成当前版本。
BUILD_ID = os.environ.get("DOC_NEXUS_BUILD_ID", "").strip() or "dev"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "build": BUILD_ID}
