from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.core.security import now
from app.models.assistant_watch import AssistantEvent
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroupMessage
from app.services import assistant_watch


@pytest.fixture
def watch_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "assistant-watch.sqlite3"
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


def _user(db: Session, username: str, display: str, *, role: str = "user") -> User:
    user = User(
        id=f"usr_{username}",
        username=username,
        email=f"{username}@example.com",
        display_name=display,
        password_hash="unused",
        role=role,
        status="active",
        first_login=False,
        created_at=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def seeded(watch_db: Session):
    admin = _user(watch_db, "admin", "管理员", role="admin")
    _user(watch_db, "planner", "李计划")
    _user(watch_db, "worker", "赵车间")
    _user(watch_db, "sales", "王销售")
    _user(watch_db, "keeper", "张仓管")
    _user(watch_db, "qc", "孙质检")
    watch_db.flush()
    return watch_db, admin


class FakeClient:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.calls = 0

    def fetch_snapshot(self):
        self.calls += 1
        return self._snapshot


SO_APPROVED = {
    "单据编号": "SO-20261112-009",
    "单据状态": "已审核",
    "审批状态": "已通过",
    "客户": "宏远机械",
    "业务员": "王销售",
}


def _snapshot(so_rows=None, manu_rows=None):
    return {"SO_ORDER": so_rows or [], "MANU_ORDER": manu_rows or []}


# ---------------- 规则 ----------------

def test_approved_order_without_manu_order_becomes_event():
    candidates = assistant_watch.evaluate(
        _snapshot([SO_APPROVED]), today=date(2026, 11, 12)
    )
    assert len(candidates) == 1
    item = candidates[0]
    assert item.kind == "so_unstarted"
    assert item.dedupe_key == "so_unstarted:SO-20261112-009"
    assert "已审核" in item.title
    assert item.targets == ["planner", "worker"]
    assert item.severity == "warning"


def test_order_with_existing_manu_order_is_not_reported():
    manu = [{"单据编号": "MO-1", "销售订单号": "SO-20261112-009", "单据状态": "生产中"}]
    assert assistant_watch.evaluate(_snapshot([SO_APPROVED], manu)) == []


def test_draft_order_notifies_salesman_by_display_name():
    draft = dict(SO_APPROVED, **{"单据编号": "SO-20261112-010", "单据状态": "草稿"})
    candidates = assistant_watch.evaluate(_snapshot([draft]))
    assert candidates[0].kind == "so_draft"
    assert candidates[0].targets == ["王销售"]


def test_manu_order_due_today_is_critical_and_late_is_critical():
    today = date(2026, 11, 12)
    rows = [
        {"单据编号": "MO-A", "销售订单号": "X", "单据状态": "生产中", "预完工日": "2026-11-10"},
        {"单据编号": "MO-B", "销售订单号": "Y", "单据状态": "已审核", "预完工日": "2026-11-13"},
        {"单据编号": "MO-C", "销售订单号": "Z", "单据状态": "生产中", "预完工日": "2026-12-01"},
        {"单据编号": "MO-D", "销售订单号": "W", "单据状态": "已完工", "预完工日": "2026-11-01"},
    ]
    candidates = assistant_watch.evaluate(_snapshot([], rows), today=today)
    kinds = {(c.doc_no, c.severity) for c in candidates}
    assert ("MO-A", "critical") in kinds   # 已逾期
    assert ("MO-B", "warning") in kinds    # 2 天内到期
    assert ("MO-C", "info") not in kinds   # 还早
    assert all(c.doc_no != "MO-D" for c in candidates)  # 已完工不管


# ---------------- 落地 ----------------

def test_scan_creates_event_todos_and_group_message(seeded):
    db, admin = seeded
    client = FakeClient(_snapshot([SO_APPROVED]))

    result = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    assert result["created"] == 1
    event = db.scalar(select(AssistantEvent))
    assert event.status == "open"
    assert event.notifications == 2

    todos = list(db.scalars(select(GroupTodo)))
    assert len(todos) == 2
    assert {t.assignee_user_id for t in todos} == {"usr_planner", "usr_worker"}
    assert all(t.status == "pending" for t in todos)

    group = db.scalar(select(assistant_watch.ProjectGroup))
    assert group is not None and group.name == settings.mes_watch_group_name
    messages = [m for m in db.scalars(select(ProjectGroupMessage)) if m.kind == "ai"]
    assert len(messages) == 1
    assert "SO-20261112-009" in messages[0].content
    assert "@planner" in messages[0].content


def test_second_scan_does_not_duplicate(seeded):
    db, _ = seeded
    client = FakeClient(_snapshot([SO_APPROVED]))
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    again = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    assert again["created"] == 0
    assert len(list(db.scalars(select(AssistantEvent)))) == 1
    assert len(list(db.scalars(select(GroupTodo)))) == 2


def test_event_resolves_when_condition_disappears(seeded):
    db, _ = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))

    manu = [{"单据编号": "MO-9", "销售订单号": "SO-20261112-009", "单据状态": "生产中"}]
    result = assistant_watch.scan(
        db, client=FakeClient(_snapshot([SO_APPROVED], manu)), today=date(2026, 11, 12)
    )

    assert result["resolved"] == 1
    event = db.scalar(select(AssistantEvent))
    assert event.status == "resolved"
    assert event.resolved_at is not None


