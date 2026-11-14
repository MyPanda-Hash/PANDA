from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.models.execution_admission import (
    PiExecutionAdmissionGuard,
    PiExecutionAdmissionLease,
)
from app.services.pi_runtime.admission import (
    DatabasePiAdmissionController,
    PiAdmissionCancelled,
    PiAdmissionLimits,
    PiAdmissionQueueFull,
    PiAdmissionQueueTimeout,
)
from app.services.pi_runtime.config import PiRuntimeSettings, ResolvedPiModel
from app.services.pi_runtime.events import pi_event_to_sse_item
from app.services.pi_runtime.runner import PiRpcError, PiRpcRunner


def _limits(
    *, total: int = 1, per_user: int = 1, waiters: int = 1, timeout: float = 1
) -> PiAdmissionLimits:
    return PiAdmissionLimits(
        max_concurrency=total,
        max_per_user=per_user,
        max_waiters=waiters,
        queue_timeout_seconds=timeout,
    )


@pytest.fixture
def admission_database(tmp_path, monkeypatch):
    configured = os.getenv("DOC_NEXUS_TEST_ADMISSION_DATABASE_URL", "").strip()
    db_path = tmp_path / "pi-admission.sqlite3"
    database.reset_database(db_path)
    monkeypatch.setattr(
        settings,
        "database_url",
        configured or f"sqlite:///{db_path}",
    )
    monkeypatch.setattr(settings, "db_path", db_path)
    database.initialize_database()
    with transaction() as db:
        db.execute(delete(PiExecutionAdmissionLease))
        db.execute(
            update(PiExecutionAdmissionGuard)
            .where(PiExecutionAdmissionGuard.id == 1)
            .values(revision=0)
        )
    yield settings.database_url
    with transaction() as db:
        db.execute(delete(PiExecutionAdmissionLease))
    database.reset_database(db_path)


@pytest.mark.asyncio
async def test_waiting_admission_is_cancellable_and_queue_full_is_stable(
    admission_database,
):
    controller = DatabasePiAdmissionController(_limits(waiters=1, timeout=2))
    first = await controller.acquire(user_id="user-a")
    cancellation = asyncio.Event()
    waiting = asyncio.Event()

    async def acquire_waiter():
        return await controller.acquire(
            user_id="user-a",
            cancel_check=cancellation.is_set,
            on_waiting=lambda _position: waiting.set(),
        )

    second = asyncio.create_task(acquire_waiter())
    await asyncio.wait_for(waiting.wait(), timeout=1)
    with pytest.raises(PiAdmissionQueueFull) as caught:
        await controller.acquire(user_id="user-b")
    assert caught.value.code == "pi_execution_queue_full"

    cancellation.set()
    with pytest.raises(PiAdmissionCancelled):
        await asyncio.wait_for(second, timeout=1)
    await first.release()

    replacement = await controller.acquire(user_id="user-b")
    await replacement.release()
    with transaction() as db:
        assert db.scalar(select(func.count(PiExecutionAdmissionLease.id))) == 0

@pytest.mark.asyncio
async def test_wait_timeout_removes_ticket_and_does_not_leak_permit(
    admission_database,
):
    controller = DatabasePiAdmissionController(_limits(timeout=0.15))
    first = await controller.acquire(user_id="user-a")
    with pytest.raises(PiAdmissionQueueTimeout) as caught:
        await controller.acquire(user_id="user-b")
    assert caught.value.code == "pi_execution_queue_timeout"
    await first.release()

    replacement = await controller.acquire(user_id="user-b")
    await replacement.release()


@pytest.mark.asyncio
async def test_runner_queue_full_uses_stable_non_retrying_error(
    admission_database, tmp_path
):
    controller = DatabasePiAdmissionController(_limits(waiters=0))
    occupied = await controller.acquire(user_id="user-a")
    runner = PiRpcRunner(
        PiRuntimeSettings(
            executable="pi",
            root=tmp_path / "runtime",
            max_stderr_chars=1024,
            default_timeout_sec=60,
        ),
        admission=controller,
    )
    with pytest.raises(PiRpcError) as caught:
        await runner.run(
            agent=SimpleNamespace(id="agent", tenant_id="tenant"),
            resolved_model=ResolvedPiModel(
                credential_id="local",
                provider_name="provider",
                model="model",
                api_key="secret",
                base_url="http://model.test/v1",
            ),
            session_id="session",
            prompt="prompt",
            admission_user_id="user-b",
        )
    assert caught.value.code == "pi_execution_queue_full"
    assert caught.value.max_attempts == 1
    await occupied.release()


