"""Pi 聊天回答边界：只依据原生输入身份与完成事件，不按正文猜测。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.core.security import iso, now
from app.services.pi_runtime.events import PiEventStreamRedactor, pi_events_to_chat_parts

_LOCAL_FILE_LINK = re.compile(
    r"!?\[([^\]\n]+)\]\(<?((?:sandbox:|file:|/workspace/|workspace/|/mnt/data/)[^)\n>]*)>?\)",
    re.IGNORECASE,
)


def _process_reply_file_links(text: str, declared_paths: set[str] | None = None) -> str:
    """统一跳过代码区；本地链接展示与完整路径归属采用同一组真实链接。"""
    def replace_link(match: re.Match[str]) -> str:
        if declared_paths is not None:
            workspace_path = re.fullmatch(r"(?:sandbox:)?/workspace/(.+)", match.group(2))
            if workspace_path:
                declared_paths.add(workspace_path.group(1))
        return match.group(1)

    output: list[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            output.append(line)
            continue
        if fence:
            output.append(line)
            continue
        cursor = 0
        chunks: list[str] = []
        for code in re.finditer(r"(`+)(.+?)\1(?!`)", line):
            chunks.append(_LOCAL_FILE_LINK.sub(replace_link, line[cursor:code.start()]))
            chunks.append(code.group(0))
            cursor = code.end()
        chunks.append(_LOCAL_FILE_LINK.sub(replace_link, line[cursor:]))
        output.append("".join(chunks))
    return "".join(output)


def safe_reply_file_links(text: str) -> str:
    """本地路径不是下载入口；仅移除明确本地链接目标，保留标签及代码原文。"""
    return _process_reply_file_links(text)


@dataclass
class ReplySegment:
    id: str
    source_user_id: str
    created_at: str
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    frozen: bool = False
    incomplete: bool = False
    persisted: bool = False
    started: bool = False
    native_content: str = ""

    def text(self) -> str:
        return "".join(
            str(part.get("text") or "")
            for part in pi_events_to_chat_parts(
                self.events, stream_redactor=PiEventStreamRedactor()
            )
            if part.get("type") == "content"
        )


class ChatReplySegments:
    def __init__(self, root_id: str, user_payload: dict[str, Any]) -> None:
        self.root_id = root_id
        self.user_payload = user_payload
        self.clock = now()
        self.current = ReplySegment(root_id, user_payload["id"], self.tick())
        self.segments = [self.current]
        self.controls: dict[str, dict[str, Any]] = {}
        self.order = [user_payload["id"], root_id]
        self.native_owners: dict[str, ReplySegment] = {}
        self.ended_native: set[str] = set()
        self.tool_owners: dict[str, ReplySegment] = {}
        self.native_sequence = 0

    def tick(self) -> str:
        self.clock += timedelta(milliseconds=1)
        return iso(self.clock)

    def new_id(self, identity: str) -> str:
        digest = hashlib.sha256(f"{self.root_id}\0{identity}".encode()).hexdigest()[:40]
        return f"msg_reply_{digest}"

    def register_control(self, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        identity = payload["id"]
        previous = self.controls.get(identity)
        if previous is not None:
            if previous["payload"] != payload or previous["mode"] != mode:
                raise ValueError("control input id conflicts with a different request")
            return previous
        control = {"payload": payload, "mode": mode, "consumed": False, "accepted": False}
        self.controls[identity] = control
        return control

    def consume(self, identity: str) -> ReplySegment:
        control = self.controls[identity]
        control["consumed"] = True
        control["accepted"] = True
        if self.current.frozen:
            self.current = ReplySegment(self.new_id(identity), identity, self.tick())
            self.segments.append(self.current)
            self.order.extend([identity, self.current.id])
        else:
            # 尚无正文的引导沿用当前回答，工具轨迹保持原样。
            self.current.source_user_id = identity
            self.order.insert(self.order.index(self.current.id), identity)
        control["assistant_id"] = self.current.id
        control["user_created_at"] = self.tick()
        return self.current

    def native_key(self, message: dict[str, Any]) -> str:
        value = message.get("timestamp")
        response = message.get("responseId")
        return f"{value}:{response or ''}" if value is not None or response else ""

    def event_segment(self, event: dict[str, Any]) -> tuple[ReplySegment, bool]:
        message = event.get("message")
        kind = event.get("type")
        if isinstance(message, dict) and message.get("role") == "assistant":
            key = self.native_key(message)
            if kind == "message_end" and key and key in self.ended_native:
                return self.native_owners.get(key, self.current), True
            owner = self.native_owners.get(key) if key else None
            if owner is None:
                if self.current.frozen:
                    self.native_sequence += 1
                    self.current = ReplySegment(
                        self.new_id(f"native:{key or self.native_sequence}"),
                        self.current.source_user_id, self.tick(),
                    )
                    self.segments.append(self.current)
                    self.order.append(self.current.id)
                owner = self.current
                if key:
                    self.native_owners[key] = owner
            if kind == "message_end" and key:
                self.ended_native.add(key)
            return owner, False
        tool_id = str(event.get("toolCallId") or "")
        if tool_id:
            return self.tool_owners.setdefault(tool_id, self.current), False
        return self.current, False

    def ordered_ids(self) -> list[str]:
        return self.order + [
            identity for identity, control in self.controls.items()
            if control["accepted"] and not control["consumed"]
        ]

    def files_by_reply(self, files: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """只用明确的完整工作区链接关联文件；无唯一归属时沿用运行末段交付。"""
        declarations: dict[str, set[str]] = {}
        for segment in self.segments:
            declared_paths: set[str] = set()
            _process_reply_file_links(segment.native_content, declared_paths)
            declarations[segment.id] = declared_paths
        grouped: dict[str, list[dict[str, Any]]] = {}
        for generated in files:
            path = str(generated.get("path") or "")
            candidates = {path}
            if path.endswith(".docx") and "evidence_sources" in generated:
                candidates.add(path[:-5] + ".md")
            matches = [
                segment.id for segment in self.segments
                if path and candidates.intersection(declarations[segment.id])
            ]
            owner = matches[0] if len(matches) == 1 else self.current.id
            grouped.setdefault(owner, []).append(generated)
        return grouped

    def snapshots(self) -> list[dict[str, Any]]:
        return [segment.snapshot for segment in self.segments if segment.snapshot is not None]

    def all_persisted(self) -> bool:
        return bool(self.snapshots()) and all(
            segment.persisted for segment in self.segments if segment.snapshot is not None
        )
