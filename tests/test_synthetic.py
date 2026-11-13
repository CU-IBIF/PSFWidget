"""Tests for the synthetic bead-field generator (T0.4)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from psfwidget import synthetic
from psfwidget.synthetic import FWHM_PER_SIGMA, BeadSpec, FieldSpec

VOXEL = (0.15, 0.05, 0.05)
FWHM = (0.60, 0.22, 0.20)


def _moments(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the centroid and covariance matrix (z, y, x) of an image."""
    grids = np.meshgrid(*(np.arange(n) for n in image.shape), indexing="ij")
    total = image.sum()
    centroid = np.array([(g * image).sum() / total for g in grids])
    deltas = [g - c for g, c in zip(grids, centroid, strict=True)]
    cov = np.array([[(a * b * image).sum() / total for b in deltas] for a in deltas])
    return centroid, cov


def test_noise_free_bead_has_requested_position_width_and_photons() -> None:
    bead = BeadSpec(position_px=(20.3, 40.7, 35.2), fwhm_um=FWHM, photons=5e4)
    image = synthetic.render_signal((41, 81, 71), VOXEL, [bead])

    assert image.sum() == pytest.approx(bead.photons, rel=1e-4)
    centroid, cov = _moments(image)
    np.testing.assert_allclose(centroid, bead.position_px, atol=1e-3)
    fwhm_um = FWHM_PER_SIGMA * np.sqrt(np.diag(cov)) * np.array(VOXEL)
    np.testing.assert_allclose(fwhm_um, FWHM, rtol=1e-3)


def test_peak_photons_matches_rendered_peak_for_centred_bead() -> None:
    bead = BeadSpec(position_px=(10.0, 20.0, 20.0), fwhm_um=FWHM, photons=1e4)
    image = synthetic.render_signal((21, 41, 41), VOXEL, [bead])
    assert image[10, 20, 20] == pytest.approx(bead.peak_photons(VOXEL), rel=1e-12)
    assert image.max() == image[10, 20, 20]


@pytest.mark.parametrize("angle", [0.0, 30.0, 90.0, 135.0])
def test_lateral_rotation_sets_orientation_of_major_axis(angle: float) -> None:
    bead = BeadSpec(
        position_px=(10.0, 40.0, 40.0),
        fwhm_um=(0.6, 0.20, 0.40),  # x is the major lateral axis
        photons=1e4,
        angle_xy_deg=angle,
    )
    image = synthetic.render_signal((21, 81, 81), VOXEL, [bead])
    _, cov = _moments(image)
    lateral = cov[1:, 1:]  # [[yy, yx], [xy, xx]]
    eigenvalues, eigenvectors = np.linalg.eigh(lateral)
    major_y, major_x = eigenvectors[:, 1]
    measured = math.degrees(math.atan2(major_y, major_x)) % 180.0
    assert measured == pytest.approx(angle % 180.0, abs=0.05)
    widths_um = FWHM_PER_SIGMA * np.sqrt(eigenvalues) * VOXEL[1]
    np.testing.assert_allclose(widths_um, (0.20, 0.40), rtol=2e-3)


def test_oversampling_conserves_photons_and_centroid() -> None:
    bead = BeadSpec(position_px=(8.4, 15.6, 15.1), fwhm_um=FWHM, photons=2e4)
    point = synthetic.render_signal((17, 31, 31), VOXEL, [bead])
    integrated = synthetic.render_signal((17, 31, 31), VOXEL, [bead], oversample=3)
    assert integrated.sum() == pytest.approx(bead.photons, rel=1e-4)
    np.testing.assert_allclose(_moments(integrated)[0], _moments(point)[0], atol=1e-3)
    assert integrated.max() < point.max() or bead.position_px != (8.0, 15.0, 15.0)


def test_bead_outside_image_is_ignored_and_edge_bead_is_cropped() -> None:
    outside = BeadSpec(position_px=(500.0, 500.0, 500.0), fwhm_um=FWHM, photons=1e4)
    # Centred on the lower y face of the image, so exactly half lies inside.
    edge = BeadSpec(position_px=(5.0, -0.5, 10.0), fwhm_um=FWHM, photons=1e4, label="edge")
    image = synthetic.render_signal((11, 21, 21), VOXEL, [outside, edge])
    assert image.sum() == pytest.approx(0.5 * edge.photons, rel=5e-3)


def test_camera_model_background_statistics() -> None:
    spec = FieldSpec(
        shape=(20, 100, 100),
        voxel_size_um=VOXEL,
        background_photons=50.0,
        gain=2.0,
        offset=100.0,
        read_noise=3.0,
        bit_depth=16,
    )
    field = synthetic.render_field(spec, [], np.random.default_rng(1))
    n = field.data.size
    expected_mean = 2.0 * 50.0 + 100.0
    expected_var = 2.0**2 * 50.0 + 3.0**2 + 1.0 / 12.0  # shot + read + rounding
    assert field.data.mean() == pytest.approx(expected_mean, abs=5 * math.sqrt(expected_var / n))
    assert field.data.var() == pytest.approx(expected_var, rel=0.03)
    np.testing.assert_allclose(field.expected_counts, expected_mean)


def test_noise_can_be_disabled() -> None:
    spec = FieldSpec(
        shape=(9, 21, 21), voxel_size_um=VOXEL, read_noise=0.0, shot_noise=False, bit_depth=16
    )
    bead = BeadSpec(position_px=(4.0, 10.0, 10.0), fwhm_um=FWHM, photons=1e5)
    field = synthetic.render_field(spec, [bead], np.random.default_rng(0))
    np.testing.assert_array_equal(field.data, np.rint(field.expected_counts).astype(np.uint16))


