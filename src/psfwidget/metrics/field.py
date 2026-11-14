"""Field-dependent metrics (MET-11, MET-12, MET-13).

Tilt, field curvature, illumination uniformity, and chromatic registration.

All of them use the sub-voxel bead centres and integrated intensities of the
accepted beads; positions are in µm in the stack's coordinates.

* **Tilt** (MET-11): a plane z = a + b·x + c·y fitted to the bead focus
  positions. Reported as the angle of the plane and the focus range it
  causes across the field. A tilted slide or stage gives a plane.
* **Field curvature** (MET-11): the r² term of z = a + b·x + c·y + k·r², with
  r the distance from the field centre, reported as the sag k·r² at the
  field corner relative to the centre.
* **Illumination uniformity** (MET-12): I = a + b·r² fitted to the bead
  integrated intensities, reported as the corner-to-centre ratio a + b·r²
  over a. Bead-to-bead brightness varies, so many beads spread over the
  field are needed for a meaningful value.
* **Chromatic registration** (MET-13): beads seen in two channels (e.g.
  TetraSpeck) are matched by nearest lateral position, and the median
  shift of each channel relative to the first analysed channel is reported
  in nm, laterally and axially.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from psfwidget.metrics.bead import BeadResult

MIN_BEADS_PLANE = 3
MIN_BEADS_CURVATURE = 6
MIN_BEADS_UNIFORMITY = 6
MIN_SPREAD_FRACTION = 0.25
"""Beads must span at least this fraction of the field in x and in y for a fit."""
MATCH_RADIUS_FWHM = 2.0
"""Beads are matched across channels within this many lateral FWHM."""
NM_PER_UM = 1000.0

CHANNEL_METRICS: tuple[str, ...] = (
    "tilt_deg",
    "focal_range_um",
    "curvature_sag_um",
    "uniformity_corner_ratio",
    "chromatic_lateral_nm",
    "chromatic_axial_nm",
    "strehl",
    "psf_similarity",
    "axial_skewness",
    "axial_asymmetry",
    "ratio_lateral_ism",
    "ratio_axial_ism",
)
"""Channel-level metrics, tracked like the bead summaries (one value per session)."""


@dataclass(frozen=True)
class FieldGeometry:
    """Tilt, curvature, and uniformity of one channel's field."""

    field_size_um: tuple[float, float]
    """Field width (x) and height (y)."""
    n_beads: int
    tilt_deg: float | None = None
    tilt_direction_deg: float | None = None
    """Direction of steepest rise of the focus, from +x towards +y."""
    focal_range_um: float | None = None
    """Range of the fitted focus plane across the field."""
    curvature_sag_um: float | None = None
    """Focus at the field corner minus at the centre, from the r² term."""
    uniformity_corner_ratio: float | None = None
    """Fitted bead intensity at the field corner divided by that at the centre."""
    notes: tuple[str, ...] = ()

    def metrics(self) -> dict[str, float]:
        """Return the channel-level metrics that could be computed."""
        values = {
            "tilt_deg": self.tilt_deg,
            "focal_range_um": self.focal_range_um,
            "curvature_sag_um": self.curvature_sag_um,
            "uniformity_corner_ratio": self.uniformity_corner_ratio,
        }
        return {k: v for k, v in values.items() if v is not None}


@dataclass(frozen=True)
class ChromaticShift:
    """Median position of one channel's beads relative to the reference channel."""

    channel: int
    reference: int
    n_pairs: int
    dx_nm: float
    dy_nm: float
    dz_nm: float

    @property
    def lateral_nm(self) -> float:
        """Lateral shift magnitude."""
        return math.hypot(self.dx_nm, self.dy_nm)


def _positions(beads: Sequence[BeadResult]) -> np.ndarray:
    rows = [
        (b.centre_x_um, b.centre_y_um, b.centre_z_um, b.integrated_intensity)
        for b in beads
        if b.accepted and None not in (b.centre_x_um, b.centre_y_um, b.centre_z_um)
    ]
    return np.asarray(rows, dtype=np.float64).reshape(-1, 4)


def _spread_ok(x: np.ndarray, y: np.ndarray, size: tuple[float, float]) -> bool:
    return bool(
        np.ptp(x) >= MIN_SPREAD_FRACTION * size[0] and np.ptp(y) >= MIN_SPREAD_FRACTION * size[1]
    )


