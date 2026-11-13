"""Numerical scalar model of the widefield and confocal PSF width (THE-01, THE-08).

The amplitude PSF of an aberration-free objective is computed as

    E(r, z) = ∫₀¹ J₀(k · NA · r · ρ) · exp(i · k · z · √(n² − NA² ρ²)) · ρ dρ

with k = 2π / λ (vacuum wavelength) and a uniform pupil. The intensity PSF is
|E|². This keeps the exact high-NA defocus phase but ignores polarisation and
apodisation, and assumes the sample and immersion refractive indices match.

* Widefield: the emission PSF.
* Confocal and spinning disk: the excitation PSF multiplied by the emission
  PSF integrated over the pinhole projected into the sample. Spinning disk is
  treated as a single pinhole; crosstalk between pinholes is ignored.

Resolution-enhancing modes (NSPARC, Live-SR, Airyscan-type pixel
reassignment) are not modelled. They are compared with the model of their
base modality (THE-08), so their ratios are expected to fall below 1.

Lengths are in micrometres and wavelengths in nanometres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray
from scipy import signal, special

from psfwidget.theory.analytic import NM_PER_UM, airy_unit_um

_PUPIL_NODES = 400
_PINHOLE_NODES = 64
_LATERAL_SAMPLES_PER_RESOLUTION = 40
_AXIAL_SAMPLES = 1200


@dataclass(frozen=True)
class ScalarWidth:
    """FWHM of the scalar model."""

    lateral_um: float
    axial_um: float
    model: str
    """Short description of what was modelled."""


@lru_cache(maxsize=4)
def _pupil_nodes(count: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    nodes, weights = np.polynomial.legendre.leggauss(count)
    return (nodes + 1.0) / 2.0, weights / 2.0  # mapped to [0, 1]


def amplitude(
    r_um: NDArray[np.float64],
    z_um: NDArray[np.float64],
    wavelength_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> NDArray[np.complex128]:
    """Return the amplitude PSF E(r, z) on the outer grid of ``r_um`` and ``z_um``."""
    rho, weights = _pupil_nodes(_PUPIL_NODES)
    k = 2.0 * math.pi / (wavelength_nm / NM_PER_UM)
    bessel = special.j0(k * numerical_aperture * np.multiply.outer(r_um, rho))  # (r, ρ)
    axial_phase = np.sqrt(refractive_index**2 - (numerical_aperture * rho) ** 2)
    defocus = np.exp(1j * k * np.multiply.outer(z_um, axial_phase))  # (z, ρ)
    weighted = defocus * (weights * rho)
    result: NDArray[np.complex128] = np.einsum("rp,zp->rz", bessel, weighted)
    return result


def intensity(
    r_um: NDArray[np.float64],
    z_um: NDArray[np.float64],
    wavelength_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> NDArray[np.float64]:
    """Return the intensity PSF |E(r, z)|², normalised to 1 at the focus."""
    field = amplitude(r_um, z_um, wavelength_nm, numerical_aperture, refractive_index)
    focus = amplitude(
        np.zeros(1), np.zeros(1), wavelength_nm, numerical_aperture, refractive_index
    )
    return np.asarray(np.abs(field) ** 2 / np.abs(focus[0, 0]) ** 2, dtype=np.float64)


def half_width(coordinates: NDArray[np.float64], profile: NDArray[np.float64]) -> float:
    """Return the FWHM of a profile that peaks at ``coordinates[0]`` and is symmetric about it.

    Raises
    ------
    ValueError
        If the profile does not fall below half its maximum within the range.
    """
    half = profile[0] / 2.0
    below = np.nonzero(profile < half)[0]
    if below.size == 0:
        raise ValueError("profile does not fall to half maximum within the sampled range")
    i = int(below[0])
    x0, x1, y0, y1 = coordinates[i - 1], coordinates[i], profile[i - 1], profile[i]
    crossing = x0 + (y0 - half) * (x1 - x0) / (y0 - y1)
    return float(2.0 * (crossing - coordinates[0]))


def _disc_mask(size: int, step: float, radius: float) -> NDArray[np.float64]:
    """Return a disc of ``radius`` on a grid, with area-weighted edge pixels."""
    sub = 5
    offsets = (np.arange(sub) + 0.5) / sub - 0.5
    axis = (np.arange(size) - size // 2) * step
    fine = (axis[:, None] + offsets[None, :] * step).ravel()
    inside = (fine[:, None] ** 2 + fine[None, :] ** 2) <= radius**2
    return inside.reshape(size, sub, size, sub).mean(axis=(1, 3)).astype(np.float64)


def _pinhole_integral(
    z_um: NDArray[np.float64],
    radius_um: float,
    wavelength_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> NDArray[np.float64]:
    """Return ∫ over the pinhole disc of the emission PSF, at r = 0, for each z."""
    nodes, weights = np.polynomial.legendre.leggauss(_PINHOLE_NODES)
    r = (nodes + 1.0) / 2.0 * radius_um
    psf = intensity(r, z_um, wavelength_nm, numerical_aperture, refractive_index)  # (r, z)
    return np.asarray((weights * radius_um / 2.0 * 2.0 * math.pi * r) @ psf, dtype=np.float64)


def _lateral_detection(
    x_um: NDArray[np.float64],
    radius_um: float,
    wavelength_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> NDArray[np.float64]:
    """Return the emission PSF at focus convolved with the pinhole disc, along x ≥ 0."""
    step = float(x_um[1] - x_um[0])
    reach = float(x_um[-1]) + radius_um
    half = math.ceil(reach / step)
    size = 2 * half + 1
    axis = (np.arange(size) - half) * step
    radius = np.hypot(axis[:, None], axis[None, :])
    unique, inverse = np.unique(np.round(radius / step, 6), return_inverse=True)
    values = intensity(
        unique * step, np.zeros(1), wavelength_nm, numerical_aperture, refractive_index
    )[:, 0]
    psf = values[inverse].reshape(size, size)
    disc = _disc_mask(2 * math.ceil(radius_um / step) + 3, step, radius_um)
    blurred = signal.fftconvolve(psf, disc, mode="same")
    return np.asarray(blurred[half, half : half + x_um.size], dtype=np.float64)


@lru_cache(maxsize=256)
def scalar_width(
    modality: str,
    excitation_nm: float | None,
    emission_nm: float,
    numerical_aperture: float,
    refractive_index: float,
    pinhole_au: float | None = None,
) -> ScalarWidth:
    """Return the FWHM of the scalar model.

    Parameters
    ----------
    modality
        ``"widefield"``, ``"confocal"``, or ``"spinning_disk"``.
    excitation_nm
        Excitation wavelength; required for confocal and spinning disk.
    emission_nm
        Emission wavelength.
    numerical_aperture, refractive_index
        Objective NA and immersion refractive index.
    pinhole_au
        Pinhole diameter in Airy units at the emission wavelength; required
        for confocal and spinning disk. ``0`` models a point detector.
    """
    if not 0 < numerical_aperture < refractive_index:
        raise ValueError(
            f"numerical aperture must be positive and below the refractive index "
            f"({numerical_aperture:g} vs {refractive_index:g})"
        )
    resolution = emission_nm / NM_PER_UM / numerical_aperture
    x = np.linspace(0.0, 2.0 * resolution, 2 * _LATERAL_SAMPLES_PER_RESOLUTION + 1)
    depth = (
        6.0
        * emission_nm
        / NM_PER_UM
        / (refractive_index - math.sqrt(refractive_index**2 - numerical_aperture**2))
    )
    z = np.linspace(0.0, depth, _AXIAL_SAMPLES)

    if modality == "widefield":
        lateral = intensity(x, np.zeros(1), emission_nm, numerical_aperture, refractive_index)
        axial = intensity(np.zeros(1), z, emission_nm, numerical_aperture, refractive_index)
        return ScalarWidth(
            lateral_um=half_width(x, lateral[:, 0]),
            axial_um=half_width(z, axial[0, :]),
            model="scalar widefield (emission PSF)",
        )

    if modality not in ("confocal", "spinning_disk"):
        raise ValueError(f"unknown modality: {modality!r}")
    if excitation_nm is None or pinhole_au is None:
        raise ValueError("confocal models need the excitation wavelength and pinhole size")
    if pinhole_au < 0:
        raise ValueError("pinhole size must not be negative")

    excitation_lateral = intensity(
        x, np.zeros(1), excitation_nm, numerical_aperture, refractive_index
    )[:, 0]
    excitation_axial = intensity(
        np.zeros(1), z, excitation_nm, numerical_aperture, refractive_index
    )[0, :]
    radius = pinhole_au * airy_unit_um(emission_nm, numerical_aperture) / 2.0
    if radius == 0:
        detection_lateral = intensity(
            x, np.zeros(1), emission_nm, numerical_aperture, refractive_index
        )[:, 0]
        detection_axial = intensity(
            np.zeros(1), z, emission_nm, numerical_aperture, refractive_index
        )[0, :]
    else:
        detection_lateral = _lateral_detection(
            x, radius, emission_nm, numerical_aperture, refractive_index
        )
        detection_axial = _pinhole_integral(
            z, radius, emission_nm, numerical_aperture, refractive_index
        )
    name = "spinning-disk" if modality == "spinning_disk" else "confocal"
    return ScalarWidth(
        lateral_um=half_width(x, excitation_lateral * detection_lateral),
        axial_um=half_width(z, excitation_axial * detection_axial),
        model=f"scalar {name}, pinhole {pinhole_au:.2f} AU",
    )
