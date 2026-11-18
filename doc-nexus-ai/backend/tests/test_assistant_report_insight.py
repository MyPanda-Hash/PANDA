from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import assistant_report_insight as insight_mod


def _ctx(**overrides):
    base = dict(
        kind="daily",
        period_label="日报",
        period_start=datetime.now(timezone.utc) - timedelta(days=1),
        period_end=datetime.now(timezone.utc),
        created=9,
        resolved=2,
        open_now=7,
        stale=3,
        by_kind={"so_unstarted": 3, "stock_low": 2},
        resolved_by_kind={"pu_draft": 2},
        by_person={"李计划": 4, "张仓管": 2},
        avg_ack_hours=0.7,
        ack_within_24h=1.0,
        scans=25,
        scan_failures=1,
        avg_scan_ms=208,
        missing_owners=1,
        top_items=[
            {"title": "销售订单 SO-2026-08-0002 已审核，尚未安排生产",
             "severity": "warning", "kind": "so_unstarted", "doc_no": "SO-2026-08-0002"},
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_prompt_contains_only_real_facts():
    prompt = insight_mod.build_prompt(_ctx())
    assert "新发现待办 9 条" in prompt
    assert "当前积压 7 条" in prompt
    assert "其中 3 条超过 24 小时" in prompt
    assert "销售订单未排产" in prompt
    assert "李计划: 4 条" in prompt
    assert "SO-2026-08-0002" in prompt


def test_parse_insight_tolerates_code_fence():
    raw = "```json\n" + json.dumps({
        "summary": "积压集中在销售与库存",
        "highlights": ["积压 7 条"],
        "advice": [{"target": "计划员", "action": "今天排完 3 张订单",
                    "reason": "已超期", "urgency": "high"}],
        "risks": ["订单继续积压会拖交期"],
    }, ensure_ascii=False) + "\n```"
    parsed = insight_mod.parse_insight(raw)
    assert parsed.summary.startswith("积压")
    assert parsed.advice[0]["urgency"] == "high"


def test_parse_insight_handles_garbage():
    assert insight_mod.parse_insight("我觉得还行") == insight_mod.ReportInsight()
    assert insight_mod.parse_insight("") == insight_mod.ReportInsight()


def test_verify_drops_fabricated_numbers():
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="本期积压 7 条，其中 3 条超期",                 # 真实数字
        highlights=["本周新增 999 条待办"],                     # 编造
        advice=[
            {"target": "计划员", "action": "处理 3 张未排产订单", "reason": "", "urgency": "high"},
            {"target": "仓管", "action": "补足 500 件库存", "reason": "低于下限", "urgency": "normal"},
        ],
        risks=["若不处理将影响 42 个客户"],
    )
    verified = insight_mod.verify_insight(parsed, ctx)

    assert "7" in verified.summary and "3" in verified.summary
    assert verified.highlights == [], "编造数字的要点应被丢弃"
    assert len(verified.advice) == 1, "只保留数字可信的那条"
    assert verified.advice[0]["action"].startswith("处理 3 张")
    assert verified.risks == [], "编造客户数应被丢弃"


def test_verify_keeps_advice_without_numbers():
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="整体可控",
        advice=[{"target": "计划员", "action": "优先安排已审核订单", "reason": "避免超期", "urgency": "normal"}],
    )
    verified = insight_mod.verify_insight(parsed, ctx)
    assert len(verified.advice) == 1


def test_render_insight_markdown():
    insight = insight_mod.ReportInsight(
        summary="积压主要在销售端",
        highlights=["未排产 3 张"],
        advice=[{"target": "计划员", "action": "今天排完", "reason": "已超期", "urgency": "high"}],
        risks=["拖交期"],
    )
    text = insight_mod.render_insight(insight)
    assert "### 分析" in text
    assert "### 建议" in text
    assert "### 风险" in text
    assert "**计划员**" in text
    assert "⚠️" in text


@pytest.mark.asyncio
async def test_generate_insight_returns_empty_when_model_unavailable(monkeypatch):
    async def boom(*args, **kwargs):
        from app.services.plain_llm import LlmUnavailable

        raise LlmUnavailable("没配模型")

    monkeypatch.setattr("app.services.plain_llm.complete_chat", boom)
    assert await insight_mod.generate_insight(_ctx()) == ""


@pytest.mark.asyncio
async def test_generate_insight_renders_verified_content(monkeypatch):
    class Reply:
        text = json.dumps({
            "summary": "本期积压 7 条",
            "highlights": ["销售订单未排产 3 条"],
            "advice": [{"target": "计划员", "action": "安排 3 张订单排产",
                        "reason": "已审核未排产", "urgency": "high"}],
            "risks": ["继续积压影响交期"],
        }, ensure_ascii=False)

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    text = await insight_mod.generate_insight(_ctx())
    assert "### 分析" in text
    assert "### 建议" in text
    assert "安排 3 张订单排产" in text


@pytest.mark.asyncio
async def test_generate_insight_drops_all_when_everything_fabricated(monkeypatch):
    class Reply:
        text = json.dumps({
            "summary": "本期新增 8888 条",
            "advice": [{"target": "计划员", "action": "处理 777 张单", "urgency": "high"}],
        }, ensure_ascii=False)

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    assert await insight_mod.generate_insight(_ctx()) == "", "全是编造就不该输出"


def test_doc_numbers_are_not_treated_as_fabricated():
    """单号里的数字（SO-2026-08-0002）是合法引用，不能当成编造。"""
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="积压 7 条",
        advice=[{
            "target": "李计划",
            "action": "今日内为 SO-2026-08-0002 排出生产计划",
            "reason": "已审核未排产",
            "urgency": "high",
        }],
    )
    verified = insight_mod.verify_insight(parsed, ctx)
    assert len(verified.advice) == 1, "引用真实单号的建议不该被丢弃"
    assert "SO-2026-08-0002" in verified.advice[0]["action"]


def test_fabricated_doc_number_is_dropped():
    """模型自己编一个不存在的单号，必须丢弃。"""
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="",
        advice=[{"target": "李计划", "action": "处理 SO-2099-99-9999 这张单",
                 "reason": "", "urgency": "high"}],
    )
    verified = insight_mod.verify_insight(parsed, ctx)
    assert verified.advice == []


def test_future_date_phrase_is_softened_not_dropped():
    """模型写「11-13 前」这类未来日期时，把日期换成『尽快』，保留建议。"""
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="",
        advice=[{
            "target": "李计划",
            "action": "11-13 前完成 SO-2026-08-0002 排产",
            "reason": "已审核未排产",
            "urgency": "high",
        }],
    )
    verified = insight_mod.verify_insight(parsed, ctx)
    assert len(verified.advice) == 1, "不该因为一个日期就丢掉整条建议"
    action = verified.advice[0]["action"]
    assert "11-13" not in action
    assert "尽快" in action
    assert "SO-2026-08-0002" in action


def test_real_number_fabrication_still_dropped():
    """抹日期不等于放宽数值校验：真编造的数字依然要丢。"""
    ctx = _ctx()
    parsed = insight_mod.ReportInsight(
        summary="",
        advice=[{"target": "张仓管", "action": "今天内补货 888 件", "reason": "", "urgency": "high"}],
    )
    assert insight_mod.verify_insight(parsed, ctx).advice == []
