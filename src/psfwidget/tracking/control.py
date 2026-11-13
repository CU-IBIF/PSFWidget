"""Control limits, Westgard evaluation, and drift for one tracked metric (TRK-03 to TRK-09).

A configuration's history is cut into *segments* at every rebaselining event
(TRK-06). Each segment has its own baseline (TRK-04):

* ``first_n`` (the default): mean and SD of the first N included sessions of
  the segment. With fewer than N the limits are *provisional*; with fewer
  than five there are none.
* ``range``: mean and SD of the included sessions acquired in a date range.
* ``target``: a mean and SD entered by the user, per metric. Metrics without
  a target fall back to ``first_n``.

Westgard rules (TRK-05) are evaluated within each segment.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from psfwidget.profiles.spec import SpecLimits
from psfwidget.tracking.settings import (
    ACCEPTED_BEADS,
    DEFAULT_BASELINE_SESSIONS,
    MIN_BASELINE_SESSIONS,
    TrackingSettings,
)
from psfwidget.tracking.store import Configuration, Event, ResultsDatabase, SessionPoint
from psfwidget.tracking.westgard import Rule, evaluate, severity

DAYS_PER_MONTH = 30.4375
TREND_SESSIONS = 10
"""Sessions used for the drift estimate (TRK-09), all from the latest baseline segment."""


class BaselineError(ValueError):
    """Raised when a baseline specification is invalid."""


@dataclass(frozen=True)
class BaselineMethod:
    """How a segment's control limits are set."""

    kind: str
    """``first_n``, ``range``, or ``target``."""
    sessions: int = DEFAULT_BASELINE_SESSIONS
    start: datetime | None = None
    end: datetime | None = None
    targets: Mapping[str, tuple[float, float]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready description, as stored with an event."""
        data: dict[str, Any] = {"method": self.kind, "sessions": self.sessions}
        if self.start is not None:
            data["start"] = self.start.isoformat()
        if self.end is not None:
            data["end"] = self.end.isoformat()
        if self.targets is not None:
            data["targets"] = {k: list(v) for k, v in self.targets.items()}
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BaselineMethod:
        """Rebuild a method from :meth:`to_dict` output.

        Raises
        ------
        BaselineError
            If the description is invalid.
        """
        kind = data.get("method")
        sessions = int(data.get("sessions", DEFAULT_BASELINE_SESSIONS))
        if sessions < MIN_BASELINE_SESSIONS:
            raise BaselineError(f"a baseline needs at least {MIN_BASELINE_SESSIONS} sessions")
        if kind == "first_n":
            return cls("first_n", sessions)
        if kind == "range":
            try:
                start = datetime.fromisoformat(str(data["start"]))
                end = datetime.fromisoformat(str(data["end"]))
            except (KeyError, ValueError) as exc:
                raise BaselineError(
                    f"a range baseline needs start and end dates ({exc})"
                ) from None
            if start.tzinfo is None or end.tzinfo is None:
                raise BaselineError("baseline dates need a time zone")
            if end <= start:
                raise BaselineError("the baseline range must end after it starts")
            return cls("range", sessions, start=start, end=end)
        if kind == "target":
            raw = data.get("targets")
            if not isinstance(raw, Mapping) or not raw:
                raise BaselineError("a target baseline needs targets: {metric: [mean, sd]}")
            targets: dict[str, tuple[float, float]] = {}
            for metric, pair in raw.items():
                try:
                    mean, sd = (float(v) for v in pair)
                except (TypeError, ValueError):
                    raise BaselineError(f"target for {metric} must be [mean, sd]") from None
                if not (math.isfinite(mean) and math.isfinite(sd)) or sd <= 0:
                    raise BaselineError(f"target for {metric} needs a finite mean and an SD > 0")
                targets[str(metric)] = (mean, sd)
            return cls("target", sessions, targets=targets)
        raise BaselineError(f"unknown baseline method {kind!r}; use first_n, range, or target")


@dataclass(frozen=True)
class Baseline:
    """Control limits of one segment for one metric."""

    mean: float
    sd: float
    n: int
    method: str
    provisional: bool
    description: str


@dataclass(frozen=True)
class Segment:
    """A stretch of history between rebaselining events."""

    index: int
    start: datetime | None
    """``None`` for the first segment."""
    event: Event | None
    """The event that started it, if any."""
    baseline: Baseline | None
    status: str
    """Human-readable state of the limits, e.g. ``collecting baseline (3 of 20)``."""


@dataclass(frozen=True)
class ControlPoint:
    """One session on a control chart."""

    session: SessionPoint
    segment: int
    z: float | None
    violations: frozenset[Rule]
    severity: str
    """``ok``, ``warning``, ``rejection``, or ``none`` (no limits, excluded, or no value)."""
    in_baseline: bool


@dataclass(frozen=True)
class Trend:
    """Least-squares drift over recent sessions (TRK-09)."""

    sessions: int
    per_month: float
    per_month_in_sd: float | None
    """Drift per month in units of the current baseline SD, when limits exist."""


@dataclass(frozen=True)
class SpecLine:
    """A specification limit drawn on the chart (TRK-08)."""

    value: float
    kind: str
    """``max`` or ``min``."""
    label: str


@dataclass(frozen=True)
class ControlSeries:
    """Everything needed to judge and draw one metric of one configuration."""

    configuration: Configuration
    metric: str
    points: tuple[ControlPoint, ...]
    segments: tuple[Segment, ...]
    events: tuple[Event, ...]
    rules: tuple[Rule, ...]
    spec: SpecLine | None
    trend: Trend | None

    @property
    def latest(self) -> ControlPoint | None:
        """The most recent session that has a value and is not excluded."""
        for point in reversed(self.points):
            if point.session.value is not None and not point.session.excluded:
                return point
        return None


def spec_line(metric: str, limits: SpecLimits | None) -> SpecLine | None:
    """Return the spec limit that applies to ``metric``, if any."""
    if limits is None:
        return None
    if metric in ("ratio_x", "ratio_y") and limits.lateral_ratio_max is not None:
        return SpecLine(limits.lateral_ratio_max, "max", f"spec ≤ {limits.lateral_ratio_max:g}")
    if metric == "ratio_z" and limits.axial_ratio_max is not None:
        return SpecLine(limits.axial_ratio_max, "max", f"spec ≤ {limits.axial_ratio_max:g}")
    if metric == "ellipticity_xy" and limits.ellipticity_min is not None:
        return SpecLine(limits.ellipticity_min, "min", f"spec ≥ {limits.ellipticity_min:g}")
    if metric == ACCEPTED_BEADS and limits.min_beads is not None:
        return SpecLine(float(limits.min_beads), "min", f"spec ≥ {limits.min_beads}")
    return None


def _usable(point: SessionPoint) -> bool:
    return point.value is not None and not point.excluded and math.isfinite(point.value)


def _from_values(values: Sequence[float], method: str, wanted: int, label: str) -> Baseline | None:
    if len(values) < MIN_BASELINE_SESSIONS:
        return None
    data = np.asarray(values, dtype=np.float64)
    sd = float(data.std(ddof=1))
    if sd <= 0:
        return None
    return Baseline(
        mean=float(data.mean()),
        sd=sd,
        n=int(data.size),
        method=method,
        provisional=method == "first_n" and data.size < wanted,
        description=label,
    )


def _baseline(
    method: BaselineMethod,
    metric: str,
    segment_points: Sequence[SessionPoint],
    all_points: Sequence[SessionPoint],
) -> tuple[Baseline | None, set[int], str]:
    """Return the baseline, the session ids it used, and a status text."""
    if method.kind == "target" and method.targets and metric in method.targets:
        mean, sd = method.targets[metric]
        return Baseline(mean, sd, 0, "target", False, "target entered by user"), set(), "target"
    if method.kind == "range":
        assert method.start is not None
        assert method.end is not None
        chosen = [
            p for p in all_points if _usable(p) and method.start <= p.acquired_utc <= method.end
        ]
        span = f"{method.start.date()} to {method.end.date()}"
        values = [p.value for p in chosen if p.value is not None]
        baseline = _from_values(values, "range", len(values), f"sessions from {span}")
        if baseline is None:
            return None, set(), f"no limits: {len(values)} usable sessions from {span}"
        return baseline, {p.session_id for p in chosen}, f"baseline from {span} ({len(values)})"
    wanted = method.sessions
    chosen = [p for p in segment_points if _usable(p)][:wanted]
    values = [p.value for p in chosen if p.value is not None]
    baseline = _from_values(values, "first_n", wanted, f"first {len(values)} sessions")
    if baseline is None:
        reason = "no spread" if len(values) >= MIN_BASELINE_SESSIONS else ""
        status = f"collecting baseline ({len(values)} of {wanted})"
        return None, {p.session_id for p in chosen}, f"{status}; {reason}" if reason else status
    status = (
        f"provisional baseline ({len(values)} of {wanted})"
        if baseline.provisional
        else f"baseline of {wanted} sessions"
    )
    return baseline, {p.session_id for p in chosen}, status


def _trend(points: Sequence[ControlPoint], baseline: Baseline | None) -> Trend | None:
    """Return the drift over the last sessions of the latest segment that has any."""
    segments = [p.segment for p in points if _usable(p.session)]
    if not segments:
        return None
    latest = max(segments)
    usable = [p.session for p in points if p.segment == latest and _usable(p.session)][
        -TREND_SESSIONS:
    ]
    if len(usable) < MIN_BASELINE_SESSIONS:
        return None
    start = usable[0].acquired_utc
    days = np.asarray([(p.acquired_utc - start).total_seconds() / 86400 for p in usable])
    if np.ptp(days) <= 0:
        return None
    values = np.asarray([p.value for p in usable], dtype=np.float64)
    slope = float(np.polyfit(days, values, 1)[0]) * DAYS_PER_MONTH
    in_sd = None if baseline is None else slope / baseline.sd
    return Trend(sessions=len(usable), per_month=slope, per_month_in_sd=in_sd)


def control_series(
    configuration: Configuration,
    metric: str,
    points: Sequence[SessionPoint],
    events: Sequence[Event],
    settings: TrackingSettings | None = None,
    spec_limits: SpecLimits | None = None,
) -> ControlSeries:
    """Build the control chart data for one metric of one configuration.

    Raises
    ------
    BaselineError
        If a rebaselining event carries an invalid baseline specification.
    """
    settings = settings or TrackingSettings()
    rules = settings.rules_for(metric)
    ordered = sorted(points, key=lambda p: (p.acquired_utc, p.session_id))
    relevant = sorted(
        (e for e in events if e.applies_to(configuration)), key=lambda e: (e.occurred_utc, e.id)
    )
    starts = [e for e in relevant if e.rebaseline]

    default = BaselineMethod("first_n", settings.baseline_sessions)
    boundaries: list[tuple[datetime | None, Event | None, BaselineMethod]] = [
        (None, None, default)
    ]
    for event in starts:
        method = default if event.baseline is None else BaselineMethod.from_dict(event.baseline)
        boundaries.append((event.occurred_utc, event, method))

    def segment_of(point: SessionPoint) -> int:
        index = 0
        for i, (start, _, _) in enumerate(boundaries):
            if start is not None and point.acquired_utc >= start:
                index = i
        return index

    by_segment: dict[int, list[SessionPoint]] = {i: [] for i in range(len(boundaries))}
    for point in ordered:
        by_segment[segment_of(point)].append(point)

    segments: list[Segment] = []
    control: list[ControlPoint] = []
    current: Baseline | None = None
    for index, (start, opener, method) in enumerate(boundaries):
        members = by_segment[index]
        if method.kind == "target" and method.targets and metric not in method.targets:
            method = BaselineMethod("first_n", method.sessions)
        baseline, used, status = _baseline(method, metric, members, ordered)
        segments.append(Segment(index, start, opener, baseline, status))
        z_scores: list[float | None] = []
        for point in members:
            if baseline is None or not _usable(point) or point.value is None:
                z_scores.append(None)
            else:
                z_scores.append((point.value - baseline.mean) / baseline.sd)
        violations = evaluate(z_scores, rules)
        for point, z, found in zip(members, z_scores, violations, strict=True):
            control.append(
                ControlPoint(
                    session=point,
                    segment=index,
                    z=z,
                    violations=frozenset(found),
                    severity="none" if z is None else severity(found),
                    in_baseline=point.session_id in used,
                )
            )
        if members or baseline is not None:
            current = baseline

    return ControlSeries(
        configuration=configuration,
        metric=metric,
        points=tuple(control),
        segments=tuple(segments),
        events=tuple(relevant),
        rules=rules,
        spec=spec_line(metric, spec_limits),
        trend=_trend(control, current),
    )


def series_from_database(
    database: ResultsDatabase,
    configuration: Configuration,
    metric: str,
    settings: TrackingSettings | None = None,
) -> ControlSeries:
    """Build the control series of one metric of one configuration from the database."""
    return control_series(
        configuration,
        metric,
        database.points(configuration, metric),
        database.events(configuration),
        settings,
        database.latest_spec_limits(configuration),
    )
