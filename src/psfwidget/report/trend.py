"""The trend report: control charts and rule violations over a period (REP-02, REP-07)."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from psfwidget.report import document as doc
from psfwidget.report.session import fmt
from psfwidget.tracking.chart import draw, metric_label
from psfwidget.tracking.control import ControlSeries, series_from_database
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import Configuration, ResultsDatabase

DEFAULT_CHART_METRICS: tuple[str, ...] = (
    "fwhm_x_um",
    "fwhm_y_um",
    "fwhm_z_um",
    "integrated_intensity",
    "accepted_beads",
)
"""Metrics drawn as charts; every tracked metric is still listed in the tables."""

_SEVERITY_STATUS = {
    "rejection": doc.Status.FAIL,
    "warning": doc.Status.WARNING,
    "ok": doc.Status.PASS,
    "none": doc.Status.NOT_EVALUATED,
}


def _in_period(
    series: ControlSeries, since: datetime | None, until: datetime | None
) -> ControlSeries:
    """Keep only the sessions and events in the period; limits still use all history."""

    def inside(moment: datetime) -> bool:
        return (since is None or moment >= since) and (until is None or moment <= until)

    return replace(
        series,
        points=tuple(p for p in series.points if inside(p.session.acquired_utc)),
        events=tuple(e for e in series.events if inside(e.occurred_utc)),
    )


def chart_png(series: ControlSeries) -> bytes:
    """Return a Levey-Jennings chart as PNG bytes."""
    with tempfile.TemporaryDirectory() as folder:
        path = draw(series, Path(folder) / "chart.png", dpi=110)
        return path.read_bytes()


def _latest_table(all_series: Sequence[ControlSeries]) -> tuple[doc.Table, doc.Status]:
    rows = []
    statuses = []
    for series in all_series:
        latest = series.latest
        if latest is None:
            continue
        status = _SEVERITY_STATUS[latest.severity]
        statuses.append(status)
        trend = series.trend
        drift = (
            "-"
            if trend is None or trend.per_month_in_sd is None
            else (f"{trend.per_month_in_sd:+.2f} SD/month")
        )
        rows.append(
            (
                metric_label(series.metric),
                fmt(latest.session.value),
                "-" if latest.z is None else f"{latest.z:+.2f}",
                status.value,
                ", ".join(sorted(r.value for r in latest.violations)) or "-",
                series.segments[latest.segment].status,
                drift,
            )
        )
    table = doc.Table(
        ("Metric", "Latest", "z", "Status", "Rules broken", "Control limits", "Drift"),
        tuple(rows),
        frozenset({1, 2}),
        status_column=3,
        caption="Most recent included session of each tracked metric.",
    )
    return table, doc.worst(statuses)


def _violations_table(all_series: Sequence[ControlSeries]) -> doc.Table | None:
    rows = []
    for series in all_series:
        for point in series.points:
            if point.severity not in ("warning", "rejection"):
                continue
            rows.append(
                (
                    point.session.acquired_utc.strftime("%Y-%m-%d"),
                    str(point.session.session_id),
                    point.session.file_name,
                    metric_label(series.metric),
                    fmt(point.session.value),
                    f"{point.z:+.2f}" if point.z is not None else "-",
                    _SEVERITY_STATUS[point.severity].value,
                    ", ".join(sorted(r.value for r in point.violations)),
                )
            )
    if not rows:
        return None
    rows.sort()
    return doc.Table(
        ("Date", "Session", "File", "Metric", "Value", "z", "Status", "Rules"),
        tuple(rows),
        frozenset({1, 4, 5}),
        status_column=6,
        caption="Every Westgard warning and rejection in the period.",
    )


def _events_table(series: ControlSeries) -> doc.Table | None:
    if not series.events:
        return None
    rows = tuple(
        (
            e.occurred_utc.strftime("%Y-%m-%d"),
            e.kind,
            e.description,
            "yes" if e.rebaseline else "no",
            e.created_by,
        )
        for e in series.events
    )
    return doc.Table(
        ("Date", "Kind", "Description", "New baseline", "Recorded by"),
        rows,
        caption="Events in the period.",
    )


def _sessions_text(series: ControlSeries) -> str:
    points = series.points
    if not points:
        return "No sessions in the period."
    excluded = sum(1 for p in points if p.session.excluded)
    first = points[0].session.acquired_utc.strftime("%Y-%m-%d")
    last = points[-1].session.acquired_utc.strftime("%Y-%m-%d")
    text = f"{len(points)} sessions from {first} to {last}"
    if excluded:
        reasons = sorted({p.session.exclusion_reason or "" for p in points if p.session.excluded})
        text += f"; {excluded} excluded ({'; '.join(r for r in reasons if r)})"
    return text + "."


def build_trend_report(
    database: ResultsDatabase,
    configurations: Sequence[Configuration],
    settings: TrackingSettings | None = None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    chart_metrics: Sequence[str] = DEFAULT_CHART_METRICS,
    branding: doc.Branding | None = None,
) -> doc.Report:
    """Build a trend report for the given configurations and period."""
    settings = settings or TrackingSettings()
    blocks: list[doc.Block] = []
    statuses: list[doc.Status] = []
    period = " to ".join(m.strftime("%Y-%m-%d") for m in (since, until) if m is not None)
    for index, configuration in enumerate(configurations):
        if index:
            blocks.append(doc.PageBreak())
        all_series = [
            _in_period(
                series_from_database(database, configuration, metric, settings), since, until
            )
            for metric in dict.fromkeys([*settings.metrics, *chart_metrics])
        ]
        tracked = [s for s in all_series if s.metric in settings.metrics]
        blocks.append(doc.Heading(configuration.label))
        blocks.append(doc.Paragraph(_sessions_text(all_series[0])))
        table, status = _latest_table(tracked)
        statuses.append(status)
        blocks.append(doc.StatusLine(status, "status of the most recent session"))
        blocks.append(table)
        violations = _violations_table(tracked)
        if violations is not None:
            blocks.append(violations)
        events = _events_table(all_series[0])
        if events is not None:
            blocks.append(events)
        for series in all_series:
            if series.metric in chart_metrics and any(
                p.session.value is not None for p in series.points
            ):
                blocks.append(
                    doc.Figure(
                        chart_png(series), f"{metric_label(series.metric)}: Levey-Jennings chart."
                    )
                )
    if not configurations:
        blocks.append(doc.Paragraph("No recorded results match this report."))
    names = sorted({c.instrument_id for c in configurations})
    subtitle = ", ".join(names) or "no instruments"
    if period:
        subtitle += f" · {period}"
    return doc.Report(
        title="PSF quality control: trend report",
        subtitle=subtitle,
        status=doc.worst(statuses),
        generated_utc=datetime.now(UTC),
        blocks=tuple(blocks),
        branding=branding or doc.Branding(),
    )
