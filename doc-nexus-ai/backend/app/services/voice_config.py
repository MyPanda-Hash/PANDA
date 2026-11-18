"""语音助手的运行时配置：界面改了就生效，不用重启进程。

为什么单独一层而不是改各处 settings.moss_*：
语音配置散落在 15+ 处（moss_voice 的鉴权、通话时长、写操作限额、人设，
asr 的模型与密钥……），逐处改成「先查库再回落」，漏一处就会出现
「界面显示已改、实际通话还在用旧配置」这种最难查的故障。

这里用一次性同步的方式：
  读取 = 库里有没有覆盖值？有就用，没有回落 settings（.env）
  写入 = 落库 + 立刻回写 settings 单例
因此调用方继续读 settings.moss_* 也能拿到最新值，无需改任何调用点。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import system_config as config_repo

logger = logging.getLogger(__name__)

# 界面可改的键 -> settings 属性名
# 只开放这些：模型名与密钥是日常要调的；采样率、超时这类属于运维细节，不进界面。
EDITABLE = {
    "moss_api_key": "moss_api_key",
    "moss_realtime_model": "moss_realtime_model",
    "moss_realtime_url": "moss_realtime_url",
    "moss_voice_id": "moss_voice_id",
    "moss_voice_persona": "moss_voice_persona",
    "moss_voice_max_seconds": "moss_voice_max_seconds",
    "moss_voice_enabled": "moss_voice_enabled",
    "moss_voice_write_enabled": "moss_voice_write_enabled",
    "moss_voice_write_limit": "moss_voice_write_limit",
    "moss_voice_briefing_enabled": "moss_voice_briefing_enabled",
    "dashscope_asr_model": "dashscope_asr_model",
    "dashscope_api_key": "dashscope_api_key",
}

# 布尔型需要显式转换：库里存的是 "1"/"0" 字符串
BOOL_KEYS = {
    "moss_voice_enabled",
    "moss_voice_write_enabled",
    "moss_voice_briefing_enabled",
}
INT_KEYS = {"moss_voice_max_seconds", "moss_voice_write_limit"}

# 密钥类：接口只回「是否配置」，永不回传明文
SECRET_KEYS = {"moss_api_key", "dashscope_api_key"}


def _coerce(key: str, raw: str) -> Any:
    if key in BOOL_KEYS:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if key in INT_KEYS:
        try:
            return int(str(raw).strip())
        except ValueError:
            return getattr(settings, key)
    return str(raw)


def apply_overrides(db: Session) -> None:
    """启动时与每次写入后调用：把库里的覆盖值同步进 settings 单例。"""
    stored = config_repo.get_all(db)
    applied = 0
    for key, attr in EDITABLE.items():
        raw = stored.get(f"voice_{key}")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            setattr(settings, attr, _coerce(key, raw))
            applied += 1
        except Exception:  # noqa: BLE001 - 单条坏配置不该拖垮整个启动
            logger.warning("voice config apply failed: %s", key)
    if applied:
        logger.info("voice config overrides applied: %d", applied)


def current_view() -> dict[str, Any]:
    """给界面看的当前生效值；密钥只报是否已配置。"""
    view: dict[str, Any] = {}
    for key, attr in EDITABLE.items():
        value = getattr(settings, attr, "")
        if key in SECRET_KEYS:
            view[key] = {"configured": bool(str(value or "").strip())}
        else:
            view[key] = value
    return view


def save(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    """写入覆盖值并立刻生效。返回新的当前视图。"""
    for key, value in values.items():
        if key not in EDITABLE:
            continue
        attr = EDITABLE[key]
        if key in SECRET_KEYS:
            # 空串表示「不改」；显式清除用 "__clear__"
            text = "" if value is None else str(value).strip()
            if text == "":
                continue
            if text == "__clear__":
                text = ""
            setattr(settings, attr, text)
            config_repo.upsert(db, f"voice_{key}", text, "语音助手密钥")
            continue
        if key in BOOL_KEYS:
            stored = "1" if bool(value) else "0"
            coerced: Any = bool(value)
        elif key in INT_KEYS:
            try:
                stored = str(int(value))
            except (TypeError, ValueError):
                continue
            coerced = int(stored)
        else:
            stored = str(value)
            coerced = stored
        setattr(settings, attr, coerced)
        config_repo.upsert(db, f"voice_{key}", stored, "语音助手配置")
    return current_view()