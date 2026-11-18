from __future__ import annotations

import pytest

from app.schemas.chat import ChatImageContent
from app.services.media_intent_router import (
    CLASSIFIER_VERSION,
    build_classifier_messages,
    classifier_payload_contains_base64,
    route_media_intent,
)

_CAPABILITIES = {
    "image_generate": True,
    "image_edit": True,
    "video_generate": True,
}
_IMAGE = ChatImageContent(data="aW1hZ2U=", mimeType="image/png")


@pytest.fixture(autouse=True)
def _enable_calibrated_auto_rules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.services.media_intent_router.settings.media_intent_auto_calibration_version",
        CLASSIFIER_VERSION,
    )
    monkeypatch.setattr(
        "app.services.media_intent_router.settings.media_intent_auto_reason_codes",
        ",".join(
            (
                "explicit_image_action",
                "explicit_edit_action",
                "explicit_video_action",
                "explicit_video_request",
                "single_image_to_video",
                "continue_previous_image_generation",
            )
        ),
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("请生成一张赛博朋克风格的海报", "image_generate"),
        ("给我生成一张照片，背景是在机场里面", "image_generate"),
        ("把这张图片的背景换成海边", "image_edit"),
        ("请生成一段月球基地的视频", "video_generate"),
    ],
)
def test_auto_routes_high_confidence_media_rules(message: str, expected: str):
    images = [_IMAGE] if expected == "image_edit" else []

    decision = route_media_intent(
        message,
        images=images,
        mode="auto",
        capabilities=_CAPABILITIES,
    )

    assert decision.predicted_intent == expected
    assert decision.effective_route == expected
    assert decision.final_intent == expected
    assert decision.route_source == "deterministic_rule"
    assert decision.confidence_band == "high"
    assert decision.classifier_version == CLASSIFIER_VERSION


def test_auto_requires_matching_production_calibration(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.services.media_intent_router.settings.media_intent_auto_calibration_version",
        "",
    )

    decision = route_media_intent(
        "请生成一张赛博朋克风格的海报",
        mode="auto",
        capabilities=_CAPABILITIES,
    )

    assert decision.predicted_intent == "image_generate"
    assert decision.effective_route == "clarify"
    assert decision.final_intent is None
    assert decision.reason_code == "rule_not_auto_enabled"
    assert decision.requires_confirmation is True


@pytest.mark.parametrize(
    "message",
    [
        "给我一个生成图片的提示词",
        "分析这张图片表达了什么",
        "写一个视频脚本和分镜",
        "不要生成图片，只说明设计思路",
        "例如命令是“/skill:image-generation 画一只猫”，不要执行",
    ],
)
def test_consultation_analysis_negation_and_quotes_stay_in_chat(message: str):
    decision = route_media_intent(
        message,
        images=[_IMAGE] if "这张" in message else [],
        mode="auto",
        capabilities=_CAPABILITIES,
    )

    assert decision.effective_route == "chat"
    assert decision.final_intent is None


def test_general_capability_question_stays_in_deterministic_chat():
    def classifier(*_args, **_kwargs):
        pytest.fail("generic capability questions must not call the media classifier")

    decision = route_media_intent(
        "你会做啥？",
        mode="auto",
        capabilities=_CAPABILITIES,
        classifier=classifier,
    )

    assert decision.predicted_intent == "chat"
    assert decision.effective_route == "chat"
    assert decision.route_source == "deterministic_rule"
    assert decision.confidence_band == "high"
    assert decision.reason_code == "general_capability_question"


def test_explicit_ui_route_wins_over_text_classifier():
    decision = route_media_intent(
        "怎么生成图片？",
        explicit_skill="image-generation",
        mode="off",
        capabilities=_CAPABILITIES,
    )

    assert decision.effective_route == "image_generate"
    assert decision.route_source == "explicit_ui"


def test_conflicting_ui_and_slash_routes_require_clarification():
    decision = route_media_intent(
        "/skill:video-generation 做成短片",
        explicit_skill="image-generation",
        mode="auto",
        capabilities=_CAPABILITIES,
    )

    assert decision.effective_route == "clarify"
    assert decision.reason_code == "explicit_skill_conflict"
    assert decision.status == "awaiting_confirmation"


