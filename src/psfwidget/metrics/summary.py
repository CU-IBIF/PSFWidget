"""Summary statistics of accepted beads (per field and channel)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from psfwidget.metrics.bead import BeadResult

OUTLIER_MIN_RELATIVE_SPREAD = 0.05
"""Smallest spread, as a fraction of the median, used for outlier rejection."""

SUMMARY_METRICS: tuple[str, ...] = (
    "fwhm_x_um",
    "fwhm_y_um",
    "fwhm_z_um",
    "fwhm_x_raw_um",
    "fwhm_y_raw_um",
    "fwhm_z_raw_um",
    "xy_major_um",
    "xy_minor_um",
    "xz_major_um",
    "xz_minor_um",
    "yz_major_um",
    "yz_minor_um",
    "ellipticity_xy",
    "ratio_x",
    "ratio_y",
    "ratio_z",
    "fwhm_x_3d_um",
    "fwhm_y_3d_um",
    "fwhm_z_3d_um",
    "integrated_intensity",
    "peak",
    "background",
    "snr",
    "sbr",
)


@dataclass(frozen=True)
class Stats:
    """Summary of one metric over accepted beads."""

    n: int
    mean: float
    sd: float
    median: float
    q25: float
    q75: float

    @property
    def iqr(self) -> float:
        """Interquartile range."""
        return self.q75 - self.q25


def describe(values: Sequence[float]) -> Stats | None:
    """Return summary statistics of the finite values, or ``None`` if there are none."""
    data = np.asarray([v for v in values if math.isfinite(v)], dtype=np.float64)
    if data.size == 0:
        return None
    q25, median, q75 = (float(q) for q in np.percentile(data, [25, 50, 75]))
    return Stats(
        n=int(data.size),
        mean=float(data.mean()),
        sd=float(data.std(ddof=1)) if data.size > 1 else math.nan,
        median=median,
        q25=q25,
        q75=q75,
    )


def summarize(beads: Sequence[BeadResult]) -> dict[str, Stats]:
    """Return statistics of each summary metric over the accepted beads."""
    accepted = [b for b in beads if b.accepted]
    summary: dict[str, Stats] = {}
    for metric in SUMMARY_METRICS:
        values = [v for b in accepted if (v := getattr(b, metric)) is not None]
        stats = describe(values)
        if stats is not None:
            summary[metric] = stats
    return summary


def rejection_counts(beads: Sequence[BeadResult]) -> dict[str, int]:
    """Count rejected beads by the kind of their first reason (DET-08)."""
    counts: dict[str, int] = {}
    for bead in beads:
        if bead.accepted or not bead.reasons:
            continue
        kind = bead.reasons[0].split(" (")[0].split(":")[0]
        for prefix in ("SNR", "SBR"):
            if kind.startswith(prefix):
                kind = f"low {prefix}"
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def flag_outliers(beads: Sequence[BeadResult], threshold: float, minimum: int) -> list[BeadResult]:
    """Reject accepted beads far from the median integrated intensity or lateral FWHM (DET-06).

    The spread is the median absolute deviation scaled to a normal SD, but at
    least 5% of the median, so that photon noise among near-identical beads is
    not mistaken for outliers: with the default threshold of 4, a bead must
    differ from the median by at least 20%. Nothing is flagged with fewer than
    ``minimum`` accepted beads.
    """
    result = list(beads)
    accepted = [i for i, b in enumerate(result) if b.accepted]
    if len(accepted) < minimum:
        return result

    def lateral(bead: BeadResult) -> float | None:
        if bead.fwhm_x_um is None or bead.fwhm_y_um is None:
            return None
        return (bead.fwhm_x_um + bead.fwhm_y_um) / 2.0

    checks = (
        ("integrated intensity", lambda b: b.integrated_intensity),
        ("lateral FWHM", lateral),
    )
    for label, value in checks:
        values = np.asarray(
            [v for i in accepted if (v := value(result[i])) is not None], dtype=np.float64
        )
        if values.size < minimum:
            continue
        median = float(np.median(values))
        spread = max(
            1.4826 * float(np.median(np.abs(values - median))),
            OUTLIER_MIN_RELATIVE_SPREAD * abs(median),
        )
        if spread <= 0:
            continue
        for i in accepted:
            v = value(result[i])
            if v is not None and abs(v - median) > threshold * spread:
                direction = "high" if v > median else "low"
                result[i] = result[i].reject(f"outlier: {label} {direction}")
    return result
