from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.models.model_endpoints import ModelEndpoint
from app.schemas.chat import ChatImageContent
from app.services import budget
from app.services.model_routing import ResolvedModelTarget
from app.services.vision_fallback import (
    VisionFallbackError,
    _extract_response_text,
    _request_shape,
    parse_images_with_vision,
    probe_vision_capability,
)


def _endpoint(
    *,
    protocol: str = "chat_completions",
    supports_vision: bool = True,
    secret_ref: str | None = None,
) -> ModelEndpoint:
    return ModelEndpoint(
        id="mep-vision-test",
        provider="vision-provider",
        adapter="openai_compatible",
        api_protocol=protocol,
        model="vision-model",
        capability="vision",
        endpoint_url="https://vision.example.test/v1",
        secret_ref=secret_ref,
        status="active",
        supports_vision=supports_vision,
        allowed_data_level="internal",
        timeout_ms=5_000,
        concurrency_limit=2,
        created_at=None,
        updated_at=None,
    )


def _image(value: bytes = b"image-bytes", mime: str = "image/png") -> ChatImageContent:
    return ChatImageContent(
        data=base64.b64encode(value).decode("ascii"),
        mimeType=mime,
    )


def _response_body() -> dict[str, object]:
    return {
        "images": [
            {
                "index": 1,
                "visible_text": "发票编号 A-001",
                "observations": "图片中有一张发票。",
                "uncertainty": "金额字段部分模糊。",
            },
            {
                "index": 2,
                "visible_text": "第二张图片",
                "observations": "图片中有另一张票据。",
                "uncertainty": "未确认日期。",
            },
        ]
    }


