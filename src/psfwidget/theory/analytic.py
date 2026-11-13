"""Analytical approximations of the theoretical PSF width (THE-01).

All lengths are in micrometres and wavelengths in nanometres. The formulas
are the standard ones used for microscope quality control:

Widefield, using the emission wavelength:

    lateral FWHM = 0.51 λ_em / NA
    axial FWHM   = 0.88 λ_em / (n − √(n² − NA²))

Confocal with a closed pinhole (≤ 0.25 Airy units), using the mean
wavelength λ̄ = √2 · λ_ex · λ_em / √(λ_ex² + λ_em²):

    lateral FWHM = 0.37 λ̄ / NA
    axial FWHM   = 0.64 λ̄ / (n − √(n² − NA²))

Confocal with an open pinhole (≥ 1 Airy unit), where the excitation PSF
sets the width of a point source:

    lateral FWHM = 0.51 λ_ex / NA
    axial FWHM   = 0.88 λ_ex / (n − √(n² − NA²))

Sources: the widefield, closed-pinhole, and open-pinhole lateral formulas are
from Wilhelm S, Gröbler B, Gluch M, Heinz H. *Confocal Laser Scanning
Microscopy: Principles*. Carl Zeiss, Jena. The open-pinhole axial formula is
the excitation-limited counterpart of the lateral one; it is the limit of the
numerical model in :mod:`psfwidget.theory.scalar` as the pinhole opens.

The same publication gives an open-pinhole axial formula with a pinhole
term, √[(0.88 λ_em / (n − √(n² − NA²)))² + (√2 · n · PH / NA)²]. That formula
is the optical section thickness for a fluorescent *layer*, which grows with
the pinhole. A bead is a point source, whose axial width does not, so that
formula is not used here.

Between 0.25 and 1 AU there is no closed formula. The measured/theoretical
ratios therefore use the numerical model, which handles any pinhole size, and
the analytical values are reported alongside it for reference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NM_PER_UM = 1000.0
AIRY_DIAMETER_FACTOR = 1.22
"""One Airy unit is a pinhole diameter of 1.22 λ / NA projected into the sample."""
CLOSED_PINHOLE_AU = 0.25
OPEN_PINHOLE_AU = 1.0


@dataclass(frozen=True)
class AnalyticWidth:
    """A theoretical FWHM pair with the formula that produced it."""

    lateral_um: float
    axial_um: float
    regime: str
    """``widefield``, ``confocal-closed`` (≤ 0.25 AU), or ``confocal-open`` (≥ 1 AU)."""
    lateral_formula: str
    axial_formula: str


def _check(numerical_aperture: float, refractive_index: float, *wavelengths_nm: float) -> None:
    if not 0 < numerical_aperture < refractive_index:
        raise ValueError(
            f"numerical aperture must be positive and below the refractive index "
            f"({numerical_aperture:g} vs {refractive_index:g})"
        )
    if any(w <= 0 for w in wavelengths_nm):
        raise ValueError("wavelengths must be positive")


def axial_denominator(numerical_aperture: float, refractive_index: float) -> float:
    """Return ``n − √(n² − NA²)``, the axial scale factor of a high-NA lens."""
    return refractive_index - math.sqrt(refractive_index**2 - numerical_aperture**2)


def mean_wavelength_nm(excitation_nm: float, emission_nm: float) -> float:
    """Return λ̄ = √2 · λ_ex · λ_em / √(λ_ex² + λ_em²)."""
    return math.sqrt(2.0) * excitation_nm * emission_nm / math.hypot(excitation_nm, emission_nm)


def airy_unit_um(wavelength_nm: float, numerical_aperture: float) -> float:
    """Return the diameter of one Airy unit projected into the sample, in µm."""
    return AIRY_DIAMETER_FACTOR * wavelength_nm / NM_PER_UM / numerical_aperture


def widefield(
    emission_nm: float, numerical_aperture: float, refractive_index: float
) -> AnalyticWidth:
    """Return the widefield FWHM."""
    _check(numerical_aperture, refractive_index, emission_nm)
    wavelength = emission_nm / NM_PER_UM
    return AnalyticWidth(
        lateral_um=0.51 * wavelength / numerical_aperture,
        axial_um=0.88 * wavelength / axial_denominator(numerical_aperture, refractive_index),
        regime="widefield",
        lateral_formula="0.51 λem / NA",
        axial_formula="0.88 λem / (n − √(n² − NA²))",
    )


def confocal_closed(
    excitation_nm: float,
    emission_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> AnalyticWidth:
    """Return the confocal FWHM for a pinhole of at most 0.25 AU."""
    _check(numerical_aperture, refractive_index, excitation_nm, emission_nm)
    wavelength = mean_wavelength_nm(excitation_nm, emission_nm) / NM_PER_UM
    return AnalyticWidth(
        lateral_um=0.37 * wavelength / numerical_aperture,
        axial_um=0.64 * wavelength / axial_denominator(numerical_aperture, refractive_index),
        regime="confocal-closed",
        lateral_formula="0.37 λ̄ / NA, λ̄ = √2 λex λem / √(λex² + λem²)",
        axial_formula="0.64 λ̄ / (n − √(n² − NA²))",
    )


def confocal_open(
    excitation_nm: float,
    emission_nm: float,
    numerical_aperture: float,
    refractive_index: float,
) -> AnalyticWidth:
    """Return the confocal FWHM of a point source for a pinhole of at least 1 AU."""
    _check(numerical_aperture, refractive_index, excitation_nm, emission_nm)
    wavelength = excitation_nm / NM_PER_UM
    return AnalyticWidth(
        lateral_um=0.51 * wavelength / numerical_aperture,
        axial_um=0.88 * wavelength / axial_denominator(numerical_aperture, refractive_index),
        regime="confocal-open",
        lateral_formula="0.51 λex / NA",
        axial_formula="0.88 λex / (n − √(n² − NA²))",
    )


def confocal(
    excitation_nm: float,
    emission_nm: float,
    numerical_aperture: float,
    refractive_index: float,
    pinhole_au: float,
) -> AnalyticWidth | None:
    """Return the formula for the pinhole regime, or ``None`` between 0.25 and 1 AU."""
    if pinhole_au < 0:
        raise ValueError("pinhole size must not be negative")
    if pinhole_au <= CLOSED_PINHOLE_AU:
        return confocal_closed(excitation_nm, emission_nm, numerical_aperture, refractive_index)
    if pinhole_au >= OPEN_PINHOLE_AU:
        return confocal_open(excitation_nm, emission_nm, numerical_aperture, refractive_index)
    return None
