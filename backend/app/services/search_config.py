"""联网检索的运行时配置：界面改了就生效，不用重启进程。

照 voice_config 的那套做法：
  读取 = 库里有没有覆盖值？有就用，没有回落 settings（.env）
  写入 = 落库 + 立刻回写 settings 单例
这样 web_search 继续读 settings.* 就能拿到最新值，不用改调用点。

为什么检索也要进界面：
- 换搜索服务商不该改代码、重启进程；
- 用量和配额是管理员要看的东西，密钥不该只在 .env 里躺着。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import system_config as config_repo

logger = logging.getLogger(__name__)

# 界面可改的键 -> settings 属性名
EDITABLE = {
    "anysearch_api_key": "anysearch_api_key",
    "anysearch_api_url": "anysearch_api_url",
    "search_api_key": "search_api_key",
    "search_api_model": "search_api_model",
    "search_enabled": "search_enabled",
}

BOOL_KEYS = {"search_enabled"}
# 密钥类：接口只回「是否配置」，永不回传明文
SECRET_KEYS = {"anysearch_api_key", "search_api_key"}


def _coerce(key: str, raw: Any) -> Any:
    if key in BOOL_KEYS:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return str(raw)


def apply_overrides(db: Session) -> None:
    """启动时与每次写入后调用：把库里的覆盖值同步进 settings 单例。"""
    stored = config_repo.get_all(db)
    applied = 0
    for key, attr in EDITABLE.items():
        raw = stored.get("search_" + key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            setattr(settings, attr, _coerce(key, raw))
            applied += 1
        except Exception:  # noqa: BLE001 - 单条坏配置不该拖垮启动
            logger.warning("search config apply failed: %s", key)
    if applied:
        logger.info("search config overrides applied: %d", applied)


def current_view() -> dict[str, Any]:
    """给界面看的当前生效值；密钥只报是否已配置。"""
    view: dict[str, Any] = {}
    for key, attr in EDITABLE.items():
        value = getattr(settings, attr, "")
        if key in SECRET_KEYS:
            text = str(value or "")
            view[key] = {
                "configured": bool(text.strip()),
                # 只露前 6 位，够管理员认出是哪把钥匙，又不至于泄密
                "hint": (text[:6] + "…") if len(text) > 8 else "",
            }
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
            text = "" if value is None else str(value).strip()
            # 空串表示「不改」；显式清除用 __clear__
            if text == "":
                continue
            if text == "__clear__":
                text = ""
            setattr(settings, attr, text)
            config_repo.upsert(db, "search_" + key, text, "联网检索密钥")
            continue

        if key in BOOL_KEYS:
            stored = "1" if bool(value) else "0"
        else:
            stored = str(value).strip()

        setattr(settings, attr, _coerce(key, stored))
        config_repo.upsert(db, "search_" + key, stored, "联网检索配置")

    return current_view()