"""周回顾与责任人画像。

两条不能破的边界：
1. 画像只说系统里看得见的行为（派了多少、多久关掉、还压着几条），
   不评价态度、不猜能力，也不引入系统外的信息；
2. 回顾的重点是「重复」——同一类问题反复出现说明根因没动，
   这时候加提醒只是让同一个坑被踩得更响。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.security import now as _now
from app.models.assistant_watch import AssistantEvent
from app.models.auth import User
from app.services import owner_profile, weekly_review
from app.services.assistant_watch import KIND_SUMMARY

TZ = timezone.utc


@pytest.fixture
def db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "review.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _user(db, username: str, display: str) -> User:
    user = User(
        id=f"usr_{username}",
        username=username,
        email=f"{username}@e.com",
        display_name=display,
        password_hash="x",
        role="user",
        status="active",
        first_login=False,
        created_at=datetime(2026, 5, 1, tzinfo=TZ),
    )
    db.add(user)
    db.flush()
    return user


def _event(
    db,
    *,
    n: int,
    kind: str = "so_unstarted",
    doc_no: str = "SO-1",
    targets: list[str] | None = None,
    status: str = "open",
    hours_ago: float = 1,
) -> AssistantEvent:
    stamp = _now() - timedelta(hours=hours_ago)
    row = AssistantEvent(
        id=f"aev_{n}",
        dedupe_key=f"{kind}:{doc_no}:{n}",
        kind=kind,
        source="mes",
        panel_code="SO_ORDER",
        doc_no=doc_no,
        title=f"{KIND_SUMMARY.get(kind, kind)} {doc_no}",
        detail="",
        suggested_action="",
        severity="warning",
        original_severity="warning",
        status=status,
        target_usernames_json=json.dumps(targets or ["planner"], ensure_ascii=False),
        first_seen_at=stamp,
        created_at=stamp,
        updated_at=stamp,
        resolved_at=stamp if status == "resolved" else None,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- 责任人画像


def test_profile_counts_only_my_events(db):
    _user(db, "planner", "李计划")
    _user(db, "keeper", "张仓管")
    _event(db, n=1, targets=["planner"])
    _event(db, n=2, targets=["keeper"])
    person = owner_profile.find_user(db, "planner")
    profile = owner_profile.build_profile(db, user=person)
    assert profile.total == 1
    assert profile.display_name == "李计划"


def test_profile_flags_stale_backlog(db):
    _user(db, "planner", "李计划")
    for i in range(3):
        _event(db, n=i, hours_ago=48, targets=["planner"])
    profile = owner_profile.build_profile(db, user=owner_profile.find_user(db, "planner"))
    assert profile.stale_open == 3
    assert "超过一天没动" in profile.verdict


def test_profile_finds_repeat_doc(db):
    _user(db, "planner", "李计划")
    _event(db, n=1, doc_no="OTHE-1", targets=["planner"])
    _event(db, n=2, kind="pu_draft", doc_no="OTHE-1", targets=["planner"])
    profile = owner_profile.build_profile(db, user=owner_profile.find_user(db, "planner"))
    assert profile.repeat_docs
    assert "根因" in profile.verdict


def test_profile_says_nothing_when_no_history(db):
    """没数据就不要编评价。"""
    _user(db, "newbie", "新人")
    profile = owner_profile.build_profile(db, user=owner_profile.find_user(db, "newbie"))
    assert profile.total == 0
    assert "暂时没有" in profile.render()


# ---------------------------------------------------------------- 周回顾


def test_review_finds_repeated_kind(db):
    _user(db, "planner", "李计划")
    for i in range(3):
        _event(db, n=i, kind="pending_approval", doc_no=f"DOC-{i}", hours_ago=2)
    review = weekly_review.build_review(db, today=_now().date())
    assert review.kinds
    assert review.kinds[0][1] == 3
    assert any("反复" in item or "同一件事" in item for item in review.focus)
    assert "反复出现" in review.render()


def test_review_flags_repeat_doc(db):
    _user(db, "planner", "李计划")
    _event(db, n=1, doc_no="OTHE-9")
    _event(db, n=2, kind="pu_draft", doc_no="OTHE-9")
    review = weekly_review.build_review(db, today=_now().date())
    assert review.repeat_docs == [("OTHE-9", 2)]
    assert any("OTHE-9" in item for item in review.focus)


def test_review_compares_with_last_week(db):
    _user(db, "planner", "李计划")
    # 上周一条（8 天前），本周一条（今天）
    _event(db, n=1, hours_ago=24 * 8, doc_no="SO-LAST")
    _event(db, n=2, hours_ago=2, doc_no="SO-NOW")
    review = weekly_review.build_review(db, today=_now().date())
    assert review.this_week.created >= 1
    assert review.last_week.created == 1


def test_review_quiet_week_has_no_fake_focus(db):
    """没有数据时说「正常」，不要编三条改善建议。"""
    review = weekly_review.build_review(db, today=date(2026, 11, 13))
    assert review.this_week.created == 0
    assert len(review.focus) == 1
    assert "没有新问题" in review.focus[0]


def test_review_renders_markdown_sections(db):
    _user(db, "planner", "李计划")
    _event(db, n=1, hours_ago=2)
    text = weekly_review.build_review(db, today=_now().date()).render()
    assert "这周的数字" in text
    assert "下周先解决什么" in text

# ---------------------------------------------------------------- 根因归并


def test_root_cause_clusters_same_doc(db):
    """同一张单上的两个问题，应该被认成同一件事。"""
    from app.services import root_cause

    _user(db, "planner", "李计划")
    _event(db, n=1, kind="pending_approval", doc_no="OTHE-1")
    _event(db, n=2, kind="so_unstarted", doc_no="OTHE-1")
    causes = root_cause.analyze(db)
    assert len(causes) == 1
    assert causes[0].count == 2
    assert "OTHE-1" in causes[0].label


def test_root_cause_ignores_single_unrepeated_event(db):
    from app.services import root_cause

    _user(db, "planner", "李计划")
    _event(db, n=1, doc_no="SO-1")
    assert root_cause.analyze(db) == []


def test_root_cause_surfaces_recurrence(db):
    """复发过的单条事件也要单独成簇——它本来就是「同一件事又来了」。"""
    from app.services import root_cause

    _user(db, "planner", "李计划")
    row = _event(db, n=1, doc_no="SO-2")
    row.recurrence = 3
    db.flush()
    causes = root_cause.analyze(db)
    assert len(causes) == 1
    assert causes[0].recurrence == 3
    assert "第 4 次" in causes[0].verdict


def test_root_cause_merges_multi_approval_into_one_line(db):
    """一张单卡在两级审批，是同一件事，不该分两次催。"""
    from app.services import root_cause

    _user(db, "planner", "李计划")
    for i, approver in enumerate(("admin", "keeper"), start=1):
        row = _event(db, n=i, kind="pending_approval", doc_no="OTHE-9")
        row.payload_json = json.dumps({"approver": approver})
        db.flush()
    causes = root_cause.analyze(db)
    assert "审批" in causes[0].verdict
    assert "admin" in causes[0].verdict and "keeper" in causes[0].verdict


def test_plan_merges_events_of_one_doc():
    """计划里同一张单只占一行。"""
    from app.services import production_insight as pi

    plan = pi.plan_day([
        {"kind": "pending_approval", "title": "A 待审", "severity": "warning",
         "age_hours": 30, "doc_no": "OTHE-1"},
        {"kind": "so_unstarted", "title": "A 未排产", "severity": "warning",
         "age_hours": 5, "doc_no": "OTHE-1"},
    ])
    assert len(plan["top"]) == 1
    assert "2 个问题" in plan["top"][0]["title"]


# ---------------------------------------------------------------- 跨实体关联


def test_supplier_chain_needs_two_kinds(db):
    """采购逾期 + 到货没检，同一个供应商，才算这条线断了。"""
    from app.services import root_cause

    _user(db, "keeper", "张仓管")
    for i, kind in enumerate(("pu_unshipped", "arrival_uninspected"), start=1):
        row = _event(db, n=i, kind=kind, doc_no=f"DOC-{i}")
        row.payload_json = json.dumps({"supplier": "北方机械"})
        db.flush()
    chains = [c for c in root_cause.analyze(db) if c.kind == "supplier"]
    assert len(chains) == 1
    assert "北方机械" in chains[0].verdict
    assert "断了" in chains[0].verdict


def test_supplier_chain_ignores_same_kind(db):
    """同一供应商两张单都是「采购逾期」，是同类问题，不算链路断裂。"""
    from app.services import root_cause

    _user(db, "keeper", "张仓管")
    for i in (1, 2):
        row = _event(db, n=i, kind="pu_unshipped", doc_no=f"DOC-{i}")
        row.payload_json = json.dumps({"supplier": "北方机械"})
        db.flush()
    assert [c for c in root_cause.analyze(db) if c.kind == "supplier"] == []


def test_customer_chain_needs_two_docs(db):
    """同一客户的两条事件如果都在同一张单上，不算客户链。"""
    from app.services import root_cause

    _user(db, "planner", "李计划")
    for i, kind in enumerate(("so_unstarted", "so_draft"), start=1):
        row = _event(db, n=i, kind=kind, doc_no="SO-SAME")
        row.payload_json = json.dumps({"customer": "中天精工"})
        db.flush()
    assert [c for c in root_cause.analyze(db) if c.kind == "customer"] == []


def test_customer_chain_across_orders(db):
    from app.services import root_cause

    _user(db, "planner", "李计划")
    for i in (1, 2):
        row = _event(db, n=i, doc_no=f"SO-{i}")
        row.payload_json = json.dumps({"customer": "中天精工"})
        db.flush()
    chains = [c for c in root_cause.analyze(db) if c.kind == "customer"]
    assert len(chains) == 1
    assert "救火" in chains[0].verdict


def test_approver_bottleneck_needs_three(db):
    """两条审批还不算瓶颈，三条才是。"""
    from app.services import root_cause

    _user(db, "admin", "管理员")
    for i in (1, 2):
        row = _event(db, n=i, kind="pending_approval", doc_no=f"D-{i}")
        row.payload_json = json.dumps({"approver": "admin"})
        db.flush()
    assert [c for c in root_cause.analyze(db) if c.kind == "approver"] == []

    row = _event(db, n=3, kind="pending_approval", doc_no="D-3")
    row.payload_json = json.dumps({"approver": "admin"})
    db.flush()
    chains = [c for c in root_cause.analyze(db) if c.kind == "approver"]
    assert len(chains) == 1
    assert "流程瓶颈" in chains[0].verdict


def test_cross_entity_render_is_grouped(db):
    from app.services import root_cause

    _user(db, "admin", "管理员")
    for i in range(3):
        row = _event(db, n=i, kind="pending_approval", doc_no=f"D-{i}")
        row.payload_json = json.dumps({"approver": "admin"})
        db.flush()
    text = root_cause.render_all(root_cause.analyze(db))
    assert "跨单的共同根因" in text
    assert "怎么处理" in text


def test_dual_role_entity_clusters_both_sides(db):
    """同一家公司既是供应商又是客户：两端待办要合成一条，否则会一边催一边承诺。"""
    from app.services import root_cause

    _user(db, "keeper", "张仓管")
    buy = _event(db, n=1, kind="pu_unshipped", doc_no="PO-1")
    buy.payload_json = json.dumps({"supplier": "华东铝业", "dual_role": "华东铝业"})
    sell = _event(db, n=2, kind="so_unstarted", doc_no="SO-1")
    sell.payload_json = json.dumps({"customer": "华东铝业", "dual_role": "华东铝业"})
    db.flush()

    chains = [c for c in root_cause.analyze(db) if c.kind == "chain"]
    assert len(chains) == 1
    assert "买卖同源" in chains[0].label
    assert "同一批货" in chains[0].verdict


def test_dual_role_ignored_without_both_sides(db):
    from app.services import root_cause

    _user(db, "keeper", "张仓管")
    row = _event(db, n=1, kind="pu_unshipped", doc_no="PO-1")
    row.payload_json = json.dumps({"supplier": "北方机械", "dual_role": "北方机械"})
    db.flush()
    assert [c for c in root_cause.analyze(db) if c.kind == "chain"] == []
