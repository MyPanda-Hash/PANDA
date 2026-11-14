from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.core import database
from app.core.config import settings
from app.core.security import now
from app.models.governance import ModelPrice
from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
from app.repositories import system_config
from app.services import linkmind, pi_local

from app.services.model_routing import (
    list_default_model_options,
    list_route_options,
    list_vision_model_options,
    resolve_embedding_endpoint,
    resolve_model_endpoint,
    resolve_vision_endpoint,
)
from app.services.pi_runtime.config import (
    PI_ANTHROPIC_PROVIDER_NAME,
    PI_AZURE_PROVIDER_NAME,
    PI_COMPATIBLE_RESPONSES_PROVIDER_NAME,
    PI_PROVIDER_NAME,
    resolve_pi_model,
    write_models_json,
)
from app.services.pi_runtime.persona import PiPersona, load_pi_persona, qa_system_prompt


def _use_database(path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    return original_path, original_url


def _restore_database(path: Path, original_path: Path, original_url: str) -> None:
    settings.db_path = original_path
    settings.database_url = original_url
    database.reset_database(path)


def _endpoint(
    endpoint_id: str,
    *,
    model: str,
    capability: str,
    provider: str = "openai-compatible",
    supports_vision: bool = False,
    allowed_data_level: str = "internal",
    status: str = "active",
    sort_order: int = 0,
    extra_models: tuple[tuple[str, bool], ...] = (),
) -> ModelEndpoint:
    timestamp = now()
    endpoint = ModelEndpoint(
        id=endpoint_id,
        provider=provider,
        model=model,
        capability=capability,
        endpoint_url="https://models.example.test/v1",
        secret_ref="env://DOC_NEXUS_TEST_MODEL_KEY",
        supports_vision=supports_vision,
        extra_models=", ".join(name for name, _ in extra_models),
        allowed_data_level=allowed_data_level,
        status=status,
        sort_order=sort_order,
        created_at=timestamp,
        updated_at=timestamp,
        last_test_status="reachable",
    )
    endpoint.models = [
        ModelEndpointModel(
            id=f"mepm-{endpoint_id}-{index}",
            endpoint_id=endpoint_id,
            model=name,
            status="active",
            is_default=index == 0,
            sort_order=index * 10,
            max_context=128000,
            supports_image_input=supports_image_input,
            supports_streaming_usage=False,
            supports_structured_output=False,
            supports_cache_pricing=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
        for index, (name, supports_image_input) in enumerate(
            [(model, supports_vision), *extra_models]
        )
    ]
    return endpoint


def _price(provider: str, model: str, *, price_id: str) -> ModelPrice:
    timestamp = now()
    return ModelPrice(
        id=price_id,
        provider=provider,
        model=model,
        currency="CNY",
        input_per_million="1",
        output_per_million="1",
        cache_read_per_million=None,
        cache_write_per_million=None,
        effective_at=timestamp,
        superseded_at=None,
        created_at=timestamp,
    )


def test_resolve_vision_endpoint_does_not_require_price_and_prefers_dedicated(
    tmp_path: Path,
):
    original_path, original_url = _use_database(tmp_path / "vision-routing.sqlite3")
    try:
        with database.transaction() as db:
            dedicated = _endpoint(
                "mep-vision-dedicated",
                model="vision-model",
                capability="vision",
                supports_vision=True,
                allowed_data_level="confidential",
                sort_order=20,
            )
            text_only = _endpoint(
                "mep-text-only",
                model="text-model",
                capability="chat",
                supports_vision=False,
                sort_order=0,
            )
            chat_vision = _endpoint(
                "mep-chat-vision",
                model="chat-vision-model",
                capability="chat",
                supports_vision=True,
                allowed_data_level="restricted",
                sort_order=0,
            )
            lower_level = _endpoint(
                "mep-vision-low-level",
                model="low-vision-model",
                capability="vision",
                supports_vision=True,
                allowed_data_level="internal",
                sort_order=0,
            )
            disabled = _endpoint(
                "mep-vision-disabled",
                model="disabled-vision-model",
                capability="vision",
                supports_vision=True,
                allowed_data_level="restricted",
                status="disabled",
                sort_order=0,
            )
            db.add_all([dedicated, text_only, chat_vision, lower_level, disabled])

        with database.transaction() as db:
            resolved = resolve_vision_endpoint(db, data_level="confidential")
            assert resolved is not None
            assert resolved.id == "mep-vision-dedicated"

            chat_fallback = resolve_vision_endpoint(db, data_level="restricted")
            assert chat_fallback is not None
            assert chat_fallback.id == "mep-chat-vision"
    finally:
        _restore_database(
            tmp_path / "vision-routing.sqlite3", original_path, original_url
        )


def test_vision_model_options_and_explicit_selection(tmp_path: Path):
    original_path, original_url = _use_database(tmp_path / "vision-selection.sqlite3")
    try:
        with database.transaction() as db:
            chat = _endpoint(
                "mep-chat-vision-selection",
                model="deepseek-v4-flash",
                capability="chat",
                supports_vision=False,
                extra_models=(("qwen3.8-flash", True),),
            )
            other = _endpoint(
                "mep-other-vision-selection",
                model="vision-model",
                capability="vision",
                supports_vision=True,
                provider="azure",
                sort_order=10,
            )
            db.add_all([chat, other])
            system_config.upsert(
                db,
                "pi_vision_model",
                "mepm-mep-chat-vision-selection-1",
                "vision model",
            )

        from app.api import chat as chat_api

        with database.transaction() as db:
            options = list_vision_model_options(db)
            selected = resolve_vision_endpoint(
                db,
                configured_profile_id="mepm-mep-chat-vision-selection-1",
            )
            route = chat_api._resolve_chat_image_route(
                db, model="deepseek-v4-flash", image_count=1
            )

        assert options[0] == ("", "自动选择（按端点顺序）")
        assert any(
            value == "mepm-mep-chat-vision-selection-1"
            and "qwen3.8-flash" in label
            for value, label in options
        )
        assert selected is not None
        assert selected.id == "mep-chat-vision-selection"
        assert selected.model == "qwen3.8-flash"
        assert route.mode == "vision_fallback"
        assert route.vision_model == "qwen3.8-flash"
    finally:
        _restore_database(
            tmp_path / "vision-selection.sqlite3", original_path, original_url
        )


def test_chat_capability_options_preserve_endpoint_vision_flag(tmp_path: Path):
    original_path, original_url = _use_database(tmp_path / "vision-options.sqlite3")
    try:
        with database.transaction() as db:
            text = _endpoint(
                "mep-chat-text",
                model="text-model",
                capability="chat",
                supports_vision=False,
            )
            vision = _endpoint(
                "mep-chat-vision",
                model="vision-model",
                capability="chat",
                supports_vision=True,
            )
            db.add_all([text, vision])

        from app.api import chat as chat_api

        default, models, options = chat_api._local_chat_models()
        assert default == "text-model"
        assert models == ["text-model", "vision-model"]
        assert [item.supports_vision for item in options] == [False, True]
    finally:
        _restore_database(
            tmp_path / "vision-options.sqlite3", original_path, original_url
        )


def test_extra_chat_models_do_not_inherit_primary_image_capability(
    tmp_path: Path,
):
    original_path, original_url = _use_database(
        tmp_path / "per-model-vision-options.sqlite3"
    )
    try:
        with database.transaction() as db:
            endpoint = _endpoint(
                "mep-chat-mixed",
                model="qwen3.7-plus",
                capability="chat",
                supports_vision=True,
                extra_models=(("deepseek-v4-flash", False),),
            )
            db.add(endpoint)

        from app.api import chat as chat_api

        _, models, options = chat_api._local_chat_models()
        assert models == ["qwen3.7-plus", "deepseek-v4-flash"]
        assert [item.supports_vision for item in options] == [True, False]
    finally:
        _restore_database(
            tmp_path / "per-model-vision-options.sqlite3",
            original_path,
            original_url,
        )


def test_requested_model_exact_match_beats_default_suffix_alias(tmp_path: Path):
    original_path, original_url = _use_database(
        tmp_path / "exact-model-routing.sqlite3"
    )
    try:
        with database.transaction() as db:
            db.add(
                _endpoint(
                    "mep-chat-aliases",
                    model="Alibaba/qwen3.7-plus",
                    capability="chat",
                    supports_vision=True,
                    extra_models=(("qwen3.7-plus", True),),
                )
            )

        with database.transaction() as db:
            short_name = resolve_model_endpoint(
                db, "chat", requested_model="qwen3.7-plus"
            )
            qualified_name = resolve_model_endpoint(
                db, "chat", requested_model="Alibaba/qwen3.7-plus"
            )

        assert short_name is not None
        assert short_name.model == "qwen3.7-plus"
        assert qualified_name is not None
        assert qualified_name.model == "Alibaba/qwen3.7-plus"
    finally:
        _restore_database(
            tmp_path / "exact-model-routing.sqlite3",
            original_path,
            original_url,
        )


def test_image_route_combines_selected_text_model_with_same_endpoint_vision_model(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "vision_fallback_enabled", False)
    original_path, original_url = _use_database(
        tmp_path / "same-endpoint-vision-routing.sqlite3"
    )
    try:
        with database.transaction() as db:
            endpoint = _endpoint(
                "mep-chat-mixed",
                model="deepseek-v4-flash",
                capability="chat",
                supports_vision=False,
                extra_models=(("qwen3.7-plus", True),),
            )
            endpoint.last_test_status = "capability_failed"
            db.add(endpoint)
            db.add(
                _price(
                    "openai-compatible",
                    "qwen3.7-plus",
                    price_id="price-chat-mixed",
                )
            )

        from app.api import chat as chat_api

        with database.transaction() as db:
            route = chat_api._resolve_chat_image_route(
                db,
                model="deepseek-v4-flash",
                image_count=1,
                data_level="internal",
            )

        assert route.mode == "vision_fallback"
        assert route.answer_endpoint_id == "mep-chat-mixed"
        assert route.answer_model == "deepseek-v4-flash"
        assert route.vision_endpoint_id == "mep-chat-mixed"
        assert route.vision_model == "qwen3.7-plus"
    finally:
        _restore_database(
            tmp_path / "same-endpoint-vision-routing.sqlite3",
            original_path,
            original_url,
        )


def test_image_capable_model_ignores_sibling_probe_failure(tmp_path: Path):
    path = tmp_path / "sibling-vision-probe.sqlite3"
    original_path, original_url = _use_database(path)
    try:
        with database.transaction() as db:
            endpoint = _endpoint(
                "mep-shared-vision",
                model="text-model",
                capability="chat",
                extra_models=(("qwen3.8-flash", True), ("kimi/kimi-k3", True)),
            )
            endpoint.last_test_status = "capability_failed"
            endpoint.last_test_error = "kimi/kimi-k3: fixed-image probe failed"
            db.add(endpoint)

        from app.api import chat as chat_api

        with database.transaction() as db:
            route = chat_api._resolve_chat_image_route(
                db, model="qwen3.8-flash", image_count=1
            )

        assert route.mode == "direct"
        assert route.answer_model == "qwen3.8-flash"
        assert route.vision_target is None
    finally:
        _restore_database(path, original_path, original_url)


def test_disabling_single_vision_endpoint_preserves_governance_records(
    tmp_path: Path,
):
    original_path, original_url = _use_database(
        tmp_path / "vision-endpoint-rollback.sqlite3"
    )
    try:
        with database.transaction() as db:
            db.add(
                _endpoint(
                    "mep-vision-rollback",
                    model="vision-model",
                    capability="vision",
                    supports_vision=True,
                    allowed_data_level="restricted",
                )
            )
            db.add(
                _price(
                    "openai-compatible",
                    "vision-model",
                    price_id="price-vision-rollback",
                )
            )

        with database.transaction() as db:
            assert resolve_vision_endpoint(db, data_level="restricted") is not None
            endpoint = db.get(ModelEndpoint, "mep-vision-rollback")
            assert endpoint is not None
            endpoint.models[0].supports_image_input = False

        with database.transaction() as db:
            assert resolve_vision_endpoint(db, data_level="restricted") is None
            endpoint = db.get(ModelEndpoint, "mep-vision-rollback")
            assert endpoint is not None
            assert endpoint.status == "active"
            assert len(endpoint.models) == 1
            assert db.get(ModelPrice, "price-vision-rollback") is not None
    finally:
        _restore_database(
            tmp_path / "vision-endpoint-rollback.sqlite3",
            original_path,
            original_url,
        )


def test_model_routes_resolve_configured_endpoint_and_do_not_fallback_across_capabilities(
    tmp_path: Path,
):
    original_path, original_url = _use_database(tmp_path / "model-routing.sqlite3")
    try:
        with database.transaction() as db:
            chat = _endpoint("mep-chat", model="chat-model", capability="chat")
            embedding = _endpoint(
                "mep-embedding", model="embedding-model", capability="embedding"
            )
            db.add_all([chat, embedding])
            system_config.upsert(db, "pi_chat_model", chat.id, "chat route")
            system_config.upsert(db, "embedding_model", embedding.id, "embedding route")

        with database.transaction() as db:
            chat_route = resolve_model_endpoint(db, "chat")
            embedding_route = resolve_embedding_endpoint(db)
            assert chat_route is not None
            assert embedding_route is not None
            assert chat_route.id == chat.id
            assert embedding_route.id == embedding.id
            assert list_route_options(db, "embedding") == [
                (embedding.id, "openai-compatible / embedding-model")
            ]

        database.reset_database(tmp_path / "model-routing.sqlite3")
        database.initialize_database()
        with database.transaction() as db:
            db.add(_endpoint("mep-chat-only", model="chat-model", capability="chat"))
        with database.transaction() as db:
            assert resolve_embedding_endpoint(db) is None
    finally:
        _restore_database(
            tmp_path / "model-routing.sqlite3", original_path, original_url
        )


def test_governed_embedding_does_not_switch_away_from_configured_model(
    tmp_path: Path,
):
    path = tmp_path / "governed-embedding-route.sqlite3"
    original_path, original_url = _use_database(path)
    try:
        with database.transaction() as db:
            configured = _endpoint(
                "mep-embedding-configured",
                model="configured-embedding",
                capability="embedding",
                allowed_data_level="internal",
                sort_order=20,
            )
            alternate = _endpoint(
                "mep-embedding-alternate",
                model="alternate-embedding",
                capability="embedding",
                allowed_data_level="restricted",
                sort_order=0,
            )
            db.add_all([configured, alternate])
            system_config.upsert(
                db,
                "embedding_model",
                configured.id,
                "configured embedding route",
            )

        with database.transaction() as db:
            selected = resolve_embedding_endpoint(db)
            assert selected is not None
            assert selected.id == configured.id
            assert resolve_embedding_endpoint(db, data_level="restricted") is None
    finally:
        _restore_database(path, original_path, original_url)


def test_linkmind_user_key_applies_to_model_calls_but_not_embedding(
    tmp_path: Path, monkeypatch
):
    original_path, original_url = _use_database(
        tmp_path / "linkmind-key-routing.sqlite3"
    )
    original_linkmind_enabled = settings.linkmind_enabled
    settings.linkmind_enabled = True
    monkeypatch.setattr(
        pi_local.linkmind, "ensure_user_key", lambda user_id: "lmk_user"
    )
    try:
        with database.transaction() as db:
            chat = _endpoint(
                "mep-linkmind-chat",
                model="chat-model",
                capability="chat",
                provider="local",
            )
            chat.endpoint_url = "http://host.docker.internal:8080/v1"
            chat.secret_ref = linkmind.LINKMIND_ENDPOINT_SECRET_REF
            embedding = _endpoint(
                "mep-linkmind-embedding",
                model="embedding-model",
                capability="embedding",
                provider="local",
            )
            embedding.endpoint_url = "http://host.docker.internal:8080/v1"
            embedding.secret_ref = linkmind.LINKMIND_ENDPOINT_SECRET_REF
            db.add_all([chat, embedding])
            system_config.upsert(db, "pi_chat_model", chat.id, "LinkMind chat route")
            system_config.upsert(
                db, "embedding_model", embedding.id, "LinkMind embedding route"
            )

        chat_route = pi_local.resolve_local_pi_endpoint(
            capability="chat", user_id="usr_1"
        )
        embedding_route = pi_local.resolve_local_pi_endpoint(
            capability="embedding", user_id="usr_1"
        )
        assert chat_route.api_key == "lmk_user"
        assert embedding_route.api_key == ""
    finally:
        settings.linkmind_enabled = original_linkmind_enabled
        _restore_database(
            tmp_path / "linkmind-key-routing.sqlite3", original_path, original_url
        )


def test_writing_default_model_options_come_from_selected_endpoint(tmp_path: Path):
    original_path, original_url = _use_database(tmp_path / "writing-models.sqlite3")
    try:
        with database.transaction() as db:
            writing = _endpoint(
                "mep-write",
                model="qwen-plus",
                capability="chat",
                extra_models=(("qwen-max", False), ("qwen-turbo", False)),
            )
            db.add(writing)
            system_config.upsert(db, "pi_writing_model", writing.id, "writing route")

        with database.transaction() as db:
            assert list_default_model_options(db, "writing") == [
                ("qwen-plus", "qwen-plus"),
                ("qwen-max", "qwen-max"),
                ("qwen-turbo", "qwen-turbo"),
            ]
    finally:
        _restore_database(
            tmp_path / "writing-models.sqlite3", original_path, original_url
        )


def test_local_pi_writing_uses_configured_default_extra_model(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DOC_NEXUS_TEST_MODEL_KEY", "secret")
    original_path, original_url = _use_database(tmp_path / "writing-default.sqlite3")
    try:
        with database.transaction() as db:
            writing = _endpoint(
                "mep-write",
                model="qwen-plus",
                capability="chat",
                extra_models=(("qwen-max", False),),
            )
            db.add(writing)
            system_config.upsert(db, "pi_writing_model", writing.id, "writing route")
            system_config.upsert(
                db, "pi_writing_default_model", "qwen-max", "writing default"
            )
        resolved = pi_local.resolve_local_pi_endpoint(capability="writing")
        assert resolved.model == "qwen-max"
        assert resolved.endpoint_id == "mep-write"
    finally:
        _restore_database(
            tmp_path / "writing-default.sqlite3", original_path, original_url
        )


def test_writing_project_model_persists_and_dispatch_does_not_leak_or_fallback(
    tmp_path: Path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.pi_agent_client import PiAgentResult
    from app.services.writing_dispatcher import WritingDispatcher
    from tests._auth import bootstrap_admin_response

    path = tmp_path / "project-model.sqlite3"
    original_path, original_url = _use_database(path)
    monkeypatch.setenv("DOC_NEXUS_TEST_MODEL_KEY", "secret")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid", "evidence": [], "knowledge_gaps": [],
        },
    )

    def chat(client, message, **kwargs):
        # 保留真实端点解析，仅替代外部收费模型响应。
        endpoint = pi_local.resolve_local_pi_endpoint(
            client.model, capability="writing", user_id=client.user_id
        )
        return PiAgentResult(
            payload={
                "chapters": [{"title": "实施计划", "objective": "明确实施步骤"}],
                "verification": {"passed": True, "findings": []},
            },
            model=endpoint.model,
            session_id=kwargs.get("session_id"),
            token_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            run_time_ms=1,
            trace_steps=1,
        )

    monkeypatch.setattr(pi_local.LocalPiAgentClient, "chat", chat)
    try:
        with database.transaction() as db:
            endpoint = _endpoint(
                "mep-project", model="qwen-plus", capability="chat",
                extra_models=(("qwen-max", False),),
            )
            db.add(endpoint)
            system_config.upsert(db, "pi_writing_model", endpoint.id, "写作路由")
            system_config.upsert(db, "pi_writing_default_model", "qwen-max", "默认模型")
        with TestClient(app) as client:
            auth = bootstrap_admin_response(client)
            headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
            dispatcher = WritingDispatcher(worker_id="model-choice")
            selected_id = ""
            for preferred, expected in (("qwen-plus", "qwen-plus"), ("", "qwen-max")):
                created = client.post(
                    "/writing/projects", headers=headers,
                    json={"title": "模型选择验证", "space_ids": [], "brief": {"preferred_model": preferred}},
                )
                assert created.status_code == 201, created.text
                project_id = created.json()["id"]
                if preferred:
                    selected_id = project_id
                    patched = client.patch(
                        f"/writing/projects/{project_id}", headers=headers,
                        json={"brief": {"purpose": "更新写作要求"}},
                    )
                    assert patched.status_code == 200
                detail = client.get(f"/writing/projects/{project_id}", headers=headers).json()
                assert detail["brief"]["preferred_model"] == preferred
                run = client.post(
                    f"/writing/projects/{project_id}/outline-runs",
                    headers={**headers, "Idempotency-Key": project_id},
                ).json()
                assert dispatcher.run_once()
                result = client.get(f"/writing/operations/{run['id']}", headers=headers).json()
                assert result["status"] == "completed", result
                assert result["usage"]["model"] == expected
            # 创建之后模型被停用：不得偷偷改用仍可用的默认模型。
            with database.transaction() as db:
                endpoint = db.get(ModelEndpoint, "mep-project")
                endpoint.models[0].status = "inactive"
            invalid = client.post(
                "/writing/projects", headers=headers,
                json={"title": "失效模型", "space_ids": [], "brief": {"preferred_model": "qwen-plus"}},
            )
            assert invalid.status_code == 422
            run = client.post(
                f"/writing/projects/{selected_id}/outline-runs",
                headers={**headers, "Idempotency-Key": "disabled-model"},
            ).json()
            assert dispatcher.run_once()
            result = client.get(f"/writing/operations/{run['id']}", headers=headers).json()
            assert result["status"] == "failed", result
            assert result["attempt"] == 1
    finally:
        _restore_database(path, original_path, original_url)


def test_pi_persona_reads_governance_overrides(tmp_path: Path):
    original_path, original_url = _use_database(tmp_path / "persona.sqlite3")
    try:
        with database.transaction() as db:
            system_config.upsert(db, "pi_persona_name", "小助理", "persona name")
            system_config.upsert(db, "pi_persona_title", "知识顾问", "persona title")
            system_config.upsert(
                db,
                "pi_persona_system_prompt",
                "只输出有依据的结论。",
                "persona prompt",
            )

        persona = load_pi_persona()
        assert persona.name == "小助理"
        assert persona.title == "知识顾问"
    finally:
        _restore_database(tmp_path / "persona.sqlite3", original_path, original_url)


def test_pi_persona_default_prompt_tracks_company_and_name(tmp_path: Path):
    original_path, original_url = _use_database(tmp_path / "persona-default.sqlite3")
    try:
        with database.transaction() as db:
            system_config.upsert(db, "app_name", "北京星河科技有限公司", "company name")
            system_config.upsert(db, "pi_persona_name", "小助理", "persona name")

        persona = load_pi_persona()
        assert persona.system_prompt.startswith(
            "你叫「小助理」，是北京星河科技有限公司的数字员工。"
        )
        assert qa_system_prompt() == persona.system_prompt
    finally:
        _restore_database(
            tmp_path / "persona-default.sqlite3", original_path, original_url
        )


def test_local_pi_agent_uses_loaded_persona(monkeypatch):
    monkeypatch.setattr(
        pi_local,
        "load_pi_persona",
        lambda: SimpleNamespace(system_prompt="翠花系统提示词"),
    )

    agent = pi_local._agent(agent_id="agent", user_id="user", model="model")

    assert agent.id == pi_local.PI_RUNTIME_AGENT_ID
    assert agent.tenant_id == "_system"
    assert agent.system_message == "翠花系统提示词"
    assert agent.model_ref.model == "model"


def test_local_pi_agent_describes_sandbox_capabilities(monkeypatch):
    monkeypatch.setattr(
        pi_local,
        "load_pi_persona",
        lambda: PiPersona(name="翠花", title="助手", system_prompt="基础提示"),
    )
    monkeypatch.setattr(settings, "pi_execution_mode", "sandbox")
    monkeypatch.setattr(settings, "pi_chat_network_allowlist", "api.example.com")

    agent = pi_local._agent(agent_id="agent", user_id="user", model="model")

    assert "PPT 运行依赖" in agent.system_message
    assert "禁止直接调用包管理器或在线安装" in agent.system_message
    assert "api.example.com" in agent.system_message
    assert "任意公网网页，但仅限 `GET`/`HEAD`" in agent.system_message


def test_backend_pi_ignores_legacy_external_agent_identity(monkeypatch):
    monkeypatch.setattr(settings, "pi_agent_id", "legacy-agent")
    monkeypatch.setattr(settings, "agent_chat_agent_id", "legacy-agent")

    client = pi_local.LocalPiAgentClient(agent_id="legacy-agent")

    assert client.agent_id == pi_local.PI_RUNTIME_AGENT_ID


def test_pi_model_provider_mapping_supports_openai_compatible_and_azure():
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="agent-model")
    )
    openai = SimpleNamespace(
        provider="openai-compatible",
        api_key="openai-secret",
        base_url="https://models.example.test/v1",
        default_model="default-model",
    )
    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: openai)),
    )
    assert resolved.provider_name == PI_PROVIDER_NAME
    assert resolved.model == "agent-model"
    assert resolved.base_url == "https://models.example.test/v1"

    azure = SimpleNamespace(
        provider="azure_openai",
        api_key="azure-secret",
        azure_endpoint="https://azure.example.test",
        azure_deployment="deployment-v1",
        default_model="azure-model",
    )
    switched = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: azure)),
        credential_id_override="azure",
    )
    assert switched.provider_name == PI_AZURE_PROVIDER_NAME
    assert switched.model == "azure-model"
    assert switched.azure_deployment == "deployment-v1"


