"""待办关联单据与跳转链接。

要保证三件事：
1. 审批类待办只可能分给「MES 说他有这个面板审批权」的人；
2. 待办里记下是哪张单据、哪个面板，前端才能拼出能点的链接；
3. 同一批单据的建议不要重复三遍。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.assistant_watch import AssistantEvent
from app.models.group_chat import GroupTodo
from app.services import assistant_watch
from app.services.assistant_watch import Candidate, evaluate_approvals

TZ = timezone.utc


@pytest.fixture
def refs_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "todo-refs.sqlite3"
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


def _event(db: Session, *, panel: str, doc: str, title: str, action: str = "") -> AssistantEvent:
    row = AssistantEvent(
        id=f"evt_{doc}",
        dedupe_key=f"{panel}:{doc}",
        kind="pending_approval",
        source="mes",
        panel_code=panel,
        doc_no=doc,
        title=title,
        detail="",
        suggested_action=action,
        severity="warning",
        status="open",
        target_usernames_json="[]",
        target_user_ids_json="[]",
        payload_json=json.dumps({"panelName": "其他入库单"}, ensure_ascii=False),
        created_at=datetime.now(TZ),
        updated_at=datetime.now(TZ),
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- 审批人


def test_approval_only_targets_the_person_mes_named():
    """审批人只能来自 MES 返回的 approver，这里不许自己推断。"""
    candidates = evaluate_approvals(
        [
            ("admin", {"panelCode": "OTHER_IN", "formNo": "OTHE-1", "panelName": "其他入库单"}),
            ("keeper", {"panelCode": "OTHER_IN", "formNo": "OTHE-2", "panelName": "其他入库单"}),
        ]
    )
    assert [c.targets for c in candidates] == [["admin"], ["keeper"]]
    assert all(c.kind == "pending_approval" for c in candidates)


def test_approval_candidate_is_marked_exact():
    """exact=True 表示来自开放接口，解析不到账号时不应该乱派。"""
    (candidate,) = evaluate_approvals(
        [("admin", {"panelCode": "OTHER_IN", "formNo": "OTHE-1", "panelName": "其他入库单"})]
    )
    assert candidate.exact is True
    assert candidate.panel_code == "OTHER_IN"
    assert candidate.doc_no == "OTHE-1"


def test_approval_drops_rows_without_approver():
    assert evaluate_approvals([("", {"formNo": "OTHE-1"})]) == []


# ---------------------------------------------------------------- 关联单据


def test_refs_recorded_on_todo(refs_db):
    event = _event(
        refs_db, panel="OTHER_IN", doc="OTHE-20261112-008",
        title="其他入库单 OTHE-20261112-008 待 admin 审批",
    )
    refs = json.loads(assistant_watch._refs_of([event]))
    assert refs == [{"panel": "OTHER_IN", "docNo": "OTHE-20261112-008", "label": "其他入库单"}]


def test_refs_deduplicate_same_document(refs_db):
    event = _event(refs_db, panel="OTHER_IN", doc="OTHE-1", title="单据 OTHE-1")
    refs = json.loads(assistant_watch._refs_of([event, event]))
    assert len(refs) == 1


def test_refs_skip_rows_without_doc_no(refs_db):
    row = _event(refs_db, panel="SO_ORDER", doc="SO-1", title="无单号")
    row.doc_no = None
    refs_db.flush()
    assert json.loads(assistant_watch._refs_of([row])) == []


# ---------------------------------------------------------------- 链接


def test_todo_response_builds_mes_link(monkeypatch):
    from app.api.group_chat import _todo_refs

    monkeypatch.setattr(settings, "mes_watch_ui_url", "http://localhost:4173", raising=False)
    todo = GroupTodo(
        id="gtodo_link", group_id=None, assignee_user_id="u1", origin="ai",
        title="x", detail="", priority="normal", status="pending",
        refs_json=json.dumps(
            [{"panel": "OTHER_IN", "docNo": "OTHE-20261112-008", "label": "其他入库单"}],
            ensure_ascii=False,
        ),
        created_at=datetime.now(TZ), updated_at=datetime.now(TZ),
    )
    refs = _todo_refs(todo)
    assert refs and refs[0]["url"] == (
        "http://localhost:4173/#/panelx/form/OTHER_IN?code=OTHE-20261112-008"
    )
    assert refs[0]["label"] == "其他入库单"


def test_todo_response_skips_link_when_ui_url_missing(monkeypatch):
    from app.api.group_chat import _todo_refs

    monkeypatch.setattr(settings, "mes_watch_ui_url", "", raising=False)
    todo = GroupTodo(
        id="gtodo_nolink", group_id=None, assignee_user_id="u1", origin="ai",
        title="x", detail="", priority="normal", status="pending",
        refs_json=json.dumps([{"panel": "OTHER_IN", "docNo": "OTHE-1"}]),
        created_at=datetime.now(TZ), updated_at=datetime.now(TZ),
    )
    assert _todo_refs(todo) == []


# ---------------------------------------------------------------- 明细去重


def test_detail_dedupes_identical_suggestions(refs_db):
    rows = [
        _event(refs_db, panel="OTHER_IN", doc=f"OTHE-{i}", title=f"其他入库单 OTHE-{i} 待 admin 审批",
               action="在 MES 打开该单据完成审批或退回")
        for i in (1, 2, 3)
    ]
    detail = assistant_watch._detail_of(rows)
    assert detail.count("建议：") == 1


def test_detail_does_not_repeat_doc_number(refs_db):
    event = _event(
        refs_db, panel="OTHER_IN", doc="OTHE-20261112-008",
        title="其他入库单 OTHE-20261112-008 待 admin 审批",
    )
    detail = assistant_watch._detail_of([event])
    assert detail.count("OTHE-20261112-008") == 1