def test_list_events_only_shows_mine_unless_admin(seeded):
    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    planner = db.scalar(select(User).where(User.username == "planner"))
    sales = db.scalar(select(User).where(User.username == "sales"))

    assert len(assistant_watch.list_events(db, user=planner)) == 1
    assert assistant_watch.list_events(db, user=sales) == []
    assert len(assistant_watch.list_events(db, user=admin)) == 1


def test_ack_event_marks_handled(seeded):
    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))

    assistant_watch.ack_event(db, event=event, user=admin)

    assert event.status == "handled"
    assert event.acked_by_user_id == admin.id
    assert assistant_watch.list_events(db, user=admin, status="open") == []


def test_draft_with_unknown_salesman_creates_no_todo(seeded):
    """草稿是业务员自己的事；对不上账号就只记录事件，不要乱派给计划员。"""
    db, _ = seeded
    draft = dict(SO_APPROVED, **{"单据编号": "SO-20261112-011", "单据状态": "草稿", "业务员": "查无此人"})

    assistant_watch.scan(db, client=FakeClient(_snapshot([draft])), today=date(2026, 11, 12))

    event = db.scalar(select(AssistantEvent))
    assert event is not None
    assert list(db.scalars(select(GroupTodo))) == []


def test_unknown_approver_falls_back_to_panel_owners(monkeypatch):
    """面板推断类事件找不到人时，退回面板默认角色，不能没人管。"""
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    row = {"单据编号": "SO-20261112-030", "单据状态": "已审核", "审批状态": "待审批"}
    candidates = assistant_watch.evaluate(_snapshot([row]))
    assert candidates[0].targets == ["planner", "worker"]
    assert candidates[0].exact is False


def test_pending_approval_document_notifies_panel_owners(monkeypatch):
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    row = {"单据编号": "SO-20261112-012", "单据状态": "已审核", "审批状态": "待审批"}
    candidates = assistant_watch.evaluate(_snapshot([row]))
    assert [c.kind for c in candidates] == ["pending_approval"]
    assert candidates[0].dedupe_key == "pending_approval:SO_ORDER:SO-20261112-012"
    assert candidates[0].targets == ["planner", "worker"]
    assert candidates[0].severity == "warning"


def test_existing_event_gets_targets_backfilled_on_next_scan(seeded, monkeypatch):
    """面板类事件首次没匹配到人时，下次扫描要补上，不能一直悬空。"""
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    db, _ = seeded
    row = {"单据编号": "SO-20261112-013", "单据状态": "已审核", "审批状态": "待审批"}
    client = FakeClient(_snapshot([row]))

    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    # 手动清空目标，模拟旧数据
    event.target_usernames_json = "[]"
    event.target_user_ids_json = "[]"
    event.notifications = 0
    db.flush()

    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    refreshed = db.scalar(select(AssistantEvent))
    assert json.loads(refreshed.target_usernames_json) == ["planner", "worker"]
    assert len(list(db.scalars(select(GroupTodo)))) == 2


class FakeOpenClient(FakeClient):
    """带开放审批接口的假客户端。"""

    def __init__(self, snapshot, approvers, pending_by_approver):
        super().__init__(snapshot)
        self._approvers = approvers
        self._pending = pending_by_approver

    def approvers(self):
        return self._approvers

    def pending_approvals(self, approver, limit=50):
        return self._pending.get(approver, [])


def test_exact_approver_from_open_api(seeded, monkeypatch):
    """开放接口给出的审批人才是权威，不再按面板猜。"""
    db, _ = seeded
    monkeypatch.setattr(settings, "mes_open_api_enabled", True)
    client = FakeOpenClient(
        _snapshot([]),
        approvers=[
            {"username": "planner", "displayName": "李计划", "panels": ["SO_ORDER"]},
            {"username": "worker", "displayName": "赵车间", "panels": ["MANU_ORDER"]},
        ],
        pending_by_approver={
            "planner": [
                {
                    "panelCode": "SO_ORDER",
                    "panelName": "销售订单",
                    "formNo": "SO-20261112-020",
                    "submitter": "sales",
                    "submittedAt": "2026-11-12 10:00:00",
                }
            ],
            "worker": [],
        },
    )

    result = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    assert result["created"] == 1
    event = db.scalar(select(AssistantEvent))
    assert event.kind == "pending_approval"
    assert event.dedupe_key == "pending_approval:planner:SO_ORDER:SO-20261112-020"
    assert json.loads(event.target_usernames_json) == ["planner"]
    assert "待 planner 审批" in event.title

    todos = list(db.scalars(select(GroupTodo)))
    assert [t.assignee_user_id for t in todos] == ["usr_planner"]
    assert "SO-20261112-020" in todos[0].title