def test_shadow_records_prediction_without_executing_media():
    decision = route_media_intent(
        "生成一张水墨画",
        mode="shadow",
        capabilities=_CAPABILITIES,
    )

    assert decision.predicted_intent == "image_generate"
    assert decision.effective_route == "chat"
    assert decision.final_intent is None


def test_confirm_mode_requires_confirmation_for_deterministic_media():
    decision = route_media_intent(
        "生成一段产品动画视频",
        mode="confirm",
        capabilities=_CAPABILITIES,
    )

    assert decision.predicted_intent == "video_generate"
    assert decision.effective_route == "clarify"
    assert decision.requires_confirmation is True
    assert decision.status == "awaiting_confirmation"


def test_classifier_media_prediction_never_auto_executes():
    def classifier(_endpoint, _messages, **_kwargs):
        return (
            '{"predicted_intent":"video_generate","confidence_band":"high",'
            '"reason_code":"classifier_prediction"}',
            {"input_tokens": 9, "output_tokens": 4},
        )

    decision = route_media_intent(
        "给这个产品来点动态效果",
        mode="auto",
        capabilities=_CAPABILITIES,
        classifier=classifier,
    )

    assert decision.predicted_intent == "video_generate"
    assert decision.effective_route == "clarify"
    assert decision.requires_confirmation is True
    assert decision.classifier_usage == {"input_tokens": 9, "output_tokens": 4}


@pytest.mark.parametrize("mode", ["auto", "confirm", "shadow"])
def test_invalid_classifier_output_requires_choice_outside_shadow(mode: str):
    decision = route_media_intent(
        "给这个产品来点动态效果",
        mode=mode,
        capabilities=_CAPABILITIES,
        classifier=lambda *_args, **_kwargs: "not-json",
    )

    assert decision.effective_route == ("chat" if mode == "shadow" else "clarify")
    assert decision.requires_confirmation is (mode != "shadow")
    assert decision.final_intent is None
    assert decision.reason_code == "classifier_failed"
    assert decision.classifier_error_code == "invalid_json"
    if mode != "shadow":
        assert {choice.intent for choice in decision.clarification_choices} == {
            "image_generate", "image_edit", "video_generate", "chat",
        }


@pytest.mark.parametrize(
    "message",
    ["会议时间换成下午三点", "修改报告里的颜色"],
)
def test_failed_classifier_does_not_bind_non_image_edits(message: str):
    decision = route_media_intent(
        message,
        mode="confirm",
        capabilities=_CAPABILITIES,
        classifier=lambda *_args, **_kwargs: "not-json",
        user_id="usr-owner",
        session_id="chs-owner",
        recent_images=[{
            "task_id": "igt-active",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "status": "succeeded",
            "is_active_context": True,
        }],
    )

    assert decision.effective_route == "clarify"
    assert decision.reason_code == "classifier_failed"
    assert decision.source_kind == "none"
    assert decision.source_task_id is None
    assert {choice.intent for choice in decision.clarification_choices} == {
        "image_generate", "image_edit", "video_generate", "chat",
    }


def test_classifier_payload_contains_context_metadata_but_no_sensitive_values():
    messages = build_classifier_messages(
        "能不能改成中国国内的",
        [_IMAGE],
        [
            {
                "task_id": "igt-secret",
                "task_kind": "image",
                "intent": "image_generate",
                "status": "succeeded",
                "created_at": "2026-09-03T10:00:00Z",
                "prompt": "机场值机服务台",
                "image_url": "https://example.com/secret.png",
                "is_immediate_context": True,
                "is_active_context": True,
            }
        ],
    )
    assert "predicted_intent" in messages[0]["content"]
    assert "不得把 predicted_intent 简写为 intent" in messages[0]["content"]
    assert "is_immediate_context=true" in messages[0]["content"]
    assert "is_active_context=true" in messages[0]["content"]

    assert '"mime_type":"image/png"' in messages[1]["content"]
    assert '"characters":8' in messages[1]["content"]
    assert '"intent":"image_generate"' in messages[1]["content"]
    assert '"prompt":"机场值机服务台"' in messages[1]["content"]
    assert '"is_immediate_context":true' in messages[1]["content"]
    assert '"is_active_context":true' in messages[1]["content"]
    assert "igt-secret" not in messages[1]["content"]
    assert "https://example.com/secret.png" not in messages[1]["content"]
    assert _IMAGE.data not in messages[1]["content"]
    assert classifier_payload_contains_base64(messages) is False


