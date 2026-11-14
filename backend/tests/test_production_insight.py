"""生产执行层：发现「单据状态看不出来」的问题。

这些用例都来自真实数据里踩到的形状：
- 工单写着「生产中」，但三道工序一道都没报工；
- 汇报单里某道工序不合格率偏高；
- 工序上挂着待返修数量。

它们共同点是：只看单据状态永远发现不了，必须下沉到工序。
"""

from __future__ import annotations

from datetime import date

from app.services import production_insight as pi


TODAY = date(2026, 11, 13)


def _manu(**over):
    row = {
        "编号": "MO-1",
        "单据状态": "已审核",
        "开工日期": "2026-11-11",
        "预完工日": "2026-11-20",
        "客户": "中天精工",
        "detail": {
            "processes": [
                {"工序名称": "车削", "工序完工状态": "完工", "计划数量": 10},
                {"工序名称": "钻孔", "工序完工状态": "未完工", "计划数量": 10},
                {"工序名称": "抛光", "工序完工状态": "未完工", "计划数量": 10},
            ]
        },
    }
    row.update(over)
    return row


def _report(**over):
    row = {
        "编号": "GX-1",
        "单据状态": "已审核",
        "加工单号": "MO-1",
        "detail": {
            "items": [
                {"工序名称": "车削", "计划数量": 10, "报工数量": 10, "合格数量": 10, "不合格数量": 0},
                {"工序名称": "钻孔", "计划数量": 10, "报工数量": 0, "合格数量": 0, "不合格数量": 0},
            ]
        },
    }
    row.update(over)
    return row


def test_stalled_process_is_detected_when_no_report():
    """「生产中」+ 工序未完工 + 无报工 = 车间真的停了。"""
    problems = pi.analyze(
        {"MANU_ORDER": [_manu()], "PROCESS_REPORT": [_report()]},
        today=TODAY,
    )
    stalled = [p for p in problems if p.kind == "process_stalled"]
    assert len(stalled) == 1
    problem = stalled[0]
    # 标题指名「第一道卡住的工序」，后面排不上的写在详情里
    assert "钻孔" in problem.title
    assert "抛光" in problem.detail
    assert "车削" not in problem.title, "已完工的工序不该出现在停滞名单里"
    assert problem.steps, "发现问题还不够，要给处置步骤"
    assert problem.impact, "要说清影响（停几天、多少件）"


def test_reported_but_unfinished_is_not_stalled():
    """报过工只是没完工，属于「落后」不算「停滞」，别误报。"""
    report = _report()
    report["detail"]["items"][1]["报工数量"] = 6  # 钻孔报过 6 件
    problems = pi.analyze(
        {"MANU_ORDER": [_manu()], "PROCESS_REPORT": [report]}, today=TODAY
    )
    stalled = [p for p in problems if p.kind == "process_stalled"]
    # 钻孔报过工 → 不算停滞；抛光从没报过 → 仍然要报
    assert len(stalled) == 1
    assert "抛光" in stalled[0].title
    assert "钻孔" not in stalled[0].title


def test_finished_order_is_ignored():
    manu = _manu(单据状态="已完工")
    problems = pi.analyze({"MANU_ORDER": [manu], "PROCESS_REPORT": []}, today=TODAY)
    assert problems == []


def test_severity_escalates_with_days():
    """停得越久越严重，这是「主动提醒」的意义。"""
    def severity(days: int) -> str:
        manu = _manu()
        manu["开工日期"] = f"2026-11-{13 - days:02d}"
        problems = pi.analyze({"MANU_ORDER": [manu], "PROCESS_REPORT": []}, today=TODAY)
        return problems[0].severity

    assert severity(1) == "info"
    assert severity(2) == "warning"
    assert severity(4) == "critical"


def test_quality_rate_flags_bad_process():
    report = _report()
    report["detail"]["items"] = [
        {"工序名称": "钻孔", "计划数量": 20, "报工数量": 20, "合格数量": 17, "不合格数量": 3}
    ]
    problems = pi.analyze({"MANU_ORDER": [], "PROCESS_REPORT": [report]}, today=TODAY)
    quality = [p for p in problems if p.kind == "quality_rate"]
    assert len(quality) == 1
    assert "15%" in quality[0].title
    assert quality[0].severity == "critical"


