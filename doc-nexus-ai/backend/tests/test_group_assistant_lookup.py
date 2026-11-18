"""L2 只读融合：带单号的问题要自动拉追踪 + 详情。

不验证模型（模型不可控），只验证「问之前数据已经查好」这条链路。
"""

from __future__ import annotations

from app.services import group_assistant, group_tools


def test_doc_number_question_pulls_trace_and_detail(monkeypatch):
    calls: list[dict] = []

    def fake_lookup(spec, client=None):
        calls.append(dict(spec))
        if spec["kind"] == "trace":
            return group_tools.LookupResult(
                kind="trace",
                title="单据 SO-20261110-001 追踪",
                payload={"单号": "SO-20261110-001", "环节数": 39, "已完成数": 39, "待生成数": 0,
                         "卡点单号": "", "卡点原因": "",
                         "steps": [{"序号": 39, "面板编码": "SALE_OUT", "环节": "销售出库",
                                    "单号": "SO-20261110-001", "单据状态": "已审核"}]},
            )
        return group_tools.LookupResult(
            kind="form", title="单据详情",
            payload={"panelName": "销售出库单", "formNo": "SO-20261110-001", "status": "已审核",
                     "header": {"客户": "华东铝业"}, "tabs": [], "link": "/panelx/form/SALE_OUT?code=SO-20261110-001"},
        )

    monkeypatch.setattr(group_tools, "lookup", fake_lookup)

    blocks = group_assistant._business_lookup_blocks("SO-20261110-001 这单到哪一步了")

    assert [spec["kind"] for spec in calls] == ["trace", "form"]
    assert calls[1] == {"kind": "form", "panel_code": "SALE_OUT", "form_no": "SO-20261110-001"}
    assert len(blocks) == 1
    assert "39 环节" in blocks[0]
    assert "华东铝业" in blocks[0]


def test_plain_small_talk_produces_no_lookup(monkeypatch):
    def boom(spec, client=None):  # pragma: no cover - 不该被调用
        raise AssertionError("闲聊不应触发查询")

    monkeypatch.setattr(group_tools, "lookup", boom)
    assert group_assistant._business_lookup_blocks("你好") == []
