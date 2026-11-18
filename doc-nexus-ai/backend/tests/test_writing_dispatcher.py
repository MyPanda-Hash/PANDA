from __future__ import annotations

import importlib
import sqlite3
import threading

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core import database
from app.core.config import settings
from app.services.writing_dispatcher import WritingDispatcher
from app.services.execution_resources import WorkerExecutionResources


def test_worker_startup_does_not_race_initial_migrations(monkeypatch, tmp_path):
    """Worker 抢先启动不能建出缺少 Alembic 版本的库，阻断 API 初始化。"""
    worker = importlib.import_module("run_writing_dispatcher")
    db_path = tmp_path / "startup.sqlite3"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(worker.signal, "signal", lambda _signal, handler: handler())
    try:
        worker.main()
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (ScriptDirectory.from_config(config).get_current_head(),)
            ]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        database._get_engine().dispose()


def test_pi_dispatch_lease_covers_multi_chapter_runs(monkeypatch):
    monkeypatch.setattr(settings, "pi_writing_timeout_seconds", 180.0)

    dispatcher = WritingDispatcher(lease_seconds=60)

    assert dispatcher.effective_lease_seconds() == 210
    assert dispatcher.effective_lease_seconds(chapter_count=3) == 570


@pytest.mark.asyncio
async def test_document_worker_uses_dedicated_pool_not_api_default_executor():
    resources = WorkerExecutionResources("documents")
    try:
        thread_name = await resources.run_document(
            lambda: threading.current_thread().name
        )
        assert thread_name.startswith("doc-worker")
        with pytest.raises(RuntimeError, match="background worker executor"):
            await resources.run_background(lambda: None)
    finally:
        resources.close()
