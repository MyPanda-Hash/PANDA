from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import transaction
from app.core.security import new_token, now
from app.models.model_endpoints import ModelEndpoint
from app.models.system_config import SystemConfig
from app.repositories.model_endpoints import sync_legacy_models
from app.services import linkmind

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_EMBEDDING_URL = "http://host.docker.internal:8888/v1"
DEFAULT_CHAT_MODEL = "Alibaba/qwen3.7-plus"
DEFAULT_CHAT_EXTRA_MODELS = (
    "DeepSeek/deepseek-v4-flash,DeepSeek/deepseek-v4-pro,MiniMax/MiniMax-M3"
)


def _linkmind_model_url() -> str:
    """Return the configured LinkMind OpenAI-compatible model base URL."""
    base = (settings.linkmind_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def default_chat_url() -> str:
    return _linkmind_model_url()


def default_chat_models_url() -> str:
    base = default_chat_url()
    return f"{base}/models" if base else ""


def default_embedding_url() -> str:
    return (settings.embedding_api_url or "").strip().rstrip("/") or DEFAULT_EMBEDDING_URL


def _default_secret_ref() -> str | None:
    return linkmind.LINKMIND_ENDPOINT_SECRET_REF if settings.linkmind_enabled else None


def _configured_endpoint_urls(capability: str) -> list[str]:
    """Return active configured endpoint URLs for a capability, from the DB."""
    with transaction() as db:
        return [
            ep.endpoint_url
            for ep in db.scalars(
                select(ModelEndpoint).where(
                    ModelEndpoint.capability == capability,
                    ModelEndpoint.status == "active",
                    ModelEndpoint.endpoint_url.is_not(None),
                )
            )
        ]


def _models_url(base: str) -> str:
    """endpoint_url 可能已含 /v1，避免拼成 /v1/v1/models."""
    base = base.rstrip("/")
    return f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"


async def probe_default_endpoints(
    user_id: str | None = None,
) -> dict[str, Any]:
    """探测可用 Chat 端点；任一 chat 端点可用即视为系统已就绪.

    - 探测目标是数据库里已配置的 chat 端点 + `.env` 里的 LinkMind 网关；
      不默认探测本机硬编码端点（host.docker.internal 可能根本没有服务）。
    - linkmind_enabled=0 时不要求 LinkMind User Key；401/403 视为服务在线
      （只是缺 Key），仅连接失败/5xx 才算不可达。
    - 可用性判定只看 chat 端点；embedding 端点仅作为信息返回，不参与判定。
    """
    chat_ok = False
    detected_chat_model = DEFAULT_CHAT_MODEL
    embedding_ok = False
    chat_headers: dict[str, str] = {}

    if settings.linkmind_enabled:
        if not user_id:
            logger.warning("Cannot probe LinkMind models without a current user")
        else:
            try:
                user_key = await asyncio.to_thread(linkmind.ensure_user_key, user_id)
                chat_headers["Authorization"] = f"Bearer {user_key}"
            except linkmind.LinkMindError as exc:
                logger.warning(
                    "Cannot resolve LinkMind User Key for model probe user_id=%s error=%s",
                    user_id,
                    exc.detail,
                )

    configured_chat_urls = _configured_endpoint_urls("chat")
    configured_embedding_urls = _configured_endpoint_urls("embedding")

    gateway = (settings.linkmind_base_url or "").strip().rstrip("/")
    if gateway and gateway not in configured_chat_urls:
        configured_chat_urls.append(gateway)

    # 1. 检测 Chat 端点：任一候选 URL 的 /v1/models 返回 200/401/403 即可用
    async def _chat_reachable(base: str) -> bool:
        url = _models_url(base)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url, headers=chat_headers)
                # 401/403 表示服务在线但缺有效 Key（LinkMind 关闭时无 User Key），
                # 只有连接失败/5xx 才算不可达
                return resp.status_code in (200, 401, 403)
        except Exception as exc:
            logger.debug("Failed to probe chat endpoint %s: %s", url, exc)
            return False

    reachable_chat_url = ""
    for base in configured_chat_urls:
        if await _chat_reachable(base):
            chat_ok = True
            reachable_chat_url = base
            break

    # 2. 检测 Embedding 端点：仅作信息返回，不参与 all_ready 判定
    async def _embedding_reachable(base: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(base.rstrip("/"))
                return resp.status_code in (200, 404, 405, 301, 302)
        except Exception as exc:
            logger.debug("Failed to probe embedding endpoint %s: %s", base, exc)
            return False

    reachable_embedding_url = ""
    for base in configured_embedding_urls:
        if await _embedding_reachable(base):
            embedding_ok = True
            reachable_embedding_url = base
            break

    return {
        # 可用性判定只看 chat 端点：任一 chat 端点在线即就绪，不要求 embedding
        "all_ready": chat_ok,
        "chat": {
            "reachable": chat_ok,
            "endpoint_url": reachable_chat_url,
            "extra_models": DEFAULT_CHAT_EXTRA_MODELS,
            "model": detected_chat_model,
        },
        "embedding": {
            "reachable": embedding_ok,
            "endpoint_url": reachable_embedding_url,
            "model": DEFAULT_EMBEDDING_MODEL,
        },
    }


def _local_chat_reachable() -> bool:
    """Probe the configured LinkMind OpenAI-compatible model gateway."""
    models_url = default_chat_models_url()
    if not models_url:
        return False
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(models_url)
            return resp.status_code in (200, 401, 403)
    except Exception as exc:
        logger.debug("Failed to probe model gateway %s: %s", models_url, exc)
        return False


def refresh_existing_default_chat_endpoint(db: Session) -> bool:
    """Refresh the configured LinkMind model endpoint on startup.

    This helper is retained for existing installations. New LinkMind-enabled
    installations use ``ensure_linkmind_default_endpoints`` instead.
    """
    default_url = default_chat_url()
    if not default_url:
        return False
    endpoint = next(
        (
            item
            for item in db.scalars(
                select(ModelEndpoint).where(ModelEndpoint.capability == "chat")
            )
            if item.endpoint_url.rstrip("/") == default_url
        ),
        None,
    )

    if endpoint is None:
        return False
    if not settings.linkmind_enabled:
        if endpoint.status != "unavailable":
            endpoint.status = "unavailable"
            endpoint.updated_at = now()
            return True
        return False
    if not _local_chat_reachable():
        if endpoint.status != "unavailable":
            endpoint.status = "unavailable"
            endpoint.updated_at = now()
            return True
        return False

    # Do not rewrite model / extra_models — admin sort_order and catalog are source of truth.
    secret_ref = _default_secret_ref()
    changed = endpoint.status != "active" or endpoint.secret_ref != secret_ref
    if changed:
        endpoint.status = "active"
        endpoint.secret_ref = secret_ref
        endpoint.updated_at = now()
    return changed

def ensure_linkmind_default_endpoints(db: Session) -> dict[str, str]:
    """Idempotently install the fixed LinkMind model catalog and routes."""
    current_time = now()
    chat_url = default_chat_url()
    embedding_url = default_embedding_url()
    if not chat_url or not embedding_url:
        logger.warning(
            "LinkMind defaults skipped: DOC_NEXUS_LINKMIND_BASE_URL is not configured"
        )
        return {}

    def canonical_endpoint(
        capability: str, endpoint_url: str, model: str
    ) -> ModelEndpoint | None:
        endpoint = db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.provider == "local",
                ModelEndpoint.model == model,
                ModelEndpoint.capability == capability,
                ModelEndpoint.region == "",
            )
            .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
        )
        if endpoint is not None:
            return endpoint
        return db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.capability == capability,
                ModelEndpoint.endpoint_url == endpoint_url,
            )
            .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
        )

    chat_ep = canonical_endpoint("chat", chat_url, DEFAULT_CHAT_MODEL)
    if chat_ep is None:
        chat_ep = ModelEndpoint(
            id=f"ep-{new_token(12)}",
            provider="local",
            adapter="openai_compatible",
            api_protocol="chat_completions",
            model=DEFAULT_CHAT_MODEL,
            extra_models=DEFAULT_CHAT_EXTRA_MODELS,
            sort_order=0,
            capability="chat",
            endpoint_url=chat_url,
            status="active",
            # qwen3.7-plus supports multimodal image input via LinkMind.
            supports_vision=True,
            secret_ref=linkmind.LINKMIND_ENDPOINT_SECRET_REF,
            created_at=current_time,
            updated_at=current_time,
        )
        db.add(chat_ep)
    else:
        chat_ep.provider = "local"
        chat_ep.adapter = "openai_compatible"
        chat_ep.api_protocol = "chat_completions"
        chat_ep.model = DEFAULT_CHAT_MODEL
        chat_ep.extra_models = DEFAULT_CHAT_EXTRA_MODELS
        chat_ep.capability = "chat"
        chat_ep.endpoint_url = chat_url
        chat_ep.region = ""
        chat_ep.sort_order = 0
        chat_ep.status = "active"
        chat_ep.supports_vision = True
        chat_ep.secret_ref = linkmind.LINKMIND_ENDPOINT_SECRET_REF
        chat_ep.updated_at = current_time
    sync_legacy_models(chat_ep, timestamp=current_time)
    db.flush()

    embedding_ep = canonical_endpoint(
        "embedding", embedding_url, DEFAULT_EMBEDDING_MODEL
    )
    if embedding_ep is None:
        embedding_ep = ModelEndpoint(
            id=f"ep-{new_token(12)}",
            provider="local",
            adapter="openai_compatible",
            api_protocol="chat_completions",
            model=DEFAULT_EMBEDDING_MODEL,
            sort_order=0,
            capability="embedding",
            endpoint_url=embedding_url,
            secret_ref=linkmind.LINKMIND_ENDPOINT_SECRET_REF,
            status="active",
            created_at=current_time,
            updated_at=current_time,
        )
        db.add(embedding_ep)
    else:
        embedding_ep.provider = "local"
        embedding_ep.adapter = "openai_compatible"
        embedding_ep.api_protocol = "chat_completions"
        embedding_ep.model = DEFAULT_EMBEDDING_MODEL
        embedding_ep.capability = "embedding"
        embedding_ep.endpoint_url = embedding_url
        embedding_ep.region = ""
        embedding_ep.extra_models = ""
        embedding_ep.sort_order = 0
        embedding_ep.status = "active"
        embedding_ep.updated_at = current_time
        embedding_ep.secret_ref = linkmind.LINKMIND_ENDPOINT_SECRET_REF
    sync_legacy_models(embedding_ep, timestamp=current_time)
    db.flush()

    for key, value in {
        "pi_chat_model": chat_ep.id,
        "embedding_model": embedding_ep.id,
    }.items():
        config = db.get(SystemConfig, key)
        if config is None:
            db.add(SystemConfig(key=key, value=value, updated_at=current_time))
        else:
            config.value = value
            config.updated_at = current_time

    return {
        "chat_endpoint_id": chat_ep.id,
        "embedding_endpoint_id": embedding_ep.id,
    }


