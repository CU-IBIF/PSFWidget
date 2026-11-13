"""Tests for candidate detection (DET-01)."""

from __future__ import annotations

import numpy as np
import pytest

from psfwidget import synthetic
from psfwidget.detect.finder import find_candidates

VOXEL = (0.15, 0.05, 0.05)
FWHM = (0.6, 0.2, 0.2)
EXPECTED = (0.2, 0.6)


def _field(n: int, seed: int, photons: float = 4e4) -> synthetic.SyntheticField:
    spec = synthetic.FieldSpec(
        shape=(31, 160, 160), voxel_size_um=VOXEL, background_photons=20, offset=100, read_noise=2
    )
    return synthetic.make_bead_field(
        spec,
        n,
        FWHM,
        photons,
        np.random.default_rng(seed),
        margin_fwhm=1.5,
        min_separation_fwhm=3.0,
    )


@pytest.mark.req("DET-01")
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_detects_every_bead_and_nothing_else(seed: int) -> None:
    field = _field(15, seed)
    found = find_candidates(
        field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.05, median_px=3
    )
    truth = np.array([b.position_px for b in field.beads])
    positions = np.array([c.position_px for c in found.candidates])
    assert len(positions) == len(truth)  # precision and recall 100%
    distance = np.abs(positions[:, None, :] - truth[None, :, :]).max(axis=2).min(axis=1)
    assert np.all(distance <= 1.0)  # each candidate within one voxel of a true bead
    heights = [c.height for c in found.candidates]
    assert heights == sorted(heights, reverse=True)
    assert found.threshold > found.background
    assert found.noise > 0


def test_median_filter_removes_hot_pixels() -> None:
    spec = synthetic.FieldSpec(
        shape=(21, 120, 120), voxel_size_um=VOXEL, background_photons=20, offset=100, read_noise=2
    )
    rng = np.random.default_rng(4)
    beads = [synthetic.BeadSpec((10.0, 60.0, 60.0), FWHM, 4e4)]
    field = synthetic.render_field(spec, beads, rng, n_hot_pixels=40)
    without = find_candidates(field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.0)
    with_median = find_candidates(
        field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.0, median_px=3
    )
    assert len(without.candidates) > 1  # hot pixels are picked up
    assert [c.position_px for c in with_median.candidates] == [(10, 60, 60)]


def test_maxima_on_the_outermost_layer_are_ignored() -> None:
    spec = synthetic.FieldSpec(
        shape=(21, 80, 80), voxel_size_um=VOXEL, background_photons=20, offset=100, read_noise=2
    )
    beads = [
        synthetic.BeadSpec((10.0, 40.0, 40.0), FWHM, 4e4),
        synthetic.BeadSpec((10.0, 0.0, 20.0), FWHM, 4e4),  # centred on the y edge
        synthetic.BeadSpec((0.0, 60.0, 60.0), FWHM, 4e4),  # centred on the first plane
        synthetic.BeadSpec((10.0, 79.0, 79.0), FWHM, 4e4),  # in the far corner
    ]
    field = synthetic.render_field(spec, beads, np.random.default_rng(6))
    result = find_candidates(field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.0)
    assert [c.position_px for c in result.candidates] == [(10, 40, 40)]


def test_empty_and_flat_volumes_have_no_candidates() -> None:
    flat = np.full((10, 40, 40), 100, dtype=np.uint16)
    result = find_candidates(flat, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.05)
    assert result.candidates == ()
    noise = np.random.default_rng(1).normal(100, 3, (15, 60, 60)).astype(np.float32)
    assert (
        find_candidates(noise, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.05).candidates
        == ()
    )


def test_relative_threshold_drops_faint_maxima_and_close_maxima_merge() -> None:
    spec = synthetic.FieldSpec(
        shape=(21, 100, 140), voxel_size_um=VOXEL, background_photons=20, offset=100, read_noise=2
    )
    beads = [
        synthetic.BeadSpec((10.0, 50.0, 30.0), FWHM, 1e5),
        synthetic.BeadSpec((10.0, 50.0, 100.0), FWHM, 3e3),
    ]
    field = synthetic.render_field(spec, beads, np.random.default_rng(2))
    both = find_candidates(field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.0)
    bright = find_candidates(field.data, VOXEL, EXPECTED, threshold_sd=8, relative_threshold=0.2)
    assert len(both.candidates) == 2
    assert [c.position_px for c in bright.candidates] == [(10, 50, 30)]
