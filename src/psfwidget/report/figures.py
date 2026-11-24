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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class AverageImage:
    """The averaged PSF shown in the report figures, and how it was made."""

    data: NDArray[np.float64]
    count: int
    """Number of beads averaged."""
    planes: int
    """Number of z-planes the average covers."""
    full_planes: int
    """Number of z-planes of the full box (larger than ``planes`` when truncated)."""

    @property
    def truncated(self) -> bool:
        """Whether the stack was too short for the full box (an undersampled volume)."""
        return self.planes < self.full_planes

    @property
    def label(self) -> str:
        """A short description for captions and legends."""
        text = f"{self.count} beads"
        if self.truncated:
            text += (
                f"; undersampled volume: z truncated to {self.planes} of {self.full_planes} planes"
            )
        return text


def average_image(
    volume: NDArray[Any],
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
) -> AverageImage | None:
    """Return the averaged PSF of the accepted beads for the figures, or ``None``.

    When the stack is too short for the full box, the average is made over a
    shorter z range instead and marked as truncated.
    """
    from psfwidget.analysis import averaged_psf, truncated_averaged_psf
    from psfwidget.average import AverageError

    full = 2 * half_box(voxel_um, channel.expected_fwhm_um, settings)[0] + 1
    for method in (averaged_psf, truncated_averaged_psf):
        try:
            average, used, half = method(
                volume, channel.beads, voxel_um, channel.expected_fwhm_um, settings
            )
        except AverageError:
            continue
        return AverageImage(np.asarray(average, dtype=np.float64), used, 2 * half[0] + 1, full)
    return None


BACKGROUND_PERCENTILE = 5.0
"""Percentile of the bead box taken as background in the image comparison."""


def _peak_one(image: NDArray[np.float64]) -> NDArray[np.float64]:
    peak = float(image.max())
    return image / peak if peak > 0 else image


