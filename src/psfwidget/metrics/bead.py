"""Measure one bead: profiles, plane fits, intensity, and quality checks.

Requirements: DET-04, DET-05, MET-01, MET-02, MET-04 to MET-08, MET-10, MET-15.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from psfwidget.metrics.gaussian import (
    Fit1D,
    Fit2D,
    FitError,
    bead_corrected_fwhm,
    fit_gaussian_1d,
    fit_gaussian_2d,
)
from psfwidget.settings import AnalysisSettings

ACCEPTED = "accepted"
REJECTED = "rejected"


@dataclass(frozen=True)
class BeadResult:
    """Measurements of one bead. Fields are ``None`` when not measured.

    Positions are in the image's coordinates; lengths in micrometres;
    intensities in the image's counts.
    """

    bead_id: int
    z_px: int
    y_px: int
    x_px: int
    status: str = ACCEPTED
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    """Caveats that do not reject the bead, e.g. a clipped measurement box."""
    centre_z_um: float | None = None
    centre_y_um: float | None = None
    centre_x_um: float | None = None
    peak: float | None = None
    background: float | None = None
    background_sd: float | None = None
    integrated_intensity: float | None = None
    snr: float | None = None
    sbr: float | None = None
    saturated: bool | None = None
    fwhm_x_raw_um: float | None = None
    fwhm_y_raw_um: float | None = None
    fwhm_z_raw_um: float | None = None
    fwhm_x_um: float | None = None
    """Bead-size corrected when the diameter is known and correction is on; else raw."""
    fwhm_y_um: float | None = None
    fwhm_z_um: float | None = None
    fwhm_x_se_um: float | None = None
    fwhm_y_se_um: float | None = None
    fwhm_z_se_um: float | None = None
    r2_x: float | None = None
    r2_y: float | None = None
    r2_z: float | None = None
    xy_major_um: float | None = None
    xy_minor_um: float | None = None
    xy_angle_deg: float | None = None
    xy_r2: float | None = None
    xz_major_um: float | None = None
    xz_minor_um: float | None = None
    xz_angle_deg: float | None = None
    xz_r2: float | None = None
    yz_major_um: float | None = None
    yz_minor_um: float | None = None
    yz_angle_deg: float | None = None
    yz_r2: float | None = None
    ellipticity_xy: float | None = None
    ratio_x: float | None = None
    ratio_y: float | None = None
    ratio_z: float | None = None

    @property
    def accepted(self) -> bool:
        """Whether the bead passed every check."""
        return self.status == ACCEPTED

    def reject(self, reason: str) -> BeadResult:
        """Return a copy marked rejected with ``reason`` added."""
        return replace(self, status=REJECTED, reasons=(*self.reasons, reason))

    def to_row(self) -> dict[str, Any]:
        """Return the result as a flat dictionary for tables."""
        row: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            row[item.name] = "; ".join(value) if item.name in ("reasons", "notes") else value
        return row


BEAD_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(BeadResult))


@dataclass(frozen=True)
class Geometry:
    """Fixed inputs for measuring every bead in one channel."""

    voxel_size_um: tuple[float, float, float]
    expected_fwhm_um: tuple[float, float]
    """Expected ``(lateral, axial)`` FWHM of a bead image."""
    half_box_px: tuple[int, int, int]
    saturation_level: float
    bead_diameter_um: float | None
    theory_fwhm_um: tuple[float, float] | None
    """Theoretical ``(lateral, axial)`` FWHM for ratios, or ``None``."""
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)


def half_box(
    voxel_size_um: tuple[float, float, float],
    expected_fwhm_um: tuple[float, float],
    settings: AnalysisSettings,
) -> tuple[int, int, int]:
    """Return the measurement box half-size ``(z, y, x)`` in voxels."""
    lateral, axial = expected_fwhm_um
    return (
        math.ceil(settings.crop_axial_fwhm * axial / voxel_size_um[0]),
        math.ceil(settings.crop_lateral_fwhm * lateral / voxel_size_um[1]),
        math.ceil(settings.crop_lateral_fwhm * lateral / voxel_size_um[2]),
    )


def border_margin(
    voxel_size_um: tuple[float, float, float],
    expected_fwhm_um: tuple[float, float],
    settings: AnalysisSettings,
) -> tuple[int, int, int]:
    """Return the minimum distance ``(z, y, x)`` in voxels from a bead centre to the edge."""
    lateral, axial = expected_fwhm_um
    return (
        math.ceil(settings.min_border_fwhm * axial / voxel_size_um[0]),
        math.ceil(settings.min_border_fwhm * lateral / voxel_size_um[1]),
        math.ceil(settings.min_border_fwhm * lateral / voxel_size_um[2]),
    )


def far_from_border(
    position: tuple[int, int, int], margin: tuple[int, int, int], shape: tuple[int, ...]
) -> bool:
    """Whether ``position`` is at least ``margin`` voxels from every edge (DET-02)."""
    return all(m <= p <= n - 1 - m for p, m, n in zip(position, margin, shape, strict=True))


def _normalised_radius(
    offsets_px: tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]],
    voxel_size_um: tuple[float, float, float],
    expected_fwhm_um: tuple[float, float],
) -> NDArray[np.float64]:
    """Return each box voxel's distance from the bead centre in units of the expected FWHM."""
    lateral, axial = expected_fwhm_um
    axes = [
        offset * v / f
        for offset, v, f in zip(offsets_px, voxel_size_um, (axial, lateral, lateral), strict=True)
    ]
    dz, dy, dx = np.meshgrid(*axes, indexing="ij")
    return np.asarray(np.sqrt(dz**2 + dy**2 + dx**2), dtype=np.float64)


