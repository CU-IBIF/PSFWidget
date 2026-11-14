"""Tests for the multiphoton (two-photon) theory (THE-10)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.io import parse_assignments, read_stack
from psfwidget.io._nd2 import _modality
from psfwidget.io.model import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    Sourced,
    missing_required,
)
from psfwidget.profiles.model import parse_profile
from psfwidget.settings import AnalysisSettings
from psfwidget.theory import analytic
from psfwidget.theory.model import TheoryUnavailableError, channel_theory
from psfwidget.theory.scalar import scalar_width


def _meta(excitation: float | None, emission: float | None = None) -> AcquisitionMetadata:
    channel = ChannelMetadata(
        index=0,
        excitation_nm=None if excitation is None else Sourced(excitation),
        emission_nm=None if emission is None else Sourced(emission),
    )
    return AcquisitionMetadata(
        voxel_z_um=Sourced(0.3),
        voxel_y_um=Sourced(0.1),
        voxel_x_um=Sourced(0.1),
        numerical_aperture=Sourced(1.05),
        immersion_ri=Sourced(1.333),
        modality=Sourced(Modality.MULTIPHOTON),
        channels=(channel,),
    )


@pytest.mark.req("THE-10")
def test_zipfel_formulas_by_hand() -> None:
    # 920 nm, NA 1.05 water: ω_xy = 0.325·0.92/(√2·1.05^0.91), ω_z = 0.532·0.92/√2/(n−√(n²−NA²)).
    width = analytic.multiphoton(920, 1.05, 1.333)
    radius_xy = 0.325 * 0.92 / (math.sqrt(2) * 1.05**0.91)
    radius_z = 0.532 * 0.92 / math.sqrt(2) / (1.333 - math.sqrt(1.333**2 - 1.05**2))
    factor = 2 * math.sqrt(math.log(2))
    assert width.lateral_um == pytest.approx(factor * radius_xy)
    assert width.axial_um == pytest.approx(factor * radius_z)
    assert width.lateral_um == pytest.approx(0.337, abs=0.001)
    assert width.axial_um == pytest.approx(1.126, abs=0.001)
    low = analytic.multiphoton(800, 0.6, 1.0)
    assert low.lateral_um == pytest.approx(factor * 0.320 * 0.8 / (math.sqrt(2) * 0.6))
    assert "0.320" in low.lateral_formula


@pytest.mark.req("THE-10", "THE-02")
@pytest.mark.parametrize(
    ("wavelength", "na", "n"),
    [(800, 0.8, 1.333), (920, 1.05, 1.333), (800, 1.2, 1.333), (780, 1.4, 1.518)],
)
def test_scalar_model_agrees_with_zipfel(wavelength: float, na: float, n: float) -> None:
    scalar = scalar_width("multiphoton", wavelength, 0.0, na, n)
    formula = analytic.multiphoton(wavelength, na, n)
    assert scalar.lateral_um == pytest.approx(formula.lateral_um, rel=0.07)
    assert scalar.axial_um == pytest.approx(formula.axial_um, rel=0.07)
    # Two-photon at λ is wider than one-photon widefield at λ/2 but narrower than at λ.
    assert scalar.lateral_um < scalar_width("widefield", None, wavelength, na, n).lateral_um


@pytest.mark.req("THE-10")
def test_channel_theory_needs_the_excitation_only() -> None:
    theory = channel_theory(_meta(920).channels[0], _meta(920))
    assert theory.modality is Modality.MULTIPHOTON
    assert theory.emission_nm is None
    assert theory.pinhole_au is None
    assert theory.analytic is not None
    assert theory.analytic.regime == "multiphoton"
    assert "two-photon" in theory.model
    with pytest.raises(TheoryUnavailableError, match="excitation_nm"):
        channel_theory(_meta(None, 520).channels[0], _meta(None, 520))
    short = channel_theory(_meta(460).channels[0], _meta(460))
    assert any("laser wavelength" in note for note in short.notes)


@pytest.mark.req("THE-10", "IMP-06")
def test_required_metadata_for_multiphoton() -> None:
    assert missing_required(_meta(920)) == []
    assert missing_required(_meta(None, 520)) == ["missing excitation_nm for channel 0"]


@pytest.mark.req("THE-10", "IMP-04")
def test_modality_from_files_and_profiles() -> None:
    assert _modality(["multiphoton", "laserScanConfocal"]) is Modality.MULTIPHOTON
    assert _modality(["laserScanConfocal"]) is Modality.CONFOCAL
    from psfwidget.io import _czi, _tiff

    assert _czi._MODALITIES["multiphotonmicroscopy"] is Modality.MULTIPHOTON
    assert _tiff._MODALITIES["multiphotonmicroscopy"] is Modality.MULTIPHOTON
    profile = parse_profile({"instrument": {"id": "mp", "modality": "multiphoton"}})
    assert profile.modality is Modality.MULTIPHOTON


@pytest.mark.req("THE-10", "THE-03")
def test_analysis_of_a_two_photon_field(tmp_path: Path) -> None:
    theory = scalar_width("multiphoton", 920, 0.0, 1.05, 1.333)
    spec = synthetic.FieldSpec(
        shape=(41, 128, 128),
        voxel_size_um=(0.2, 0.07, 0.07),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        6,
        (theory.axial_um, theory.lateral_um, theory.lateral_um),
        5e4,
        np.random.default_rng(2),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    path = tmp_path / "mp.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(1 / 0.07, 1 / 0.07),
        metadata={"axes": "ZYX", "spacing": 0.2, "unit": "um"},
    )
    stack = read_stack(
        path,
        overrides=parse_assignments(
            [
                "numerical_aperture=1.05",
                "immersion_ri=1.333",
                "modality=multiphoton",
                "channel.0.excitation_nm=920",
            ]
        ),
    )
    assert stack.problems() == []
    channel = analyze_stack(stack, AnalysisSettings()).channels[0]
    assert channel.theory is not None
    assert len(channel.accepted) >= 4
    for key in ("ratio_x", "ratio_y", "ratio_z"):
        assert channel.summary[key].median == pytest.approx(1.0, abs=0.06), key


@pytest.mark.req("THE-10", "IMP-05")
def test_plausibility_for_multiphoton_lasers() -> None:
    from dataclasses import replace

    from psfwidget.io.metadata import plausibility_warnings

    meta = _meta(1040, 520)
    assert plausibility_warnings(meta) == []  # laser longer than emission is normal
    one_photon = replace(meta, modality=Sourced(Modality.CONFOCAL))
    warnings = plausibility_warnings(one_photon)
    assert any("not longer than excitation" in w for w in warnings)
    assert any("outside 300-1400" in w for w in plausibility_warnings(_meta(1500)))
