from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import admin_user
from app.core.config import settings
from app.core.database import transaction
from app.models.auth import User
from app.repositories import audit
from app.services import linkmind
from app.services.default_endpoints import (
    auto_provision_default_endpoints,
    probe_default_endpoints,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/provider-keys", tags=["provider_keys"])

SUPPORTED_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "placeholder": "sk-..."},
    {"id": "ailibaba", "name": "Alibaba / 通义千问 (DashScope)", "placeholder": "sk-..."},
    {"id": "xiaomi", "name": "Xiaomi (小米)", "placeholder": "输入小米大模型 API Key"},
    {"id": "minimax", "name": "MiniMax (名之梦)", "placeholder": "输入 MiniMax API Key"},
    {"id": "moonshot", "name": "Moonshot AI (月之暗面)", "placeholder": "sk-..."},
]


class ProviderKeyRegisterRequest(BaseModel):
    provider: str = Field(..., description="Provider 标识，如 xiaomi, deepseek, ailibaba 等")
    apiKey: str = Field(..., min_length=1, description="用户自己的 API Key")
    name: str = Field(default="", description="Key 名称/备注")


class BatchProviderKeyRequest(BaseModel):
    items: list[ProviderKeyRegisterRequest]


class SetupProbeAutoProvisionResponse(BaseModel):
    all_ready: bool
    auto_provisioned: bool
    chat: dict[str, Any]
    embedding: dict[str, Any]


def _require_linkmind_enabled() -> None:
    if not linkmind.enabled():
        raise HTTPException(status_code=503, detail="LinkMind 集成未启用")


def gateway_api_key_add_url() -> str:
    base = (settings.linkmind_base_url or "").strip().rstrip("/")
    return f"{base}/apiKey/add" if base else ""


@router.get("/meta")
def get_provider_meta(_: User = Depends(admin_user)) -> dict[str, Any]:
    """返回支持的 Provider 列表与 LinkMind 集成状态"""
    return {"providers": SUPPORTED_PROVIDERS, "linkmind_enabled": linkmind.enabled()}


@router.post("/probe-and-setup", response_model=SetupProbeAutoProvisionResponse)
async def probe_and_setup(admin: User = Depends(admin_user)) -> SetupProbeAutoProvisionResponse:
    """探测默认端点；若全连通则自动配置并绑定到系统模型路由"""
    probe_result = await probe_default_endpoints(admin.id)
    auto_provisioned = False
    if probe_result.get("all_ready"):
        with transaction() as db:
            auto_provision_default_endpoints(db, probe_result, admin.id)
            audit.record(
                db,
                admin.id,
                "default_endpoints_auto_provisioned",
                admin.id,
                "success",
            )
        auto_provisioned = True

    return SetupProbeAutoProvisionResponse(
        all_ready=bool(probe_result.get("all_ready")),
        auto_provisioned=auto_provisioned,
        chat=probe_result.get("chat", {}),
        embedding=probe_result.get("embedding", {}),
    )


@router.post("/register")
async def register_provider_key(
    payload: ProviderKeyRegisterRequest, admin: User = Depends(admin_user)
) -> dict[str, Any]:
    """向配置的 LinkMind 网关注册单个 Provider API Key。"""
    _require_linkmind_enabled()
    name = payload.name.strip() or payload.provider.strip()
    body = {
        "provider": payload.provider.strip(),
        "apiKey": payload.apiKey.strip(),
        "name": name,
    }
    gateway_url = gateway_api_key_add_url()
    if not gateway_url:
        raise HTTPException(status_code=503, detail="LinkMind 网关地址未配置")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(gateway_url, json=body)
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"注册 Provider 密钥失败（HTTP {resp.status_code}）：{resp.text}",
                )
            with transaction() as db:
                audit.record(
                    db,
                    admin.id,
                    "provider_key_registered",
                    payload.provider,
                    "success",
                )
            try:
                data = resp.json()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            return {"status": "ok", "provider": payload.provider}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to reach gateway for apiKey/add: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"无法连接到模型网关 ({gateway_url}): {exc}",
        )


@router.post("/batch-register")
async def batch_register_provider_keys(
    payload: BatchProviderKeyRequest, admin: User = Depends(admin_user)
) -> dict[str, Any]:
    """批量注册 Provider 的 API Key"""
    _require_linkmind_enabled()
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in payload.items:
        if not item.apiKey.strip():
            continue
        try:
            res = await register_provider_key(item, admin)
            succeeded.append({"provider": item.provider, "result": res})
        except Exception as exc:
            failed.append({"provider": item.provider, "error": str(exc)})

    return {"succeeded": succeeded, "failed": failed}