@pytest.mark.parametrize(
    "reported_usage",
    [
        {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    ],
)
def test_parse_images_with_vision_batches_images_and_returns_bounded_context(
    monkeypatch, reported_usage,
):
    monkeypatch.setenv("DOC_NEXUS_VISION_TEST_KEY", "vision-secret")
    settled: list[tuple[str, dict[str, object]]] = []
    released: list[str] = []
    monkeypatch.setattr(
        budget,
        "preflight_vision_budget",
        lambda **_: SimpleNamespace(id="reservation-success"),
    )
    monkeypatch.setattr(
        budget,
        "settle_vision_budget",
        lambda reservation_id, **kwargs: settled.append((reservation_id, kwargs)),
    )
    monkeypatch.setattr(
        budget,
        "release_vision_budget",
        lambda reservation_id: released.append(reservation_id),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert payload["model"] == "vision-model"
        assert payload["stream"] is False
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert "不可信" in payload["messages"][0]["content"]
        content = payload["messages"][1]["content"]
        assert sum(item["type"] == "image_url" for item in content) == 2
        assert all(
            "data:image/" in item["image_url"]["url"]
            for item in content
            if item["type"] == "image_url"
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_response_body(), ensure_ascii=False)
                        }
                    }
                ],
                "usage": reported_usage,
            },
        )

    result = parse_images_with_vision(
        _endpoint(secret_ref="env://DOC_NEXUS_VISION_TEST_KEY"),
        [_image(), _image(b"second")],
        user_id="user-1",
        operation_id="op-1",
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 1
    assert result.model == "vision-model"
    assert result.provider == "vision-provider"
    assert result.usage["total_tokens"] == reported_usage["total_tokens"]
    assert result.usage["reported_total_tokens"] == reported_usage["total_tokens"]
    assert "发票编号 A-001" in result.context
    assert "不可信证据" in result.context
    assert settled == [
        (
            "reservation-success",
            {"actual_cost": str(result.usage.get("cost") or "0")},
        ),
    ]
    assert released == []
    assert result.content_hash


def test_parse_images_with_vision_uses_resolved_profile_model_for_request_and_usage(
    monkeypatch,
):
    endpoint = _endpoint()
    endpoint.model = "legacy-endpoint-model"
    endpoint.supports_vision = False
    target = ResolvedModelTarget(
        endpoint=endpoint,
        profile=SimpleNamespace(
            model="profile-selected-model",
            status="active",
            supports_image_input=True,
        ),
    )
    requested_models: list[str] = []
    monkeypatch.setattr(
        budget,
        "preflight_vision_budget",
        lambda **kwargs: (
            requested_models.append(str(kwargs["model"]))
            or SimpleNamespace(id="reservation-profile")
        ),
    )
    monkeypatch.setattr(
        budget,
        "settle_vision_budget",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(budget, "release_vision_budget", lambda *_args: None)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "profile-selected-model"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "images": [
                                        {
                                            "index": 1,
                                            "visible_text": "profile text",
                                            "observations": "",
                                            "uncertainty": "",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    result = parse_images_with_vision(
        target,
        [_image()],
        user_id="user-profile",
        operation_id="op-profile",
        model="caller-must-not-win",
        transport=httpx.MockTransport(handler),
    )

    assert requested_models == ["profile-selected-model"]
    assert result.model == "profile-selected-model"
    assert result.provider == "vision-provider"
    assert result.usage["model"] == "profile-selected-model"
    assert result.endpoint_id == "mep-vision-test"


@pytest.mark.parametrize("recovery_fails", [False, True])
def test_parse_images_with_vision_fails_closed_when_budget_settlement_fails(
    monkeypatch, recovery_fails,
):
    settled: list[tuple[str, bool]] = []
    released: list[str] = []
    monkeypatch.setattr(
        budget,
        "preflight_vision_budget",
        lambda **_: SimpleNamespace(id="reservation-settle-failure"),
    )

    def fail_first_settlement(reservation_id: str, **kwargs):
        settled.append((reservation_id, bool(kwargs.get("uncertain"))))
        if len(settled) == 1 or recovery_fails:
            raise budget.VisionBudgetExceeded("system")

    monkeypatch.setattr(budget, "settle_vision_budget", fail_first_settlement)
    monkeypatch.setattr(
        budget,
        "release_vision_budget",
        lambda reservation_id: released.append(reservation_id),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        body = _response_body()
        body["images"] = body["images"][:1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(body, ensure_ascii=False),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 8,
                    "total_tokens": 18,
                },
            },
        )

    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(),
            [_image()],
            user_id="user-1",
            operation_id="op-settle-failure",
            transport=httpx.MockTransport(handler),
        )

    assert captured.value.code == "vision_budget_exceeded"
    assert captured.value.reasons == ["vision_settle_failed"]
    assert captured.value.http_status == 200
    assert settled == [
        ("reservation-settle-failure", False),
        ("reservation-settle-failure", True),
    ]
    # A failed recovery must not release a possibly billable provider call;
    # the durable hold can only expire through its bounded hold_until window.
    assert released == []


def test_parse_images_with_vision_rejects_limits_before_request(monkeypatch):
    monkeypatch.setattr(settings, "vision_max_images_per_message", 1)

    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(),
            [_image(), _image(b"second")],
            user_id="user-1",
            operation_id="op-1",
            transport=httpx.MockTransport(lambda _: httpx.Response(500)),
            enforce_budget=False,
        )

    assert captured.value.code == "vision_budget_exceeded"
    assert captured.value.reasons == ["image_count_exceeded"]



def test_preflight_database_outage_is_not_reported_as_exhausted(monkeypatch):
    requests: list[httpx.Request] = []

    def database_unavailable(_db):
        raise OperationalError("BEGIN", {}, RuntimeError("database offline"))

    monkeypatch.setattr(budget, "_begin_serialized", database_unavailable)

    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(),
            [_image()],
            user_id="user-1",
            operation_id="op-db-unavailable",
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200)
            ),
        )

    assert captured.value.code == "vision_budget_exceeded"
    assert captured.value.reasons == ["vision_budget_unavailable"]
    assert requests == []


@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("malformed_output", "vision_output_invalid"),
        ("missing_usage", "vision_usage_invalid"),
        ("incomplete_usage", "vision_usage_invalid"),
    ],
)
def test_parse_images_with_vision_rejects_invalid_response(case, expected_code):
    def handler(_: httpx.Request) -> httpx.Response:
        content = "not-json" if case == "malformed_output" else json.dumps(
            {"images": _response_body()["images"][:1]}
        )
        body = {"choices": [{"message": {"content": content}}]}
        if case == "incomplete_usage":
            body["usage"] = {"prompt_tokens": 8}
        return httpx.Response(
            200,
            json=body,
        )

    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(),
            [_image()],
            user_id="user-1",
            operation_id="op-1",
            transport=httpx.MockTransport(handler),
            enforce_budget=False,
        )

    assert captured.value.code == expected_code


