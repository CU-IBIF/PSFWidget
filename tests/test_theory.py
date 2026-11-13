"""Tests for the theoretical PSF width (THE-01, THE-08, MET-16).

Hand calculations for NA 1.4, n 1.518:
  n − √(n² − NA²) = 1.518 − √(2.304324 − 1.96) = 1.518 − 0.586791 = 0.931209
  λ̄(488, 520) = √2 · 488 · 520 / √(488² + 520²) = 503.24 nm
"""

from __future__ import annotations

import pytest

from psfwidget.io import AcquisitionMetadata, ChannelMetadata, Modality, Sourced
from psfwidget.theory import (
    TheoryUnavailableError,
    analytic,
    channel_theory,
    nyquist_voxel_um,
    pinhole_airy_units,
    scalar_width,
)

NA, N = 1.4, 1.518


@pytest.mark.req("THE-01")
def test_widefield_formula_matches_hand_calculation() -> None:
    width = analytic.widefield(520, NA, N)
    assert width.lateral_um == pytest.approx(0.18943, abs=5e-6)  # 0.51 · 0.520 / 1.4
    assert width.axial_um == pytest.approx(0.49140, abs=5e-6)  # 0.88 · 0.520 / 0.931209
    assert width.regime == "widefield"
    assert width.lateral_formula == "0.51 λem / NA"
    assert "n − √(n² − NA²)" in width.axial_formula


@pytest.mark.req("THE-01")
def test_confocal_formulas_match_hand_calculation() -> None:
    assert analytic.mean_wavelength_nm(488, 520) == pytest.approx(503.24, abs=0.01)
    closed = analytic.confocal(488, 520, NA, N, 0.2)
    assert closed is not None
    assert closed.regime == "confocal-closed"
    assert closed.lateral_um == pytest.approx(0.13300, abs=5e-6)  # 0.37 · 0.50324 / 1.4
    assert closed.axial_um == pytest.approx(0.34587, abs=5e-6)  # 0.64 · 0.50324 / 0.931209

    opened = analytic.confocal(488, 520, NA, N, 1.0)
    assert opened is not None
    assert opened.regime == "confocal-open"
    assert opened.lateral_um == pytest.approx(0.17777, abs=5e-6)  # 0.51 · 0.488 / 1.4
    assert opened.axial_um == pytest.approx(0.46116, abs=5e-6)  # 0.88 · 0.488 / 0.931209
    assert analytic.confocal(488, 520, NA, N, 0.5) is None  # no formula between regimes

    assert analytic.airy_unit_um(515, NA) == pytest.approx(0.44879, abs=5e-6)  # 1.22·0.515/1.4


@pytest.mark.parametrize(
    "call",
    [
        lambda: analytic.widefield(520, 1.6, 1.518),
        lambda: analytic.widefield(520, 0.0, 1.518),
        lambda: analytic.widefield(-1, 1.4, 1.518),
        lambda: analytic.confocal(488, 520, NA, N, -0.1),
    ],
)
def test_analytic_rejects_impossible_input(call: object) -> None:
    with pytest.raises(ValueError, match=r"numerical aperture|wavelength|pinhole"):
        call()  # type: ignore[operator]


@pytest.mark.req("THE-01")
def test_scalar_widefield_lateral_is_the_airy_pattern() -> None:
    # At focus the scalar model reduces exactly to the Airy pattern, FWHM 0.5145 λ/NA.
    width = scalar_width("widefield", None, 520, NA, N)
    assert width.lateral_um == pytest.approx(0.5145 * 0.520 / NA, rel=2e-3)
    assert width.axial_um == pytest.approx(analytic.widefield(520, NA, N).axial_um, rel=0.06)
    assert width.model == "scalar widefield (emission PSF)"


@pytest.mark.req("THE-01")
@pytest.mark.parametrize(("na", "n"), [(1.4, 1.518), (0.8, 1.0), (1.2, 1.333)])
def test_scalar_point_detector_matches_closed_pinhole_formula(na: float, n: float) -> None:
    scalar = scalar_width("confocal", 488, 520, na, n, 0.0)
    closed = analytic.confocal_closed(488, 520, na, n)
    assert scalar.lateral_um == pytest.approx(closed.lateral_um, rel=0.01)
    assert scalar.axial_um == pytest.approx(closed.axial_um, rel=0.05)


@pytest.mark.req("THE-01")
def test_scalar_confocal_widens_with_pinhole_towards_the_open_limit() -> None:
    sizes = (0.0, 0.25, 0.5, 1.0, 2.0, 10.0)
    widths = [scalar_width("confocal", 488, 515, NA, N, au) for au in sizes]
    laterals = [w.lateral_um for w in widths]
    axials = [w.axial_um for w in widths]
    assert laterals[:4] == sorted(laterals[:4])
    assert axials == sorted(axials)
    opened = analytic.confocal_open(488, 515, NA, N)
    assert widths[-1].lateral_um == pytest.approx(opened.lateral_um, rel=0.02)
    assert widths[-1].axial_um == pytest.approx(opened.axial_um, rel=0.06)
    assert widths[3].model == "scalar confocal, pinhole 1.00 AU"


