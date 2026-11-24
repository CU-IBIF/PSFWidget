"""Tests for the vectorial and refractive-index-mismatch models (THE-02)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from psfwidget.io import InvalidMetadataError, parse_assignments
from psfwidget.io.metadata import apply_overrides
from psfwidget.io.model import AcquisitionMetadata, ChannelMetadata, Modality, Sourced
from psfwidget.profiles.model import ProfileError, parse_profile
from psfwidget.theory.model import channel_theory
from psfwidget.theory.optics import Optics, fwhm_around_peak, optics_width
from psfwidget.theory.psf3d import theoretical_psf
from psfwidget.theory.scalar import scalar_width

# Widefield, 520 nm, NA 1.4 oil (n 1.518) imaging beads in water (n 1.33) at
# depth d: FWHM from the exact Gibson-Lanni integration of MicroscPSF-Py 2.x
# (gLZRFocalScanSlow; independent of PSFWidget), used as the reference. It
# limits the aperture to the sample index, as PSFWidget does.
GIBSON_LANNI = {  # depth µm: (lateral µm, axial µm, focus µm)
    0.0: (0.2012, 0.6001, 0.0),
    2.0: (0.2170, 0.7893, -2.64),
    5.0: (0.2352, 1.0192, -6.31),
    10.0: (0.2553, 1.2912, -12.28),
}


@pytest.mark.req("THE-02")
@pytest.mark.parametrize(
    ("modality", "excitation", "emission", "na", "n", "pinhole"),
    [
        ("widefield", None, 520.0, 1.4, 1.518, None),
        ("confocal", 488.0, 520.0, 1.4, 1.518, 1.0),
        ("multiphoton", 920.0, None, 1.05, 1.333, None),
    ],
)
def test_matched_scalar_case_reproduces_the_scalar_model(
    modality: str,
    excitation: float | None,
    emission: float | None,
    na: float,
    n: float,
    pinhole: float | None,
) -> None:
    general = optics_width(modality, excitation, emission, na, n, pinhole, Optics())
    scalar = scalar_width(modality, excitation, emission or 0.0, na, n, pinhole)
    assert general.lateral_um == pytest.approx(scalar.lateral_um, rel=2e-3)
    assert general.axial_um == pytest.approx(scalar.axial_um, rel=2e-3)
    assert general.focus_um == pytest.approx(0.0, abs=0.01)


@pytest.mark.req("THE-02")
def test_vectorial_model_limits() -> None:
    for na in (0.1, 0.25):
        vectorial = optics_width("widefield", None, 520, na, 1.0, None, Optics(vectorial=True))
        scalar = scalar_width("widefield", None, 520, na, 1.0)
        assert vectorial.lateral_um == pytest.approx(scalar.lateral_um, rel=0.01)
        assert vectorial.axial_um == pytest.approx(scalar.axial_um, rel=0.01)
    # At high NA the unpolarised vectorial focus is wider laterally (depolarisation).
    high = optics_width("widefield", None, 520, 1.4, 1.518, None, Optics(vectorial=True))
    assert high.lateral_um > 1.05 * scalar_width("widefield", None, 520, 1.4, 1.518).lateral_um
    assert "vectorial" in high.model


@pytest.mark.req("THE-02")
@pytest.mark.parametrize("depth", sorted(GIBSON_LANNI))
def test_refractive_index_mismatch_agrees_with_gibson_lanni(depth: float) -> None:
    lateral, axial, focus = GIBSON_LANNI[depth]
    ours = optics_width(
        "widefield", None, 520, 1.4, 1.518, None, Optics(sample_ri=1.33, depth_um=depth)
    )
    assert ours.lateral_um == pytest.approx(lateral, rel=0.01)
    assert ours.axial_um == pytest.approx(axial, rel=0.01)
    assert ours.focus_um == pytest.approx(focus, abs=0.1)


@pytest.mark.req("THE-02")
def test_matched_sample_only_moves_the_focus() -> None:
    deep = optics_width(
        "widefield", None, 520, 1.2, 1.333, None, Optics(sample_ri=1.333, depth_um=8.0)
    )
    surface = scalar_width("widefield", None, 520, 1.2, 1.333)
    assert deep.lateral_um == pytest.approx(surface.lateral_um, rel=5e-3)
    assert deep.axial_um == pytest.approx(surface.axial_um, rel=5e-3)
    assert deep.focus_um == pytest.approx(-8.0, abs=0.02)


@pytest.mark.req("THE-02")
def test_fwhm_around_an_off_centre_peak() -> None:
    x = np.linspace(-5, 5, 1001)
    assert fwhm_around_peak(x, np.exp(-0.5 * ((x - 1.3) / 0.5) ** 2)) == pytest.approx(
        2.3548 * 0.5, rel=1e-3
    )
    with pytest.raises(ValueError, match="half maximum"):
        fwhm_around_peak(x, np.exp(-0.5 * ((x - 4.9) / 0.5) ** 2))


def _metadata(**extra: object) -> AcquisitionMetadata:
    channel = ChannelMetadata(index=0, emission_nm=Sourced(520.0))
    base = AcquisitionMetadata(
        numerical_aperture=Sourced(1.4),
        immersion_ri=Sourced(1.518),
        modality=Sourced(Modality.WIDEFIELD),
        channels=(channel,),
    )
    assignments = [f"{k}={v}" for k, v in extra.items()]
    return apply_overrides(base, parse_assignments(assignments), "user") if extra else base


@pytest.mark.req("THE-02", "IMP-05")
def test_channel_theory_uses_the_metadata_settings() -> None:
    plain = channel_theory(_metadata().channels[0], _metadata())
    assert plain.optics is None
    vectorial = _metadata(psf_model="vectorial")
    theory = channel_theory(vectorial.channels[0], vectorial)
    assert theory.optics is not None
    assert theory.optics.vectorial
    assert theory.lateral_um > plain.lateral_um
    water = _metadata(sample_ri=1.33)
    theory = channel_theory(water.channels[0], water)
    assert any("bead depth unknown" in note for note in theory.notes)
    deep = _metadata(sample_ri=1.33, bead_depth_um=5)
    theory = channel_theory(deep.channels[0], deep)
    assert theory.focus_um < -5
    assert "5 µm depth" in theory.model
    psf = theoretical_psf(theory, (0.1, 0.04, 0.04), (41, 31, 31))
    assert tuple(int(i) for i in np.unravel_index(int(np.argmax(psf)), psf.shape)) == (20, 15, 15)


@pytest.mark.req("THE-02", "IMP-05", "IMP-10")
def test_new_keys_are_validated() -> None:
    with pytest.raises(InvalidMetadataError, match="psf_model"):
        parse_assignments(["psf_model=wave"])
    with pytest.raises(InvalidMetadataError, match="bead_depth_um"):
        parse_assignments(["bead_depth_um=-1"])
    assert parse_assignments(["bead_depth_um=0"]).bead_depth_um == 0.0
    profile = parse_profile(
        {
            "instrument": {"id": "x"},
            "theory": {
                "model": "vectorial",
                "sample_ri": 1.46,
                "coverslip_um": 160,
                "bead_depth_um": 0,
            },
        }
    )
    assert (profile.psf_model, profile.sample_ri, profile.coverslip_um) == (
        "vectorial",
        1.46,
        160.0,
    )
    with pytest.raises(ProfileError):
        parse_profile({"instrument": {"id": "x"}, "theory": {"focus": 1}})
    warned = replace(_metadata(sample_ri=2.5))
    from psfwidget.io.metadata import plausibility_warnings

    assert any("sample refractive index" in w for w in plausibility_warnings(warned))


@pytest.mark.req("THE-02", "THE-03")
def test_both_models_are_computed_whichever_is_the_reference() -> None:
    """Every channel gets the scalar and the vectorial FWHM; the profile picks the reference."""
    plain = _metadata()
    scalar_ref = channel_theory(plain.channels[0], plain)
    assert not scalar_ref.reference_is_vectorial
    assert scalar_ref.scalar_lateral_um == scalar_ref.lateral_um
    assert scalar_ref.scalar_axial_um == scalar_ref.axial_um

    chosen = _metadata(psf_model="vectorial")
    vector_ref = channel_theory(chosen.channels[0], chosen)
    assert vector_ref.reference_is_vectorial
    assert vector_ref.vectorial_lateral_um == vector_ref.lateral_um
    assert vector_ref.vectorial_axial_um == vector_ref.axial_um

    # The same two numbers either way round, and they match the models computed directly.
    for theory in (scalar_ref, vector_ref):
        assert theory.scalar_lateral_um == pytest.approx(scalar_ref.lateral_um)
        assert theory.vectorial_lateral_um == pytest.approx(vector_ref.lateral_um)
        assert theory.vectorial_axial_um == pytest.approx(vector_ref.axial_um)
    assert vector_ref.lateral_um > 1.05 * scalar_ref.lateral_um  # depolarisation at NA 1.4

    # Refractive-index mismatch applies to both models.
    deep = _metadata(sample_ri=1.33, bead_depth_um=5)
    theory = channel_theory(deep.channels[0], deep)
    mismatch = Optics(vectorial=True, sample_ri=1.33, depth_um=5)
    direct = optics_width("widefield", None, 520.0, 1.4, 1.518, None, mismatch)
    assert theory.vectorial_lateral_um == pytest.approx(direct.lateral_um)
    assert theory.vectorial_axial_um == pytest.approx(direct.axial_um)
    assert theory.scalar_axial_um == pytest.approx(theory.axial_um)
