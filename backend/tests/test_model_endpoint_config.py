"""Contract tests for model endpoint adapter/protocol resolution."""

from __future__ import annotations

import pytest

from app.services.model_endpoint_config import (
    ADAPTER_ANTHROPIC,
    ADAPTER_AZURE_OPENAI,
    ADAPTER_OPENAI,
    ADAPTER_OPENAI_COMPATIBLE,
    PROTOCOL_CHAT_COMPLETIONS,
    PROTOCOL_MESSAGES,
    PROTOCOL_RESPONSES,
    canonical_provider,
    legacy_adapter,
    legacy_protocol,
    resolve_endpoint_configuration,
)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", ADAPTER_OPENAI),
        ("azure_openai", ADAPTER_AZURE_OPENAI),
        ("Azure OpenAI", ADAPTER_AZURE_OPENAI),
        ("openai_compatible", ADAPTER_OPENAI_COMPATIBLE),
        ("anthropic", ADAPTER_ANTHROPIC),
        ("Anthropic", ADAPTER_ANTHROPIC),
        ("claude", ADAPTER_ANTHROPIC),
        ("anthropic_proxy", ADAPTER_ANTHROPIC),
        ("unknown-vendor", ADAPTER_OPENAI_COMPATIBLE),
    ],
)
def test_legacy_adapter_maps_provider_names(provider: str, expected: str) -> None:
    assert legacy_adapter(provider) == expected


@pytest.mark.parametrize(
    ("provider", "adapter", "expected"),
    [
        # legacy_protocol is called with an already-inferred adapter by the
        # resolver; provider alone only matters for legacy provider keywords.
        ("openai", ADAPTER_OPENAI, PROTOCOL_RESPONSES),
        ("azure_openai", ADAPTER_AZURE_OPENAI, PROTOCOL_RESPONSES),
        ("openai_compatible", ADAPTER_OPENAI_COMPATIBLE, PROTOCOL_CHAT_COMPLETIONS),
        ("", ADAPTER_ANTHROPIC, PROTOCOL_MESSAGES),
        ("anthropic", ADAPTER_ANTHROPIC, PROTOCOL_MESSAGES),
        ("openai_responses", ADAPTER_OPENAI_COMPATIBLE, PROTOCOL_RESPONSES),
    ],
)
def test_legacy_protocol_selects_messages_for_anthropic(
    provider: str, adapter: str, expected: str
) -> None:
    assert legacy_protocol(provider, adapter) == expected


def test_legacy_protocol_messages_without_adapter_hint() -> None:
    # When no adapter is passed the provider keyword drives the choice.
    assert legacy_protocol("anthropic", "") == PROTOCOL_MESSAGES
    assert legacy_protocol("anthropic_proxy", "") == PROTOCOL_MESSAGES
    assert legacy_protocol("claude-sonnet", "") == PROTOCOL_MESSAGES


def test_canonical_provider_maps_anthropic_adapter() -> None:
    assert canonical_provider(ADAPTER_ANTHROPIC) == "anthropic"


def test_resolve_anthropic_adapter_forces_messages_protocol() -> None:
    config = resolve_endpoint_configuration(
        provider="anthropic",
        adapter=ADAPTER_ANTHROPIC,
        api_protocol=PROTOCOL_CHAT_COMPLETIONS,
    )
    assert config.adapter == ADAPTER_ANTHROPIC
    assert config.api_protocol == PROTOCOL_MESSAGES
    assert config.provider == "anthropic"


def test_resolve_anthropic_from_provider_only() -> None:
    config = resolve_endpoint_configuration(provider="anthropic/claude-sonnet")
    assert config.adapter == ADAPTER_ANTHROPIC
    assert config.api_protocol == PROTOCOL_MESSAGES


def test_resolve_openai_adapter_forces_responses_protocol() -> None:
    config = resolve_endpoint_configuration(
        provider="openai",
        adapter=ADAPTER_OPENAI,
        api_protocol=PROTOCOL_CHAT_COMPLETIONS,
    )
    assert config.adapter == ADAPTER_OPENAI
    assert config.api_protocol == PROTOCOL_RESPONSES


def test_resolve_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="unsupported model API protocol"):
        resolve_endpoint_configuration(
            provider="openai_compatible",
            adapter=ADAPTER_OPENAI_COMPATIBLE,
            api_protocol="grpc",
        )


def test_resolve_rejects_unknown_adapter() -> None:
    with pytest.raises(ValueError, match="unsupported model adapter"):
        resolve_endpoint_configuration(provider="", adapter="llama.cpp")