def test_quality_rate_ignores_tiny_sample():
    """1 件里坏 1 件是 100%，但样本太小，不能当趋势报。"""
    report = _report()
    report["detail"]["items"] = [
        {"工序名称": "钻孔", "计划数量": 1, "报工数量": 1, "合格数量": 0, "不合格数量": 1}
    ]
    problems = pi.analyze({"MANU_ORDER": [], "PROCESS_REPORT": [report]}, today=TODAY)
    assert [p for p in problems if p.kind == "quality_rate"] == []


def test_rework_pending_is_reported():
    manu = _manu()
    manu["detail"]["processes"][1]["待返修数量-本序发现"] = 3
    problems = pi.analyze({"MANU_ORDER": [manu], "PROCESS_REPORT": []}, today=TODAY)
    rework = [p for p in problems if p.kind == "rework_pending"]
    assert len(rework) == 1
    assert "3" in rework[0].title


# ---------------------------------------------------------------- 计划层


def _event(**over):
    base = {
        "kind": "so_unstarted",
        "title": "销售订单 SO-1 未安排生产",
        "severity": "warning",
        "age_hours": 5.0,
    }
    base.update(over)
    return base


def test_plan_puts_stale_before_fresh():
    """拖了两天的预警，要压过刚出现的预警；不能只看谁更响。"""
    plan = pi.plan_day([
        _event(title="新发现的问题", severity="warning", age_hours=1),
        _event(kind="process_stalled", title="停滞三天", severity="warning", age_hours=72),
    ])
    assert plan["top"][0]["title"] == "停滞三天"


def test_plan_merges_same_kind():
    """三张单都「未安排生产」是一个动作，不该占三条。"""
    plan = pi.plan_day(
        [_event(title=f"销售订单 SO-{i} 未安排生产") for i in (1, 2, 3)],
        kind_labels={"so_unstarted": "张销售订单待安排生产"},
    )
    assert len(plan["top"]) == 1
    assert plan["top"][0]["count"] == 3
    assert "3 张销售订单待安排生产" in plan["top"][0]["title"]


def test_plan_explains_why_urgent():
    """计划要写清影响和步骤，否则用户还是不知道怎么动手。"""
    plan = pi.plan_day([
        _event(
            kind="process_stalled",
            title="钻孔停滞 3 天",
            severity="critical",
            age_hours=72,
            impact="15 件未产出",
            steps=["问车间缺什么人", "定恢复时间"],
        )
    ])
    summary = plan["summary"]
    assert "影响：15 件未产出" in summary
    assert "1) 问车间缺什么人" in summary
    assert "已经拖了 3 天" in summary


def test_plan_empty_day_is_quiet():
    plan = pi.plan_day([])
    assert plan["top"] == []
    assert "没有" in plan["summary"]

# ---------------------------------------------------------------- 交期风险


def _manu_due(due: str, status: str = "已审核"):
    row = _manu(单据状态=status, 预完工日=due)
    return row


def test_delivery_risk_flags_overdue_order():
    """已经过了交期还没完工，是最硬的风险。"""
    problems = pi.analyze(
        {"MANU_ORDER": [_manu_due("2026-11-10")], "PROCESS_REPORT": []}, today=TODAY
    )
    risk = [p for p in problems if p.kind == "delivery_risk"]
    assert len(risk) == 1
    assert risk[0].severity == "critical"
    assert "已过交期" in risk[0].title


def test_delivery_risk_flags_behind_schedule():
    """交期还没到，但按进度赶不上——这才是计划岗该被提醒的时点。"""
    manu = _manu_due("2026-11-15")
    manu["开工日期"] = "2026-11-05"      # 已过 8 天
    manu["detail"]["processes"] = [
        {"工序名称": "车削", "工序完工状态": "完工", "计划数量": 10},
        {"工序名称": "钻孔", "工序完工状态": "未完工", "计划数量": 10},
        {"工序名称": "抛光", "工序完工状态": "未完工", "计划数量": 10},
    ]
    problems = pi.analyze({"MANU_ORDER": [manu], "PROCESS_REPORT": []}, today=TODAY)
    risk = [p for p in problems if p.kind == "delivery_risk"]
    assert len(risk) == 1
    assert risk[0].severity == "warning"
    assert "赶不上" in risk[0].title


def test_delivery_risk_falls_back_to_sales_order_due():
    """加工单没填预完工日时，用销售订单的交期——否则这条规则永远不触发。"""
    manu = _manu_due("")
    manu["销售订单号"] = "SO-1"
    so = {"单据编号": "SO-1", "预计交货日期": "2026-11-10"}
    problems = pi.analyze(
        {"MANU_ORDER": [manu], "SO_ORDER": [so], "PROCESS_REPORT": []}, today=TODAY
    )
    assert [p for p in problems if p.kind == "delivery_risk"], "应当按销售订单交期判定"


