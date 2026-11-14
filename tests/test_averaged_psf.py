"""Tests for the averaged PSF, its export, and the comparison with theory (MET-14, THE-04, THE-05)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack, averaged_psf
from psfwidget.average import AverageError, average_beads, compare, write_ome_tiff
from psfwidget.io import parse_assignments, read_stack
from psfwidget.metrics.bead import BeadResult
from psfwidget.settings import AnalysisSettings
from psfwidget.theory.scalar import scalar_width

VOXEL = (0.1, 0.04, 0.04)
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
]


def _field(tmp_path: Path, scale: float, name: str) -> Path:
    theory = scalar_width("widefield", None, 520, 1.4, 1.518)
    spec = synthetic.FieldSpec(
        shape=(61, 200, 200),
        voxel_size_um=VOXEL,
        background_photons=10,
        offset=100,
        read_noise=2,
        bit_depth=16,
    )
    fwhm = (theory.axial_um * scale, theory.lateral_um * scale, theory.lateral_um * scale)
    field = synthetic.make_bead_field(
        spec, 10, fwhm, 2e5, np.random.default_rng(8), margin_fwhm=2.0, min_separation_fwhm=5.0
    )
    path = tmp_path / name
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(25, 25),
        metadata={"axes": "ZYX", "spacing": 0.1, "unit": "um"},
    )
    return path


@pytest.mark.req("MET-14", "THE-04", "THE-05")
def test_averaged_psf_compared_with_theory(tmp_path: Path) -> None:
    sharp = analyze_stack(
        read_stack(_field(tmp_path, 1.0, "a.tif"), overrides=parse_assignments(OPTICS))
    ).channels[0]
    blurred = analyze_stack(
        read_stack(_field(tmp_path, 1.5, "b.tif"), overrides=parse_assignments(OPTICS))
    ).channels[0]
    assert sharp.psf is not None
    assert blurred.psf is not None
    assert sharp.psf.n_beads >= 8
    assert sharp.psf.similarity > 0.95
    # A PSF 1.5 times wider in every direction has a much lower peak at equal energy
    # (1/1.5³ in an infinite box; less of a drop here because the box grows with the beads).
    assert blurred.psf.strehl / sharp.psf.strehl < 0.6
    assert blurred.psf.similarity < sharp.psf.similarity
    # The averaged PSF's own FWHM matches the bead FWHM.
    assert sharp.psf.fwhm_um[1] == pytest.approx(sharp.summary["fwhm_y_raw_um"].median, rel=0.08)
    assert sharp.channel_metrics["strehl"] == sharp.psf.strehl
    off = analyze_stack(
        read_stack(tmp_path / "a.tif", overrides=parse_assignments(OPTICS)),
        AnalysisSettings(compare_averaged_psf=False),
    ).channels[0]
    assert off.psf is None


@pytest.mark.req("MET-14", "THE-05")
def test_compare_identical_and_errors() -> None:
    grid = np.zeros((5, 7, 7))
    grid[2, 3, 3] = 2.0
    grid[2, 3, 2] = grid[2, 3, 4] = grid[1, 3, 3] = grid[3, 3, 3] = 1.0
    grid /= grid.sum()
    same = compare(grid, grid, 4, (0.1, 0.1, 0.1))
    assert same.strehl == pytest.approx(1.0)
    assert same.similarity == pytest.approx(1.0)
    with pytest.raises(AverageError, match="at least 3"):
        average_beads(np.zeros((10, 10, 10)), [BeadResult(1, 5, 5, 5)], (0.1, 0.1, 0.1), (2, 2, 2))


@pytest.mark.req("MET-14", "REP-04")
def test_ome_tiff_export_round_trip(tmp_path: Path) -> None:
    path = _field(tmp_path, 1.0, "a.tif")
    stack = read_stack(path, overrides=parse_assignments(OPTICS))
    channel = analyze_stack(stack).channels[0]
    average, used, _ = averaged_psf(
        stack.channel(0),
        channel.beads,
        stack.voxel_size_um,
        channel.expected_fwhm_um,
        AnalysisSettings(),
    )
    files = write_ome_tiff(
        average,
        tmp_path / "psf.ome.tif",
        voxel_size_um=VOXEL,
        name="beads channel 0",
        details={"n_beads": used},
    )
    with tifffile.TiffFile(files[0]) as tif:
        assert tif.is_ome
        data = tif.asarray()
        ome = tif.ome_metadata or ""
    assert data.dtype == np.float32
    assert data.shape == average.shape
    assert float(data.sum()) == pytest.approx(1.0, rel=1e-4)
    assert 'PhysicalSizeX="0.04"' in ome
    assert 'PhysicalSizeZ="0.1"' in ome
    assert files[1].name == "psf.json"


@pytest.mark.req("THE-04", "THE-05")
def test_beads_rendered_from_theory_have_a_strehl_of_one(tmp_path: Path) -> None:
    from psfwidget.theory.model import channel_theory
    from psfwidget.theory.psf3d import theoretical_psf

    template = read_stack(_field(tmp_path, 1.0, "t.tif"), overrides=parse_assignments(OPTICS))
    theory = channel_theory(template.metadata.channels[0], template.metadata)
    kernel = theoretical_psf(theory, VOXEL, (31, 41, 41))
    rng = np.random.default_rng(5)
    volume = np.zeros((61, 200, 200))
    for z, y, x in [(30, 40, 40), (30, 40, 150), (30, 150, 40), (30, 150, 150), (30, 95, 95)]:
        volume[z - 15 : z + 16, y - 20 : y + 21, x - 20 : x + 21] += 2e5 * kernel
    data = rng.poisson(volume + 10.0) + 100
    path = tmp_path / "airy.tif"
    tifffile.imwrite(
        path,
        data.astype(np.uint16),
        imagej=True,
        resolution=(25, 25),
        metadata={"axes": "ZYX", "spacing": 0.1, "unit": "um"},
    )
    channel = analyze_stack(
        read_stack(path, overrides=parse_assignments(OPTICS)), AnalysisSettings(outlier_mad=None)
    ).channels[0]
    assert channel.psf is not None
    assert channel.psf.n_beads == 5
    assert channel.psf.strehl == pytest.approx(1.0, abs=0.08)
    assert channel.psf.similarity > 0.99


@pytest.mark.req("MET-14", "REP-04", "AUT-01")
def test_cli_writes_averaged_psfs(tmp_path: Path) -> None:
    import json

    from psfwidget.automation import cli

    path = _field(tmp_path, 1.0, "a.tif")
    out = tmp_path / "out"
    arguments = ["analyze", str(path), "--out", str(out), "--psf"]
    for option in OPTICS:
        arguments += ["--set", option]
    assert cli.main(arguments) == 0
    tiff = next(out.glob("*_ch0_psf.ome.tif"))
    details = json.loads(next(out.glob("*_ch0_psf.json")).read_text("utf-8"))
    assert details["n_beads"] >= 8
    assert details["comparison"]["strehl"] > 0
    assert tifffile.imread(tiff).ndim == 3


@pytest.mark.req("MET-09")
def test_axial_asymmetry() -> None:
    from psfwidget.average import axial_asymmetry

    z = np.arange(21) - 10.0
    symmetric = np.exp(-(z**2) / 8)[:, None, None] * np.ones((1, 3, 3))
    skewness, ratio = axial_asymmetry(symmetric / symmetric.sum())
    assert skewness == pytest.approx(0.0, abs=1e-9)
    assert ratio == pytest.approx(1.0)
    tail = np.where(z > 0, np.exp(-(z**2) / 30), np.exp(-(z**2) / 4))[:, None, None]
    skewness, ratio = axial_asymmetry(tail * np.ones((1, 3, 3)))
    assert skewness > 0.3
    assert ratio > 1.5
