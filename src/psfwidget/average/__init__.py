"""Averaged empirical PSF, its OME-TIFF export, and comparison with theory.

Requirements: MET-14 (averaged PSF and export), THE-04 (Strehl ratio
estimate), THE-05 (similarity score), REP-04 (OME-TIFF export).

Every accepted bead is cut out of the stack on a box centred on the voxel
nearest its fitted centre, shifted by the remaining sub-voxel offset (cubic
spline interpolation) so all beads share one centre, background-subtracted
(median of the box's outer shell), and normalised to a sum of 1. The mean of
these boxes is the averaged PSF, also normalised to a sum of 1.

The theoretical bead image of :mod:`psfwidget.theory.psf3d` on the same grid
(same voxels, bead size, and sum) is the reference:

* **Strehl ratio estimate**: peak of the averaged PSF divided by the peak of
  the theoretical one. Aberrations spread the energy, so values below 1 mean
  a degraded PSF. Energy outside the box and imperfect registration also
  lower it, so the value is an estimate, best tracked over time.
* **Similarity**: the Pearson correlation of the two images over the box.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from psfwidget.metrics.bead import BeadResult

MIN_BEADS = 3
AXES = ("z", "y", "x")


class AverageError(ValueError):
    """Raised when an averaged PSF cannot be made."""


@dataclass(frozen=True)
class PSFComparison:
    """The averaged PSF of one channel compared with theory (THE-04, THE-05)."""

    n_beads: int
    strehl: float
    similarity: float
    fwhm_um: tuple[float, float, float]
    """FWHM ``(z, y, x)`` of the averaged PSF through its centre."""
    axial_skewness: float = 0.0
    """Skewness of the axial energy profile (MET-09); 0 for a symmetric PSF."""
    axial_asymmetry: float = 1.0
    """Energy above focus (higher z) divided by energy below (MET-09); 1 when symmetric."""


def box_shape(half: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return the odd box shape for a half-size in voxels."""
    return (2 * half[0] + 1, 2 * half[1] + 1, 2 * half[2] + 1)


def _shell(box: NDArray[np.float64]) -> NDArray[np.float64]:
    mask = np.ones(box.shape, dtype=bool)
    mask[1:-1, 1:-1, 1:-1] = False
    return box[mask]


def average_beads(
    volume: NDArray[Any],
    beads: list[BeadResult] | tuple[BeadResult, ...],
    voxel_size_um: tuple[float, float, float],
    half: tuple[int, int, int],
) -> tuple[NDArray[np.float64], int]:
    """Register and average the accepted beads; return the PSF (sum 1) and the bead count.

    Beads whose box would extend past the stack are skipped.

    Raises
    ------
    AverageError
        If fewer than :data:`MIN_BEADS` beads can be used.
    """
    shape = box_shape(half)
    total = np.zeros(shape, dtype=np.float64)
    used = clipped = 0
    for bead in beads:
        if not bead.accepted or None in (bead.centre_z_um, bead.centre_y_um, bead.centre_x_um):
            continue
        centre = (
            bead.centre_z_um / voxel_size_um[0],  # type: ignore[operator]
            bead.centre_y_um / voxel_size_um[1],  # type: ignore[operator]
            bead.centre_x_um / voxel_size_um[2],  # type: ignore[operator]
        )
        nearest = tuple(round(c) for c in centre)
        start = [p - h - 1 for p, h in zip(nearest, half, strict=True)]
        stop = [p + h + 2 for p, h in zip(nearest, half, strict=True)]
        if any(a < 0 or b > n for a, b, n in zip(start, stop, volume.shape, strict=True)):
            clipped += 1
            continue
        padded = np.asarray(
            volume[tuple(slice(a, b) for a, b in zip(start, stop, strict=True))],
            dtype=np.float64,
        )
        offset = [n - c for n, c in zip(nearest, centre, strict=True)]
        shifted = ndimage.shift(padded, offset, order=3, mode="nearest")[1:-1, 1:-1, 1:-1]
        shifted -= float(np.median(_shell(shifted)))
        energy = shifted.sum()
        if energy <= 0:
            continue
        total += shifted / energy
        used += 1
    if used < MIN_BEADS:
        reason = (
            f"; {clipped} bead(s) too close to the stack's edges for a "
            f"{shape[0]}×{shape[1]}×{shape[2]}-voxel box (stack {volume.shape[0]} planes)"
            if clipped
            else ""
        )
        raise AverageError(
            f"an averaged PSF needs at least {MIN_BEADS} beads; {used} usable{reason}"
        )
    return total / total.sum(), used


