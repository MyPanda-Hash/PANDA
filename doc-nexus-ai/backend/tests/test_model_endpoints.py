from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.core import database
from app.core.config import settings
from app.main import app
from app.services import linkmind
from app.services.vision_fallback import VisionFallbackError, VisionProbeResult


class BootstrapSystemAdministrator:
    id = "usr-bootstrap-system-admin"


def _admin_headers(client: TestClient) -> dict[str, str]:
    password = "Admin-model-endpoint-123"
    initialized = client.post(
        "/auth/setup",
        json={
            "new_password": password,
            "password_confirmation": password,
        },
    )
    assert initialized.status_code == 200
    return {"Authorization": f"Bearer {initialized.json()['access_token']}"}


def test_model_endpoint_admin_configuration_and_secret_redaction(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "model-endpoints.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            payload = {
                "provider": "openai-compatible",
                "model": "gpt-test",
                "capability": "chat",
                "endpoint_url": "http://127.0.0.1:1/v1",
                "region": "test",
                "secret_ref": "env://DOC_NEXUS_MODEL_KEY",
                "secret_fingerprint": "a" * 64,
                "allowed_data_level": "internal",
                "supports_streaming_usage": True,
                "supports_structured_output": True,
            }
            invited = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "model-reader",
                    "email": "model-reader@example.com",
                    "display_name": "模型读者",
                },
            ).json()
            assert (
                client.post(
                    f"/auth/invitations/{invited['invite_token']}/accept",
                    json={
                        "password": "Model-reader-123",
                        "password_confirmation": "Model-reader-123",
                    },
                ).status_code
                == 200
            )
            reader = client.post(
                "/auth/login",
                json={"username": "model-reader", "password": "Model-reader-123"},
            ).json()
            assert (
                client.get(
                    "/admin/model-endpoints",
                    headers={"Authorization": f"Bearer {reader['access_token']}"},
                ).status_code
                == 403
            )
            created_response = client.post(
                "/admin/model-endpoints", headers=headers, json=payload
            )
            created = created_response.json()
            assert created["secret_configured"] is True
            assert created["secret_fingerprint"] == "a" * 64
            assert created["adapter"] == "openai_compatible"
            assert created["api_protocol"] == "chat_completions"
            assert created["extra_models"] == ""
            assert isinstance(created["sort_order"], int)
            assert created["models"] == [
                {
                    "id": created["models"][0]["id"],
                    "model": "gpt-test",
                    "status": "active",
                    "is_default": True,
                    "sort_order": 0,
                    "max_context": 128000,
                    "supports_image_input": False,
                    "supports_streaming_usage": True,
                    "supports_structured_output": True,
                    "supports_cache_pricing": False,
                }
            ]
            assert "DOC_NEXUS_MODEL_KEY" not in created_response.text
            endpoint_id = created["id"]

            duplicate = client.post(
                "/admin/model-endpoints", headers=headers, json=payload
            )
            assert duplicate.status_code == 409
            updated = client.patch(
                f"/admin/model-endpoints/{endpoint_id}",
                headers=headers,
                json={
                    "status": "disabled",
                    "model": "gpt-test-v2",
                    "adapter": "openai",
                    "api_protocol": "chat_completions",
                    "sort_order": 3,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["status"] == "disabled"
            assert updated.json()["model"] == "gpt-test-v2"
            assert updated.json()["adapter"] == "openai"
            assert updated.json()["api_protocol"] == "responses"
            assert updated.json()["sort_order"] == 3

            profiled = client.patch(
                f"/admin/model-endpoints/{endpoint_id}",
                headers=headers,
                json={
                    "models": [
                        {
                            "model": "gpt-test-v2",
                            "status": "active",
                            "is_default": True,
                            "sort_order": 0,
                            "max_context": 200000,
                            "supports_image_input": False,
                            "supports_streaming_usage": True,
                            "supports_structured_output": True,
                            "supports_cache_pricing": False,
                        },
                        {
                            "model": "gpt-vision",
                            "status": "active",
                            "is_default": False,
                            "sort_order": 10,
                            "max_context": 128000,
                            "supports_image_input": True,
                            "supports_streaming_usage": True,
                            "supports_structured_output": False,
                            "supports_cache_pricing": False,
                        },
                    ]
                },
            )
            assert profiled.status_code == 200
            assert profiled.json()["model"] == "gpt-test-v2"
            assert profiled.json()["extra_models"] == "gpt-vision"
            assert profiled.json()["supports_vision"] is False
            assert [
                (item["model"], item["supports_image_input"])
                for item in profiled.json()["models"]
            ] == [("gpt-test-v2", False), ("gpt-vision", True)]

            with patch(
                "app.api.model_endpoints.probe_vision_capability",
                side_effect=VisionFallbackError("vision_endpoint_unavailable"),
            ):
                tested = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test", headers=headers
                )
            assert tested.status_code == 200
            assert tested.json()["reachable"] is False
            assert tested.json()["status"] == "disabled"
            assert (
                client.get(
                    f"/admin/model-endpoints/{endpoint_id}", headers=headers
                ).json()["last_test_status"]
                == "unavailable"
            )
            assert (
                client.delete(
                    f"/admin/model-endpoints/{endpoint_id}", headers=headers
                ).status_code
                == 204
            )
            assert (
                client.get(
                    f"/admin/model-endpoints/{endpoint_id}", headers=headers
                ).status_code
                == 404
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "model-endpoints.sqlite3")


def test_model_endpoint_list_orders_enabled_before_disabled(tmp_path: Path):
    """Admin list keeps in-service connections above disabled ones."""
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "model-endpoint-order.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)

            def create(model: str, sort_order: int) -> str:
                created = client.post(
                    "/admin/model-endpoints",
                    headers=headers,
                    json={
                        "provider": "openai-compatible",
                        "model": model,
                        "capability": "chat",
                        "endpoint_url": "http://127.0.0.1:1/v1",
                        "sort_order": sort_order,
                    },
                )
                assert created.status_code == 201
                return created.json()["id"]

            enabled = create("order-enabled", 20)
            disabled = create("order-disabled", 0)
            assert (
                client.patch(
                    f"/admin/model-endpoints/{disabled}",
                    headers=headers,
                    json={"status": "disabled"},
                ).status_code
                == 200
            )

            rows = client.get("/admin/model-endpoints", headers=headers).json()
            listed = [row["id"] for row in rows]
            assert listed.index(enabled) < listed.index(disabled)
            first_inactive = next(
                index for index, row in enumerate(rows) if row["status"] != "active"
            )
            assert all(row["status"] == "active" for row in rows[:first_inactive])
            assert all(row["status"] != "active" for row in rows[first_inactive:])
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "model-endpoint-order.sqlite3")


