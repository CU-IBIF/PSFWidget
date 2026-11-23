"""Theoretical PSFs and empirical-vs-theoretical comparison (WP3).

Requirements: THE-01 to THE-10, including the vectorial and Gibson-Lanni
models (THE-02). How the models work is explained in ``docs/THEORY.md``.
"""

from psfwidget.theory.analytic import AnalyticWidth
from psfwidget.theory.model import (
    Theory,
    TheoryUnavailableError,
    channel_theory,
    nyquist_voxel_um,
    pinhole_airy_units,
)
from psfwidget.theory.scalar import ScalarWidth, scalar_width

__all__ = [
    "AnalyticWidth",
    "ScalarWidth",
    "Theory",
    "TheoryUnavailableError",
    "channel_theory",
    "nyquist_voxel_um",
    "pinhole_airy_units",
    "scalar_width",
]
