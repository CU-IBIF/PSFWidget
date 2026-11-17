"""Vectorial and refractive-index-mismatch PSF models (THE-02).

Two refinements of the scalar model in :mod:`psfwidget.theory.scalar`, used
when a profile or the metadata asks for them:

* **Vectorial (Richards–Wolf) focusing.** The focal field of an aplanatic
  objective (apodisation √cos θ) is built from the integrals

      I₀ = ∫ √cosθ (1 + cosθ) J₀(k r s) e^{iΦ} ρ dρ / cosθ
      I₁ = ∫ √cosθ sinθ        J₁(k r s) e^{iΦ} ρ dρ / cosθ
      I₂ = ∫ √cosθ (1 − cosθ) J₂(k r s) e^{iΦ} ρ dρ / cosθ

  with s = NA·ρ = n·sinθ, and the intensity for unpolarised (azimuthally
  averaged) light is |I₀|² + 2|I₁|² + |I₂|² (Richards and Wolf 1959;
  Novotny and Hecht, *Principles of Nano-Optics*, ch. 3). At low NA it
  reduces to the scalar model.

* **Refractive-index mismatch and coverslip** (Gibson and Lanni 1991). The
  pupil phase gains the optical path difference of a point at depth d in a
  sample of index n_s, and of a coverslip that differs from the objective's
  design (index and thickness):

      Φ = k [ z·√(n_i² − s²) + d·√(n_s² − s²) + t_g·√(n_g² − s²) − t_g*·√(n_g*² − s²) ]

  where z is the focus (stage) position. Rays beyond the critical angle
  (s > n_s) become evanescent. Mismatch makes the PSF axially asymmetric and
  shifts its focus, so widths are measured around the maximum, wherever it
  falls. When the NA exceeds the sample's index, the aperture is limited to
  it, as in the Gibson-Lanni model (supercritical-angle fluorescence of
  beads touching the coverslip is not modelled). Fresnel transmission at the
  interfaces is not included.

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
from psfwidget.theory.scalar import ScalarWidth, _disc_mask

DESIGN_COVERSLIP_RI = 1.523
"""Index of a standard #1.5H coverslip (the design value of most objectives)."""
DESIGN_COVERSLIP_UM = 170.0
_PUPIL_NODES = 400
_LATERAL_SAMPLES = 81
_AXIAL_SAMPLES = 801
_PINHOLE_NODES = 48


@dataclass(frozen=True)
class Optics:
    """How the theoretical PSF departs from the matched scalar model."""

    vectorial: bool = False
    sample_ri: float | None = None
    """Index around the beads; ``None`` means matched to the immersion."""
    depth_um: float = 0.0
    coverslip_ri: float = DESIGN_COVERSLIP_RI
    coverslip_um: float = DESIGN_COVERSLIP_UM

    @property
    def aberrated(self) -> bool:
        """Whether the pupil has a mismatch phase (sample depth or coverslip)."""
        depth = self.sample_ri is not None and self.depth_um > 0
        coverslip = (
            self.coverslip_ri != DESIGN_COVERSLIP_RI or self.coverslip_um != DESIGN_COVERSLIP_UM
        )
        return depth or coverslip

    @property
    def is_default(self) -> bool:
        """Whether this is the plain matched scalar model."""
        coverslip = (
            self.coverslip_ri != DESIGN_COVERSLIP_RI or self.coverslip_um != DESIGN_COVERSLIP_UM
        )
        return not self.vectorial and self.sample_ri is None and not coverslip

    def describe(self) -> str:
        """Return a short description for reports."""
        parts = ["vectorial (Richards-Wolf)" if self.vectorial else "scalar"]
        if self.sample_ri is not None and self.depth_um > 0:
            parts.append(f"sample n {self.sample_ri:g} at {self.depth_um:g} µm depth")
        if self.coverslip_ri != DESIGN_COVERSLIP_RI or self.coverslip_um != DESIGN_COVERSLIP_UM:
            parts.append(f"coverslip n {self.coverslip_ri:g}, {self.coverslip_um:g} µm")
        return ", ".join(parts)


