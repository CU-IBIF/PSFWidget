"""Gaussian fits to bead profiles and planes (MET-01, MET-02, MET-08, MET-10).

All fits include a constant offset and work in physical units, so FWHM and
orientation are correct for anisotropic voxels.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import optimize

from psfwidget.synthetic import FWHM_PER_SIGMA

_TOLERANCE = 1e-6
"""Relative convergence tolerance; far below the precision a FWHM needs."""
_MAX_EXPONENT = 50.0


class FitError(RuntimeError):
    """Raised when a fit does not converge or its result is unusable."""


@dataclass(frozen=True)
class Fit1D:
    """Result of fitting ``A · exp(−(x − x0)² / 2σ²) + B``."""

    amplitude: float
    centre_um: float
    fwhm_um: float
    offset: float
    fwhm_se_um: float
    """Standard error of the FWHM from the fit covariance."""
    r_squared: float
    reduced_chi_squared: float


@dataclass(frozen=True)
class Fit2D:
    """Result of fitting a rotated elliptical Gaussian with offset to a plane."""

    amplitude: float
    centre_um: tuple[float, float]
    """Centre along the plane's ``(first, second)`` axes."""
    fwhm_major_um: float
    fwhm_minor_um: float
    angle_deg: float
    """Orientation of the major axis, from the first plane axis towards the second, in [0, 180)."""
    offset: float
    r_squared: float

    @property
    def ellipticity(self) -> float:
        """Ratio of minor to major FWHM; 1 for a round profile (MET-08)."""
        return self.fwhm_minor_um / self.fwhm_major_um


def _r_squared(data: NDArray[np.float64], residual: NDArray[np.float64]) -> float:
    total = float(np.sum((data - data.mean()) ** 2))
    return 1.0 - float(np.sum(residual**2)) / total if total > 0 else 0.0


def _gauss_1d(
    t: NDArray[np.float64], a: float, c: float, s: float, b: float
) -> NDArray[np.float64]:
    return np.asarray(a * np.exp(-0.5 * ((t - c) / s) ** 2) + b, dtype=np.float64)


def _gauss_1d_jacobian(
    t: NDArray[np.float64], a: float, c: float, s: float, b: float
) -> NDArray[np.float64]:
    u = (t - c) / s
    g = np.exp(-0.5 * u**2)
    return np.column_stack([g, a * g * u / s, a * g * u**2 / s, np.ones_like(t)])


