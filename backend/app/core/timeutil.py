"""时间输出的统一口径。

SQLite 读回来的 ``datetime`` 没有时区，但写入时用的是 UTC。接口如果原样序列化，
前端 ``new Date("2026-11-12T03:36:00")`` 会按**本地时间**解析，导致显示差 8 小时
（实测：11:36 的消息显示成 03:36）。这里统一补成 UTC 时区，序列化带偏移，
前端解析即正确。
"""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime | None) -> datetime | None:
    """裸时间按 UTC 处理；已带时区的转成 UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
