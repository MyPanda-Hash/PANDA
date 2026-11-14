from __future__ import annotations

from decimal import Decimal, InvalidOperation
import httpx
import os
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.models.auth import User
from app.models.model_endpoints import ModelEndpoint
from app.repositories import audit, model_endpoints, users
from app.schemas.model_endpoints import (
    ModelEndpointCreateRequest,
    ModelEndpointModelResponse,
    ModelEndpointResponse,
    ModelEndpointTestResponse,
    ModelEndpointUpdateRequest,
)
from app.services import linkmind
from app.services.anthropic_http import anthropic_headers
from app.services.model_routing import vision_endpoint_metadata_errors
from app.services.pricing import fill_usage_cost
from app.services.vision_fallback import (
    VisionFallbackError,
    VisionProbeResult,
    probe_vision_capability,
)

router = APIRouter(prefix="/admin/model-endpoints", tags=["admin-model-endpoints"])


def _validate_secret_ref(secret_ref: str | None) -> None:
    """LinkMind 保留引用只能在 LinkMind 集成启用时由系统写入。"""
    if secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF and not linkmind.enabled():
        raise HTTPException(
            status_code=422,
            detail="LinkMind 集成未启用，不能使用 LinkMind 用户 Key 作为端点密钥",
        )


def _resolve_configured_secret(secret_ref: str | None) -> str | None:
    """Resolve a non-LinkMind endpoint credential without exposing it."""
    raw = (secret_ref or "").strip()
    if not raw:
        return None
    if raw.startswith("env://"):
        return os.environ.get(raw[6:])
    return raw


def _resolve_test_credentials(
    endpoint: ModelEndpoint, db: Session, *, operator_id: str
) -> tuple[str | None, str]:
    """Resolve endpoint-test credentials and their LinkMind key owner."""
    if linkmind.enabled():
        bootstrap_admin = users.get_bootstrap_admin(db, settings.bootstrap_username)
        if bootstrap_admin is None:
            raise linkmind.LinkMindError(
                "LinkMind bootstrap system administrator could not be resolved"
            )
        return (
            linkmind.ensure_user_key(bootstrap_admin.id, db=db),
            bootstrap_admin.id,
        )
    return _resolve_configured_secret(endpoint.secret_ref), operator_id


