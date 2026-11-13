"""Tests for baselines, segments, and control series (TRK-04, TRK-06 to TRK-09)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from psfwidget.profiles.spec import SpecLimits
from psfwidget.tracking.control import (
    BaselineError,
    BaselineMethod,
    control_series,
    spec_line,
)
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import Configuration, Event, SessionPoint
from psfwidget.tracking.westgard import Rule

CONF = Configuration("scope", "63x", "green", "confocal")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _points(
    values: Sequence[float | None], excluded: set[int] | None = None
) -> list[SessionPoint]:
    excluded = excluded or set()
    return [
        SessionPoint(
            session_id=i + 1,
            channel_index=0,
            acquired_utc=T0 + timedelta(days=i),
            date_source="metadata",
            file_name=f"f{i}.nd2",
            value=v,
            n_beads=10,
            excluded=i in excluded,
            exclusion_reason="dirty" if i in excluded else None,
        )
        for i, v in enumerate(values)
    ]


def _event(
    day: float,
    *,
    rebaseline: bool = True,
    baseline: dict[str, Any] | None = None,
    objective: str | None = None,
    channel: str | None = None,
    instrument: str = "scope",
    event_id: int = 1,
) -> Event:
    when = T0 + timedelta(days=day)
    return Event(
        event_id,
        when,
        instrument,
        objective,
        channel,
        "service",
        "laser aligned",
        rebaseline,
        baseline,
        when,
        "tester",
    )


def _series(
    values: Sequence[float | None],
    events: list[Event] | None = None,
    settings: TrackingSettings | None = None,
    excluded: set[int] | None = None,
    metric: str = "fwhm_z_um",
    spec: SpecLimits | None = None,
) -> Any:
    return control_series(
        CONF,
        metric,
        _points(values, excluded),
        events or [],
        settings or TrackingSettings(baseline_sessions=5),
        spec,
    )


BASE = [0.60, 0.62, 0.58, 0.61, 0.59]  # mean 0.60, SD 0.01581


@pytest.mark.req("TRK-04")
def test_first_n_baseline() -> None:
    series = _series([*BASE, 0.60, 0.65])
    (segment,) = series.segments
    assert segment.baseline is not None
    assert segment.baseline.mean == pytest.approx(0.60)
    assert segment.baseline.sd == pytest.approx(float(np.std(BASE, ddof=1)))
    assert segment.baseline.n == 5
    assert not segment.baseline.provisional
    assert segment.status == "baseline of 5 sessions"
    assert [p.in_baseline for p in series.points] == [True] * 5 + [False, False]
    last = series.points[-1]
    assert last.z == pytest.approx((0.65 - 0.60) / segment.baseline.sd)
    assert last.severity == "rejection"
    assert Rule.R1_3S in last.violations


@pytest.mark.req("TRK-04")
def test_baseline_states_while_collecting() -> None:
    settings = TrackingSettings(baseline_sessions=20)
    collecting = _series(BASE[:4], settings=settings)
    assert collecting.segments[0].baseline is None
    assert collecting.segments[0].status == "collecting baseline (4 of 20)"
    assert all(p.severity == "none" and p.z is None for p in collecting.points)
    provisional = _series([*BASE, 0.6], settings=settings)
    baseline = provisional.segments[0].baseline
    assert baseline is not None
    assert baseline.provisional
    assert provisional.segments[0].status == "provisional baseline (6 of 20)"
    flat = _series([0.6] * 6)
    assert flat.segments[0].baseline is None
    assert flat.segments[0].status == "collecting baseline (5 of 5); no spread"


@pytest.mark.req("TRK-07")
def test_excluded_and_missing_sessions_do_not_count() -> None:
    series = _series([0.90, *BASE, None, 0.60], excluded={0})
    baseline = series.segments[0].baseline
    assert baseline is not None
    assert baseline.mean == pytest.approx(0.60)  # the excluded 0.90 is ignored
    assert series.points[0].severity == "none"
    assert not series.points[0].in_baseline
    assert series.points[6].z is None
    assert series.latest is series.points[7]


@pytest.mark.req("TRK-06")
def test_rebaseline_starts_a_new_segment_and_restarts_rules() -> None:
    after = [0.70, 0.72, 0.68, 0.71, 0.69]
    events = [_event(4.5)]
    series = _series([*BASE, *after, 0.70], events)
    first, second = series.segments
    assert first.baseline is not None
    assert second.baseline is not None
    assert first.baseline.mean == pytest.approx(0.60)
    assert second.baseline.mean == pytest.approx(0.70)
    assert second.event is events[0]
    assert second.start == events[0].occurred_utc
    assert [p.segment for p in series.points] == [0] * 5 + [1] * 6
    # Without the rebaseline, the new level would be a run of rejections.
    assert all(p.severity == "ok" for p in series.points[5:])
    plain = _series([*BASE, *after, 0.70])
    assert all(p.severity == "rejection" for p in plain.points[5:])


@pytest.mark.req("TRK-06")
def test_event_scope_and_annotation_only_events() -> None:
    other_objective = _event(2.5, objective="10x")
    other_channel = _event(2.5, channel="red", event_id=2)
    other_instrument = _event(2.5, instrument="other", event_id=3)
    note = _event(2.5, rebaseline=False, event_id=4)
    series = _series([*BASE, 0.6], [other_objective, other_channel, other_instrument, note])
    assert len(series.segments) == 1
    assert series.events == (note,)
    matching = _event(2.5, objective="63x", channel="green", event_id=5)
    assert len(_series([*BASE, 0.6], [matching]).segments) == 2


@pytest.mark.req("TRK-04")
def test_range_baseline() -> None:
    values = [0.50, 0.50, *BASE, 0.60, 0.60]
    start, end = T0 + timedelta(days=2), T0 + timedelta(days=6)
    method = BaselineMethod("range", start=start, end=end)
    series = _series(values, [_event(0.5, baseline=method.to_dict())])
    baseline = series.segments[1].baseline
    assert baseline is not None
    assert baseline.method == "range"
    assert baseline.mean == pytest.approx(0.60)
    assert series.segments[1].status == "baseline from 2026-01-03 to 2026-01-07 (5)"
    too_short = BaselineMethod("range", start=start, end=start + timedelta(days=1))
    short = _series(values, [_event(0.5, baseline=too_short.to_dict())])
    assert short.segments[1].baseline is None
    assert short.segments[1].status.startswith("no limits: 2 usable sessions")


@pytest.mark.req("TRK-04")
def test_target_baseline_with_fallback_for_other_metrics() -> None:
    method = BaselineMethod("target", targets={"fwhm_z_um": (0.55, 0.02)})
    events = [_event(-1, baseline=method.to_dict())]
    series = _series([*BASE, 0.61], events)
    baseline = series.segments[1].baseline
    assert baseline is not None
    assert (baseline.mean, baseline.sd, baseline.method) == (0.55, 0.02, "target")
    assert series.points[-1].z == pytest.approx((0.61 - 0.55) / 0.02)
    other = _series([*BASE, 0.61], events, metric="fwhm_x_um")
    other_baseline = other.segments[1].baseline
    assert other_baseline is not None
    assert other_baseline.method == "first_n"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"method": "magic"}, "unknown baseline method"),
        ({"method": "first_n", "sessions": 2}, "at least 5"),
        ({"method": "range", "start": "2026-01-01T00:00:00+00:00"}, "start and end"),
        ({"method": "range", "start": "2026-01-01", "end": "2026-02-01"}, "time zone"),
        (
            {
                "method": "range",
                "start": "2026-02-01T00:00:00+00:00",
                "end": "2026-01-01T00:00:00+00:00",
            },
            "end after it starts",
        ),
        ({"method": "target"}, "needs targets"),
        ({"method": "target", "targets": {"fwhm_z_um": [0.5]}}, r"\[mean, sd\]"),
        ({"method": "target", "targets": {"fwhm_z_um": [0.5, 0]}}, "SD > 0"),
    ],
)
def test_invalid_baseline_methods(data: dict[str, Any], message: str) -> None:
    with pytest.raises(BaselineError, match=message):
        BaselineMethod.from_dict(data)


def test_baseline_method_round_trip() -> None:
    for method in (
        BaselineMethod("first_n", 12),
        BaselineMethod("range", start=T0, end=T0 + timedelta(days=9)),
        BaselineMethod("target", targets={"ratio_z": (1.2, 0.05)}),
    ):
        assert BaselineMethod.from_dict(method.to_dict()) == method


@pytest.mark.req("TRK-05")
def test_per_metric_rules() -> None:
    settings = TrackingSettings(baseline_sessions=5, metric_rules={"fwhm_z_um": (Rule.R1_3S,)})
    series = _series([*BASE, 0.635], settings=settings)  # z ≈ 2.2
    assert series.rules == (Rule.R1_3S,)
    assert series.points[-1].severity == "ok"


@pytest.mark.req("TRK-08")
def test_spec_lines() -> None:
    limits = SpecLimits(
        lateral_ratio_max=1.3, axial_ratio_max=1.6, ellipticity_min=0.8, min_beads=5
    )
    assert spec_line("ratio_x", limits).label == "spec ≤ 1.3"  # type: ignore[union-attr]
    assert spec_line("ratio_y", limits).value == 1.3  # type: ignore[union-attr]
    assert spec_line("ratio_z", limits).value == 1.6  # type: ignore[union-attr]
    ellipticity = spec_line("ellipticity_xy", limits)
    assert ellipticity is not None
    assert (ellipticity.kind, ellipticity.label) == ("min", "spec ≥ 0.8")
    assert spec_line("accepted_beads", limits).value == 5.0  # type: ignore[union-attr]
    assert spec_line("fwhm_x_um", limits) is None
    assert spec_line("ratio_x", None) is None
    assert spec_line("ratio_x", SpecLimits()) is None
    assert _series(BASE, metric="ratio_z", spec=limits).spec is not None


@pytest.mark.req("TRK-09")
def test_trend_uses_the_latest_segment() -> None:
    rising = [0.60 + 0.002 * i for i in range(10)]
    series = _series([*BASE, *rising])
    assert series.trend is not None
    assert series.trend.sessions == 10
    assert series.trend.per_month == pytest.approx(0.002 * 30.4375, rel=1e-6)
    assert series.trend.per_month_in_sd is not None
    reset = _series([*rising, *BASE], [_event(9.5)])
    assert reset.trend is not None
    assert reset.trend.sessions == 5  # only the sessions after the reset
    assert _series(BASE[:4]).trend is None


def test_empty_series() -> None:
    series = _series([])
    assert series.points == ()
    assert series.latest is None
    assert series.trend is None
