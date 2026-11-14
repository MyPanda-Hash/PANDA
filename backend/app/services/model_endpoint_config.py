from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


ADAPTER_OPENAI = "openai"
ADAPTER_AZURE_OPENAI = "azure_openai"
ADAPTER_OPENAI_COMPATIBLE = "openai_compatible"
ADAPTER_ANTHROPIC = "anthropic"
PROTOCOL_RESPONSES = "responses"
PROTOCOL_CHAT_COMPLETIONS = "chat_completions"
PROTOCOL_MESSAGES = "messages"

SUPPORTED_ADAPTERS = frozenset(
    {
        ADAPTER_OPENAI,
        ADAPTER_AZURE_OPENAI,
        ADAPTER_OPENAI_COMPATIBLE,
        ADAPTER_ANTHROPIC,
    }
)
SUPPORTED_PROTOCOLS = frozenset(
    {PROTOCOL_RESPONSES, PROTOCOL_CHAT_COMPLETIONS, PROTOCOL_MESSAGES}
)


@dataclass(frozen=True, slots=True)
class EndpointConfiguration:
    provider: str
    adapter: str
    api_protocol: str


def _clean(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def legacy_adapter(provider: object) -> str:
    value = _clean(provider)
    if value in {"azure_openai", "azureopenai"} or "azure" in value:
        return ADAPTER_AZURE_OPENAI
    if value in {"openai", "open_ai"}:
        return ADAPTER_OPENAI
    if value in {"anthropic", "claude"} or "claude" in value or "anthropic" in value:
        return ADAPTER_ANTHROPIC
    return ADAPTER_OPENAI_COMPATIBLE


def legacy_protocol(provider: object, adapter: str) -> str:
    value = _clean(provider)
    if adapter in {ADAPTER_OPENAI, ADAPTER_AZURE_OPENAI}:
        return PROTOCOL_RESPONSES
    if value in {"openai_responses", "openai_compatible_responses"}:
        return PROTOCOL_RESPONSES
    if adapter == ADAPTER_ANTHROPIC:
        return PROTOCOL_MESSAGES
    if "anthropic" in value or "claude" in value:
        return PROTOCOL_MESSAGES
    return PROTOCOL_CHAT_COMPLETIONS


def canonical_provider(adapter: str) -> str:
    return {
        ADAPTER_OPENAI: "openai",
        ADAPTER_AZURE_OPENAI: "azure_openai",
        ADAPTER_OPENAI_COMPATIBLE: "openai_compatible",
        ADAPTER_ANTHROPIC: "anthropic",
    }[adapter]


def resolve_endpoint_configuration(
    provider: object = "",
    adapter: object = "",
    api_protocol: object = "",
) -> EndpointConfiguration:
    raw_provider = str(provider or "").strip()
    normalized_adapter = _clean(adapter) if str(adapter or "").strip() else ""
    if not normalized_adapter:
        normalized_adapter = legacy_adapter(raw_provider)
    if normalized_adapter not in SUPPORTED_ADAPTERS:
        raise ValueError(f"unsupported model adapter: {normalized_adapter}")

    normalized_protocol = _clean(api_protocol) if str(api_protocol or "").strip() else ""
    if not normalized_protocol:
        normalized_protocol = legacy_protocol(raw_provider, normalized_adapter)
    if normalized_protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(f"unsupported model API protocol: {normalized_protocol}")
    if normalized_adapter in {ADAPTER_OPENAI, ADAPTER_AZURE_OPENAI}:
        normalized_protocol = PROTOCOL_RESPONSES
    if normalized_adapter == ADAPTER_ANTHROPIC:
        normalized_protocol = PROTOCOL_MESSAGES

    return EndpointConfiguration(
        provider=raw_provider or canonical_provider(normalized_adapter),
        adapter=normalized_adapter,
        api_protocol=normalized_protocol,
    )


def normalize_azure_openai_base_url(base_url: str, *, adapter: str) -> str:
    """Use Azure's native API prefix without rewriting other hosts or adapters."""
    if adapter != ADAPTER_AZURE_OPENAI:
        return base_url
    parsed = urlsplit(base_url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if hostname.endswith(
        (".openai.azure.com", ".cognitiveservices.azure.com", ".ai.azure.com")
    ) and path in {"", "/openai", "/openai/v1/responses"}:
        path = "/openai/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