def test_chat_model_options_follow_endpoint_sort_order(tmp_path: Path):
    """Chat picker expands endpoints as primary+extras in sort_order sequence."""
    from sqlalchemy import select

    from app.api import chat as chat_api
    from app.core.security import now
    from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
    from app.repositories import system_config

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "chat-model-sort.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        database.reset_database(settings.db_path)
        database.initialize_database()
        stamp = now()
        with database.transaction() as db:
            for row in db.scalars(
                select(ModelEndpoint).where(ModelEndpoint.capability == "chat")
            ):
                row.status = "disabled"
            db.add_all(
                [
                    ModelEndpoint(
                        id="mep_sort_b",
                        provider="zzz-provider",
                        adapter="openai_compatible",
                        api_protocol="chat_completions",
                        model="endpoint-b-primary",
                        extra_models="endpoint-b-extra",
                        capability="chat",
                        endpoint_url="https://b.example.test/v1",
                        region="",
                        status="active",
                        sort_order=20,
                        created_at=stamp,
                        updated_at=stamp,
                        models=[
                            ModelEndpointModel(
                                id="mepm_sort_b_primary",
                                endpoint_id="mep_sort_b",
                                model="endpoint-b-primary",
                                status="active",
                                is_default=True,
                                sort_order=0,
                                max_context=128000,
                                created_at=stamp,
                                updated_at=stamp,
                            ),
                            ModelEndpointModel(
                                id="mepm_sort_b_extra",
                                endpoint_id="mep_sort_b",
                                model="endpoint-b-extra",
                                status="active",
                                is_default=False,
                                sort_order=10,
                                max_context=128000,
                                created_at=stamp,
                                updated_at=stamp,
                            ),
                        ],
                    ),
                    ModelEndpoint(
                        id="mep_sort_a",
                        provider="aaa-provider",
                        adapter="openai_compatible",
                        api_protocol="chat_completions",
                        model="endpoint-a-primary",
                        extra_models="endpoint-a-extra-1, endpoint-a-extra-2",
                        capability="chat",
                        endpoint_url="https://a.example.test/v1",
                        region="",
                        status="active",
                        sort_order=10,
                        created_at=stamp,
                        updated_at=stamp,
                        models=[
                            ModelEndpointModel(
                                id="mepm_sort_a_primary",
                                endpoint_id="mep_sort_a",
                                model="endpoint-a-primary",
                                status="active",
                                is_default=True,
                                sort_order=0,
                                max_context=128000,
                                created_at=stamp,
                                updated_at=stamp,
                            ),
                            ModelEndpointModel(
                                id="mepm_sort_a_extra_1",
                                endpoint_id="mep_sort_a",
                                model="endpoint-a-extra-1",
                                status="active",
                                is_default=False,
                                sort_order=10,
                                max_context=128000,
                                created_at=stamp,
                                updated_at=stamp,
                            ),
                            ModelEndpointModel(
                                id="mepm_sort_a_extra_2",
                                endpoint_id="mep_sort_a",
                                model="endpoint-a-extra-2",
                                status="active",
                                is_default=False,
                                sort_order=20,
                                max_context=128000,
                                created_at=stamp,
                                updated_at=stamp,
                            ),
                        ],
                    ),
                ]
            )
            system_config.upsert(
                db, "pi_chat_model", "mep_sort_b", "default chat route"
            )

        default_model, models, options = chat_api._local_chat_models()
        assert default_model == "endpoint-a-primary"
        assert [item.model for item in options] == [
            "endpoint-a-primary",
            "endpoint-a-extra-1",
            "endpoint-a-extra-2",
            "endpoint-b-primary",
            "endpoint-b-extra",
        ]
        assert models == [item.model for item in options]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "chat-model-sort.sqlite3")


