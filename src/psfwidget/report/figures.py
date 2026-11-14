"""Figures for session reports (REP-01, THE-07).

* Bead overview: maximum projection of the field with accepted and rejected
  beads marked by shape and colour.
* Orthogonal views: XY, XZ, and YZ slices through a representative bead at
  true physical aspect ratio.
* Profile fits: the measured x, y, and z profiles with the fitted Gaussian and
  a Gaussian of the theoretical width for comparison.

Needs matplotlib (``pip install psfwidget[report]``).
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from psfwidget.analysis import ChannelResult
from psfwidget.metrics.bead import BeadResult, half_box
from psfwidget.metrics.gaussian import FitError, fit_gaussian_1d
from psfwidget.settings import AnalysisSettings
from psfwidget.synthetic import FWHM_PER_SIGMA
from psfwidget.tracking.chart import (
    CRITICAL,
    GRID,
    INK,
    INK_MUTED,
    INK_SECONDARY,
    SERIES,
    SURFACE,
)

GOOD = "#0ca30c"


def _figure(width: float, height: float) -> Any:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height), facecolor=SURFACE)
    FigureCanvasAgg(figure)
    return figure


def _png(figure: Any, dpi: int = 130) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, facecolor=SURFACE)
    return buffer.getvalue()


def _contrast(image: NDArray[Any]) -> tuple[float, float]:
    low, high = (float(v) for v in np.percentile(image, [1.0, 99.8]))
    return low, high if high > low else low + 1.0


def representative_bead(channel: ChannelResult) -> BeadResult | None:
    """Return the accepted bead whose lateral FWHM is closest to the channel median."""
    accepted = [b for b in channel.accepted if b.fwhm_x_um is not None and b.fwhm_y_um is not None]
    stats_x, stats_y = channel.summary.get("fwhm_x_um"), channel.summary.get("fwhm_y_um")
    if not accepted or stats_x is None or stats_y is None:
        return None
    target = (stats_x.median + stats_y.median) / 2
    return min(accepted, key=lambda b: abs((b.fwhm_x_um + b.fwhm_y_um) / 2 - target))  # type: ignore[operator]


def bead_overview(
    volume: NDArray[Any], beads: Sequence[BeadResult], voxel_um: tuple[float, float, float]
) -> bytes:
    """Return a PNG of the XY maximum projection with beads marked."""
    from matplotlib.lines import Line2D

    projection = volume.max(axis=0)
    height_um = projection.shape[0] * voxel_um[1]
    width_um = projection.shape[1] * voxel_um[2]
    aspect = height_um / width_um
    figure = _figure(7.0, min(9.0, max(3.0, 7.0 * aspect)) + 0.6)
    axes = figure.add_axes((0.08, 0.12, 0.84, 0.82))
    low, high = _contrast(projection)
    axes.imshow(
        projection,
        cmap="gray",
        vmin=low,
        vmax=high,
        origin="upper",
        extent=(0, width_um, height_um, 0),
        interpolation="nearest",
    )
    accepted = [b for b in beads if b.accepted]
    rejected = [b for b in beads if not b.accepted]
    if accepted:
        axes.scatter(
            [b.x_px * voxel_um[2] for b in accepted],
            [b.y_px * voxel_um[1] for b in accepted],
            s=110,
            facecolors="none",
            edgecolors=GOOD,
            linewidths=1.4,
        )
        for bead in accepted:
            axes.annotate(
                str(bead.bead_id),
                (bead.x_px * voxel_um[2], bead.y_px * voxel_um[1]),
                xytext=(6, 4),
                textcoords="offset points",
                color=GOOD,
                fontsize=7,
            )
    if rejected:
        axes.scatter(
            [b.x_px * voxel_um[2] for b in rejected],
            [b.y_px * voxel_um[1] for b in rejected],
            s=50,
            marker="x",
            color=CRITICAL,
            linewidths=1.2,
        )
    axes.set_xlabel("x (µm)", color=INK_SECONDARY, fontsize=8)
    axes.set_ylabel("y (µm)", color=INK_SECONDARY, fontsize=8)
    axes.tick_params(colors=INK_MUTED, labelsize=7)
    handles = [
        Line2D(
            [],
            [],
            linestyle="",
            marker="o",
            markersize=8,
            markerfacecolor="none",
            markeredgecolor=GOOD,
            label=f"accepted ({len(accepted)})",
        ),
        Line2D(
            [],
            [],
            linestyle="",
            marker="x",
            markersize=7,
            color=CRITICAL,
            label=f"rejected ({len(rejected)})",
        ),
    ]
    legend = figure.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.07, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
    )
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)
    return _png(figure)


def _box(
    volume: NDArray[Any], bead: BeadResult, half: tuple[int, int, int]
) -> tuple[NDArray[np.float64], tuple[int, int, int], tuple[int, int, int]]:
    """Return the clipped box around a bead, the bead's index in it, and the box start."""
    position = (bead.z_px, bead.y_px, bead.x_px)
    starts = tuple(max(p - h, 0) for p, h in zip(position, half, strict=True))
    stops = tuple(min(p + h + 1, n) for p, h, n in zip(position, half, volume.shape, strict=True))
    box = np.asarray(
        volume[tuple(slice(a, b) for a, b in zip(starts, stops, strict=True))], dtype=np.float64
    )
    centre = (position[0] - starts[0], position[1] - starts[1], position[2] - starts[2])
    return box, (centre[0], centre[1], centre[2]), (starts[0], starts[1], starts[2])


