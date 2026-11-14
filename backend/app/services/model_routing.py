"""Resolve backend-owned model routes from governance settings and endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
from app.repositories import system_config

MODEL_ROUTE_KEYS: Final[dict[str, str]] = {
    "chat": "pi_chat_model",
    "writing": "pi_writing_model",
    "embedding": "embedding_model",
    "rerank": "rerank_model",
}
MODEL_DEFAULT_KEYS: Final[dict[str, str]] = {
    "writing": "pi_writing_default_model",
}
MODEL_ROUTE_CAPABILITIES: Final[dict[str, str]] = {
    "chat": "chat",
    "writing": "chat",
    "embedding": "embedding",
    "rerank": "rerank",
}

# ``allowed_data_level`` describes the most sensitive data an endpoint may
# receive.  Keep this ordering local to routing so a vision fallback cannot
# silently send a confidential image to an internal-only endpoint.
_DATA_LEVEL_ORDER: Final[dict[str, int]] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """A selected endpoint; endpoint id is the persisted mapping value."""

    slot: str
    endpoint_id: str
    provider: str
    model: str
    capability: str


@dataclass(frozen=True, slots=True)
class ResolvedModelTarget:
    """One concrete model selected from one reusable endpoint connection."""

    endpoint: ModelEndpoint
    profile: ModelEndpointModel

    @property
    def id(self) -> str:
        return self.endpoint.id

    @property
    def model(self) -> str:
        return self.profile.model

    @property
    def supports_vision(self) -> bool:
        return self.profile.supports_image_input

    @property
    def supports_image_input(self) -> bool:
        return self.profile.supports_image_input

    @property
    def max_context(self) -> int:
        return self.profile.max_context

    def __getattr__(self, name: str):
        return getattr(self.endpoint, name)


def route_key(slot: str) -> str:
    normalized = (slot or "").strip().lower()
    try:
        return MODEL_ROUTE_KEYS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported model route slot: {slot}") from exc


def route_capability(slot: str) -> str:
    normalized = (slot or "").strip().lower()
    try:
        return MODEL_ROUTE_CAPABILITIES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported model route slot: {slot}") from exc


def configured_route_id(slot: str, stored: dict[str, str] | None = None) -> str:
    """Return the configured ModelEndpoint id for a runtime slot, if any."""
    values = stored
    if values is None:
        try:
            from app.core.database import transaction

            with transaction() as db:
                values = system_config.get_all(db)
        except Exception:
            values = {}
    return (values.get(route_key(slot), "") if values else "").strip()


def endpoint_model_profiles(
    endpoint: ModelEndpoint | ResolvedModelTarget,
) -> list[ModelEndpointModel]:
    connection = (
        endpoint.endpoint if isinstance(endpoint, ResolvedModelTarget) else endpoint
    )
    return sorted(
        (profile for profile in connection.models if profile.status == "active"),
        key=lambda profile: (
            not profile.is_default,
            profile.sort_order,
            profile.model,
        ),
    )


def endpoint_model_names(
    endpoint: ModelEndpoint | ResolvedModelTarget,
) -> list[str]:
    """Active concrete model names ordered with the default model first."""
    return [profile.model for profile in endpoint_model_profiles(endpoint)]


def configured_default_model(slot: str, stored: dict[str, str] | None = None) -> str:
    """Return the selected default model name for a slot, if configured."""
    from app.core.config import settings

    key = MODEL_DEFAULT_KEYS.get((slot or "").strip().lower())
    if not key:
        return ""
    values = stored
    if values is None:
        try:
            from app.core.database import transaction

            with transaction() as db:
                values = system_config.get_all(db)
        except Exception:
            values = {}
    return (values.get(key, "") if values else "").strip() or (
        getattr(settings, key, "") or ""
    ).strip()


def list_default_model_options(
    db: Session, slot: str, stored: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    """Return selectable model names for the currently configured endpoint."""
    values = stored if stored is not None else system_config.get_all(db)
    endpoint_id = configured_route_id(slot, values)
    if not endpoint_id:
        return [("", "请先选择模型端点")]
    endpoint = db.get(ModelEndpoint, endpoint_id)
    capability = route_capability(slot)
    if (
        endpoint is None
        or endpoint.status != "active"
        or endpoint.capability != capability
    ):
        return [("", "请先选择有效的模型端点")]
    names = endpoint_model_names(endpoint)
    if not names:
        return [("", "所选端点没有可用模型")]
    return [(name, name) for name in names]


def list_route_options(db: Session, slot: str) -> list[tuple[str, str]]:
    """Return active endpoint ids and safe display labels for a governance select."""
    capability = route_capability(slot)
    rows = list(
        db.scalars(
            select(ModelEndpoint)
            .join(
                ModelEndpointModel,
                ModelEndpointModel.endpoint_id == ModelEndpoint.id,
            )
            .where(
                ModelEndpoint.status == "active",
                ModelEndpoint.capability == capability,
                ModelEndpointModel.status == "active",
            )
            .distinct()
            .order_by(
                ModelEndpoint.sort_order,
                ModelEndpoint.provider,
                ModelEndpoint.model,
                ModelEndpoint.region,
            )
        )
    )
    options: list[tuple[str, str]] = []
    for row in rows:
        target = _target_for_endpoint(row)
        if target is None:
            continue
        options.append(
            (
                row.id,
                f"{row.provider} / {target.model}"
                + (f" · {row.region}" if row.region else ""),
            )
        )
    return options


def _model_name_match_rank(stored: str, requested: str) -> int | None:
    """Rank an exact model id ahead of an unqualified provider suffix."""
    normalized_stored = stored.strip().lower()
    normalized_requested = requested.strip().lower()
    if normalized_stored == normalized_requested:
        return 0
    if "/" in normalized_stored and (
        normalized_stored.rsplit("/", 1)[-1] == normalized_requested
    ):
        return 1
    if (
        "/" not in normalized_stored
        and "/" in normalized_requested
        and (normalized_stored == normalized_requested.rsplit("/", 1)[-1])
    ):
        return 1
    return None


def _target_for_endpoint(
    endpoint: ModelEndpoint,
    requested_model: str | None = None,
) -> ResolvedModelTarget | None:
    profiles = endpoint_model_profiles(endpoint)
    requested = (requested_model or "").strip()
    if requested:
        suffix_match: ModelEndpointModel | None = None
        for profile in profiles:
            rank = _model_name_match_rank(profile.model, requested)
            if rank == 0:
                return ResolvedModelTarget(endpoint=endpoint, profile=profile)
            if rank == 1 and suffix_match is None:
                suffix_match = profile
        if suffix_match is not None:
            return ResolvedModelTarget(endpoint=endpoint, profile=suffix_match)
        return None
    if not profiles:
        return None
    return ResolvedModelTarget(endpoint=endpoint, profile=profiles[0])


def resolve_model_endpoint(
    db: Session,
    slot: str,
    *,
    requested_model: str | None = None,
    configured_id: str | None = None,
) -> ResolvedModelTarget | None:
    """Resolve one active concrete model with request > governance > fallback."""
    capability = route_capability(slot)
    requested = (requested_model or "").strip()
    if requested:
        rows = db.execute(
            select(ModelEndpoint, ModelEndpointModel)
            .join(
                ModelEndpointModel,
                ModelEndpointModel.endpoint_id == ModelEndpoint.id,
            )
            .where(
                ModelEndpoint.status == "active",
                ModelEndpoint.capability == capability,
                ModelEndpointModel.status == "active",
            )
            .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
        )
        suffix_match: ResolvedModelTarget | None = None
        for endpoint, profile in rows:
            rank = _model_name_match_rank(profile.model, requested)
            if rank == 0:
                return ResolvedModelTarget(endpoint=endpoint, profile=profile)
            if rank == 1 and suffix_match is None:
                suffix_match = ResolvedModelTarget(endpoint=endpoint, profile=profile)
        if suffix_match is not None:
            return suffix_match

    selected_id = (configured_id or configured_route_id(slot)).strip()
    if selected_id:
        selected = db.get(ModelEndpoint, selected_id)
        if (
            selected is not None
            and selected.status == "active"
            and selected.capability == capability
        ):
            target = _target_for_endpoint(selected, requested or None)
            if target is not None:
                return target
            target = _target_for_endpoint(selected)
            if target is not None:
                return target

    candidates = list(
        db.scalars(
            select(ModelEndpoint)
            .join(
                ModelEndpointModel,
                ModelEndpointModel.endpoint_id == ModelEndpoint.id,
            )
            .where(
                ModelEndpoint.status == "active",
                ModelEndpoint.capability == capability,
                ModelEndpointModel.status == "active",
            )
            .distinct()
            .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
        )
    )
    for endpoint in candidates:
        target = _target_for_endpoint(endpoint)
        if target is not None:
            return target
    return None


def route_from_endpoint(
    slot: str, endpoint: ModelEndpoint | ResolvedModelTarget
) -> ModelRoute:
    return ModelRoute(
        slot=(slot or "").strip().lower(),
        endpoint_id=endpoint.id,
        provider=endpoint.provider,
        model=endpoint.model,
        capability=endpoint.capability,
    )


def resolve_embedding_endpoint(
    db: Session,
    *,
    data_level: str | None = None,
    requested_model: str | None = None,
    endpoint_url: str | None = None,
) -> ResolvedModelTarget | None:
    """Resolve the configured target, or bind an explicit transport to governance."""
    if requested_model is None and endpoint_url is None:
        target = resolve_model_endpoint(db, "embedding")
        if (
            target is not None
            and (data_level is None or _data_level_allows(target.endpoint, data_level))
        ):
            return target
        return None

    from app.services.pi_local import _base_url

    requested = (requested_model or "").strip()
    required_url = _base_url(endpoint_url or "")
    rows = db.execute(
        select(ModelEndpoint, ModelEndpointModel)
        .join(
            ModelEndpointModel,
            ModelEndpointModel.endpoint_id == ModelEndpoint.id,
        )
        .where(
            ModelEndpoint.status == "active",
            ModelEndpoint.capability == "embedding",
            ModelEndpointModel.status == "active",
        )
        .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id.desc())
    )
    for endpoint, profile in rows:
        if data_level is not None and not _data_level_allows(endpoint, data_level):
            continue
        if requested and _model_name_match_rank(profile.model, requested) != 0:
            continue
        if required_url and _base_url(endpoint.endpoint_url) != required_url:
            continue
        return ResolvedModelTarget(endpoint=endpoint, profile=profile)
    return None


def _data_level_allows(endpoint: ModelEndpoint, requested: str) -> bool:
    requested_level = (requested or "internal").strip().lower() or "internal"
    endpoint_level = (
        str(getattr(endpoint, "allowed_data_level", "internal") or "internal")
        .strip()
        .lower()
        or "internal"
    )
    requested_rank = _DATA_LEVEL_ORDER.get(requested_level)
    endpoint_rank = _DATA_LEVEL_ORDER.get(endpoint_level)
    if requested_rank is None or endpoint_rank is None:
        return False
    return endpoint_rank >= requested_rank


def vision_endpoint_metadata_errors(
    db: Session,
    endpoint: ModelEndpoint,
    *,
    profile: ModelEndpointModel | None = None,
) -> tuple[str, ...]:
    """Return stable configuration reasons why a vision endpoint may not receive an image."""
    errors: list[str] = []
    if endpoint.status != "active":
        errors.append("endpoint_inactive")
    if endpoint.capability not in {"vision", "chat"}:
        errors.append("capability_unsupported")
    if profile is not None:
        if profile.status != "active":
            errors.append("model_inactive")
        if not bool(getattr(profile, "supports_image_input", False)):
            errors.append("image_support_disabled")
        model_name = profile.model
    else:
        if not bool(getattr(endpoint, "supports_vision", False)):
            errors.append("image_support_disabled")
        model_name = endpoint.model
    if not str(model_name or "").strip():
        errors.append("model_missing")
    allowed_level = (
        str(getattr(endpoint, "allowed_data_level", "") or "").strip().lower()
    )
    if allowed_level not in _DATA_LEVEL_ORDER:
        errors.append("data_level_invalid")
    try:
        if int(getattr(endpoint, "concurrency_limit", 0) or 0) <= 0:
            errors.append("concurrency_invalid")
    except (TypeError, ValueError, OverflowError):
        errors.append("concurrency_invalid")
    try:
        if int(getattr(endpoint, "timeout_ms", 0) or 0) <= 0:
            errors.append("timeout_invalid")
    except (TypeError, ValueError, OverflowError):
        errors.append("timeout_invalid")
    if not str(getattr(endpoint, "secret_ref", "") or "").strip():
        errors.append("secret_missing")
    return tuple(dict.fromkeys(errors))


def _vision_candidates(
    db: Session, *, capability: str, data_level: str
) -> list[ResolvedModelTarget]:
    rows = list(
        db.execute(
            select(ModelEndpoint, ModelEndpointModel)
            .join(
                ModelEndpointModel,
                ModelEndpointModel.endpoint_id == ModelEndpoint.id,
            )
            .where(
                ModelEndpoint.status == "active",
                ModelEndpoint.capability == capability,
                ModelEndpointModel.status == "active",
                ModelEndpointModel.supports_image_input.is_(True),
            )
            .order_by(
                ModelEndpoint.sort_order,
                ModelEndpoint.provider,
                ModelEndpoint.region,
                ModelEndpoint.id,
                ModelEndpointModel.is_default.desc(),
                ModelEndpointModel.sort_order,
                ModelEndpointModel.model,
            )
        )
    )
    eligible: list[ResolvedModelTarget] = []
    for endpoint, profile in rows:
        if not _data_level_allows(endpoint, data_level):
            continue
        if vision_endpoint_metadata_errors(db, endpoint, profile=profile):
            continue
        eligible.append(ResolvedModelTarget(endpoint=endpoint, profile=profile))
    return eligible


def _vision_model_candidates(
    db: Session, *, data_level: str
) -> list[ResolvedModelTarget]:
    return [
        *_vision_candidates(db, capability="vision", data_level=data_level),
        *_vision_candidates(db, capability="chat", data_level=data_level),
    ]


def list_vision_model_options(
    db: Session, *, data_level: str = "public"
) -> list[tuple[str, str]]:
    """Return selectable image-capable models, with automatic routing first."""
    options = [("", "自动选择（按端点顺序）")]
    for target in _vision_model_candidates(db, data_level=data_level):
        endpoint = target.endpoint
        suffix = endpoint.region or endpoint.id
        options.append(
            (
                target.profile.id,
                f"{endpoint.provider} / {target.profile.model} · {suffix}",
            )
        )
    return options


def resolve_vision_endpoint(
    db: Session,
    *,
    data_level: str = "internal",
    configured_profile_id: str | None = None,
) -> ResolvedModelTarget | None:
    """Resolve one eligible concrete model for bounded image parsing."""
    if configured_profile_id:
        return next(
            (
                target
                for target in _vision_model_candidates(db, data_level=data_level)
                if target.profile.id == configured_profile_id
            ),
            None,
        )
    dedicated = _vision_candidates(db, capability="vision", data_level=data_level)
    if dedicated:
        return dedicated[0]
    chat = _vision_candidates(db, capability="chat", data_level=data_level)
    return chat[0] if chat else None
