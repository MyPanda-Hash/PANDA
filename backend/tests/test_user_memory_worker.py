"""Privacy, revocation and billing boundaries through a controlled HTTP upstream."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core import database
from app.core.config import settings
from app.core.security import now
from app.models import Base
from app.models.auth import User
from app.models.governance import BudgetReservation, BudgetRule, ModelPrice
from app.models.user_memory import (
    UserMemoryEntry,
    UserMemoryJob,
    UserMemoryOperation,
    UserMemoryTurn,
)
from app.repositories import usage, user_memory as repo
from app.services import user_memory_dispatcher as worker
from app.services import user_memory_extractor as extractor
from app.services.pi_local import LocalPiEndpoint


@pytest.fixture
def worker_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker.sqlite'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def sqlite_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "personal_memory_enabled", True)
    monkeypatch.setattr(settings, "personal_memory_max_attempts", 3)
    with sessions.begin() as db:
        for owner in ("worker-owner", "other-owner"):
            db.add(
                User(
                    id=owner,
                    username=owner,
                    display_name="同名同事",
                    password_hash="unused",
                    status="active",
                    created_at=now(),
                )
            )
        db.add(
            ModelPrice(
                id="worker-price",
                provider="openai",
                model="controlled-memory-model",
                currency="CNY",
                input_per_million="1",
                output_per_million="2",
                effective_at=now(),
                created_at=now(),
            )
        )
    try:
        yield sessions
    finally:
        engine.dispose()


@pytest.fixture
def upstream(monkeypatch):
    control = {"requests": [], "status": 200, "callback": None, "candidates": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            control["requests"].append(payload)
            callback = control["callback"]
            if callback is not None:
                callback()
            candidates = control["candidates"]
            if candidates is None:
                candidates = [
                    {
                        "key": "profile.self_reported_name",
                        "value": "李梅",
                        "quote": "我叫李梅",
                        "quote_start": 0,
                        "quote_end": 4,
                        "self_attribution": "self",
                        "temporality": "persistent",
                        "explicitness": "explicit",
                        "conflict_note": "",
                    }
                ]
            body = {
                "model": "controlled-memory-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"candidates": candidates}, ensure_ascii=False
                            )
                        }
                    }
                ],
            }
            if control["status"] == 200:
                body["usage"] = {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                }
            encoded = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(control["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = LocalPiEndpoint(
        endpoint_id="controlled-endpoint",
        provider="openai",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        model="controlled-memory-model",
        api_key="",
        allow_anonymous_api_key=True,
    )
    monkeypatch.setattr(
        extractor, "resolve_local_pi_endpoint", lambda **_kwargs: endpoint
    )
    try:
        yield control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def enqueue(sessions, *, mode="suggest", owner="worker-owner", text="我叫李梅"):
    with sessions.begin() as db:
        state = repo.get_or_create_state(db, owner)
        state.learning_mode = mode
        state.automatic_source_cutoff_at = now() - timedelta(seconds=1)
        turn = repo.register_turn(
            db,
            owner_user_id=owner,
            channel="open",
            request_key="source-one",
            runtime_session_id=f"session-{owner}",
            raw_text=text,
            source_text_safe=text,
            source_text_retention=timedelta(days=7),
        )
        job = repo.enqueue_extraction_job(
            db,
            owner_user_id=owner,
            turn_id=turn.id,
            source_hash_value=turn.source_hash,
            extractor_version="v1",
            epoch=turn.epoch,
        )
        assert job is not None
        repo.close_turn(db, owner_user_id=owner, turn_id=turn.id)
        return job.id, turn.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("suggest", "candidate"), ("auto_low_risk", "active")]
)
async def test_closed_source_respects_learning_policy_and_owner(
    worker_db, upstream, mode, expected
):
    job_id, turn_id = enqueue(worker_db, mode=mode)
    enqueue(worker_db, owner="other-owner", text="他人的私有原文不得拼入")
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    await dispatcher.close()
    with worker_db() as db:
        entry = db.scalar(
            select(UserMemoryEntry).where(
                UserMemoryEntry.owner_user_id == "worker-owner"
            )
        )
        assert entry.status == expected
        assert json.loads(entry.value_json) == "李梅"
        assert db.get(UserMemoryJob, job_id).status == "succeeded"
        assert db.get(UserMemoryTurn, turn_id).source_text_safe is None
        assert (
            db.scalar(
                select(UserMemoryEntry).where(
                    UserMemoryEntry.owner_user_id == "other-owner"
                )
            )
            is None
        )
        summary = usage.summarize_usage_by_user(db, feature="memory")
        assert [(item.model, item.total_tokens) for item in summary.by_model] == [
            ("controlled-memory-model", 30)
        ]
    source = json.loads(upstream["requests"][0]["messages"][-1]["content"])
    assert source == {"source_text": "我叫李梅"}


@pytest.mark.asyncio
async def test_revocation_during_http_discards_memory_but_keeps_bill(
    worker_db, upstream
):
    job_id, _ = enqueue(worker_db, mode="auto_low_risk")
    with worker_db.begin() as db:
        db.add(
            BudgetRule(
                id="budget",
                scope="user",
                cost_limit="10",
                soft_threshold=0.8,
                hard_stop=True,
                created_at=now(),
                updated_at=now(),
            )
        )

    def revoke():
        with worker_db.begin() as db:
            state = repo.get_or_create_state(db, "worker-owner")
            repo.clear_entries(
                db, owner_user_id="worker-owner", expected_revision=state.revision
            )

    upstream["callback"] = revoke
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    await dispatcher.close()
    with worker_db() as db:
        assert db.scalar(select(UserMemoryEntry)) is None
        assert db.get(UserMemoryJob, job_id).status == "cancelled"
        operation = db.scalar(select(UserMemoryOperation))
        assert operation.status == "failed"
        assert json.loads(operation.usage_json)["total_tokens"] == 30
        summary = usage.summarize_usage_by_user(db, feature="memory")
        assert summary.total_tokens == 30
        assert float(summary.total_cost) > 0
        reservation = db.scalar(select(BudgetReservation))
        assert reservation.status == "accounted"
        assert reservation.held_cost == "0"


@pytest.mark.asyncio
async def test_temporary_retry_and_permanent_rejection_preserve_unknown_cost(
    worker_db, upstream
):
    job_id, _ = enqueue(worker_db)
    upstream["status"] = 503
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    with worker_db.begin() as db:
        job = db.get(UserMemoryJob, job_id)
        assert job.status == "pending"
        job.next_attempt_at = now() - timedelta(seconds=1)
    upstream["status"] = 400
    assert await dispatcher.run_once()
    assert not await dispatcher.run_once()
    await dispatcher.close()
    with worker_db() as db:
        assert db.get(UserMemoryJob, job_id).status == "failed"
        operations = list(
            db.scalars(
                select(UserMemoryOperation).order_by(UserMemoryOperation.created_at)
            )
        )
        assert len(operations) == 2
        first, second = [json.loads(item.usage_json) for item in operations]
        assert first["cost_status"] == "reserved_unknown"
        assert float(first["cost"]) > 0
        assert second["cost_status"] == "not_billed"
        assert second["cost"] == "0"
        summary = usage.summarize_usage_by_user(db, feature="memory")
        assert summary.by_model == []
        assert float(summary.total_cost) > 0
        assert db.scalar(select(UserMemoryEntry)) is None


@pytest.mark.asyncio
async def test_shutdown_blocks_late_result_and_new_claims(
    worker_db, upstream, monkeypatch
):
    job_id, _ = enqueue(worker_db, mode="auto_low_risk")
    entered = threading.Event()
    release = threading.Event()

    def hold_response():
        entered.set()
        assert release.wait(timeout=5)

    upstream["callback"] = hold_response
    monkeypatch.setattr(settings, "personal_memory_shutdown_grace_seconds", 0.01)
    dispatcher = worker.UserMemoryDispatcher()
    running = asyncio.create_task(dispatcher.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await dispatcher.close()
    finally:
        release.set()
        await running
    assert not await dispatcher.run_once()
    with worker_db() as db:
        assert db.scalar(select(UserMemoryEntry)) is None
        assert db.get(UserMemoryJob, job_id).status == "cancelled"
        assert (
            json.loads(db.scalar(select(UserMemoryOperation)).usage_json)[
                "total_tokens"
            ]
            == 30
        )


@pytest.mark.asyncio
async def test_sensitive_input_never_reaches_provider_or_model_usage(
    worker_db, upstream
):
    job_id, turn_id = enqueue(
        worker_db, text="我的API key是 sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    await dispatcher.close()
    assert upstream["requests"] == []
    with worker_db() as db:
        assert db.get(UserMemoryJob, job_id).status == "failed"
        assert db.get(UserMemoryTurn, turn_id).source_text_safe is None
        assert db.scalar(select(UserMemoryEntry)) is None
        operation = db.scalar(select(UserMemoryOperation))
        assert operation.error_code == "memory_sensitive_source"
        assert operation.usage_json == "{}"
        assert usage.summarize_usage_by_user(db, feature="memory").by_model == []


@pytest.mark.asyncio
async def test_invalid_evidence_cannot_save_but_consumes_reported_tokens(
    worker_db, upstream
):
    job_id, _ = enqueue(worker_db, mode="auto_low_risk")
    upstream["candidates"] = [
        {
            "key": "profile.self_reported_name",
            "value": "王芳",
            "quote": "我叫王芳",
            "quote_start": 0,
            "quote_end": 4,
            "self_attribution": "self",
            "temporality": "persistent",
            "explicitness": "explicit",
            "conflict_note": "",
        }
    ]
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    await dispatcher.close()
    with worker_db() as db:
        assert db.get(UserMemoryJob, job_id).status == "failed"
        assert db.scalar(select(UserMemoryEntry)) is None
        operation = db.scalar(select(UserMemoryOperation))
        assert operation.error_code == "memory_evidence_invalid"
        assert json.loads(operation.usage_json)["total_tokens"] == 30


@pytest.mark.asyncio
async def test_hard_budget_refusal_prevents_transmission(worker_db, upstream):
    job_id, _ = enqueue(worker_db)
    with worker_db.begin() as db:
        db.add(
            BudgetRule(
                id="empty-budget",
                scope="user",
                cost_limit="0",
                soft_threshold=0.8,
                hard_stop=True,
                created_at=now(),
                updated_at=now(),
            )
        )
    dispatcher = worker.UserMemoryDispatcher()
    assert await dispatcher.run_once()
    await dispatcher.close()
    assert upstream["requests"] == []
    with worker_db() as db:
        assert db.get(UserMemoryJob, job_id).status == "failed"
        operation = db.scalar(select(UserMemoryOperation))
        assert operation.error_code == "memory_budget_exceeded"
        assert operation.usage_json == "{}"
        assert usage.summarize_usage_by_user(db, feature="memory").by_model == []
