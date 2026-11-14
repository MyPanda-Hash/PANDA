"""数据脱敏守卫的契约测试。

目标：云端模型的每一条出网路径都不能带原文；本机/内网端点不改变行为。
"""

from __future__ import annotations

import pytest

from app.services import data_guard
from app.services.data_guard import (
    CloudEgressBlocked,
    MaskSession,
    anonymize_text,
    is_cloud_endpoint,
    session_for_endpoint,
)


@pytest.fixture(autouse=True)
def _reset_policy(monkeypatch):
    monkeypatch.delenv("DOC_NEXUS_DATA_MASK_MODE", raising=False)
    monkeypatch.delenv("DOC_NEXUS_DATA_MASK_TRUSTED_HOSTS", raising=False)
    monkeypatch.delenv("DOC_NEXUS_DATA_MASK_TERMS", raising=False)
    monkeypatch.delenv("DOC_NEXUS_DATA_MASK_MEDIA_POLICY", raising=False)
    yield


# ---------------- 端点分级 ----------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434/v1",
        "http://192.168.1.20:8000/v1",
        "http://10.0.0.5/v1",
        "http://172.16.3.9:9000/v1",
        "http://[::1]:8000/v1",
        "http://model-gw.internal/v1",
    ],
)
def test_private_endpoints_are_trusted(url):
    assert is_cloud_endpoint(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://api.openai.com/v1",
        "https://api.deepseek.com/v1",
        "https://some-proxy.example.com/v1",
    ],
)
def test_public_endpoints_are_cloud(url):
    assert is_cloud_endpoint(url) is True


def test_trusted_host_override(monkeypatch):
    monkeypatch.setenv("DOC_NEXUS_DATA_MASK_TRUSTED_HOSTS", "gw.corp.example.com")
    assert is_cloud_endpoint("https://gw.corp.example.com/v1") is False


# ---------------- 脱敏规则 ----------------

def test_mask_text_covers_pii_kinds():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    raw = "客户张三 手机13812345678 身份证110101199003071234 邮箱zhang@corp.com 卡号6222021234567890123"
    masked = session.mask_text(raw)
    for leaked in ("13812345678", "110101199003071234", "zhang@corp.com", "6222021234567890123"):
        assert leaked not in masked, f"{leaked} 不应出现在脱敏文本里"
    assert "PHONE" in masked and "ID_CARD" in masked and "EMAIL" in masked and "BANK_CARD" in masked


def test_placeholder_is_stable_within_request():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    first = session.mask_text("电话13812345678")
    second = session.mask_text("再确认13812345678")
    token = first.split("〔")[1].split("〕")[0]
    assert token in second, "同一请求内同一原文必须得到同一个占位符，模型才能推理「同一人」"


def test_mask_payload_walks_nested_and_masks_sensitive_keys(monkeypatch):
    # 自由文本里的公司名只有配了词表才认得出来（见下一个用例）
    monkeypatch.setenv("DOC_NEXUS_DATA_MASK_TERMS", "华东铝业")
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    payload = {
        "model": "gpt-x",
        "messages": [
            {"role": "user", "content": [{"type": "input_text", "text": "帮我查客户 华东铝业 的单价"}]},
        ],
        "metadata": {"客户": "华东铝业", "金额": 128.5, "安全字段": "保持原样"},
    }
    masked = session.mask_payload(payload)
    dumped = str(masked)
    assert "华东铝业" not in dumped, "键名含「客户」的值必须整体脱敏"
    assert "128.5" not in dumped
    assert "保持原样" in dumped
    # 原始 payload 不被就地改写（避免后续逻辑读到假数据）
    assert payload["metadata"]["客户"] == "华东铝业"


def test_sensitive_key_masked_without_term_list():
    """没有词表时，键名（客户/金额）也必须兜住——这是最后一道防线。"""
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    masked = session.mask_payload({"客户": "任意公司名", "金额": 99.9})
    assert "任意公司名" not in str(masked)
    assert "99.9" not in str(masked)


def test_mask_payload_records_hits():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    session.mask_payload({"messages": [{"content": "手机13812345678"}]})
    assert session.hits.get("PHONE") == 1


# ---------------- 还原 ----------------