def endpoint_response(endpoint: ModelEndpoint) -> ModelEndpointResponse:
    return ModelEndpointResponse(
        id=endpoint.id,
        provider=endpoint.provider,
        adapter=endpoint.adapter,
        api_protocol=endpoint.api_protocol,
        model=endpoint.model,
        capability=endpoint.capability,
        endpoint_url=endpoint.endpoint_url,
        region=endpoint.region,
        status=endpoint.status,
        secret_configured=endpoint.secret_ref is not None,
        secret_fingerprint=endpoint.secret_fingerprint,
        allowed_data_level=endpoint.allowed_data_level,
        max_context=endpoint.max_context,
        timeout_ms=endpoint.timeout_ms,
        concurrency_limit=endpoint.concurrency_limit,
        supports_streaming_usage=endpoint.supports_streaming_usage,
        supports_structured_output=endpoint.supports_structured_output,
        supports_vision=endpoint.supports_vision,
        supports_cache_pricing=endpoint.supports_cache_pricing,
        extra_models=endpoint.extra_models or "",
        sort_order=endpoint.sort_order,
        api_version=endpoint.api_version or "",
        models=[
            ModelEndpointModelResponse(
                id=profile.id,
                model=profile.model,
                status=profile.status,
                is_default=profile.is_default,
                sort_order=profile.sort_order,
                max_context=profile.max_context,
                supports_image_input=profile.supports_image_input,
                supports_streaming_usage=profile.supports_streaming_usage,
                supports_structured_output=profile.supports_structured_output,
                supports_cache_pricing=profile.supports_cache_pricing,
            )
            for profile in sorted(
                endpoint.models,
                key=lambda item: (item.sort_order, item.model),
            )
        ],
        last_tested_at=endpoint.last_tested_at,
        last_test_status=endpoint.last_test_status,
        last_test_error=endpoint.last_test_error,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


def _ensure_unique(
    db,
    payload: ModelEndpointCreateRequest | ModelEndpointUpdateRequest,
    endpoint_id: str | None = None,
) -> None:
    values = payload.model_dump(exclude_unset=True)
    profiles = values.pop("models", None)
    if profiles:
        values["model"] = next(
            profile["model"] for profile in profiles if profile["is_default"]
        )
    if (
        not {"provider", "model", "capability", "region"}.issubset(values)
        and endpoint_id
    ):
        existing = db.get(ModelEndpoint, endpoint_id)
        if not existing:
            return
        values = {
            "provider": values.get("provider", existing.provider),
            "model": values.get("model", existing.model),
            "capability": values.get("capability", existing.capability),
            "region": values.get("region", existing.region),
        }
    if not {"provider", "model", "capability", "region"}.issubset(values):
        return
    query = select(ModelEndpoint).where(
        ModelEndpoint.provider == values["provider"],
        ModelEndpoint.model == values["model"],
        ModelEndpoint.capability == values["capability"],
        ModelEndpoint.region == values["region"],
    )
    if endpoint_id:
        query = query.where(ModelEndpoint.id != endpoint_id)
    if db.scalar(query):
        raise HTTPException(
            status_code=409, detail="相同供应商、模型、能力和区域的端点已存在"
        )
def _validate_vision_update(
    endpoint: ModelEndpoint, payload: ModelEndpointUpdateRequest
) -> None:
    capability = payload.capability or endpoint.capability
    supports_vision = (
        payload.supports_vision
        if "supports_vision" in payload.model_fields_set
        else endpoint.supports_vision
    )
    profiles = (
        payload.models
        if "models" in payload.model_fields_set
        else endpoint.models
    )
    if capability == "vision" and not supports_vision:
        raise HTTPException(
            status_code=422,
            detail="vision endpoints must enable supports_vision",
        )
    if capability == "vision" and any(
        profile.status == "active" and not profile.supports_image_input
        for profile in profiles
    ):
        raise HTTPException(
            status_code=422,
            detail="active vision endpoint models must enable supports_image_input",
        )


@router.get("", response_model=list[ModelEndpointResponse])
def list_model_endpoints(_: User = Depends(admin_user)) -> list[ModelEndpointResponse]:
    with transaction() as db:
        return [endpoint_response(item) for item in model_endpoints.list_endpoints(db)]


@router.post("", response_model=ModelEndpointResponse, status_code=201)
def create_model_endpoint(
    payload: ModelEndpointCreateRequest, admin: User = Depends(admin_user)
) -> ModelEndpointResponse:
    _validate_secret_ref(payload.secret_ref)
    with transaction() as db:
        _ensure_unique(db, payload)
        endpoint = model_endpoints.create_endpoint(db, payload)
        audit.record(db, admin.id, "model_endpoint_created", endpoint.id, "success")
        return endpoint_response(endpoint)


@router.get("/{endpoint_id}", response_model=ModelEndpointResponse)
def get_model_endpoint(
    endpoint_id: str, _: User = Depends(admin_user)
) -> ModelEndpointResponse:
    with transaction() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="模型端点不存在")
        return endpoint_response(endpoint)


@router.patch("/{endpoint_id}", response_model=ModelEndpointResponse)
def update_model_endpoint(
    endpoint_id: str,
    payload: ModelEndpointUpdateRequest,
    admin: User = Depends(admin_user),
) -> ModelEndpointResponse:
    if payload.secret_ref is not None:
        _validate_secret_ref(payload.secret_ref)
    with transaction() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="模型端点不存在")
        _validate_vision_update(endpoint, payload)
        _ensure_unique(db, payload, endpoint_id)
        model_endpoints.update_endpoint(db, endpoint, payload)
        audit.record(db, admin.id, "model_endpoint_updated", endpoint.id, "success")
        return endpoint_response(endpoint)


@router.delete("/{endpoint_id}", status_code=204, response_model=None)
def delete_model_endpoint(endpoint_id: str, admin: User = Depends(admin_user)) -> None:
    with transaction() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="模型端点不存在")
        db.delete(endpoint)
        audit.record(db, admin.id, "model_endpoint_deleted", endpoint_id, "success")

def _health_base_url(endpoint: ModelEndpoint) -> str:
    base_url = endpoint.endpoint_url.split("?")[0].rstrip("/")
    for suffix in (
        "/chat/completions",
        "/embeddings",
        "/rerank",
        "/completions",
        "/responses",
    ):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    if base_url.endswith("/openai"):
        base_url = base_url[: -len("/openai")]
    return base_url.rstrip("/")


