from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    mes_project_code: str | None = Field(default=None, max_length=64)


class GroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class GroupMemberAddRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: str = Field(default="member", pattern="^(manager|member)$")


class GroupMemberResponse(BaseModel):
    user_id: str
    display_name: str
    avatar_url: str | None = None
    username: str
    role: str
    joined_at: datetime


class GroupSummaryResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    owner_user_id: str
    owner_display_name: str | None = None
    member_count: int = 0
    unread_count: int = 0
    my_role: str | None = None
    updated_at: datetime


class GroupDetailResponse(GroupSummaryResponse):
    mes_project_code: str | None = None
    members: list[GroupMemberResponse] = []
    memory_summary: str = ""
    memory_message_count: int = 0


class AttachmentResponse(BaseModel):
    id: str
    kind: str
    filename: str
    mime_type: str
    size_bytes: int
    extract_status: str
    transcript: str | None = None
    url: str


class MessageCreateRequest(BaseModel):
    kind: str = Field(default="text", pattern="^(text|image|file|voice)$")
    content: str = Field(default="", max_length=8000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    reply_to_message_id: str | None = Field(default=None, max_length=64)


class QuotedMessage(BaseModel):
    id: str
    sender_name: str
    content: str


class MessageResponse(BaseModel):
    id: str
    sender_user_id: str | None = None
    sender_name: str
    sender_avatar_url: str | None = None
    sender_kind: str
    kind: str
    content: str
    attachments: list[AttachmentResponse] = []
    parts_json: str | None = None
    mine: bool = False
    read_count: int = 0          # 我的消息被多少人读过
    read_total: int = 0          # 除自己以外的群成员数
    quoted: QuotedMessage | None = None
    recalled: bool = False
    created_at: datetime


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    latest_message_id: str | None = None
    unread_from_id: str | None = None   # 从这个消息之后都是未读
    read_up_to_id: str | None = None


class TodoResponse(BaseModel):
    id: str
    group_id: str | None = None
    group_name: str | None = None
    assignee_user_id: str
    assignee_display_name: str | None = None
    title: str
    detail: str
    priority: str
    status: str
    due_at: datetime | None = None
    origin: str
    source_message_id: str | None = None
    confidence: float | None = None
    created_at: datetime
    # 跟进痕迹：详情面板要显示「催过几次、什么时候升级过」
    refs: list[dict] = []
    nudge_count: int = 0
    nudged_at: datetime | None = None
    escalated_at: datetime | None = None
    last_action_at: datetime | None = None


class MarkReadRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)


class TodoStatusRequest(BaseModel):
    status: str = Field(pattern="^(pending|accepted|doing|done|dismissed)$")


class AnalyzeResponse(BaseModel):
    run_id: str
    status: str
    todos_created: int
    summary: str = ""
    message_id: str | None = None
    error: str | None = None


class DirectoryUserResponse(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None
    username: str
    email: str | None = None
