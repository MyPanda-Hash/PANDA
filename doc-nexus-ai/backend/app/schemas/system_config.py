from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Importance = Literal["required", "recommended", "optional"]


class SystemConfigOption(BaseModel):
    value: str
    label: str


class SystemConfigItem(BaseModel):
    key: str
    value: str
    label: str = ""
    description: str
    default_value: str = ""
    group: str = "general"
    group_label: str = "通用"
    importance: Importance = "optional"
    secret: bool = False
    deprecated: bool = False
    multiline: bool = False
    options: list[SystemConfigOption] = Field(default_factory=list)


class SystemConfigGroup(BaseModel):
    id: str
    label: str
    description: str = ""
    items: list[SystemConfigItem] = Field(default_factory=list)


class SystemConfigResponse(BaseModel):
    items: list[SystemConfigItem]
    groups: list[SystemConfigGroup] = Field(default_factory=list)


# 长文本人设提示词允许更长；其余配置仍保持紧凑上限。
VALUE_MAX_LENGTH = 2000
PERSONA_PROMPT_MAX_LENGTH = 8000
_PERSONA_PROMPT_KEY = "pi_persona_system_prompt"


class SystemConfigUpdateItem(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    # max_length 取最宽项的上限，具体按 key 在下面 validator 收紧。
    value: str = Field(min_length=0, max_length=PERSONA_PROMPT_MAX_LENGTH)

    @model_validator(mode="after")
    def _enforce_value_length(self) -> SystemConfigUpdateItem:
        limit = (
            PERSONA_PROMPT_MAX_LENGTH
            if self.key == _PERSONA_PROMPT_KEY
            else VALUE_MAX_LENGTH
        )
        if len(self.value) > limit:
            raise ValueError(f"{self.key} 的值长度不能超过 {limit} 个字符")
        return self


class SystemConfigUpdateRequest(BaseModel):
    items: list[SystemConfigUpdateItem] = Field(min_length=1, max_length=50)