def test_parse_images_with_vision_maps_timeout_without_exposing_payload(monkeypatch):
    secret = "private-secret"
    settled: list[tuple[str, dict[str, object]]] = []
    released: list[str] = []
    monkeypatch.setattr(
        budget,
        "preflight_vision_budget",
        lambda **_: SimpleNamespace(id="reservation-timeout"),
    )
    monkeypatch.setattr(
        budget,
        "settle_vision_budget",
        lambda reservation_id, **kwargs: settled.append((reservation_id, kwargs)),
    )
    monkeypatch.setattr(
        budget,
        "release_vision_budget",
        lambda reservation_id: released.append(reservation_id),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout", request=request)

    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(secret_ref=secret),
            [_image(b"private-image")],
            user_id="user-1",
            operation_id="op-1",
            transport=httpx.MockTransport(handler),
        )

    assert captured.value.code == "vision_timeout"
    assert "private-image" not in str(captured.value)
    assert secret not in str(captured.value)
    assert settled == [("reservation-timeout", {"uncertain": True})]
    assert released == []


@pytest.mark.parametrize(
    ("protocol", "response_body", "expected_path"),
    [
        (
            "chat_completions",
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "images": [
                                        {
                                            "index": 1,
                                            "visible_text": "NEXUS 7319",
                                            "observations": "",
                                            "uncertainty": "",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
            "/v1/chat/completions",
        ),
        (
            "responses",
            {
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "images": [
                                            {
                                                "index": 1,
                                                "visible_text": "NEXUS-7319",
                                                "observations": "",
                                                "uncertainty": "",
                                            }
                                        ]
                                    }
                                ),
                            }
                        ]
                    }
                ],
                "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            },
            "/v1/responses",
        ),
    ],
)
def test_probe_vision_capability_requires_fixed_image_sentinel(
    protocol: str,
    response_body: dict[str, object],
    expected_path: str,
):
    requests: list[httpx.Request] = []

    def matching_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == expected_path
        assert payload["model"] == "qualified/vision-model"
        assert "NEXUS" not in request.content.decode()
        return httpx.Response(200, json=response_body)

    assert probe_vision_capability(
        _endpoint(protocol=protocol),
        model="qualified/vision-model",
        user_id="admin-1",
        transport=httpx.MockTransport(matching_handler),
    )
    assert len(requests) == 1

    def ignored_image_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "images": [
                                        {
                                            "index": 1,
                                            "visible_text": "中国铁路标志",
                                            "observations": "",
                                            "uncertainty": "",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )

    assert not probe_vision_capability(
        _endpoint(),
        model="qualified/vision-model",
        user_id="admin-1",
        transport=httpx.MockTransport(ignored_image_handler),
    )


def test_probe_vision_capability_validates_four_images_and_blocks_over_limit():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        content = payload["messages"][1]["content"]
        assert sum(item["type"] == "image_url" for item in content) == 4
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "images": [
                                        {
                                            "index": index,
                                            "visible_text": "NEXUS 7319",
                                            "observations": "",
                                            "uncertainty": "",
                                        }
                                        for index in range(1, 5)
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 32,
                    "completion_tokens": 16,
                    "total_tokens": 48,
                },
            },
        )

    result = probe_vision_capability(
        _endpoint(),
        model="qualified/vision-model",
        user_id="admin-1",
        image_count=4,
        transport=httpx.MockTransport(handler),
    )

    assert result.matched is True
    assert result.usage["total_tokens"] == 48
    with pytest.raises(VisionFallbackError) as captured:
        probe_vision_capability(
            _endpoint(),
            model="qualified/vision-model",
            user_id="admin-1",
            image_count=5,
            transport=httpx.MockTransport(handler),
        )
    assert captured.value.code == "vision_budget_exceeded"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("timeout", "vision_timeout"),
        ("no_key", "vision_authentication_failed"),
        ("bad_json", "vision_output_invalid"),
        ("missing_usage", "vision_usage_invalid"),
        ("request_parameters", "vision_request_invalid"),
        ("image_media_type", "vision_unsupported_image"),
    ],
)
def test_probe_vision_capability_maps_contract_failures(
    case: str, expected_code: str
):
    def handler(request: httpx.Request) -> httpx.Response:
        if case == "timeout":
            raise httpx.ReadTimeout("slow provider", request=request)
        if case == "no_key":
            assert "Authorization" not in request.headers
            return httpx.Response(401)
        if case == "bad_json":
            return httpx.Response(200, content=b"{not-json")
        if case == "request_parameters":
            return httpx.Response(400, json={"error": {
                "param": "temperature",
                "message": "Unsupported parameter: temperature. private-provider-detail",
            }})
        if case == "image_media_type":
            return httpx.Response(415)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "images": [
                                        {
                                            "index": 1,
                                            "visible_text": "NEXUS 7319",
                                            "observations": "",
                                            "uncertainty": "",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    with pytest.raises(VisionFallbackError) as captured:
        probe_vision_capability(
            _endpoint(),
            model="qualified/vision-model",
            user_id="admin-1",
            secret_override="" if case == "no_key" else "test-key",
            transport=httpx.MockTransport(handler),
        )

    assert captured.value.code == expected_code
    assert "private-provider-detail" not in str(captured.value)


def test_request_shape_anthropic_messages_builds_image_blocks():
    endpoint = _endpoint(
        protocol="messages",
        secret_ref=None,
    )
    endpoint.adapter = "anthropic"
    endpoint.provider = "anthropic"
    url, payload = _request_shape(
        endpoint,
        [("image/png", "YWJj"), ("image/jpeg", "ZGVm")],
        model="claude-sonnet-4-5",
    )
    assert url == "https://vision.example.test/v1/messages"
    assert payload["model"] == "claude-sonnet-4-5"
    assert payload["system"] is not None
    assert "max_tokens" in payload
    messages = payload["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    # Two image source blocks followed by one text block.
    assert len(content) == 3
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "YWJj",
    }
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert content[2]["type"] == "text"


def test_extract_response_text_anthropic_messages_reads_content_blocks():
    text = _extract_response_text(
        {
            "content": [
                {"type": "text", "text": "第一段"},
                {"type": "text", "text": "第二段"},
            ]
        },
        "messages",
    )
    assert text == "第一段第二段"


def test_extract_response_text_anthropic_messages_empty_raises():
    with pytest.raises(VisionFallbackError) as captured:
        _extract_response_text({"content": []}, "messages")
    assert captured.value.code == "vision_output_invalid"


@pytest.mark.parametrize("max_total", [180, 179])
def test_probe_anthropic_messages_endpoint_round_trip(monkeypatch, max_total):
    """Full probe_vision_capability round trip over the Messages protocol."""
    monkeypatch.setattr(settings, "vision_max_total_tokens", max_total)
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["path"] = request.url.path
        received["headers"] = dict(request.headers)
        received["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"images":[{"index":1,"visible_text":"NEXUS7319",'
                            '"observations":"sentinel visible","uncertainty":""}]}'
                        ),
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 20,
                },
            },
        )

    endpoint = _endpoint(protocol="messages", secret_ref="env://VISION_ANTHROPIC_KEY")
    endpoint.adapter = "anthropic"
    endpoint.provider = "anthropic"
    if max_total < 180:
        with pytest.raises(VisionFallbackError) as captured:
            probe_vision_capability(
                endpoint,
                model="claude-sonnet-4-5",
                user_id="admin-1",
                secret_override="sk-ant-probe",
                transport=httpx.MockTransport(handler),
                image_count=1,
            )
        assert captured.value.code == "vision_budget_exceeded"
        assert captured.value.http_status == 200
        return
    result = probe_vision_capability(
        endpoint,
        model="claude-sonnet-4-5",
        user_id="admin-1",
        secret_override="sk-ant-probe",
        transport=httpx.MockTransport(handler),
        image_count=1,
    )
    assert result.matched is True
    assert received["path"] == "/v1/messages"
    payload = received["payload"]
    assert payload["model"] == "claude-sonnet-4-5"
    assert isinstance(payload["messages"][0]["content"], list)
    image_block = payload["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    headers = received["headers"]
    # Proxy host (vision.example.test) → Bearer auth with version header.
    assert headers.get("authorization") == "Bearer sk-ant-probe"
    assert "anthropic-version" in headers


@pytest.mark.parametrize("image_count", [1, 4])
def test_azure_reasoning_vision_probe_accepts_images_without_sampling_parameters(
    image_count,
):
    endpoint = _endpoint(protocol="responses")
    endpoint.provider = "azure_openai"
    endpoint.adapter = "azure_openai"
    endpoint.endpoint_url = "https://resource.openai.azure.com/openai"
    endpoint.api_version = "v1"

    def azure(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "temperature" in payload:
            return httpx.Response(400, json={"error": {
                "param": "temperature",
                "message": "Unsupported parameter: 'temperature' is not supported with this model.",
            }})
        images = [
            item for item in payload["input"][0]["content"]
            if item["type"] == "input_image"
        ]
        assert len(images) == image_count
        assert request.url.path == "/openai/v1/responses"
        assert request.headers["api-key"] == "test-azure-key"
        # Reasoning consumes the same output allowance as the visible JSON.
        if payload["max_output_tokens"] < 256 + 128 * image_count:
            return httpx.Response(200, json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            })
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"type": "message", "content": [{
                "type": "output_text",
                "text": json.dumps({"images": [
                    {"index": index, "visible_text": "NEXUS7319",
                     "observations": "", "uncertainty": ""}
                    for index in range(1, image_count + 1)
                ]}),
            }]}],
            "usage": {"input_tokens": 311, "output_tokens": 79, "total_tokens": 390},
        })

    result = probe_vision_capability(
        endpoint,
        model="gpt-5.6-luna",
        user_id="admin-1",
        secret_override="test-azure-key",
        image_count=image_count,
        transport=httpx.MockTransport(azure),
    )
    assert result.matched is True
    assert result.usage["total_tokens"] == 390