def _profile(
    box: NDArray[np.float64], axis: int, width: int, centre: tuple[int, int, int]
) -> NDArray[np.float64]:
    """Return the line through ``centre`` along ``axis``, averaged over ``width``² lines."""
    radius = width // 2
    index: list[slice] = []
    for dim, c in enumerate(centre):
        if dim == axis:
            index.append(slice(None))
        else:
            index.append(slice(max(c - radius, 0), min(c + radius + 1, box.shape[dim])))
    bundle = box[tuple(index)]
    other = tuple(d for d in range(3) if d != axis)
    return np.asarray(bundle.mean(axis=other), dtype=np.float64)


def measure_bead(volume: NDArray[np.generic], bead: BeadResult, geometry: Geometry) -> BeadResult:
    """Measure a bead whose centre is far enough from the image edge.

    The measurement box is clipped where it would leave the image. Returns the
    bead with its measurements, rejected with a reason if a check fails.
    """
    settings = geometry.settings
    vz, vy, vx = geometry.voxel_size_um
    lateral, axial = geometry.expected_fwhm_um
    position = (bead.z_px, bead.y_px, bead.x_px)
    starts = tuple(max(p - h, 0) for p, h in zip(position, geometry.half_box_px, strict=True))
    stops = tuple(
        min(p + h + 1, n)
        for p, h, n in zip(position, geometry.half_box_px, volume.shape, strict=True)
    )
    box = np.asarray(
        volume[tuple(slice(a, b) for a, b in zip(starts, stops, strict=True))], dtype=np.float64
    )
    centre = (position[0] - starts[0], position[1] - starts[1], position[2] - starts[2])
    offsets = (
        np.arange(starts[0], stops[0]) - position[0],
        np.arange(starts[1], stops[1]) - position[1],
        np.arange(starts[2], stops[2]) - position[2],
    )
    radius = _normalised_radius(offsets, geometry.voxel_size_um, geometry.expected_fwhm_um)
    clipped = [
        name
        for name, start, stop, p, h in zip(
            ("z", "y", "x"), starts, stops, position, geometry.half_box_px, strict=True
        )
        if start > p - h or stop < p + h + 1
    ]
    if clipped:
        bead = replace(
            bead,
            notes=(
                *bead.notes,
                f"measurement box clipped by the image edge along {', '.join(clipped)}",
            ),
        )

    outside = box[radius > settings.integration_radius_fwhm]
    if outside.size < settings.min_background_voxels:
        return bead.reject("too few background voxels in the measurement box")
    background = float(outside.mean())
    background_sd = float(outside.std(ddof=1))
    core = box[radius <= 0.5]
    peak = float(core.max())
    inside = box[radius <= settings.integration_radius_fwhm]
    integrated: float | None = float(inside.sum() - background * inside.size)
    reach = [
        settings.integration_radius_fwhm * f / v
        for f, v in zip((axial, lateral, lateral), (vz, vy, vx), strict=True)
    ]
    if any(
        p - r < start or p + r > stop - 1
        for p, r, start, stop in zip(position, reach, starts, stops, strict=True)
    ):
        integrated = None  # the summed ellipsoid would extend past the image edge
        bead = replace(
            bead,
            notes=(*bead.notes, "integrated intensity not measured: too close to the image edge"),
        )
    signal = peak - background
    snr = signal / background_sd if background_sd > 0 else math.inf
    sbr = peak / background if background > 0 else None
    saturated = bool(np.any(box[radius <= 1.0] >= geometry.saturation_level))
    bead = replace(
        bead,
        peak=peak,
        background=background,
        background_sd=background_sd,
        integrated_intensity=integrated,
        snr=snr,
        sbr=sbr,
        saturated=saturated,
    )
    if saturated:
        bead = bead.reject("saturated")
    if snr < settings.min_snr:
        bead = bead.reject(f"SNR {snr:.1f} below {settings.min_snr:g}")
    if settings.min_sbr is not None and (sbr is None or sbr < settings.min_sbr):
        bead = bead.reject(f"SBR below {settings.min_sbr:g}")
    if not bead.accepted and not saturated:
        return bead  # too faint to measure; usually noise, and fitting it costs the most time

    coordinates = [
        np.arange(start, stop) * v
        for start, stop, v in zip(starts, stops, (vz, vy, vx), strict=True)
    ]
    cz, cy, cx = centre
    try:
        fits_1d = [
            fit_gaussian_1d(
                coordinates[axis],
                _profile(box, axis, settings.profile_width_px, centre),
                fwhm_guess_um=axial if axis == 0 else lateral,
            )
            for axis in (2, 1, 0)
        ]
        fit_xy = fit_gaussian_2d(
            coordinates[2], coordinates[1], box[cz].T, fwhm_guess_um=(lateral, lateral)
        )
        fit_xz = fit_gaussian_2d(
            coordinates[2], coordinates[0], box[:, cy, :].T, fwhm_guess_um=(lateral, axial)
        )
        fit_yz = fit_gaussian_2d(
            coordinates[1], coordinates[0], box[:, :, cx].T, fwhm_guess_um=(lateral, axial)
        )
    except FitError as exc:
        return bead.reject(f"fit failed: {exc}")
    return _with_fits(bead, fits_1d, (fit_xy, fit_xz, fit_yz), geometry)


