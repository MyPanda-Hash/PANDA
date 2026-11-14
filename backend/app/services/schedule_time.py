from __future__ import annotations

import calendar
import heapq
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_RULE_KINDS = frozenset({"once", "daily", "weekly", "monthly"})


def _timezone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("timezone must be an IANA timezone name")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be an IANA timezone name") from exc


def _rule_time(rule: dict) -> time:
    value = rule.get("time")
    if not isinstance(value, str) or _TIME_RE.fullmatch(value) is None:
        raise ValueError("rule.time must use HH:MM in 24-hour time")
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour=hour, minute=minute)


def _once_date(rule: dict) -> date:
    value = rule.get("date")
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ValueError("once rule.date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("once rule.date must be a valid calendar date") from exc


def _weekly_days(rule: dict) -> tuple[int, ...]:
    values = rule.get("weekdays")
    if not isinstance(values, list) or not values:
        raise ValueError("weekly rule.weekdays must be a non-empty list")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 7
        for value in values
    ):
        raise ValueError("weekly weekdays must be ISO weekday numbers 1 through 7")
    if len(values) != len(set(values)):
        raise ValueError("weekly weekdays must not contain duplicates")
    return tuple(sorted(values))


def _month_day(rule: dict) -> int | str:
    value = rule.get("month_day")
    if value == "last":
        return value
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
        raise ValueError("monthly rule.month_day must be 1 through 31 or 'last'")
    return value


def validate_rule(rule: dict, timezone_name: str) -> None:
    """校验首期支持的本地日历规则与 IANA 时区。"""
    if not isinstance(rule, dict):
        raise ValueError("rule must be an object")
    kind = rule.get("kind")
    if not isinstance(kind, str) or kind not in _RULE_KINDS:
        raise ValueError("rule.kind must be once, daily, weekly, or monthly")

    _timezone(timezone_name)
    _rule_time(rule)
    if kind == "once":
        _once_date(rule)
    elif kind == "weekly":
        _weekly_days(rule)
    elif kind == "monthly":
        _month_day(rule)


def _aware_utc(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _local_candidate(day: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    """不存在的墙钟时间返回空；重复墙钟时间只采用 fold=0 的首次。"""
    naive = datetime.combine(day, local_time)
    local = naive.replace(tzinfo=zone, fold=0)
    utc_value = local.astimezone(UTC)
    round_trip = utc_value.astimezone(zone)
    if round_trip.replace(tzinfo=None) != naive or round_trip.fold != 0:
        return None
    return utc_value


def _month_candidate(year: int, month: int, month_day: int | str) -> date | None:
    final_day = calendar.monthrange(year, month)[1]
    if month_day == "last":
        return date(year, month, final_day)
    if not isinstance(month_day, int):
        raise ValueError("monthly rule.month_day must be 1 through 31 or 'last'")
    if month_day > final_day:
        return None
    return date(year, month, month_day)


def _next_month(year: int, month: int) -> tuple[int, int] | None:
    if month == 12:
        if year == date.max.year:
            return None
        return year + 1, 1
    return year, month + 1


def _iter_daily(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        if current == date.max:
            return
        current += timedelta(days=1)


def _iter_weekly(start: date, end: date, weekdays: tuple[int, ...]) -> Iterator[date]:
    streams: list[tuple[date, int]] = []
    for weekday in weekdays:
        days_until = (weekday - start.isoweekday()) % 7
        if days_until > (date.max - start).days:
            continue
        first = start + timedelta(days=days_until)
        if first <= end:
            heapq.heappush(streams, (first, weekday))

    while streams:
        current, weekday = heapq.heappop(streams)
        yield current
        if current > date.max - timedelta(days=7):
            continue
        following = current + timedelta(days=7)
        if following <= end:
            heapq.heappush(streams, (following, weekday))


def _iter_monthly(start: date, end: date, month_day: int | str) -> Iterator[date]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        candidate = _month_candidate(year, month, month_day)
        if candidate is not None and start <= candidate <= end:
            yield candidate
        following = _next_month(year, month)
        if following is None:
            return
        year, month = following


def _candidate_dates(rule: dict, start: date, end: date) -> Iterator[date]:
    kind = rule["kind"]
    if kind == "once":
        candidate = _once_date(rule)
        if start <= candidate <= end:
            yield candidate
        return
    if kind == "daily":
        yield from _iter_daily(start, end)
        return
    if kind == "weekly":
        yield from _iter_weekly(start, end, _weekly_days(rule))
        return
    yield from _iter_monthly(start, end, _month_day(rule))


def next_occurrence(rule: dict, timezone_name: str, after: datetime) -> datetime | None:
    """返回严格晚于 ``after`` 的下一次 UTC 触发时间。"""
    validate_rule(rule, timezone_name)
    after_utc = _aware_utc(after, "after")
    zone = _timezone(timezone_name)
    local_start = after_utc.astimezone(zone).date()
    local_time = _rule_time(rule)

    for candidate_day in _candidate_dates(rule, local_start, date.max):
        candidate = _local_candidate(candidate_day, local_time, zone)
        if candidate is not None and candidate > after_utc:
            return candidate
    return None


def preview_occurrences(
    rule: dict,
    timezone_name: str,
    after: datetime,
    count: int = 3,
) -> list[datetime]:
    """预览严格晚于 ``after`` 的至多 ``count`` 次 UTC 触发时间。"""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a non-negative integer")
    validate_rule(rule, timezone_name)
    cursor = _aware_utc(after, "after")
    occurrences: list[datetime] = []
    for _ in range(count):
        candidate = next_occurrence(rule, timezone_name, cursor)
        if candidate is None:
            break
        occurrences.append(candidate)
        cursor = candidate
    return occurrences


def latest_occurrence(
    rule: dict,
    timezone_name: str,
    start: datetime,
    end: datetime,
) -> tuple[datetime | None, int]:
    """返回闭区间内最近一次有效 occurrence 及区间内有效 occurrence 总数。"""
    validate_rule(rule, timezone_name)
    start_utc = _aware_utc(start, "start")
    end_utc = _aware_utc(end, "end")
    if end_utc < start_utc:
        return None, 0

    zone = _timezone(timezone_name)
    local_time = _rule_time(rule)
    local_start = start_utc.astimezone(zone).date()
    local_end = end_utc.astimezone(zone).date()
    latest: datetime | None = None
    count = 0

    # 只遍历规则可能命中的日历日（周规则按 7 天、月规则按月跳跃），不按分钟扫描。
    for candidate_day in _candidate_dates(rule, local_start, local_end):
        candidate = _local_candidate(candidate_day, local_time, zone)
        if candidate is None or candidate < start_utc or candidate > end_utc:
            continue
        latest = candidate
        count += 1
    return latest, count