def fit_gaussian_1d(
    coordinates_um: NDArray[np.float64],
    values: NDArray[np.float64],
    *,
    fwhm_guess_um: float,
) -> Fit1D:
    """Fit a 1D Gaussian with offset to a profile.

    Uses Levenberg-Marquardt with an analytic Jacobian, then checks that the
    result is physical: positive amplitude, centre inside the profile, and a
    width narrower than the profile.

    Raises
    ------
    FitError
        If the profile is too short or flat, the fit fails, or the result is
        not physical.
    """
    x = np.asarray(coordinates_um, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    if x.size < 5 or x.size != y.size:
        raise FitError("profile has fewer than 5 points")
    span = float(x[-1] - x[0])
    low, high = float(y.min()), float(y.max())
    if high <= low:
        raise FitError("profile is flat")
    peak = float(x[int(np.argmax(y))])
    start = [high - low, peak, max(fwhm_guess_um, 1e-6) / FWHM_PER_SIGMA, low]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", optimize.OptimizeWarning)
            params, covariance = optimize.curve_fit(
                _gauss_1d,
                x,
                y,
                p0=start,
                jac=_gauss_1d_jacobian,
                method="lm",
                maxfev=2000,
                ftol=_TOLERANCE,
                xtol=_TOLERANCE,
            )
    except (RuntimeError, ValueError) as exc:
        raise FitError(f"1D Gaussian fit failed: {exc}") from exc
    amplitude, centre, sigma, offset = (float(v) for v in params)
    sigma = abs(sigma)
    if amplitude <= 0:
        raise FitError("1D Gaussian fit has no positive peak")
    if not x[0] <= centre <= x[-1]:
        raise FitError("1D Gaussian fit centre is outside the profile")
    if sigma >= span:
        raise FitError("1D Gaussian fit is wider than the profile")
    residual = y - _gauss_1d(x, amplitude, centre, sigma, offset)
    dof = max(x.size - 4, 1)
    variance = float(covariance[2, 2]) if np.all(np.isfinite(covariance)) else math.inf
    return Fit1D(
        amplitude=amplitude,
        centre_um=centre,
        fwhm_um=FWHM_PER_SIGMA * sigma,
        offset=offset,
        fwhm_se_um=FWHM_PER_SIGMA * math.sqrt(variance) if variance >= 0 else math.inf,
        r_squared=_r_squared(y, residual),
        reduced_chi_squared=float(np.sum(residual**2)) / dof,
    )


def fit_gaussian_2d(
    first_um: NDArray[np.float64],
    second_um: NDArray[np.float64],
    plane: NDArray[np.float64],
    *,
    fwhm_guess_um: tuple[float, float],
) -> Fit2D:
    """Fit a rotated elliptical Gaussian with offset to a plane.

    Parameters
    ----------
    first_um, second_um
        Coordinates of the plane's rows and columns.
    plane
        Values, shape ``(len(first_um), len(second_um))``.
    fwhm_guess_um
        Starting FWHM along the first and second axes.

    Raises
    ------
    FitError
        If the plane is too small or flat, the fit fails, or the result is
        not physical.
    """
    data = np.asarray(plane, dtype=np.float64)
    if data.shape != (first_um.size, second_um.size) or min(data.shape) < 3 or data.size < 12:
        raise FitError("plane is too small for a 2D fit")
    low, high = float(data.min()), float(data.max())
    if high <= low:
        raise FitError("plane is flat")
    u, v = np.meshgrid(first_um, second_um, indexing="ij")
    iu, iv = np.unravel_index(int(np.argmax(data)), data.shape)
    span_u = float(first_um[-1] - first_um[0])
    span_v = float(second_um[-1] - second_um[0])
    sigma_u = max(fwhm_guess_um[0], 1e-6) / FWHM_PER_SIGMA
    sigma_v = max(fwhm_guess_um[1], 1e-6) / FWHM_PER_SIGMA
    # Parameters: amplitude, centre (u, v), inverse covariance (p_uu, p_uv, p_vv), offset.
    # This form stays well conditioned for round spots, where an angle would not be.
    start = np.array(
        [
            high - low,
            float(first_um[iu]),
            float(second_um[iv]),
            1.0 / sigma_u**2,
            0.0,
            1.0 / sigma_v**2,
            low,
        ]
    )
    flat_u, flat_v, flat = u.ravel(), v.ravel(), data.ravel()

    def parts(p: NDArray[np.float64]) -> tuple[NDArray[np.float64], ...]:
        du, dv = flat_u - p[1], flat_v - p[2]
        exponent = -0.5 * (p[3] * du * du + 2.0 * p[4] * du * dv + p[5] * dv * dv)
        # Trial steps can make the form indefinite; clip to avoid overflow.
        g = np.exp(np.minimum(exponent, _MAX_EXPONENT))
        return g, du, dv

    def residual(p: NDArray[np.float64]) -> NDArray[np.float64]:
        g, _, _ = parts(p)
        return np.asarray(p[0] * g + p[6] - flat, dtype=np.float64)

    def jacobian(p: NDArray[np.float64]) -> NDArray[np.float64]:
        g, du, dv = parts(p)
        ag = p[0] * g
        out = np.empty((g.size, 7))
        out[:, 0] = g
        out[:, 1] = ag * (p[3] * du + p[4] * dv)
        out[:, 2] = ag * (p[4] * du + p[5] * dv)
        out[:, 3] = -0.5 * ag * du * du
        out[:, 4] = -ag * du * dv
        out[:, 5] = -0.5 * ag * dv * dv
        out[:, 6] = 1.0
        return out

    try:
        result = optimize.least_squares(
            residual,
            start,
            jac=jacobian,
            method="lm",
            max_nfev=4000,
            ftol=_TOLERANCE,
            xtol=_TOLERANCE,
        )
    except ValueError as exc:
        raise FitError(f"2D Gaussian fit failed: {exc}") from exc
    if not result.success:
        raise FitError(f"2D Gaussian fit did not converge: {result.message}")
    a, cu, cv, p_uu, p_uv, p_vv, offset = (float(x) for x in result.x)
    if a <= 0:
        raise FitError("2D Gaussian fit has no positive peak")
    determinant = p_uu * p_vv - p_uv * p_uv
    if p_uu <= 0 or determinant <= 0:
        raise FitError("2D Gaussian fit is not a peak (inverse covariance not positive definite)")
    if not (first_um[0] <= cu <= first_um[-1] and second_um[0] <= cv <= second_um[-1]):
        raise FitError("2D Gaussian fit centre is outside the plane")
    covariance = np.array([[p_vv, -p_uv], [-p_uv, p_uu]]) / determinant
    if math.sqrt(covariance[0, 0]) >= span_u or math.sqrt(covariance[1, 1]) >= span_v:
        raise FitError("2D Gaussian fit is wider than the plane")
    variances, vectors = np.linalg.eigh(covariance)  # ascending
    major_u, major_v = vectors[0, 1], vectors[1, 1]
    return Fit2D(
        amplitude=a,
        centre_um=(cu, cv),
        fwhm_major_um=FWHM_PER_SIGMA * math.sqrt(float(variances[1])),
        fwhm_minor_um=FWHM_PER_SIGMA * math.sqrt(float(variances[0])),
        angle_deg=math.degrees(math.atan2(major_v, major_u)) % 180.0,
        offset=offset,
        r_squared=_r_squared(flat, -np.asarray(result.fun)),
    )


def bead_corrected_fwhm(fwhm_um: float, bead_diameter_um: float | None) -> float:
    """Remove the width of a uniform spherical bead from a measured FWHM (MET-15).

    The bead is replaced by the Gaussian with the same second moment, whose
    FWHM is 0.527 times the diameter, and subtracted in quadrature. Returns
    NaN when the measured width is not larger than the bead's.
    """
    if bead_diameter_um is None or bead_diameter_um <= 0:
        return fwhm_um
    bead = BEAD_FWHM_PER_DIAMETER * bead_diameter_um
    if fwhm_um <= bead:
        return math.nan
    return math.sqrt(fwhm_um**2 - bead**2)


BEAD_FWHM_PER_DIAMETER = FWHM_PER_SIGMA / math.sqrt(20.0)
"""FWHM of the Gaussian matching a uniform sphere's variance (d²/20), per unit diameter."""


@dataclass(frozen=True)
class Fit3D:
    """An axis-aligned 3D Gaussian fit (MET-03)."""

    centre_um: tuple[float, float, float]
    """Sub-voxel centre ``(z, y, x)``."""
    fwhm_um: tuple[float, float, float]
    """FWHM ``(z, y, x)``."""
    amplitude: float
    offset: float
    r_squared: float


def fit_gaussian_3d(
    coordinates: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
    box: NDArray[np.float64],
    *,
    fwhm_guess_um: tuple[float, float, float],
) -> Fit3D:
    """Fit offset + A·exp(−½ Σ ((x_i − c_i) / σ_i)²) to a 3D box (MET-03).

    ``coordinates`` are the ``(z, y, x)`` axis coordinates of the box in µm.

    Raises
    ------
    FitError
        If the fit does not converge or gives a non-physical width.
    """
    data = np.asarray(box, dtype=np.float64)
    z, y, x = coordinates
    peak = np.unravel_index(int(np.argmax(data)), data.shape)
    offset0 = float(np.median(data))
    start = np.array(
        [
            float(data[peak]) - offset0,
            z[peak[0]],
            y[peak[1]],
            x[peak[2]],
            *(f / FWHM_PER_SIGMA for f in fwhm_guess_um),
            offset0,
        ]
    )

    def residual(p: NDArray[np.float64]) -> NDArray[np.float64]:
        amplitude, cz, cy, cx, sz, sy, sx, offset = p
        gz = np.exp(-0.5 * ((z - cz) / sz) ** 2)
        gy = np.exp(-0.5 * ((y - cy) / sy) ** 2)
        gx = np.exp(-0.5 * ((x - cx) / sx) ** 2)
        model = amplitude * gz[:, None, None] * gy[None, :, None] * gx[None, None, :] + offset
        return np.asarray((model - data).ravel(), dtype=np.float64)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            solution = optimize.least_squares(residual, start, method="lm", xtol=_TOLERANCE)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise FitError(f"3D fit failed: {exc}") from None
    amplitude, cz, cy, cx, sz, sy, sx, offset = solution.x
    sigmas = (abs(sz), abs(sy), abs(sx))
    extent = tuple(float(a[-1] - a[0]) for a in (z, y, x))
    if (
        not solution.success
        or amplitude <= 0
        or any(
            not math.isfinite(s) or s <= 0 or s * FWHM_PER_SIGMA > e
            for s, e in zip(sigmas, extent, strict=True)
        )
    ):
        raise FitError("3D fit did not converge to a bead-sized Gaussian")
    r_squared = _r_squared(data.ravel(), solution.fun)
    return Fit3D(
        centre_um=(float(cz), float(cy), float(cx)),
        fwhm_um=(
            sigmas[0] * FWHM_PER_SIGMA,
            sigmas[1] * FWHM_PER_SIGMA,
            sigmas[2] * FWHM_PER_SIGMA,
        ),
        amplitude=float(amplitude),
        offset=float(offset),
        r_squared=r_squared,
    )