def _with_fits(
    bead: BeadResult,
    fits_1d: list[Fit1D],
    planes: tuple[Fit2D, Fit2D, Fit2D],
    geometry: Geometry,
) -> BeadResult:
    fx, fy, fz = fits_1d
    fit_xy, fit_xz, fit_yz = planes
    correct = geometry.settings.bead_size_correction
    diameter = geometry.bead_diameter_um if correct else None
    corrected = [bead_corrected_fwhm(f.fwhm_um, diameter) for f in fits_1d]
    ratios: list[float | None] = [None, None, None]
    if geometry.theory_fwhm_um is not None:
        lateral, axial = geometry.theory_fwhm_um
        ratios = [c / t for c, t in zip(corrected, (lateral, lateral, axial), strict=True)]
    bead = replace(
        bead,
        centre_x_um=fx.centre_um,
        centre_y_um=fy.centre_um,
        centre_z_um=fz.centre_um,
        fwhm_x_raw_um=fx.fwhm_um,
        fwhm_y_raw_um=fy.fwhm_um,
        fwhm_z_raw_um=fz.fwhm_um,
        fwhm_x_um=corrected[0],
        fwhm_y_um=corrected[1],
        fwhm_z_um=corrected[2],
        fwhm_x_se_um=fx.fwhm_se_um,
        fwhm_y_se_um=fy.fwhm_se_um,
        fwhm_z_se_um=fz.fwhm_se_um,
        r2_x=fx.r_squared,
        r2_y=fy.r_squared,
        r2_z=fz.r_squared,
        xy_major_um=fit_xy.fwhm_major_um,
        xy_minor_um=fit_xy.fwhm_minor_um,
        xy_angle_deg=fit_xy.angle_deg,
        xy_r2=fit_xy.r_squared,
        xz_major_um=fit_xz.fwhm_major_um,
        xz_minor_um=fit_xz.fwhm_minor_um,
        xz_angle_deg=fit_xz.angle_deg,
        xz_r2=fit_xz.r_squared,
        yz_major_um=fit_yz.fwhm_major_um,
        yz_minor_um=fit_yz.fwhm_minor_um,
        yz_angle_deg=fit_yz.angle_deg,
        yz_r2=fit_yz.r_squared,
        ellipticity_xy=fit_xy.ellipticity,
        ratio_x=ratios[0],
        ratio_y=ratios[1],
        ratio_z=ratios[2],
    )
    worst = min(fx.r_squared, fy.r_squared, fz.r_squared)
    if worst < geometry.settings.min_r_squared:
        bead = bead.reject(f"poor fit (R² {worst:.2f} below {geometry.settings.min_r_squared:g})")
    if any(math.isnan(c) for c in corrected):
        bead = bead.reject("measured width not larger than the bead")
    return bead
