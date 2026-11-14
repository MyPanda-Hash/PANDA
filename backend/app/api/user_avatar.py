"""头像：上传、删除、读取。

只做三件事：把用户上传的图片裁成 256×256 PNG 落盘、没有就删、按需读回。
不建表也不加列——头像的「有没有」由文件是否存在决定，缓存用文件 mtime 做 ETag，
所以换头像立刻生效，不需要数据库迁移。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from app.api.deps import current_user_from_bearer_or_cookie as current_user
from app.core.database import transaction
from app.models.auth import User
from app.repositories import audit

router = APIRouter(tags=["user-avatar"])

AVATAR_DIR = Path(__file__).resolve().parents[2] / "uploads" / "avatars"
MAX_BYTES = 5 * 1024 * 1024
SIZE = 256
ALLOWED = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}


def avatar_path(user_id: str) -> Path:
    return AVATAR_DIR / f"{user_id}.png"


def has_avatar(user_id: str) -> bool:
    return avatar_path(user_id).is_file()


def avatar_url(user_id: str | None) -> str | None:
    """给前端用的地址；没有头像就返回 None（前端回退到姓名首字）。"""
    if not user_id or not has_avatar(user_id):
        return None
    return f"/api/users/{user_id}/avatar"


@router.post("/auth/avatar")
def upload_avatar(
    file: Annotated[UploadFile, File(description="头像图片")],
    user: User = Depends(current_user),
) -> JSONResponse:
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="头像不能超过 5MB")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if (img.format or "").upper() not in ALLOWED:
                raise HTTPException(status_code=415, detail="只支持 PNG / JPG / WEBP / GIF / BMP")
            image = img.convert("RGBA")
    except UnidentifiedImageError:
        raise HTTPException(status_code=415, detail="这不是一张图片") from None
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(status_code=415, detail="图片已损坏，无法读取") from None

    # 正方形居中裁切后缩放，避免长方形头像被拉扁
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    square = image.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    target = avatar_path(user.id)
    square.save(target, format="PNG", optimize=True)

    with transaction() as db:
        audit.record(db, actor=user.id, event_type="user.avatar.upload",
                     target=user.id, result="success")
    return JSONResponse(status_code=200, content={"avatar_url": avatar_url(user.id)})


@router.delete("/auth/avatar")
def delete_avatar(user: User = Depends(current_user)) -> JSONResponse:
    target = avatar_path(user.id)
    if target.is_file():
        target.unlink()
    with transaction() as db:
        audit.record(db, actor=user.id, event_type="user.avatar.delete",
                     target=user.id, result="success")
    return JSONResponse(status_code=200, content={"avatar_url": None})


@router.get("/users/{user_id}/avatar")
def get_avatar(user_id: str, _: User = Depends(current_user)) -> FileResponse:
    target = avatar_path(user_id)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="该用户没有头像")
    stat = target.stat()
    return FileResponse(
        target,
        media_type="image/png",
        headers={
            # 文件变了 URL 才会变（前端带 ?v=），但这里仍让浏览器每次校验一次
            "Cache-Control": "private, no-cache",
            "ETag": f'"{user_id}-{int(stat.st_mtime)}"',
        },
    )