def _health_check(
    endpoint: ModelEndpoint, secret: str | None
) -> tuple[bool, int | None, str | None, str | None]:
    headers = {"User-Agent": "Doc-Nexus-AI/0.1"}
    if secret:
        if endpoint.adapter == "azure_openai":
            headers["api-key"] = secret
        elif endpoint.adapter == "anthropic" or endpoint.api_protocol == "messages":
            headers.update(anthropic_headers(secret, endpoint.endpoint_url))
        else:
            headers["Authorization"] = f"Bearer {secret}"
    try:
        with httpx.Client(
            timeout=max(1.0, float(endpoint.timeout_ms or 0) / 1000.0),
            trust_env=False,
        ) as client:
            response = client.get(f"{_health_base_url(endpoint)}/models", headers=headers)
    except httpx.TimeoutException:
        return False, None, "timeout", "端点响应超时"
    except httpx.HTTPError:
        return False, None, "unavailable", "端点暂时不可用"
    status = int(response.status_code)
    if status in {401, 403}:
        return True, status, "authentication_failed", "端点认证失败"
    if status == 404:
        return True, status, "reachable", "端点可达，但模型列表路径不可用"
    if status >= 500:
        return True, status, "unavailable", "端点暂时不可用"
    if status >= 400:
        return True, status, "unavailable", "端点请求未通过"
    return True, status, None, None


def _vision_test_models(endpoint: ModelEndpoint) -> list[tuple[str, bool, object | None]]:
    profiles = [
        profile
        for profile in sorted(
            endpoint.models or [], key=lambda item: (item.sort_order, item.model)
        )
        if profile.status == "active" and profile.supports_image_input
    ]
    if profiles:
        return [(profile.model, True, profile) for profile in profiles]
    if endpoint.supports_vision and endpoint.model:
        return [(endpoint.model, True, None)]
    return []


def _probe_error_status(code: str) -> str:
    return {
        "vision_authentication_failed": "authentication_failed",
        "vision_timeout": "timeout",
        "vision_output_invalid": "malformed_response",
        "vision_usage_invalid": "usage_invalid",
        "vision_unsupported_image": "unsupported_image",
        "vision_endpoint_unavailable": "unavailable",
        "vision_metadata_invalid": "metadata_invalid",
    }.get(code, "capability_failed")


_PROBE_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "input_uncached_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)
_PROBE_USAGE_TEXT_FIELDS = ("currency", "price_version_id")
_SAFE_PROBE_STATUSES = frozenset({"completed", "failed"})


