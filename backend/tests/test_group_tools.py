"""群助手的 MES 查询工具：意图识别 + 查询 + 白名单。"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import group_tools


def test_guess_queries_maps_business_words_to_panels():
    assert group_tools.guess_queries("最新的销售订单谁提交的？")[0]["panel"] == "SO_ORDER"
    assert group_tools.guess_queries("包装木箱库存还有多少")[0]["panel"] == "STOCK_STATUS"
    assert group_tools.guess_queries("这批生产工单进度如何")[0]["panel"] == "MANU_ORDER"
    assert group_tools.guess_queries("采购单到货了吗")[0]["panel"] == "PU_ORDER"


def test_guess_queries_extracts_doc_number():
    found = group_tools.guess_queries("SO-20261112-004 谁审的")
    assert found[0]["panel"] == "SO_ORDER"
    assert found[0]["keyword"] == "SO-20261112-004"


def test_guess_queries_ignores_small_talk():
    assert group_tools.guess_queries("你好啊") == []
    assert group_tools.guess_queries("好的收到") == []
    assert group_tools.guess_queries("") == []


def test_query_rejects_unknown_panel():
    result = group_tools.query_business(panel="HACK_PANEL")
    assert not result.ok
    assert "不支持查询" in result.error


def test_query_without_credentials_reports_clearly(monkeypatch):
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
    monkeypatch.setattr(settings, "mes_watch_password", "")
    result = group_tools.query_business(panel="SO_ORDER")
    assert not result.ok
    assert "未配置" in result.error


def test_safe_fields_whitelist_drops_details():
    """单据正文（明细）不能带出去，只保留白名单字段。"""
    row = {
        "单据编号": "SO-1", "客户": "宏远", "提交人": "sales",
        "detail": {"lines": [{"产品": "机架", "金额": 99999}]},
        "金额": 88888,
    }
    safe = group_tools._safe_row("SO_ORDER", row)
    assert "detail" not in safe
    assert "金额" not in safe, "金额不在 SO_ORDER 白名单里"
    assert safe["单据编号"] == "SO-1"


def test_query_filters_by_keyword(monkeypatch):
    class FakeClient:
        def query_panel(self, panel, page_size=20):
            return [
                {"单据编号": "SO-20260101-001", "客户": "A", "提交人": "sales"},
                {"单据编号": "SO-20260202-002", "客户": "B", "提交人": "planner"},
            ]

    monkeypatch.setattr(settings, "mes_open_api_enabled", True)
    result = group_tools.query_business(
        panel="SO_ORDER", keyword="20260202", client=FakeClient()
    )
    assert len(result.rows) == 1
    assert result.rows[0]["单据编号"] == "SO-20260202-002"


def test_render_says_nothing_found_without_lying():
    from app.services.group_tools import QueryResult

    result = QueryResult(panel="STOCK_STATUS", panel_name="库存", keyword="木箱", rows=[])
    text = result.render()
    assert "没有匹配" in text
    assert "木箱" in text


def test_tool_description_lists_panels():
    text = group_tools.tool_description()
    for panel in group_tools.QUERYABLE_PANELS:
        assert panel in text
    assert "不要说「我查不到" in text


# ---------- L2 只读融合：单据追踪 / 详情 / 库存 ----------


def test_guess_lookups_finds_doc_number_for_trace():
    found = group_tools.guess_lookups("SO-20261110-001 卡在哪一步了？")
    assert found and found[0]["kind"] == "trace"
    assert found[0]["doc_no"] == "SO-20261110-001"


def test_guess_lookups_ignores_small_talk_and_plain_stock_words():
    assert group_tools.guess_lookups("你好") == []
    assert group_tools.guess_lookups("好的收到") == []


def test_guess_lookups_routes_stock_question():
    found = group_tools.guess_lookups("CP001 库存还有多少")
    assert found and found[0]["kind"] == "stock"
    assert found[0]["item_code"] == "CP001"


def test_trace_render_shows_stall_and_remaining_steps():
    payload = {
        "单号": "SO-20261110-001",
        "环节数": 39,
        "已完成数": 37,
        "待生成数": 2,
        "当前环节": "SALE_OUT-001",
        "卡点单号": "SALE_OUT-001",
        "卡点原因": "草稿未审核",
        "steps": [
            {"序号": 38, "面板编码": "SALE_OUT", "环节": "销售出库", "单号": "SALE_OUT-001", "单据状态": "草稿"},
            {"序号": 39, "面板编码": "SALE_INVOICE", "环节": "销售发票", "单号": "", "单据状态": "待生成"},
        ],
    }
    text = group_tools.render_trace(payload)
    assert "39 环节" in text
    assert "37" in text and "2" in text
    assert "SALE_OUT-001" in text
    assert "草稿未审核" in text
    assert "待生成" in text


def test_form_render_lists_header_and_detail_rows():
    payload = {
        "panelCode": "SALE_OUT",
        "panelName": "销售出库单",
        "formNo": "SALE_OUT-001",
        "status": "已审核",
        "header": {"单据编号": "SALE_OUT-001", "客户": "华东铝业"},
        "tabs": [
            {"key": "items", "label": "明细", "rows": [{"存货名称": "轴套 C", "数量": 10}]},
        ],
        "link": "/panelx/form/SALE_OUT?code=SALE_OUT-001",
    }
    text = group_tools.render_form(payload)
    assert "销售出库单" in text and "SALE_OUT-001" in text
    assert "客户=华东铝业" in text
    assert "存货名称=轴套 C" in text
    assert "/panelx/form/SALE_OUT?code=SALE_OUT-001" in text


def test_stock_render_shows_totals_and_breakdown():
    payload = {
        "存货编码": "CP001",
        "数量合计": 15.5,
        "金额合计": 65.1,
        "rows": [
            {"仓库": "原料仓", "数量": 10, "金额": 42},
            {"仓库": "成品仓", "数量": 5.5, "金额": 23.1},
        ],
    }
    text = group_tools.render_stock(payload)
    assert "15.5" in text and "65.1" in text
    assert "原料仓" in text and "成品仓" in text
