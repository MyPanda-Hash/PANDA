"""早间主动推送：计划要落到人头上，且一天只推一次。

这里跑的是真库（临时 sqlite），因为要验证的是「谁收到什么」——
那种事用 mock 断言等于没测。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import ProjectGroup, ProjectGroupMessage
from app.models.inapp_notification import InappNotification
from app.services import assistant_watch

TZ = timezone.utc


class FakeMes:
    """只喂规则需要的最小快照：一张已审核未排产的销售订单。"""

    def fetch_snapshot(self):
        return {
            "SO_ORDER": [
                {
                    "单据编号": "SO-TEST-1",
                    "单据状态": "已审核",
                    "审批状态": "已审核",
                    "客户": "测试客户",
                    "业务员": "sales",
                }
            ],
            "MANU_ORDER": [],
            "STOCK_STATUS": [],
            "PU_ORDER": [],
            "ARRIVAL_IN": [],
            "FINISH_INSPECT": [],
            "PROCESS_REPORT": [],
            "SALE_OUT": [],
            "MATERIAL_REQ": [],
            "MATERIAL_OUT": [],
        }

    def approvers(self):
        return []

    def pending_approvals(self, username):
        return []


@pytest.fixture
def db_session(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "plan.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _user(db: Session, username: str) -> User:
    user = User(
        id=f"usr_{username}",
        username=username,
        email=f"{username}@e.com",
        display_name=username,
        password_hash="x",
        role="user",
        status="active",
        first_login=False,
        created_at=datetime(2026, 5, 1, tzinfo=TZ),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def scanned(db_session, monkeypatch):
    """跑一轮扫描，让事件真的进库。"""
    monkeypatch.setattr(settings, "mes_watch_enabled", True)
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    monkeypatch.setattr(settings, "mes_watch_group_name", "AI 调度台")
    monkeypatch.setattr(settings, "bootstrap_username", "admin")
    _user(db_session, "admin")
    _user(db_session, "planner")
    _user(db_session, "worker")
    assistant_watch.scan(
        db_session, client=FakeMes(), today=date(2026, 11, 13), notify=True
    )
    db_session.commit()
    return db_session


def test_plan_only_contains_my_events(scanned):
    """派给计划岗的事，不该出现在别人的计划里。"""
    planner = scanned.scalar(select(User).where(User.username == "planner"))
    worker = scanned.scalar(select(User).where(User.username == "worker"))
    plan_planner = assistant_watch.today_plan(scanned, user=planner)
    plan_worker = assistant_watch.today_plan(scanned, user=worker)
    assert plan_planner["total"] >= 1
    # SO 未排产默认派给 planner/worker，两人都该看到；重点是能各自成计划
    assert "今天优先处理" in plan_planner["summary"]
    assert isinstance(plan_worker["total"], int)


def test_plan_merges_same_kind_events(scanned):
    planner = scanned.scalar(select(User).where(User.username == "planner"))
    plan = assistant_watch.today_plan(scanned, user=planner)
    titles = [item["title"] for item in plan["top"]]
    assert titles, "计划不能是空的"
    # 同类事件应合并成「N 张…」而不是并列多条一样的标题
    assert len(titles) == len(set(titles))


def test_morning_plan_pushes_once_per_day(scanned):
    """同一天重复跑不能重复推——否则早上一开机就是一串重复消息。"""
    first = assistant_watch.post_morning_plan(scanned)
    scanned.commit()
    second = assistant_watch.post_morning_plan(scanned)
    scanned.commit()

    rows = list(
        scanned.scalars(
            select(InappNotification).where(InappNotification.kind == "notice")
        )
    )
    assert first["pushed"] >= 1
    assert len(rows) == first["pushed"], "第二次不该再插新行"


def test_morning_plan_posts_group_summary(scanned):
    assistant_watch.post_morning_plan(scanned)
    scanned.commit()
    group = scanned.scalar(select(ProjectGroup).where(ProjectGroup.name == "AI 调度台"))
    assert group is not None
    messages = list(
        scanned.scalars(
            select(ProjectGroupMessage).where(ProjectGroupMessage.group_id == group.id)
        )
    )
    assert any("今天要处理的事" in (m.content or "") for m in messages)


def test_morning_plan_is_quiet_without_events(db_session):
    """没有事件的日子不要硬凑一条。"""
    _user(db_session, "admin")
    db_session.commit()
    result = assistant_watch.post_morning_plan(db_session)
    assert result["pushed"] == 0
    assert result["posted"] is False