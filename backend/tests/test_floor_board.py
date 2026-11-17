"""车间现场看板：在制/逾期/停滞/今日报工 的聚合口径。

盯三件事：
- 已完工与已中止的工单不能混进「在制」；
- 没报工不等于停滞，要过了阈值天数才算；
- 报工要按当天筛，昨天的不算今天的产出。
"""

from __future__ import annotations

from datetime import date

from app.services.floor_board import build_floor


def mo(doc_no: str, *, status: str = "已审核", due: str = "", start: str = "",
       qty: float = 10, reported: float = 0, product: str = "轴套 C") -> dict:
    return {
        "编号": doc_no,
        "单据状态": status,
        "生产车间": "熔铸车间",
        "预完工日": due,
        "开工日期": start,
        "detail": {"products": [{"产品名称": product, "数量": qty,
                                 "累计汇报套数(工序单位)": reported}]},
    }


def report(day: str, *, qty: float = 2, process: str = "车削", mo_no: str = "MO-1") -> dict:
    return {
        "单据编号": "GX-1",
        "单据日期": day,
        "detail": {"items": [{"工序名称": process, "报工数量": qty,
                              "工人名称": "张三", "加工单号": mo_no}]},
    }


TODAY = date(2026, 11, 17)


def test_finished_and_cancelled_orders_are_not_in_progress():
    board = build_floor([
        mo("MO-DONE", status="已完工", qty=10, reported=10),
        mo("MO-STOP", status="已中止"),
        mo("MO-OK", status="生产中"),
    ], [], TODAY)
    assert [o["docNo"] for o in board["orders"]] == ["MO-OK"]


def test_overdue_counts_only_past_due_in_progress():
    board = build_floor([
        mo("MO-LATE", due="2026-11-10"),
        mo("MO-FUTURE", due="2026-11-30"),
    ], [], TODAY)
    assert len(board["overdue"]) == 1
    assert board["overdue"][0]["docNo"] == "MO-LATE"
    assert board["overdue"][0]["overdueDays"] == 7


def test_stalled_requires_idle_days_and_zero_reports():
    board = build_floor([
        mo("MO-IDLE", start="2026-11-10", reported=0),      # 闲置 7 天，没报工 → 停滞
        mo("MO-FRESH", start="2026-11-17", reported=0),     # 今天刚开工 → 不算停滞
        mo("MO-REPORTED", start="2026-11-01", reported=5),  # 早就开工但已报工 → 不算停滞
    ], [], TODAY)
    assert [o["docNo"] for o in board["stalled"]] == ["MO-IDLE"]


def test_today_reports_only_count_today():
    board = build_floor([], [
        report("2026-11-17", qty=3),
        report("2026-11-16", qty=99),
    ], TODAY)
    assert board["stats"][1]["value"] == "1"
    assert board["reports"][0]["qty"] == 3
    assert "3.0" in board["stats"][1]["hint"]


def test_progress_is_zero_safe():
    # 计划数量为 0 时不能除零，也不能报出 NaN 给界面
    board = build_floor([mo("MO-ZERO", qty=0, reported=0)], [], TODAY)
    assert board["orders"][0]["progress"] == 0.0


def test_headline_mentions_stalled_and_overdue():
    board = build_floor([
        mo("MO-LATE", due="2026-11-01", start="2026-11-01"),
    ], [], TODAY)
    text = board["headline"]
    assert "1 张工单在制" in text
    assert "已过预完工日" in text        # 逾期要报
    assert "仍未报工" in text            # 停滞要报
