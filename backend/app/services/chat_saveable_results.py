"""聊天交付成果的独立来源授权、轻量列表与原件读取。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from fastapi import HTTPException
from minio.error import InvalidResponseError, S3Error, ServerError
from sqlalchemy import select
from sqlalchemy.orm import Session, defer
from urllib3.exceptions import HTTPError

from app.api.files import (
    _generated_file_candidates,
    _resolve_local_generated_file,
    read_generated_file_bytes,
)
from app.core.config import settings
from app.models.access import KnowledgeSpace, SpaceMember
from app.models.artifacts import Artifact
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.chat_deck_render import ChatDeckRender
from app.models.documents import Document, DocumentVersion
from app.models.harness import HarnessTask
from app.repositories import usage
from app.services import minio_storage
from app.services.authorization import PermissionService
from app.services.document_processor import (
    normalize_source_relative_path,
    validate_file,
)
from app.services.harness.file_storage import (
    base_generated_file_task_id,
    resolve_workspace,
)

logger = logging.getLogger(__name__)

_MARKDOWN_FILE = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))")
_UNAVAILABLE = "成果原件不存在或当前不可用，请重新生成"


@dataclass(frozen=True)
class SaveableResult:
    id: str
    message_id: str
    name: str
    href: str
    created_at: str
    task_id: str
    file_path: str
    checksum: str | None
    version: str
    reason: str | None

    def source_prefix(self, user_id: str) -> str:
        return f"chat-results/{user_id}/{self.id}/"

    def source_path(self, user_id: str, raw: bytes) -> str:
        # 原始字节快照独立于聊天；活动目录的后来同名文件也不会覆盖旧资料。
        return f"{self.source_prefix(user_id)}{hashlib.sha256(raw).hexdigest()}/{self.name}"


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _references(message: ChatMessage) -> list[tuple[str, str]]:
    hrefs = [
        part.get("href") for part in _json_list(message.parts_json)
        if isinstance(part, dict) and part.get("type") == "file"
    ]
    hrefs.extend(match.group(1) or match.group(2) for match in _MARKDOWN_FILE.finditer(message.content or ""))
    refs: dict[tuple[str, str], None] = {}
    for href in hrefs:
        if not isinstance(href, str):
            continue
        try:
            parsed = urlsplit(href)
        except ValueError:
            continue
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/api/files/"):
            continue
        path = unquote(parsed.path[len("/api/files/"):])
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            continue
        task_id, separator, file_path = path.partition("/")
        if not separator or not task_id or task_id in {".", ".."} or "\\" in task_id:
            continue
        if _generated_file_candidates(file_path) is None:
            continue
        # 套版标准内容与内部运行目录不是交付给用户的文件。
        if file_path.lower().endswith(".deck.json") or any(part.startswith(".") for part in file_path.split("/")):
            continue
        refs[(task_id, file_path)] = None
    return list(refs)


def owned_session(db: Session, session_id: str, user: User, *, lock: bool = False) -> ChatSession:
    query = select(ChatSession).where(ChatSession.id == session_id, ChatSession.owner_user_id == user.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    session = db.scalar(query)
    if session is None:
        raise HTTPException(status_code=404, detail="聊天会话不存在")
    return session


def _source_documents_accessible(db: Session, artifact: Artifact, user: User, *, lock: bool) -> bool:
    for version_id in usage.artifact_source_version_ids(artifact):
        query = select(Document).join(DocumentVersion).where(DocumentVersion.id == version_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        document = db.scalar(query)
        if document is None or document.status != "active":
            return False
        if document.is_temporary:
            if document.created_by_user_id != user.id:
                return False
            continue
        space_query = select(KnowledgeSpace).where(KnowledgeSpace.id == document.space_id)
        if lock:
            space_query = space_query.with_for_update().execution_options(populate_existing=True)
        space = db.scalar(space_query)
        if space is None or space.status != "active":
            return False
        if lock:
            db.scalars(select(SpaceMember).where(
                SpaceMember.space_id == space.id, SpaceMember.user_id == user.id,
            ).with_for_update().execution_options(populate_existing=True)).all()
        if not PermissionService.has_space_member_role(db, user, space.id):
            return False
    return True


def _generated_owner(db: Session, task_id: str, user: User, *, lock: bool) -> bool:
    base = base_generated_file_task_id(task_id)
    # 不使用下载接口的管理员旁路，也不把消息里的链接当作拥有权证据。
    # 最早的个人工作区没有 session 后缀，仍以精确用户命名空间授权。
    if base == f"pi-chat-{user.id}":
        return True
    if base.startswith("pi-chat-"):
        prefix = f"pi-chat-{user.id}-"
        session_id = base[len(prefix):] if base.startswith(prefix) else base[len("pi-chat-"):]
        query = select(ChatSession.id).where(ChatSession.id == session_id, ChatSession.owner_user_id == user.id)
        if lock:
            query = query.with_for_update()
        return db.scalar(query) is not None
    query = select(HarnessTask.id).where(HarnessTask.id == base, HarnessTask.user_id == str(user.id))
    if lock:
        query = query.with_for_update()
    return db.scalar(query) is not None


def _generated_metadata(task_id: str, file_path: str) -> tuple[str, int, str] | None:
    # 只做对象 HEAD/stat；全会话列表不能逐个下载巨量原件。
    if minio_storage.is_minio_enabled():
        for relative in _generated_file_candidates(file_path) or ():
            try:
                metadata = minio_storage.stat_object(object_key=f"harness/{task_id}/{relative}")
                version = str(getattr(metadata, "version_id", "") or getattr(metadata, "etag", "") or getattr(metadata, "last_modified", ""))
                if metadata.size is None:
                    continue
                return relative, int(metadata.size), f"object:{version}:{metadata.size}"
            except (S3Error, InvalidResponseError, ServerError, HTTPError, OSError, RuntimeError) as exc:
                logger.debug("成果对象元数据不可用，检查本地副本：%s", type(exc).__name__)
    local = _resolve_local_generated_file(task_id, file_path)
    if local is None:
        return None
    path, _ = local
    try:
        stat = path.stat()
    except OSError:
        return None
    workspace = resolve_workspace(task_id)
    relative = path.relative_to(workspace.resolve()).as_posix() if workspace is not None else file_path
    return relative, stat.st_size, f"local:{stat.st_mtime_ns}:{stat.st_size}"


def _resolve(db: Session, message: ChatMessage, user: User, task_id: str, file_path: str, *, lock: bool = False) -> SaveableResult | None:
    name = PurePosixPath(file_path).name
    checksum = None
    reason = None
    size = 0
    version = "unavailable"
    source_id = file_path.partition("/")[0]
    if task_id == "artifacts":
        query = select(Artifact, Artifact.content.is_not(None)).options(defer(Artifact.content)).where(
            Artifact.id == source_id, Artifact.owner_user_id == user.id, Artifact.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = db.execute(query).first()
        if row is None:
            return None
        artifact = row[0]
        if not _source_documents_accessible(db, artifact, user, lock=lock):
            return None
        name = artifact.name
        checksum = artifact.checksum_sha256
        size = int(artifact.content_size or 0)
        version = checksum or "unavailable"
        file_path = f"{artifact.id}/{name}"
        if artifact.status != "ready" or not checksum or not artifact.content_type or size <= 0 or not row[1]:
            reason = _UNAVAILABLE
    elif task_id == "chat-deck-renders":
        query = select(ChatDeckRender, ChatDeckRender.output_content.is_not(None)).options(defer(ChatDeckRender.output_content)).where(
            ChatDeckRender.id == source_id, ChatDeckRender.owner_user_id == user.id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = db.execute(query).first()
        if row is None:
            return None
        render = row[0]
        origin = db.scalar(select(ChatMessage.id).join(ChatSession, ChatSession.id == ChatMessage.session_id).where(
            ChatMessage.id == render.message_id, ChatMessage.session_id == render.session_id,
            ChatMessage.owner_user_id == user.id, ChatSession.owner_user_id == user.id,
        ))
        if origin is None:
            return None
        if lock:
            owned_session(db, render.session_id, user, lock=True)
            db.scalar(select(ChatMessage.id).where(ChatMessage.id == render.message_id).with_for_update())
        name = render.output_name or name
        checksum = render.output_sha256
        size = int(render.output_size or 0)
        version = checksum or "unavailable"
        file_path = f"{render.id}/{name}"
        if render.status != "ready" or not checksum or size <= 0 or not row[1]:
            reason = _UNAVAILABLE
    else:
        if not _generated_owner(db, task_id, user, lock=lock):
            return None
        metadata = _generated_metadata(task_id, file_path)
        if metadata is None:
            reason = _UNAVAILABLE
        else:
            file_path, size, version = metadata
            name = PurePosixPath(file_path).name
    if name.lower().endswith(".deck.json") or name.startswith("."):
        return None
    try:
        normalize_source_relative_path(name, name)
    except ValueError:
        reason = "成果文件名无效，无法存入资料"
    if "%" in name:
        reason = "成果文件名包含 %，无法存入资料"
    reason = reason or validate_file(name, size)
    href = f"/api/files/{quote(task_id, safe='')}/{quote(file_path, safe='/')}"
    # 不可变交付快照的身份不因 MinIO 暂时回退到本地而变化；
    # 历史活动目录则绑定 stat 版本，避免把后来同名文件当作旧成果。
    identity_version = "snapshot" if base_generated_file_task_id(task_id) != task_id else version
    identity = hashlib.sha256(f"{href}\n{identity_version}".encode()).hexdigest()
    return SaveableResult(identity, message.id, name, href, message.created_at.isoformat(), task_id, file_path, checksum, version, reason)


def message_results(db: Session, message: ChatMessage, user: User, *, lock: bool = False) -> list[SaveableResult]:
    results = {}
    for task_id, file_path in _references(message):
        result = _resolve(db, message, user, task_id, file_path, lock=lock)
        if result is not None:
            results.setdefault(result.id, result)
    return list(results.values())


def require_result(db: Session, message: ChatMessage, user: User, result_id: str, *, lock: bool = False) -> SaveableResult:
    for result in message_results(db, message, user, lock=lock):
        if result.id == result_id:
            if result.reason:
                raise HTTPException(status_code=422, detail=result.reason)
            return result
    raise HTTPException(status_code=404, detail="聊天成果不存在或无权访问")


def read_result_bytes(result: SaveableResult, user: User) -> bytes:
    # 外部读取在入库事务前完成；提交时重新锁定并核对来源版本与所有权限。
    if result.task_id in {"artifacts", "chat-deck-renders"}:
        from app.core.database import transaction

        with transaction() as db:
            source_id = result.file_path.partition("/")[0]
            if result.task_id == "artifacts":
                artifact = usage.owned_artifact(db, source_id, user.id)
                if artifact is None or artifact.status != "ready" or not usage.has_downloadable_snapshot(artifact):
                    raise HTTPException(status_code=404, detail=_UNAVAILABLE)
                raw = artifact.content
            else:
                render = db.get(ChatDeckRender, source_id)
                if render is None or render.owner_user_id != user.id or render.status != "ready":
                    raise HTTPException(status_code=404, detail=_UNAVAILABLE)
                raw = render.output_content
        if raw is None or hashlib.sha256(raw).hexdigest() != result.checksum:
            raise HTTPException(status_code=409, detail="成果已变化，请刷新后重试")
    else:
        try:
            raw, _ = read_generated_file_bytes(result.task_id, result.file_path, max_bytes=settings.max_upload_mb * 1024 * 1024)
        except ValueError:
            raise HTTPException(status_code=422, detail="成果大小超过上传限制") from None
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404, detail=_UNAVAILABLE) from None
    error = validate_file(result.name, len(raw))
    if error:
        raise HTTPException(status_code=422, detail=error)
    return raw


def saved_documents(db: Session, result: SaveableResult, user: User) -> list[dict[str, str]]:
    rows = db.execute(select(Document.id, KnowledgeSpace.id, KnowledgeSpace.name).join(
        KnowledgeSpace, KnowledgeSpace.id == Document.space_id,
    ).where(
        Document.status == "active", Document.is_temporary.is_(False), KnowledgeSpace.status == "active",
        Document.versions.any(DocumentVersion.source_relative_path.startswith(result.source_prefix(user.id), autoescape=True)),
    ).order_by(Document.created_at, Document.id)).all()
    return [
        {"document_id": document_id, "space_id": space_id, "space_name": space_name}
        for document_id, space_id, space_name in rows
        if PermissionService.has_space_member_role(db, user, space_id)
    ]


def list_results(db: Session, session_id: str, user: User, *, message_id: str | None, offset: int, limit: int) -> dict:
    owned_session(db, session_id, user)
    query = select(ChatMessage).where(
        ChatMessage.session_id == session_id, ChatMessage.owner_user_id == user.id, ChatMessage.role == "assistant",
    ).order_by(ChatMessage.created_at, ChatMessage.id)
    if message_id is not None:
        query = query.where(ChatMessage.id == message_id)
        if db.scalar(select(ChatMessage.id).where(
            ChatMessage.id == message_id, ChatMessage.session_id == session_id,
            ChatMessage.owner_user_id == user.id, ChatMessage.role == "assistant",
        )) is None:
            raise HTTPException(status_code=404, detail="聊天消息不存在")
    seen = set()
    page = []
    total = 0
    for message in db.scalars(query.execution_options(yield_per=50)):
        for result in message_results(db, message, user):
            if result.id in seen:
                continue
            seen.add(result.id)
            if offset <= total < offset + limit:
                page.append(result)
            total += 1
    return {
        "items": [{
            "id": result.id, "message_id": result.message_id, "name": result.name,
            "href": result.href, "created_at": result.created_at, "can_save": result.reason is None,
            "reason": result.reason, "saved_documents": saved_documents(db, result, user),
        } for result in page],
        "total": total, "next_offset": offset + limit if offset + limit < total else None,
    }
