from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    """`username` accepts either the account username or email address."""

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class InviteRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    email: EmailStr | None = None
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email", mode="before")
    @classmethod
    def empty_email_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AdminCreateUserRequest(InviteRequest):
    """Create via invite link, or with an initial password for immediate login."""

    password: str | None = Field(default=None, min_length=8, max_length=256)


class AdminResetPasswordRequest(BaseModel):
    """Admin-set password; omit to auto-generate a temporary password."""

    new_password: str | None = Field(default=None, min_length=8, max_length=256)


class AcceptInviteRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)
    # 企业模式下必须与当前企业告知版本一致；非企业模式可省略。
    consent_version: str | None = Field(default=None, max_length=64)


class InvitationInspectResponse(BaseModel):
    username: str
    email: EmailStr | None = None
    display_name: str
    expires_at: str
    # 企业模式时返回企业名称与当前告知版本，前端据此展示隐私说明并回传版本。
    enterprise_enabled: bool = False
    enterprise_name: str = ""
    consent_version: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)


class SetupPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)


class SetupStatusResponse(BaseModel):
    required: bool
    username: str
    display_name: str
    linkmind_enabled: bool = False


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    email: EmailStr | None = None
    display_name: str
    avatar_url: str | None = None
    role: str
    first_login: bool
    permissions: list[str] = Field(default_factory=list)
    linkmind_sso_url: str | None = None
    linkmind_enabled: bool = False
    company_name: str = ""
    persona_name: str = ""


class DisplayNameUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class ApiKeyItem(BaseModel):
    id: str
    api_key: str
    status: str
    created_at: str


class ApiKeyResponse(BaseModel):
    items: list[ApiKeyItem]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
