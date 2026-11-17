"""When scheduled jobs are due (AUT-03).

Schedules are written in plain words:

* ``every 15 minutes``, ``every 2 hours``, ``every 1 day``
* ``daily 07:30``
* ``weekly monday 07:30``
* ``monthly 1 07:30`` (day 1 to 28)
* ``cron 30 7 * * 1-5``: a standard five-field cron expression (minute, hour,
  day of month, month, day of week), with ``*``, lists (``1,15``), ranges
  (``1-5``), steps (``*/15``, ``8-18/2``), and names (``mon``, ``jan``).
  Day of week 0 and 7 are Sunday. As in cron, when both the day of month and
  the day of week are restricted, either may match.
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
    """``off``, ``interval``, ``daily``, ``weekly``, ``monthly``, or ``cron``."""
    interval: timedelta | None = None
    at: time | None = None
    weekday: int | None = None
    """0 = Monday."""
    day: int | None = None
    text: str = ""
    cron: CronSpec | None = None

    def latest_occurrence(self, now: datetime) -> datetime | None:
        """Return the most recent scheduled time at or before ``now`` (calendar schedules)."""
        if self.cron is not None:
            return self.cron.latest(now)
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

    if parts[0] == "cron":
        return Schedule("cron", cron=parse_cron(" ".join(parts[1:]), text), text=words)
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
        "'daily 07:30', 'weekly monday 07:30', 'monthly 1 07:30', 'cron 30 7 * * 1-5', "
        "or 'off'"
    )


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_FIELDS = (  # name, lowest, highest, names
    ("minute", 0, 59, ()),
    ("hour", 0, 23, ()),
    ("day of month", 1, 31, ()),
    ("month", 1, 12, _MONTHS),
    ("day of week", 0, 7, _DAYS),
)
MAX_DAYS_BACK = 4 * 366


@dataclass(frozen=True)
class CronSpec:
    """A parsed five-field cron expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    """0 = Sunday."""
    any_day: bool
    any_weekday: bool

    def matches_day(self, moment: datetime) -> bool:
        """Whether the cron day-of-month / day-of-week / month fields allow this date."""
        if moment.month not in self.months:
            return False
        weekday = (moment.weekday() + 1) % 7  # Monday 0 → 1, Sunday 6 → 0
        day_ok, weekday_ok = moment.day in self.days, weekday in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return weekday_ok
        if self.any_weekday:
            return day_ok
        return day_ok or weekday_ok

    def latest(self, now: datetime) -> datetime | None:
        """Return the most recent matching minute at or before ``now``."""
        hours = sorted(self.hours, reverse=True)
        minutes = sorted(self.minutes, reverse=True)
        start = now.replace(second=0, microsecond=0)
        for back in range(MAX_DAYS_BACK):
            day = start - timedelta(days=back)
            if not self.matches_day(day):
                continue
            for hour in hours:
                if back == 0 and hour > start.hour:
                    continue
                for minute in minutes:
                    if back == 0 and hour == start.hour and minute > start.minute:
                        continue
                    return day.replace(hour=hour, minute=minute)
        return None


def _field(
    text: str, name: str, low: int, high: int, names: tuple[str, ...], whole: str
) -> frozenset[int]:
    def number(value: str) -> int:
        value = value.lower()
        if value in names:
            return names.index(value) + (1 if name == "month" else 0)
        if not value.isdigit():
            raise ScheduleError(f"{name}: {value!r} is not a number in {whole!r}")
        result = int(value)
        if not low <= result <= high:
            raise ScheduleError(f"{name}: {result} is outside {low}-{high} in {whole!r}")
        return result

    found: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleError(f"{name}: invalid step {step_text!r} in {whole!r}")
            step = int(step_text)
        if base == "*":
            first, last = low, high
        elif "-" in base:
            first_text, last_text = base.split("-", 1)
            first, last = number(first_text), number(last_text)
            if first > last:
                raise ScheduleError(f"{name}: range {base!r} runs backwards in {whole!r}")
        else:
            first = number(base)
            last = high if step_text else first
        found.update(range(first, last + 1, step))
    if name == "day of week" and 7 in found:
        found.discard(7)
        found.add(0)
    return frozenset(found)


def parse_cron(expression: str, whole: str = "") -> CronSpec:
    """Parse a five-field cron expression.

    Raises
    ------
    ScheduleError
        If the expression is malformed or a value is out of range.
    """
    fields = expression.split()
    whole = whole or expression
    if len(fields) != 5:
        raise ScheduleError(
            f"a cron schedule needs 5 fields (minute hour day month weekday), got {whole!r}"
        )
    values = [
        _field(text, name, low, high, names, whole)
        for text, (name, low, high, names) in zip(fields, _FIELDS, strict=True)
    ]
    minutes, hours, days, months, weekdays = values
    return CronSpec(
        minutes,
        hours,
        days,
        months,
        weekdays,
        any_day=fields[2] == "*",
        any_weekday=fields[4] == "*",
    )