def test_model_endpoint_rejects_linkmind_secret_ref_when_disabled(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    original_linkmind_enabled = settings.linkmind_enabled
    settings.db_path = tmp_path / "model-endpoints-linkmind.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.linkmind_enabled = False
    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            payload = {
                "provider": "openai-compatible",
                "model": "gpt-linkmind",
                "capability": "chat",
                "endpoint_url": "http://127.0.0.1:1/v1",
                "region": "test",
                "secret_ref": linkmind.LINKMIND_ENDPOINT_SECRET_REF,
            }
            created = client.post(
                "/admin/model-endpoints", headers=headers, json=payload
            )
            assert created.status_code == 422
            assert "LinkMind" in created.json()["detail"]

            # 已存在端点也不允许在未启用时把 secret_ref 改成 LinkMind 引用。
            base_payload = dict(payload, secret_ref=None)
            existing = client.post(
                "/admin/model-endpoints", headers=headers, json=base_payload
            )
            assert existing.status_code == 201
            endpoint_id = existing.json()["id"]
            updated = client.patch(
                f"/admin/model-endpoints/{endpoint_id}",
                headers=headers,
                json={"secret_ref": linkmind.LINKMIND_ENDPOINT_SECRET_REF},
            )
            assert updated.status_code == 422
            assert (
                client.get(
                    f"/admin/model-endpoints/{endpoint_id}", headers=headers
                ).json()["secret_configured"]
                is False
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(tmp_path / "model-endpoints-linkmind.sqlite3")


def test_model_endpoint_visual_probe_failure_updates_test_state_only(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "model-endpoints-visual-probe.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"


    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            created = client.post(
                "/admin/model-endpoints",
                headers=headers,
                json={
                    "provider": "openai-compatible",
                    "model": "Alibaba/qwen3.7-plus",
                    "capability": "chat",
                    "endpoint_url": "https://models.example.test/v1",
                    "supports_vision": True,
                    "secret_ref": "test-probe-secret",
                    "region": "test",
                    "models": [
                        {
                            "model": "Alibaba/qwen3.7-plus",
                            "status": "active",
                            "is_default": True,
                            "sort_order": 0,
                            "supports_image_input": True,
                        },
                        {
                            "model": "qwen3.7-plus",
                            "status": "active",
                            "is_default": False,
                            "sort_order": 10,
                            "supports_image_input": True,
                        },
                        {
                            "model": "disabled-vision",
                            "status": "disabled",
                            "supports_image_input": True,
                        },
                        {
                            "model": "text-only",
                            "supports_image_input": False,
                        },
                    ],
                },
            )
            assert created.status_code == 201
            endpoint_id = created.json()["id"]

            with patch(
                "app.api.model_endpoints.probe_vision_capability",
                side_effect=[
                    VisionFallbackError("vision_call_failed", reachable=True),
                    VisionFallbackError("vision_call_failed", reachable=True),
                    VisionProbeResult(
                        matched=True,
                        usage={
                            "provider": "openai-compatible",
                            "model": "qwen3.7-plus",
                            "endpoint_id": "probe",
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                            "cost": "0",
                            "latency_ms": 1,
                        },
                        latency_ms=1,
                    ),
                    VisionProbeResult(
                        matched=True,
                        usage={
                            "provider": "openai-compatible",
                            "model": "qwen3.7-plus",
                            "endpoint_id": "probe",
                            "input_tokens": 4,
                            "output_tokens": 1,
                            "total_tokens": 5,
                            "cost": "0",
                            "latency_ms": 1,
                        },
                        latency_ms=1,
                    ),
                ],
            ) as probe:
                tested = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    headers=headers,
                )

            assert tested.status_code == 200
            result = tested.json()
            assert result["reachable"] is True
            assert result["test_status"] == "capability_failed"
            assert result["capability_tested"] == "vision"
            assert result["capability_ok"] is False
            assert "Alibaba/qwen3.7-plus" in result["error"]
            assert [item.kwargs["model"] for item in probe.call_args_list] == [
                "Alibaba/qwen3.7-plus",
                "Alibaba/qwen3.7-plus",
                "qwen3.7-plus",
                "qwen3.7-plus",
            ]
            assert [item.kwargs["image_count"] for item in probe.call_args_list] == [
                1,
                4,
                1,
                4,
            ]

            persisted = client.get(
                f"/admin/model-endpoints/{endpoint_id}",
                headers=headers,
            ).json()
            assert persisted["status"] == "active"
            assert persisted["last_test_status"] == "capability_failed"
            assert "Alibaba/qwen3.7-plus" in persisted["last_test_error"]
            assert {
                item["model"]: item["supports_image_input"]
                for item in persisted["models"]
            } == {
                "Alibaba/qwen3.7-plus": True,
                "qwen3.7-plus": True,
                "disabled-vision": True,
                "text-only": False,
            }

            selected_model = "Alibaba/qwen3.7-plus"
            with patch(
                "app.api.model_endpoints.probe_vision_capability",
                return_value=VisionProbeResult(
                    matched=True,
                    usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    latency_ms=1,
                ),
            ):
                selected = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    params={"model": selected_model},
                    headers=headers,
                )
            assert selected.status_code == 200
            selection = selected.json()
            assert selection["test_status"] == "reachable"
            assert selection["capability_ok"] is True
            assert selection["probe_usage"]["model"] == selected_model
            assert [
                (check["model"], check["image_count"], check["status"])
                for check in selection["probe_usage"]["checks"]
            ] == [
                (selected_model, 1, "completed"),
                (selected_model, 4, "completed"),
            ]

            with patch(
                "app.api.model_endpoints.probe_vision_capability",
                side_effect=VisionFallbackError("vision_endpoint_unavailable"),
            ):
                failed = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    params={"model": "qwen3.7-plus"},
                    headers=headers,
                )
            assert failed.status_code == 200
            assert failed.json()["reachable"] is False
            assert failed.json()["test_status"] == "unavailable"
            assert failed.json()["status"] == "active"
            persisted = client.get(
                f"/admin/model-endpoints/{endpoint_id}", headers=headers
            ).json()
            assert persisted["status"] == "active"
            assert persisted["last_test_status"] == "unavailable"

            with patch(
                "app.api.model_endpoints._resolve_test_credentials",
                side_effect=AssertionError("invalid selection resolved credentials"),
            ):
                for invalid_model, expected_status in (
                    ("missing-model", 404),
                    ("Alibaba/qwen3.7-plus ", 404),
                    ("disabled-vision", 422),
                    ("text-only", 422),
                ):
                    invalid = client.post(
                        f"/admin/model-endpoints/{endpoint_id}:test",
                        params={"model": invalid_model},
                        headers=headers,
                    )
                    assert invalid.status_code == expected_status
            assert client.get(
                f"/admin/model-endpoints/{endpoint_id}", headers=headers
            ).json()["last_tested_at"] == persisted["last_tested_at"]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(path)


