from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.services.pricing import calculate_cost, fill_usage_cost, format_cost
from app.services.usage_normalize import normalize_usage_buckets
from tests._auth import bootstrap_admin_response


def test_normalize_usage_buckets_are_mutually_exclusive():
    buckets = normalize_usage_buckets(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    )
    assert buckets["input_tokens"] == 1000
    assert buckets["cache_read_tokens"] == 800
    assert buckets["input_uncached_tokens"] == 200
    assert buckets["cache_write_tokens"] == 0
    assert buckets["output_tokens"] == 200


def test_calculate_cost_does_not_double_count_cache_tokens():
    amount = calculate_cost(
        input_tokens=1000,
        input_uncached_tokens=200,
        cache_read_tokens=800,
        cache_write_tokens=0,
        output_tokens=100,
        input_per_million="10",
        output_per_million="20",
        cache_read_per_million="1",
        cache_write_per_million="10",
    )
    # 200/1e6*10 + 800/1e6*1 + 100/1e6*20 = 0.002 + 0.0008 + 0.002 = 0.0048
    assert amount == Decimal("0.004800")
    assert format_cost(amount) == "0.0048"

    naive = calculate_cost(
        input_tokens=1000,
        output_tokens=100,
        input_per_million="10",
        output_per_million="20",
    )
    # Without exclusive buckets this would be 0.012; exclusive path must be lower.
    assert amount < naive


def test_admin_usage_summary_prices_historical_zero_cost_rows(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "pricing-summary.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            assert (
                client.post(
                    "/admin/model-prices",
                    headers=headers,
                    json={
                        "provider": "千问",
                        "model": "qwen3.7-plus",
                        "currency": "CNY",
                        "input_per_million": "2",
                        "output_per_million": "8",
                        "cache_read_per_million": "0.2",
                        "cache_write_per_million": "2",
                    },
                ).status_code
                == 201
            )

            from app.core.security import now
            from app.models.chat import ChatMessage, ChatSession

            admin_id = admin["user"]["id"]
            with transaction() as db:
                stamp = now()
                db.add(
                    ChatSession(
                        id="chs_price",
                        owner_user_id=admin_id,
                        title="定价",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.add(
                    ChatMessage(
                        id="chm_price",
                        session_id="chs_price",
                        owner_user_id=admin_id,
                        role="assistant",
                        content="ok",
                        usage=(
                            '{"provider":"agent_chat","model":"qwen3.7-plus",'
                            '"input_tokens":1000000,"cache_read_tokens":800000,'
                            '"input_uncached_tokens":200000,"output_tokens":500000,'
                            '"total_tokens":1500000,"cost":"0","latency_ms":10}'
                        ),
                        created_at=stamp,
                    )
                )

            summary = client.get("/admin/usage/summary", headers=headers)
            assert summary.status_code == 200
            payload = summary.json()
            by_user = {item["user_id"]: item for item in payload["items"]}
            # 0.2M*2 + 0.8M*0.2 + 0.5M*8 = 0.4 + 0.16 + 4 = 4.56
            assert by_user[admin_id]["cache_read_tokens"] == 800_000
            assert by_user[admin_id]["input_uncached_tokens"] == 200_000
            assert float(by_user[admin_id]["cost"]) == 4.56
            assert payload["total_cache_read_tokens"] == 800_000
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "pricing-summary.sqlite3")


def test_fill_usage_cost_matches_model_and_keeps_upstream_cost(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "pricing.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/admin/model-prices",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-test",
                    "currency": "CNY",
                    "input_per_million": "1.00",
                    "output_per_million": "2.00",
                    "cache_read_per_million": "0.10",
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["cache_read_per_million"] == "0.10"
            assert body["cache_write_per_million"] == "1.00"

            with transaction() as db:
                priced = fill_usage_cost(
                    db,
                    {
                        "provider": "agent_chat",
                        "model": "gpt-test",
                        "input_tokens": 1_000_000,
                        "output_tokens": 1_000_000,
                        "total_tokens": 2_000_000,
                        "cost": "0",
                        "latency_ms": 10,
                    },
                )
                assert priced is not None
                assert priced["cost"] == "3"
                assert priced["currency"] == "CNY"
                assert priced["price_version_id"].startswith("prc_")

                kept = fill_usage_cost(
                    db,
                    {
                        "provider": "openai",
                        "model": "gpt-test",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "cost": "9.99",
                        "latency_ms": 10,
                    },
                )
                assert kept is not None
                assert kept["cost"] == "9.99"
                assert kept["currency"] == "CNY"
                assert kept["price_version_id"] == body["id"]
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "pricing.sqlite3")
