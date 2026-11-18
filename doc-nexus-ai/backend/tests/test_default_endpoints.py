from pathlib import Path

from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.models.model_endpoints import ModelEndpoint
from app.services.default_endpoints import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    auto_provision_default_endpoints,
    default_chat_url,
    ensure_linkmind_default_endpoints,
    refresh_existing_default_chat_endpoint,
)


def test_auto_provision_uses_probe_chat_url(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "default-endpoints.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            result = auto_provision_default_endpoints(
                db,
                {
                    "chat": {
                        "endpoint_url": "http://192.168.88.193:8080/v1",
                        "model": "Alibaba/qwen3.7-plus",
                        "extra_models": "DeepSeek/deepseek-v4-flash",
                    },
                    "embedding": {
                        "endpoint_url": "http://192.168.88.193:8888/v1",
                        "model": "bge-m3",
                    },
                },
                "usr-test-admin",
            )
            endpoint = db.get(ModelEndpoint, result["chat_endpoint_id"])
            assert endpoint is not None
            assert endpoint.endpoint_url == "http://192.168.88.193:8080/v1"
            assert endpoint.model == DEFAULT_CHAT_MODEL
            assert endpoint.status == "active"
            embedding = db.get(ModelEndpoint, result["embedding_endpoint_id"])
            assert embedding is not None
            assert embedding.endpoint_url == "http://192.168.88.193:8888/v1"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_auto_provision_empty_probe_creates_nothing(tmp_path: Path):
    """空探测结果不应创建硬编码模型端点."""
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "default-endpoints-empty.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            result = auto_provision_default_endpoints(
                db,
                {"chat": {}, "embedding": {"model": "bge-m3"}},
                "usr-test-admin",
            )
            assert result["chat_endpoint_id"] == ""
            endpoint = db.scalar(
                select(ModelEndpoint).where(ModelEndpoint.capability == "chat")
            )
            assert endpoint is None
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_linkmind_defaults_skip_without_configured_base(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    original_base_url = settings.linkmind_base_url
    path = tmp_path / "linkmind-defaults-without-base.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_base_url = ""
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            assert ensure_linkmind_default_endpoints(db) == {}
            assert db.query(ModelEndpoint).count() == 0
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)


def _create_local_chat_endpoint(db) -> ModelEndpoint:
    endpoint = ModelEndpoint(
        id="ep-local-refresh",
        provider="local",
        adapter="openai_compatible",
        api_protocol="chat_completions",
        model="old-model",
        capability="chat",
        endpoint_url=default_chat_url(),
        status="disabled",
        created_at=now(),
        updated_at=now(),
    )
    db.add(endpoint)
    db.flush()
    return endpoint


def test_refresh_marks_local_endpoint_unavailable_when_linkmind_disabled(
    tmp_path: Path,
):
    original_path = settings.db_path
    original_url = settings.database_url
    original_linkmind = settings.linkmind_enabled
    original_base_url = settings.linkmind_base_url
    settings.linkmind_base_url = "http://linkmind.example:8080"
    settings.linkmind_enabled = False
    path = tmp_path / "default-endpoints-refresh-disabled.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            endpoint = _create_local_chat_endpoint(db)
            changed = refresh_existing_default_chat_endpoint(db)
            assert changed is True
            assert endpoint.status == "unavailable"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)


def test_refresh_marks_local_endpoint_unavailable_when_gateway_down(
    tmp_path: Path, monkeypatch
):
    original_path = settings.db_path
    original_url = settings.database_url
    original_linkmind = settings.linkmind_enabled
    original_base_url = settings.linkmind_base_url
    settings.linkmind_base_url = "http://linkmind.example:8080"
    settings.linkmind_enabled = True
    path = tmp_path / "default-endpoints-refresh-down.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    try:
        monkeypatch.setattr(
            "app.services.default_endpoints._local_chat_reachable",
            lambda: False,
        )
        with transaction() as db:
            endpoint = _create_local_chat_endpoint(db)
            changed = refresh_existing_default_chat_endpoint(db)
            assert changed is True
            assert endpoint.status == "unavailable"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)


