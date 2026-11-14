"""Backend-owned Pi child-process configuration and model resolution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.services.model_endpoint_config import (
    ADAPTER_ANTHROPIC,
    ADAPTER_AZURE_OPENAI,
    ADAPTER_OPENAI,
    ADAPTER_OPENAI_COMPATIBLE,
    PROTOCOL_CHAT_COMPLETIONS,
    PROTOCOL_MESSAGES,
    PROTOCOL_RESPONSES,
    resolve_endpoint_configuration,
)

PI_RUNTIME_AGENT_ID = "doc-nexus-pi"
PI_PROVIDER_NAME = "platform-qwen"
PI_OPENAI_PROVIDER_NAME = "platform-openai"
PI_COMPATIBLE_RESPONSES_PROVIDER_NAME = "platform-openai-responses"
PI_AZURE_PROVIDER_NAME = "azure-openai-responses"
PI_ANTHROPIC_PROVIDER_NAME = "anthropic"
PI_API_KEY_ENV = "PI_PLATFORM_QWEN_API_KEY"
PI_AZURE_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
PI_AZURE_BASE_URL_ENV = "AZURE_OPENAI_BASE_URL"
PI_AZURE_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
PI_AZURE_DEPLOYMENT_MAP_ENV = "AZURE_OPENAI_DEPLOYMENT_NAME_MAP"
PI_AZURE_RESPONSES_API_VERSION = "v1"


class PiRuntimeConfigurationError(ValueError):
    """Pi runtime configuration is incomplete or invalid."""


class CredentialStore(Protocol):
    def get_credential(self, credential_id: str): ...


@dataclass(frozen=True, slots=True)
class PiRuntimeSettings:
    executable: str
    root: Path
    max_stderr_chars: int
    default_timeout_sec: int
    tool_timeout_sec: int = 240
    idle_timeout_sec: int = 600
    execution_mode: str = "host"
    sandbox_image: str = ""
    sandbox_network: str = ""
    sandbox_host_runtime_dir: Path | None = None
    sandbox_memory: str = "1g"
    sandbox_cpus: str = "2"
    sandbox_pids_limit: int = 256
    sandbox_workspace_max_bytes: int = 1024 * 1024 * 1024
    sandbox_workspace_max_files: int = 20_000
    nexus_capability_base_url: str = ""
    nexus_extension_path: Path = Path(__file__).resolve().parent / "extensions" / "nexus_capability.ts"
    tool_policy_extension_path: Path = Path(__file__).resolve().parent / "extensions" / "tool_policy.ts"
    tool_gateway_extension_path: Path = Path(__file__).resolve().parent / "extensions" / "tool_gateway_proxy.ts"
    harness_gateway_url: str = ""
    harness_gateway_token: str = ""

    @classmethod
    def from_env(cls) -> PiRuntimeSettings:
        try:
            max_stderr = int(settings.pi_max_stderr_chars)
        except (TypeError, ValueError):
            max_stderr = 16384
        try:
            default_timeout = int(settings.pi_default_timeout_seconds)
        except (TypeError, ValueError):
            default_timeout = 900
        return cls(
            executable=(settings.pi_executable or "pi").strip() or "pi",
            root=Path(settings.pi_runtime_dir or settings.pi_root),
            max_stderr_chars=max(256, min(1_000_000, max_stderr)),
            default_timeout_sec=max(1, min(3600, default_timeout)),
            tool_timeout_sec=max(1, min(600, int(settings.pi_tool_timeout_seconds))),
            idle_timeout_sec=max(30, min(3600, int(settings.pi_idle_timeout_seconds))),
            execution_mode=(settings.pi_execution_mode or "host").strip().lower(),
            sandbox_image=(settings.pi_sandbox_image or "").strip(),
            sandbox_network=(settings.pi_sandbox_network or "").strip(),
            sandbox_host_runtime_dir=(
                Path(settings.pi_sandbox_host_runtime_dir)
                if settings.pi_sandbox_host_runtime_dir
                else None
            ),
            sandbox_memory=(settings.pi_sandbox_memory or "1g").strip(),
            sandbox_cpus=(settings.pi_sandbox_cpus or "2").strip(),
            sandbox_pids_limit=max(16, min(4096, int(settings.pi_sandbox_pids_limit))),
            sandbox_workspace_max_bytes=max(
                1024 * 1024,
                min(100 * 1024 * 1024 * 1024, int(settings.pi_sandbox_workspace_max_bytes)),
            ),
            sandbox_workspace_max_files=max(
                100,
                min(1_000_000, int(settings.pi_sandbox_workspace_max_files)),
            ),
            nexus_capability_base_url=(settings.pi_nexus_capability_base_url or "").strip().rstrip("/"),
            nexus_extension_path=Path(settings.pi_nexus_extension_path),
            tool_policy_extension_path=Path(settings.pi_tool_policy_extension_path),
            tool_gateway_extension_path=Path(settings.pi_tool_gateway_extension_path),
            harness_gateway_url=(settings.pi_harness_gateway_url or "").strip().rstrip("/"),
            harness_gateway_token=(settings.pi_harness_gateway_token or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ResolvedPiModel:
    credential_id: str
    provider_name: str
    model: str
    base_url: str
    api_key: str
    azure_deployment: str = ""
    azure_api_version: str = ""
    adapter: str = ADAPTER_OPENAI_COMPATIBLE
    api_protocol: str = PROTOCOL_CHAT_COMPLETIONS
    supports_image_input: bool = False
    max_context: int = 128000

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise PiRuntimeConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise PiRuntimeConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed

def _provider_value(credential: object) -> str:
    raw_provider = getattr(credential, "provider", "")
    provider = str(getattr(raw_provider, "value", raw_provider)).strip().lower()
    return {
        "千问": "qwen",
        "通义千问": "qwen",
        "阿里云百炼": "qwen",
        "百炼": "qwen",
        "qianwen": "qwen",
        "openai 兼容": "openai-compatible",
        "openai兼容": "openai-compatible",
    }.get(provider, provider)


def _endpoint_configuration(credential: object, provider: str):
    try:
        return resolve_endpoint_configuration(
            provider,
            getattr(credential, "adapter", ""),
            getattr(credential, "api_protocol", ""),
        )
    except ValueError as exc:
        raise PiRuntimeConfigurationError(str(exc)) from exc


def resolve_pi_model(
    agent: object,
    store: CredentialStore,
    *,
    model_override: str | None = None,
    credential_id_override: str | None = None,
) -> ResolvedPiModel:
    model_ref = getattr(agent, "model_ref", None)
    agent_cred_id = str(getattr(model_ref, "credential_id", "") or "").strip()
    requested_cred_id = (credential_id_override or "").strip()
    credential_id = requested_cred_id or agent_cred_id
    if not credential_id:
        raise PiRuntimeConfigurationError("Pi Agent has no model credential")
    credential = store.get_credential(credential_id)
    if credential is None:
        raise PiRuntimeConfigurationError(f"Pi model credential not found: {credential_id}")
    api_key = str(getattr(credential, "api_key", "") or "").strip()
    if not api_key and not bool(
        getattr(credential, "allow_anonymous_api_key", False)
    ):
        raise PiRuntimeConfigurationError("Pi model credential has no API key")
    switched = bool(requested_cred_id and requested_cred_id != agent_cred_id)
    use_agent_model = bool(agent_cred_id and not switched)
    model = (
        (model_override or "").strip()
        or (str(getattr(model_ref, "model", "") or "").strip() if use_agent_model else "")
        or str(getattr(credential, "default_model", "") or "").strip()
    )
    if not model:
        raise PiRuntimeConfigurationError("Pi model credential has no model")

    provider = _provider_value(credential)
    config = _endpoint_configuration(credential, provider)
    if config.adapter == ADAPTER_AZURE_OPENAI:
        endpoint = str(
            getattr(credential, "azure_endpoint", "")
            or getattr(credential, "base_url", "")
            or ""
        ).strip().rstrip("/")
        deployment = str(getattr(credential, "azure_deployment", "") or "").strip() or model
        if not endpoint:
            raise PiRuntimeConfigurationError("Pi Azure credential has no azure_endpoint")
        return ResolvedPiModel(
            credential_id=credential_id,
            provider_name=PI_AZURE_PROVIDER_NAME,
            model=model,
            base_url=endpoint,
            api_key=api_key,
            azure_deployment=deployment,
            azure_api_version=PI_AZURE_RESPONSES_API_VERSION,
            adapter=config.adapter,
            api_protocol=PROTOCOL_RESPONSES,
            supports_image_input=bool(
                getattr(credential, "supports_image_input", False)
            ),
            max_context=int(getattr(credential, "max_context", 128000) or 128000),
        )

    base_url = str(getattr(credential, "base_url", "") or "").strip().rstrip("/")
    if not base_url:
        raise PiRuntimeConfigurationError("Pi model credential has no base_url")
    if config.adapter == ADAPTER_OPENAI:
        provider_name = PI_OPENAI_PROVIDER_NAME
        api_protocol = PROTOCOL_RESPONSES
    elif config.adapter == ADAPTER_ANTHROPIC:
        provider_name = PI_ANTHROPIC_PROVIDER_NAME
        api_protocol = PROTOCOL_MESSAGES
        # The Anthropic SDK appends /v1/messages to baseURL. Normalize to the
        # root so a configured "https://proxy/v1" does not become /v1/v1.
        # Longest suffix first: "https://proxy/v1/messages" must drop the
        # whole /v1/messages, not just /messages (which would leave /v1).
        for suffix in ("/v1/messages", "/openai/v1", "/v1", "/messages"):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)].rstrip("/")
                break
    elif config.api_protocol == PROTOCOL_RESPONSES:
        provider_name = PI_COMPATIBLE_RESPONSES_PROVIDER_NAME
        api_protocol = PROTOCOL_RESPONSES
    elif config.adapter == ADAPTER_OPENAI_COMPATIBLE:
        provider_name = PI_PROVIDER_NAME
        api_protocol = PROTOCOL_CHAT_COMPLETIONS
    else:
        raise PiRuntimeConfigurationError(
            f"Unsupported Pi model adapter: {config.adapter}"
        )
    return ResolvedPiModel(
        credential_id,
        provider_name,
        model,
        base_url,
        api_key,
        adapter=config.adapter,
        api_protocol=api_protocol,
        supports_image_input=bool(
            getattr(credential, "supports_image_input", False)
        ),
        max_context=int(getattr(credential, "max_context", 128000) or 128000),
    )


def write_models_json(config_dir: Path, resolved: ResolvedPiModel) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "models.json"
    if resolved.adapter == ADAPTER_AZURE_OPENAI:
        api = "azure-openai-responses"
        api_key_env = PI_AZURE_API_KEY_ENV
    elif resolved.api_protocol == PROTOCOL_MESSAGES:
        api = "anthropic-messages"
        api_key_env = PI_API_KEY_ENV
    elif resolved.api_protocol == PROTOCOL_RESPONSES:
        api = "openai-responses"
        api_key_env = PI_API_KEY_ENV
    else:
        api = "openai-completions"
        api_key_env = PI_API_KEY_ENV
    compat = (
        {"supportsDeveloperRole": False, "supportsReasoningEffort": False}
        if api == "openai-completions"
        else {}
    )
    payload = {
        "providers": {
            resolved.provider_name: {
                "baseUrl": resolved.base_url,
                "api": api,
                "apiKey": f"${api_key_env}",
                "compat": compat,
                "models": [
                    {
                        "id": resolved.model,
                        "name": resolved.model,
                        "reasoning": True,
                        "input": (
                            ["text", "image"]
                            if resolved.supports_image_input
                            else ["text"]
                        ),
                        "contextWindow": resolved.max_context,
                        "maxTokens": 32768,
                    }
                ],
            }
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
