from contextlib import contextmanager

from app.core.config import settings
from app.services import chat_agent_config


def test_load_chat_agent_config_reuses_shared_platform_values(monkeypatch):
    reads = 0
    db = object()
    values = {
        "agent_chat_base_url": "http://platform.db/",
        "agent_chat_api_key": "db-key",
        "agent_chat_agent_id": "agent-db",
    }

    @contextmanager
    def fake_transaction():
        yield db

    def get_all(received_db):
        nonlocal reads
        assert received_db is db
        reads += 1
        return values

    def fail_resolve(key, default=""):
        raise AssertionError("configuration must be loaded in one snapshot read")

    monkeypatch.setattr(
        chat_agent_config, "transaction", fake_transaction, raising=False
    )
    monkeypatch.setattr(chat_agent_config.system_config, "get_all", get_all)
    monkeypatch.setattr(chat_agent_config.system_config, "resolve", fail_resolve)

    config = chat_agent_config.load_chat_agent_config()

    assert config.base_url == "http://platform.db"
    assert config.api_key == "db-key"
    assert config.agent_id == "agent-db"
    assert config.configured is True
    assert reads == 1


def test_load_chat_agent_config_falls_back_to_application_settings(monkeypatch):
    @contextmanager
    def fake_transaction():
        yield object()

    monkeypatch.setattr(
        chat_agent_config, "transaction", fake_transaction, raising=False
    )
    monkeypatch.setattr(chat_agent_config.system_config, "get_all", lambda db: {})
    monkeypatch.setattr(settings, "agent_chat_base_url", "http://platform.env/")
    monkeypatch.setattr(settings, "agent_chat_api_key", "env-key")
    monkeypatch.setattr(settings, "agent_chat_agent_id", "agent-env")

    config = chat_agent_config.load_chat_agent_config()

    assert config.base_url == "http://platform.env"
    assert config.api_key == "env-key"
    assert config.agent_id == "agent-env"


def test_chat_agent_config_requires_all_three_values():
    config = chat_agent_config.ChatAgentConfig(
        base_url="http://platform",
        api_key="",
        agent_id="agent-1",
    )

    assert config.configured is False


def test_load_chat_welcome_suggestions_uses_stored_lines(monkeypatch):
    monkeypatch.setattr(
        chat_agent_config.system_config,
        "resolve",
        lambda key, default="": "自定义问题一\n\n自定义问题二\n",
    )

    assert chat_agent_config.load_chat_welcome_suggestions() == [
        "自定义问题一",
        "自定义问题二",
    ]


def test_load_chat_welcome_suggestions_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(
        chat_agent_config.system_config,
        "resolve",
        lambda key, default="": default,
    )

    assert chat_agent_config.load_chat_welcome_suggestions() == []
    assert chat_agent_config.DEFAULT_CHAT_WELCOME_SUGGESTIONS == ()


def test_load_chat_welcome_suggestions_allows_empty_config(monkeypatch):
    monkeypatch.setattr(
        chat_agent_config.system_config,
        "resolve",
        lambda key, default="": "",
    )

    assert chat_agent_config.load_chat_welcome_suggestions() == []
