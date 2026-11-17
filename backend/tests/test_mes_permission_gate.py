"""MES 工具必须按调用者账号裁数据。

这条线是安全边界，不是样式问题：
AI 侧工具早期用系统级账号查数，等于任何提问的人都能看到全部面板
（2026-11-17 实测：worker 账号能查到销售订单，而 MES 里他只有 10 个面板、不含销售订单）。
下面每个断言都对应一个真实可复现的越权路径。
"""

from __future__ import annotations

import pytest

from app.services import mes_capability, mes_permissions
from app.services.mes_capability import MesCapabilityError


def _viewer(is_admin: bool, panels: set[str]) -> mes_permissions.MesViewer:
    return mes_permissions.MesViewer(
        token="t", is_admin=is_admin, panels=panels, real_name="t", loaded_at=0.0
    )


@pytest.fixture
def as_worker(monkeypatch):
    """车间主任：只可见生产与库存，看不到销售/采购。"""
    viewer = _viewer(False, {"MANU_ORDER", "STOCK_STATUS", "PROCESS_REPORT"})
    monkeypatch.setattr(mes_permissions, "viewer_of", lambda token: viewer)
    return viewer


@pytest.fixture
def as_admin(monkeypatch):
    viewer = _viewer(True, set())
    monkeypatch.setattr(mes_permissions, "viewer_of", lambda token: viewer)
    return viewer


def test_missing_token_is_rejected(monkeypatch):
    """没有登录态时绝不退回系统账号。"""
    with pytest.raises(MesCapabilityError) as err:
        mes_capability.query_panel({"panel": "SO_ORDER"}, token="")
    assert "登录态" in str(err.value)


def test_worker_cannot_query_sales_order(as_worker, monkeypatch):
    """越权路径一：直接查销售订单列表。"""
    called = {"hit": False}

    def fake_query(**kwargs):
        called["hit"] = True
        raise AssertionError("不该走到取数")

    monkeypatch.setattr(mes_capability, "query_business", fake_query)
    with pytest.raises(MesCapabilityError) as err:
        mes_capability.query_panel({"panel": "SO_ORDER"}, token="t")
    assert "没有「SO_ORDER」的查看权限" in str(err.value)
    assert called["hit"] is False, "无权限时不得访问数据源"


def test_admin_can_query_sales_order(as_admin, monkeypatch):
    """管理员不受面板白名单限制。"""
    class _Res:
        error = ""
        panel_name = "销售订单"
        rows = [{"单据编号": "SO-1"}]

    monkeypatch.setattr(
        mes_capability, "query_business",
        lambda **kwargs: _Res(),
    )
    out = mes_capability.query_panel({"panel": "SO_ORDER"}, token="t")
    assert "销售订单" in out["text"]


def test_worker_can_query_allowed_panel(as_worker, monkeypatch):
    """有权面板照常放行：权限收紧不能把正常工作也挡掉。"""
    class _Res:
        error = ""
        panel_name = "生产加工单"
        rows = [{"编号": "MO-1"}]

    monkeypatch.setattr(
        mes_capability, "query_business",
        lambda **kwargs: _Res(),
    )
    out = mes_capability.query_panel({"panel": "MANU_ORDER"}, token="t")
    assert "生产加工单" in out["text"]


def test_doc_detail_uses_panel_code_from_mes(as_worker, monkeypatch):
    """越权路径二：详情接口传空 panel，但 MES 返回 panelCode 是销售订单。

    MES 的表单接口不按 token 裁面板，所以必须用返回的 panelCode 事后校验。
    """
    class _Client:
        def form(self, panel, form_no):
            return {"panelCode": "SO_ORDER", "panelName": "销售订单", "formNo": form_no}

    monkeypatch.setattr(mes_capability, "_client", lambda token="": _Client())
    with pytest.raises(MesCapabilityError) as err:
        mes_capability.doc_detail({"panel": "", "form_no": "SO-1"}, token="t")
    assert "SO_ORDER" in str(err.value)


def test_trace_rejects_doc_from_forbidden_panel(as_worker, monkeypatch):
    """越权路径三：流程追踪。销售订单同样带生产路线，不能靠「是否生产路线」判断归属。"""
    class _Client:
        def trace(self, doc_no):
            return {"环节数": 1, "已完成数": 0, "待生成数": 1, "是否生产路线": True}

    class _Res:
        error = ""
        rows = [{"单据编号": "SO-1"}]

    monkeypatch.setattr(mes_capability, "_client", lambda token="": _Client())
    monkeypatch.setattr(mes_capability, "_probe_client", lambda: object())
    # 无权面板里能搜到这个单号 -> 说明越权
    def probe(**kwargs):
        if kwargs["panel"] == "SO_ORDER":
            return _Res()
        res = _Res()
        res.rows = []
        return res

    monkeypatch.setattr(mes_capability, "query_business", probe)
    with pytest.raises(MesCapabilityError) as err:
        mes_capability.trace_doc({"doc_no": "SO-1"}, token="t")
    assert "SO_ORDER" in str(err.value)


def test_stock_has_no_panel_gate(as_worker, monkeypatch):
    """库存是各岗位都要看的基础数据，不应被销售类权限连坐。"""
    class _Client:
        def stock(self, **kwargs):
            return {"rows": [{"存货": "轴套C", "数量": 10}]}

    monkeypatch.setattr(mes_capability, "_client", lambda token="": _Client())
    out = mes_capability.stock({"item_code": "", "warehouse": ""}, token="t")
    assert "库存" in out["text"] or "轴套C" in out["text"]