from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.auth import Base
from app.models.chat import ChatSession
from app.models.chat_control import ChatControlOperation
from app.repositories import chat_control_operations, usage


def test_control_operation_persists_safe_nested_usage_and_is_listed() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            recorded = chat_control_operations.record_control_operation(
                db,
                owner_user_id="usr-1",
                session_id="chs-1",
                action="steer",
                status="completed",
                usage={
                    "provider": "answer-provider",
                    "model": "answer-model",
                    "answer_model": "answer-model",
                    "answer_endpoint_id": "mep-answer",
                    "vision_model": "vision-model",
                    "vision_endpoint_id": "mep-vision",
                    "status": "completed",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "cost": "0.12",
                    "latency_ms": 25,
                    "price_version_id": "price-v1",
                    "currency": "CNY",
                    "route_mode": "vision_fallback",
                    "vision_fallback_used": True,
                    "prompt": "原始提示词不得落库",
                    "visual_context": "视觉正文不得落库",
                    "calls": [
                        {
                            "purpose": "vision_parse",
                            "provider": "vision-provider",
                            "model": "vision-model",
                            "endpoint_id": "mep-vision",
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                            "cost": "0.12",
                            "latency_ms": 20,
                            "status": "completed",
                            "price_version_id": "price-v1",
                            "currency": "CNY",
                            "prompt": "nested prompt",
                            "context": "nested visual body",
                        }
                    ],
                },
            )
            db.commit()

            stored = db.get(ChatControlOperation, recorded.id)
            assert stored is not None
            assert stored.action == "steer"
            assert stored.status == "completed"
            assert stored.error_code is None
            assert "原始图片" not in stored.usage
            assert "视觉辅助结果" not in stored.usage
            assert "原始提示词不得落库" not in stored.usage
            assert "视觉正文不得落库" not in stored.usage
            assert "nested prompt" not in stored.usage
            assert "nested visual body" not in stored.usage

            operations = usage._chat_control_operations(db, "usr-1")
            assert len(operations) == 1
            operation = operations[0]
            assert operation.feature == "chat"
            assert operation.action == "steer"
            assert operation.resource_path == "/chat/chs-1"
            assert operation.usage["answer_model"] == "answer-model"
            assert operation.usage["vision_model"] == "vision-model"
            assert operation.usage["answer_endpoint_id"] == "mep-answer"
            assert operation.usage["vision_endpoint_id"] == "mep-vision"
            assert operation.usage["currency"] == "CNY"
            assert operation.usage["price_version_id"] == "price-v1"
            assert operation.usage["calls"] == json.loads(stored.usage)["calls"]
    finally:
        Base.metadata.drop_all(engine)
