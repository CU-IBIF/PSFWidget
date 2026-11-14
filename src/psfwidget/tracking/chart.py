"""Levey-Jennings charts (TRK-03, TRK-06, TRK-08).

Each session's median is plotted against its acquisition date with the
baseline mean and ±1, ±2, ±3 SD limits of its segment. Westgard warnings and
rejections are marked by shape and colour and named in the legend; rejection
points are labelled with the rules they broke. Excluded sessions are hollow
and not connected. Events are vertical lines; a solid line starts a new
baseline. A spec limit, when one applies, is a dash-dot line.

Needs matplotlib (``pip install psfwidget[report]``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psfwidget.tracking.control import ControlPoint, ControlSeries, Segment
from psfwidget.tracking.store import Event

if TYPE_CHECKING:
    from matplotlib.axes import Axes

# Light chart theme (dataviz reference palette).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = "#2a78d6"
WARNING = "#fab219"
CRITICAL = "#d03b3b"
BASELINE_SHADE = "#f0efec"

METRIC_LABELS: dict[str, str] = {
    "fwhm_x_um": "FWHM x (µm)",
    "fwhm_y_um": "FWHM y (µm)",
    "fwhm_z_um": "FWHM z (µm)",
    "fwhm_x_raw_um": "FWHM x, uncorrected (µm)",
    "fwhm_y_raw_um": "FWHM y, uncorrected (µm)",
    "fwhm_z_raw_um": "FWHM z, uncorrected (µm)",
    "xy_major_um": "XY major FWHM (µm)",
    "xy_minor_um": "XY minor FWHM (µm)",
    "xz_major_um": "XZ major FWHM (µm)",
    "xz_minor_um": "XZ minor FWHM (µm)",
    "yz_major_um": "YZ major FWHM (µm)",
    "yz_minor_um": "YZ minor FWHM (µm)",
    "ellipticity_xy": "XY ellipticity (minor / major)",
    "ratio_x": "FWHM x / theoretical",
    "ratio_y": "FWHM y / theoretical",
    "ratio_z": "FWHM z / theoretical",
    "integrated_intensity": "Integrated intensity (counts)",
    "peak": "Peak (counts)",
    "background": "Background (counts)",
    "snr": "Signal-to-noise ratio",
    "sbr": "Signal-to-background ratio",
    "accepted_beads": "Accepted beads",
}


class ChartError(RuntimeError):
    """Raised when a chart cannot be drawn."""


def metric_label(metric: str) -> str:
    """Return the axis label for a metric."""
    return METRIC_LABELS.get(metric, metric)


def _segment_span(
    series: ControlSeries, segment: Segment, pad: timedelta
) -> tuple[datetime, datetime] | None:
    members = [p.session.acquired_utc for p in series.points if p.segment == segment.index]
    later = [s.start for s in series.segments if s.index > segment.index and s.start is not None]
    start = (
        segment.start if segment.start is not None else (min(members) - pad if members else None)
    )
    if start is None:
        return None
    if later:
        end = min(later)
    elif members:
        end = max(members) + pad
    else:
        end = start + pad
    return start, end


def _limit_lines(axes: Axes, series: ControlSeries, pad: timedelta) -> list[float]:
    """Draw each segment's mean and SD lines; return the y values drawn."""
    drawn: list[float] = []
    last: tuple[Segment, datetime] | None = None
    for segment in series.segments:
        baseline = segment.baseline
        span = _segment_span(series, segment, pad)
        if baseline is None or span is None:
            continue
        alpha = 0.55 if baseline.provisional else 1.0
        x = [_num(span[0]), _num(span[1])]
        axes.plot(
            x, [baseline.mean] * 2, color=INK_SECONDARY, linewidth=1.2, alpha=alpha, zorder=2
        )
        for k, color, style, width in (
            (1, INK_MUTED, ":", 0.8),
            (2, WARNING, "--", 1.2),
            (3, CRITICAL, "--", 1.2),
        ):
            for sign in (1, -1):
                y = baseline.mean + sign * k * baseline.sd
                axes.plot(
                    x, [y, y], color=color, linestyle=style, linewidth=width, alpha=alpha, zorder=2
                )
                drawn.append(y)
        drawn.append(baseline.mean)
        last = (segment, span[1])
    if last is not None:
        segment, end = last
        baseline = segment.baseline
        assert baseline is not None
        suffix = " (provisional)" if baseline.provisional else ""
        labels = [(baseline.mean, f"mean{suffix}")] + [
            (baseline.mean + sign * k * baseline.sd, f"{'+' if sign > 0 else '−'}{k} SD")
            for k in (1, 2, 3)
            for sign in (1, -1)
        ]
        for y, text in labels:
            axes.annotate(
                text,
                (end, y),
                xytext=(4, 0),
                textcoords="offset points",
                va="center",
                ha="left",
                fontsize=7.5,
                color=INK_MUTED,
                annotation_clip=False,
            )
    return drawn


