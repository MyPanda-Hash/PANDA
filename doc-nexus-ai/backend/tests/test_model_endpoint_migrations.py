from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.model_endpoints import ModelEndpoint
from app.models.system_config import SystemConfig


ROOT = Path(__file__).parents[2]


def test_sort_order_migration_preserves_configured_chat_default(tmp_path: Path):
    database_path = tmp_path / "model-endpoints.sqlite3"
    original_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    timestamp = datetime.now(timezone.utc)
    try:
        command.upgrade(config, "20260829_0062")
        engine = create_engine(settings.database_url)
        with Session(engine) as db:
            db.add_all(
                [
                    ModelEndpoint(
                        id="configured-chat",
                        provider="zeta",
                        model="zeta-chat",
                        capability="chat",
                        endpoint_url="https://zeta.example/v1",
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                    ModelEndpoint(
                        id="other-chat",
                        provider="alpha",
                        model="alpha-chat",
                        capability="chat",
                        endpoint_url="https://alpha.example/v1",
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                    SystemConfig(
                        key="pi_chat_model",
                        value="configured-chat",
                        description="chat route",
                        updated_at=timestamp,
                    ),
                ]
            )
            db.commit()
        engine.dispose()

        command.upgrade(config, "head")

        engine = create_engine(settings.database_url)
        with Session(engine) as db:
            ordered_ids = list(
                db.scalars(select(ModelEndpoint.id).order_by(ModelEndpoint.sort_order))
            )
            assert ordered_ids == ["configured-chat", "other-chat"]
            assert db.get(ModelEndpoint, "configured-chat").sort_order == 0
            assert db.get(ModelEndpoint, "other-chat").sort_order == 10
        assert "ix_model_endpoints_sort_order" in {
            item["name"] for item in inspect(engine).get_indexes("model_endpoints")
        }
        engine.dispose()
    finally:
        settings.database_url = original_database_url


def test_model_profiles_migration_backfills_without_changing_legacy_columns(
    tmp_path: Path,
):
    database_path = tmp_path / "model-profiles.sqlite3"
    original_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    timestamp = datetime.now(timezone.utc)
    try:
        command.upgrade(config, "20260830_0069")
        engine = create_engine(settings.database_url)
        with Session(engine) as db:
            db.add(
                ModelEndpoint(
                    id="legacy-chat",
                    provider="openai-compatible",
                    model="qwen3.7-plus",
                    extra_models="deepseek-v4-flash, qwen3-vl",
                    capability="chat",
                    endpoint_url="https://models.example/v1",
                    status="active",
                    max_context=196_608,
                    supports_streaming_usage=True,
                    supports_structured_output=True,
                    supports_vision=True,
                    supports_cache_pricing=True,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            db.commit()
        engine.dispose()

        command.upgrade(config, "head")

        engine = create_engine(settings.database_url)
        with Session(engine) as db:
            profiles = list(
                db.execute(
                    text(
                        """
                        SELECT model, is_default, sort_order, max_context,
                               supports_image_input, supports_structured_output,
                               supports_streaming_usage, supports_cache_pricing
                        FROM model_endpoint_models
                        WHERE endpoint_id = :endpoint_id
                        ORDER BY sort_order
                        """
                    ),
                    {"endpoint_id": "legacy-chat"},
                ).mappings()
            )
            legacy = db.execute(
                text(
                    """
                    SELECT model, extra_models, supports_vision
                    FROM model_endpoints
                    WHERE id = :endpoint_id
                    """
                ),
                {"endpoint_id": "legacy-chat"},
            ).mappings().one()

        assert [profile["model"] for profile in profiles] == [
            "qwen3.7-plus",
            "deepseek-v4-flash",
            "qwen3-vl",
        ]
        assert bool(profiles[0]["is_default"]) is True
        assert bool(profiles[0]["supports_image_input"]) is True
        assert all(profile["max_context"] == 196_608 for profile in profiles)
        assert all(profile["supports_structured_output"] for profile in profiles)
        assert all(profile["supports_streaming_usage"] for profile in profiles)
        assert all(profile["supports_cache_pricing"] for profile in profiles)
        assert all(
            not profile["supports_image_input"] for profile in profiles[1:]
        )
        assert legacy == {
            "model": "qwen3.7-plus",
            "extra_models": "deepseek-v4-flash, qwen3-vl",
            "supports_vision": 1,
        }
        assert "model_endpoint_models" in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        settings.database_url = original_database_url
