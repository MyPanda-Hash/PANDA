from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.chat import ChatDispatchDecision, ChatMessage
from app.schemas.chat import MediaRequestInputs


DISPATCH_INTENTS = frozenset(
    {"chat", "image_generate", "image_edit", "video_generate", "clarify"}
)
DISPATCH_STATUSES = frozenset(
    {
        "resolved",
        "awaiting_confirmation",
        "dispatching",
        "dispatched",
        "failed",
        "outcome_unknown",
        "superseded",
    }
)


class DispatchConflictError(ValueError):
    """The idempotency key is already bound to different request input."""


class DispatchNotFoundError(LookupError):
    """A referenced dispatch decision is not owned by the current user."""


def digest_payload(value: object) -> str:
    """Return a stable digest without retaining the supplied payload."""
    if value is None:
        canonical = "null"
    else:
        try:
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            canonical = repr(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_digest(prompt: str) -> str:
    return digest_payload((prompt or "").strip())


def attachment_digest(attachment: object) -> str:
    return digest_payload(attachment)


def _canonical_media_options(media_options: object) -> dict[str, Any]:
    if hasattr(media_options, "model_dump"):
        try:
            media_options = media_options.model_dump(mode="json")
        except (TypeError, ValueError):
            media_options = {}
    if isinstance(media_options, Mapping):
        options = dict(media_options)
        if "duration" in options and "video_duration" not in options:
            options["video_duration"] = options.pop("duration")
    else:
        options = {}
    return {
        key: options.get(key)
        for key in (
            "resolution",
            "video_mode",
            "video_duration",
            "source_image_index",
        )
    }


def _normalize_model(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def request_input_evidence(
    prompt: str,
    attachment: object,
    *,
    media_options: object = None,
    requested_skill: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Return the non-sensitive fields needed to audit a route replay."""
    normalized_attachment = (
        None if isinstance(attachment, list) and not attachment else attachment
    )
    return {
        "prompt_digest": prompt_digest(prompt),
        "attachment_digest": attachment_digest(normalized_attachment),
        "media_options": _canonical_media_options(media_options),
        "requested_skill": (requested_skill or "").strip() or None,
        "model": _normalize_model(model),
        "mode": (mode or "").strip().lower() or None,
    }


def request_digest(
    prompt: str,
    attachment: object,
    *,
    media_options: object = None,
    requested_skill: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> str:
    """Return the stable idempotency digest for a media route request.

    The attachment bytes are represented only by their digest.  Options and
    the explicit skill are included because they can change the route without
    changing the user-visible prompt.
    """
    evidence = request_input_evidence(
        prompt,
        attachment,
        media_options=media_options,
        requested_skill=requested_skill,
        model=model,
        mode=mode,
    )
    return digest_payload(
        {
            "prompt": str(prompt or ""),
            "attachment_digest": evidence["attachment_digest"],
            "media_options": evidence["media_options"],
            "requested_skill": evidence["requested_skill"],
            "model": evidence["model"],
            "mode": evidence["mode"],
        }
    )


def _normalize_client_message_id(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("client_message_id is required")
    if len(normalized) > 128:
        raise ValueError("client_message_id is too long")
    return normalized


def _json_object(value: Mapping[str, Any] | str | None) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return "{}"
        return (
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            if isinstance(parsed, dict)
            else "{}"
        )
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    return "{}"


def decision_request_digest(decision: ChatDispatchDecision) -> str | None:
    """Read the request digest kept in the history-safe decision payload."""
    try:
        payload = json.loads(decision.decision_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("request_digest")
    return str(value).strip() if value else None


def decision_request_inputs(
    decision: ChatDispatchDecision,
) -> Mapping[str, Any] | None:
    """Read optional request evidence from a decision's history-safe payload."""
    try:
        payload = json.loads(decision.decision_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("request_inputs", "request_evidence", "request"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    direct_keys = (
        "prompt_digest",
        "prompt",
        "attachment_digest",
        "attachment",
        "images",
        "media_options",
        "request_media_options",
        "options",
        "requested_skill",
        "explicit_skill",
        "skill",
        "model",
        "request_model",
        "mode",
        "media_mode",
    )
    direct = {key: payload[key] for key in direct_keys if key in payload}
    return direct or None


def history_safe_request_inputs(
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project only original media controls safe for SSE/history responses.

    ``decision_request_inputs`` remains intentionally broad for validating
    legacy digest evidence.  This projection must never expose prompts,
    attachments, digests, routing mode, or any future evidence fields.
    """
    if not isinstance(evidence, Mapping):
        return None
    option_keys = ("media_options", "request_media_options", "options")
    skill_keys = ("requested_skill", "explicit_skill", "skill")
    model_keys = ("model", "request_model")
    allowed_keys = option_keys + skill_keys + model_keys
    if not any(key in evidence for key in allowed_keys):
        return None

    raw_options = next(
        (evidence[key] for key in option_keys if key in evidence),
        None,
    )
    raw_skill = next(
        (evidence[key] for key in skill_keys if key in evidence),
        None,
    )
    raw_model = next(
        (evidence[key] for key in model_keys if key in evidence),
        None,
    )
    if raw_skill is not None and not isinstance(raw_skill, str):
        return None
    if raw_model is not None and not isinstance(raw_model, str):
        return None
    raw_skill = (raw_skill.strip() or None) if raw_skill is not None else None
    raw_model = (raw_model.strip() or None) if raw_model is not None else None
    try:
        snapshot_values: dict[str, Any] = {
            "requested_skill": raw_skill,
            "model": raw_model,
        }
        if raw_options is not None:
            snapshot_values["media_options"] = raw_options
        snapshot = MediaRequestInputs(**snapshot_values)
    except (TypeError, ValueError):
        return None
    return snapshot.model_dump(mode="json")


def _decision_json_with_request_digest(
    value: Mapping[str, Any] | str | None,
    request_digest_value: str | None,
) -> str:
    payload_text = _json_object(value)
    if not request_digest_value:
        return payload_text
    try:
        payload = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["request_digest"] = request_digest_value
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_dispatch_decision(
    db: Session,
    *,
    owner_user_id: str,
    client_message_id: str,
    for_update: bool = False,
) -> ChatDispatchDecision | None:
    client_message_id = _normalize_client_message_id(client_message_id)
    statement = select(ChatDispatchDecision).where(
        ChatDispatchDecision.owner_user_id == owner_user_id,
        ChatDispatchDecision.client_message_id == client_message_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def get_dispatch_decision_by_id(
    db: Session,
    *,
    decision_id: str,
    owner_user_id: str,
    for_update: bool = False,
) -> ChatDispatchDecision | None:
    statement = select(ChatDispatchDecision).where(
        ChatDispatchDecision.id == decision_id,
        ChatDispatchDecision.owner_user_id == owner_user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _assert_request_matches(
    decision: ChatDispatchDecision,
    *,
    chat_session_id: str,
    predicted_intent: str,
    effective_route: str,
    prompt_digest_value: str,
    attachment_digest_value: str,
    request_digest_value: str | None = None,
) -> None:
    if decision.chat_session_id != chat_session_id:
        raise DispatchConflictError("client_message_id 已绑定到其他会话")
    stored_request_digest = decision_request_digest(decision)
    if stored_request_digest and request_digest_value:
        if stored_request_digest != request_digest_value:
            raise DispatchConflictError("client_message_id 已绑定到其他请求")
        return
    if decision.predicted_intent != predicted_intent:
        raise DispatchConflictError("client_message_id 已绑定到其他意图")
    if decision.effective_route != effective_route:
        raise DispatchConflictError("client_message_id 已绑定到其他执行路径")
    if (decision.prompt_digest or "") != (prompt_digest_value or ""):
        raise DispatchConflictError("client_message_id 已绑定到其他提示词")
    if (decision.attachment_digest or "") != (attachment_digest_value or ""):
        raise DispatchConflictError("client_message_id 已绑定到其他附件")


def create_dispatch_decision(
    db: Session,
    *,
    owner_user_id: str,
    chat_session_id: str,
    client_message_id: str,
    requested_skill: str | None = None,
    predicted_intent: str = "chat",
    effective_route: str = "chat",
    final_intent: str | None = None,
    route_source: str = "classifier",
    confidence_band: str = "low",
    reason_code: str = "unspecified",
    classifier_model: str | None = None,
    classifier_version: str | None = None,
    decision_json: Mapping[str, Any] | str | None = None,
    classifier_usage_json: Mapping[str, Any] | str | None = None,
    status: str = "resolved",
    target_type: str | None = None,
    target_id: str | None = None,
    prompt_digest: str = "",
    attachment_digest: str = "",
    request_digest: str | None = None,
    decision_id: str | None = None,
) -> ChatDispatchDecision:
    client_message_id = _normalize_client_message_id(client_message_id)
    if predicted_intent not in DISPATCH_INTENTS:
        raise ValueError("unsupported predicted_intent")
    if effective_route not in DISPATCH_INTENTS:
        raise ValueError("unsupported effective_route")
    if status not in DISPATCH_STATUSES:
        raise ValueError("unsupported dispatch status")
    decision = ChatDispatchDecision(
        id=decision_id or f"dsp_{new_token(10)}",
        owner_user_id=owner_user_id,
        chat_session_id=chat_session_id,
        client_message_id=client_message_id,
        requested_skill=requested_skill,
        predicted_intent=predicted_intent,
        effective_route=effective_route,
        final_intent=final_intent,
        route_source=route_source,
        confidence_band=confidence_band,
        reason_code=reason_code,
        classifier_model=classifier_model,
        classifier_version=classifier_version,
        decision_json=_decision_json_with_request_digest(
            decision_json, request_digest
        ),
        classifier_usage_json=(
            _json_object(classifier_usage_json)
            if classifier_usage_json is not None
            else None
        ),
        prompt_digest=prompt_digest or "",
        attachment_digest=attachment_digest or "",
        status=status,
        target_type=target_type,
        target_id=target_id,
        created_at=now(),
        updated_at=now(),
    )
    db.add(decision)
    return decision


def get_or_create_dispatch_decision(
    db: Session,
    *,
    owner_user_id: str,
    chat_session_id: str,
    client_message_id: str,
    requested_skill: str | None = None,
    predicted_intent: str = "chat",
    effective_route: str = "chat",
    final_intent: str | None = None,
    route_source: str = "classifier",
    confidence_band: str = "low",
    reason_code: str = "unspecified",
    classifier_model: str | None = None,
    classifier_version: str | None = None,
    decision_json: Mapping[str, Any] | str | None = None,
    classifier_usage_json: Mapping[str, Any] | str | None = None,
    status: str = "resolved",
    target_type: str | None = None,
    target_id: str | None = None,
    prompt_digest: str = "",
    attachment_digest: str = "",
    request_digest: str | None = None,
    decision_id: str | None = None,
) -> tuple[ChatDispatchDecision, bool]:
    client_message_id = _normalize_client_message_id(client_message_id)
    existing = get_dispatch_decision(
        db,
        owner_user_id=owner_user_id,
        client_message_id=client_message_id,
        for_update=True,
    )
    if existing is not None:
        _assert_request_matches(
            existing,
            chat_session_id=chat_session_id,
            predicted_intent=predicted_intent,
            effective_route=effective_route,
            prompt_digest_value=prompt_digest or "",
            attachment_digest_value=attachment_digest or "",
            request_digest_value=request_digest,
        )
        return existing, False

    try:
        # Add and flush inside the savepoint. Session.begin_nested() flushes
        # pre-existing state before opening the savepoint, so the new object
        # must not be pending before this block.
        with db.begin_nested():
            decision = create_dispatch_decision(
                db,
                owner_user_id=owner_user_id,
                chat_session_id=chat_session_id,
                client_message_id=client_message_id,
                requested_skill=requested_skill,
                predicted_intent=predicted_intent,
                effective_route=effective_route,
                final_intent=final_intent,
                route_source=route_source,
                confidence_band=confidence_band,
                reason_code=reason_code,
                classifier_model=classifier_model,
                classifier_version=classifier_version,
                decision_json=decision_json,
                classifier_usage_json=classifier_usage_json,
                status=status,
                target_type=target_type,
                target_id=target_id,
                prompt_digest=prompt_digest,
                attachment_digest=attachment_digest,
                request_digest=request_digest,
                decision_id=decision_id,
            )
            db.flush()
    except IntegrityError:
        winner = get_dispatch_decision(
            db,
            owner_user_id=owner_user_id,
            client_message_id=client_message_id,
            for_update=True,
        )
        if winner is None:
            raise
        _assert_request_matches(
            winner,
            chat_session_id=chat_session_id,
            predicted_intent=predicted_intent,
            effective_route=effective_route,
            prompt_digest_value=prompt_digest or "",
            attachment_digest_value=attachment_digest or "",
            request_digest_value=request_digest,
        )
        return winner, False
    return decision, True


def update_dispatch_decision(
    decision: ChatDispatchDecision,
    *,
    status: str | None = None,
    effective_route: str | None = None,
    final_intent: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    dispatched_at: object = None,
) -> ChatDispatchDecision:
    if status is not None:
        if status not in DISPATCH_STATUSES:
            raise ValueError("unsupported dispatch status")
        decision.status = status
    if effective_route is not None:
        if effective_route not in DISPATCH_INTENTS:
            raise ValueError("unsupported effective_route")
        decision.effective_route = effective_route
    if final_intent is not None:
        decision.final_intent = final_intent
    if target_type is not None:
        decision.target_type = target_type
    if target_id is not None:
        decision.target_id = target_id
    if dispatched_at is not None:
        decision.dispatched_at = dispatched_at  # type: ignore[assignment]
    decision.updated_at = now()
    return decision


def confirm_dispatch_decision(
    db: Session,
    *,
    decision_id: str,
    owner_user_id: str,
    chat_session_id: str,
    client_message_id: str,
    final_intent: str,
    effective_route: str,
    route_source: str,
    confidence_band: str,
    reason_code: str,
    classifier_model: str | None,
    classifier_version: str | None,
    decision_json: Mapping[str, Any] | str | None,
    classifier_usage_json: Mapping[str, Any] | str | None,
    request_digest_value: str | None = None,
    requested_skill: str | None = None,
    status: str | None = None,
) -> tuple[ChatDispatchDecision, bool]:
    """CAS the first confirmation while preserving task ownership.

    Media confirmations remain ``awaiting_confirmation`` until the dispatch
    service claims the durable task lease.  This keeps a concurrent replay
    from creating a second task between confirmation and task creation.
    """
    decision = get_dispatch_decision_by_id(
        db,
        decision_id=decision_id,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    if decision is None:
        raise DispatchNotFoundError("路由决策不存在")
    if decision.chat_session_id != chat_session_id:
        raise DispatchConflictError("路由决策与会话不匹配")
    if decision.client_message_id != client_message_id:
        raise DispatchConflictError("路由决策与 client_message_id 不匹配")
    stored_request_digest = decision_request_digest(decision)
    if (
        stored_request_digest
        and request_digest_value
        and stored_request_digest != request_digest_value
    ):
        raise DispatchConflictError("client_message_id 已绑定到其他请求")
    if decision.final_intent and decision.final_intent != final_intent:
        raise DispatchConflictError("路由决策已确认其他意图")
    if decision.status != "awaiting_confirmation":
        if decision.final_intent == final_intent:
            return decision, False
        raise DispatchConflictError("路由决策已确认其他意图")
    if status is not None and status not in DISPATCH_STATUSES:
        raise ValueError("unsupported dispatch status")

    values: dict[str, Any] = {
        "effective_route": effective_route,
        "final_intent": final_intent,
        "route_source": route_source,
        "confidence_band": confidence_band,
        "reason_code": reason_code,
        "classifier_model": classifier_model,
        "classifier_version": classifier_version,
        "decision_json": _decision_json_with_request_digest(
            decision_json, request_digest_value
        ),
        "classifier_usage_json": (
            _json_object(classifier_usage_json)
            if classifier_usage_json is not None
            else None
        ),
        "updated_at": now(),
    }
    if requested_skill is not None:
        values["requested_skill"] = requested_skill
    if status is not None:
        values["status"] = status
    result = db.execute(
        update(ChatDispatchDecision)
        .where(
            ChatDispatchDecision.id == decision.id,
            ChatDispatchDecision.owner_user_id == owner_user_id,
            ChatDispatchDecision.status == "awaiting_confirmation",
            ChatDispatchDecision.final_intent.is_(None),
        )
        .values(**values)
    )
    db.flush()
    refreshed = get_dispatch_decision_by_id(
        db,
        decision_id=decision.id,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    if refreshed is None:
        raise DispatchNotFoundError("路由决策不存在")
    if getattr(result, "rowcount", 0) == 1:
        return refreshed, True
    if refreshed.final_intent == final_intent:
        return refreshed, False
    raise DispatchConflictError("路由决策已确认其他意图")


def dispatch_decision_to_dict(decision: ChatDispatchDecision) -> dict[str, Any]:
    try:
        parsed = json.loads(decision.decision_json or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    parameters = parsed.get("parameters", {}) if isinstance(parsed, dict) else {}
    if not isinstance(parameters, dict):
        parameters = {}
    request_inputs = history_safe_request_inputs(
        decision_request_inputs(decision)
    )
    return {
        "decision_id": decision.id,
        "predicted_intent": decision.predicted_intent,
        "effective_route": decision.effective_route,
        "final_intent": decision.final_intent,
        "route_source": decision.route_source,
        "confidence_band": decision.confidence_band,
        "reason_code": decision.reason_code,
        "requires_confirmation": bool(parsed.get("requires_confirmation", False))
        if isinstance(parsed, dict)
        else False,
        "parameters": parameters,
        "request_inputs": request_inputs,
        "clarification_message": (
            parsed.get("clarification_message")
            if isinstance(parsed, dict)
            else None
        ),
        "clarification_choices": (
            parsed.get("clarification_choices", [])
            if isinstance(parsed, dict)
            else []
        ),
        "status": decision.status,
    }


def get_message_dispatch_decision(
    db: Session,
    message_id: str,
    owner_user_id: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    statement = (
        select(ChatDispatchDecision)
        .join(ChatMessage, ChatMessage.dispatch_id == ChatDispatchDecision.id)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.owner_user_id == owner_user_id,
            ChatDispatchDecision.owner_user_id == owner_user_id,
        )
    )
    if session_id is not None:
        statement = statement.where(ChatMessage.session_id == session_id)
    decision = db.scalar(statement)
    return dispatch_decision_to_dict(decision) if decision is not None else None


def record_dispatch_feedback(
    decision: ChatDispatchDecision,
    *,
    corrected_intent: str | None,
) -> ChatDispatchDecision:
    if corrected_intent is not None and corrected_intent not in DISPATCH_INTENTS:
        raise ValueError("unsupported corrected_intent")
    try:
        payload = json.loads(decision.decision_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["feedback"] = {
        "reported_wrong": True,
        "corrected_intent": corrected_intent,
        "reported_at": now().isoformat(),
    }
    decision.decision_json = _json_object(payload)
    decision.updated_at = now()
    return decision


def aggregate_dispatch_metrics(
    db: Session,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    dimensions = (
        ChatDispatchDecision.predicted_intent,
        ChatDispatchDecision.effective_route,
        ChatDispatchDecision.route_source,
        ChatDispatchDecision.reason_code,
        ChatDispatchDecision.classifier_version,
        ChatDispatchDecision.status,
    )
    grouped = select(*dimensions, func.count(ChatDispatchDecision.id)).group_by(
        *dimensions
    )
    usage_rows = select(
        ChatDispatchDecision.decision_json,
        ChatDispatchDecision.classifier_usage_json,
    )
    if since is not None:
        grouped = grouped.where(ChatDispatchDecision.created_at >= since)
        usage_rows = usage_rows.where(ChatDispatchDecision.created_at >= since)

    groups = [
        {
            "predicted_intent": row[0],
            "effective_route": row[1],
            "route_source": row[2],
            "reason_code": row[3],
            "classifier_version": row[4],
            "status": row[5],
            "count": int(row[6]),
        }
        for row in db.execute(grouped)
    ]
    classifier_input_tokens = 0
    classifier_output_tokens = 0
    classifier_cost = Decimal("0")
    reported_wrong = 0
    corrected_intents: dict[str, int] = {}
    for decision_raw, usage_raw in db.execute(usage_rows):
        try:
            decision_payload = json.loads(decision_raw or "{}")
        except (TypeError, json.JSONDecodeError):
            decision_payload = {}
        feedback = (
            decision_payload.get("feedback")
            if isinstance(decision_payload, dict)
            else None
        )
        if isinstance(feedback, dict) and feedback.get("reported_wrong") is True:
            reported_wrong += 1
            corrected = str(feedback.get("corrected_intent") or "unspecified")
            corrected_intents[corrected] = corrected_intents.get(corrected, 0) + 1
        try:
            usage = json.loads(usage_raw or "{}")
        except (TypeError, json.JSONDecodeError):
            usage = {}
        if not isinstance(usage, dict):
            continue
        try:
            classifier_input_tokens += int(usage.get("input_tokens") or 0)
            classifier_output_tokens += int(usage.get("output_tokens") or 0)
            classifier_cost += Decimal(str(usage.get("cost") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return {
        "total": sum(group["count"] for group in groups),
        "reported_wrong": reported_wrong,
        "corrected_intents": corrected_intents,
        "classifier_usage": {
            "input_tokens": classifier_input_tokens,
            "output_tokens": classifier_output_tokens,
            "cost": str(classifier_cost),
        },
        "groups": groups,
    }
