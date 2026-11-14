"""Tests for the image-scanning (pixel-reassignment) model (THE-09)."""

from __future__ import annotations

import pytest

from psfwidget.io.model import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    Sourced,
)
from psfwidget.theory.model import channel_theory
from psfwidget.theory.scalar import ism_width, scalar_width


@pytest.mark.req("THE-09")
def test_ism_limits() -> None:
    closed = scalar_width("confocal", 488, 520, 1.4, 1.518, 0.0)
    point = ism_width(488, 520, 1.4, 1.518, 0.0)
    assert point.lateral_um == pytest.approx(closed.lateral_um, rel=1e-3)
    assert point.axial_um == pytest.approx(closed.axial_um, rel=1e-3)
    widefield = scalar_width("widefield", None, 520, 1.4, 1.518)
    one_au = ism_width(488, 520, 1.4, 1.518, 1.0)
    # Published: image scanning with about 1 AU gives about 1.5x widefield resolution.
    assert widefield.lateral_um / one_au.lateral_um == pytest.approx(1.53, abs=0.08)
    # ...and is sharper laterally than a 1 AU confocal pinhole.
    assert one_au.lateral_um < scalar_width("confocal", 488, 520, 1.4, 1.518, 1.0).lateral_um
    with pytest.raises(ValueError, match="negative"):
        ism_width(488, 520, 1.4, 1.518, -1)


@pytest.mark.req("THE-09", "THE-08")
def test_resolution_enhanced_channels_get_both_models() -> None:
    channel = ChannelMetadata(
        index=0,
        excitation_nm=Sourced(488.0),
        emission_nm=Sourced(520.0),
        pinhole_airy=Sourced(1.0),
    )
    meta = AcquisitionMetadata(
        numerical_aperture=Sourced(1.4),
        immersion_ri=Sourced(1.518),
        modality=Sourced(Modality.CONFOCAL),
        resolution_enhancement=Sourced("NSPARC SR"),
        channels=(channel,),
    )
    theory = channel_theory(channel, meta)
    assert theory.ism_lateral_um is not None
    assert theory.ism_axial_um is not None
    assert theory.ism_lateral_um < theory.lateral_um
    assert any("image-scanning model" in note for note in theory.notes)
    from dataclasses import replace

    plain = channel_theory(channel, replace(meta, resolution_enhancement=None))
    assert plain.ism_lateral_um is None
