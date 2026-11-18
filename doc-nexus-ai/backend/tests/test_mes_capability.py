"""主对话的 MES 只读能力：流程追踪要细到工序，面板名要和 MES 一致。

这些断言都是「用户能直接感觉到」的行为，不是实现细节：
- 问「卡在哪道工序」，回答里必须真的出现工序名，不能只说「17 个环节」；
- 说话得用 MES 页面上的名字（生产加工单），不能自己发明（生产工单）。
"""

from __future__ import annotations

import pytest

from app.services import mes_capability
from app.services.agent_capability import CAPABILITY_TOOLS, CHAT_CAPABILITY_TOOLS
from app.services.group_tools import QUERYABLE_PANELS


TRACE_PAYLOAD = {
    "单号": "SO-20261112-003",
    "环节数": 6,
    "已完成数": 4,
    "待生成数": 2,
    "当前环节": "FI-20261112-005",
    "加工单号": "SO-20261112-003-1",
    "卡点单号": "PG-20261112-003",
    "卡点原因": "排产单未审核",
    "提示": "",
    "route": [
        {"序号": 1, "面板编码": "SO_ORDER", "环节": "销售订单", "工序名称": "",
         "单号": "SO-20261112-003", "单据状态": "已审核", "是否已发生": True, "是否卡点": False},
        {"序号": 2, "面板编码": "DISPATCH", "环节": "工单排产", "工序名称": "车削",
         "单号": "PG-20261112-002", "单据状态": "已审核", "是否已发生": True, "是否卡点": False},
        {"序号": 3, "面板编码": "DISPATCH", "环节": "工单排产", "工序名称": "钻孔",
         "单号": "PG-20261112-003", "单据状态": "待审核", "是否已发生": True, "是否卡点": True},
        {"序号": 4, "面板编码": "DISPATCH", "环节": "工单排产", "工序名称": "抛光",
         "单号": "PG-20261112-004", "单据状态": "已审核", "是否已发生": True, "是否卡点": False},
        {"序号": 5, "面板编码": "PROCESS_REPORT", "环节": "工序汇报单", "工序名称": "车削",
         "单号": "GX-1", "单据状态": "已审核", "是否已发生": True, "是否卡点": False},
        {"序号": 6, "面板编码": "FINISH_IN", "环节": "产成品入库单", "工序名称": "",
         "单号": "", "单据状态": "待生成", "是否已发生": False, "是否卡点": False},
    ],
    "steps": [
        {"序号": 2, "面板编码": "DISPATCH", "环节": "工单排产", "工序名称": "车削",
         "单号": "PG-20261112-002", "单据状态": "已审核", "是否卡点": False},
        {"序号": 3, "面板编码": "DISPATCH", "环节": "工单排产", "工序名称": "钻孔",
         "单号": "PG-20261112-003", "单据状态": "待审核", "是否卡点": True},
    ],
    "lanes": [],
}


class FakeClient:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else TRACE_PAYLOAD

    def trace(self, doc_no: str):
        return self.payload


@pytest.fixture(autouse=True)
def unrestricted_viewer(monkeypatch):
    # 这些用例验证排版输出，不验证权限；统一放行以免探测逻辑干扰断言。
    from app.services import mes_permissions

    viewer = mes_permissions.MesViewer(
        token="t", is_admin=True, panels=set(), real_name="t", loaded_at=0.0
    )
    monkeypatch.setattr(mes_permissions, "viewer_of", lambda token: viewer)

@pytest.fixture
def fake_client(monkeypatch):
    def install(payload=None):
        client = FakeClient(payload)
        monkeypatch.setattr(mes_capability, "_client", lambda token="": client)
        return client

    return install


def test_trace_reports_process_names_not_just_counts(fake_client):
    """「卡在哪道工序」必须能答到工序名。"""
    fake_client()
    text = mes_capability.trace_doc({"doc_no": "SO-20261112-003"})["text"]
    assert "共 6 个环节" in text
    assert "〔钻孔〕" in text, "路线上要带工序名"
    assert "工序级进展" in text
    # 钻孔只有一张排产单且未审核，所以是 0/1
    assert "钻孔：0/1" in text


def test_trace_marks_stalled_step(fake_client):
    fake_client()
    text = mes_capability.trace_doc({"doc_no": "SO-20261112-003"})["text"]
    assert "⚠" in text
    assert "卡点：PG-20261112-003（排产单未审核）" in text


def test_trace_marks_pending_steps(fake_client):
    fake_client()
    text = mes_capability.trace_doc({"doc_no": "SO-20261112-003"})["text"]
    assert "○ 6. 产成品入库单" in text


def test_trace_drops_duplicate_hint(fake_client):
    """MES 的提示常常只是重复环节数，别让模型读到两个数字。"""
    payload = dict(TRACE_PAYLOAD, 提示="6 个环节")
    fake_client(payload)
    text = mes_capability.trace_doc({"doc_no": "SO-20261112-003"})["text"]
    assert "提示：" not in text


def test_trace_requires_doc_no(fake_client):
    fake_client()
    with pytest.raises(mes_capability.MesCapabilityError):
        mes_capability.trace_doc({"doc_no": "  "})


def test_trace_returns_structured_route_for_ui(fake_client):
    fake_client()
    result = mes_capability.trace_doc({"doc_no": "SO-20261112-003"})
    assert result["total"] == 6
    assert result["done"] == 4
    assert result["stalled"] == "PG-20261112-003"
    assert len(result["route"]) == 6


def test_unknown_panel_is_rejected_with_options(fake_client):
    fake_client()
    with pytest.raises(mes_capability.MesCapabilityError) as exc:
        mes_capability.query_panel({"panel": "NOT_A_PANEL"})
    assert "SO_ORDER" in str(exc.value)


def test_panel_names_follow_mes_page_names():
    """MES 页面上叫「生产加工单」，AI 不能自己改叫「生产工单」。"""
    assert QUERYABLE_PANELS["MANU_ORDER"]["name"] == "生产加工单"
    assert QUERYABLE_PANELS["STOCK_STATUS"]["name"] == "库存状况表"


def test_chat_grant_includes_mes_tools():
    """主对话的授权里必须真有这四条，否则模型根本看不见。"""
    for tool in ("mes_query_panel", "mes_trace_doc", "mes_doc_detail", "mes_stock"):
        assert tool in CHAT_CAPABILITY_TOOLS
        assert tool in CAPABILITY_TOOLS