def test_spinning_disk_uses_the_confocal_model() -> None:
    disk = scalar_width("spinning_disk", 488, 515, NA, N, 1.65)
    point = scalar_width("confocal", 488, 515, NA, N, 1.65)
    assert (disk.lateral_um, disk.axial_um) == (point.lateral_um, point.axial_um)
    assert disk.model.startswith("scalar spinning-disk")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("widefield", None, 520, 1.6, 1.518), "below the refractive index"),
        (("lightsheet", 488, 520, 1.4, 1.518, 1.0), "unknown modality"),
        (("confocal", None, 520, 1.4, 1.518, 1.0), "excitation wavelength and pinhole"),
        (("confocal", 488, 520, 1.4, 1.518, None), "excitation wavelength and pinhole"),
        (("confocal", 488, 520, 1.4, 1.518, -1.0), "must not be negative"),
    ],
)
def test_scalar_rejects_bad_input(args: tuple[object, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        scalar_width(*args)


def _metadata(
    modality: Modality | None, **channel: object
) -> tuple[ChannelMetadata, AcquisitionMetadata]:
    values = {k: Sourced(v) for k, v in channel.items()}
    chan = ChannelMetadata(0, **values)  # type: ignore[arg-type]
    meta = AcquisitionMetadata(
        numerical_aperture=Sourced(NA),
        immersion_ri=Sourced(N),
        magnification=Sourced(60.0),
        modality=None if modality is None else Sourced(modality),
        channels=(chan,),
    )
    return chan, meta


@pytest.mark.req("THE-01")
def test_channel_theory_widefield_and_confocal() -> None:
    chan, meta = _metadata(Modality.WIDEFIELD, emission_nm=520.0)
    theory = channel_theory(chan, meta)
    assert theory.modality is Modality.WIDEFIELD
    assert theory.pinhole_au is None
    assert theory.analytic is not None
    assert theory.lateral_um == scalar_width("widefield", None, 520.0, NA, N).lateral_um
    assert theory.notes == ()

    chan, meta = _metadata(
        Modality.CONFOCAL, emission_nm=515.0, excitation_nm=488.0, pinhole_airy=0.5
    )
    theory = channel_theory(chan, meta)
    assert theory.pinhole_au == 0.5
    assert theory.analytic is None
    assert theory.notes == ("no closed-form formula between 0.25 and 1 AU",)


def test_pinhole_from_physical_size_states_the_assumption() -> None:
    chan, meta = _metadata(
        Modality.SPINNING_DISK, emission_nm=580.0, excitation_nm=561.0, pinhole_um=50.0
    )
    airy, how = pinhole_airy_units(chan, meta)
    # 50 µm / 60 = 0.8333 µm in the sample; 1 AU = 1.22 · 0.580 / 1.4 = 0.50543 µm
    assert airy == pytest.approx(0.8333333 / 0.5054286, rel=1e-6)
    assert "assumes no magnification between the objective and the pinhole" in how
    theory = channel_theory(chan, meta)
    assert theory.notes[0] == how


@pytest.mark.req("THE-08")
def test_resolution_enhancement_is_compared_with_the_base_model() -> None:
    chan, meta = _metadata(
        Modality.CONFOCAL, emission_nm=515.0, excitation_nm=488.0, pinhole_airy=1.0
    )
    plain = channel_theory(chan, meta)
    from dataclasses import replace

    enhanced = channel_theory(chan, replace(meta, resolution_enhancement=Sourced("NSPARC SR")))
    assert (enhanced.lateral_um, enhanced.axial_um) == (plain.lateral_um, plain.axial_um)
    assert enhanced.notes[-1] == (
        "NSPARC SR is not modelled; compared with the confocal model, so ratios below 1 "
        "are expected (THE-08)"
    )


@pytest.mark.parametrize(
    ("modality", "channel", "message"),
    [
        (None, {"emission_nm": 520.0}, "missing modality"),
        (Modality.WIDEFIELD, {}, "missing emission_nm"),
        (Modality.CONFOCAL, {"emission_nm": 520.0, "pinhole_airy": 1.0}, "missing excitation_nm"),
        (
            Modality.CONFOCAL,
            {"emission_nm": 520.0, "excitation_nm": 488.0},
            "pinhole size is unknown",
        ),
    ],
)
def test_channel_theory_reports_what_is_missing(
    modality: Modality | None, channel: dict[str, object], message: str
) -> None:
    chan, meta = _metadata(modality, **channel)
    with pytest.raises(TheoryUnavailableError, match=message):
        channel_theory(chan, meta)


@pytest.mark.req("MET-16")
def test_nyquist_voxel_size() -> None:
    chan, meta = _metadata(Modality.WIDEFIELD, emission_nm=520.0)
    theory = channel_theory(chan, meta)
    lateral, axial = nyquist_voxel_um(theory)
    assert lateral == pytest.approx(theory.lateral_um / 2.3)
    assert axial == pytest.approx(theory.axial_um / 2.3)
    assert nyquist_voxel_um(theory, 3.0)[0] == pytest.approx(theory.lateral_um / 3.0)