def _first_endpoint(
    db: Session, capability: str, *, active_only: bool
) -> ModelEndpoint | None:
    query = select(ModelEndpoint).where(ModelEndpoint.capability == capability)
    if active_only:
        query = query.where(ModelEndpoint.status == "active")
    return db.scalar(
        query.order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
    )


def auto_provision_default_endpoints(
    db: Session, probe_result: dict[str, Any], user_id: str
) -> dict[str, str]:
    """把探测到的可用 chat 端点绑定到系统模型路由；已有端点时不重复创建.

    - 复用顺序：已配置为 pi_chat_model 的端点 > 任意已存在的同 capability 端点
      （即使 disabled/unavailable，激活之）> 按探测到的可达 URL 新建。
    - 用 probe_result 里实际可达的 endpoint_url，而非硬编码 host.docker.internal。
    - URL 匹配兼容末尾 /v1 差异（193:8080 vs 193:8080/v1）。
    """
    current_time = now()
    chat_config = probe_result.get("chat", {})
    embedding_config = probe_result.get("embedding", {})
    chat_url = (chat_config.get("endpoint_url") or "").rstrip("/")
    embedding_url = (embedding_config.get("endpoint_url") or "").rstrip("/")
    chat_model_name = chat_config.get("model") or DEFAULT_CHAT_MODEL
    chat_extra_models = chat_config.get("extra_models") or DEFAULT_CHAT_EXTRA_MODELS

    def _url_match(ep_url: str, target: str) -> bool:
        ep = ep_url.rstrip("/")
        t = target.rstrip("/")
        return ep == t or ep == t + "/v1" or ep + "/v1" == t

    def _pick_existing(capability: str, url: str) -> ModelEndpoint | None:
        rows = list(
            db.scalars(
                select(ModelEndpoint)
                .where(ModelEndpoint.capability == capability)
                .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
            )
        )
        # 1) 已配置路由指向的 active 端点（用户禁用的不重新激活）
        if capability == "chat":
            cfg = db.get(SystemConfig, "pi_chat_model")
            if cfg and cfg.value:
                for ep in rows:
                    if ep.id == cfg.value and ep.status == "active":
                        return ep
        # 2) URL 匹配（含 /v1 差异）且 active
        for ep in rows:
            if url and ep.status == "active" and _url_match(ep.endpoint_url, url):
                return ep
        # 3) 任意 active 端点
        for ep in rows:
            if ep.status == "active":
                return ep
        # 4) 完全没有 active 端点时才回退到任意已存在端点（激活它）
        return rows[0] if rows else None

    # 1. Chat 端点：优先复用已有
    chat_ep = _pick_existing("chat", chat_url)
    if chat_ep is not None:
        chat_ep.status = "active"
        chat_ep.updated_at = current_time
    elif chat_url:
        chat_ep = ModelEndpoint(
            id=f"ep-{new_token(12)}",
            provider="local",
            adapter="openai_compatible",
            api_protocol="chat_completions",
            model=chat_model_name,
            extra_models=chat_extra_models,
            sort_order=0,
            capability="chat",
            endpoint_url=chat_url,
            status="active",
            secret_ref=_default_secret_ref(),
            created_at=current_time,
            updated_at=current_time,
        )
        sync_legacy_models(chat_ep, timestamp=current_time)
        db.add(chat_ep)
        db.flush()

    # 2. Embedding 端点：同样优先复用已有
    emb_ep = _pick_existing("embedding", embedding_url)
    if emb_ep is not None:
        emb_ep.status = "active"
        emb_ep.updated_at = current_time
    elif embedding_url:
        emb_ep = ModelEndpoint(
            id=f"ep-{new_token(12)}",
            provider="local",
            adapter="openai_compatible",
            api_protocol="chat_completions",
            model=DEFAULT_EMBEDDING_MODEL,
            capability="embedding",
            endpoint_url=embedding_url,
            status="active",
            sort_order=0,
            created_at=current_time,
            updated_at=current_time,
        )
        sync_legacy_models(emb_ep, timestamp=current_time)
        db.add(emb_ep)
        db.flush()

    # 3. 写入系统全局配置关联
    configs: dict[str, str] = {}
    if chat_ep is not None:
        configs["pi_chat_model"] = chat_ep.id
    if emb_ep is not None:
        configs["embedding_model"] = emb_ep.id
    for key, val in configs.items():
        cfg = db.get(SystemConfig, key)
        if not cfg:
            cfg = SystemConfig(key=key, value=val, updated_at=current_time)
            db.add(cfg)
        else:
            cfg.value = val
            cfg.updated_at = current_time

    return {
        "chat_endpoint_id": chat_ep.id if chat_ep else "",
        "embedding_endpoint_id": emb_ep.id if emb_ep else "",
    }