def test_open_api_disabled_keeps_heuristic_rule(seeded, monkeypatch):
    db, _ = seeded
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    row = {"单据编号": "SO-20261112-021", "单据状态": "已审核", "审批状态": "待审批"}
    assistant_watch.scan(db, client=FakeClient(_snapshot([row])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    # 没配接口时退回面板角色，dedupe_key 不带人名
    assert event.dedupe_key == "pending_approval:SO_ORDER:SO-20261112-021"
    assert json.loads(event.target_usernames_json) == ["planner", "worker"]


def test_unknown_approver_does_not_create_todo(seeded, monkeypatch):
    db, _ = seeded
    monkeypatch.setattr(settings, "mes_open_api_enabled", True)
    client = FakeOpenClient(
        _snapshot([]),
        approvers=[{"username": "ghost", "panels": ["SO_ORDER"]}],
        pending_by_approver={"ghost": [{"panelCode": "SO_ORDER", "panelName": "销售订单",
                                        "formNo": "SO-9", "submitter": "sales"}]},
    )
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assert list(db.scalars(select(GroupTodo))) == []


def test_same_person_multiple_docs_get_one_aggregated_todo(seeded):
    """同一个人 + 同类事情只给一条待办，标题带数量，详情列单据。"""
    db, _ = seeded
    orders = [
        dict(SO_APPROVED, **{"单据编号": "SO-A"}),
        dict(SO_APPROVED, **{"单据编号": "SO-B"}),
        dict(SO_APPROVED, **{"单据编号": "SO-C"}),
    ]
    assistant_watch.scan(db, client=FakeClient(_snapshot(orders)), today=date(2026, 11, 12))

    todos = list(db.scalars(select(GroupTodo)))
    assert len(todos) == 2, "planner 一条、worker 一条"
    planner_todo = next(t for t in todos if t.assignee_user_id == "usr_planner")
    assert planner_todo.title.startswith("3 张销售订单待安排生产")
    for doc in ("SO-A", "SO-B", "SO-C"):
        assert doc in planner_todo.detail
    assert len(json.loads(planner_todo.assistant_event_ids_json)) == 3

    message = db.scalar(select(ProjectGroupMessage).where(ProjectGroupMessage.kind == "ai"))
    assert "@planner" in message.content
    assert "SO-A、SO-B、SO-C" in message.content


def test_resolved_event_closes_its_todos(seeded):
    """MES 状态变了（工单建好），AI 派的待办要自动关闭，否则清单越堆越长。"""
    db, _ = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    assert all(t.status == "pending" for t in db.scalars(select(GroupTodo)))

    manu = [{"单据编号": "MO-1", "销售订单号": "SO-20261112-009", "单据状态": "生产中"}]
    assistant_watch.scan(
        db, client=FakeClient(_snapshot([SO_APPROVED], manu)), today=date(2026, 11, 12)
    )

    todos = list(db.scalars(select(GroupTodo)))
    assert todos, "待办还在，只是被关闭"
    assert all(t.status == "done" for t in todos)
    assert all(t.completed_at is not None for t in todos)
    assert any("MES 状态已变化" in t.detail for t in todos)


def test_announcement_separates_draft_from_unstarted(seeded):
    """同一个人的不同事情要分行写，不能把草稿混进待排产里。"""
    db, _ = seeded
    rows = [
        dict(SO_APPROVED, **{"单据编号": "SO-A"}),
        dict(SO_APPROVED, **{"单据编号": "SO-B"}),
        dict(SO_APPROVED, **{"单据编号": "SO-C", "单据状态": "草稿"}),
    ]
    assistant_watch.scan(db, client=FakeClient(_snapshot(rows)), today=date(2026, 11, 12))

    message = db.scalar(select(ProjectGroupMessage).where(ProjectGroupMessage.kind == "ai"))
    text = message.content
    assert "2 张销售订单待安排生产（SO-A、SO-B）" in text
    assert "SO-C 仍是草稿" in text


def test_partial_resolve_keeps_todo_open_with_remaining_docs(seeded):
    """汇总待办关联 3 张单，处理掉 1 张后要剩 2 张，不能整条关掉。"""
    db, _ = seeded
    rows = [dict(SO_APPROVED, **{"单据编号": f"SO-P{i}"}) for i in (1, 2, 3)]
    assistant_watch.scan(db, client=FakeClient(_snapshot(rows)), today=date(2026, 11, 12))

    planner_todo = next(
        t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_planner"
    )
    assert planner_todo.title.startswith("3 张销售订单待安排生产")

    # 只给 SO-P1 建了生产工单
    manu = [{"单据编号": "MO-1", "销售订单号": "SO-P1", "单据状态": "生产中"}]
    assistant_watch.scan(
        db, client=FakeClient(_snapshot(rows, manu)), today=date(2026, 11, 12)
    )

    planner_todo = next(
        t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_planner"
    )
    assert planner_todo.status == "pending"
    assert planner_todo.title.startswith("2 张销售订单待安排生产")
    assert "SO-P1" not in planner_todo.detail
    assert "SO-P2" in planner_todo.detail

    # 三张都建了 → 关闭
    manu_all = [
        {"单据编号": f"MO-{i}", "销售订单号": f"SO-P{i}", "单据状态": "生产中"} for i in (1, 2, 3)
    ]
    assistant_watch.scan(
        db, client=FakeClient(_snapshot(rows, manu_all)), today=date(2026, 11, 12)
    )
    planner_todo = next(
        t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_planner"
    )
    assert planner_todo.status == "done"


# ---------------- 新增四条规则 ----------------

def _snap(**panels):
    base = {"SO_ORDER": [], "MANU_ORDER": [], "STOCK_STATUS": [],
            "PU_ORDER": [], "ARRIVAL_IN": [], "FINISH_INSPECT": [], "PROCESS_REPORT": []}
    base.update(panels)
    return base


def test_stock_low_uses_configured_threshold(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_low_stock_threshold", 50.0)
    monkeypatch.setattr(settings, "mes_watch_low_stock_items", "CP004:200")
    rows = [
        {"存货编码": "CL005", "存货": "包装木箱", "数量": 40.0, "仓库": "主仓", "仓库编码": "CK07"},
        {"存货编码": "CP004", "存货": "减速箱体", "数量": 120.0, "仓库": "半成品", "仓库编码": "CK03"},
        {"存货编码": "CP002", "存货": "铝板", "数量": 450.0, "仓库": "原料", "仓库编码": "CK03"},
    ]
    candidates = assistant_watch.evaluate(_snap(STOCK_STATUS=rows), today=date(2026, 11, 12))
    keys = {c.dedupe_key for c in candidates}
    assert "stock_low:CL005:CK07" in keys          # 低于全局阈值
    assert "stock_low:CP004:CK03" in keys          # 低于该物料覆盖阈值
    assert "stock_low:CP002:CK03" not in keys      # 高于阈值


def test_stock_rule_disabled_when_no_threshold(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_low_stock_threshold", 0.0)
    monkeypatch.setattr(settings, "mes_watch_low_stock_items", "")
    rows = [{"存货编码": "CL005", "存货": "包装木箱", "数量": 1.0, "仓库编码": "CK07"}]
    assert assistant_watch.evaluate(_snap(STOCK_STATUS=rows)) == []


def test_purchase_draft_and_unshipped(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_purchase_lead_days", 3)
    rows = [
        {"单据编号": "PO-1", "单据状态": "草稿", "制单人": "planner", "供应商": "宏远"},
        {"单据编号": "PO-2", "单据状态": "已审核", "发货状态": "未发货",
         "交货日期": "2026-11-13", "供应商": "宏远"},
        {"单据编号": "PO-3", "单据状态": "已审核", "发货状态": "已发货",
         "交货日期": "2026-11-13", "供应商": "宏远"},
    ]
    candidates = assistant_watch.evaluate(_snap(PU_ORDER=rows), today=date(2026, 11, 12))
    kinds = {(c.doc_no, c.kind, c.severity) for c in candidates}
    assert ("PO-1", "pu_draft", "info") in kinds
    assert ("PO-2", "pu_unshipped", "warning") in kinds
    assert all(c.doc_no != "PO-3" for c in candidates)


def test_arrival_uninspected_after_window(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_inspect_overdue_days", 2)
    rows = [
        {"单据编号": "DH-1", "单据状态": "已审核", "检验状态": "未检",
         "到货日期": "2026-11-01", "供应商": "宏远"},
        {"单据编号": "DH-2", "单据状态": "已审核", "检验状态": "已检",
         "到货日期": "2026-11-01"},
        {"单据编号": "DH-3", "单据状态": "已审核", "检验状态": "未检",
         "到货日期": "2026-11-12"},
    ]
    candidates = assistant_watch.evaluate(_snap(ARRIVAL_IN=rows), today=date(2026, 11, 12))
    assert [c.doc_no for c in candidates] == ["DH-1"]
    assert candidates[0].targets == ["qc"]


def test_manu_without_report_uses_last_report_date(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_no_report_days", 3)
    manu = [
        {"编号": "MO-1", "单据状态": "生产中", "开工日期": "2026-11-01"},
        {"编号": "MO-2", "单据状态": "生产中", "开工日期": "2026-11-01"},
        {"编号": "MO-3", "单据状态": "已完工", "开工日期": "2026-11-01"},
    ]
    reports = [
        {"加工单号": "MO-1", "单据日期": "2026-11-11"},   # 昨天报过，不提醒
        {"加工单号": "MO-2", "单据日期": "2026-11-01"},   # 十天没报，提醒
    ]
    candidates = assistant_watch.evaluate(
        _snap(MANU_ORDER=manu, PROCESS_REPORT=reports), today=date(2026, 11, 12)
    )
    docs = [c.doc_no for c in candidates if c.kind == "manu_no_report"]
    assert docs == ["MO-2"]


def test_manu_due_rule_reads_number_field(seeded):
    """MANU_ORDER 没有「单据编号」字段，用「编号」；这条规则之前一直没生效。"""
    rows = [{"编号": "SO-1-1", "单据状态": "生产中", "预完工日": "2026-11-10"}]
    candidates = assistant_watch.evaluate(_snap(MANU_ORDER=rows), today=date(2026, 11, 12))
    assert [c.doc_no for c in candidates if c.kind == "manu_due"] == ["SO-1-1"]


def test_announcement_puts_one_line_per_person(seeded):
    """同一个人的不同类别合成一行，别在群里反复 @ 同一个人。"""
    db, _ = seeded
    snapshot = _snap(
        SO_ORDER=[dict(SO_APPROVED, **{"单据编号": "SO-A"})],
        STOCK_STATUS=[{"存货编码": "CL005", "存货": "包装木箱", "数量": 10.0,
                       "仓库": "主仓", "仓库编码": "CK07"}],
    )
    import app.services.assistant_watch as aw
    monkey = aw.settings
    old = monkey.mes_watch_low_stock_threshold
    monkey.mes_watch_low_stock_threshold = 50.0
    try:
        assistant_watch.scan(db, client=FakeClient(snapshot), today=date(2026, 11, 12))
    finally:
        monkey.mes_watch_low_stock_threshold = old

    message = db.scalar(select(ProjectGroupMessage).where(ProjectGroupMessage.kind == "ai"))
    planner_lines = [ln for ln in message.content.splitlines() if ln.startswith("- @planner")]
    assert len(planner_lines) == 1, message.content
    assert "销售订单" in planner_lines[0]
    assert "低于下限" in planner_lines[0]


def test_one_person_gets_n_todos_for_n_kinds(seeded, monkeypatch):
    """决策（2026-11-12 用户确认）：一个人有多类事情就收 N 条待办，不合并成一条。

    理由：不同类别处理方式不同（补货要采购、催货要联系供应商、审批要开单据），
    合一条会导致「处理完一半」时无法逐类勾选。
    """
    monkeypatch.setattr(settings, "mes_watch_low_stock_threshold", 50.0)
    monkeypatch.setattr(settings, "mes_watch_low_stock_items", "")
    db, _ = seeded

    snapshot = _snap(
        SO_ORDER=[dict(SO_APPROVED, **{"单据编号": "SO-A"})],
        STOCK_STATUS=[{"存货编码": "CL005", "存货": "包装木箱", "数量": 10.0,
                       "仓库": "主仓", "仓库编码": "CK07"}],
        PU_ORDER=[{"单据编号": "PO-9", "单据状态": "已审核", "发货状态": "未发货",
                   "交货日期": "2026-11-10", "供应商": "宏远"}],
    )
    assistant_watch.scan(db, client=FakeClient(snapshot), today=date(2026, 11, 12))

    # 张仓管（keeper）同时命中「低库存」和「采购未发货」
    keeper_todos = [
        t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_keeper"
    ]
    assert len(keeper_todos) == 2, [t.title for t in keeper_todos]
    titles = sorted(t.title for t in keeper_todos)
    assert any("低于下限" in t for t in titles)
    assert any("未发货" in t for t in titles)

    # 每条待办只关联自己那类事件，勾选一条不影响另一条
    for todo in keeper_todos:
        ids = json.loads(todo.assistant_event_ids_json)
        assert len(ids) == 1
        kinds = {
            event.kind
            for event in db.scalars(select(AssistantEvent).where(AssistantEvent.id.in_(ids)))
        }
        assert len(kinds) == 1
    assert len({k for t in keeper_todos for k in json.loads(t.assistant_event_ids_json)}) == 2


def test_closing_one_kind_leaves_other_kind_open(seeded, monkeypatch):
    """两类待办独立：一类条件消失，另一类照旧待处理。"""
    monkeypatch.setattr(settings, "mes_watch_low_stock_threshold", 50.0)
    monkeypatch.setattr(settings, "mes_watch_low_stock_items", "")
    db, _ = seeded

    stock = [{"存货编码": "CL005", "存货": "包装木箱", "数量": 10.0,
              "仓库": "主仓", "仓库编码": "CK07"}]
    assistant_watch.scan(
        db,
        client=FakeClient(_snap(SO_ORDER=[dict(SO_APPROVED, **{"单据编号": "SO-A"})],
                                STOCK_STATUS=stock)),
        today=date(2026, 11, 12),
    )
    keeper = [t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_keeper"]
    assert len(keeper) == 1

    # 库存补上去了，但销售订单仍未排产 → 库存待办关，订单待办仍在
    assistant_watch.scan(
        db,
        client=FakeClient(_snap(SO_ORDER=[dict(SO_APPROVED, **{"单据编号": "SO-A"})],
                                STOCK_STATUS=[])),
        today=date(2026, 11, 12),
    )
    keeper = [t for t in db.scalars(select(GroupTodo)) if t.assignee_user_id == "usr_keeper"]
    assert all(t.status == "done" for t in keeper), "库存待办应关闭"

    planner = [t for t in db.scalars(select(GroupTodo))
               if t.assignee_user_id == "usr_planner" and t.status == "pending"]
    assert planner, "销售订单未排产的待办必须还在"


# ---------------- 提醒 / 升级 / 静默 / 新规则 ----------------

def test_so_shipping_overdue_uses_order_date(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_so_ship_days", 7)
    so = [dict(SO_APPROVED, **{"单据编号": "SO-OLD", "单据日期": "2026-11-01"})]
    manu = [{"编号": "MO-OLD-1", "销售订单号": "SO-OLD", "单据状态": "生产中"}]
    out = [{"单据编号": "CK-2", "单据状态": "已审核", "销售订单号": "SO-OTHER"}]

    candidates = assistant_watch.evaluate(
        _snap(SO_ORDER=so, MANU_ORDER=manu, SALE_OUT=out), today=date(2026, 11, 12)
    )
    assert [c.kind for c in candidates] == ["so_shipping_overdue"]
    assert candidates[0].severity == "warning"

    # 已出库就不再报
    shipped = [{"单据编号": "CK-1", "单据状态": "已审核", "销售订单号": "SO-OLD"}]
    assert assistant_watch.evaluate(
        _snap(SO_ORDER=so, MANU_ORDER=manu, SALE_OUT=shipped), today=date(2026, 11, 12)
    ) == []

    # 没排产的单子归 so_unstarted 管，不算发货超期
    kinds = [c.kind for c in assistant_watch.evaluate(
        _snap(SO_ORDER=so, SALE_OUT=[]), today=date(2026, 11, 12))]
    assert kinds == ["so_unstarted"]


def test_shipping_far_overdue_escalates_to_critical(monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_so_ship_days", 7)
    so = [dict(SO_APPROVED, **{"单据编号": "SO-VERYOLD", "单据日期": "2026-10-01"})]
    manu = [{"编号": "MO-1", "销售订单号": "SO-VERYOLD", "单据状态": "生产中"}]
    candidates = assistant_watch.evaluate(
        _snap(SO_ORDER=so, MANU_ORDER=manu), today=date(2026, 11, 12)
    )
    assert [c.kind for c in candidates] == ["so_shipping_overdue"]
    assert candidates[0].severity == "critical"


def test_quiet_hours_blocks_notification(seeded, monkeypatch):
    db, _ = seeded
    # 0-24 表示整个自然日都静默
    monkeypatch.setattr(settings, "mes_watch_quiet_hours", "0-24")
    result = assistant_watch.scan(
        db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12)
    )
    assert result["quiet"] == 1
    # 事件照样记录，只是不派待办、不喊话
    assert result["created"] == 1
    assert list(db.scalars(select(GroupTodo))) == []


def test_reminder_resends_unhandled_events(seeded, monkeypatch):
    db, _ = seeded
    monkeypatch.setattr(settings, "mes_watch_remind_hours", 0.0)
    monkeypatch.setattr(settings, "mes_watch_max_reminders", 2)
    client = FakeClient(_snapshot([SO_APPROVED]))

    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    first = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assert first["reminded"] == 1

    event = db.scalar(select(AssistantEvent))
    assert event.reminders == 1

    second = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assert second["reminded"] == 1
    third = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assert third["reminded"] == 0, "超过上限就不再提醒"

    messages = [m.content for m in db.scalars(select(ProjectGroupMessage)) if m.kind == "ai"]
    assert any("再提醒一次" in m for m in messages)


def test_handled_event_does_not_get_reminded(seeded, monkeypatch):
    db, admin = seeded
    monkeypatch.setattr(settings, "mes_watch_remind_hours", 0.0)
    monkeypatch.setattr(settings, "mes_watch_max_reminders", 3)
    client = FakeClient(_snapshot([SO_APPROVED]))
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    event = db.scalar(select(AssistantEvent))
    assistant_watch.ack_event(db, event=event, user=admin)

    result = assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assert result["reminded"] == 0


def test_severity_escalates_when_left_unhandled(seeded, monkeypatch):
    """旧事件拖过阈值，重扫时要提到 critical。"""
    from datetime import timedelta

    db, _ = seeded
    client = FakeClient(_snapshot([SO_APPROVED]))
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    event = db.scalar(select(AssistantEvent))
    assert event.severity == "warning"
    event.first_seen_at = now() - timedelta(hours=48)
    db.flush()

    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    refreshed = db.scalar(select(AssistantEvent))
    assert refreshed.severity == "critical"
    assert refreshed.original_severity == "warning"


def test_critical_events_produce_urgent_todos(seeded):
    db, _ = seeded
    so = [dict(SO_APPROVED, **{"单据编号": "SO-CRIT", "单据状态": "已审核",
                               "审批状态": "待审批"})]
    assistant_watch.scan(db, client=FakeClient(_snap(SO_ORDER=so)), today=date(2026, 11, 12))
    todos = list(db.scalars(select(GroupTodo)))
    assert todos and all(t.priority == "high" for t in todos)


# ---------------- 认领备注 / 撤回认领 / 归档 / 汇总 / 按人提醒 ----------------

def test_ack_stores_note_and_can_be_reopened(seeded):
    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))

    assistant_watch.ack_event(db, event=event, user=admin, note="已联系供应商，等回信")
    assert event.status == "handled"
    assert event.note == "已联系供应商，等回信"
    assert event.acked_by_user_id == admin.id

    assistant_watch.reopen_event(db, event=event, user=admin)
    assert event.status == "open"
    assert event.acked_by_user_id is None
    # 备注保留，便于追溯上次处理到哪
    assert event.note == "已联系供应商，等回信"


def test_archive_marks_old_resolved_events_only(seeded, monkeypatch):
    from datetime import timedelta

    db, admin = seeded
    client = FakeClient(_snapshot([SO_APPROVED]))
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    assistant_watch.ack_event(db, event=event, user=admin)

    # 刚处理完的不该归档
    assert assistant_watch.archive_old_events(db, older_than_days=30) == 0

    event.updated_at = now() - timedelta(days=40)
    assert assistant_watch.archive_old_events(db, older_than_days=30) == 1
    assert event.archived_at is not None

    # 归档后不再出现在列表里
    assert assistant_watch.list_events(db, user=admin, status="all") == []


def test_open_events_are_never_archived(seeded):
    from datetime import timedelta

    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    event.first_seen_at = now() - timedelta(days=90)
    db.flush()

    assert assistant_watch.archive_old_events(db, older_than_days=30) == 0
    assert event.archived_at is None


def test_digest_counts_created_resolved_open(seeded, monkeypatch):
    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    assistant_watch.ack_event(db, event=event, user=admin)

    digest = assistant_watch.build_digest(db, user=admin, days=1)
    assert digest["created"] == 1
    assert digest["resolved"] == 1
    assert digest["open"] == 0
    assert digest["by_kind"].get("so_unstarted") == 1


def test_digest_message_is_posted_with_breakdown(seeded):
    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))

    content = assistant_watch.post_digest(db, days=1)
    assert content and "MES 待办汇总" in content
    assert "新发现" in content
    assert "@planner" in content
    assert "销售订单未排产" in content


def test_reminder_groups_by_person(seeded, monkeypatch):
    monkeypatch.setattr(settings, "mes_watch_remind_hours", 0.0)
    monkeypatch.setattr(settings, "mes_watch_max_reminders", 3)
    db, _ = seeded
    orders = [dict(SO_APPROVED, **{"单据编号": f"SO-R{i}"}) for i in range(3)]
    client = FakeClient(_snapshot(orders))

    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))
    assistant_watch.scan(db, client=client, today=date(2026, 11, 12))

    messages = [m.content for m in db.scalars(select(ProjectGroupMessage)) if m.kind == "ai"]
    reminder = next(m for m in messages if "再提醒一次" in m)
    # 一个人一行，3 条同类事件合成一句
    planner_lines = [ln for ln in reminder.splitlines() if ln.startswith("- @planner")]
    assert len(planner_lines) == 1
    assert "3 件事未处理" in planner_lines[0]


# ---------------- 认领人筛选 ----------------

def _seed_three(db, admin, users):
    """造三条事件：一条未认领、一条我认领、一条别人认领。"""
    stamp = now()
    made = []
    for index, (dedupe, acked_by) in enumerate(
        (("k1", None), ("k2", admin.id), ("k3", users["planner"].id))
    ):
        event = AssistantEvent(
            id=f"aev_f{index}",
            dedupe_key=dedupe,
            kind="so_unstarted",
            title=f"销售订单 SO-F{index} 已审核，尚未安排生产",
            severity="warning",
            status="handled" if acked_by else "open",
            target_usernames_json='["planner","admin"]',
            target_user_ids_json=json.dumps([admin.id, users["planner"].id]),
            payload_json="{}",
            acked_by_user_id=acked_by,
            created_at=stamp,
            updated_at=stamp,
            first_seen_at=stamp,
        )
        db.add(event)
        made.append(event)
    db.flush()
    return made


def test_ack_filter_none_me_others(seeded):
    db, admin = seeded
    planner = db.scalar(select(User).where(User.username == "planner"))
    _seed_three(db, admin, {"planner": planner})

    def titles(**kwargs):
        return {
            e.title for e in assistant_watch.list_events(
                db, user=admin, status="all", limit=50, **kwargs
            )
        }

    assert len(titles(acked="any")) == 3
    assert titles(acked="none") == {"销售订单 SO-F0 已审核，尚未安排生产"}
    assert titles(acked="me") == {"销售订单 SO-F1 已审核，尚未安排生产"}
    assert titles(acked="others") == {"销售订单 SO-F2 已审核，尚未安排生产"}


def test_assignee_filter_matches_username(seeded):
    db, admin = seeded
    planner = db.scalar(select(User).where(User.username == "planner"))
    _seed_three(db, admin, {"planner": planner})

    rows = assistant_watch.list_events(
        db, user=admin, status="all", limit=50, assignee="planner"
    )
    assert len(rows) == 3, "三条都派给了 planner"

    rows = assistant_watch.list_events(
        db, user=admin, status="all", limit=50, assignee="worker"
    )
    assert rows == [], "没派给 worker"


def test_assignee_me_only_returns_my_events(seeded):
    db, admin = seeded
    planner = db.scalar(select(User).where(User.username == "planner"))
    _seed_three(db, admin, {"planner": planner})

    # planner 不是 admin：既受可见性限制，也受 me 筛选
    rows = assistant_watch.list_events(db, user=planner, status="all", limit=50, assignee="me")
    assert rows, "planner 应该能看到派给自己的"
    for row in rows:
        assert planner.id in json.loads(row.target_user_ids_json)


def test_facets_count_by_state_and_assignee(seeded):
    db, admin = seeded
    planner = db.scalar(select(User).where(User.username == "planner"))
    _seed_three(db, admin, {"planner": planner})

    facets = assistant_watch.ack_facets(db, user=admin, status="all")
    assert facets["total"] == 3
    assert facets["unacked"] == 1
    assert facets["acked_by_me"] == 1
    assert facets["acked_by_others"] == 1
    assert facets["by_assignee"]["planner"] == 3


def test_facets_by_assignee_respects_acked_filter(seeded):
    """按人筛选的计数必须跟当前认领筛选一致，否则会出现「标 5 条实际 4 条」。"""
    db, admin = seeded
    planner = db.scalar(select(User).where(User.username == "planner"))
    _seed_three(db, admin, {"planner": planner})

    # 三条都派给 planner：未认领 1 条、admin 认领 1 条、planner 认领 1 条
    assert assistant_watch.ack_facets(db, user=admin, status="all")["by_assignee"]["planner"] == 3
    assert (
        assistant_watch.ack_facets(db, user=admin, status="all", acked="none")["by_assignee"]["planner"]
        == 1
    )
    assert (
        assistant_watch.ack_facets(db, user=admin, status="all", acked="me")["by_assignee"]["planner"]
        == 1
    )


# ---------------- 巡查健康度 / 响应时效 ----------------

def test_scan_records_a_run(seeded):
    from app.models.assistant_watch import WatchScanRun

    db, _ = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))

    run = db.scalar(select(WatchScanRun))
    assert run is not None
    assert run.status == "ok"
    assert run.events_created == 1
    assert run.panels_fetched == 2
    assert run.finished_at is not None


def test_health_reports_success_rate_and_stale(seeded, monkeypatch):
    db, _ = seeded
    now_stamp = now()

    from app.models.assistant_watch import WatchScanRun

    db.add(WatchScanRun(
        id="wrun_ok", trigger="timer", status="ok",
        started_at=now_stamp, finished_at=now_stamp,
        duration_ms=120, panels_fetched=7,
    ))
    db.add(WatchScanRun(
        id="wrun_bad", trigger="timer", status="failed",
        started_at=now_stamp, finished_at=now_stamp,
        duration_ms=0, error="MES 登录失败",
    ))
    db.flush()
    monkeypatch.setattr(settings, "mes_watch_interval_seconds", 60.0)

    health = assistant_watch.scan_health(db, hours=24)
    assert health["runs"] == 2
    assert health["ok"] == 1
    assert health["failed"] == 1
    assert health["success_rate"] == 0.5
    assert health["avg_duration_ms"] == 120
    assert health["stale"] is False


def test_health_reports_stale_when_long_silent(seeded, monkeypatch):
    from datetime import timedelta

    from app.models.assistant_watch import WatchScanRun

    db, _ = seeded
    db.add(WatchScanRun(
        id="wrun_old", trigger="timer", status="ok",
        started_at=now() - timedelta(hours=3), finished_at=now() - timedelta(hours=3),
        duration_ms=100,
    ))
    db.flush()
    monkeypatch.setattr(settings, "mes_watch_interval_seconds", 60.0)

    health = assistant_watch.scan_health(db, hours=24)
    assert health["stale"] is True, "超过 2 个周期没跑应判为停摆"


def test_health_empty_when_no_runs(seeded):
    db, _ = seeded
    health = assistant_watch.scan_health(db, hours=24)
    assert health["runs"] == 0
    assert health["success_rate"] is None
    assert health["stale"] is True


def test_response_metrics_measures_ack_and_close(seeded):
    from datetime import timedelta

    db, admin = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))

    # 造一条「2 小时后认领」的历史
    event.first_seen_at = now() - timedelta(hours=10)
    event.updated_at = now() - timedelta(hours=8)
    event.acked_by_user_id = admin.id
    event.status = "handled"
    db.flush()

    metrics = assistant_watch.response_metrics(db, days=7)
    assert metrics["events"] == 1
    assert metrics["acked"] == 1
    assert metrics["avg_ack_hours"] == pytest.approx(2.0, rel=0.2)
    assert metrics["ack_within_4h"] == 1.0