def test_delivery_risk_ignores_finished_order():
    manu = _manu_due("2026-11-10", status="已完工")
    problems = pi.analyze({"MANU_ORDER": [manu], "PROCESS_REPORT": []}, today=TODAY)
    assert [p for p in problems if p.kind == "delivery_risk"] == []


# ---------------------------------------------------------------- 跨单趋势


def _report_with(manu: str, process: str, reported: float, bad: float) -> dict:
    return {
        "编号": f"GX-{manu}",
        "单据状态": "已审核",
        "加工单号": manu,
        "detail": {
            "items": [
                {
                    "工序名称": process,
                    "计划数量": reported,
                    "报工数量": reported,
                    "合格数量": reported - bad,
                    "不合格数量": bad,
                }
            ]
        },
    }


def test_process_trend_across_orders():
    """同一道工序跨三张单都偏高，说明是工序本身的问题。"""
    reports = [
        _report_with("MO-1", "车削", 10, 1),
        _report_with("MO-2", "车削", 10, 1),
        _report_with("MO-3", "车削", 10, 1),
    ]
    problems = pi.analyze({"MANU_ORDER": [], "PROCESS_REPORT": reports}, today=TODAY)
    trend = [p for p in problems if p.kind == "process_trend"]
    assert len(trend) == 1
    assert "车削" in trend[0].title
    assert "3 张单" in trend[0].title


def test_process_trend_needs_multiple_orders():
    """只有一张单不合格，可能只是这批料，不该上升成工序问题。"""
    reports = [_report_with("MO-1", "车削", 30, 3)]
    problems = pi.analyze({"MANU_ORDER": [], "PROCESS_REPORT": reports}, today=TODAY)
    assert [p for p in problems if p.kind == "process_trend"] == []


def test_process_trend_ignores_small_sample():
    reports = [
        _report_with("MO-1", "车削", 2, 1),
        _report_with("MO-2", "车削", 2, 1),
    ]
    problems = pi.analyze({"MANU_ORDER": [], "PROCESS_REPORT": reports}, today=TODAY)
    assert [p for p in problems if p.kind == "process_trend"] == []


# ---------------------------------------------------------------- 卡在报工环节


def _manu_with_processes(names: list[str]) -> dict:
    manu = _manu()
    manu["detail"]["processes"] = [
        {"工序名称": name, "工序完工状态": "未完工", "计划数量": 10}
        for name in names
    ]
    return manu


def _dispatch(manu_no: str, processes: list[str]) -> dict:
    return {
        "加工单号": manu_no,
        "编号": f"PG-{manu_no}",
        "单据状态": "已审核",
        "detail": {"items": [{"工序名称": name, "计划数量": 10} for name in processes]},
    }


def test_stall_says_report_sheet_missing():
    """工序是串行的：第一道汇报单都没建，后面几道排不上。"""
    manu = _manu_with_processes(["车削", "钻孔", "抛光"])
    problems = pi.analyze(
        {
            "MANU_ORDER": [manu],
            "PROCESS_REPORT": [],
            "DISPATCH": [_dispatch("MO-1", ["车削"])],
        },
        today=TODAY,
    )
    stalled = [p for p in problems if p.kind == "process_stalled"]
    assert len(stalled) == 1
    problem = stalled[0]
    assert "汇报单还没建" in problem.title
    assert "车削" in problem.title
    assert "钻孔、抛光" in problem.detail, "要说清后面几道排不上"
    assert "建单" in problem.steps[0]


def test_stall_says_report_sheet_empty():
    """汇报单建了但没数量，是漏填，不是没干。"""
    manu = _manu_with_processes(["车削", "钻孔"])
    report = _report()
    report["detail"]["items"] = [{"工序名称": "车削", "报工数量": 0, "计划数量": 10}]
    problems = pi.analyze(
        {"MANU_ORDER": [manu], "PROCESS_REPORT": [report], "DISPATCH": []},
        today=TODAY,
    )
    stalled = [p for p in problems if p.kind == "process_stalled"]
    assert stalled and "没有数量" in stalled[0].title
    assert "补录" in stalled[0].steps[1]