def test_restore_round_trips_placeholders():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    masked = session.mask_text("客户手机13812345678")
    restored = session.restore_text(f"已为您记录 {masked}")
    assert "13812345678" in restored


def test_restore_ignores_unknown_tokens():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    assert session.restore_text("没有占位符〔UNKNOWN_9〕") == "没有占位符〔UNKNOWN_9〕"


# ---------------- 策略 ----------------

def test_local_endpoint_session_is_disabled():
    session = session_for_endpoint("http://127.0.0.1:8000/v1")
    assert session.enabled is False
    raw = "手机13812345678"
    assert session.mask_text(raw) == raw


def test_cloud_endpoint_session_masks_by_default():
    session = session_for_endpoint("https://api.deepseek.com/v1")
    assert session.enabled is True
    assert session.mode == "mask"


def test_block_mode_raises_on_sensitive_content():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="block", enabled=True)
    with pytest.raises(CloudEgressBlocked):
        session.mask_payload({"messages": [{"content": "身份证110101199003071234"}]})


def test_off_mode_passes_through():
    session = MaskSession(endpoint="https://api.example.com/v1", mode="off", enabled=True)
    raw = "手机13812345678"
    assert session.mask_text(raw) == raw


def test_media_policy_defaults_to_block(monkeypatch):
    assert data_guard.media_egress_allowed("https://api.openai.com/v1") is False
    monkeypatch.setenv("DOC_NEXUS_DATA_MASK_MEDIA_POLICY", "allow")
    assert data_guard.media_egress_allowed("https://api.openai.com/v1") is True
    # 本地端点本来就不需要拦
    assert data_guard.media_egress_allowed("http://127.0.0.1:8000/v1") is True


# ---------------- 向量化用的一致性匿名化 ----------------

def test_anonymize_text_is_deterministic_and_unindexed():
    raw = "客户13812345678 身份证110101199003071234"
    first = anonymize_text(raw)
    second = anonymize_text(raw)
    assert first == second, "向量化必须在不同批次得到相同结果，否则检索会漂移"
    assert "13812345678" not in first
    assert "ID_CARD" in first and "PHONE" in first
    assert "〔PHONE_1〕" not in first, "匿名化不带序号"


# ---------------- 端到端：出网字节确实被脱敏 ----------------

def _model(base_url: str):
    from app.services.pi_runtime.config import ResolvedPiModel

    return ResolvedPiModel(
        credential_id="cred-1",
        provider_name="platform-qwen",
        model="qwen-test",
        base_url=base_url,
        api_key="secret",
    )


def _prepare(base_url: str, text: str):
    import json as _json

    from app.services.pi_runtime.model_proxy import prepare_model_proxy_request

    body = _json.dumps(
        {
            "model": "placeholder",
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 64,
        }
    ).encode()
    return prepare_model_proxy_request(_model(base_url), "chat/completions", [], body)


def test_cloud_request_body_is_masked():
    prepared = _prepare(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "客户手机13812345678 身份证110101199003071234",
    )
    sent = prepared.body.decode()
    assert "13812345678" not in sent
    assert "110101199003071234" not in sent
    assert "PHONE" in sent and "ID_CARD" in sent
    assert prepared.mask_session is not None


def test_local_request_body_is_untouched():
    raw = "客户手机13812345678"
    prepared = _prepare("http://127.0.0.1:11434/v1", raw)
    assert "13812345678" in prepared.body.decode()
    assert prepared.mask_session is None


def test_mask_session_restores_streamed_answer():
    """回答里的占位符要能换回真实值（含被切在两个 chunk 之间的情形）。"""
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    masked = session.mask_text("手机13812345678")
    head = masked[:4]
    tail = masked[4:]
    assert session.restore_text(head + tail) == "手机13812345678"


def test_money_requires_unit_to_avoid_false_positives():
    """金额规则必须带单位：订单号/数量不能被误当金额盖掉。"""
    session = MaskSession(endpoint="https://api.example.com/v1", mode="mask", enabled=True)
    masked = session.mask_text("单价128.5元，订单号 SO20261109001，数量200件")
    assert "128.5" not in masked
    assert "SO20261109001" in masked, "单号不能被金额规则误伤"
    assert "200件" in masked, "数量不是金额"
