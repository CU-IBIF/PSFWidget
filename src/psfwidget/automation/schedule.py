"""When scheduled jobs are due (AUT-03).

Schedules are written in plain words:

* ``every 15 minutes``, ``every 2 hours``, ``every 1 day``
* ``daily 07:30``
* ``weekly monday 07:30``
* ``monthly 1 07:30`` (day 1 to 28)
* ``off``

Clock times are local time on the machine running PSFWidget. A job is due when
a scheduled time has passed since it last ran; a calendar job that has never
run waits for its first scheduled time after the schedule was first seen, so a
new installation does not fire every report at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_INTERVAL = re.compile(r"^every\s+(\d+)\s+(minute|hour|day)s?$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    """Raised when a schedule cannot be understood."""


@dataclass(frozen=True)
class Schedule:
    """A parsed schedule."""

    kind: str
    """``off``, ``interval``, ``daily``, ``weekly``, or ``monthly``."""
    interval: timedelta | None = None
    at: time | None = None
    weekday: int | None = None
    """0 = Monday."""
    day: int | None = None
    text: str = ""

    def latest_occurrence(self, now: datetime) -> datetime | None:
        """Return the most recent scheduled time at or before ``now`` (calendar schedules)."""
        if self.at is None:
            return None
        today = now.replace(hour=self.at.hour, minute=self.at.minute, second=0, microsecond=0)
        if self.kind == "daily":
            return today if today <= now else today - timedelta(days=1)
        if self.kind == "weekly":
            assert self.weekday is not None
            candidate = today - timedelta(days=(now.weekday() - self.weekday) % 7)
            return candidate if candidate <= now else candidate - timedelta(days=7)
        if self.kind == "monthly":
            assert self.day is not None
            candidate = today.replace(day=self.day)
            if candidate <= now:
                return candidate
            previous_month = candidate.replace(day=1) - timedelta(days=1)
            return previous_month.replace(day=self.day)
        return None

    def is_due(self, now: datetime, last_run: datetime | None, first_seen: datetime) -> bool:
        """Whether the job should run at ``now``.

        ``last_run`` is when it last ran (``None`` if never); ``first_seen`` is
        when the schedule was first used, which stands in for a last run for
        calendar jobs that have never run.
        """
        if self.kind == "off":
            return False
        if self.kind == "interval":
            assert self.interval is not None
            return last_run is None or now - last_run >= self.interval
        occurrence = self.latest_occurrence(now)
        reference = last_run if last_run is not None else first_seen
        return occurrence is not None and occurrence > reference


def parse_schedule(text: str) -> Schedule:
    """Parse a schedule written in plain words.

    Raises
    ------
    ScheduleError
        If the text is not a recognised schedule.
    """
    words = " ".join(str(text).strip().lower().split())
    if words in ("", "off", "never", "none"):
        return Schedule("off", text=words or "off")
    match = _INTERVAL.match(words)
    if match:
        count = int(match.group(1))
        if count < 1:
            raise ScheduleError(f"interval must be at least 1: {text!r}")
        unit = {"minute": "minutes", "hour": "hours", "day": "days"}[match.group(2)]
        return Schedule("interval", interval=timedelta(**{unit: count}), text=words)
    parts = words.split(" ")

    def clock(value: str) -> time:
        found = _TIME.match(value)
        if found is None:
            raise ScheduleError(f"expected a time like 07:30 in {text!r}, got {value!r}")
        return time(int(found.group(1)), int(found.group(2)))

    if parts[0] == "daily" and len(parts) == 2:
        return Schedule("daily", at=clock(parts[1]), text=words)
    if parts[0] == "weekly" and len(parts) == 3:
        if parts[1] not in WEEKDAYS:
            raise ScheduleError(f"unknown weekday {parts[1]!r} in {text!r}")
        return Schedule("weekly", at=clock(parts[2]), weekday=WEEKDAYS.index(parts[1]), text=words)
    if parts[0] == "monthly" and len(parts) == 3:
        if not parts[1].isdigit() or not 1 <= int(parts[1]) <= 28:
            raise ScheduleError(f"monthly schedules need a day from 1 to 28 in {text!r}")
        return Schedule("monthly", at=clock(parts[2]), day=int(parts[1]), text=words)
    raise ScheduleError(
        f"could not understand schedule {text!r}; use e.g. 'every 15 minutes', "
        "'daily 07:30', 'weekly monday 07:30', 'monthly 1 07:30', or 'off'"
    )
