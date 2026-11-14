"""项目群聊：项目组、成员、消息、附件、AI 分析记录、待办。

与个人对话（``chat_sessions``，按 ``owner_user_id`` 私有）不同，项目群聊是
多人群组：一个 ``ProjectGroup`` 有组长（``owner``）、管理员（``manager``）和
普通成员（``member``），消息对该组成员可见。

AI 助手常驻在群里，读消息与附件后产出 ``GroupTodo``，按角色派给具体成员。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base

GROUP_ROLES = ("owner", "manager", "member")
GROUP_STATUSES = ("active", "archived")
MESSAGE_KINDS = ("text", "image", "file", "voice", "ai", "system")
MESSAGE_SENDER_KINDS = ("user", "ai", "system")
ATTACHMENT_KINDS = ("image", "file", "voice")
EXTRACT_STATUSES = ("pending", "done", "failed", "skipped")
AI_RUN_TRIGGERS = ("message", "attachment", "manual")
AI_RUN_STATUSES = ("queued", "running", "done", "failed")
TODO_STATUSES = ("pending", "accepted", "doing", "done", "dismissed")
TODO_ORIGINS = ("ai", "human")
TODO_PRIORITIES = ("low", "normal", "high", "urgent")


class ProjectGroup(Base):
    __tablename__ = "project_groups"
    __table_args__ = (
        CheckConstraint(
            "status in ('active', 'archived')", name="ck_project_groups_status"
        ),
        Index("ix_project_groups_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 可选的业务侧关联（MES 项目号），为空表示纯 AI 侧项目组。
    mes_project_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # 群聊里的资料归属到哪个知识空间，为空表示不建空间。
    knowledge_space_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    members: Mapped[list[ProjectGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    messages: Mapped[list[ProjectGroupMessage]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class ProjectGroupMember(Base):
    __tablename__ = "project_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
        CheckConstraint(
            "role in ('owner', 'manager', 'member')", name="ck_group_members_role"
        ),
        Index("ix_group_members_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member")
    invited_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 已读到哪条消息，用于未读数。
    last_read_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    group: Mapped[ProjectGroup] = relationship(back_populates="members")


class ProjectGroupMessage(Base):
    __tablename__ = "project_group_messages"
    __table_args__ = (
        Index("ix_group_messages_group_created", "group_id", "created_at"),
        CheckConstraint(
            "kind in ('text', 'image', 'file', 'voice', 'ai', 'system')",
            name="ck_group_messages_kind",
        ),
        CheckConstraint(
            "sender_kind in ('user', 'ai', 'system')",
            name="ck_group_messages_sender_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), index=True
    )
    sender_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_kind: Mapped[str] = mapped_column(String(16), default="user")
    kind: Mapped[str] = mapped_column(String(16), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    # 结构化片段（与个人对话的 parts_json 同构），供前端渲染卡片。
    parts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[str] = mapped_column(Text, default="[]")
    reply_to_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    group: Mapped[ProjectGroup] = relationship(back_populates="messages")
    attachments: Mapped[list[ProjectGroupAttachment]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class ProjectGroupAttachment(Base):
    __tablename__ = "project_group_attachments"
    __table_args__ = (
        Index("ix_group_attachments_group_created", "group_id", "created_at"),
        CheckConstraint(
            "kind in ('image', 'file', 'voice')", name="ck_group_attachments_kind"
        ),
        CheckConstraint(
            "extract_status in ('pending', 'done', 'failed', 'skipped')",
            name="ck_group_attachments_extract_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_group_messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    uploader_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="file")
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # AI 读文件用：抽出的纯文本；语音则存识别结果。
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extract_status: Mapped[str] = mapped_column(String(16), default="pending")
    extract_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    message: Mapped[ProjectGroupMessage | None] = relationship(
        back_populates="attachments"
    )


class ProjectGroupAiRun(Base):
    """一次「读群聊 → 产出待办」的分析记录，便于复盘与失败重试。"""

    __tablename__ = "project_group_ai_runs"
    __table_args__ = (
        Index("ix_group_ai_runs_group_created", "group_id", "created_at"),
        CheckConstraint(
            "trigger in ('message', 'attachment', 'manual')",
            name="ck_group_ai_runs_trigger",
        ),
        CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed')",
            name="ck_group_ai_runs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # 分析输入的消息/附件 id 列表，便于「这条待办从哪来」溯源。
    input_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    todos_created: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GroupTodo(Base):
    """群聊里 AI 或人指定的待办，派给具体成员。"""

    __tablename__ = "group_todos"
    __table_args__ = (
        Index("ix_group_todos_assignee_status", "assignee_user_id", "status"),
        Index("ix_group_todos_group_status", "group_id", "status"),
        CheckConstraint(
            "status in ('pending', 'accepted', 'doing', 'done', 'dismissed')",
            name="ck_group_todos_status",
        ),
        CheckConstraint("origin in ('ai', 'human')", name="ck_group_todos_origin"),
        CheckConstraint(
            "priority in ('low', 'normal', 'high', 'urgent')",
            name="ck_group_todos_priority",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    assignee_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    origin: Mapped[str] = mapped_column(String(16), default="ai")
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_attachment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # AI 主动监控产生的待办会记下来源事件 id 列表（可能一条待办汇总多张单据）
    assistant_event_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    # 主题键：同一件事的不同细节要并进同一条待办（见 todo_topics）
    topic: Mapped[str] = mapped_column(String(160), default="", index=True)
    # 关联的 MES 单据：[{panel, docNo, label}]，用来生成跳转链接
    refs_json: Mapped[str] = mapped_column(Text, default="[]")
    # 催办痕迹：状态由人改，这里只记 AI 跟进了几次、什么时候跟的。
    # last_action_at 只在「人」改状态时更新——AI 催办也会改 updated_at，
    # 拿 updated_at 当「多久没动」会被自己催一次就清零。
    last_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    nudged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    nudge_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_group_ai_runs.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectGroupMemory(Base):
    """群的长期记忆：更早对话的滚动摘要，防止上下文无限增长后丢信息。

    每跑一次群助手分析，模型可以把「已有纪要 + 新增消息」合并成一段新纪要，
    存这里并记下覆盖到哪条消息；下次只需给模型「纪要 + 之后的消息」。
    """

    __tablename__ = "project_group_memories"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_group_memories_group"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    covered_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covered_message_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
