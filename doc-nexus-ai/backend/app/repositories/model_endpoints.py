from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
from app.schemas.model_endpoints import (
    ModelEndpointCreateRequest,
    ModelEndpointModelRequest,
    ModelEndpointUpdateRequest,
)
from app.services.model_endpoint_config import resolve_endpoint_configuration


def list_endpoints(db: Session) -> list[ModelEndpoint]:
    return list(
        db.scalars(
            select(ModelEndpoint).order_by(
                # Enabled connections first; disabled/unavailable sink below
                # so admins see what is actually in service at the top.
                (ModelEndpoint.status != "active").asc(),
                ModelEndpoint.sort_order,
                ModelEndpoint.provider,
                ModelEndpoint.model,
                ModelEndpoint.capability,
                ModelEndpoint.region,
            )
        )
    )


def next_sort_order(db: Session) -> int:
    current = db.scalar(select(func.max(ModelEndpoint.sort_order)))
    return (current or 0) + 10


_LEGACY_MODEL_FIELDS = {
    "model",
    "extra_models",
    "max_context",
    "supports_streaming_usage",
    "supports_structured_output",
    "supports_vision",
    "supports_cache_pricing",
}


def _legacy_model_profiles(
    endpoint: ModelEndpoint,
) -> list[ModelEndpointModelRequest]:
    names: list[str] = []
    for value in [endpoint.model, *(endpoint.extra_models or "").split(",")]:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return [
        ModelEndpointModelRequest(
            model=name,
            status="active",
            is_default=index == 0,
            sort_order=index * 10,
            max_context=int(endpoint.max_context or 128000),
            supports_image_input=bool(endpoint.supports_vision)
            if index == 0
            else False,
            supports_streaming_usage=bool(endpoint.supports_streaming_usage),
            supports_structured_output=bool(endpoint.supports_structured_output),
            supports_cache_pricing=bool(endpoint.supports_cache_pricing),
        )
        for index, name in enumerate(names)
    ]


def _sync_models(
    endpoint: ModelEndpoint,
    profiles: Sequence[ModelEndpointModelRequest],
    *,
    timestamp,
) -> None:
    existing = {profile.model: profile for profile in endpoint.models}
    synced: list[ModelEndpointModel] = []
    for profile in profiles:
        record = existing.get(profile.model)
        if record is None:
            record = ModelEndpointModel(
                id=f"mepm_{new_token(10)}",
                endpoint_id=endpoint.id,
                model=profile.model,
                created_at=timestamp,
                updated_at=timestamp,
            )
        record.status = profile.status
        record.is_default = profile.is_default
        record.sort_order = profile.sort_order
        record.max_context = profile.max_context
        record.supports_image_input = profile.supports_image_input
        record.supports_streaming_usage = profile.supports_streaming_usage
        record.supports_structured_output = profile.supports_structured_output
        record.supports_cache_pricing = profile.supports_cache_pricing
        record.updated_at = timestamp
        synced.append(record)
    endpoint.models = synced

    default = next(profile for profile in synced if profile.is_default)
    extras = sorted(
        (profile for profile in synced if not profile.is_default),
        key=lambda profile: (profile.sort_order, profile.model),
    )
    endpoint.model = default.model
    endpoint.extra_models = ", ".join(profile.model for profile in extras)
    endpoint.max_context = default.max_context
    endpoint.supports_vision = default.supports_image_input
    endpoint.supports_streaming_usage = default.supports_streaming_usage
    endpoint.supports_structured_output = default.supports_structured_output
    endpoint.supports_cache_pricing = default.supports_cache_pricing


def sync_legacy_models(endpoint: ModelEndpoint, *, timestamp) -> None:
    """Materialize legacy primary/extra fields as concrete model profiles."""
    _sync_models(
        endpoint,
        _legacy_model_profiles(endpoint),
        timestamp=timestamp,
    )


def create_endpoint(
    db: Session, payload: ModelEndpointCreateRequest
) -> ModelEndpoint:
    timestamp = now()
    config = resolve_endpoint_configuration(
        payload.provider, payload.adapter, payload.api_protocol
    )
    # Explicit 0 is allowed; otherwise append after existing endpoints.
    sort_order = (
        payload.sort_order
        if "sort_order" in payload.model_fields_set
        else next_sort_order(db)
    )
    endpoint = ModelEndpoint(
        id=f"mep_{new_token(10)}",
        provider=config.provider,
        adapter=config.adapter,
        api_protocol=config.api_protocol,
        model=payload.model,
        capability=payload.capability,
        endpoint_url=payload.endpoint_url,
        region=payload.region,
        status="active",
        secret_ref=payload.secret_ref,
        secret_fingerprint=payload.secret_fingerprint,
        allowed_data_level=payload.allowed_data_level,
        max_context=payload.max_context,
        timeout_ms=payload.timeout_ms,
        concurrency_limit=payload.concurrency_limit,
        supports_streaming_usage=payload.supports_streaming_usage,
        supports_structured_output=payload.supports_structured_output,
        supports_vision=payload.supports_vision,
        supports_cache_pricing=payload.supports_cache_pricing,
        extra_models=payload.extra_models or "",
        sort_order=sort_order,
        api_version=payload.api_version or "",
        created_at=timestamp,
        updated_at=timestamp,
    )
    if payload.models:
        _sync_models(endpoint, payload.models, timestamp=timestamp)
    else:
        sync_legacy_models(endpoint, timestamp=timestamp)
    db.add(endpoint)
    db.flush()
    return endpoint


def update_endpoint(
    db: Session, endpoint: ModelEndpoint, payload: ModelEndpointUpdateRequest
) -> ModelEndpoint:
    values = payload.model_dump(exclude_unset=True, exclude={"models"})
    requested_profiles = payload.models if "models" in payload.model_fields_set else None
    if any(
        field in values for field in ("provider", "adapter", "api_protocol")
    ):
        config = resolve_endpoint_configuration(
            values.get("provider", endpoint.provider),
            values.get("adapter", endpoint.adapter),
            values.get("api_protocol", endpoint.api_protocol),
        )
        values["provider"] = config.provider
        values["adapter"] = config.adapter
        values["api_protocol"] = config.api_protocol
    if "secret_ref" in values:
        values["secret_fingerprint"] = (
            values.get("secret_fingerprint")
            if values["secret_ref"] is not None
            else None
        )
    for field, value in values.items():
        setattr(endpoint, field, value)
    timestamp = now()
    if requested_profiles is not None:
        _sync_models(endpoint, requested_profiles, timestamp=timestamp)
    elif _LEGACY_MODEL_FIELDS.intersection(values):
        sync_legacy_models(endpoint, timestamp=timestamp)
    endpoint.updated_at = timestamp
    db.flush()
    return endpoint