@pytest.mark.parametrize(
    ("secret_ref", "expected_secret"),
    [
        ("env://DOC_NEXUS_AUTHENTICATED_PROBE_KEY", "probe-secret"),
        ("raw-probe-secret", "raw-probe-secret"),
    ],
)
def test_model_endpoint_vision_probe_uses_configured_secret(
    tmp_path: Path,
    secret_ref: str,
    expected_secret: str,
):
    original = settings.db_path
    original_url = settings.database_url
    original_linkmind_enabled = settings.linkmind_enabled
    path = tmp_path / "model-endpoints-authenticated-probe.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_enabled = False
    try:
        with (
            patch.dict(
                "os.environ",
                {"DOC_NEXUS_AUTHENTICATED_PROBE_KEY": "probe-secret"},
            ),
            TestClient(app) as client,
        ):
            headers = _admin_headers(client)
            created = client.post(
                "/admin/model-endpoints",
                headers=headers,
                json={
                    "provider": "openai-compatible",
                    "model": "vision-model",
                    "capability": "chat",
                    "endpoint_url": "https://models.example.test/v1",
                    "region": "authenticated-probe",
                    "secret_ref": secret_ref,
                    "supports_vision": True,
                    "allowed_data_level": "restricted",
                    "models": [
                        {
                            "model": "vision-model",
                            "status": "active",
                            "is_default": True,
                            "sort_order": 0,
                            "supports_image_input": True,
                        }
                    ],
                },
            )
            assert created.status_code == 201
            endpoint_id = created.json()["id"]
            with patch(
                "app.api.model_endpoints.probe_vision_capability",
                return_value=VisionProbeResult(
                    matched=True,
                    usage={
                        "provider": "openai-compatible",
                        "model": "vision-model",
                        "endpoint_id": endpoint_id,
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                        "cost": "0",
                        "latency_ms": 1,
                    },
                    latency_ms=1,
                ),
            ) as vision_probe:
                tested = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    headers=headers,
                )
            assert tested.status_code == 200
            result = tested.json()
            assert result["reachable"] is True
            assert result["test_status"] == "reachable"
            assert result["capability_ok"] is True
            assert result["probe_usage"]["model"] == "vision-model"
            assert result["probe_usage"]["total_tokens"] == 4
            assert [
                check["image_count"] for check in result["probe_usage"]["checks"]
            ] == [1, 4]
            assert vision_probe.call_count == 2
            assert all(
                call.kwargs["secret_override"] == expected_secret
                for call in vision_probe.call_args_list
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(path)

def test_model_endpoint_metadata_invalid_keeps_safe_probe_summary(
    tmp_path: Path,
) -> None:
    original = settings.db_path
    original_url = settings.database_url
    original_linkmind_enabled = settings.linkmind_enabled
    path = tmp_path / "model-endpoints-metadata-invalid.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_enabled = False

    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            created = client.post(
                "/admin/model-endpoints",
                headers=headers,
                json={
                    "provider": "openai-compatible",
                    "model": "vision-model",
                    "capability": "chat",
                    "endpoint_url": "https://models.example.test/v1",
                    "region": "metadata-invalid",
                    "supports_vision": True,
                    "models": [
                        {
                            "model": "vision-model",
                            "status": "active",
                            "is_default": True,
                            "sort_order": 0,
                            "supports_image_input": True,
                        }
                    ],
                },
            )
            assert created.status_code == 201
            endpoint_id = created.json()["id"]
            usage = {
                "provider": "openai-compatible",
                "model": "vision-model",
                "endpoint_id": endpoint_id,
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "cost": "0",
                "latency_ms": 1,
                "prompt": "probe prompt must not be returned",
                "images": ["image bytes must not be returned"],
                "body": {"raw": "provider raw body must not be returned"},
                "secret": "probe secret must not be returned",
            }
            with (
                patch(
                    "app.api.model_endpoints.vision_endpoint_metadata_errors",
                    return_value=("concurrency_invalid",),
                ),
                patch(
                    "app.api.model_endpoints.probe_vision_capability",
                    return_value=VisionProbeResult(
                        matched=True,
                        usage=usage,
                        latency_ms=1,
                    ),
                ) as vision_probe,
            ):
                tested = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    headers=headers,
                )

            assert tested.status_code == 200
            result = tested.json()
            assert result["reachable"] is True
            assert result["test_status"] == "metadata_invalid"
            assert result["capability_ok"] is False
            assert result["failure_code"] == "vision_metadata_invalid"
            assert set(result) == {
                "id",
                "status",
                "reachable",
                "test_status",
                "capability_tested",
                "capability_ok",
                "latency_ms",
                "http_status",
                "tested_at",
                "error",
                "failure_code",
                "probe_usage",
            }
            assert result["probe_usage"]["total_tokens"] == 4
            assert [
                check["image_count"] for check in result["probe_usage"]["checks"]
            ] == [1, 4]
            assert all(
                check["status"] == "completed"
                for check in result["probe_usage"]["checks"]
            )
            assert all(
                set(check) == {
                    "model",
                    "image_count",
                    "status",
                    "latency_ms",
                    "error_code",
                    "usage",
                }
                for check in result["probe_usage"]["checks"]
            )
            assert all(
                set(check["usage"]).issubset(
                    {
                        "provider",
                        "model",
                        "endpoint_id",
                        "input_tokens",
                        "input_uncached_tokens",
                        "cache_read_tokens",
                        "cache_write_tokens",
                        "output_tokens",
                        "total_tokens",
                        "cost",
                        "latency_ms",
                        "status",
                        "error_code",
                        "currency",
                        "price_version_id",
                    }
                )
                for check in result["probe_usage"]["checks"]
            )
            assert vision_probe.call_count == 2
            for forbidden in (
                "probe prompt must not be returned",
                "image bytes must not be returned",
                "provider raw body must not be returned",
                "probe secret must not be returned",
            ):
                assert forbidden not in tested.text
    finally:
        settings.db_path = original
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(path)


