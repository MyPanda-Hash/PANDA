from pydantic import BaseModel, Field


class RoleCreateRequest(BaseModel):
    code: str = Field(pattern="^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    permission_codes: list[str] = []


class PermissionResponse(BaseModel):
    code: str
    name: str
    description: str


class RoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str
    scope: str
    builtin: bool
    permissions: list[str]

class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permission_codes: list[str] | None = None


class UserRolesUpdateRequest(BaseModel):
    role_ids: list[str] = Field(default_factory=list)
