"""阈值改动留痕：谁、什么时候、从多少改到多少、为什么。

阈值直接决定「会不会报警」。把不合格率门槛从 5% 调到 15%，可以是真的工艺稳定了，
也可以是有人嫌报警太吵——事后必须能查。所以这张表不能省。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.models.rule_threshold import RuleThresholdChange
from app.services import rule_thresholds as rt

TZ = timezone.utc


@pytest.fixture
def db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "rules.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    rt.load_overrides(database.SessionLocal())
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def test_default_value_when_never_changed(db):
    assert rt.current().stall_days == 1
    assert all(row["source"] == "default" for row in rt.describe())
    assert "没有被改过" in rt.render_history(db)


def test_set_value_takes_effect_and_records_history(db):
    rt.set_value(db, key="stall_days", value=3, actor="admin", reason="大厂停三天才算事")
    db.commit()

    assert rt.current().stall_days == 3, "改完要马上生效，不用等重启"
    rows = rt.history(db)
    assert len(rows) == 1
    assert rows[0]["actor"] == "admin"
    assert rows[0]["oldValue"] == 1, "记的是改之前的实际值，不是 None"
    assert rows[0]["newValue"] == 3
    assert "大厂停三天" in rows[0]["reason"]


def test_rate_values_are_stored_as_integers(db):
    """比例不能用浮点存：0.07 * 10000 在浮点里不精确。"""
    rt.set_value(db, key="quality_rate", value=0.07, actor="admin")
    db.commit()
    assert rt.current().quality_rate == 0.07
    row = db.scalar(select(RuleThresholdChange))
    assert row.value_kind == "rate"
    assert isinstance(row.new_value, int)


def test_revert_restores_default_and_leaves_trace(db):
    rt.set_value(db, key="stall_days", value=5, actor="admin")
    db.commit()
    rt.revert(db, key="stall_days", actor="admin", reason="恢复默认")
    db.commit()

    assert rt.current().stall_days == 1
    rows = rt.history(db)
    assert rows[0]["sourceTo"] == "reverted"
    assert rows[0]["oldValue"] == 5
    assert rows[0]["newValue"] == 1


def test_overrides_survive_reload(db):
    """重启后覆盖值要还在——否则改了等于没改。"""
    rt.set_value(db, key="approver_min_events", value=5, actor="admin")
    db.commit()

    count = rt.load_overrides(db)
    assert count == 1
    assert rt.current().approver_min_events == 5


def test_invalid_values_rejected(db):
    with pytest.raises(rt.ThresholdError):
        rt.set_value(db, key="quality_rate", value=1.5)   # 比例不能超过 100%
    with pytest.raises(rt.ThresholdError):
        rt.set_value(db, key="stall_days", value=0)       # 计数至少 1
    with pytest.raises(rt.ThresholdError):
        rt.set_value(db, key="not_a_rule", value=1)


def test_history_is_filterable_by_key(db):
    rt.set_value(db, key="stall_days", value=2, actor="a")
    rt.set_value(db, key="quality_rate", value=0.03, actor="b")
    db.commit()
    rows = rt.history(db, key="quality_rate")
    assert len(rows) == 1
    assert rows[0]["key"] == "quality_rate"


def test_changed_threshold_actually_changes_detection(db):
    """留痕之外，还要证明它真的影响规则——否则只是个漂亮的日志。"""
    from datetime import date

    from app.services import production_insight as pi

    snapshot = {
        "MANU_ORDER": [
            {
                "编号": "MO-1",
                "单据状态": "已审核",
                "开工日期": "2026-11-12",
                "detail": {"processes": [{"工序名称": "车削", "工序完工状态": "未完工"}]},
            }
        ],
        "PROCESS_REPORT": [],
    }
    today = date(2026, 11, 13)   # 开工 1 天

    assert [p for p in pi.analyze(snapshot, today=today) if p.kind == "process_stalled"]

    rt.set_value(db, key="stall_days", value=3, actor="admin", reason="别天天报")
    db.commit()
    assert [p for p in pi.analyze(snapshot, today=today) if p.kind == "process_stalled"] == []