def test_stall_skips_process_with_actual_output():
    """报了工只是没完工，属于落后，不该报成停滞。"""
    manu = _manu_with_processes(["车削"])
    report = _report()
    report["detail"]["items"] = [{"工序名称": "车削", "报工数量": 6, "计划数量": 10}]
    problems = pi.analyze(
        {"MANU_ORDER": [manu], "PROCESS_REPORT": [report], "DISPATCH": []},
        today=TODAY,
    )
    assert [p for p in problems if p.kind == "process_stalled"] == []


def test_stall_reports_blocked_downstream_count():
    """后续没排产的工序要数出来，这是「永远排不上」的证据。"""
    manu = _manu_with_processes(["车削", "钻孔", "抛光", "检验"])
    problems = pi.analyze(
        {
            "MANU_ORDER": [manu],
            "PROCESS_REPORT": [],
            "DISPATCH": [_dispatch("MO-1", ["车削"])],
        },
        today=TODAY,
    )
    stalled = [p for p in problems if p.kind == "process_stalled"][0]
    assert "3 道后续工序排不上" in stalled.impact


# ---------------------------------------------------------------- 领料规格不符


def _stock(code: str, name: str, spec: str, warehouse: str, qty: float) -> dict:
    return {"存货编码": code, "存货": name, "规格型号": spec, "仓库": warehouse, "数量": qty}


def _req_item(code: str, name: str, spec: str, warehouse: str, need: float, manu: str) -> dict:
    return {
        "材料编码": code,
        "材料名称": name,
        "规格型号": spec,
        "仓库": warehouse,
        "数量": need,
        "累计领料数量": 0,
        "加工单号": manu,
    }


def _req(*items: dict) -> dict:
    return {"单据编号": "LL-1", "单据状态": "已审核", "detail": {"items": list(items)}}


def test_material_spec_flags_wrong_warehouse():
    """要的规格在别的仓：不是没料，是仓不对——告诉人去哪调拨。"""
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu(单据状态="已审核")],
            "MATERIAL_REQ": [_req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 45, "MO-1"))],
            "STOCK_STATUS": [
                _stock("CL001", "45#圆钢", "Φ40", "原料仓", 5693),
                _stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820),
            ],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert len(found) == 1
    assert "原料仓没有" in found[0].title
    assert "委外仓1" in found[0].suggested_action
    assert found[0].severity == "critical", "未完工的工单现在就卡着"


def test_material_spec_flags_missing_spec():
    """整个库存都没有这个规格：要么补料，要么 BOM 挂错了。"""
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu(单据状态="已审核")],
            "MATERIAL_REQ": [_req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 45, "MO-1"))],
            "STOCK_STATUS": [_stock("CL001", "45#圆钢", "Φ40", "原料仓", 5693)],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert len(found) == 1
    assert "没有这个规格" in found[0].title
    assert "BOM" in found[0].steps[0]


def test_material_spec_silent_when_stock_is_enough():
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu()],
            "MATERIAL_REQ": [_req(_req_item("CL004", "切削液", "20L/桶", "辅料仓", 1.02, "MO-1"))],
            "STOCK_STATUS": [_stock("CL004", "切削液", "20L/桶", "辅料仓", 305)],
        },
        today=TODAY,
    )
    assert [p for p in problems if p.kind == "material_spec"] == []


def test_material_spec_groups_multiple_requisitions():
    """同一物料规格的多张领料单合成一条，别一张单刷一条。"""
    problems = pi.analyze(
        {
            "MANU_ORDER": [],
            "MATERIAL_REQ": [
                _req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 1.85, "MO-1")),
                _req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 45.3, "MO-2")),
            ],
            "STOCK_STATUS": [_stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820)],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert len(found) == 1
    assert "MO-1" in found[0].detail and "MO-2" in found[0].detail


def test_material_spec_on_finished_order_is_only_info():
    """已完工的单说明当初是黑料生产的，属于数据问题，别当紧急事件。"""
    manu = _manu(单据状态="已完工", 编号="MO-1")
    problems = pi.analyze(
        {
            "MANU_ORDER": [manu],
            "MATERIAL_REQ": [_req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 1.85, "MO-1"))],
            "STOCK_STATUS": [_stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820)],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert found and found[0].severity == "info"


def _material_row(manu: str, code: str, name: str, spec: str, wh: str, need: float,
                  status: str = "已审核") -> dict:
    return {
        "单据编号": manu,
        "单据状态": status,
        "材料编码": code,
        "材料名称": name,
        "规格型号": spec,
        "预出仓库": wh,
        "需用数量": need,
    }


