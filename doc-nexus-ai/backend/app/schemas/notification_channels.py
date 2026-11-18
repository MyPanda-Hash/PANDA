"""外部通知配置输入：拒绝未知字段，凭据只允许写入不允许回显。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionCreate(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["smtp", "dingtalk_group", "wecom_group"]
    config: dict = Field(default_factory=dict)
    credential: dict
    status: Literal["active", "disabled"] = "active"


class ConnectionPatch(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None
    credential: dict | None = None
    status: Literal["active", "disabled"] | None = None


class GroupTargetCreate(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    connection_id: str
    label: str = Field(min_length=1, max_length=120)
    consent: Literal[True]


class GroupTargetPatch(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "disabled"] | None = None


class GrantRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    user_id: str


class RequestId(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)


class EmailTargetCreate(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    connection_id: str
    label: str = Field(min_length=1, max_length=120)
    address: EmailStr
    consent: Literal[True]


class VerifyRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


class PreferencePatch(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    external_enabled: bool