def test_refresh_activates_local_endpoint_when_gateway_up(
    tmp_path: Path, monkeypatch
):
    original_path = settings.db_path
    original_url = settings.database_url
    original_linkmind = settings.linkmind_enabled
    original_base_url = settings.linkmind_base_url
    settings.linkmind_base_url = "http://linkmind.example:8080"
    settings.linkmind_enabled = True
    path = tmp_path / "default-endpoints-refresh-up.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.reset_database(path)
    database.initialize_database()
    try:
        monkeypatch.setattr(
            "app.services.default_endpoints._local_chat_reachable",
            lambda: True,
        )
        with transaction() as db:
            endpoint = _create_local_chat_endpoint(db)
            changed = refresh_existing_default_chat_endpoint(db)
            assert changed is True
            assert endpoint.status == "active"
            assert endpoint.model == "old-model"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_enabled = original_linkmind
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)


def test_linkmind_defaults_reuse_existing_unique_routes(tmp_path: Path, monkeypatch):
    original_path = settings.db_path
    original_url = settings.database_url
    original_base_url = settings.linkmind_base_url
    monkeypatch.setattr(settings, "embedding_api_url", "http://configured-bge:8889/v1/")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    path = tmp_path / "linkmind-default-route-reuse.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_base_url = "http://linkmind.example:8080"
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            timestamp = now()
            chat = ModelEndpoint(
                id="existing-chat",
                provider="local",
                adapter="openai_compatible",
                api_protocol="chat_completions",
                model=DEFAULT_CHAT_MODEL,
                capability="chat",
                endpoint_url="http://legacy-chat.example/v1",
                region="",
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
            embedding = ModelEndpoint(
                id="existing-embedding",
                provider="local",
                adapter="openai_compatible",
                api_protocol="chat_completions",
                model=DEFAULT_EMBEDDING_MODEL,
                capability="embedding",
                endpoint_url="http://legacy-embedding.example",
                region="",
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
            db.add_all([chat, embedding])
            db.flush()

            result = ensure_linkmind_default_endpoints(db)

            assert result == {
                "chat_endpoint_id": "existing-chat",
                "embedding_endpoint_id": "existing-embedding",
            }
            assert chat.endpoint_url == default_chat_url()
            assert chat.supports_vision is True
            assert embedding.endpoint_url == "http://configured-bge:8889/v1"
            assert db.query(ModelEndpoint).count() == 2
            by_model = {profile.model: profile for profile in chat.models}
            assert by_model[DEFAULT_CHAT_MODEL].supports_image_input is True
            assert all(
                not profile.supports_image_input
                for model, profile in by_model.items()
                if model != DEFAULT_CHAT_MODEL
            )
        from app.services.pgvector_store import resolve_embedding_config

        assert resolve_embedding_config()[:3] == (
            "http://configured-bge:8889/v1", "", DEFAULT_EMBEDDING_MODEL,
        )
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)


def test_linkmind_defaults_enable_vision_on_qwen_plus(tmp_path: Path):
    original_path = settings.db_path
    original_url = settings.database_url
    original_base_url = settings.linkmind_base_url
    path = tmp_path / "linkmind-default-vision.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.linkmind_base_url = "http://linkmind.example:8080"
    database.reset_database(path)
    database.initialize_database()
    try:
        with transaction() as db:
            result = ensure_linkmind_default_endpoints(db)
            chat = db.get(ModelEndpoint, result["chat_endpoint_id"])
            assert chat is not None
            assert chat.model == DEFAULT_CHAT_MODEL
            assert chat.supports_vision is True
            by_model = {profile.model: profile for profile in chat.models}
            assert by_model[DEFAULT_CHAT_MODEL].supports_image_input is True
            assert by_model["DeepSeek/deepseek-v4-flash"].supports_image_input is False
            assert by_model["DeepSeek/deepseek-v4-pro"].supports_image_input is False
            assert by_model["MiniMax/MiniMax-M3"].supports_image_input is False
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.linkmind_base_url = original_base_url
        database.reset_database(path)
