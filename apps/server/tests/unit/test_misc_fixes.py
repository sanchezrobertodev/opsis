"""The caps' day and month begin at midnight in TIMEZONE, not at UTC midnight."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from studio.budget.guard import windows
from studio.config import Settings, get_settings


def test_the_day_and_month_begin_at_local_midnight(monkeypatch):
    monkeypatch.setattr(get_settings(), "timezone", ZoneInfo("America/Chicago"))
    # 01:00 UTC on the 15th is 8 PM CDT on the 14th: a charge then counts toward the 14th
    day, month = windows(datetime(2026, 10, 15, 1, 0, tzinfo=UTC))
    assert day == datetime(2026, 10, 14, 5, 0, tzinfo=UTC) <= datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
    assert month == datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    # after the clocks go back on Nov 1 the day starts an hour later in UTC, the month still at CDT midnight
    day, month = windows(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    assert (day, month) == (datetime(2026, 11, 2, 6, 0, tzinfo=UTC), datetime(2026, 11, 1, 5, 0, tzinfo=UTC))


def test_utc_by_default_and_a_wrong_zone_fails_at_load(monkeypatch):
    assert Settings.model_fields["timezone"].default == ZoneInfo("UTC")  # existing deployments keep UTC days
    monkeypatch.setattr(get_settings(), "timezone", ZoneInfo("UTC"))
    assert windows(datetime(2026, 10, 15, 1, 0, tzinfo=UTC))[0] == datetime(2026, 10, 15, tzinfo=UTC)
    monkeypatch.setenv("TIMEZONE", "Mars/Base")
    with pytest.raises(ValidationError, match="timezone"):
        Settings()
