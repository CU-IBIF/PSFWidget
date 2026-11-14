"""Tests for tilt, field curvature, uniformity, and chromatic registration (MET-11 to MET-13)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.io import parse_assignments, read_stack
from psfwidget.metrics.bead import BeadResult
from psfwidget.metrics.field import CHANNEL_METRICS, chromatic_shift, field_geometry
from psfwidget.tracking.settings import TRACKABLE_METRICS
from psfwidget.tracking.store import ResultsDatabase

VOXEL = (0.1, 0.05, 0.05)
SPEC = synthetic.FieldSpec(
    shape=(61, 256, 256),
    voxel_size_um=VOXEL,
    background_photons=20,
    offset=100,
    read_noise=2,
    bit_depth=12,
)
FWHM = (0.6, 0.22, 0.22)
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "instrument_id=scope",
]


def _bead(i: int, x: float, y: float, z: float, intensity: float = 1e5) -> BeadResult:
    return BeadResult(
        bead_id=i,
        z_px=0,
        y_px=0,
        x_px=0,
        centre_x_um=x,
        centre_y_um=y,
        centre_z_um=z,
        integrated_intensity=intensity,
    )


@pytest.mark.req("MET-11", "MET-12")
def test_field_geometry_on_exact_positions() -> None:
    size = (100.0, 80.0)
    rng = np.random.default_rng(0)
    points = rng.uniform((0, 0), size, (30, 2))
    slope_x, slope_y, k = 0.01, -0.005, 2e-4
    beads = []
    for i, (x, y) in enumerate(points):
        dx, dy = x - 50, y - 40
        z = 3 + slope_x * dx + slope_y * dy + k * (dx * dx + dy * dy)
        beads.append(_bead(i, x, y, z, intensity=1e5 * (1 - 1e-4 * (dx * dx + dy * dy))))
    geometry = field_geometry(beads, size)
    corner2 = 50**2 + 40**2
    assert geometry.curvature_sag_um == pytest.approx(k * corner2, rel=1e-6)
    assert geometry.tilt_deg == pytest.approx(
        math.degrees(math.atan(math.hypot(0.01, 0.005))), rel=0.05
    )
    assert geometry.uniformity_corner_ratio == pytest.approx(1 - 1e-4 * corner2, rel=1e-6)
    assert set(geometry.metrics()) == {
        "tilt_deg",
        "focal_range_um",
        "curvature_sag_um",
        "uniformity_corner_ratio",
    }
    assert set(CHANNEL_METRICS) <= set(TRACKABLE_METRICS)


@pytest.mark.req("MET-11")
def test_field_geometry_needs_enough_spread_beads() -> None:
    assert field_geometry([_bead(0, 1, 1, 1)], (10, 10)).tilt_deg is None
    clustered = [_bead(i, 5 + 0.1 * i, 5, 1) for i in range(5)]
    assert "too little of the field" in field_geometry(clustered, (10, 10)).notes[0]
    few = [_bead(i, x, y, 1) for i, (x, y) in enumerate([(1, 1), (9, 1), (5, 9), (1, 9)])]
    geometry = field_geometry(few, (10, 10))
    assert geometry.tilt_deg == pytest.approx(0, abs=1e-9)
    assert geometry.curvature_sag_um is None
    assert any("curvature needs" in n for n in geometry.notes)


@pytest.mark.req("MET-13")
def test_chromatic_shift_matches_mutual_neighbours() -> None:
    first = [_bead(i, 10 * i, 5, 2) for i in range(5)]
    second = [_bead(i, 10 * i + 0.03, 5 - 0.02, 2.1) for i in range(5)]
    second.append(_bead(9, 200, 200, 2))  # unmatched
    shift = chromatic_shift(first, second, reference_index=0, other_index=1, match_radius_um=0.5)
    assert shift is not None
    assert shift.n_pairs == 5
    assert (shift.dx_nm, shift.dy_nm, shift.dz_nm) == pytest.approx((30, -20, 100))
    assert shift.lateral_nm == pytest.approx(math.hypot(30, 20))
    assert (
        chromatic_shift(first, [], reference_index=0, other_index=1, match_radius_um=0.5) is None
    )


@pytest.mark.req("MET-11", "MET-13", "TRK-01")
def test_tilted_two_channel_field_end_to_end(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    field = synthetic.make_bead_field(
        SPEC, 14, FWHM, 6e4, rng, margin_fwhm=3.0, min_separation_fwhm=5.0
    )
    tilt = (0.0, 0.02)  # dz/dx = 0.02: 1.15°
    from dataclasses import replace

    in_focus = [replace(b, position_px=(30.0, *b.position_px[1:])) for b in field.beads]
    tilted = synthetic.apply_focal_surface(in_focus, VOXEL, centre_px=(128, 128), tilt=tilt)
    shifted = synthetic.shift_beads(tilted, VOXEL, (0.12, 0.0, 0.04))
    first = synthetic.render_field(SPEC, tilted, rng)
    second = synthetic.render_field(SPEC, shifted, rng)
    path = tmp_path / "two.tif"
    tifffile.imwrite(
        path,
        np.stack([first.data, second.data], axis=1),
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZCYX", "spacing": 0.1, "unit": "um"},
    )
    stack = read_stack(
        path,
        overrides=parse_assignments(
            [*OPTICS, "channel.0.emission_nm=520", "channel.1.emission_nm=580"]
        ),
    )
    result = analyze_stack(stack)
    reference, other = result.channels
    assert reference.field is not None
    assert reference.field.tilt_deg == pytest.approx(math.degrees(math.atan(0.02)), abs=0.15)
    assert reference.chromatic is None
    assert other.chromatic is not None
    assert other.chromatic.dx_nm == pytest.approx(40, abs=10)
    assert other.chromatic.dz_nm == pytest.approx(120, abs=25)
    assert other.channel_metrics["chromatic_axial_nm"] == other.chromatic.dz_nm
    # Channel metrics are stored and can be tracked like bead summaries.
    result = replace(result, provenance=replace(result.provenance, sha256="1" * 64))
    with ResultsDatabase(tmp_path / "qc.sqlite") as db:
        db.record(result)
        configuration = next(c for c in db.configurations() if c.channel_key.endswith("580"))
        points = db.points(configuration, "chromatic_axial_nm")
        assert points[0].value == pytest.approx(other.chromatic.dz_nm)


@pytest.mark.req("MET-11", "REP-01")
def test_field_map_figure() -> None:
    from psfwidget.report.figures import field_map

    beads = [
        BeadResult(
            bead_id=i,
            z_px=0,
            y_px=0,
            x_px=0,
            centre_x_um=x,
            centre_y_um=y,
            centre_z_um=0.01 * x,
            fwhm_x_um=0.22,
            fwhm_y_um=0.23,
        )
        for i, (x, y) in enumerate([(1, 1), (9, 1), (5, 9), (2, 7)])
    ]
    png = field_map(beads, (10.0, 10.0))
    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert field_map(beads[:2], (10.0, 10.0)) is None
