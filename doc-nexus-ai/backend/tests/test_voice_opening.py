"""开场播报：拼装与开关（不连上游、不连库）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.services import moss_voice


def _briefing(lines: list[str], ok: bool = True, note: str = ""):
    class B:
        pass

    b = B()
    b.ok = ok
    b.note = note
    b.lines = lines
    b.items = []
    b.skipped = []

    def speak() -> str:
        if note:
            return note
        return "生产概况：" + "；".join(lines) + "。"

    b.speak = speak
    return b


def test_instructions_carry_opening_block() -> None:
    text = moss_voice.build_instructions("管理员", opening="加工单 7 张，在制 1 张")
    assert "开场播报" in text
    assert "加工单 7 张，在制 1 张" in text
    assert "管理员" in text


def test_instructions_without_opening_has_no_briefing_block() -> None:
    assert "开场播报" not in moss_voice.build_instructions("管理员")


def test_opening_skipped_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "moss_voice_briefing_enabled", False)
    assert moss_voice.opening_briefing("usr_x") == ""


def test_opening_skipped_when_not_ok(monkeypatch) -> None:
    from app.services import production_briefing

    monkeypatch.setattr(settings, "moss_voice_briefing_enabled", True)
    monkeypatch.setattr(production_briefing, "for_user", lambda _uid: _briefing([], ok=False))
    assert moss_voice.opening_briefing("usr_x") == ""


def test_opening_truncated_to_limit(monkeypatch) -> None:
    from app.services import production_briefing

    monkeypatch.setattr(settings, "moss_voice_briefing_enabled", True)
    monkeypatch.setattr(settings, "moss_voice_briefing_chars", 20)
    long_line = "加工单 7 张，在制 1 张，低库存 9 项，销售订单审批中 0 张"
    monkeypatch.setattr(production_briefing, "for_user", lambda _uid: _briefing([long_line]))
    text = moss_voice.opening_briefing("usr_x")
    assert len(text) <= 20, text
    assert text.startswith("生产概况")
    assert text.endswith("。") or text.endswith("；")
