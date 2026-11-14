"""The theoretical PSF as a 3D image at the acquisition sampling (THE-02, THE-04, THE-05).

The scalar model of :mod:`psfwidget.theory.scalar` is evaluated on a radial
grid, mapped to a finer 3D grid, and then made comparable with a measured
bead image:

* confocal and spinning disk: excitation PSF × (emission PSF convolved
  laterally with the pinhole disc, plane by plane);
* multiphoton: squared excitation PSF;
* widefield: emission PSF;
* convolved with the bead (a uniform sphere of the bead diameter);
* integrated over each voxel (box average of the finer grid);
* normalised to a sum of 1.

The comparison with the averaged empirical PSF (Strehl ratio and similarity)
is in :mod:`psfwidget.average`.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy import interpolate, signal

from psfwidget.io.model import Modality
from psfwidget.theory.analytic import NM_PER_UM, airy_unit_um
from psfwidget.theory.model import Theory
from psfwidget.theory.scalar import _disc_mask, intensity

OVERSAMPLE = 3
RADIAL_SAMPLES = 400


def _radial(
    wavelength_nm: float, na: float, n: float, r_max: float, z: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    r = np.linspace(0.0, r_max, RADIAL_SAMPLES)
    return r, intensity(r, z, wavelength_nm, na, n)


def _on_grid(
    r: NDArray[np.float64], values: NDArray[np.float64], radius: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Interpolate a radial (r, z) profile onto lateral radii, giving (z, y, x)."""
    function = interpolate.interp1d(r, values, axis=0, bounds_error=False, fill_value=0.0)
    mapped = function(radius)  # (y, x, z)
    return np.asarray(np.moveaxis(mapped, -1, 0), dtype=np.float64)


def _ball(step: tuple[float, float, float], diameter_um: float) -> NDArray[np.float64]:
    radius = diameter_um / 2.0
    half = [max(1, math.ceil(radius / s)) for s in step]
    axes = [(np.arange(-h, h + 1)) * s for h, s in zip(half, step, strict=True)]
    zz, yy, xx = np.meshgrid(*axes, indexing="ij")
    ball = ((zz**2 + yy**2 + xx**2) <= radius**2).astype(np.float64)
    if ball.sum() == 0:
        ball[tuple(h for h in half)] = 1.0
    return np.asarray(ball / ball.sum(), dtype=np.float64)


def theoretical_psf(
    theory: Theory,
    voxel_size_um: tuple[float, float, float],
    shape: tuple[int, int, int],
    *,
    bead_diameter_um: float | None = None,
    oversample: int = OVERSAMPLE,
) -> NDArray[np.float64]:
    """Return the theoretical bead image on a centred grid of ``shape`` voxels, sum 1.

    Each dimension of ``shape`` should be odd so the centre falls on a voxel.
    """
    vz, vy, vx = voxel_size_um
    fine_step = (vz / oversample, vy / oversample, vx / oversample)
    fine_shape = tuple(s * oversample for s in shape)
    axes = [
        np.asarray((np.arange(n) - (n - 1) / 2.0) * step, dtype=np.float64)
        for n, step in zip(fine_shape, fine_step, strict=True)
    ]
    z = axes[0]
    radius = np.hypot(axes[1][:, None], axes[2][None, :])
    r_max = float(radius.max()) + 1e-9
    na, n = theory.numerical_aperture, theory.refractive_index
    modality = theory.modality

    if modality is Modality.MULTIPHOTON:
        assert theory.excitation_nm is not None
        r, values = _radial(theory.excitation_nm, na, n, r_max, z)
        psf = _on_grid(r, values, radius) ** 2
    elif modality is Modality.WIDEFIELD:
        assert theory.emission_nm is not None
        r, values = _radial(theory.emission_nm, na, n, r_max, z)
        psf = _on_grid(r, values, radius)
    else:
        assert theory.excitation_nm is not None
        assert theory.emission_nm is not None
        r, excitation = _radial(theory.excitation_nm, na, n, r_max, z)
        r, emission = _radial(theory.emission_nm, na, n, r_max, z)
        detection = _on_grid(r, emission, radius)
        pinhole = theory.pinhole_au or 0.0
        pinhole_radius = pinhole * airy_unit_um(theory.emission_nm, na) / 2.0
        if pinhole_radius > fine_step[1] / 2:
            size = 2 * math.ceil(pinhole_radius / fine_step[1]) + 3
            disc = _disc_mask(size, fine_step[1], pinhole_radius)
            detection = signal.fftconvolve(detection, disc[None, :, :], mode="same", axes=(1, 2))
        psf = _on_grid(r, excitation, radius) * detection

    if bead_diameter_um:
        psf = signal.fftconvolve(psf, _ball(fine_step, bead_diameter_um), mode="same")
    psf = np.clip(psf, 0.0, None)
    binned = psf.reshape(shape[0], oversample, shape[1], oversample, shape[2], oversample).sum(
        axis=(1, 3, 5)
    )
    return np.asarray(binned / binned.sum(), dtype=np.float64)


def wavelength_um(theory: Theory) -> float:
    """Return the wavelength that sets the PSF size, in µm (for grid sizing)."""
    wavelength = (
        theory.excitation_nm
        if theory.modality is Modality.MULTIPHOTON
        else (theory.emission_nm or theory.excitation_nm)
    )
    assert wavelength is not None
    return wavelength / NM_PER_UM
