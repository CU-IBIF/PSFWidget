"""Tests for bead screening, measurement, and the analysis pipeline.

Requirements: DET-02 to DET-06, DET-08, MET-04 to MET-07, MET-10, MET-15, MET-16,
THE-03, THE-06, NFR-03.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack, saturation_level
from psfwidget.io import ImageStack, StackNotAnalyzableError, parse_assignments, read_stack
from psfwidget.metrics.bead import BeadResult, Geometry, half_box, measure_bead
from psfwidget.metrics.summary import describe, flag_outliers, rejection_counts, summarize
from psfwidget.profiles.spec import SpecLimits, Status
from psfwidget.settings import AnalysisSettings
from psfwidget.theory import scalar_width

VOXEL = (0.15, 0.05, 0.05)
FWHM = (0.60, 0.22, 0.22)
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
]


def _write(path: Path, data: np.ndarray, voxel: tuple[float, float, float] = VOXEL) -> Path:
    axes = "ZCYX" if data.ndim == 4 else "ZYX"
    tifffile.imwrite(
        path,
        data,
        imagej=True,
        resolution=(1 / voxel[2], 1 / voxel[1]),
        metadata={"axes": axes, "spacing": voxel[0], "unit": "um"},
    )
    return path


def _stack(path: Path, extra: list[str] | None = None) -> ImageStack:
    return read_stack(
        path, overrides=parse_assignments(OPTICS + (extra or [])), compute_hash=False
    )


def _spec(shape: tuple[int, int, int] = (41, 200, 200), bits: int = 12) -> synthetic.FieldSpec:
    return synthetic.FieldSpec(
        shape=shape,
        voxel_size_um=VOXEL,
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=bits,
    )


@pytest.fixture(scope="module")
def clean_field(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, synthetic.SyntheticField]:
    field = synthetic.make_bead_field(_spec(), 12, FWHM, 4e4, np.random.default_rng(11))
    return _write(tmp_path_factory.mktemp("clean") / "clean.tif", field.data), field


@pytest.mark.req("MET-01", "MET-02", "MET-04", "MET-05", "MET-06", "MET-07", "THE-03")
def test_clean_field_is_measured_correctly(
    clean_field: tuple[Path, synthetic.SyntheticField],
) -> None:
    path, field = clean_field
    result = analyze_stack(_stack(path))
    (channel,) = result.channels
    assert len(channel.detection.candidates) == 12
    assert len(channel.accepted) == 12
    assert channel.rejections == {}
    stats = channel.summary
    assert stats["fwhm_x_um"].median == pytest.approx(FWHM[2], rel=0.02)
    assert stats["fwhm_y_um"].median == pytest.approx(FWHM[1], rel=0.02)
    assert stats["fwhm_z_um"].median == pytest.approx(FWHM[0], rel=0.02)
    assert stats["xy_major_um"].median == pytest.approx(FWHM[2], rel=0.03)
    assert stats["xz_major_um"].median == pytest.approx(FWHM[0], rel=0.03)
    assert stats["xz_minor_um"].median == pytest.approx(FWHM[2], rel=0.03)
    assert stats["ellipticity_xy"].median > 0.95
    # Background: 20 photons + offset 100 counts.
    assert stats["background"].median == pytest.approx(120.0, rel=0.01)
    assert stats["snr"].median > 50
    assert stats["sbr"].median > 2
    # Integrated over an ellipsoid sized from theory, which is a little smaller than these beads.
    assert 0.9 * 4e4 < stats["integrated_intensity"].median < 4e4

    theory = channel.theory
    assert theory is not None
    expected = scalar_width("widefield", None, 520.0, 1.4, 1.518)
    assert (theory.lateral_um, theory.axial_um) == (expected.lateral_um, expected.axial_um)
    assert stats["ratio_x"].median == pytest.approx(FWHM[2] / theory.lateral_um, rel=0.02)
    assert stats["ratio_z"].median == pytest.approx(FWHM[0] / theory.axial_um, rel=0.02)
    bead = channel.accepted[0]
    truth = np.array([b.position_px for b in field.beads]) * np.array(VOXEL)
    measured = np.array([bead.centre_z_um, bead.centre_y_um, bead.centre_x_um])
    assert np.min(np.abs(truth - measured).max(axis=1)) < 0.02  # sub-voxel localisation


@pytest.mark.req("NFR-03")
def test_reanalysis_is_identical(clean_field: tuple[Path, synthetic.SyntheticField]) -> None:
    path, _ = clean_field
    first = analyze_stack(_stack(path))
    second = analyze_stack(_stack(path))
    assert first.channels[0].beads == second.channels[0].beads
    assert first.channels[0].summary == second.channels[0].summary


@pytest.mark.req("DET-02", "DET-03", "DET-04", "DET-05", "DET-08")
def test_defects_are_rejected_with_reasons(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    good = [synthetic.BeadSpec((20.0, 40.0 + 40 * i, 50.0), FWHM, 4e4) for i in range(4)]
    doublet = synthetic.make_doublet(
        synthetic.BeadSpec((20.0, 60.0, 130.0), FWHM, 4e4), (0.0, 0.0, 6.0)
    )
    edge = synthetic.BeadSpec((20.0, 2.0, 170.0), FWHM, 4e4, label="edge")
    saturated = synthetic.BeadSpec((20.0, 120.0, 130.0), FWHM, 4e6, label="saturated")
    # Bright enough to be detected but below SNR 10.
    faint = synthetic.BeadSpec((20.0, 170.0, 130.0), FWHM, 2.5e3, label="faint")
    field = synthetic.render_field(_spec(), [*good, *doublet, edge, saturated, faint], rng)
    channel = analyze_stack(_stack(_write(tmp_path / "defects.tif", field.data))).channels[0]

    by_position = {(b.y_px, b.x_px): b for b in channel.beads}

    def near(y: int, x: int) -> BeadResult:
        return next(
            b for (by, bx), b in by_position.items() if abs(by - y) <= 1 and abs(bx - x) <= 3
        )

    assert len(channel.accepted) == 4
    assert all(near(int(b.position_px[1]), 50).accepted for b in good)
    assert near(60, 127).reasons[0] == "another bead inside the measurement box"
    assert near(120, 130).reasons[0] == "saturated"
    assert near(120, 130).saturated is True
    assert near(170, 130).reasons[0].startswith("SNR")
    edge_bead = next(b for b in channel.beads if b.y_px <= 3)
    assert edge_bead.reasons[0] == "too close to the image border"
    assert channel.rejections == {
        "another bead inside the measurement box": 2,
        "low SNR": 1,
        "saturated": 1,
        "too close to the image border": 1,
    }
    # The TIFF does not state its bit depth; the clipping plateau gives it away.
    assert channel.saturation_level == 4095.0
    assert any("saturation taken as 4095" in w for w in channel.warnings)


def test_border_margin_allows_a_clipped_box_in_short_stacks(tmp_path: Path) -> None:
    # 9 planes: the full ±2 FWHM box (±8 planes) never fits, but a bead 4 planes in
    # is more than 1 FWHM from each end, so it is measured with a clipped box.
    field = synthetic.render_field(
        _spec((9, 80, 80)),
        [synthetic.BeadSpec((4.0, 40.0, 40.0), FWHM, 4e4)],
        np.random.default_rng(2),
    )
    (channel,) = analyze_stack(_stack(_write(tmp_path / "short.tif", field.data))).channels
    (bead,) = channel.beads
    assert bead.accepted
    assert bead.fwhm_z_um == pytest.approx(FWHM[0], rel=0.1)
    assert "measurement box clipped by the image edge along z" in bead.notes
    assert "integrated intensity not measured: too close to the image edge" in bead.notes
    assert bead.integrated_intensity is None


@pytest.mark.req("DET-06")
def test_outliers_are_flagged() -> None:
    beads = [
        BeadResult(
            i, 0, 0, 0, integrated_intensity=1000.0 + i, fwhm_x_um=0.2 + 0.001 * i, fwhm_y_um=0.2
        )
        for i in range(8)
    ]
    beads.append(BeadResult(8, 0, 0, 0, integrated_intensity=2500.0, fwhm_x_um=0.2, fwhm_y_um=0.2))
    beads.append(BeadResult(9, 0, 0, 0, integrated_intensity=1003.0, fwhm_x_um=0.4, fwhm_y_um=0.4))
    flagged = flag_outliers(beads, 4.0, 5)
    assert flagged[8].reasons == ("outlier: integrated intensity high",)
    assert flagged[9].reasons == ("outlier: lateral FWHM high",)
    assert all(b.accepted for b in flagged[:8])
    assert flag_outliers(beads[:4], 4.0, 5) == beads[:4]  # too few to judge
    assert rejection_counts(flagged) == {"outlier": 2}
    # Small differences among near-identical beads are photon noise, not outliers.
    close = [
        BeadResult(i, 0, 0, 0, integrated_intensity=1000.0 + 3 * i, fwhm_x_um=0.2, fwhm_y_um=0.2)
        for i in range(8)
    ]
    close.append(
        BeadResult(8, 0, 0, 0, integrated_intensity=1150.0, fwhm_x_um=0.23, fwhm_y_um=0.23)
    )
    assert all(b.accepted for b in flag_outliers(close, 4.0, 5))


def test_wide_beads_enlarge_the_measurement_box(tmp_path: Path) -> None:
    wide = (1.8, 0.22, 0.22)  # three times the theoretical axial width
    field = synthetic.make_bead_field(
        _spec((81, 200, 200)), 8, wide, 2e5, np.random.default_rng(9), min_separation_fwhm=2.5
    )
    (channel,) = analyze_stack(_stack(_write(tmp_path / "wide.tif", field.data))).channels
    assert channel.expected_fwhm_um[1] > 1.5
    assert any("measurement box was sized from the measured FWHM" in w for w in channel.warnings)
    assert channel.summary["fwhm_z_um"].median == pytest.approx(1.8, rel=0.05)


@pytest.mark.req("MET-15")
def test_bead_size_correction_is_applied_when_diameter_is_known(
    clean_field: tuple[Path, synthetic.SyntheticField],
) -> None:
    path, _ = clean_field
    raw = analyze_stack(_stack(path)).channels[0]
    corrected = analyze_stack(_stack(path, ["bead_diameter_nm=100"])).channels[0]
    bead = 0.5266 * 0.1
    assert any("bead diameter unknown" in w for w in raw.warnings)
    assert not any("bead diameter unknown" in w for w in corrected.warnings)
    first = corrected.accepted[0]
    assert first.fwhm_x_raw_um is not None
    assert first.fwhm_x_um is not None
    assert first.fwhm_x_um == pytest.approx(math.sqrt(first.fwhm_x_raw_um**2 - bead**2), rel=1e-3)
    off = analyze_stack(
        _stack(path, ["bead_diameter_nm=100"]), AnalysisSettings(bead_size_correction=False)
    ).channels[0]
    assert off.accepted[0].fwhm_x_um == off.accepted[0].fwhm_x_raw_um


@pytest.mark.req("THE-06")
def test_spec_limits_are_evaluated(clean_field: tuple[Path, synthetic.SyntheticField]) -> None:
    path, _ = clean_field
    passing = SpecLimits(
        lateral_ratio_max=1.3, axial_ratio_max=1.3, ellipticity_min=0.8, min_beads=5
    )
    failing = SpecLimits(lateral_ratio_max=1.1, min_beads=20)
    ok = analyze_stack(_stack(path), spec_limits=passing).channels[0].spec
    bad = analyze_stack(_stack(path), spec_limits=failing).channels[0].spec
    assert ok is not None
    assert ok.status is Status.PASS
    assert [c.status for c in ok.checks] == [Status.PASS] * 5
    assert bad is not None
    assert bad.status is Status.FAIL
    # Too few beads: the count fails and the ratio checks cannot be evaluated.
    assert [c.status for c in bad.checks] == [
        Status.FAIL,
        Status.NOT_EVALUATED,
        Status.NOT_EVALUATED,
    ]
    assert analyze_stack(_stack(path)).channels[0].spec is None


@pytest.mark.req("MET-16")
def test_undersampling_is_reported(tmp_path: Path) -> None:
    coarse = (0.5, 0.12, 0.12)
    spec = replace(_spec((15, 80, 80)), voxel_size_um=coarse)
    field = synthetic.render_field(
        spec,
        [synthetic.BeadSpec((7.0, 40.0, 40.0), (1.0, 0.3, 0.3), 4e4)],
        np.random.default_rng(1),
    )
    (channel,) = analyze_stack(
        _stack(_write(tmp_path / "coarse.tif", field.data, coarse))
    ).channels
    assert any(w.startswith("undersampled laterally: pixel 120 nm") for w in channel.warnings)
    assert any(w.startswith("undersampled axially: z step 500 nm") for w in channel.warnings)


def test_theory_unavailable_is_reported_and_measurement_continues(
    clean_field: tuple[Path, synthetic.SyntheticField],
) -> None:
    path, _ = clean_field
    stack = read_stack(
        path,
        compute_hash=False,
        overrides=parse_assignments(
            ["numerical_aperture=1.4", "immersion_ri=1.518", "channel.0.emission_nm=520"]
        ),
    )
    (channel,) = analyze_stack(stack).channels
    assert channel.theory is None
    assert channel.theory_unavailable == "missing modality"
    assert len(channel.accepted) == 12
    assert "ratio_x" not in channel.summary


def test_multichannel_selection_and_refusals(tmp_path: Path) -> None:
    field = synthetic.make_bead_field(
        _spec((31, 120, 120)), 4, FWHM, 4e4, np.random.default_rng(3)
    )
    data = np.stack([field.data, field.data], axis=1)
    path = _write(tmp_path / "two.tif", data)
    stack = _stack(path, ["channel.1.emission_nm=600"])
    result = analyze_stack(stack, channels=[1])
    assert [c.index for c in result.channels] == [1]
    with pytest.raises(IndexError, match="channel 2 out of range"):
        analyze_stack(stack, channels=[2])
    with pytest.raises(StackNotAnalyzableError, match="missing emission_nm for channel 1"):
        analyze_stack(_stack(path))


def test_empty_field_reports_no_beads(tmp_path: Path) -> None:
    field = synthetic.render_field(_spec((15, 60, 60)), [], np.random.default_rng(0))
    (channel,) = analyze_stack(_stack(_write(tmp_path / "empty.tif", field.data))).channels
    assert channel.beads == ()
    assert "no beads were detected" in channel.warnings
    assert channel.summary == {}


def test_saturation_level_uses_significant_bits(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.tif", np.zeros((3, 8, 8), dtype=np.uint16))
    assert saturation_level(_stack(path)) == 65535.0
    assert saturation_level(_stack(path, ["significant_bits=12"])) == 4095.0
    assert saturation_level(_stack(path, ["significant_bits=20"])) == 65535.0
    floating = _write(tmp_path / "f.tif", np.zeros((3, 8, 8), dtype=np.float32))
    assert saturation_level(_stack(floating)) == math.inf


def test_measure_bead_needs_background_voxels() -> None:
    settings = AnalysisSettings(min_background_voxels=10_000)
    expected = (0.22, 0.6)
    half = half_box(VOXEL, expected, settings)
    geometry = Geometry(VOXEL, expected, half, 4095.0, None, None, settings)
    volume = np.full(tuple(2 * h + 1 for h in half), 100, dtype=np.uint16)
    bead = measure_bead(volume, BeadResult(1, *half), geometry)
    assert bead.reasons == ("too few background voxels in the measurement box",)


def test_summary_statistics() -> None:
    stats = describe([1.0, 2.0, 3.0, 4.0, math.nan, math.inf])
    assert stats is not None
    assert (stats.n, stats.mean, stats.median, stats.q25, stats.q75) == (4, 2.5, 2.5, 1.75, 3.25)
    assert stats.iqr == 1.5
    assert stats.sd == pytest.approx(1.2910, abs=1e-4)
    assert describe([]) is None
    single = describe([5.0])
    assert single is not None
    assert math.isnan(single.sd)
    rejected = BeadResult(1, 0, 0, 0, fwhm_x_um=9.0).reject("x")
    assert summarize([rejected, BeadResult(2, 0, 0, 0, fwhm_x_um=1.0)])["fwhm_x_um"].median == 1.0


def test_settings_validation_and_round_trip() -> None:
    settings = AnalysisSettings(min_snr=5.0, min_sbr=2.0)
    assert AnalysisSettings.from_dict(settings.to_dict()) == settings
    with pytest.raises(ValueError, match="unknown analysis setting"):
        AnalysisSettings.from_dict({"colour": 1})
    for bad, message in (
        ({"crop_lateral_fwhm": 0}, "must be positive"),
        ({"detection_relative_threshold": 1.0}, r"\[0, 1\)"),
        ({"min_r_squared": 1.5}, r"\[0, 1\]"),
        ({"profile_width_px": 2}, "odd number"),
        ({"detection_median_px": 0}, "odd number"),
        ({"integration_radius_fwhm": 3.0}, "fit inside"),
        ({"min_border_fwhm": 3.0}, "must not exceed"),
    ):
        with pytest.raises(ValueError, match=message):
            AnalysisSettings(**bad)
