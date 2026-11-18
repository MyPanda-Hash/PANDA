"""生产概况汇报：口径与权限跳过逻辑（不连 MES、不连库）。"""

from __future__ import annotations

from datetime import date

from app.services import production_briefing as pb


def test_overdue_orders_skip_done_and_future() -> None:
    rows = [
        {"单据编号": "MO-1", "单据状态": "已审核", "预完工日": "2026-11-01"},
        {"单据编号": "MO-2", "单据状态": "已完工", "预完工日": "2026-11-01"},
        {"单据编号": "MO-3", "单据状态": "已审核", "预完工日": "2026-12-01"},
        {"单据编号": "MO-4", "单据状态": "已审核", "预完工日": ""},
    ]
    overdue = pb.overdue_orders(rows, today=date(2026, 11, 20))
    assert [r["单据编号"] for r in overdue] == ["MO-1"]


def test_low_stock_threshold() -> None:
    rows = [
        {"存货编码": "CL001", "现存量(主)": "12.5"},
        {"存货编码": "CL002", "现存量(主)": "500"},
        {"存货编码": "CL003", "现存量(主)": ""},
    ]
    low = pb.low_stock(rows)
    assert [r["存货编码"] for r in low] == ["CL001", "CL003"]


def test_briefing_skips_unauthorized_panels_loudly() -> None:
    def fetch(panel: str):
        if panel == "MANU_ORDER":
            return [
                {"单据编号": "MO-9", "单据状态": "已审核", "预完工日": "2026-11-01"},
                {"单据编号": "MO-10", "单据状态": "已完工", "预完工日": "2026-11-01"},
            ]
        raise AssertionError(f"无权面板不应该被查询：{panel}")

    def can(panel: str) -> bool:
        return panel == "MANU_ORDER"

    result = pb.build(fetch=fetch, can=can, today=date(2026, 11, 20))

    text = result.speak()
    assert "加工单 2 张" in text
    assert "1 张已过预完工日" in text
    # 没有权限的面板要说明跳过了，而不是假装没数据
    assert "库存状况表" in result.skipped
    assert "没有权限" in text
    assert result.items[0]["status"] == "超期"


def test_briefing_empty_when_nothing_visible() -> None:
    result = pb.build(fetch=lambda p: [], can=lambda p: True, today=date(2026, 11, 20))
    assert result.ok is False
    assert "没有查到" in result.speak()


def test_for_user_without_binding_explains_instead_of_empty() -> None:
    """没绑定 MES 的人得到的是「去绑定」，不是「今天一切正常」。"""
    from app.services import mes_user_client, production_briefing as pb2

    def boom(_user_id):
        raise mes_user_client.MesPermissionError("请先绑定 MES")

    original = getattr(mes_user_client, "_test_original_client_for_user", mes_user_client.client_for_user)
    mes_user_client._test_original_client_for_user = original
    mes_user_client.client_for_user = boom
    try:
        result = pb2.for_user("usr_unbound")
    finally:
        mes_user_client.client_for_user = original

    assert result.ok is False
    assert "请先绑定 MES" in result.speak()
    assert result.lines == []


def test_briefing_note_wins_over_lines() -> None:
    b = pb.Briefing(lines=["加工单 3 张"], note="权限读取失败")
    assert b.speak() == "权限读取失败"
