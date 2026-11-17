"""Tests for cron-expression schedules (AUT-03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from psfwidget.automation.config import ConfigError, parse_config
from psfwidget.automation.schedule import ScheduleError, parse_cron, parse_schedule


@pytest.mark.req("AUT-03")
@pytest.mark.parametrize(
    ("expression", "now", "expected"),
    [
        ("30 7 * * *", datetime(2026, 3, 4, 8, 0), datetime(2026, 3, 4, 7, 30)),
        ("30 7 * * *", datetime(2026, 3, 4, 7, 29), datetime(2026, 3, 3, 7, 30)),
        ("30 7 * * *", datetime(2026, 3, 4, 7, 30), datetime(2026, 3, 4, 7, 30)),
        ("*/15 * * * *", datetime(2026, 3, 4, 8, 44), datetime(2026, 3, 4, 8, 30)),
        ("0 8-18/2 * * *", datetime(2026, 3, 4, 13, 5), datetime(2026, 3, 4, 12, 0)),
        ("0 9 * * 1-5", datetime(2026, 3, 8, 12, 0), datetime(2026, 3, 6, 9, 0)),  # Sun → Fri
        ("0 9 * * sat,sun", datetime(2026, 3, 4, 12, 0), datetime(2026, 3, 1, 9, 0)),
        ("0 9 * * 7", datetime(2026, 3, 4, 12, 0), datetime(2026, 3, 1, 9, 0)),  # 7 = Sunday
        ("15 6 1,15 * *", datetime(2026, 3, 14, 0, 0), datetime(2026, 3, 1, 6, 15)),
        ("0 0 1 jan *", datetime(2026, 6, 1), datetime(2026, 1, 1, 0, 0)),
        ("0 0 31 * *", datetime(2026, 3, 30), datetime(2026, 1, 31, 0, 0)),  # Feb has none
        # Both day fields restricted: either matches (cron semantics).
        ("0 12 13 * 5", datetime(2026, 3, 13, 13, 0), datetime(2026, 3, 13, 12, 0)),
        ("0 12 13 * 5", datetime(2026, 3, 11, 13, 0), datetime(2026, 3, 6, 12, 0)),
    ],
)
def test_latest_occurrence(expression: str, now: datetime, expected: datetime) -> None:
    assert parse_cron(expression).latest(now) == expected


@pytest.mark.req("AUT-03")
def test_cron_schedule_due_logic_and_time_zones() -> None:
    schedule = parse_schedule("cron 30 7 * * mon")
    assert schedule.kind == "cron"
    first_seen = datetime(2026, 3, 2, 6, 0)  # Monday before 07:30
    assert not schedule.is_due(datetime(2026, 3, 2, 7, 0), None, first_seen)
    assert schedule.is_due(datetime(2026, 3, 2, 7, 31), None, first_seen)
    ran = datetime(2026, 3, 2, 7, 31)
    assert not schedule.is_due(datetime(2026, 3, 5, 9, 0), ran, first_seen)
    assert schedule.is_due(datetime(2026, 3, 9, 7, 30), ran, first_seen)
    # Aware times (as the runner uses) keep their zone; a later DST change does
    # not make a job run twice, because "due" compares against the last run.
    cet = timezone(timedelta(hours=1))
    aware = datetime(2026, 3, 30, 7, 31, tzinfo=cet)
    assert schedule.latest_occurrence(aware) == datetime(2026, 3, 30, 7, 30, tzinfo=cet)
    utc_run = datetime(2026, 3, 30, 6, 31, tzinfo=UTC)
    assert not schedule.is_due(aware + timedelta(minutes=5), utc_run.astimezone(cet), first_seen)


@pytest.mark.req("AUT-03", "AUT-05")
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("cron 30 7 * *", "5 fields"),
        ("cron 61 7 * * *", "minute: 61 is outside"),
        ("cron 0 7 * * funday", "not a number"),
        ("cron 0 7 5-1 * *", "runs backwards"),
        ("cron */0 7 * * *", "invalid step"),
    ],
)
def test_bad_cron_expressions(text: str, message: str) -> None:
    with pytest.raises(ScheduleError, match=message):
        parse_schedule(text)


@pytest.mark.req("AUT-03", "AUT-05")
def test_cron_in_the_config_file(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    config = parse_config(
        {
            "schedule": {"trend_report": "cron 45 6 * * 1"},
            "backup": {"folder": "b", "schedule": "cron 0 2 * * *"},
        },
        folder / "a.toml",
    )
    assert config.trend_report.kind == "cron"
    assert config.backup.kind == "cron"
    with pytest.raises(ConfigError, match="schedule"):
        parse_config({"schedule": {"ingest": "cron 0 99 * * *"}}, folder / "a.toml")
