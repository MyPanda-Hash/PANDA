"""只读复制 PPT 原件；不创建模板，也不改写源文件。"""
from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryFile

from app.core.config import settings
from app.services import minio_storage

MAX_SOURCE_BYTES = 512 * 1024 * 1024

CHUNK_BYTES = 1024 * 1024


class PresentationOriginalError(ValueError):
    """原件不存在、越界或在读取期间变化。"""


def presentation_format(source_relative_path: str | None, file_path: str) -> str:
    # 展示名称可编辑；格式只能取自持久化的原件路径。
    suffix = (
        Path((source_relative_path or "").replace("\\", "/")).suffix
        or Path(file_path.replace("\\", "/")).suffix
    ).lower()
    if suffix not in {".ppt", ".pptx", ".potx"}:
        raise PresentationOriginalError("仅支持 PPT、PPTX 或 POTX 原件")
    return suffix[1:]


def _identity(info, remote: bool):
    if info is None:
        raise PresentationOriginalError("附件原件不可用")
    if remote:
        return (info.size, info.etag, getattr(info, "version_id", None))
    return (info.st_size, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def copy_original(file_path: str):
    """继承旧附件导入的有界快照语义，验证完整后才允许 HTTP 交付。"""
    remote = minio_storage.is_minio_uri(file_path)
    path = Path(file_path).absolute()
    stream = None
    try:
        if remote:
            config = minio_storage.load_minio_settings()
            bucket, key = minio_storage.parse_minio_uri(file_path)
            if config is None or bucket not in {config.bucket, config.temp_bucket} or not key:
                raise PresentationOriginalError("附件原件不可用")
            before = _identity(minio_storage.stat_object(minio_uri=file_path), True)
        else:
            root = settings.upload_dir.resolve()
            configured_root = settings.upload_dir.absolute()
            if path.is_relative_to(configured_root):
                path = root / path.relative_to(configured_root)
            if not path.is_relative_to(root):
                raise PresentationOriginalError("附件原件不可用")
            # 每一层均拒绝软链接；openat 持有父目录，避免检查后替换路径。
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parts = path.relative_to(root).parts
                if not parts:
                    raise PresentationOriginalError("附件原件不可用")
                for part in parts[:-1]:
                    if part in {".", ".."}:
                        raise PresentationOriginalError("附件原件不可用")
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                stream = os.fdopen(fd, "rb")
            finally:
                os.close(directory)
            assert stream is not None
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise PresentationOriginalError("附件原件不可用")
            before = _identity(info, False)
        size = before[0]
        if not isinstance(size, int) or not 0 < size <= MAX_SOURCE_BYTES:
            raise PresentationOriginalError("附件原件为空或超过大小上限")
        if remote:
            stream = minio_storage.open_object(minio_uri=file_path)
        if stream is None:
            raise PresentationOriginalError("附件原件不可用")
        with TemporaryFile() as copied:
            digest = hashlib.sha256()
            received = 0
            while chunk := stream.read(min(CHUNK_BYTES, size - received + 1)):
                received += len(chunk)
                if received > size:
                    raise PresentationOriginalError("附件原件大小已变化")
                digest.update(chunk)
                copied.write(chunk)
            after = _identity(
                minio_storage.stat_object(minio_uri=file_path) if remote else os.fstat(stream.fileno()), remote
            )
            if received != size or before != after:
                raise PresentationOriginalError("附件原件不完整或已变化")
            if not remote and (path.resolve() != path or _identity(path.stat(), False) != before):
                raise PresentationOriginalError("附件原件已变化")
            copied.seek(0)
            yield copied, size, digest.hexdigest()
    except PresentationOriginalError:
        raise
    except Exception as exc:
        # 不将 MinIO 凭据、URL、宿主路径或底层异常带入模型上下文。
        raise PresentationOriginalError("附件原件不可用") from exc
    finally:
        if stream is not None:
            stream.close()
            release = getattr(stream, "release_conn", None)
            if remote and callable(release):
                release()