def orthogonal_views(
    volume: NDArray[Any],
    bead: BeadResult,
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
) -> bytes:
    """Return a PNG of the XY, XZ, and YZ slices through a bead, at true aspect ratio."""
    half = half_box(voxel_um, channel.expected_fwhm_um, settings)
    box, (cz, cy, cx), _ = _box(volume, bead, half)
    vz, vy, vx = voxel_um
    planes = (
        ("XY", box[cz], vx, vy, "x (µm)", "y (µm)"),
        ("XZ", box[:, cy, :], vx, vz, "x (µm)", "z (µm)"),
        ("YZ", box[:, :, cx], vy, vz, "y (µm)", "z (µm)"),
    )
    low, high = _contrast(box)
    figure = _figure(9.0, 3.6)
    for index, (name, plane, step_h, step_v, label_h, label_v) in enumerate(planes):
        axes = figure.add_subplot(1, 3, index + 1)
        extent = (0, plane.shape[1] * step_h, plane.shape[0] * step_v, 0)
        axes.imshow(
            plane, cmap="gray", vmin=low, vmax=high, extent=extent, interpolation="nearest"
        )
        axes.set_title(name, color=INK, fontsize=9)
        axes.set_xlabel(label_h, color=INK_SECONDARY, fontsize=8)
        axes.set_ylabel(label_v, color=INK_SECONDARY, fontsize=8)
        axes.tick_params(colors=INK_MUTED, labelsize=7)
    figure.subplots_adjust(left=0.07, right=0.98, bottom=0.16, top=0.9, wspace=0.35)
    return _png(figure)


def _profile(
    box: NDArray[np.float64], axis: int, centre: tuple[int, int, int], width: int
) -> NDArray[np.float64]:
    radius = width // 2
    index: list[slice] = []
    for dim, c in enumerate(centre):
        index.append(
            slice(None)
            if dim == axis
            else slice(max(c - radius, 0), min(c + radius + 1, box.shape[dim]))
        )
    other = tuple(d for d in range(3) if d != axis)
    return np.asarray(box[tuple(index)].mean(axis=other), dtype=np.float64)


def profile_fits(
    volume: NDArray[Any],
    bead: BeadResult,
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
) -> bytes:
    """Return a PNG of the x, y, and z profiles of a bead with fitted and theoretical curves."""
    from matplotlib.lines import Line2D

    half = half_box(voxel_um, channel.expected_fwhm_um, settings)
    box, centre, starts = _box(volume, bead, half)
    theory = channel.theory
    figure = _figure(9.0, 3.3)
    for panel, (axis, name) in enumerate(((2, "x"), (1, "y"), (0, "z"))):
        axes = figure.add_subplot(1, 3, panel + 1)
        values = _profile(box, axis, centre, settings.profile_width_px)
        coordinates = (np.arange(values.size, dtype=np.float64) + starts[axis]) * voxel_um[axis]
        axes.plot(
            coordinates,
            values,
            linestyle="",
            marker="o",
            markersize=4,
            color=INK_SECONDARY,
            zorder=3,
        )
        try:
            fit = fit_gaussian_1d(
                coordinates, values, fwhm_guess_um=channel.expected_fwhm_um[1 if axis == 0 else 0]
            )
        except FitError:
            fit = None
        fine = np.linspace(coordinates[0], coordinates[-1], 300)
        if fit is not None:
            sigma = fit.fwhm_um / FWHM_PER_SIGMA
            axes.plot(
                fine,
                fit.amplitude * np.exp(-0.5 * ((fine - fit.centre_um) / sigma) ** 2) + fit.offset,
                color=SERIES,
                linewidth=2,
                zorder=2,
            )
            if theory is not None:
                reference = theory.axial_um if axis == 0 else theory.lateral_um
                sigma_t = reference / FWHM_PER_SIGMA
                axes.plot(
                    fine,
                    fit.amplitude * np.exp(-0.5 * ((fine - fit.centre_um) / sigma_t) ** 2)
                    + fit.offset,
                    color=INK_MUTED,
                    linestyle="--",
                    linewidth=1.2,
                    zorder=1,
                )
            title = f"{name}: FWHM {fit.fwhm_um * 1000:.0f} nm"
            if theory is not None:
                title += (
                    f" (theory {(theory.axial_um if axis == 0 else theory.lateral_um) * 1000:.0f})"
                )
        else:
            title = f"{name}: fit failed"
        axes.set_title(title, color=INK, fontsize=8.5)
        axes.set_xlabel(f"{name} (µm)", color=INK_SECONDARY, fontsize=8)
        axes.tick_params(colors=INK_MUTED, labelsize=7)
        axes.grid(axis="y", color=GRID, linewidth=0.6)
        for side in ("top", "right"):
            axes.spines[side].set_visible(False)
    handles = [
        Line2D(
            [],
            [],
            linestyle="",
            marker="o",
            markersize=4,
            color=INK_SECONDARY,
            label="measured (3×3 line bundle)",
        ),
        Line2D([], [], color=SERIES, linewidth=2, label="Gaussian fit"),
        Line2D(
            [],
            [],
            color=INK_MUTED,
            linestyle="--",
            linewidth=1.2,
            label="theoretical width, same height",
        ),
    ]
    legend = figure.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.06, 0.0),
        ncol=3,
        frameon=False,
        fontsize=8,
    )
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)
    figure.subplots_adjust(left=0.07, right=0.98, bottom=0.27, top=0.88, wspace=0.3)
    return _png(figure)


