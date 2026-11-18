"""一线看板：什么算「待报工」、什么算「我的产出」。

盯三件事：
- 派工量已报满的工序不能还挂在待办里（否则永远清不掉）；
- 未审核/中止的派工不算活儿；
- 报工没填工人时如实计入「未登记」，不能算到某个人头上。
"""

from __future__ import annotations

from datetime import date

from app.services.my_board import build_mine

TODAY = date(2026, 11, 17)


def dispatch(doc_no: str, *, status: str = "已审核", qty: float = 10,
             process: str = "车削", mo: str = "MO-1") -> dict:
    return {
        "编号": doc_no,
        "单据状态": status,
        "加工单号": mo,
        "产品名称": "轴套 C",
        "detail": {"items": [{"工序名称": process, "派工数量": qty, "加工单号": mo}]},
    }


def report(day: str, *, qty: float = 10, process: str = "车削", mo: str = "MO-1",
           worker: str = "") -> dict:
    return {
        "单据编号": "GX-1",
        "单据日期": day,
        "加工单号": mo,
        "detail": {"items": [{"工序名称": process, "报工数量": qty,
                              "工人名称": worker, "加工单号": mo}]},
    }


def test_fully_reported_dispatch_drops_out_of_pending():
    board = build_mine(
        [dispatch("PG-1", qty=10)],
        [report("2026-11-16", qty=10)],
        today=TODAY,
    )
    assert board["pending"] == []


def test_partially_reported_dispatch_stays_pending():
    board = build_mine(
        [dispatch("PG-1", qty=10)],
        [report("2026-11-16", qty=4)],
        today=TODAY,
    )
    assert len(board["pending"]) == 1
    assert board["pending"][0]["reported"] == 4


def test_unapproved_dispatch_is_not_a_task():
    board = build_mine([dispatch("PG-1", status="草稿"), dispatch("PG-2", status="中止")], [], today=TODAY)
    assert board["pending"] == []


def test_reports_without_worker_are_flagged_not_attributed():
    board = build_mine(
        [],
        [report("2026-11-17", qty=3, worker=""), report("2026-11-17", qty=2, worker="张三")],
        real_name="张三",
        today=TODAY,
    )
    # 只有填了「张三」的那条算我的产出；未填的单独列，不能算到我头上
    assert board["unassigned"] == 1
    assert board["stats"][1]["value"] == "2"
    assert [r["qty"] for r in board["unassignedReports"]] == [3]


def test_other_workers_reports_are_not_mine():
    board = build_mine(
        [],
        [report("2026-11-17", qty=7, worker="李四")],
        real_name="张三",
        today=TODAY,
    )
    assert board["stats"][1]["value"] == "0"
    assert board["unassigned"] == 0


def test_old_reports_fall_outside_window():
    board = build_mine([], [report("2026-10-01", qty=99, worker="张三")], real_name="张三", today=TODAY)
    assert board["stats"][1]["value"] == "0"