def test_pi_model_provider_mapping_accepts_chinese_qwen_label():
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="qwen-model")
    )
    qwen = SimpleNamespace(
        provider="千问",
        api_key="qwen-secret",
        base_url="https://dashscope.example.test/compatible-mode/v1",
        default_model="qwen-model",
    )

    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: qwen)),
    )

    assert resolved.provider_name == PI_PROVIDER_NAME
    assert resolved.model == "qwen-model"
    assert resolved.base_url == "https://dashscope.example.test/compatible-mode/v1"


def test_pi_model_provider_mapping_selects_anthropic_messages_api(tmp_path: Path):
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="claude-model")
    )
    anthropic = SimpleNamespace(
        provider="anthropic",
        adapter="anthropic",
        api_key="anthropic-secret",
        base_url="https://api.anthropic.com",
        default_model="claude-model",
        supports_image_input=True,
    )

    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: anthropic)),
    )

    assert resolved.provider_name == PI_ANTHROPIC_PROVIDER_NAME
    assert resolved.api_protocol == "messages"
    assert resolved.base_url == "https://api.anthropic.com"
    models_path = write_models_json(tmp_path, resolved)
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    provider = payload["providers"][PI_ANTHROPIC_PROVIDER_NAME]
    assert provider["api"] == "anthropic-messages"
    assert provider["baseUrl"] == "https://api.anthropic.com"
    assert provider["models"][0]["id"] == "claude-model"
    assert provider["models"][0]["input"] == ["text", "image"]


