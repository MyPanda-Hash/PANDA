from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.assistant_watch import AssistantEvent, WatchScanRun
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.services import assistant_reports


@pytest.fixture
def report_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "assistant-reports.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        db.add(
            User(
                id="usr_admin",
                username=settings.bootstrap_username,
                email="admin@example.com",
                display_name="管理员",
                password_hash="unused",
                role="admin",
                status="active",
                first_login=False,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _event(db: Session, *, event_id: str, kind: str, status: str, hours_ago: float,
           severity: str = "warning", targets: list[str] | None = None,
           acked: bool = False, resolved_hours_ago: float | None = None) -> AssistantEvent:
    stamp = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    event = AssistantEvent(
        id=event_id,
        dedupe_key=f"k-{event_id}",
        kind=kind,
        title=f"{kind} 事件 {event_id}",
        severity=severity,
        status=status,
        target_usernames_json=json.dumps(targets or ["planner"]),
        target_user_ids_json="[]",
        payload_json="{}",
        acked_by_user_id="usr_admin" if acked else None,
        created_at=stamp,
        updated_at=stamp + timedelta(hours=1) if acked else stamp,
        first_seen_at=stamp,
        resolved_at=(stamp + timedelta(hours=resolved_hours_ago))
        if resolved_hours_ago is not None
        else None,
    )
    db.add(event)
    db.flush()
    return event


def test_daily_report_counts_created_and_open(report_db: Session):
    _event(report_db, event_id="e1", kind="so_unstarted", status="open", hours_ago=2)
    _event(report_db, event_id="e2", kind="stock_low", status="open", hours_ago=3)
    _event(report_db, event_id="e3", kind="pu_draft", status="open", hours_ago=40)
    report_db.commit()

    ctx = assistant_reports.build_context(report_db, kind="daily")
    assert ctx.created == 2, "日报窗口是最近 24 小时"
    assert ctx.open_now == 3, "积压按全部未处理算"


def test_report_renders_numbers_and_sections(report_db: Session):
    _event(report_db, event_id="e1", kind="so_unstarted", status="open", hours_ago=2)
    _event(report_db, event_id="e2", kind="stock_low", status="resolved", hours_ago=5,
           resolved_hours_ago=1)
    report_db.commit()

    result = assistant_reports.generate_report(report_db, kind="daily", deliver=True)
    content = result["content"]
    assert "## MES 日报" in content
    assert "### 概览" in content
    assert "新发现 **2** 条" in content
    assert "### 新增分布" in content
    assert "销售订单未排产" in content
    assert "### 重点关注" in content


def test_report_is_delivered_to_admin_personal_chat(report_db: Session):
    _event(report_db, event_id="e1", kind="so_unstarted", status="open", hours_ago=1)
    report_db.commit()

    result = assistant_reports.generate_report(report_db, kind="daily")

    assert result["session_id"]
    session = report_db.scalar(select(ChatSession))
    assert session.owner_user_id == "usr_admin"
    assert "日报" in session.title

    messages = list(report_db.scalars(select(ChatMessage)))
    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert "### 概览" in messages[0].content


def test_same_day_report_reuses_session(report_db: Session):
    _event(report_db, event_id="e1", kind="so_unstarted", status="open", hours_ago=1)
    report_db.commit()

    assistant_reports.generate_report(report_db, kind="daily")
    assistant_reports.generate_report(report_db, kind="daily")

    sessions = list(report_db.scalars(select(ChatSession)))
    assert len(sessions) == 1, "同一天不该建多个会话"
    messages = list(report_db.scalars(select(ChatMessage)))
    assert len(messages) == 2, "但消息应该追加两条"


def test_weekly_and_monthly_periods(report_db: Session):
    anchor = date(2026, 11, 12)  # 周四
    weekly = assistant_reports.build_context(report_db, kind="weekly", anchor=anchor)
    assert weekly.period_label == "周报"
    # 周报覆盖上一整周（周一起）
    start = weekly.period_start.astimezone()
    assert start.weekday() == 0

    monthly = assistant_reports.build_context(report_db, kind="monthly", anchor=anchor)
    assert monthly.period_label == "月报"
    assert monthly.period_start.astimezone().day == 1


def test_health_numbers_appear_in_report(report_db: Session):
    stamp = datetime.now(timezone.utc) - timedelta(hours=1)
    report_db.add_all([
        WatchScanRun(id="w1", trigger="timer", status="ok", started_at=stamp,
                     finished_at=stamp, duration_ms=120),
        WatchScanRun(id="w2", trigger="timer", status="failed", started_at=stamp,
                     finished_at=stamp, error="boom"),
    ])
    report_db.commit()

    ctx = assistant_reports.build_context(report_db, kind="daily")
    assert ctx.scans == 2
    assert ctx.scan_failures == 1
    assert ctx.avg_scan_ms == 120

    content = assistant_reports.generate_report(report_db, kind="daily")["content"]
    assert "失败 1 次" in content
    assert "平均 120 ms" in content


def test_empty_system_reports_cleanly(report_db: Session):
    ctx = assistant_reports.build_context(report_db, kind="daily")
    assert ctx.created == 0
    assert ctx.open_now == 0
    content = assistant_reports.generate_report(report_db, kind="daily")["content"]
    assert "没有待处理事项" in content


def test_invalid_kind_raises(report_db: Session):
    with pytest.raises(ValueError):
        assistant_reports.generate_report(report_db, kind="yearly")


def test_deliver_false_skips_chat(report_db: Session):
    _event(report_db, event_id="e1", kind="so_unstarted", status="open", hours_ago=1)
    report_db.commit()
    result = assistant_reports.generate_report(report_db, kind="daily", deliver=False)
    assert result["session_id"] is None
    assert list(report_db.scalars(select(ChatSession))) == []


def test_report_schedule_due_rules():
    """日报每天、周报周一、月报 1 号；9 点前不发；同一天不重复。"""
    import datetime as dt

    from app.services.background_workers import due_reports

    friday = dt.datetime(2026, 11, 13, 10, 0)
    assert due_reports(friday, hour=9) == ["daily"]
    assert due_reports(friday, hour=9, last_key="daily:2026-11-13") == []
    # 11 点才启动也要补发（错过了 9 点不能整天不发）
    assert due_reports(dt.datetime(2026, 11, 13, 11, 0), hour=9, last_key="") == ["daily"]
    assert due_reports(dt.datetime(2026, 11, 13, 7, 0), hour=9) == [], "太早不发"

    monday = dt.datetime(2026, 11, 9, 10, 0)
    assert due_reports(monday, hour=9) == ["daily", "weekly"]

    first_of_month = dt.datetime(2026, 12, 1, 10, 0)
    assert due_reports(first_of_month, hour=9) == ["daily", "monthly"]

    # 元旦既是 1 号又是周二（不是周一）→ 日报 + 月报
    new_year = dt.datetime(2027, 1, 1, 10, 0)
    assert due_reports(new_year, hour=9) == ["daily", "monthly"]
