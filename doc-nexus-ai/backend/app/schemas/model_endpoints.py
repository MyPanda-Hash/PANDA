from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


_CAPABILITIES = "^(chat|embedding|rerank|vision)$"
_DATA_LEVELS = "^(public|internal|confidential|restricted)$"
_STATUSES = "^(active|disabled|unavailable)$"
_ADAPTERS = "^(openai|azure_openai|openai_compatible|anthropic)$"
_PROTOCOLS = "^(responses|chat_completions|messages)$"


class ModelEndpointModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=160)
    status: str = Field(default="active", pattern=_STATUSES)
    is_default: bool = False
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    max_context: int = Field(default=128000, ge=1, le=10_000_000)
    supports_image_input: bool = False
    supports_streaming_usage: bool = False
    supports_structured_output: bool = False
    supports_cache_pricing: bool = False

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()


class ModelEndpointModelResponse(ModelEndpointModelRequest):
    id: str


def _validate_model_profiles(
    profiles: list[ModelEndpointModelRequest] | None,
) -> list[ModelEndpointModelRequest] | None:
    if profiles is None:
        return None
    if not profiles:
        raise ValueError("models must contain at least one model")
    names = [profile.model for profile in profiles]
    if len(names) != len(set(names)):
        raise ValueError("model names must be unique within one endpoint")
    if sum(profile.is_default for profile in profiles) != 1:
        raise ValueError("models must contain exactly one default model")
    return profiles


class ModelEndpointCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    adapter: str | None = Field(default=None, pattern=_ADAPTERS)
    api_protocol: str | None = Field(default=None, pattern=_PROTOCOLS)
    model: str = Field(min_length=1, max_length=160)
    capability: str = Field(pattern=_CAPABILITIES)
    endpoint_url: str = Field(min_length=1, max_length=500, pattern=r"^https?://")
    region: str = Field(default="", max_length=64)
    secret_ref: str | None = Field(default=None, min_length=1, max_length=255)
    secret_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)
    allowed_data_level: str = Field(default="internal", pattern=_DATA_LEVELS)
    max_context: int = Field(default=128000, ge=1, le=10_000_000)
    timeout_ms: int = Field(default=30000, ge=100, le=300_000)
    concurrency_limit: int = Field(default=8, ge=1, le=10_000)
    supports_streaming_usage: bool = False
    supports_structured_output: bool = False
    supports_vision: bool = False
    supports_cache_pricing: bool = False
    extra_models: str = Field(default="", max_length=2000)
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    api_version: str = Field(default="", max_length=64)
    models: list[ModelEndpointModelRequest] | None = None

    @model_validator(mode="after")
    def validate_models(self) -> "ModelEndpointCreateRequest":
        _validate_model_profiles(self.models)
        if self.capability == "vision" and not self.supports_vision:
            raise ValueError("vision endpoints must enable supports_vision")
        if self.capability == "vision" and self.models:
            if any(
                profile.status == "active" and not profile.supports_image_input
                for profile in self.models
            ):
                raise ValueError(
                    "active vision endpoint models must enable supports_image_input"
                )
        return self

class ModelEndpointUpdateRequest(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    adapter: str | None = Field(default=None, pattern=_ADAPTERS)
    api_protocol: str | None = Field(default=None, pattern=_PROTOCOLS)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    capability: str | None = Field(default=None, pattern=_CAPABILITIES)
    endpoint_url: str | None = Field(
        default=None, min_length=1, max_length=500, pattern=r"^https?://"
    )
    region: str | None = Field(default=None, max_length=64)
    secret_ref: str | None = Field(default=None, min_length=1, max_length=255)
    secret_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)
    allowed_data_level: str | None = Field(default=None, pattern=_DATA_LEVELS)
    status: str | None = Field(default=None, pattern=_STATUSES)
    max_context: int | None = Field(default=None, ge=1, le=10_000_000)
    timeout_ms: int | None = Field(default=None, ge=100, le=300_000)
    concurrency_limit: int | None = Field(default=None, ge=1, le=10_000)
    supports_streaming_usage: bool | None = None
    supports_structured_output: bool | None = None
    supports_vision: bool | None = None
    supports_cache_pricing: bool | None = None
    extra_models: str | None = Field(default=None, max_length=2000)
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    api_version: str | None = Field(default=None, max_length=64)
    models: list[ModelEndpointModelRequest] | None = None

    @model_validator(mode="after")
    def validate_models(self) -> "ModelEndpointUpdateRequest":
        _validate_model_profiles(self.models)
        if self.capability == "vision" and self.supports_vision is False:
            raise ValueError("vision endpoints must enable supports_vision")
        if self.capability == "vision" and self.models:
            if any(
                profile.status == "active" and not profile.supports_image_input
                for profile in self.models
            ):
                raise ValueError(
                    "active vision endpoint models must enable supports_image_input"
                )
        return self


class ModelEndpointResponse(BaseModel):
    id: str
    provider: str
    adapter: str
    api_protocol: str
    model: str
    capability: str
    endpoint_url: str
    region: str
    status: str
    secret_configured: bool
    secret_fingerprint: str | None
    allowed_data_level: str
    max_context: int
    timeout_ms: int
    concurrency_limit: int
    supports_streaming_usage: bool
    supports_structured_output: bool
    supports_vision: bool
    supports_cache_pricing: bool
    extra_models: str = ""
    sort_order: int = 0
    api_version: str = ""
    models: list[ModelEndpointModelResponse] = Field(default_factory=list)
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


class ModelEndpointTestResponse(BaseModel):
    id: str
    status: str
    reachable: bool
    test_status: str
    capability_tested: str | None = None
    capability_ok: bool | None = None
    latency_ms: int | None
    http_status: int | None
    tested_at: datetime
    error: str | None
    failure_code: str | None = None
    probe_usage: dict[str, Any] | None = None