def test_pi_model_provider_mapping_strips_trailing_v1_from_anthropic_base_url():
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="claude-model")
    )
    proxy = SimpleNamespace(
        provider="anthropic",
        adapter="anthropic",
        api_key="anthropic-secret",
        base_url="https://proxy.example.test/v1",
        default_model="claude-model",
    )

    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: proxy)),
    )

    # Anthropic SDK appends /v1/messages; a configured /v1 would double it.
    assert resolved.base_url == "https://proxy.example.test"
    assert resolved.api_protocol == "messages"


def test_pi_model_provider_mapping_strips_full_messages_path_from_anthropic_base_url():
    """https://proxy/v1/messages must collapse to the root, not leave /v1."""
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="claude-model")
    )
    proxy = SimpleNamespace(
        provider="anthropic",
        adapter="anthropic",
        api_key="anthropic-secret",
        base_url="https://proxy.example.test/v1/messages",
        default_model="claude-model",
    )

    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: proxy)),
    )

    assert resolved.base_url == "https://proxy.example.test"
    assert resolved.api_protocol == "messages"


def test_pi_model_provider_mapping_selects_responses_api_for_explicit_endpoint(
    tmp_path: Path,
):
    agent = SimpleNamespace(
        model_ref=SimpleNamespace(credential_id="local", model="responses-model")
    )
    compatible = SimpleNamespace(
        provider="openai-compatible",
        adapter="openai_compatible",
        api_protocol="responses",
        api_key="compatible-secret",
        base_url="https://models.example.test/v1",
        default_model="responses-model",
    )

    resolved = resolve_pi_model(
        agent,
        cast(Any, SimpleNamespace(get_credential=lambda credential_id: compatible)),
    )

    assert resolved.provider_name == PI_COMPATIBLE_RESPONSES_PROVIDER_NAME
    assert resolved.api_protocol == "responses"
    models_path = write_models_json(tmp_path, resolved)
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    provider = payload["providers"][PI_COMPATIBLE_RESPONSES_PROVIDER_NAME]
    assert provider["api"] == "openai-responses"
    assert provider["models"][0]["id"] == "responses-model"
    assert provider["models"][0]["input"] == ["text"]
