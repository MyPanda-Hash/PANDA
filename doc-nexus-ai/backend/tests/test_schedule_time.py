from datetime import UTC, datetime

import pytest

from app.services.schedule_time import (
    latest_occurrence,
    next_occurrence,
    preview_occurrences,
    validate_rule,
)


def test_daily_skips_dst_gap_and_fold_uses_only_first_instant() -> None:
    gap_rule = {"kind": "daily", "time": "02:30"}
    assert next_occurrence(
        gap_rule,
        "America/New_York",
        datetime(2024, 3, 9, 8, tzinfo=UTC),
    ) == datetime(2024, 3, 11, 6, 30, tzinfo=UTC)

    fold_rule = {"kind": "daily", "time": "01:30"}
    first_fold = datetime(2024, 11, 3, 5, 30, tzinfo=UTC)
    assert (
        next_occurrence(
            fold_rule,
            "America/New_York",
            datetime(2024, 11, 2, 6, tzinfo=UTC),
        )
        == first_fold
    )
    assert next_occurrence(fold_rule, "America/New_York", first_fold) == datetime(
        2024,
        11,
        4,
        6,
        30,
        tzinfo=UTC,
    )


def test_monthly_missing_day_and_last_day_keep_distinct_semantics() -> None:
    assert next_occurrence(
        {"kind": "monthly", "time": "09:00", "month_day": 31},
        "UTC",
        datetime(2024, 1, 31, 9, tzinfo=UTC),
    ) == datetime(2024, 3, 31, 9, tzinfo=UTC)

    assert preview_occurrences(
        {"kind": "monthly", "time": "09:00", "month_day": "last"},
        "UTC",
        datetime(2024, 1, 31, 9, tzinfo=UTC),
        count=2,
    ) == [
        datetime(2024, 2, 29, 9, tzinfo=UTC),
        datetime(2024, 3, 31, 9, tzinfo=UTC),
    ]


def test_once_converts_local_midnight_across_utc_date_boundary() -> None:
    rule = {"kind": "once", "date": "2024-01-02", "time": "00:00"}
    occurrence = datetime(2024, 1, 1, 15, tzinfo=UTC)

    assert (
        next_occurrence(
            rule,
            "Asia/Tokyo",
            datetime(2024, 1, 1, 14, 59, tzinfo=UTC),
        )
        == occurrence
    )
    assert next_occurrence(rule, "Asia/Tokyo", occurrence) is None


def test_latest_occurrence_counts_inclusive_valid_instants_not_dst_gap() -> None:
    rule = {"kind": "daily", "time": "02:30"}
    start = datetime(2024, 3, 9, 7, 30, tzinfo=UTC)
    end = datetime(2024, 3, 11, 6, 30, tzinfo=UTC)

    assert latest_occurrence(rule, "America/New_York", start, end) == (end, 2)
    assert latest_occurrence(rule, "America/New_York", end, end) == (end, 1)


@pytest.mark.parametrize(
    ("rule", "timezone_name"),
    [
        ({"kind": "daily", "time": "09:00"}, "+08:00"),
        ({"kind": "weekly", "time": "09:00", "weekdays": [1, 1]}, "UTC"),
        ({"kind": "monthly", "time": "09:00", "month_day": 0}, "UTC"),
        ({"kind": "once", "date": "2024-02-30", "time": "09:00"}, "UTC"),
    ],
)
def test_validate_rule_rejects_ambiguous_or_non_calendar_inputs(
    rule: dict,
    timezone_name: str,
) -> None:
    with pytest.raises(ValueError):
        validate_rule(rule, timezone_name)