def _safe_nonnegative_int(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


def _safe_probe_usage(
    endpoint: ModelEndpoint,
    *,
    model: str,
    usage: object,
    status: object,
    error_code: object,
) -> dict[str, object]:
    """Keep only the bounded, non-sensitive probe usage contract."""
    source = usage if isinstance(usage, dict) else {}
    safe: dict[str, object] = {}
    for field in _PROBE_USAGE_TOKEN_FIELDS:
        if field not in source:
            continue
        value = _safe_nonnegative_int(source[field])
        if value is not None:
            safe[field] = value

    if "cost" in source:
        raw_cost = source["cost"]
        try:
            cost = Decimal(str(raw_cost))
            if cost.is_finite() and cost >= 0:
                safe["cost"] = str(raw_cost)
        except (InvalidOperation, ValueError, TypeError):
            pass

    for field in _PROBE_USAGE_TEXT_FIELDS:
        value = source.get(field)
        if isinstance(value, str) and value.strip() and len(value) <= 128:
            safe[field] = value

    # Identity fields come from the endpoint and current check, never from a
    # provider-controlled response object.
    if "provider" in source:
        safe["provider"] = str(endpoint.provider)
    if "model" in source:
        safe["model"] = model
    if "endpoint_id" in source:
        safe["endpoint_id"] = str(endpoint.id)

    if "latency_ms" in source:
        value = _safe_nonnegative_int(source["latency_ms"])
        if value is not None:
            safe["latency_ms"] = value
    if "status" in source:
        candidate = status if isinstance(status, str) else source.get("status")
        if isinstance(candidate, str) and candidate in _SAFE_PROBE_STATUSES:
            safe["status"] = candidate
    if "error_code" in source:
        candidate = (
            error_code if isinstance(error_code, str) else source.get("error_code")
        )
        if isinstance(candidate, str) and len(candidate) <= 128:
            safe["error_code"] = candidate
    return safe


def _safe_probe_check(
    endpoint: ModelEndpoint, check: dict[str, object]
) -> dict[str, object]:
    model_value = check.get("model")
    model = (
        model_value
        if isinstance(model_value, str) and len(model_value) <= 160
        else str(getattr(endpoint, "model", "") or "")
    )
    status_value = check.get("status")
    status = (
        status_value
        if isinstance(status_value, str) and status_value in _SAFE_PROBE_STATUSES
        else "failed"
    )
    image_count = _safe_nonnegative_int(check.get("image_count"))
    if image_count is None or not 1 <= image_count <= 4:
        image_count = 0
    latency_ms = _safe_nonnegative_int(check.get("latency_ms")) or 0
    error_value = check.get("error_code")
    error_code = (
        error_value
        if isinstance(error_value, str) and len(error_value) <= 128
        else None
    )
    return {
        "model": model,
        "image_count": image_count,
        "status": status,
        "latency_ms": latency_ms,
        "error_code": error_code,
        "usage": _safe_probe_usage(
            endpoint,
            model=model,
            usage=check.get("usage"),
            status=status,
            error_code=error_code,
        ),
    }


def _summarize_probe_usage(
    endpoint: ModelEndpoint, checks: list[dict[str, object]]
) -> dict[str, object] | None:
    safe_checks = [_safe_probe_check(endpoint, check) for check in checks]
    usages = [
        check["usage"]
        for check in safe_checks
        if isinstance(check.get("usage"), dict)
    ]
    if not usages:
        return None

    summary: dict[str, object] = {
        "provider": endpoint.provider,
        "endpoint_id": endpoint.id,
        "model": ",".join(
            dict.fromkeys(
                str(check.get("model") or "")
                for check in safe_checks
                if str(check.get("model") or "").strip()
            )
        ),
        "latency_ms": sum(int(usage.get("latency_ms") or 0) for usage in usages),
        "status": (
            "completed"
            if all(check.get("status") == "completed" for check in safe_checks)
            else "failed"
        ),
        "checks": safe_checks,
    }
    for field in _PROBE_USAGE_TOKEN_FIELDS:
        summary[field] = sum(int(usage.get(field) or 0) for usage in usages)

    total_cost = Decimal("0")
    for usage in usages:
        try:
            total_cost += Decimal(str(usage.get("cost") or "0"))
        except (InvalidOperation, ValueError, TypeError):
            continue
    summary["cost"] = str(total_cost)

    currencies = {
        str(usage.get("currency"))
        for usage in usages
        if usage.get("currency")
    }
    price_versions = {
        str(usage.get("price_version_id"))
        for usage in usages
        if usage.get("price_version_id")
    }
    summary["currency"] = next(iter(currencies)) if len(currencies) == 1 else None
    summary["price_version_id"] = (
        next(iter(price_versions)) if len(price_versions) == 1 else None
    )
    return summary


@router.post("/{endpoint_id}:test", response_model=ModelEndpointTestResponse)
def test_model_endpoint(
    endpoint_id: str,
    admin: User = Depends(admin_user),
    model: str | None = None,
) -> ModelEndpointTestResponse:
    with transaction() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="模型端点不存在")
        if model is not None:
            profile = next(
                (item for item in endpoint.models if item.model == model), None
            )
            if profile is None:
                raise HTTPException(status_code=404, detail="模型档案不存在")
            if profile.status != "active" or not profile.supports_image_input:
                raise HTTPException(
                    status_code=422, detail="仅可测试启用且支持图片输入的模型"
                )
            probe_models = [(profile.model, True, profile)]
        else:
            probe_models = _vision_test_models(endpoint)
        tested_at = now()
        started = monotonic()
        reachable = False
        authentication_failed = False
        http_status: int | None = None
        error: str | None = None
        failure_code: str | None = None
        probe_usage: dict[str, object] | None = None
        probe_checks: list[dict[str, object]] = []
        secret: str | None = None
        credential_owner_id = admin.id
        try:
            secret, credential_owner_id = _resolve_test_credentials(
                endpoint, db, operator_id=admin.id
            )
        except linkmind.LinkMindError:
            authentication_failed = True
            failure_code = "vision_authentication_failed"
            error = "无法解析 LinkMind 系统管理员密钥"

        capability_tested: str | None = "vision" if probe_models else None
        capability_ok: bool | None = None
        failures: list[tuple[str, str]] = []
        metadata_errors: set[str] = set()
        if probe_models and not authentication_failed:
            for model, supports_image_input, profile in probe_models:
                metadata_errors.update(
                    vision_endpoint_metadata_errors(
                        db,
                        endpoint,
                        profile=profile,
                    )
                )
                for image_count in (1, 4):
                    check_name = f"{model}（{image_count} 图）"
                    try:
                        result = probe_vision_capability(
                            endpoint,
                            model=model,
                            user_id=credential_owner_id,
                            secret_override=secret,
                            supports_image_input=supports_image_input,
                            image_count=image_count,
                        )
                        reachable = True
                        http_status = http_status or 200
                        usage = dict(result.usage) if isinstance(result, VisionProbeResult) else {}
                        if usage:
                            usage["status"] = (
                                "completed" if bool(result) else "failed"
                            )
                            usage["model"] = model
                            fill_usage_cost(db, usage)
                        probe_checks.append(
                            {
                                "model": model,
                                "image_count": image_count,
                                "status": "completed" if bool(result) else "failed",
                                "latency_ms": (
                                    result.latency_ms
                                    if isinstance(result, VisionProbeResult)
                                    else 0
                                ),
                                "error_code": (
                                    None if bool(result) else "vision_unsupported_image"
                                ),
                                "usage": usage,
                            }
                        )
                        if not bool(result):
                            failures.append(
                                (check_name, "vision_unsupported_image")
                            )
                    except VisionFallbackError as exc:
                        reachable = reachable or bool(exc.reachable)
                        if exc.http_status is not None:
                            http_status = exc.http_status
                        usage = {
                            "provider": endpoint.provider,
                            "model": model,
                            "endpoint_id": endpoint.id,
                            "input_tokens": 0,
                            "input_uncached_tokens": 0,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "cost": "0",
                            "latency_ms": max(
                                0, int((monotonic() - started) * 1000)
                            ),
                            "status": "failed",
                            "error_code": exc.code,
                        }
                        fill_usage_cost(db, usage)
                        probe_checks.append(
                            {
                                "model": model,
                                "image_count": image_count,
                                "status": "failed",
                                "latency_ms": usage["latency_ms"],
                                "error_code": exc.code,
                                "usage": usage,
                            }
                        )
                        failures.append((check_name, exc.code))
                        failure_code = failure_code or exc.code
                    except (ValueError, TypeError):
                        reachable = True
                        probe_checks.append(
                            {
                                "model": model,
                                "image_count": image_count,
                                "status": "failed",
                                "latency_ms": 0,
                                "error_code": "vision_output_invalid",
                                "usage": {},
                            }
                        )
                        failures.append((check_name, "vision_output_invalid"))
                        failure_code = failure_code or "vision_output_invalid"
            probe_usage = _summarize_probe_usage(endpoint, probe_checks)
            capability_ok = not failures and not metadata_errors
            if failures:
                failure_code = failure_code or failures[0][1]
                error = "视觉能力探针失败：" + "；".join(
                    f"{model}：{'视觉请求参数或格式与模型不兼容' if code == 'vision_request_invalid' else _probe_error_status(code)}"
                    for model, code in failures
                )
            elif metadata_errors:
                failure_code = "vision_metadata_invalid"
                error = "视觉端点元数据未通过校验：" + "、".join(
                    sorted(metadata_errors)
                )
        elif not probe_models and not authentication_failed:
            reachable, http_status, health_status, health_error = _health_check(
                endpoint, secret
            )
            if health_status:
                failure_code = (
                    "vision_authentication_failed"
                    if health_status == "authentication_failed"
                    else "vision_timeout"
                    if health_status == "timeout"
                    else "vision_endpoint_unavailable"
                    if health_status == "unavailable"
                    else None
                )
            error = health_error

        latency_ms = max(0, int((monotonic() - started) * 1000))
        if authentication_failed:
            test_status = "authentication_failed"
        elif failure_code:
            test_status = _probe_error_status(failure_code)
        elif not reachable:
            test_status = "unreachable"
        elif metadata_errors:
            test_status = "metadata_invalid"
        else:
            test_status = "reachable"
        endpoint.last_tested_at = tested_at
        endpoint.last_test_status = test_status
        endpoint.last_test_error = error
        if not probe_models and not reachable:
            endpoint.status = "unavailable"
        endpoint.updated_at = tested_at
        audit.record(
            db,
            admin.id,
            "model_endpoint_tested",
            endpoint.id,
            "success" if test_status == "reachable" else "failed",
        )
        return ModelEndpointTestResponse(
            id=endpoint.id,
            status=endpoint.status,
            reachable=reachable,
            test_status=test_status,
            capability_tested=capability_tested,
            capability_ok=capability_ok,
            latency_ms=latency_ms,
            http_status=http_status,
            tested_at=tested_at,
            error=error,
            failure_code=failure_code,
            probe_usage=probe_usage,
        )