@lru_cache(maxsize=4)
def _nodes(count: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    nodes, weights = np.polynomial.legendre.leggauss(count)
    return (nodes + 1.0) / 2.0, weights / 2.0


def _root(n: float, s: NDArray[np.float64]) -> NDArray[np.complex128]:
    """√(n² − s²), imaginary (evanescent) beyond the critical angle."""
    return np.asarray(np.sqrt((n**2 - s**2).astype(np.complex128)), dtype=np.complex128)


def intensity(
    r_um: NDArray[np.float64],
    z_um: NDArray[np.float64],
    wavelength_nm: float,
    numerical_aperture: float,
    refractive_index: float,
    optics: Optics,
) -> NDArray[np.float64]:
    """Return the intensity PSF on the outer grid of ``r_um`` and ``z_um`` (shape (r, z)).

    Values are relative; normalise as needed.
    """
    rho, weights = _nodes(_PUPIL_NODES)
    if optics.sample_ri is not None and optics.sample_ri < numerical_aperture:
        # Light beyond the sample's critical angle does not propagate; like the
        # Gibson-Lanni model, the aperture is limited to the sample's index
        # (supercritical-angle fluorescence at the interface is not modelled).
        limit = optics.sample_ri / numerical_aperture
        rho, weights = rho * limit, weights * limit
    k = 2.0 * math.pi / (wavelength_nm / NM_PER_UM)
    n = refractive_index
    s = numerical_aperture * rho
    phase_z = np.multiply.outer(z_um, _root(n, s))  # (z, ρ)
    extra = np.zeros_like(s, dtype=np.complex128)
    if optics.sample_ri is not None and optics.depth_um > 0:
        extra += optics.depth_um * _root(optics.sample_ri, s)
    extra += optics.coverslip_um * _root(optics.coverslip_ri, s)
    extra -= DESIGN_COVERSLIP_UM * _root(DESIGN_COVERSLIP_RI, s)
    pupil = np.exp(1j * k * (phase_z + extra[None, :]))  # (z, ρ)
    argument = k * np.multiply.outer(r_um, s)  # (r, ρ)
    if not optics.vectorial:
        weighted = pupil * (weights * rho)
        field = np.einsum("rp,zp->rz", special.j0(argument), weighted)
        return np.asarray(np.abs(field) ** 2, dtype=np.float64)
    cos_t = np.sqrt(np.clip(1.0 - (s / n) ** 2, 0.0, 1.0))
    sin_t = s / n
    base = weights * rho * np.sqrt(cos_t) / np.maximum(cos_t, 1e-12)
    total = np.zeros((r_um.size, z_um.size), dtype=np.float64)
    for order, angular, factor in (
        (0, 1.0 + cos_t, 1.0),
        (1, sin_t, 2.0),
        (2, 1.0 - cos_t, 1.0),
    ):
        bessel = special.jv(order, argument)
        field = np.einsum("rp,zp->rz", bessel, pupil * (base * angular))
        total += factor * np.abs(field) ** 2
    return total


def fwhm_around_peak(coordinates: NDArray[np.float64], profile: NDArray[np.float64]) -> float:
    """Return the FWHM of a single-peaked profile around its maximum, wherever it is.

    Raises
    ------
    ValueError
        If the profile does not fall to half its maximum on both sides.
    """
    peak = int(np.argmax(profile))
    half = profile[peak] / 2.0
    edges = []
    for direction in (-1, 1):
        index = peak
        while 0 <= index + direction < profile.size and profile[index + direction] > half:
            index += direction
        nxt = index + direction
        if not 0 <= nxt < profile.size:
            raise ValueError("profile does not fall to half maximum within the sampled range")
        a, b = profile[index], profile[nxt]
        x0, x1 = coordinates[index], coordinates[nxt]
        edges.append(x0 + (a - half) * (x1 - x0) / (a - b))
    return float(abs(edges[1] - edges[0]))


def paraxial_focus_um(refractive_index: float, optics: Optics) -> float:
    """Return where the focus moves for beads at depth (paraxial estimate, −d·n_i/n_s)."""
    if optics.sample_ri is None or optics.depth_um <= 0:
        return 0.0
    return -optics.depth_um * refractive_index / optics.sample_ri


@dataclass(frozen=True)
class OpticsWidth(ScalarWidth):
    """FWHM of a refined model, with where its axial maximum lies."""

    focus_um: float = 0.0
    """Stage position of the axial maximum relative to the nominal focus."""


def _detection(
    x: NDArray[np.float64],
    z: NDArray[np.float64],
    wavelength_nm: float,
    na: float,
    n: float,
    optics: Optics,
    pinhole_au: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return detection PSFs through the pinhole: lateral (x at z[0]) and axial (r = 0)."""
    radius = pinhole_au * airy_unit_um(wavelength_nm, na) / 2.0
    if radius <= 0:
        lateral = intensity(x, z[:1], wavelength_nm, na, n, optics)[:, 0]
        axial = intensity(np.zeros(1), z, wavelength_nm, na, n, optics)[0, :]
        return lateral, axial
    nodes, weights = np.polynomial.legendre.leggauss(_PINHOLE_NODES)
    r = (nodes + 1.0) / 2.0 * radius
    psf = intensity(r, z, wavelength_nm, na, n, optics)
    axial = np.asarray((weights * radius / 2.0 * 2.0 * math.pi * r) @ psf, dtype=np.float64)
    step = float(x[1] - x[0])
    half = math.ceil((float(x[-1]) + radius) / step)
    axis = (np.arange(2 * half + 1) - half) * step
    grid = np.hypot(axis[:, None], axis[None, :])
    # The PSF is radially symmetric: evaluate it on a fine radial line and interpolate.
    radial = np.arange(0.0, float(grid.max()) + step, step / 4.0)
    values = intensity(radial, z[:1], wavelength_nm, na, n, optics)[:, 0]
    plane = np.interp(grid, radial, values)
    disc = _disc_mask(2 * math.ceil(radius / step) + 3, step, radius)
    blurred = signal.fftconvolve(plane, disc, mode="same")
    return np.asarray(blurred[half, half : half + x.size], dtype=np.float64), axial


@lru_cache(maxsize=128)
def optics_width(
    modality: str,
    excitation_nm: float | None,
    emission_nm: float | None,
    numerical_aperture: float,
    refractive_index: float,
    pinhole_au: float | None,
    optics: Optics,
) -> OpticsWidth:
    """Return the FWHM of the vectorial and/or mismatched model (THE-02).

    Raises
    ------
    ValueError
        If a wavelength the modality needs is missing.
    """
    na, n = numerical_aperture, refractive_index
    wavelength = excitation_nm if modality == "multiphoton" else emission_nm
    if wavelength is None:
        raise ValueError(f"the {modality} model needs its wavelength")
    resolution = wavelength / NM_PER_UM / na
    x = np.linspace(0.0, 2.0 * resolution, _LATERAL_SAMPLES)
    depth_of_field = wavelength / NM_PER_UM / (n - math.sqrt(n**2 - na**2))
    span = 6.0 * depth_of_field + (2.0 * optics.depth_um if optics.aberrated else 0.0)
    centre = paraxial_focus_um(n, optics)
    z = np.linspace(centre - span, centre + span, _AXIAL_SAMPLES)

    def axial_and_lateral(
        z_focus: float | None,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        plane = np.array([0.0 if z_focus is None else z_focus])
        if modality == "widefield":
            assert emission_nm is not None
            axial = intensity(np.zeros(1), z, emission_nm, na, n, optics)[0, :]
            lateral = intensity(x, plane, emission_nm, na, n, optics)[:, 0]
            return axial, lateral
        if modality == "multiphoton":
            assert excitation_nm is not None
            axial = intensity(np.zeros(1), z, excitation_nm, na, n, optics)[0, :] ** 2
            lateral = intensity(x, plane, excitation_nm, na, n, optics)[:, 0] ** 2
            return axial, lateral
        if excitation_nm is None or emission_nm is None or pinhole_au is None:
            raise ValueError("confocal models need both wavelengths and the pinhole size")
        excitation_axial = intensity(np.zeros(1), z, excitation_nm, na, n, optics)[0, :]
        excitation_lateral = intensity(x, plane, excitation_nm, na, n, optics)[:, 0]
        _, detection_axial = _detection(x, z, emission_nm, na, n, optics, pinhole_au)
        detection_lateral, _ = _detection(x, plane, emission_nm, na, n, optics, pinhole_au)
        return excitation_axial * detection_axial, excitation_lateral * detection_lateral

    axial, _ = axial_and_lateral(None)
    focus = float(z[int(np.argmax(axial))])
    _, lateral = axial_and_lateral(focus)
    lateral_fwhm = fwhm_around_peak(
        np.concatenate([-x[:0:-1], x]), np.concatenate([lateral[:0:-1], lateral])
    )
    name = {"spinning_disk": "spinning-disk"}.get(modality, modality)
    pinhole = (
        f", pinhole {pinhole_au:.2f} AU"
        if pinhole_au is not None and modality in ("confocal", "spinning_disk")
        else ""
    )
    return OpticsWidth(
        lateral_um=lateral_fwhm,
        axial_um=fwhm_around_peak(z, axial),
        model=f"{optics.describe()} {name}{pinhole}",
        focus_um=focus,
    )
