"""Sample data for napari: a synthetic bead field with known PSF (T7.4)."""

from __future__ import annotations

from typing import Any

import numpy as np

from psfwidget import synthetic

SAMPLE_FWHM_UM = (0.60, 0.22, 0.22)
"""z, y, x FWHM of the sample beads, close to a 1.4 NA widefield PSF at 520 nm."""


def bead_field(seed: int = 7) -> list[tuple[Any, dict[str, Any], str]]:
    """Return a 12-bit synthetic field of 12 beads as napari layer data."""
    spec = synthetic.FieldSpec(
        shape=(41, 160, 160),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec, 12, SAMPLE_FWHM_UM, 4e4, np.random.default_rng(seed),
        margin_fwhm=2.0, min_separation_fwhm=4.0,
    )
    return [
        (
            field.data,
            {
                "name": "synthetic beads",
                "scale": spec.voxel_size_um,
                "colormap": "gray",
                "metadata": {"fwhm_um": SAMPLE_FWHM_UM, "source": "psfwidget.synthetic"},
            },
            "image",
        )
    ]