def test_bright_bead_saturates_at_bit_depth() -> None:
    spec = FieldSpec(shape=(9, 21, 21), voxel_size_um=VOXEL, bit_depth=12)
    bead = BeadSpec(position_px=(4.0, 10.0, 10.0), fwhm_um=FWHM, photons=5e6)
    field = synthetic.render_field(spec, [bead], np.random.default_rng(0))
    assert field.data.max() == 4095
    assert field.saturated[4, 10, 10]
    assert field.expected_counts.max() > 4095


def test_hot_pixels_are_recorded() -> None:
    spec = FieldSpec(shape=(5, 30, 30), voxel_size_um=VOXEL)
    field = synthetic.render_field(spec, [], np.random.default_rng(3), n_hot_pixels=4)
    assert len(field.hot_pixels) == 4
    assert all(field.data[p] == spec.max_count for p in field.hot_pixels)
    assert int(field.saturated.sum()) == 4


def test_same_seed_gives_identical_field() -> None:
    spec = FieldSpec(shape=(31, 96, 96), voxel_size_um=VOXEL)
    first = synthetic.make_bead_field(spec, 5, FWHM, 2e4, np.random.default_rng(42))
    second = synthetic.make_bead_field(spec, 5, FWHM, 2e4, np.random.default_rng(42))
    other = synthetic.make_bead_field(spec, 5, FWHM, 2e4, np.random.default_rng(43))
    np.testing.assert_array_equal(first.data, second.data)
    assert first.beads == second.beads
    assert not np.array_equal(first.data, other.data)


def test_random_positions_respect_margin_and_separation() -> None:
    shape = (40, 200, 200)
    margin = (6.0, 12.0, 12.0)
    separation = (16.0, 18.0, 18.0)
    positions = np.array(
        synthetic.random_positions(
            shape,
            25,
            np.random.default_rng(7),
            margin_px=margin,
            min_separation_px=separation,
        )
    )
    assert positions.shape == (25, 3)
    assert np.all(positions >= np.array(margin))
    assert np.all(positions <= np.array(shape) - 1 - np.array(margin))
    for i in range(len(positions)):
        for j in range(i):
            scaled = (positions[i] - positions[j]) / np.array(separation)
            assert np.sum(scaled**2) >= 1.0


def test_random_positions_reports_impossible_requests() -> None:
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="could not place"):
        synthetic.random_positions(
            (10, 20, 20), 50, rng, min_separation_px=(8.0, 15.0, 15.0), max_tries=500
        )
    with pytest.raises(ValueError, match="no room"):
        synthetic.random_positions((10, 20, 20), 1, rng, margin_px=(6.0, 0.0, 0.0))


def test_doublet_is_symmetric_and_labelled() -> None:
    bead = BeadSpec(position_px=(10.0, 20.0, 30.0), fwhm_um=FWHM, photons=1e4)
    first, second = synthetic.make_doublet(bead, (0.0, 0.0, 6.0))
    assert first.position_px == (10.0, 20.0, 27.0)
    assert second.position_px == (10.0, 20.0, 33.0)
    assert first.label == second.label == "doublet"
    assert first.photons == second.photons == bead.photons


def test_focal_surface_tilt_and_curvature() -> None:
    beads = [
        BeadSpec(position_px=(10.0, 50.0, 50.0), fwhm_um=FWHM, photons=1.0),
        BeadSpec(position_px=(10.0, 50.0, 150.0), fwhm_um=FWHM, photons=1.0),
        BeadSpec(position_px=(10.0, 150.0, 50.0), fwhm_um=FWHM, photons=1.0),
    ]
    tilted = synthetic.apply_focal_surface(
        beads, VOXEL, centre_px=(50.0, 50.0), tilt=(0.02, -0.01)
    )
    # 100 px = 5 um lateral; dz = slope * 5 um, converted to 0.15 um planes.
    assert tilted[0].position_px[0] == pytest.approx(10.0)
    assert tilted[1].position_px[0] == pytest.approx(10.0 - 0.01 * 5.0 / 0.15)
    assert tilted[2].position_px[0] == pytest.approx(10.0 + 0.02 * 5.0 / 0.15)

    curved = synthetic.apply_focal_surface(
        beads, VOXEL, centre_px=(50.0, 50.0), curvature_per_um=0.01
    )
    assert curved[1].position_px[0] == pytest.approx(10.0 + 0.01 * 25.0 / 0.15)
    assert curved[1].position_px[1:] == beads[1].position_px[1:]


def test_shift_beads_converts_micrometres_to_voxels() -> None:
    bead = BeadSpec(position_px=(10.0, 20.0, 30.0), fwhm_um=FWHM, photons=1.0)
    (shifted,) = synthetic.shift_beads([bead], VOXEL, (0.30, 0.0, -0.10))
    assert shifted.position_px == pytest.approx((12.0, 20.0, 28.0))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"shape": (0, 10, 10)},
        {"voxel_size_um": (0.1, 0.0, 0.1)},
        {"bit_depth": 17},
        {"gain": 0.0},
        {"read_noise": -1.0},
    ],
)
def test_invalid_field_spec_is_rejected(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"shape": (5, 10, 10), "voxel_size_um": VOXEL}
    with pytest.raises(ValueError, match=r"shape|voxel|bit_depth|gain"):
        FieldSpec(**(base | kwargs))  # type: ignore[arg-type]


def test_invalid_render_arguments_are_rejected() -> None:
    bead = BeadSpec(position_px=(2.0, 5.0, 5.0), fwhm_um=(0.0, 0.2, 0.2), photons=1.0)
    with pytest.raises(ValueError, match="widths"):
        synthetic.render_signal((5, 10, 10), VOXEL, [bead])
    with pytest.raises(ValueError, match="oversample"):
        synthetic.render_signal((5, 10, 10), VOXEL, [], oversample=0)
