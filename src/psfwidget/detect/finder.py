"""Find candidate beads in a 3D field (DET-01).

Isolated hot pixels are removed with a small in-plane median filter. The
volume is then smoothed with a Gaussian matched to the expected PSF, and local
maxima above a threshold, away from the outermost voxel layer, become
candidates. Close maxima are merged by non-maximum suppression, keeping the
brighter one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from psfwidget.synthetic import FWHM_PER_SIGMA

_MAX_STATISTIC_SAMPLES = 2_000_000
RELATIVE_REFERENCE_PERCENTILE = 50.0
"""The relative threshold is a fraction of this percentile of candidate heights."""


@dataclass(frozen=True)
class Candidate:
    """A local maximum of the smoothed volume."""

    position_px: tuple[int, int, int]
    """Voxel ``(z, y, x)`` of the maximum."""
    height: float
    """Smoothed value above the background estimate."""


@dataclass(frozen=True)
class Detection:
    """Candidates and the statistics used to find them."""

    candidates: tuple[Candidate, ...]
    background: float
    noise: float
    threshold: float
    """Smoothed value a maximum had to exceed."""


def _sample(values: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return a regular subsample small enough for fast robust statistics."""
    step = max(1, math.ceil(values.size / _MAX_STATISTIC_SAMPLES))
    return values.ravel()[::step]


def remove_hot_pixels(volume: NDArray[np.generic], median_px: int) -> NDArray[np.float32]:
    """Return ``volume`` as float32 after an in-plane median filter of ``median_px`` (1: none)."""
    data = volume.astype(np.float32)
    if median_px > 1:
        data = ndimage.median_filter(data, size=(1, median_px, median_px), mode="nearest")
    return data


def find_candidates(
    volume: NDArray[np.generic],
    voxel_size_um: tuple[float, float, float],
    expected_fwhm_um: tuple[float, float],
    *,
    threshold_sd: float,
    relative_threshold: float,
    median_px: int = 1,
) -> Detection:
    """Return candidate bead positions in ``volume`` ordered from brightest.

    Parameters
    ----------
    volume
        Image ``(z, y, x)``.
    voxel_size_um
        Voxel size ``(z, y, x)``.
    expected_fwhm_um
        Expected ``(lateral, axial)`` FWHM of a bead image.
    threshold_sd
        Required height above background in units of the smoothed noise SD.
    relative_threshold
        Required height as a fraction of the median height of
        maxima above the absolute threshold.
    median_px
        Size of an in-plane median filter applied first to remove hot
        pixels; 1 turns it off.
    """
    lateral, axial = expected_fwhm_um
    fwhm_px = (axial / voxel_size_um[0], lateral / voxel_size_um[1], lateral / voxel_size_um[2])
    sigma_px = tuple(f / FWHM_PER_SIGMA for f in fwhm_px)
    data = remove_hot_pixels(volume, median_px)
    smoothed = ndimage.gaussian_filter(data, sigma_px, mode="nearest")

    sample = _sample(smoothed)
    background = float(np.median(sample))
    noise = 1.4826 * float(np.median(np.abs(sample - background)))
    if noise <= 0:
        noise = float(np.std(sample)) or 1e-12

    window = tuple(max(3, 2 * math.floor(f / 2) + 1) for f in fwhm_px)
    local_max = ndimage.maximum_filter(smoothed, size=window, mode="nearest")
    absolute = background + threshold_sd * noise
    interior = np.zeros(smoothed.shape, dtype=bool)
    interior[tuple(slice(1, n - 1) for n in smoothed.shape)] = True
    # Smoothing averages fewer voxels at the edge, so the edge is noisier than
    # the interior the threshold was set from. A maximum on the outermost layer
    # cannot be measured anyway, so those are not candidates.
    peaks = np.argwhere((smoothed == local_max) & (smoothed > absolute) & interior)
    if peaks.size == 0:
        return Detection((), background, noise, absolute)

    heights = smoothed[tuple(peaks.T)] - background
    # Relative to a typical bright bead, not the brightest maximum, so that one
    # aggregate cannot hide the single beads.
    reference = float(np.percentile(heights, RELATIVE_REFERENCE_PERCENTILE))
    threshold = max(absolute, background + relative_threshold * reference)
    order = np.argsort(-heights, kind="stable")
    scale = np.asarray(fwhm_px, dtype=np.float64)
    kept: list[Candidate] = []
    kept_positions = np.empty((0, 3), dtype=np.float64)
    for index in order:
        if heights[index] + background <= threshold:
            break
        position = peaks[index].astype(np.float64)
        if np.any(np.sum(((kept_positions - position) / scale) ** 2, axis=1) < 1.0):
            continue  # within one FWHM of a brighter maximum
        kept_positions = np.vstack([kept_positions, position])
        z, y, x = (int(v) for v in peaks[index])
        kept.append(Candidate(position_px=(z, y, x), height=float(heights[index])))
    return Detection(tuple(kept), background, noise, threshold)
