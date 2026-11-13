"""Tests for Levey-Jennings charts (TRK-03, TRK-06, TRK-08)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from psfwidget.profiles.spec import SpecLimits
from psfwidget.tracking.chart import ChartError, _rejection_runs, draw, metric_label
from psfwidget.tracking.control import control_series
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import Configuration, Event, SessionPoint

CONF = Configuration("scope", "63x", "green", "confocal")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
VALUES = [1.10, 1.12, 1.08, 1.11, 1.09, 1.10, 1.13, 1.15, 1.17, 1.25, 1.30, 1.02, 1.04, 1.03]


def _points(values: Sequence[float | None], excluded: set[int]) -> list[SessionPoint]:
    return [
        SessionPoint(
            i + 1,
            0,
            T0 + timedelta(days=7 * i),
            "metadata",
            f"f{i}",
            v,
            10,
            i in excluded,
            "x" if i in excluded else None,
        )
        for i, v in enumerate(values)
    ]


def _events() -> list[Event]:
    service = T0 + timedelta(days=7 * 10.5)
    note = T0 + timedelta(days=7 * 2.5)
    return [
        Event(
            1,
            service,
            "scope",
            None,
            None,
            "service",
            "objective cleaned and laser aligned",
            True,
            None,
            service,
            "eng",
        ),
        Event(
            2,
            note,
            "scope",
            None,
            None,
            "other",
            "room temperature excursion",
            False,
            None,
            note,
            "eng",
        ),
    ]


@pytest.mark.req("TRK-03", "TRK-06", "TRK-08")
@pytest.mark.parametrize("suffix", ["png", "svg", "pdf"])
def test_chart_with_everything(tmp_path: Path, suffix: str) -> None:
    series = control_series(
        CONF,
        "ratio_x",
        _points(VALUES, {5}),
        _events(),
        TrackingSettings(baseline_sessions=5),
        SpecLimits(lateral_ratio_max=1.2),
    )
    assert any(p.severity == "rejection" for p in series.points)
    assert series.spec is not None
    path = draw(series, tmp_path / "charts" / f"lj.{suffix}")
    assert path.exists()
    assert path.stat().st_size > 5_000
    if suffix == "svg":
        text = path.read_text("utf-8")
        for label in (
            "FWHM x / theoretical",
            "scope / 63x / green / confocal",
            "+3 SD",
            "spec ≤ 1.2",
            "rejection (rules named)",
            "excluded",
            "[1] 2026-01-18 other: room temperature excursion",
            "objective cleaned and laser aligned (new baseline)",
        ):
            assert label in text, label


def test_chart_without_limits_events_or_spec(tmp_path: Path) -> None:
    series = control_series(CONF, "fwhm_z_um", _points([0.6, 0.62, None], set()), [])
    assert series.segments[0].baseline is None
    path = draw(series, tmp_path / "plain.png")
    assert path.stat().st_size > 1_000


def test_chart_needs_a_value(tmp_path: Path) -> None:
    series = control_series(CONF, "fwhm_z_um", _points([None, None], set()), [])
    with pytest.raises(ChartError, match="no session has a value for fwhm_z_um"):
        draw(series, tmp_path / "none.png")


def test_rejection_runs_group_consecutive_points() -> None:
    series = control_series(
        CONF,
        "fwhm_z_um",
        _points([0.6, 0.61, 0.59, 0.6, 0.62, 0.9, 0.95, 0.6, 0.98], set()),
        [],
        TrackingSettings(baseline_sessions=5),
    )
    runs = _rejection_runs(series.points)
    assert [[p.session.session_id for p in run] for run in runs] == [[6, 7], [9]]


def test_metric_labels() -> None:
    assert metric_label("fwhm_z_um") == "FWHM z (µm)"
    assert metric_label("accepted_beads") == "Accepted beads"
    assert metric_label("custom") == "custom"
