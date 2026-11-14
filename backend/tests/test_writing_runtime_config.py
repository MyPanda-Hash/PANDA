from pathlib import Path
from typing import Any, cast

from app.core.config import Settings
from app.services.writing_runtime_config import (
    resolve_pi_agent_id,
    resolve_writing_provider,
)

ROOT = Path(__file__).parents[2]


def test_writing_provider_defaults_to_pi_chat(monkeypatch):
    monkeypatch.delenv("DOC_NEXUS_WRITING_PROVIDER", raising=False)
    assert cast(Any, Settings)(_env_file=None).writing_provider == "pi_chat"


def test_pi_agent_timeout_defaults_to_fifteen_minutes(monkeypatch):
    monkeypatch.delenv("DOC_NEXUS_PI_AGENT_TIMEOUT_SECONDS", raising=False)
    assert cast(Any, Settings)(_env_file=None).pi_agent_timeout_seconds == 900.0


def test_writing_provider_only_allows_pi_chat(tmp_path, monkeypatch):
    from app.core import database
    from app.core.config import settings
    from app.core.database import transaction
    from app.repositories import system_config

    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "writing-provider.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    try:
        database.reset_database(settings.db_path)
        database.initialize_database()
        with transaction() as db:
            system_config.upsert(db, "writing_provider", "legacy", "legacy")
        assert resolve_writing_provider() == "pi_chat"
        with transaction() as db:
            system_config.upsert(db, "pi_agent_id", "pi-from-ui", "pi agent")
        assert resolve_pi_agent_id() == "pi-from-ui"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "writing-provider.sqlite3")


def test_compose_starts_writing_dispatcher_without_optional_profile():
    compose = (ROOT / "docker-compose.yml").read_text()
    service = compose.split("  writing-dispatcher:", 1)[1].split("  frontend:", 1)[0]
    assert "profiles:" not in service


def test_start_local_supervises_writing_dispatcher():
    script = (ROOT / "scripts/start-local.sh").read_text()
    assert "run_writing_dispatcher.py" in script
    assert 'dispatcher_pid=""' in script
    assert 'stop_process_tree "$dispatcher_pid"' in script


def test_compose_starts_only_backend_pi_dispatcher():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "writing-dispatcher:" in compose
    assert "external team" not in compose


def test_default_compose_pins_sqlite_and_shares_data_volume():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "sqlite:////data/db/doc_nexus.sqlite3" in compose
    assert "${DOC_NEXUS_DATA_DIR:-./data/db}:/data/db" in compose
    assert "image: pgvector/pgvector" not in compose

def test_compose_exposes_personal_memory_toggle():
    expected = "DOC_NEXUS_PERSONAL_MEMORY_ENABLED: ${DOC_NEXUS_PERSONAL_MEMORY_ENABLED:-0}"
    for name in ("docker-compose.yml", "docker-compose.local.yml"):
        assert expected in (ROOT / name).read_text()


def test_postgres_overlay_enables_pgvector():
    overlay = (ROOT / "docker-compose.postgres.yml").read_text()
    assert "image: pgvector/pgvector:pg16" in overlay
    assert "@postgres:5432" in overlay
    assert "container_name: doc-nexus-postgres" in overlay


def test_local_compose_pins_database_to_sqlite():
    compose = (ROOT / "docker-compose.local.yml").read_text()
    assert "sqlite:////data/db/doc_nexus.sqlite3" in compose
    # SQLite lives in a named volume: a bind mount gives the host and the containers
    # separate POSIX lock namespaces over the same file, which corrupts the WAL.
    assert compose.count("- doc-nexus-db:/data/db") == 3
    assert "name: ${DOC_NEXUS_DB_VOLUME:-doc-nexus-db}" in compose
    assert ":/data/db" not in compose.replace("- doc-nexus-db:/data/db", "")
    assert "@postgres:5432" not in compose
    assert "doc-nexus-postgres-local" not in compose