def test_response_metrics_counts_stale_open(seeded):
    from datetime import timedelta

    db, _ = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))
    event = db.scalar(select(AssistantEvent))
    event.first_seen_at = now() - timedelta(hours=30)
    event.status = "open"
    db.flush()

    metrics = assistant_watch.response_metrics(db, days=7)
    assert metrics["open_over_24h"] == 1


def test_scan_summary_log_extra_does_not_clash(tmp_path):
    """summary 里的 created 键会让日志格式化器报错，曾把整轮扫描判成失败。

    复现条件：项目的 HumanReadableFormatter 会把 extra 直接塞进 LogRecord，
    `created` 是内置属性，冲突时抛 KeyError。
    """
    import logging

    from app.core.logging import HumanReadableFormatter

    logger = logging.getLogger("tests.watch.extra")
    handler = logging.FileHandler(tmp_path / "log.txt", encoding="utf-8")
    handler.setFormatter(HumanReadableFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        summary = {"evaluated": 3, "created": 1, "resolved": 0, "open": 3,
                   "reminded": 0, "quiet": 0}

        # 裸 summary 会炸 —— 这就是当初的 bug
        with pytest.raises((KeyError, ValueError)):
            logger.info("mes watch scan", extra=summary)

        # 现在 worker 用的写法：包一层 watch
        logger.info("mes watch scan", extra={"watch": summary})
    finally:
        logger.removeHandler(handler)
        handler.close()

    content = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "mes watch scan" in content


def test_digest_only_posts_once_per_day(seeded):
    db, _ = seeded
    assistant_watch.scan(db, client=FakeClient(_snapshot([SO_APPROVED])), today=date(2026, 11, 12))

    first = assistant_watch.post_digest(db, days=1)
    assert first, "第一次应该发"
    second = assistant_watch.post_digest(db, days=1)
    assert second is None, "同一天不该重复发"

    forced = assistant_watch.post_digest(db, days=1, force=True)
    assert forced, "force=True 应强制发送"


def test_manu_missing_material_detected(monkeypatch):
    """有领料申请、没出库、已开工两天以上 → 提示材料账风险。"""
    manu = [{"编号": "MO-M1", "单据状态": "生产中", "开工日期": "2026-11-05"}]
    req = [{"加工单号": "MO-M1", "单据状态": "已审核"}]
    candidates = assistant_watch.evaluate(
        _snap(MANU_ORDER=manu, MATERIAL_REQ=req, MATERIAL_OUT=[]), today=date(2026, 11, 12)
    )
    item = next(c for c in candidates if c.kind == "manu_missing_material")
    assert item.severity == "critical"
    assert item.doc_no == "MO-M1"
    assert "材料未出库" in item.title


def test_manu_missing_material_clears_after_issue():
    manu = [{"编号": "MO-M2", "单据状态": "生产中", "开工日期": "2026-11-05"}]
    req = [{"加工单号": "MO-M2", "单据状态": "已审核"}]
    out = [{"加工单号": "MO-M2", "单据状态": "已审核"}]
    candidates = assistant_watch.evaluate(
        _snap(MANU_ORDER=manu, MATERIAL_REQ=req, MATERIAL_OUT=out), today=date(2026, 11, 12)
    )
    assert not [c for c in candidates if c.kind == "manu_missing_material"]


def test_manu_without_request_is_not_flagged():
    """没提交领料申请的工单不报这条（属于别的流程问题）。"""
    manu = [{"编号": "MO-M3", "单据状态": "生产中", "开工日期": "2026-11-01"}]
    candidates = assistant_watch.evaluate(
        _snap(MANU_ORDER=manu, MATERIAL_REQ=[], MATERIAL_OUT=[]), today=date(2026, 11, 12)
    )
    assert not [c for c in candidates if c.kind == "manu_missing_material"]