def _fwhm(profile: NDArray[np.float64], step: float) -> float:
    """FWHM of a centred, single-peaked profile by linear interpolation."""
    centre = profile.size // 2
    peak = profile[centre]
    half = peak / 2.0
    widths = []
    for direction in (1, -1):
        index = centre
        while 0 < index < profile.size - 1 and profile[index + direction] > half:
            index += direction
        nxt = index + direction
        if not 0 <= nxt < profile.size:
            return math.nan
        a, b = profile[index], profile[nxt]
        widths.append((abs(index - centre) + (a - half) / (a - b)) * step)
    return float(sum(widths))


def as_measured(theory: NDArray[np.float64]) -> NDArray[np.float64]:
    """Treat a theoretical box like a measured one: subtract its shell median, sum 1.

    The averaged beads lose whatever PSF tail reaches the edge of the box to
    the background subtraction, so the theoretical image must too, or the
    Strehl estimate would depend on the box size.
    """
    corrected = theory - float(np.median(_shell(theory)))
    return np.asarray(corrected / corrected.sum(), dtype=np.float64)


def compare(
    average: NDArray[np.float64],
    theory: NDArray[np.float64],
    n_beads: int,
    voxel_size_um: tuple[float, float, float],
) -> PSFComparison:
    """Compare an averaged PSF with the theoretical one on the same grid.

    ``theory`` is passed through :func:`as_measured` first.
    """
    theory = as_measured(theory)
    centre = tuple(n // 2 for n in average.shape)
    strehl = float(average[centre] / theory[centre])
    similarity = float(np.corrcoef(average.ravel(), theory.ravel())[0, 1])
    profiles = (
        average[:, centre[1], centre[2]],
        average[centre[0], :, centre[2]],
        average[centre[0], centre[1], :],
    )
    fwhm = tuple(_fwhm(p, s) for p, s in zip(profiles, voxel_size_um, strict=True))
    skewness, asymmetry = axial_asymmetry(average)
    return PSFComparison(
        n_beads, strehl, similarity, (fwhm[0], fwhm[1], fwhm[2]), skewness, asymmetry
    )


def axial_asymmetry(average: NDArray[np.float64]) -> tuple[float, float]:
    """Return the skewness of the axial energy profile and the above/below energy ratio.

    The profile is the energy in each z plane (negative values from noise
    after background subtraction are set to 0). Spherical aberration, e.g. a
    refractive-index mismatch, makes it asymmetric (MET-09). "Above" is higher
    z in the stack's order.
    """
    profile = np.clip(average.sum(axis=(1, 2)), 0.0, None)
    centre = profile.size // 2
    z = np.arange(profile.size, dtype=np.float64) - centre
    weights = profile / profile.sum()
    mean = float((weights * z).sum())
    variance = float((weights * (z - mean) ** 2).sum())
    skewness = float((weights * (z - mean) ** 3).sum() / variance**1.5) if variance > 0 else 0.0
    above, below = float(profile[centre + 1 :].sum()), float(profile[:centre].sum())
    return skewness, above / below if below > 0 else math.inf


def write_ome_tiff(
    average: NDArray[np.float64],
    path: str | Path,
    *,
    voxel_size_um: tuple[float, float, float],
    name: str,
    details: dict[str, Any],
) -> list[Path]:
    """Write an averaged PSF as a 32-bit float OME-TIFF with a JSON sidecar (MET-14, REP-04).

    The OME metadata carries the physical voxel size, so deconvolution software
    reads the sampling directly; the sidecar holds the provenance (source
    file and hash, channel, bead count, settings, comparison with theory).
    """
    import tifffile

    from psfwidget.report.paths import unique

    target = unique(Path(path))
    tifffile.imwrite(
        target,
        average.astype(np.float32),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "ZYX",
            "Name": name,
            "PhysicalSizeZ": voxel_size_um[0],
            "PhysicalSizeY": voxel_size_um[1],
            "PhysicalSizeX": voxel_size_um[2],
            "PhysicalSizeZUnit": "µm",
            "PhysicalSizeYUnit": "µm",
            "PhysicalSizeXUnit": "µm",
        },
    )
    sidecar = target.with_name(target.name.removesuffix(".ome.tif") + ".json")
    sidecar.write_text(json.dumps(details, indent=2, default=str), encoding="utf-8")
    return [target, sidecar]