def field_geometry(
    beads: Sequence[BeadResult], field_size_um: tuple[float, float]
) -> FieldGeometry:
    """Fit tilt, curvature, and uniformity to the accepted beads of one channel."""
    data = _positions(beads)
    notes: list[str] = []
    count = len(data)
    width, height = field_size_um
    if count < MIN_BEADS_PLANE:
        return FieldGeometry(
            field_size_um,
            count,
            notes=(f"tilt, curvature, and uniformity need at least {MIN_BEADS_PLANE} beads",),
        )
    x = data[:, 0] - width / 2
    y = data[:, 1] - height / 2
    z, intensity = data[:, 2], data[:, 3]
    if not _spread_ok(x, y, field_size_um):
        return FieldGeometry(
            field_size_um,
            count,
            notes=("beads cover too little of the field to fit tilt or curvature",),
        )
    corner = math.hypot(width / 2, height / 2)
    plane = np.column_stack([np.ones(count), x, y])
    (_offset, slope_x, slope_y), *_ = np.linalg.lstsq(plane, z, rcond=None)
    slope = math.hypot(slope_x, slope_y)
    tilt = math.degrees(math.atan(slope))
    direction = math.degrees(math.atan2(slope_y, slope_x)) % 360
    focal_range = abs(slope_x) * width + abs(slope_y) * height

    sag = None
    if count >= MIN_BEADS_CURVATURE:
        r2 = x**2 + y**2
        design = np.column_stack([np.ones(count), x, y, r2])
        coefficients, *_ = np.linalg.lstsq(design, z, rcond=None)
        sag = float(coefficients[3] * corner**2)
        # With enough beads, take the tilt from the model that includes curvature,
        # so an off-centre bead distribution does not turn curvature into tilt.
        slope_x, slope_y = float(coefficients[1]), float(coefficients[2])
        slope = math.hypot(slope_x, slope_y)
        tilt = math.degrees(math.atan(slope))
        direction = math.degrees(math.atan2(slope_y, slope_x)) % 360
        focal_range = abs(slope_x) * width + abs(slope_y) * height
    else:
        notes.append(f"field curvature needs at least {MIN_BEADS_CURVATURE} beads")

    ratio = None
    finite = np.isfinite(intensity)
    if finite.sum() >= MIN_BEADS_UNIFORMITY:
        r2 = (x**2 + y**2)[finite]
        design = np.column_stack([np.ones(r2.size), r2])
        (centre, change), *_ = np.linalg.lstsq(design, intensity[finite], rcond=None)
        if centre > 0:
            ratio = float((centre + change * corner**2) / centre)
    else:
        notes.append(
            f"illumination uniformity needs at least {MIN_BEADS_UNIFORMITY} beads with an "
            f"integrated intensity ({int(finite.sum())} here; beads too close to the stack's "
            "edges are not integrated)"
        )
    return FieldGeometry(
        field_size_um=field_size_um,
        n_beads=count,
        tilt_deg=tilt,
        tilt_direction_deg=direction,
        focal_range_um=float(focal_range),
        curvature_sag_um=sag,
        uniformity_corner_ratio=ratio,
        notes=tuple(notes),
    )


def chromatic_shift(
    reference: Sequence[BeadResult],
    other: Sequence[BeadResult],
    *,
    reference_index: int,
    other_index: int,
    match_radius_um: float,
) -> ChromaticShift | None:
    """Match beads between two channels and return the median shift, or ``None``.

    Each reference bead is paired with the nearest bead of the other channel
    within ``match_radius_um`` laterally; pairs that are not mutual nearest
    neighbours are dropped.
    """
    first, second = _positions(reference), _positions(other)
    if not len(first) or not len(second):
        return None
    distance = np.hypot(
        first[:, None, 0] - second[None, :, 0], first[:, None, 1] - second[None, :, 1]
    )
    forward = distance.argmin(axis=1)
    backward = distance.argmin(axis=0)
    pairs = [
        (i, j)
        for i, j in enumerate(forward)
        if backward[j] == i and distance[i, j] <= match_radius_um
    ]
    if not pairs:
        return None
    shifts = np.asarray([second[j, :3] - first[i, :3] for i, j in pairs]) * NM_PER_UM
    dx, dy, dz = (float(v) for v in np.median(shifts, axis=0))
    return ChromaticShift(other_index, reference_index, len(pairs), dx, dy, dz)
