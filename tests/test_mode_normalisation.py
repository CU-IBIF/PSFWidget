"""Tests for intensity normalised to the image mode (MET-04): 12- vs 16-bit data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.io import parse_assignments, read_stack
from psfwidget.metrics.intensity import image_mode

OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
]


@pytest.mark.req("MET-04")
def test_image_mode() -> None:
    data = np.array([[3, 3, 5], [5, 7, 3]], dtype=np.uint16)
    assert image_mode(data) == 3.0
    assert image_mode(np.array([5, 5, 2, 2], dtype=np.uint8)) == 2.0  # lowest on ties
    assert image_mode(np.array([-2, -2, 4], dtype=np.int16)) == -2.0
    floats = np.concatenate([np.full(100, 10.0), np.linspace(0, 1000, 50)])
    assert image_mode(floats) == pytest.approx(10.0, abs=0.2)


def _field(tmp_path: Path, name: str, scale: int) -> Path:
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        5,
        (0.6, 0.22, 0.22),
        3e4,
        np.random.default_rng(6),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    path = tmp_path / name
    data = (field.data.astype(np.uint32) * scale).astype(np.uint16)  # same counts, 16-bit scale
    tifffile.imwrite(
        path,
        data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    return path


@pytest.mark.req("MET-04")
def test_12_and_16_bit_images_agree_after_mode_normalisation(tmp_path: Path) -> None:
    twelve = analyze_stack(
        read_stack(
            _field(tmp_path, "a.tif", 1),
            overrides=(parse_assignments([*OPTICS, "significant_bits=12"])),
        )
    ).channels[0]
    sixteen = analyze_stack(
        read_stack(
            _field(tmp_path, "b.tif", 16),
            overrides=(parse_assignments([*OPTICS, "significant_bits=16"])),
        )
    ).channels[0]
    raw = (
        sixteen.summary["integrated_intensity"].median
        / twelve.summary["integrated_intensity"].median
    )
    assert raw == pytest.approx(16.0, rel=0.02)  # raw intensities differ by the scale
    assert twelve.image_mode is not None
    assert sixteen.image_mode == pytest.approx(16 * twelve.image_mode)
    for metric in ("integrated_intensity_per_mode", "peak_per_mode"):
        assert sixteen.channel_metrics[metric] == pytest.approx(
            twelve.channel_metrics[metric], rel=0.02
        ), metric
    assert twelve.channel_metrics["image_mode"] == twelve.image_mode


@pytest.mark.req("MET-04")
def test_zero_mode_is_not_normalised(tmp_path: Path) -> None:
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=0.05,
        offset=0,
        read_noise=0,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        5,
        (0.6, 0.22, 0.22),
        3e4,
        np.random.default_rng(6),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    path = tmp_path / "counting.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    channel = analyze_stack(read_stack(path, overrides=parse_assignments(OPTICS))).channels[0]
    assert channel.image_mode == 0.0
    assert "integrated_intensity_per_mode" not in channel.channel_metrics
    assert any("cannot be normalised" in w for w in channel.warnings)
