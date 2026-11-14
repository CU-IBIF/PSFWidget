"""Tests for the Gaussian fits and bead-size correction (MET-01, MET-02, MET-08, MET-10, MET-15)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from psfwidget.metrics.gaussian import (
    BEAD_FWHM_PER_DIAMETER,
    FitError,
    bead_corrected_fwhm,
    fit_gaussian_1d,
    fit_gaussian_2d,
)
from psfwidget.synthetic import FWHM_PER_SIGMA


def _gauss(x: np.ndarray, a: float, c: float, fwhm: float, b: float) -> np.ndarray:
    return a * np.exp(-0.5 * ((x - c) / (fwhm / FWHM_PER_SIGMA)) ** 2) + b


@pytest.mark.req("MET-01", "MET-10")
@pytest.mark.parametrize(
    ("fwhm_px", "offset_px"), [(1.5, 0.0), (2.3, 0.37), (4.0, -0.21), (10.0, 0.5)]
)
def test_noiseless_1d_fit_is_exact(fwhm_px: float, offset_px: float) -> None:
    step = 0.05
    x = np.arange(-30, 31, dtype=np.float64) * step
    y = _gauss(x, 200.0, offset_px * step, fwhm_px * step, 100.0)
    fit = fit_gaussian_1d(x, y, fwhm_guess_um=2 * fwhm_px * step)
    assert fit.fwhm_um == pytest.approx(fwhm_px * step, rel=1e-3)
    assert fit.centre_um == pytest.approx(offset_px * step, abs=1e-6)
    assert fit.amplitude == pytest.approx(200.0, rel=1e-4)
    assert fit.offset == pytest.approx(100.0, rel=1e-4)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)
    assert fit.fwhm_se_um < 1e-6
    assert fit.reduced_chi_squared < 1e-9


def test_1d_fit_reports_uncertainty_and_quality_with_noise() -> None:
    rng = np.random.default_rng(3)
    x = np.linspace(-1, 1, 41)
    y = _gauss(x, 50.0, 0.0, 0.3, 10.0) + rng.normal(0, 2.0, x.size)
    fit = fit_gaussian_1d(x, y, fwhm_guess_um=0.2)
    assert fit.fwhm_um == pytest.approx(0.3, rel=0.1)
    assert 0 < fit.fwhm_se_um < 0.05
    assert 0.9 < fit.r_squared < 1.0
    assert fit.reduced_chi_squared == pytest.approx(4.0, rel=0.5)


@pytest.mark.parametrize(
    ("x", "y", "message"),
    [
        (np.arange(4.0), np.arange(4.0), "fewer than 5"),
        (np.arange(9.0), np.ones(9), "flat"),
        (np.arange(9.0), np.arange(9.0), "outside the profile|wider than the profile|no positive"),
    ],
)
def test_1d_fit_rejects_unusable_profiles(x: np.ndarray, y: np.ndarray, message: str) -> None:
    with pytest.raises(FitError, match=message):
        fit_gaussian_1d(x, y, fwhm_guess_um=2.0)


def _ellipse(
    u: np.ndarray,
    v: np.ndarray,
    major: float,
    minor: float,
    angle_deg: float,
    centre: tuple[float, float] = (0.0, 0.0),
) -> np.ndarray:
    theta = math.radians(angle_deg)
    du, dv = u - centre[0], v - centre[1]
    along = math.cos(theta) * du + math.sin(theta) * dv
    across = -math.sin(theta) * du + math.cos(theta) * dv
    return (
        300
        * np.exp(
            -0.5
            * ((along / (major / FWHM_PER_SIGMA)) ** 2 + (across / (minor / FWHM_PER_SIGMA)) ** 2)
        )
        + 20
    )


@pytest.mark.req("MET-02", "MET-08")
@pytest.mark.parametrize("angle", [0.0, 25.0, 90.0, 135.0, 179.0])
@pytest.mark.parametrize("ratio", [1.2, 2.0, 3.0])
def test_noiseless_2d_fit_recovers_axes_and_angle(angle: float, ratio: float) -> None:
    first = np.linspace(-1.0, 1.0, 41)
    second = np.linspace(-1.2, 1.2, 49)
    u, v = np.meshgrid(first, second, indexing="ij")
    major, minor = 0.2 * ratio, 0.2
    fit = fit_gaussian_2d(
        first,
        second,
        _ellipse(u, v, major, minor, angle, (0.03, -0.02)),
        fwhm_guess_um=(0.25, 0.25),
    )
    assert fit.fwhm_major_um == pytest.approx(major, rel=1e-3)
    assert fit.fwhm_minor_um == pytest.approx(minor, rel=1e-3)
    difference = (fit.angle_deg - angle + 90.0) % 180.0 - 90.0
    assert abs(difference) < 0.05
    assert fit.centre_um == pytest.approx((0.03, -0.02), abs=1e-6)
    assert fit.ellipticity == pytest.approx(1 / ratio, rel=1e-3)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)


def test_2d_fit_of_round_spot_and_bad_planes() -> None:
    axis = np.linspace(-1, 1, 21)
    u, v = np.meshgrid(axis, axis, indexing="ij")
    fit = fit_gaussian_2d(axis, axis, _ellipse(u, v, 0.3, 0.3, 0.0), fwhm_guess_um=(0.2, 0.4))
    assert fit.ellipticity == pytest.approx(1.0, abs=1e-3)
    with pytest.raises(FitError, match="too small"):
        fit_gaussian_2d(axis[:2], axis, np.zeros((2, 21)), fwhm_guess_um=(0.2, 0.2))
    with pytest.raises(FitError, match="flat"):
        fit_gaussian_2d(axis, axis, np.ones((21, 21)), fwhm_guess_um=(0.2, 0.2))
    with pytest.raises(FitError, match=r"no positive|not a peak|outside|wider"):
        fit_gaussian_2d(axis, axis, -_ellipse(u, v, 0.3, 0.3, 0.0), fwhm_guess_um=(0.2, 0.2))


@pytest.mark.req("MET-15")
def test_bead_size_correction() -> None:
    assert pytest.approx(0.5266, abs=1e-4) == BEAD_FWHM_PER_DIAMETER  # 2.3548 / √20
    # 100 nm bead: equivalent Gaussian FWHM 52.66 nm, removed in quadrature.
    assert bead_corrected_fwhm(0.200, 0.100) == pytest.approx(
        math.sqrt(0.2**2 - 0.05266**2), rel=1e-4
    )
    assert bead_corrected_fwhm(0.200, None) == 0.200
    assert bead_corrected_fwhm(0.200, 0.0) == 0.200
    assert math.isnan(bead_corrected_fwhm(0.05, 0.100))


@pytest.mark.req("MET-03")
def test_3d_gaussian_fit_recovers_width_and_centre() -> None:
    from psfwidget.metrics.gaussian import fit_gaussian_3d
    from psfwidget.synthetic import FWHM_PER_SIGMA

    z = np.arange(21, dtype=np.float64) * 0.15
    y = np.arange(25, dtype=np.float64) * 0.05
    x = np.arange(25, dtype=np.float64) * 0.05
    centre, fwhm = (1.52, 0.61, 0.58), (0.6, 0.22, 0.25)
    sigma = [f / FWHM_PER_SIGMA for f in fwhm]
    box = 100.0 + 1000.0 * (
        np.exp(-0.5 * ((z - centre[0]) / sigma[0]) ** 2)[:, None, None]
        * np.exp(-0.5 * ((y - centre[1]) / sigma[1]) ** 2)[None, :, None]
        * np.exp(-0.5 * ((x - centre[2]) / sigma[2]) ** 2)[None, None, :]
    )
    fit = fit_gaussian_3d((z, y, x), box, fwhm_guess_um=(0.5, 0.2, 0.2))
    assert fit.centre_um == pytest.approx(centre, abs=1e-6)
    assert fit.fwhm_um == pytest.approx(fwhm, rel=1e-6)
    assert fit.offset == pytest.approx(100.0)
    assert fit.r_squared == pytest.approx(1.0)
    with pytest.raises(FitError):
        fit_gaussian_3d((z, y, x), np.full(box.shape, 5.0), fwhm_guess_um=(0.5, 0.2, 0.2))