def _document_response(*, visible_text="合同金额：100元", complete=True):
    return {"images": [{
        "index": 1, "visible_text": visible_text,
        "tables": "| 项目 | 金额（元） |\n| --- | --- |\n| 服务 | 100 |",
        "observations": "右下角可见红色印章。",
        "uncertainty": "印章小字无法辨认。", "complete": complete,
    }]}


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "messages"])
def test_document_vision_preserves_text_tables_and_prompt_for_every_protocol(protocol):
    visible_text = "逐字保留合同条款与条件。" * 400
    document = _document_response(visible_text=visible_text)
    endpoint = _endpoint(protocol=protocol)
    if protocol == "messages":
        endpoint.adapter = endpoint.provider = "anthropic"

    def handler(request):
        payload = json.loads(request.content)
        if protocol == "responses":
            assert "逐字" in payload["instructions"]
            response = {"output_text": json.dumps(document), "status": "completed"}
        elif protocol == "messages":
            assert "逐字" in payload["system"]
            response = {"content": [{"type": "text", "text": json.dumps(document)}],
                        "stop_reason": "end_turn"}
        else:
            assert "逐字" in payload["messages"][0]["content"]
            response = {"choices": [{"message": {"content": json.dumps(document)},
                                     "finish_reason": "stop"}]}
        response["usage"] = {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}
        return httpx.Response(200, json=response)

    result = parse_images_with_vision(
        endpoint, [_image()], user_id="user-1",
        operation_id="image-document", document_mode=True, enforce_budget=False,
        transport=httpx.MockTransport(handler),
    )
    assert result.items[0]["visible_text"] == visible_text
    assert result.items[0]["tables"] == document["images"][0]["tables"]
    assert "视觉描述" in result.context
    assert "不确定性" in result.context