def is_finite(value: float | None) -> bool:
    """Whether ``value`` is a finite number."""
    return value is not None and math.isfinite(value)


DIVERGING = ("#1c5cab", "#86b6ef", "#f0efec", "#ef9a9a", "#b52b2b")
"""Blue to red through a neutral gray midpoint (focus below / above the median)."""
SEQUENTIAL = ("#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b")
"""One-hue blue ramp, light to dark (magnitude)."""


def field_map(beads: Sequence[BeadResult], field_size_um: tuple[float, float]) -> bytes | None:
    """Return a PNG of the bead focus offsets and lateral FWHM across the field (MET-11).

    ``None`` if fewer than 3 accepted beads have a position.
    """
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    used = [
        b
        for b in beads
        if b.accepted
        and None not in (b.centre_x_um, b.centre_y_um, b.centre_z_um)
        and b.fwhm_x_um is not None
        and b.fwhm_y_um is not None
    ]
    if len(used) < 3:
        return None
    x = np.array([b.centre_x_um for b in used], dtype=np.float64)
    y = np.array([b.centre_y_um for b in used], dtype=np.float64)
    z = np.array([b.centre_z_um for b in used], dtype=np.float64)
    focus_nm = (z - np.median(z)) * 1000.0
    lateral_nm = np.array([(b.fwhm_x_um + b.fwhm_y_um) / 2 for b in used]) * 1000.0  # type: ignore[operator]
    width, height = field_size_um
    spread = float(max(np.abs(focus_nm).max(), 1.0))
    panels = (
        (
            "Focus relative to the median (nm)",
            focus_nm,
            LinearSegmentedColormap.from_list("diverging", DIVERGING),
            TwoSlopeNorm(vcenter=0.0, vmin=-spread, vmax=spread),
        ),
        (
            "Lateral FWHM, mean of x and y (nm)",
            lateral_nm,
            LinearSegmentedColormap.from_list("sequential", SEQUENTIAL),
            None,
        ),
    )
    figure = _figure(9.0, 3.9)
    for index, (title, values, cmap, norm) in enumerate(panels):
        axes = figure.add_subplot(1, 2, index + 1)
        axes.set_facecolor(SURFACE)
        axes.add_patch(_field_outline(width, height))
        points = axes.scatter(
            x,
            y,
            c=values,
            cmap=cmap,
            norm=norm,
            s=70,
            edgecolors=SURFACE,
            linewidths=1.5,
            zorder=3,
        )
        axes.set_xlim(0, width)
        axes.set_ylim(height, 0)  # image orientation: y down
        axes.set_aspect("equal")
        axes.set_title(title, color=INK, fontsize=9)
        axes.set_xlabel("x (µm)", color=INK_SECONDARY, fontsize=8)
        axes.set_ylabel("y (µm)", color=INK_SECONDARY, fontsize=8)
        axes.tick_params(colors=INK_MUTED, labelsize=7)
        for spine in axes.spines.values():
            spine.set_color(GRID)
        bar = figure.colorbar(points, ax=axes, fraction=0.046, pad=0.03)
        bar.ax.tick_params(colors=INK_MUTED, labelsize=7)
        bar.outline.set_edgecolor(GRID)
    figure.subplots_adjust(left=0.07, right=0.95, bottom=0.14, top=0.9, wspace=0.35)
    return _png(figure)


def _field_outline(width: float, height: float) -> Any:
    from matplotlib.patches import Rectangle

    return Rectangle((0, 0), width, height, fill=False, edgecolor=GRID, linewidth=1, zorder=1)
