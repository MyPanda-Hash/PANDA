"""资料库：上传、列表、下载、删除。

边界：
- 文件本体放 data/library/，库里只存相对文件名；
- 上传/删除要登录；列表与下载所有登录用户可见（内部资料共享）；
- 单个文件上限 20 MB，够放 PPTX/DOCX 模板，避免误传大文件把磁盘塞满。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.models.library import CATEGORIES, LibraryFile

router = APIRouter(prefix="/library", tags=["library"])

MAX_BYTES = 20 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2] / "data" / "library"


def _root() -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT


def _row_payload(row: LibraryFile) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "categoryLabel": CATEGORIES.get(row.category, "其他"),
        "mime": row.mime,
        "size": row.size,
        "note": row.note,
        "ownerName": row.owner_name,
        "createdAt": (row.created_at.isoformat() + "Z") if row.created_at else "",
    }


@router.get("/categories")
def list_categories() -> dict:
    return {"data": [{"key": k, "label": v} for k, v in CATEGORIES.items()]}


@router.get("")
def list_files(
    _: User = Depends(current_user_from_bearer_or_cookie),
    category: str = Query(default="", max_length=32),
    keyword: str = Query(default="", max_length=128),
) -> dict:
    with transaction() as db:
        stmt = select(LibraryFile).order_by(LibraryFile.created_at.desc())
        if category.strip():
            stmt = stmt.where(LibraryFile.category == category.strip())
        if keyword.strip():
            stmt = stmt.where(LibraryFile.name.contains(keyword.strip()))
        rows = db.scalars(stmt.limit(200)).all()
        return {"data": [_row_payload(row) for row in rows]}


@router.post("")
async def upload_file(
    user: User = Depends(current_user_from_bearer_or_cookie),
    file: UploadFile = File(...),
    category: str = Form(default="other"),
    note: str = Form(default=""),
) -> dict:
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="未知分类：" + category)

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=422, detail="文件是空的")
    if len(blob) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 20 MB")

    file_id = "lib_" + uuid.uuid4().hex[:16]
    # 存盘名带 id 前缀：同名文件不会互相覆盖，也便于按记录反查
    suffix = Path(file.filename or "file").suffix[:16]
    stored = file_id + suffix
    (_root() / stored).write_bytes(blob)

    with transaction() as db:
        row = LibraryFile(
            id=file_id,
            name=(file.filename or "未命名")[:255],
            category=category,
            mime=file.content_type or "application/octet-stream",
            size=len(blob),
            stored_name=stored,
            note=note[:2000],
            owner_user_id=user.id,
            owner_name=str(getattr(user, "display_name", "") or getattr(user, "username", "") or ""),
        )
        db.add(row)
        db.flush()
        return {"data": _row_payload(row)}


@router.get("/{file_id}/download")
def download_file(
    file_id: str,
    _: User = Depends(current_user_from_bearer_or_cookie),
) -> Response:
    with transaction() as db:
        row = db.get(LibraryFile, file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        name, stored, mime = row.name, row.stored_name, row.mime

    path = _root() / stored
    if not path.exists():
        raise HTTPException(status_code=410, detail="文件已不在磁盘上")

    return Response(
        content=path.read_bytes(),
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{file_id}")
def delete_file(
    file_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    with transaction() as db:
        row = db.get(LibraryFile, file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        # 只有上传者和管理员能删
        is_admin = bool(getattr(user, "is_admin", False))
        if row.owner_user_id != user.id and not is_admin:
            raise HTTPException(status_code=403, detail="只能删除自己上传的文件")
        stored = row.stored_name
        db.delete(row)

    target = _root() / stored
    if target.exists():
        target.unlink()
    return {"data": {"id": file_id}}