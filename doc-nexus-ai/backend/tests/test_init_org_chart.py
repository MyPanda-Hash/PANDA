"""组织架构图解析：只看纯逻辑（不连模型、不连库）。"""

from __future__ import annotations

import json

import pytest

from app.services import init_org_chart


def test_build_messages_puts_image_as_data_url() -> None:
    messages = init_org_chart.build_messages(b"fake-png-bytes", "image/png", hint="这是旧版组织架构")
    assert messages[0]["role"] == "system"
    assert "旧版组织架构" in messages[0]["content"]
    parts = messages[1]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_messages_rejects_unsupported_media() -> None:
    with pytest.raises(init_org_chart.OrgChartError) as exc:
        init_org_chart.build_messages(b"x", "image/gif")
    assert exc.value.code == "unsupported_media"


def test_build_messages_rejects_oversized_image() -> None:
    big = b"x" * (init_org_chart.MAX_IMAGE_BYTES + 1)
    with pytest.raises(init_org_chart.OrgChartError) as exc:
        init_org_chart.build_messages(big, "image/png")
    assert exc.value.code == "image_too_large"


def test_parse_reply_accepts_code_fence() -> None:
    text = "```json\n" + json.dumps({
        "depts": [
            {"code": "D01", "name": "生产部", "parentCode": "", "sort": 10},
            {"code": "D02", "name": "机加车间", "parentCode": "D01", "sort": 20, "positions": ["车间主任"]},
        ],
        "notes": "两层结构",
    }, ensure_ascii=False) + "\n```"
    result = init_org_chart.parse_reply(text)
    assert [d["name"] for d in result["depts"]] == ["生产部", "机加车间"]
    assert result["depts"][1]["parentCode"] == "D01"
    assert result["depts"][1]["positions"] == ["车间主任"]
    assert result["notes"] == "两层结构"


def test_parse_reply_skips_nameless_and_fills_missing_code() -> None:
    text = json.dumps({"depts": [
        {"name": "", "code": "D01"},
        {"name": "质检部"},
        {"code": "D02", "name": "仓库"},
    ]}, ensure_ascii=False)
    result = init_org_chart.parse_reply(text)
    assert [d["name"] for d in result["depts"]] == ["质检部", "仓库"]
    assert result["depts"][0]["code"] == "D02" or result["depts"][0]["code"].startswith("D")
    assert any("没有名称" in w for w in result["warnings"])


def test_parse_reply_moves_unknown_parent_to_root() -> None:
    text = json.dumps({"depts": [
        {"code": "D01", "name": "生产部", "parentCode": "D99"},
    ]}, ensure_ascii=False)
    result = init_org_chart.parse_reply(text)
    assert result["depts"][0]["parentCode"] == ""
    assert any("D99" in w for w in result["warnings"])


def test_parse_reply_breaks_cycle() -> None:
    text = json.dumps({"depts": [
        {"code": "D01", "name": "甲部", "parentCode": "D02"},
        {"code": "D02", "name": "乙部", "parentCode": "D01"},
    ]}, ensure_ascii=False)
    result = init_org_chart.parse_reply(text)
    assert any(d["parentCode"] == "" for d in result["depts"])
    assert any("成环" in w for w in result["warnings"])


def test_parse_reply_rejects_empty_or_non_json() -> None:
    with pytest.raises(init_org_chart.OrgChartError) as exc:
        init_org_chart.parse_reply("")
    assert exc.value.code == "empty_reply"
    with pytest.raises(init_org_chart.OrgChartError) as exc2:
        init_org_chart.parse_reply("图里有生产部和质检部")
    assert exc2.value.code == "bad_reply"
    with pytest.raises(init_org_chart.OrgChartError) as exc3:
        init_org_chart.parse_reply(json.dumps({"depts": []}))
    assert exc3.value.code == "no_depts"


def test_duplicate_codes_get_suffix() -> None:
    text = json.dumps({"depts": [
        {"code": "D01", "name": "生产部"},
        {"code": "D01", "name": "质检部"},
    ]}, ensure_ascii=False)
    result = init_org_chart.parse_reply(text)
    codes = [d["code"] for d in result["depts"]]
    assert len(set(codes)) == 2, codes
    assert any("重复" in w for w in result["warnings"])


def test_filter_assignments_drops_unknown_panels() -> None:
    from app.api.internal_mes import filter_assignments

    valid = {"SO_ORDER", "SALE_OUT"}
    out = filter_assignments(
        [
            {"code": "E1", "template": "销售", "approvalPanels": ["SO_ORDER", "不存在的面板", "SALE_OUT"]},
            {"code": "", "template": "忽略", "approvalPanels": ["SO_ORDER"]},
            "不是对象",
        ],
        valid,
    )
    assert len(out) == 1
    assert out[0]["approvalPanels"] == ["SO_ORDER", "SALE_OUT"]
    assert out[0]["droppedPanels"] == ["不存在的面板"]


def test_strip_code_fence_handles_wrapped_json() -> None:
    from app.api.internal_mes import _strip_code_fence

    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('好的：{"a":1} 完毕') == '{"a":1}'
    assert _strip_code_fence('{"a":1}') == '{"a":1}'
