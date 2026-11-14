"""订阅式盯单：快照比较与建订阅的边界（不连 MES）。"""

from __future__ import annotations

from app.services import doc_watch


def _trace(completed: int, total: int, current: str, steps: list[dict], stalled: str = "") -> dict:
    return {
        "环节数": total,
        "已完成数": completed,
        "当前环节": current,
        "卡点单号": stalled,
        "卡点原因": "超期未处理" if stalled else "",
        "steps": steps,
    }


BASE_STEPS = [
    {"环节": "采购订单", "面板编码": "PU_ORDER", "单号": "PO-1", "单据状态": "已审核"},
    {"环节": "到货单", "面板编码": "ARRIVAL_IN", "单号": "", "单据状态": "待生成"},
    {"环节": "采购入库单", "面板编码": "PUR_IN", "单号": "", "单据状态": "待生成"},
]


def test_progress_hit_when_completed_increases() -> None:
    before = doc_watch.snapshot_of(_trace(1, 3, "采购订单", BASE_STEPS))
    after_steps = [dict(BASE_STEPS[0]), {**BASE_STEPS[1], "单号": "DH-1", "单据状态": "已审核"}, BASE_STEPS[2]]
    after = doc_watch.snapshot_of(_trace(2, 3, "到货单", after_steps))

    signal = doc_watch.diff(before, after)
    assert signal.hit is True
    # 有下游单据出现时优先报「哪张单出来了」，比报进度百分比有用
    assert "到货单环节已生成单据 DH-1" in signal.text


def test_new_downstream_doc_counts_as_progress_even_if_completed_same() -> None:
    """首件报检单刚建出来是草稿，完成数不变，但这是真推进，必须报。"""
    before = doc_watch.snapshot_of(_trace(3, 11, "SO-1", BASE_STEPS))
    steps = [*BASE_STEPS, {"环节": "首件报检单", "面板编码": "FIRST_INSPECT", "单号": "SJ-1", "单据状态": "草稿"}]
    after = doc_watch.snapshot_of(_trace(3, 11, "SJ-1", steps))

    signal = doc_watch.diff(before, after)
    assert signal.hit is True
    assert "首件报检单" in signal.text and "SJ-1" in signal.text


def test_no_signal_when_nothing_changed() -> None:
    before = doc_watch.snapshot_of(_trace(1, 3, "采购订单", BASE_STEPS))
    after = doc_watch.snapshot_of(_trace(1, 3, "采购订单", BASE_STEPS))
    assert doc_watch.diff(before, after).hit is False


def test_target_step_reports_only_when_that_step_appears() -> None:
    before = doc_watch.snapshot_of(_trace(0, 3, "", BASE_STEPS))
    arrival = doc_watch.snapshot_of(
        _trace(1, 3, "到货单", [{**BASE_STEPS[1], "单号": "DH-9"}, BASE_STEPS[2]])
    )
    signal = doc_watch.diff(before, arrival, target_keyword="到货")
    assert signal.hit is True
    assert "DH-9" in signal.text

    # 到货还没出单，但别的环节推进了：盯「到货」的人不该被打扰
    other = doc_watch.snapshot_of(
        _trace(1, 3, "采购订单", [{**BASE_STEPS[0], "单据状态": "已审核"}, BASE_STEPS[1], BASE_STEPS[2]])
    )
    assert doc_watch.diff(before, other, target_keyword="到货").hit is False


def test_stalled_condition_reports_new_stall() -> None:
    before = doc_watch.snapshot_of(_trace(1, 3, "采购订单", BASE_STEPS))
    after = doc_watch.snapshot_of(
        _trace(1, 3, "采购订单", BASE_STEPS, stalled="PO-1")
    )
    signal = doc_watch.diff(before, after, condition="stalled")
    assert signal.hit is True
    assert "卡点" in signal.text


def test_create_rejects_unknown_doc() -> None:
    import pytest

    class FakeSession:
        def scalars(self, _q):
            class R:
                def first(self):
                    return None
            return R()
        def add(self, _obj):
            pass
        def flush(self):
            pass

    with pytest.raises(doc_watch.WatchError):
        doc_watch.create(FakeSession(), user_id="u1", doc_no="PO-X", trace={"steps": []})


def test_create_rejects_already_finished_doc() -> None:
    import pytest

    class FakeSession:
        def scalars(self, _q):
            class R:
                def first(self):
                    return None
            return R()
        def add(self, _obj):
            pass
        def flush(self):
            pass

    finished = _trace(3, 3, "采购入库单", [
        {"环节": "采购订单", "面板编码": "PU_ORDER", "单号": "PO-1", "单据状态": "已审核"},
        {"环节": "到货单", "面板编码": "ARRIVAL_IN", "单号": "DH-1", "单据状态": "已审核"},
        {"环节": "采购入库单", "面板编码": "PUR_IN", "单号": "RK-1", "单据状态": "已审核"},
    ])
    with pytest.raises(doc_watch.WatchError, match="已经全部走完"):
        doc_watch.create(FakeSession(), user_id="u1", doc_no="PO-1", trace=finished)


def test_resolve_step_tolerates_wording_difference() -> None:
    trace = _trace(1, 4, "生产加工单", [
        {"环节": "销售订单", "面板编码": "SO_ORDER", "单号": "SO-1", "单据状态": "已审核"},
        {"环节": "生产加工单", "面板编码": "MANU_ORDER", "单号": "", "单据状态": "待生成"},
        {"环节": "工序汇报", "面板编码": "PROCESS_REPORT", "单号": "", "单据状态": "待生成"},
    ])
    # 现场说「生产工单」，MES 里叫「生产加工单」
    assert doc_watch.resolve_step(trace, "生产工单") == "生产加工单"
    assert doc_watch.resolve_step(trace, "报工") == "工序汇报"
    assert doc_watch.resolve_step(trace, "财务对账") == ""


def test_unknown_step_falls_back_to_progress() -> None:
    """还没走到那一步时，不建哑巴订阅，降级成「有推进就报」并说明。"""

    class FakeSession:
        def __init__(self):
            self.added = []
        def scalars(self, _q):
            class R:
                def first(self):
                    return None
            return R()
        def add(self, obj):
            self.added.append(obj)
        def flush(self):
            pass

    trace = _trace(1, 3, "销售订单", [
        {"环节": "销售订单", "面板编码": "SO_ORDER", "单号": "SO-1", "单据状态": "已审核"},
    ])
    watch, note = doc_watch.create(FakeSession(), user_id="u1", doc_no="SO-1",
                                   target_keyword="报工", trace=trace)
    assert watch.target_keyword == ""
    assert watch.condition == "progress"
    assert "报工" in note and "有任何推进" in note
