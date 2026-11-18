from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


OnboardingStage = Literal["company", "persona"]
OnboardingDecision = Literal["accept", "clarify", "suggest"]


class OnboardingHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=300)


class OnboardingMessageRequest(BaseModel):
    stage: OnboardingStage
    message: str = Field(min_length=1, max_length=300)
    company_name: str = Field(default="", max_length=80)
    history: list[OnboardingHistoryItem] = Field(default_factory=list, max_length=8)


class OnboardingMessageResponse(BaseModel):
    stage: OnboardingStage
    decision: OnboardingDecision
    reply: str
    normalized_value: str = ""
    suggestions: list[str] = Field(default_factory=list)


class OnboardingStatusResponse(BaseModel):
    required: bool
    completed: bool
    company_name: str = ""
    persona_name: str = ""


class OnboardingCompleteRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=80)
    persona_name: str = Field(min_length=1, max_length=16)