def orthogonal_comparison(
    volume: NDArray[Any],
    bead: BeadResult,
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
    bead_diameter_um: float | None = None,
) -> tuple[bytes, AverageImage | None]:
    """Return a PNG of XY, XZ, and YZ slices of the closest bead, the average PSF, and the model.

    One row per image, one column per plane, all at the same physical scale
    and true aspect ratio. Each image is scaled to its own peak (the bead after
    subtracting its background), on one grey scale from 0 to 1, so the shapes
    compare directly. Rows that cannot be made (too few beads to average, no
    theory) are left out. An average from a stack too short for the full box
    covers fewer z-planes; the planes it lacks are hatched and its row says so.
    Also returns the average shown, or ``None`` when there is none.
    """
    half = half_box(voxel_um, channel.expected_fwhm_um, settings)
    box, centre, _ = _box(volume, bead, half)
    background = float(np.percentile(box, BACKGROUND_PERCENTILE))
    rows: list[tuple[str, NDArray[np.float64], tuple[int, int, int]]] = [
        (f"Bead {bead.bead_id}", _peak_one(np.clip(box - background, 0, None)), centre)
    ]
    average = average_image(volume, channel, voxel_um, settings)
    if average is not None:
        image = _peak_one(np.clip(average.data, 0, None))
        label = f"Average ({average.count} beads)"
        if average.truncated:
            label += "\nundersampled, z truncated"
        rows.append(
            (label, image, (image.shape[0] // 2, image.shape[1] // 2, image.shape[2] // 2))
        )
    if channel.theory is not None:
        from psfwidget.theory.psf3d import theoretical_psf

        shape = (2 * half[0] + 1, 2 * half[1] + 1, 2 * half[2] + 1)
        model = _peak_one(
            theoretical_psf(channel.theory, voxel_um, shape, bead_diameter_um=bead_diameter_um)
        )
        rows.append(("Model", model, (half[0], half[1], half[2])))

    vz, vy, vx = voxel_um
    steps = (vz, vy, vx)
    limits = tuple(h * s for h, s in zip(half, steps, strict=True))  # half-widths z, y, x (µm)
    names = ("z", "y", "x")
    # z runs horizontally in XZ and YZ, so every panel in a row has the same
    # height in µm and the whole grid shares one physical scale.
    columns = (("XY", 2, 1), ("XZ", 0, 2), ("YZ", 0, 1))  # name, horizontal, vertical axis
    widths = [2 * limits[h] + steps[h] for _, h, _ in columns]
    row_height_um = max(2 * limits[v] + steps[v] for _, _, v in columns)
    inch_per_um = 6.6 / sum(widths)
    row_inches = row_height_um * inch_per_um + 0.75
    figure = _figure(9.0, row_inches * len(rows) + 0.3)
    grid = figure.add_gridspec(
        len(rows),
        3,
        width_ratios=widths,
        left=0.1,
        right=0.98,
        wspace=0.4,
        hspace=0.6,
        bottom=0.55 / figure.get_figheight(),
        top=1 - 0.3 / figure.get_figheight(),
    )
    for row, (label, image, centre) in enumerate(rows):
        cz, cy, cx = centre
        planes = (image[cz], image[:, cy, :].T, image[:, :, cx].T)
        for column, ((name, axis_h, axis_v), plane) in enumerate(
            zip(columns, planes, strict=True)
        ):
            axes = figure.add_subplot(grid[row, column])
            centre_h, centre_v = centre[axis_h], centre[axis_v]
            step_h, step_v = steps[axis_h], steps[axis_v]
            extent = (
                (-0.5 - centre_h) * step_h,
                (plane.shape[1] - 0.5 - centre_h) * step_h,
                (plane.shape[0] - 0.5 - centre_v) * step_v,
                (-0.5 - centre_v) * step_v,
            )
            # Hatch the part of the window outside the stack (a bead near its edge).
            axes.set_facecolor(SURFACE)
            axes.patch.set_hatch("////")
            axes.patch.set_edgecolor(INK_MUTED)
            axes.imshow(
                plane, cmap="gray", vmin=0.0, vmax=1.0, extent=extent, interpolation="nearest"
            )
            limit_h, limit_v = limits[axis_h], limits[axis_v]
            axes.set_xlim(-limit_h - step_h / 2, limit_h + step_h / 2)
            axes.set_ylim(limit_v + step_v / 2, -limit_v - step_v / 2)
            axes.set_aspect("equal", anchor="W")
            if row == 0:
                axes.set_title(name, color=INK, fontsize=9, loc="left")
            axes.set_xlabel(f"{names[axis_h]} (µm)", color=INK_SECONDARY, fontsize=8)
            ylabel = f"{names[axis_v]} (µm)"
            axes.set_ylabel(
                f"{label}\n{ylabel}" if column == 0 else ylabel, color=INK_SECONDARY, fontsize=8
            )
            axes.tick_params(colors=INK_MUTED, labelsize=7)
    return _png(figure), average


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


AVERAGE = "#eb6834"
"""Second categorical slot (orange) for the averaged PSF."""
MODEL_SAMPLES_PER_VOXEL = 4


def _centre_profile(psf: NDArray[np.float64], axis: int) -> NDArray[np.float64]:
    centre = tuple(n // 2 for n in psf.shape)
    index = [slice(c, c + 1) for c in centre]
    index[axis] = slice(None)
    return np.asarray(psf[tuple(index)].ravel(), dtype=np.float64)


def _model_profiles(
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    half: tuple[int, int, int],
    bead_diameter_um: float | None,
) -> list[tuple[NDArray[np.float64], NDArray[np.float64]]] | None:
    """Return (offset µm, value) of the theoretical bead image along z, y, x, peak 1."""
    if channel.theory is None:
        return None
    from psfwidget.theory.psf3d import theoretical_psf

    fine = tuple(v / MODEL_SAMPLES_PER_VOXEL for v in voxel_um)
    shape = tuple(2 * h * MODEL_SAMPLES_PER_VOXEL + 1 for h in half)
    model = theoretical_psf(
        channel.theory,
        (fine[0], fine[1], fine[2]),
        (shape[0], shape[1], shape[2]),
        bead_diameter_um=bead_diameter_um,
        oversample=1,
    )
    model = model / model.max()
    return [
        (
            np.asarray((np.arange(n) - n // 2) * step, dtype=np.float64),
            _centre_profile(model, axis),
        )
        for axis, (n, step) in enumerate(zip(shape, fine, strict=True))
    ]


def _average_profiles(
    volume: NDArray[Any],
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
) -> tuple[list[tuple[NDArray[np.float64], NDArray[np.float64]]], AverageImage] | None:
    """Return (offset µm, value) of the averaged PSF along z, y, x (peak 1), and the average."""
    result = average_image(volume, channel, voxel_um, settings)
    if result is None:
        return None
    average = result.data / result.data.max()
    return [
        (
            np.asarray((np.arange(n) - n // 2) * step, dtype=np.float64),
            _centre_profile(average, axis),
        )
        for axis, (n, step) in enumerate(zip(average.shape, voxel_um, strict=True))
    ], result


def profile_fits(
    volume: NDArray[Any],
    bead: BeadResult,
    channel: ChannelResult,
    voxel_um: tuple[float, float, float],
    settings: AnalysisSettings,
    bead_diameter_um: float | None = None,
) -> bytes:
    """Return a PNG of the x, y, and z profiles: model, closest single bead, and average PSF.

    Every curve is centred on its own peak and scaled to a peak of 1, so their
    widths compare directly:

    1. **Model:** the theoretical PSF convolved with the bead (dashed).
    2. **Closest single bead:** the measured profile (3×3 line bundle) of the
       bead nearest the median lateral FWHM, with its Gaussian fit.
    3. **Average PSF across the field of view:** the registered average of all
       accepted beads (MET-14).
    """
    from matplotlib.lines import Line2D

    from psfwidget.theory.optics import fwhm_around_peak

    half = half_box(voxel_um, channel.expected_fwhm_um, settings)
    box, centre, starts = _box(volume, bead, half)
    model = _model_profiles(channel, voxel_um, half, bead_diameter_um)
    averaged = _average_profiles(volume, channel, voxel_um, settings)
    figure = _figure(9.0, 3.5)
    for panel, (axis, name) in enumerate(((2, "x"), (1, "y"), (0, "z"))):
        axes = figure.add_subplot(1, 3, panel + 1)
        values = _profile(box, axis, centre, settings.profile_width_px)
        coordinates = (np.arange(values.size, dtype=np.float64) + starts[axis]) * voxel_um[axis]
        widths: list[str] = []
        try:
            fit = fit_gaussian_1d(
                coordinates, values, fwhm_guess_um=channel.expected_fwhm_um[1 if axis == 0 else 0]
            )
        except FitError:
            fit = None
        if fit is not None:
            offsets = coordinates - fit.centre_um
            axes.plot(
                offsets,
                (values - fit.offset) / fit.amplitude,
                linestyle="",
                marker="o",
                markersize=4.5,
                color=SERIES,
                markeredgecolor=SURFACE,
                markeredgewidth=0.8,
                zorder=4,
            )
            fine = np.linspace(offsets[0], offsets[-1], 300)
            sigma = fit.fwhm_um / FWHM_PER_SIGMA
            axes.plot(
                fine, np.exp(-0.5 * (fine / sigma) ** 2), color=SERIES, linewidth=2, zorder=3
            )
            widths.append(f"bead {fit.fwhm_um * 1000:.0f}")
        if averaged is not None:
            offsets, profile = averaged[0][axis]
            axes.plot(
                offsets, profile, color=AVERAGE, linewidth=2, marker="s", markersize=3, zorder=2
            )
            try:
                widths.append(f"avg {fwhm_around_peak(offsets, profile) * 1000:.0f}")
            except ValueError:  # a truncated z range may not reach half maximum
                widths.append("avg n/a")
        if model is not None:
            offsets, profile = model[axis]
            axes.plot(offsets, profile, color=INK_MUTED, linestyle="--", linewidth=1.4, zorder=1)
            widths.append(f"model {fwhm_around_peak(offsets, profile) * 1000:.0f}")
        axes.set_title(
            f"{name}: FWHM " + " · ".join(widths) + " nm" if widths else f"{name}: no fit",
            color=INK,
            fontsize=8.5,
        )
        axes.set_xlabel(f"{name} offset from the peak (µm)", color=INK_SECONDARY, fontsize=8)
        axes.set_ylim(-0.15, 1.15)
        axes.tick_params(colors=INK_MUTED, labelsize=7)
        axes.grid(axis="y", color=GRID, linewidth=0.6)
        for side in ("top", "right"):
            axes.spines[side].set_visible(False)
    handles = [
        Line2D(
            [],
            [],
            color=INK_MUTED,
            linestyle="--",
            linewidth=1.4,
            label="model (theory with the bead size)",
        ),
        Line2D(
            [],
            [],
            color=SERIES,
            linewidth=2,
            marker="o",
            markersize=4.5,
            label=f"closest single bead ({bead.bead_id}): data and Gaussian fit",
        ),
    ]
    if averaged is not None:
        handles.append(
            Line2D(
                [],
                [],
                color=AVERAGE,
                linewidth=2,
                marker="s",
                markersize=3,
                label=(
                    f"average PSF across the field ({averaged[1].count} beads)"
                    + (
                        f"\nundersampled volume: z truncated to {averaged[1].planes} of "
                        f"{averaged[1].full_planes} planes"
                        if averaged[1].truncated
                        else ""
                    )
                ),
            )
        )
    legend = figure.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.06, 0.0),
        ncol=3,
        frameon=False,
        fontsize=7.5,
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
SEQUENTIAL_SECOND = ("#fbdccd", "#f5a882", "#eb6834", "#b84a20", "#7a2e12")
"""One-hue orange ramp for a second magnitude shown beside the first."""


def field_map(beads: Sequence[BeadResult], field_size_um: tuple[float, float]) -> bytes | None:
    """Return a PNG of bead focus offsets, lateral FWHM, and axial FWHM across the field (MET-11).

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
        and b.fwhm_z_um is not None
    ]
    if len(used) < 3:
        return None
    x = np.array([b.centre_x_um for b in used], dtype=np.float64)
    y = np.array([b.centre_y_um for b in used], dtype=np.float64)
    z = np.array([b.centre_z_um for b in used], dtype=np.float64)
    focus_nm = (z - np.median(z)) * 1000.0
    lateral_nm = np.array([(b.fwhm_x_um + b.fwhm_y_um) / 2 for b in used]) * 1000.0  # type: ignore[operator]
    axial_nm = np.array([b.fwhm_z_um for b in used], dtype=np.float64) * 1000.0
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
        (
            "Axial FWHM (nm)",
            axial_nm,
            LinearSegmentedColormap.from_list("sequential-second", SEQUENTIAL_SECOND),
            None,
        ),
    )
    figure = _figure(12.0, 3.9)
    for index, (title, values, cmap, norm) in enumerate(panels):
        axes = figure.add_subplot(1, 3, index + 1)
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
    figure.subplots_adjust(left=0.05, right=0.97, bottom=0.14, top=0.9, wspace=0.4)
    return _png(figure)


def _field_outline(width: float, height: float) -> Any:
    from matplotlib.patches import Rectangle

    return Rectangle((0, 0), width, height, fill=False, edgecolor=GRID, linewidth=1, zorder=1)
