"""Overall PASS / WARNING / FAIL status of a session (REP-07).

A channel **fails** if it has no accepted bead, fails a spec limit, or breaks
a Westgard rejection rule on any tracked metric. It is a **warning** if a
tracked metric breaks only the 1_2s warning rule. It **passes** if at least
one check was evaluated and none failed or warned. With no spec limits and no
established control limits, it is **not evaluated**: a report never says
PASS when nothing was checked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.profiles.spec import Status as SpecStatus
from psfwidget.report.document import Status, worst
from psfwidget.tracking.control import series_from_database
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import ResultsDatabase, channel_key


@dataclass(frozen=True)
class MetricCheck:
    """One tracked metric of one session against its control limits."""

    metric: str
    value: float | None
    z: float | None
    severity: str
    """``ok``, ``warning``, ``rejection``, or ``none`` (no limits yet, or excluded)."""
    rules: tuple[str, ...]
    limits: str
    """State of the limits, e.g. ``baseline of 20 sessions``."""


@dataclass(frozen=True)
class ChannelStatus:
    """The status of one channel and why."""

    index: int
    name: str | None
    status: Status
    reasons: tuple[str, ...]


def session_checks(
    database: ResultsDatabase,
    result: FieldResult,
    session_id: int,
    settings: TrackingSettings,
) -> dict[int, list[MetricCheck]]:
    """Return each channel's tracked metrics for a recorded session (TRK-05)."""
    metadata = result.metadata
    if metadata.instrument_id is None:
        return {}
    instrument = metadata.instrument_id.value
    objective = None if metadata.objective_id is None else metadata.objective_id.value
    configurations = database.configurations()
    checks: dict[int, list[MetricCheck]] = {}
    for channel in result.channels:
        key = channel_key(metadata.channels[channel.index])
        candidates = [
            c
            for c in configurations
            if c.instrument_id == instrument
            and c.objective_id == objective
            and c.channel_key == key
        ]
        found: list[MetricCheck] = []
        for configuration in candidates:
            for metric in settings.metrics:
                series = series_from_database(database, configuration, metric, settings)
                point = next(
                    (
                        p
                        for p in series.points
                        if p.session.session_id == session_id
                        and p.session.channel_index == channel.index
                    ),
                    None,
                )
                if point is None:
                    continue
                found.append(
                    MetricCheck(
                        metric=metric,
                        value=point.session.value,
                        z=point.z,
                        severity=point.severity,
                        rules=tuple(sorted(r.value for r in point.violations)),
                        limits=series.segments[point.segment].status,
                    )
                )
        checks[channel.index] = found
    return checks


def channel_status(channel: ChannelResult, checks: Sequence[MetricCheck] = ()) -> ChannelStatus:
    """Return a channel's overall status and the reasons for it."""
    reasons: list[str] = []
    failed = warned = evaluated = False
    if not channel.accepted:
        failed = True
        reasons.append("no bead passed every check")
    if channel.spec is not None:
        for check in channel.spec.checks:
            if check.status is SpecStatus.FAIL:
                failed = True
                value = "-" if check.value is None else f"{check.value:.3g}"
                reasons.append(f"spec: {check.name} {value} (limit {check.limit})")
            elif check.status is SpecStatus.PASS:
                evaluated = True
    for tracked in checks:
        if tracked.severity == "rejection":
            failed = True
            reasons.append(f"{tracked.metric}: Westgard {', '.join(tracked.rules)}")
        elif tracked.severity == "warning":
            warned = True
            reasons.append(f"{tracked.metric}: Westgard {', '.join(tracked.rules)}")
        elif tracked.severity == "ok":
            evaluated = True
    if failed:
        status = Status.FAIL
    elif warned:
        status = Status.WARNING
    elif evaluated:
        status = Status.PASS
    else:
        status = Status.NOT_EVALUATED
        reasons.append("no spec limits set and no control limits established yet")
    if status is Status.PASS:
        reasons.append("all spec and control checks passed")
    return ChannelStatus(channel.index, channel.name, status, tuple(reasons))


def field_status(
    result: FieldResult, checks: Mapping[int, Sequence[MetricCheck]] | None = None
) -> list[ChannelStatus]:
    """Return the status of every channel of a field."""
    checks = checks or {}
    return [channel_status(c, checks.get(c.index, ())) for c in result.channels]


def overall(statuses: Sequence[ChannelStatus]) -> Status:
    """Return the most severe channel status."""
    return worst([s.status for s in statuses])