def _baseline_shading(axes: Axes, points: Sequence[ControlPoint], pad: timedelta) -> bool:
    shaded = False
    for segment in sorted({p.segment for p in points}):
        used = [p.session.acquired_utc for p in points if p.segment == segment and p.in_baseline]
        if len(used) >= 2:
            axes.axvspan(
                _num(min(used) - pad / 2),
                _num(max(used) + pad / 2),
                color=BASELINE_SHADE,
                zorder=0,
                linewidth=0,
            )
            shaded = True
    return shaded


def _events(axes: Axes, series: ControlSeries) -> bool:
    for event in series.events:
        style = "-" if event.rebaseline else "--"
        axes.axvline(
            _num(event.occurred_utc), color=INK_SECONDARY, linestyle=style, linewidth=1.0, zorder=1
        )
        axes.annotate(
            f"{number_of(series, event)} {event.kind}",
            (event.occurred_utc, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(3, -4),
            textcoords="offset points",
            rotation=90,
            va="top",
            ha="left",
            fontsize=7.5,
            color=INK_SECONDARY,
        )
    return bool(series.events)


def number_of(series: ControlSeries, event: Event) -> str:
    """Return the event's reference number on the chart, e.g. ``[2]``."""
    return f"[{series.events.index(event) + 1}]"


def _event_notes(series: ControlSeries) -> str:
    notes = []
    for event in series.events:
        when = event.occurred_utc.strftime("%Y-%m-%d")
        reset = " (new baseline)" if event.rebaseline else ""
        notes.append(f"{number_of(series, event)} {when} {event.kind}: {event.description}{reset}")
    return "Events: " + "; ".join(notes) if notes else ""


def _num(moment: datetime) -> float:
    """Convert a datetime to matplotlib's date number."""
    from matplotlib.dates import date2num

    return float(date2num(moment))  # type: ignore[no-untyped-call]


def _dates(points: Sequence[ControlPoint]) -> list[float]:
    return [_num(p.session.acquired_utc) for p in points]


def _values(points: Sequence[ControlPoint]) -> list[float]:
    return [p.session.value if p.session.value is not None else float("nan") for p in points]


def _rejection_runs(included: Sequence[ControlPoint]) -> list[list[ControlPoint]]:
    """Group consecutive rejected points (in time order) into runs."""
    runs: list[list[ControlPoint]] = []
    current: list[ControlPoint] = []
    for point in sorted(included, key=lambda p: p.session.acquired_utc):
        if point.severity == "rejection":
            current.append(point)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _subtitle(series: ControlSeries) -> str:
    parts = [series.segments[-1].status] if series.segments else []
    parts.append("rules " + ", ".join(rule.value for rule in series.rules))
    trend = series.trend
    if trend is not None:
        drift = f"drift {trend.per_month:+.3g}/month over {trend.sessions} sessions"
        if trend.per_month_in_sd is not None:
            drift += f" ({trend.per_month_in_sd:+.2f} SD/month)"
        parts.append(drift)
    return " · ".join(parts)


def draw(series: ControlSeries, path: str | Path, *, dpi: int = 150) -> Path:
    """Draw a Levey-Jennings chart to ``path`` (PNG, SVG, or PDF by extension).

    Raises
    ------
    ChartError
        If matplotlib is missing or no session has a value for the metric.
    """
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ChartError("charts need matplotlib: pip install 'psfwidget[report]'") from exc

    figure = Figure(figsize=(10, 4.6), facecolor=SURFACE)
    FigureCanvasAgg(figure)
    render(series, figure)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=dpi, facecolor=SURFACE)
    return target