def test_source_policy_handles_zero_one_and_multiple_images():
    missing = route_media_intent(
        "把这张图片的背景换成海边",
        mode="auto",
        capabilities=_CAPABILITIES,
    )
    selected = route_media_intent(
        "把这张图片的背景换成海边",
        images=[_IMAGE],
        mode="auto",
        capabilities=_CAPABILITIES,
    )
    ambiguous = route_media_intent(
        "把这些图片的背景换成海边",
        images=[_IMAGE, _IMAGE],
        mode="auto",
        capabilities=_CAPABILITIES,
    )

    assert missing.effective_route == "clarify"
    assert missing.reason_code == "source_image_not_found"
    assert selected.effective_route == "image_edit"
    assert selected.parameters.source_image_index == 0
    assert ambiguous.effective_route == "clarify"
    assert ambiguous.reason_code == "multiple_source_images"


def test_recent_image_reference_is_owner_scoped_and_uses_latest_candidate():
    recent = [
        {
            "task_id": "igt-latest",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T09:00:00Z",
        },
        {
            "task_id": "igt-other-owner",
            "owner_user_id": "usr-other",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
        },
    ]

    decision = route_media_intent(
        "把上一张图片改成夜景",
        mode="auto",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.effective_route == "image_edit"
    assert decision.source_kind == "recent_generated_image"
    assert decision.source_task_id == "igt-latest"


def test_immediate_generated_image_context_routes_polite_follow_up_to_edit():
    recent = [
        {
            "task_id": "igt-latest",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
            "is_immediate_context": True,
        }
    ]

    decision = route_media_intent(
        "能不能改成中国国内的",
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.predicted_intent == "image_edit"
    assert decision.effective_route == "clarify"
    assert decision.route_source == "deterministic_rule"
    assert decision.reason_code == "confirmation_required"
    assert decision.source_kind == "recent_generated_image"
    assert decision.source_task_id == "igt-latest"
    assert decision.requires_confirmation is True


def test_active_generated_image_context_survives_intervening_chat_turns():
    recent = [
        {
            "task_id": "igt-active",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
            "is_immediate_context": False,
            "is_active_context": True,
        }
    ]

    decision = route_media_intent(
        "能不能改成中国国内的",
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.predicted_intent == "image_edit"
    assert decision.effective_route == "clarify"
    assert decision.source_kind == "recent_generated_image"
    assert decision.source_task_id == "igt-active"
    assert decision.requires_confirmation is True


def test_image_element_edit_uses_active_result_without_classifier():
    def unexpected_classifier(*_args, **_kwargs):
        pytest.fail("An explicit image-element edit must not need the classifier")

    decision = route_media_intent(
        "第一个字母D换成P",
        mode="confirm",
        capabilities=_CAPABILITIES,
        classifier=unexpected_classifier,
        user_id="usr-owner",
        session_id="chs-owner",
        recent_images=[
            {
                "task_id": "igt-edited-result",
                "owner_user_id": "usr-owner",
                "session_id": "chs-owner",
                "task_kind": "image_edit",
                "status": "succeeded",
                "is_active_context": True,
                "is_immediate_context": False,
            }
        ],
    )

    assert decision.predicted_intent == "image_edit"
    assert decision.effective_route == "clarify"
    assert decision.source_task_id == "igt-edited-result"
    assert decision.requires_confirmation is True


@pytest.mark.parametrize(
    ("overrides", "expected_source"),
    [
        ([{}], "igt-active-0"),
        ([{"owner_user_id": "usr-other"}], None),
        ([{"session_id": "chs-other"}], None),
        ([{"status": "running"}], None),
        ([{"is_active_context": False}], None),
        ([{}, {}], None),
    ],
)
def test_classified_edit_uses_only_unique_owned_active_result(
    overrides: list[dict], expected_source: str | None
):
    decision = route_media_intent(
        "再把M换成W",
        mode="confirm",
        capabilities=_CAPABILITIES,
        classifier=lambda *_args, **_kwargs: {
            "predicted_intent": "image_edit",
            "confidence_band": "high",
        },
        user_id="usr-owner",
        session_id="chs-owner",
        recent_images=[
            {
                "task_id": f"igt-active-{index}",
                "owner_user_id": "usr-owner",
                "session_id": "chs-owner",
                "task_kind": "image_edit",
                "status": "succeeded",
                "is_active_context": True,
                "is_immediate_context": False,
                **override,
            }
            for index, override in enumerate(overrides)
        ],
    )

    assert decision.predicted_intent == "image_edit"
    assert decision.effective_route == "clarify"
    assert decision.source_task_id == expected_source
    assert decision.final_intent is None
    if expected_source:
        assert decision.parameters.source_kind == "recent_generated_image"
        assert decision.reason_code == "confirmation_required"
        assert decision.requires_confirmation is True
    else:
        assert decision.reason_code == "missing_source_image"


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("生成一张新的星空图", "image_generate"),
        ("生成一段海边视频", "video_generate"),
    ],
)
def test_new_media_generation_does_not_reuse_active_edit_source(
    message: str, intent: str
):
    decision = route_media_intent(
        message,
        mode="confirm",
        capabilities=_CAPABILITIES,
        user_id="usr-owner",
        session_id="chs-owner",
        recent_images=[
            {
                "task_id": "igt-active",
                "owner_user_id": "usr-owner",
                "session_id": "chs-owner",
                "task_kind": "image_edit",
                "status": "succeeded",
                "is_active_context": True,
            }
        ],
    )

    assert decision.predicted_intent == intent
    assert decision.source_kind == "none"
    assert decision.source_task_id is None


def test_non_immediate_generated_image_is_not_implicitly_selected_for_edit():
    recent = [
        {
            "task_id": "igt-stale",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T09:00:00Z",
            "is_immediate_context": False,
            "is_active_context": False,
        }
    ]

    decision = route_media_intent(
        "能不能改成中国国内的",
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.predicted_intent == "chat"
    assert decision.effective_route == "chat"
    assert decision.source_task_id is None


@pytest.mark.parametrize(
    "message",
    [
        "能不能不要改成中国国内的",
        "你刚才修改了什么",
        "是否应该改成中国国内的",
        "请改成中国国内的合同",
        "如何把第一个字母D换成P",
        "不要把第一个字母D换成P",
        "把报告里的第一个字母D换成P",
        "引用：第一个字母D换成P",
        "会议时间换成下午三点",
    ],
)
def test_immediate_image_context_keeps_negation_and_analysis_in_chat(message: str):
    recent = [
        {
            "task_id": "igt-latest",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
            "is_immediate_context": True,
        }
    ]

    decision = route_media_intent(
        message,
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
        classifier=lambda *_args, **_kwargs: {
            "predicted_intent": "chat",
            "confidence_band": "high",
        },
    )

    assert decision.predicted_intent == "chat"
    assert decision.effective_route == "chat"
    assert decision.source_task_id is None


def test_explicit_recent_image_text_edit_is_not_downgraded_to_chat():
    recent = [
        {
            "task_id": "igt-latest",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
            "is_immediate_context": True,
        }
    ]

    decision = route_media_intent(
        "把这张图上的文字改成中文",
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.predicted_intent == "image_edit"
    assert decision.effective_route == "clarify"
    assert decision.source_task_id == "igt-latest"


def test_recent_image_reference_rejects_unscoped_and_cross_session_candidates():
    recent = [
        {
            "task_id": "igt-unscoped",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
        },
        {
            "task_id": "igt-other-session",
            "owner_user_id": "usr-owner",
            "session_id": "chs-other",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T09:00:00Z",
        },
    ]

    decision = route_media_intent(
        "把上一张图片改成夜景",
        mode="auto",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.effective_route == "clarify"
    assert decision.reason_code == "source_image_not_found"


def test_generic_recent_reference_requires_unique_candidate():
    recent = [
        {
            "task_id": task_id,
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": created_at,
        }
        for task_id, created_at in (
            ("igt-new", "2026-09-03T10:00:00Z"),
            ("igt-old", "2026-09-03T09:00:00Z"),
        )
    ]

    ambiguous = route_media_intent(
        "把那张图改成夜景",
        mode="auto",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )
    ordinal = route_media_intent(
        "把第二张图改成夜景",
        mode="auto",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert ambiguous.effective_route == "clarify"
    assert ambiguous.reason_code == "ambiguous_source_image"
    assert ordinal.effective_route == "image_edit"
    assert ordinal.source_task_id == "igt-old"


def test_unavailable_capability_requires_clarification():
    decision = route_media_intent(
        "生成一张产品图",
        mode="auto",
        capabilities={
            "image_generate": False,
            "image_edit": False,
            "video_generate": False,
        },
    )

    assert decision.effective_route == "clarify"
    assert decision.reason_code == "capability_unavailable"


def test_user_override_executes_only_the_pending_owned_choice():
    pending = {
        "predicted_intent": "video_generate",
        "effective_route": "clarify",
        "parameters": {
            "video_mode": "std",
            "video_duration": 5,
            "source_kind": "none",
        },
    }

    decision = route_media_intent(
        "生成一段产品动画视频",
        decision_id="dsp-pending",
        final_intent="video_generate",
        pending_decision=pending,
        mode="confirm",
        capabilities=_CAPABILITIES,
    )

    assert decision.decision_id == "dsp-pending"
    assert decision.effective_route == "video_generate"
    assert decision.route_source == "user_override"


@pytest.mark.parametrize(
    ("message", "pinned_source_id", "expected_route"),
    [
        ("把上一张图片改成夜景", "igt-confirmed", "image_edit"),
        ("再把M换成W", "igt-confirmed", "image_edit"),
        ("再把M换成W", "igt-unavailable", "clarify"),
    ],
)
def test_user_override_keeps_the_original_recent_image_candidate(
    message: str, pinned_source_id: str, expected_route: str
):
    recent = [
        {
            "task_id": "igt-newer",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T11:00:00Z",
            "is_active_context": True,
        },
        {
            "task_id": "igt-confirmed",
            "owner_user_id": "usr-owner",
            "session_id": "chs-owner",
            "task_kind": "image",
            "status": "succeeded",
            "created_at": "2026-09-03T10:00:00Z",
        },
    ]
    pending = {
        "predicted_intent": "image_edit",
        "effective_route": "clarify",
        "source_kind": "recent_generated_image",
        "source_task_id": pinned_source_id,
        "parameters": {"source_kind": "recent_generated_image"},
    }

    decision = route_media_intent(
        message,
        decision_id="dsp-pinned-source",
        final_intent="image_edit",
        pending_decision=pending,
        mode="confirm",
        capabilities=_CAPABILITIES,
        recent_images=recent,
        user_id="usr-owner",
        session_id="chs-owner",
    )

    assert decision.effective_route == expected_route
    if expected_route == "image_edit":
        assert decision.source_task_id == pinned_source_id
    else:
        assert decision.reason_code == "source_image_not_found"
        assert decision.source_task_id is None


@pytest.mark.parametrize("message", ["再把M换成W", "把上一张图片改成夜景"])
@pytest.mark.parametrize(
    ("source_kind", "source_task_id"),
    [
        ("none", None),
        ("recent_generated_image", None),
        ("recent_generated_image", ""),
        ("recent_generated_image", " "),
    ],
)
def test_confirmation_without_source_does_not_discover_later_result(
    message: str, source_kind: str, source_task_id: str | None
):
    decision = route_media_intent(
        message,
        decision_id="dsp-missing-source",
        final_intent="image_edit",
        pending_decision={
            "predicted_intent": "image_edit",
            "effective_route": "clarify",
            "source_kind": source_kind,
            "source_task_id": source_task_id,
        },
        mode="confirm",
        capabilities=_CAPABILITIES,
        user_id="usr-owner",
        session_id="chs-owner",
        recent_images=[
            {
                "task_id": "igt-later-result",
                "owner_user_id": "usr-owner",
                "session_id": "chs-owner",
                "task_kind": "image_edit",
                "status": "succeeded",
                "is_active_context": True,
                "is_immediate_context": True,
            }
        ],
    )

    assert decision.effective_route == "clarify"
    assert decision.final_intent is None
    assert decision.source_task_id is None
    assert "source_image" in decision.missing_inputs
