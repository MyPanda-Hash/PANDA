from __future__ import annotations

import json
import logging
import os
import time

from app.core.logging import HumanReadableFormatter, JSONFormatter


def test_log_formatters_follow_container_timezone() -> None:
    previous_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Asia/Shanghai"
        time.tzset()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.created = 0
        record.msecs = 0

        payload = json.loads(JSONFormatter().format(record))
        assert payload["ts"] == "1970-01-01T08:00:00.000+08:00"
        assert "1970-01-01T08:00:00.000" in HumanReadableFormatter().format(record)
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()