@pytest.mark.asyncio
async def test_runner_exception_releases_admission_and_emits_running(
    admission_database, tmp_path, monkeypatch
):
    controller = DatabasePiAdmissionController(_limits())
    runner = PiRpcRunner(
        PiRuntimeSettings(
            executable="pi",
            root=tmp_path / "runtime",
            max_stderr_chars=1024,
            default_timeout_sec=60,
        ),
        admission=controller,
    )

    async def fail(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_locked", fail)
    events: list[dict[str, object]] = []
    agent = SimpleNamespace(id="agent", tenant_id="tenant")
    model = ResolvedPiModel(
        credential_id="local",
        provider_name="provider",
        model="model",
        api_key="secret",
        base_url="http://model.test/v1",
    )
    with pytest.raises(RuntimeError, match="boom"):
        await runner.run(
            agent=agent,
            resolved_model=model,
            session_id="session",
            prompt="prompt",
            admission_user_id="user-a",
            on_event=events.append,
        )
    assert events == [
        {
            "type": "runtime",
            "event": "execution_queue",
            "status": "running",
        }
    ]

    replacement = await controller.acquire(user_id="user-b")
    await replacement.release()


def test_queue_runtime_event_uses_existing_sse_channel():
    item = pi_event_to_sse_item(
        {
            "type": "runtime",
            "event": "execution_queue",
            "status": "waiting",
            "position": 2,
        },
        agent_name="doc-nexus-assistant",
    )
    assert item == {
        "type": "PiRuntimeEvent",
        "source": "doc-nexus-assistant",
        "content": {
            "type": "runtime",
            "event": "execution_queue",
            "status": "waiting",
            "position": 2,
        },
    }


def _process_admission_contender(
    database_url: str,
    user_id: str,
    start,
    results,
) -> None:
    settings.database_url = database_url
    database.reset_database(Path("/tmp/nonexistent-pi-admission-reset"))

    async def contend() -> None:
        controller = DatabasePiAdmissionController(
            _limits(total=2, per_user=1, waiters=8, timeout=5)
        )
        await asyncio.to_thread(start.wait)
        permit = await controller.acquire(user_id=user_id)
        acquired = time.monotonic()
        await asyncio.sleep(0.4)
        released = time.monotonic()
        await permit.release()
        results.put((user_id, acquired, released, ""))

    try:
        asyncio.run(contend())
    except BaseException as exc:
        results.put((user_id, 0.0, 0.0, type(exc).__name__))


def _peak_overlap(intervals: list[tuple[float, float]]) -> int:
    points = sorted(
        [(start, 1) for start, _ in intervals]
        + [(end, -1) for _, end in intervals],
        key=lambda item: (item[0], item[1]),
    )
    active = 0
    peak = 0
    for _, delta in points:
        active += delta
        peak = max(peak, active)
    return peak


def test_cross_process_database_leases_enforce_total_and_per_user(
    admission_database,
):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    users = ["user-a", "user-a", "user-b", "user-b"]
    processes = [
        context.Process(
            target=_process_admission_contender,
            args=(admission_database, user_id, start, results),
        )
        for user_id in users
    ]
    for process in processes:
        process.start()
    start.set()
    observed = []
    try:
        for _ in processes:
            observed.append(results.get(timeout=15))
    except Empty as exc:
        raise AssertionError("admission contender did not finish") from exc
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)

    assert [error for *_rest, error in observed if error] == []
    intervals = [(started, ended) for _, started, ended, _ in observed]
    assert _peak_overlap(intervals) == 2
    assert _peak_overlap(
        [(started, ended) for user, started, ended, _ in observed if user == "user-a"]
    ) == 1
    assert _peak_overlap(
        [(started, ended) for user, started, ended, _ in observed if user == "user-b"]
    ) == 1
