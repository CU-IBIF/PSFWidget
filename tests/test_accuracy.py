"""Accuracy of the FWHM measurement on synthetic beads (NFR-01, SYN-A).

A reduced version of ``tools/fitting_accuracy.py``, which writes the full
report to docs/validation/fitting_accuracy.md.

NFR-01: on synthetic beads sampled at or above Nyquist with SNR >= 20, the
median FWHM of a field of 10 beads is within 3% of the truth on average and
within 5% in 95% of fields.
"""

from __future__ import annotations

import pytest

import fitting_accuracy
from fitting_accuracy import SAMPLINGS, Simulator, field_median, per_bead

NYQUIST = SAMPLINGS["2.3 voxels per FWHM (Nyquist limit)"]
FINE = SAMPLINGS["4 voxels per FWHM"]


@pytest.mark.req("NFR-01", "MET-01")
@pytest.mark.parametrize("voxel", [NYQUIST, FINE], ids=["nyquist", "fine"])
def test_field_median_meets_nfr01_at_snr_20(voxel: tuple[float, float, float]) -> None:
    stats = field_median(Simulator(voxel, snr=20), fields=60, beads=10, seed=21)
    for axis, result in stats.items():
        assert result.n == 60
        assert result.mean_abs <= 0.03, axis
        assert result.p95_abs <= 0.05, axis


@pytest.mark.req("NFR-01")
def test_per_bead_measurement_is_unbiased() -> None:
    stats, snr, failed = per_bead(Simulator(NYQUIST, snr=50), beads=300, seed=4)
    assert failed == 0
    assert snr == pytest.approx(44, rel=0.2)
    for axis, result in stats.items():
        assert abs(result.bias) <= 0.01, axis


def test_report_is_produced_for_a_small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fitting_accuracy, "SNRS", (50,))
    text = fitting_accuracy.report(beads=20, fields=3, beads_per_field=3)
    assert text.startswith("# FWHM measurement accuracy on synthetic beads")
    assert text.count("| 4") >= 1
    assert "## Pixel integration" in text