@pytest.mark.parametrize("failure", ["truncated", "incomplete", "over_chars", "bad_field", "bad_json"])
def test_document_vision_rejects_incomplete_output_and_keeps_completed_call_usage(failure):
    document = _document_response()
    if failure == "incomplete":
        document["images"][0]["complete"] = False
    if failure == "over_chars":
        document["images"][0]["visible_text"] = "字" * (settings.vision_max_output_chars + 1)
    if failure == "bad_field":
        document["images"][0]["visible_text"] = {"invented": "value"}
    body = {
        "choices": [{"message": {"content": "{" if failure == "bad_json" else json.dumps(document)},
                     "finish_reason": "length" if failure == "truncated" else "stop"}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 7, "total_tokens": 26},
    }
    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            _endpoint(), [_image()], user_id="user-1", operation_id="image-document",
            document_mode=True, enforce_budget=False,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )
    assert captured.value.code in {"vision_output_invalid", "vision_output_truncated"}
    assert captured.value.usage["total_tokens"] == 26


@pytest.mark.parametrize("protocol,body", [
    ("responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}),
    ("messages", {"stop_reason": "max_tokens"}),
])
def test_document_vision_detects_provider_output_limit(protocol, body):
    body["usage"] = {"input_tokens": 8, "output_tokens": 9, "total_tokens": 17}
    endpoint = _endpoint(protocol=protocol)
    if protocol == "messages":
        endpoint.adapter = endpoint.provider = "anthropic"
    with pytest.raises(VisionFallbackError) as captured:
        parse_images_with_vision(
            endpoint, [_image()], user_id="user-1",
            operation_id="image-document", document_mode=True, enforce_budget=False,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )
    assert captured.value.code == "vision_output_truncated"
    assert captured.value.usage["total_tokens"] == 17