def render(series: ControlSeries, figure: Any) -> tuple[Any, list[ControlPoint]]:
    """Draw a Levey-Jennings chart on a matplotlib ``figure`` (cleared first).

    Returns the axes and the plotted points (sessions with a value) in time
    order, so an interactive view can map clicks back to sessions.

    Raises
    ------
    ChartError
        If no session has a value for the metric.
    """
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.lines import Line2D

    valued = [p for p in series.points if p.session.value is not None]
    if not valued:
        raise ChartError(f"no session has a value for {series.metric}")

    times = [p.session.acquired_utc for p in valued]
    spread = max(times) - min(times)
    pad = max(spread / 40, timedelta(hours=12))

    figure.clear()
    figure.set_facecolor(SURFACE)
    axes = figure.add_axes((0.08, 0.23, 0.8, 0.63), facecolor=SURFACE)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(AXIS)
    axes.tick_params(colors=INK_MUTED, labelsize=8, length=3)
    axes.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    axes.set_axisbelow(True)

    shaded = _baseline_shading(axes, series.points, pad)
    limits = _limit_lines(axes, series, pad)
    has_events = _events(axes, series)

    included = [p for p in valued if not p.session.excluded]
    for segment in sorted({p.segment for p in included}):
        members = [p for p in included if p.segment == segment]
        axes.plot(
            _dates(members),
            _values(members),
            color=SERIES,
            linewidth=2,
            marker="o",
            markersize=5.5,
            markeredgecolor=SURFACE,
            markeredgewidth=1.0,
            zorder=4,
        )
    warnings = [p for p in included if p.severity == "warning"]
    rejections = [p for p in included if p.severity == "rejection"]
    excluded = [p for p in valued if p.session.excluded]
    if warnings:
        axes.scatter(
            _dates(warnings),
            _values(warnings),
            marker="^",
            s=70,
            color=WARNING,
            edgecolors=INK,
            linewidths=0.6,
            zorder=5,
        )
    if rejections:
        axes.scatter(
            _dates(rejections),
            _values(rejections),
            marker="D",
            s=55,
            color=CRITICAL,
            edgecolors=SURFACE,
            linewidths=1.2,
            zorder=6,
        )
    # One label per run of consecutive rejections, at its last point, naming every
    # rule broken in the run; labels on neighbouring points would collide.
    for run in _rejection_runs(included):
        rules = sorted({r.value for p in run for r in p.violations if not r.is_warning})
        last = run[-1]
        axes.annotate(
            ", ".join(rules),
            (last.session.acquired_utc, last.session.value),
            xytext=(4, 8),
            textcoords="offset points",
            ha="right",
            va="bottom",
            fontsize=7.5,
            color=INK,
            zorder=7,
        )
    if excluded:
        axes.scatter(
            _dates(excluded),
            _values(excluded),
            marker="o",
            s=40,
            facecolors=SURFACE,
            edgecolors=INK_MUTED,
            linewidths=1.2,
            zorder=4,
        )
    if series.spec is not None:
        axes.axhline(series.spec.value, color=INK, linestyle="-.", linewidth=1.2, zorder=3)
        axes.annotate(
            series.spec.label,
            (1.0, series.spec.value),
            xycoords=("axes fraction", "data"),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=7.5,
            color=INK,
            annotation_clip=False,
        )
        limits.append(series.spec.value)

    values = [p.session.value for p in valued if p.session.value is not None] + limits
    low, high = min(values), max(values)
    margin = (high - low) * 0.08 or abs(high) * 0.05 or 1.0
    axes.set_ylim(low - margin, high + 2 * margin)  # headroom for rule labels
    axes.set_xlim(_num(min(times) - pad), _num(max(times) + pad))
    locator = AutoDateLocator()  # type: ignore[no-untyped-call]
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(ConciseDateFormatter(locator))  # type: ignore[no-untyped-call]
    axes.set_ylabel(metric_label(series.metric), color=INK_SECONDARY, fontsize=9)

    figure.text(
        0.08,
        0.95,
        f"{metric_label(series.metric)} — {series.configuration.label}",
        fontsize=11,
        color=INK,
        ha="left",
        va="top",
    )
    figure.text(
        0.08, 0.905, _subtitle(series), fontsize=8, color=INK_SECONDARY, ha="left", va="top"
    )

    handles: list[Any] = [
        Line2D(
            [], [], color=SERIES, linewidth=2, marker="o", markersize=5.5, label="session median"
        ),
        Line2D([], [], color=INK_SECONDARY, linewidth=1.2, label="baseline mean"),
        Line2D([], [], color=WARNING, linestyle="--", linewidth=1.2, label="±2 SD"),
        Line2D([], [], color=CRITICAL, linestyle="--", linewidth=1.2, label="±3 SD"),
    ]
    if warnings:
        handles.append(
            Line2D(
                [],
                [],
                linestyle="",
                marker="^",
                markersize=8,
                color=WARNING,
                markeredgecolor=INK,
                label="warning (1_2s)",
            )
        )
    if rejections:
        handles.append(
            Line2D(
                [],
                [],
                linestyle="",
                marker="D",
                markersize=7,
                color=CRITICAL,
                label="rejection (rules named)",
            )
        )
    if excluded:
        handles.append(
            Line2D(
                [],
                [],
                linestyle="",
                marker="o",
                markersize=6,
                markerfacecolor=SURFACE,
                markeredgecolor=INK_MUTED,
                label="excluded",
            )
        )
    if shaded:
        handles.append(
            Line2D([], [], color=BASELINE_SHADE, linewidth=8, label="baseline sessions")
        )
    if has_events:
        handles.append(
            Line2D(
                [],
                [],
                color=INK_SECONDARY,
                linestyle="--",
                linewidth=1,
                label="event (solid: new baseline)",
            )
        )
    if series.spec is not None:
        handles.append(
            Line2D([], [], color=INK, linestyle="-.", linewidth=1.2, label="spec limit")
        )
    notes = _event_notes(series)
    legend = figure.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.07, 0.04 if notes else 0.0),
        ncol=min(len(handles), 5),
        frameon=False,
        fontsize=8,
    )
    if notes:
        if len(notes) > 190:
            notes = notes[:189] + "…"
        figure.text(0.08, 0.015, notes, fontsize=7.5, color=INK_SECONDARY, ha="left", va="bottom")
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)
    return axes, valued