def test_model_endpoint_linkmind_probe_uses_bootstrap_key_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = settings.db_path
    original_url = settings.database_url
    original_linkmind_enabled = settings.linkmind_enabled
    path = tmp_path / "model-endpoints-linkmind-probe.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_enabled = True
    monkeypatch.setattr(
        "app.services.linkmind.sync_user_by_id", lambda _user_id: None
    )
    monkeypatch.setattr(
        "app.services.linkmind.sync_user", lambda _db, _user: None
    )
    monkeypatch.setattr(
        "app.services.linkmind.ensure_user_key",
        lambda *_args, **_kwargs: "startup-key",
    )


    try:
        with TestClient(app) as client:
            headers = _admin_headers(client)
            created = client.post(
                "/admin/model-endpoints",
                headers=headers,
                json={
                    "provider": "openai-compatible",
                    "model": "linkmind-test-model",
                    "capability": "chat",
                    "endpoint_url": "https://models.example.test/v1",
                    "supports_vision": True,
                    "allowed_data_level": "restricted",
                    "region": "linkmind-test",
                    "secret_ref": "configured-operator-key",
                    "models": [
                        {
                            "model": "linkmind-test-model",
                            "status": "active",
                            "is_default": True,
                            "sort_order": 0,
                            "supports_image_input": True,
                        }
                    ],
                },
            )
            assert created.status_code == 201
            endpoint_id = created.json()["id"]

            with (
                patch(
                    "app.api.model_endpoints.users.get_bootstrap_admin",
                    return_value=BootstrapSystemAdministrator(),
                ) as bootstrap_lookup,
                patch(
                    "app.api.model_endpoints.linkmind.ensure_user_key",
                    return_value="bootstrap-linkmind-key",
                ) as ensure_user_key,
                patch(
                    "app.api.model_endpoints.vision_endpoint_metadata_errors",
                    return_value=(),
                ),
                patch(
                    "app.api.model_endpoints.probe_vision_capability",
                    return_value=VisionProbeResult(
                        matched=True,
                        usage={
                            "provider": "openai-compatible",
                            "model": "linkmind-test-model",
                            "endpoint_id": endpoint_id,
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                            "cost": "0",
                            "latency_ms": 1,
                        },
                        latency_ms=1,
                    ),
                ) as vision_probe,
            ):
                tested = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    headers=headers,
                )

            assert tested.status_code == 200
            assert tested.json()["reachable"] is True
            assert tested.json()["test_status"] == "reachable"
            bootstrap_args = bootstrap_lookup.call_args
            assert bootstrap_args.args[1] == settings.bootstrap_username
            assert ensure_user_key.call_args.args == (
                BootstrapSystemAdministrator.id,
            )
            assert ensure_user_key.call_args.kwargs["db"] is bootstrap_args.args[0]
            assert (
                vision_probe.call_args.kwargs["secret_override"]
                == "bootstrap-linkmind-key"
            )

            with (
                patch(
                    "app.api.model_endpoints.users.get_bootstrap_admin",
                    return_value=BootstrapSystemAdministrator(),
                ),
                patch(
                    "app.api.model_endpoints.linkmind.ensure_user_key",
                    side_effect=linkmind.LinkMindError("key provisioning failed"),
                ),
            ):
                failed = client.post(
                    f"/admin/model-endpoints/{endpoint_id}:test",
                    headers=headers,
                )

            assert failed.status_code == 200
            failure = failed.json()
            assert failure["reachable"] is False
            assert failure["test_status"] == "authentication_failed"
            assert "LinkMind" in failure["error"]
            persisted = client.get(
                f"/admin/model-endpoints/{endpoint_id}",
                headers=headers,
            ).json()
            assert persisted["last_test_status"] == "authentication_failed"
            assert persisted["status"] == "active"
    finally:
        settings.db_path = original
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(path)
