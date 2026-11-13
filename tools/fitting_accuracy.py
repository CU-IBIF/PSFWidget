"""Monte Carlo accuracy of the FWHM measurement on synthetic beads (NFR-01, SYN-A).

Renders single Gaussian beads of known width at random sub-voxel positions
through the camera model, measures them with the production code, and
reports the error per bead and of the median over a field of beads.

Usage::

    python tools/fitting_accuracy.py                 # print the report
    python tools/fitting_accuracy.py --write         # update docs/validation/fitting_accuracy.md
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from psfwidget import synthetic
from psfwidget.metrics.bead import BeadResult, Geometry, half_box, measure_bead
from psfwidget.settings import AnalysisSettings

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "validation" / "fitting_accuracy.md"

FWHM_UM = (0.60, 0.20, 0.20)
"""True bead FWHM ``(z, y, x)``."""
BACKGROUND_PHOTONS = 20.0
READ_NOISE = 2.0
SAMPLINGS = {
    "4 voxels per FWHM": (0.15, 0.05, 0.05),
    "2.3 voxels per FWHM (Nyquist limit)": (0.26, 0.087, 0.087),
}
SNRS = (10, 20, 50, 100)
AXES = ("x", "y", "z")


@dataclass(frozen=True)
class ErrorStats:
    """Relative FWHM error over many trials."""

    bias: float
    """Mean signed error."""
    mean_abs: float
    p95_abs: float
    n: int

    @classmethod
    def of(cls, errors: Sequence[float]) -> ErrorStats:
        """Summarise relative errors."""
        values = np.asarray(errors, dtype=np.float64)
        return cls(
            bias=float(values.mean()),
            mean_abs=float(np.abs(values).mean()),
            p95_abs=float(np.percentile(np.abs(values), 95)),
            n=int(values.size),
        )


class Simulator:
    """Renders and measures single beads for one sampling and SNR."""

    def __init__(
        self,
        voxel_um: tuple[float, float, float],
        snr: float,
        *,
        fwhm_um: tuple[float, float, float] = FWHM_UM,
        oversample: int = 1,
        settings: AnalysisSettings | None = None,
    ) -> None:
        self.fwhm = fwhm_um
        self.oversample = oversample
        # Acceptance checks off, so every bead is fitted and the statistics are not
        # biased towards beads whose noise happened to raise their SNR.
        self.settings = settings or AnalysisSettings(
            bead_size_correction=False, min_snr=0.0, min_r_squared=0.0
        )
        noise_sd = math.sqrt(BACKGROUND_PHOTONS + READ_NOISE**2)
        sigma = [f / synthetic.FWHM_PER_SIGMA / v for f, v in zip(fwhm_um, voxel_um, strict=True)]
        self.photons = snr * noise_sd * (2 * math.pi) ** 1.5 * sigma[0] * sigma[1] * sigma[2]
        expected = (fwhm_um[1], fwhm_um[0])
        self.half = half_box(voxel_um, expected, self.settings)
        self.spec = synthetic.FieldSpec(
            shape=(2 * self.half[0] + 1, 2 * self.half[1] + 1, 2 * self.half[2] + 1),
            voxel_size_um=voxel_um,
            background_photons=BACKGROUND_PHOTONS,
            offset=100.0,
            read_noise=READ_NOISE,
            bit_depth=16,
        )
        self.geometry = Geometry(voxel_um, expected, self.half, 65535.0, None, None, self.settings)

    def measure(self, rng: np.random.Generator) -> BeadResult:
        """Render one bead at a random sub-voxel position and measure it."""
        centre = (
            self.half[0] + rng.uniform(-0.5, 0.5),
            self.half[1] + rng.uniform(-0.5, 0.5),
            self.half[2] + rng.uniform(-0.5, 0.5),
        )
        bead = synthetic.BeadSpec(position_px=centre, fwhm_um=self.fwhm, photons=self.photons)
        field = synthetic.render_field(self.spec, [bead], rng, oversample=self.oversample)
        return measure_bead(field.data, BeadResult(0, *self.half), self.geometry)

    def errors(self, bead: BeadResult) -> dict[str, float] | None:
        """Return the relative error per axis, or ``None`` if the bead was not fitted."""
        measured = (bead.fwhm_x_raw_um, bead.fwhm_y_raw_um, bead.fwhm_z_raw_um)
        if any(m is None for m in measured):
            return None
        truth = (self.fwhm[2], self.fwhm[1], self.fwhm[0])
        return {
            axis: (m - t) / t  # type: ignore[operator]
            for axis, m, t in zip(AXES, measured, truth, strict=True)
        }


def per_bead(sim: Simulator, beads: int, seed: int) -> tuple[dict[str, ErrorStats], float, int]:
    """Return error statistics per axis, the median measured SNR, and the number not fitted."""
    rng = np.random.default_rng(seed)
    errors: dict[str, list[float]] = {axis: [] for axis in AXES}
    snrs: list[float] = []
    failed = 0
    for _ in range(beads):
        bead = sim.measure(rng)
        error = sim.errors(bead)
        if error is None:
            failed += 1
            continue
        snrs.append(bead.snr or math.nan)
        for axis in AXES:
            errors[axis].append(error[axis])
    stats = {axis: ErrorStats.of(values) for axis, values in errors.items()}
    return stats, float(np.median(snrs)), failed


def field_median(sim: Simulator, fields: int, beads: int, seed: int) -> dict[str, ErrorStats]:
    """Return error statistics of the per-field median FWHM over ``fields`` fields."""
    rng = np.random.default_rng(seed)
    medians: dict[str, list[float]] = {axis: [] for axis in AXES}
    for _ in range(fields):
        values: dict[str, list[float]] = {axis: [] for axis in AXES}
        for _ in range(beads):
            error = sim.errors(sim.measure(rng))
            if error is not None:
                for axis in AXES:
                    values[axis].append(error[axis])
        for axis in AXES:
            if values[axis]:
                medians[axis].append(float(np.median(values[axis])))
    return {axis: ErrorStats.of(values) for axis, values in medians.items()}


def _pct(value: float, signed: bool = False) -> str:
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"


def report(beads: int = 500, fields: int = 200, beads_per_field: int = 10) -> str:
    """Run the full grid and return a Markdown report."""
    lines = [
        "# FWHM measurement accuracy on synthetic beads",
        "",
        "Generated by `python tools/fitting_accuracy.py --write`. Do not edit by hand.",
        "",
        "**Set-up.** Single 3D Gaussian beads with true FWHM "
        f"{FWHM_UM[2]:.2f} µm laterally and {FWHM_UM[0]:.2f} µm axially, at random sub-voxel "
        "positions, point-sampled, with Poisson shot noise on a background of "
        f"{BACKGROUND_PHOTONS:g} photons per voxel and Gaussian read noise of {READ_NOISE:g} "
        "counts. SNR is the bead's peak above background divided by the background SD, as "
        "PSFWidget measures it; the median measured value is shown. Default analysis settings "
        "(3×3 line bundles), except that bead-size correction and the SNR and fit-quality "
        "acceptance checks are off, so that every bead is fitted. Errors are relative to the "
        "true FWHM.",
        "",
        f"**Per bead**: {beads} beads per condition. **Field median**: the median over "
        f"{beads_per_field} beads per field, {fields} fields per condition. "
        "Bias is the mean signed error; |e| the mean absolute error; P95 the 95th percentile of "
        "the absolute error.",
        "",
    ]
    for label, voxel in SAMPLINGS.items():
        lines += [
            f"## {label}",
            "",
            f"Voxel {voxel[2] * 1000:.0f} × {voxel[1] * 1000:.0f} × {voxel[0] * 1000:.0f} nm "
            "(x × y × z).",
            "",
            "| SNR | Axis | Per bead: bias | \\|e\\| | P95 | Field median: bias | \\|e\\| | P95 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for snr in SNRS:
            sim = Simulator(voxel, snr)
            single, measured_snr, failed = per_bead(sim, beads, seed=snr)
            median = field_median(sim, fields, beads_per_field, seed=1000 + snr)
            for axis in AXES:
                a, b = single[axis], median[axis]
                snr_label = f"{measured_snr:.0f}" + (f" ({failed} not fitted)" if failed else "")
                lines.append(
                    f"| {snr_label if axis == 'x' else ''} | {axis} | {_pct(a.bias, True)} | "
                    f"{_pct(a.mean_abs)} | {_pct(a.p95_abs)} | {_pct(b.bias, True)} | "
                    f"{_pct(b.mean_abs)} | {_pct(b.p95_abs)} |"
                )
        lines.append("")
    sim = Simulator(SAMPLINGS["2.3 voxels per FWHM (Nyquist limit)"], 50, oversample=5)
    integrated, _, _ = per_bead(sim, beads, seed=7)
    lines += [
        "## Pixel integration",
        "",
        "Cameras integrate light over each pixel rather than sampling a point, which widens a "
        "Gaussian by √(σ² + p²/12). Rendered with 5×5×5 sub-samples per voxel at the Nyquist "
        "limit and SNR 50, the per-bead bias is: "
        + ", ".join(f"{axis} {_pct(integrated[axis].bias, True)}" for axis in AXES)
        + ". This is not corrected yet. From the formula it would be about +1.4% at 4 voxels "
        "per FWHM. Point-scanning confocal images are closer to point samples.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print or write the accuracy report."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {REPORT.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    text = report()
    if args.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