def test_material_spec_covers_order_without_requisition():
    """倒冲领料 / 还没开领料单的工单，材料需求在「工单材料明细」里。

    这条是盲区：只看领料申请的话，这类单子缺料永远发现不了。
    """
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu(单据状态="已审核", 编号="MO-NOREQ")],
            "MATERIAL_REQ": [],
            "MANU_ORDER_MATERIAL_DETAIL": [
                _material_row("MO-NOREQ", "CL001", "45#圆钢", "Φ60", "原料仓", 3.0)
            ],
            "STOCK_STATUS": [
                _stock("CL001", "45#圆钢", "Φ40", "原料仓", 5693),
                _stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820),
            ],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert len(found) == 1
    assert "MO-NOREQ" in found[0].detail
    assert found[0].severity == "critical"


def test_material_spec_does_not_double_count_requisition_and_detail():
    """同一张工单既有领料申请又有材料明细，只算一次。"""
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu(单据状态="已审核")],
            "MATERIAL_REQ": [_req(_req_item("CL001", "45#圆钢", "Φ60", "原料仓", 3.0, "MO-1"))],
            "MANU_ORDER_MATERIAL_DETAIL": [
                _material_row("MO-1", "CL001", "45#圆钢", "Φ60", "原料仓", 3.0)
            ],
            "STOCK_STATUS": [_stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820)],
        },
        today=TODAY,
    )
    found = [p for p in problems if p.kind == "material_spec"]
    assert len(found) == 1
    assert "需求合计 3" in found[0].detail, "不能算成 6"


def test_material_spec_skips_finished_orders_from_detail():
    """已完工/已中止的工单不该再按材料明细报警。"""
    problems = pi.analyze(
        {
            "MANU_ORDER": [_manu(单据状态="已完工")],
            "MATERIAL_REQ": [],
            "MANU_ORDER_MATERIAL_DETAIL": [
                _material_row("MO-1", "CL001", "45#圆钢", "Φ60", "原料仓", 3.0, status="已中止")
            ],
            "STOCK_STATUS": [_stock("CL001", "45#圆钢", "Φ60", "委外仓1", 820)],
        },
        today=TODAY,
    )
    assert [p for p in problems if p.kind == "material_spec"] == []


# ---------------------------------------------------------------- 阈值可配


def test_thresholds_come_from_settings(monkeypatch):
    """改配置就改行为：这条是「可配置」的全部意义。"""
    from app.core.config import settings

    manu = _manu(单据状态="已审核")
    manu["开工日期"] = "2026-11-12"      # 距 TODAY 1 天
    snapshot = {"MANU_ORDER": [manu], "PROCESS_REPORT": []}

    # 默认门槛 1 天 → 开工 1 天就报（info）
    assert [p for p in pi.analyze(snapshot, today=TODAY) if p.kind == "process_stalled"]

    # 门槛抬到 3 天 → 同样过 1 天，就不报了
    monkeypatch.setattr(settings, "mes_rule_stall_days", 3)
    assert [p for p in pi.analyze(snapshot, today=TODAY) if p.kind == "process_stalled"] == []


def test_quality_threshold_is_configurable(monkeypatch):
    from app.core.config import settings

    report = _report()
    report["detail"]["items"] = [
        {"工序名称": "钻孔", "计划数量": 40, "报工数量": 40, "合格数量": 39, "不合格数量": 1}
    ]
    snapshot = {"MANU_ORDER": [], "PROCESS_REPORT": [report]}

    # 1/40 = 2.5%，在 5% 门槛下不报
    assert [p for p in pi.analyze(snapshot, today=TODAY) if p.kind == "quality_rate"] == []

    # 门槛降到 2% 就报
    monkeypatch.setattr(settings, "mes_rule_quality_rate", 0.02)
    assert [p for p in pi.analyze(snapshot, today=TODAY) if p.kind == "quality_rate"]


def test_threshold_registry_lists_every_rule():
    from app.services import rule_thresholds as rt

    rows = rt.describe()
    keys = {row["key"] for row in rows}
    assert {"stall_days", "quality_rate", "trend_rate", "approver_min_events"} <= keys
    # 每一项都要能告诉人怎么改
    assert all(row["env"].startswith("MES_RULE_") for row in rows)
    assert "工序停滞门槛" in rt.render_table()
