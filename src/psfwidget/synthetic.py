"""Synthetic bead fields with known ground truth (T0.4; datasets SYN-A and SYN-C).

Beads are rendered as 3D Gaussians with independent axial and lateral widths
and an optional rotation of the lateral axes. The camera model converts
photons to counts with a gain and offset, adds Poisson shot noise and
Gaussian read noise, and clips to the detector bit depth, so saturation
arises naturally from bright beads.

All coordinates are ``(z, y, x)``. Positions are in voxel units, where an
integer value is the centre of a voxel. Widths are in micrometres.

Theoretical-PSF bead fields (dataset SYN-B) are added with the theory module.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
"""Ratio of the full width at half maximum of a Gaussian to its standard deviation."""

_RENDER_HALF_WIDTH_SIGMA = 5.0


@dataclass(frozen=True)
class BeadSpec:
    """Ground truth for one bead.

    Parameters
    ----------
    position_px
        Sub-voxel centre ``(z, y, x)`` in voxel units.
    fwhm_um
        Full width at half maximum ``(z, y, x)`` along the bead's own axes,
        in micrometres.
    photons
        Total signal photons integrated over the whole bead.
    angle_xy_deg
        Rotation of the bead's lateral axes: the angle from the image x-axis
        to the bead's x-axis, measured towards the image y-axis.
    label
        Free-text class, e.g. ``"single"``, ``"doublet"``, ``"edge"``.
    """

    position_px: tuple[float, float, float]
    fwhm_um: tuple[float, float, float]
    photons: float
    angle_xy_deg: float = 0.0
    label: str = "single"

    def sigma_px(self, voxel_size_um: tuple[float, float, float]) -> tuple[float, float, float]:
        """Return the Gaussian standard deviations ``(z, y, x)`` in voxels."""
        sz, sy, sx = (
            f / FWHM_PER_SIGMA / v for f, v in zip(self.fwhm_um, voxel_size_um, strict=True)
        )
        return (sz, sy, sx)

    def peak_photons(self, voxel_size_um: tuple[float, float, float]) -> float:
        """Return the expected signal photons in a voxel centred on the bead."""
        sz, sy, sx = self.sigma_px(voxel_size_um)
        return self.photons / (math.pow(2.0 * math.pi, 1.5) * sz * sy * sx)


@dataclass(frozen=True)
class FieldSpec:
    """Geometry and camera model of a synthetic field.

    Parameters
    ----------
    shape
        Image shape ``(nz, ny, nx)`` in voxels.
    voxel_size_um
        Voxel size ``(z, y, x)`` in micrometres.
    background_photons
        Mean background photons per voxel.
    gain
        Counts per photon.
    offset
        Constant camera offset in counts.
    read_noise
        Standard deviation of Gaussian read noise in counts.
    bit_depth
        Detector bit depth; counts are clipped to ``2**bit_depth - 1``.
    shot_noise
        Whether to apply Poisson noise to the photon signal.
    """

    shape: tuple[int, int, int]
    voxel_size_um: tuple[float, float, float]
    background_photons: float = 10.0
    gain: float = 1.0
    offset: float = 100.0
    read_noise: float = 2.0
    bit_depth: int = 12
    shot_noise: bool = True

    def __post_init__(self) -> None:
        if len(self.shape) != 3 or any(n < 1 for n in self.shape):
            raise ValueError(f"shape must be three positive integers, got {self.shape}")
        if any(v <= 0 for v in self.voxel_size_um):
            raise ValueError(f"voxel sizes must be positive, got {self.voxel_size_um}")
        if not 1 <= self.bit_depth <= 16:
            raise ValueError(f"bit_depth must be between 1 and 16, got {self.bit_depth}")
        if self.background_photons < 0 or self.gain <= 0 or self.read_noise < 0:
            raise ValueError("background_photons and read_noise must be >= 0 and gain > 0")

    @property
    def max_count(self) -> int:
        """Largest count the detector can report."""
        return int(2**self.bit_depth - 1)


@dataclass(frozen=True)
class SyntheticField:
    """A rendered field together with its ground truth.

    Attributes
    ----------
    data
        Noisy image in counts, ``uint16``, shape ``(nz, ny, nx)``.
    expected_counts
        Noise-free expected counts before clipping, ``float64``.
    spec
        Geometry and camera model used.
    beads
        Ground truth for every rendered bead.
    hot_pixels
        ``(z, y, x)`` indices of injected hot pixels.
    """

    data: NDArray[np.uint16]
    expected_counts: NDArray[np.float64]
    spec: FieldSpec
    beads: tuple[BeadSpec, ...]
    hot_pixels: tuple[tuple[int, int, int], ...] = ()

    @property
    def saturated(self) -> NDArray[np.bool_]:
        """Mask of voxels at the detector maximum."""
        return self.data >= self.spec.max_count


def _window(centre: float, half_width: float, size: int) -> tuple[int, int]:
    """Return the clipped index range covering ``centre ± half_width``."""
    lo = max(0, math.floor(centre - half_width))
    hi = min(size, math.ceil(centre + half_width) + 1)
    return lo, hi


def render_signal(
    shape: tuple[int, int, int],
    voxel_size_um: tuple[float, float, float],
    beads: Sequence[BeadSpec],
    *,
    oversample: int = 1,
) -> NDArray[np.float64]:
    """Return the expected signal photons per voxel for a set of beads.

    Parameters
    ----------
    shape
        Image shape ``(nz, ny, nx)``.
    voxel_size_um
        Voxel size ``(z, y, x)`` in micrometres.
    beads
        Beads to render. Beads outside the image contribute what overlaps it.
    oversample
        Number of sample points per voxel along each axis. ``1`` samples the
        Gaussian at the voxel centre; larger values average over the voxel.
    """
    if oversample < 1:
        raise ValueError("oversample must be >= 1")
    image = np.zeros(shape, dtype=np.float64)
    # Offsets of the sub-samples from the voxel centre, in voxels.
    offsets = (np.arange(oversample) + 0.5) / oversample - 0.5
    for bead in beads:
        sz, sy, sx = bead.sigma_px(voxel_size_um)
        if min(sz, sy, sx) <= 0:
            raise ValueError(f"bead widths must be positive, got {bead.fwhm_um}")
        cz, cy, cx = bead.position_px
        lateral = _RENDER_HALF_WIDTH_SIGMA * max(sy, sx)
        z0, z1 = _window(cz, _RENDER_HALF_WIDTH_SIGMA * sz, shape[0])
        y0, y1 = _window(cy, lateral, shape[1])
        x0, x1 = _window(cx, lateral, shape[2])
        if z0 >= z1 or y0 >= y1 or x0 >= x1:
            continue
        dz = (np.arange(z0, z1)[:, None] + offsets[None, :]).ravel() - cz
        dy = (np.arange(y0, y1)[:, None] + offsets[None, :]).ravel() - cy
        dx = (np.arange(x0, x1)[:, None] + offsets[None, :]).ravel() - cx
        theta = math.radians(bead.angle_xy_deg)
        cos, sin = math.cos(theta), math.sin(theta)
        u = cos * dx[None, :] + sin * dy[:, None]
        v = -sin * dx[None, :] + cos * dy[:, None]
        lateral_profile = np.exp(-0.5 * ((u / sx) ** 2 + (v / sy) ** 2))
        axial_profile = np.exp(-0.5 * (dz / sz) ** 2)
        fine = axial_profile[:, None, None] * lateral_profile[None, :, :]
        nz, ny, nx = z1 - z0, y1 - y0, x1 - x0
        coarse = fine.reshape(nz, oversample, ny, oversample, nx, oversample).mean(axis=(1, 3, 5))
        image[z0:z1, y0:y1, x0:x1] += bead.peak_photons(voxel_size_um) * coarse
    return image


def render_field(
    spec: FieldSpec,
    beads: Sequence[BeadSpec],
    rng: np.random.Generator,
    *,
    oversample: int = 1,
    n_hot_pixels: int = 0,
) -> SyntheticField:
    """Render beads through the camera model.

    Parameters
    ----------
    spec
        Geometry and camera model.
    beads
        Beads to render.
    rng
        Random generator; the output is fully determined by its state.
    oversample
        See :func:`render_signal`.
    n_hot_pixels
        Number of voxels to set to the detector maximum at random positions.
    """
    photons = render_signal(spec.shape, spec.voxel_size_um, beads, oversample=oversample)
    photons += spec.background_photons
    expected = spec.gain * photons + spec.offset
    detected = rng.poisson(photons).astype(np.float64) if spec.shot_noise else photons
    counts = spec.gain * detected + spec.offset
    if spec.read_noise > 0:
        counts = counts + rng.normal(0.0, spec.read_noise, size=spec.shape)
    data = np.clip(np.rint(counts), 0, spec.max_count).astype(np.uint16)

    hot: list[tuple[int, int, int]] = []
    if n_hot_pixels > 0:
        flat = rng.choice(data.size, size=n_hot_pixels, replace=False)
        for index in np.sort(flat):
            z, y, x = (int(i) for i in np.unravel_index(index, spec.shape))
            data[z, y, x] = spec.max_count
            hot.append((z, y, x))
    return SyntheticField(
        data=data,
        expected_counts=expected,
        spec=spec,
        beads=tuple(beads),
        hot_pixels=tuple(hot),
    )


def random_positions(
    shape: tuple[int, int, int],
    n: int,
    rng: np.random.Generator,
    *,
    margin_px: tuple[float, float, float] = (0.0, 0.0, 0.0),
    min_separation_px: tuple[float, float, float] = (0.0, 0.0, 0.0),
    max_tries: int = 10_000,
) -> list[tuple[float, float, float]]:
    """Draw ``n`` sub-voxel positions that keep clear of the border and each other.

    Two positions are far enough apart when the normalized distance
    ``sqrt(sum((d / min_separation) ** 2))`` over the axes with a non-zero
    separation is at least 1, i.e. no position lies inside another's
    exclusion ellipsoid.

    Raises
    ------
    ValueError
        If the positions cannot be placed within ``max_tries`` draws.
    """
    lows = np.asarray(margin_px, dtype=np.float64)
    highs = np.asarray(shape, dtype=np.float64) - 1.0 - lows
    if np.any(highs < lows):
        raise ValueError(f"margin {margin_px} leaves no room in an image of shape {shape}")
    separation = np.asarray(min_separation_px, dtype=np.float64)
    active = separation > 0
    placed: list[NDArray[np.float64]] = []
    tries = 0
    while len(placed) < n:
        if tries >= max_tries:
            raise ValueError(
                f"could not place {n} positions with separation {min_separation_px} "
                f"in shape {shape} after {max_tries} tries"
            )
        tries += 1
        candidate = rng.uniform(lows, highs)
        if active.any() and any(
            np.sum(((candidate - other)[active] / separation[active]) ** 2) < 1.0
            for other in placed
        ):
            continue
        placed.append(candidate)
    return [(float(p[0]), float(p[1]), float(p[2])) for p in placed]


def make_doublet(
    bead: BeadSpec,
    separation_px: tuple[float, float, float],
) -> tuple[BeadSpec, BeadSpec]:
    """Return two beads placed symmetrically about ``bead``, labelled ``"doublet"``.

    Each carries the photons of the original bead.
    """
    half = tuple(s / 2.0 for s in separation_px)
    first = tuple(p - h for p, h in zip(bead.position_px, half, strict=True))
    second = tuple(p + h for p, h in zip(bead.position_px, half, strict=True))
    return (
        replace(bead, position_px=(first[0], first[1], first[2]), label="doublet"),
        replace(bead, position_px=(second[0], second[1], second[2]), label="doublet"),
    )


def apply_focal_surface(
    beads: Sequence[BeadSpec],
    voxel_size_um: tuple[float, float, float],
    *,
    centre_px: tuple[float, float],
    tilt: tuple[float, float] = (0.0, 0.0),
    curvature_per_um: float = 0.0,
) -> list[BeadSpec]:
    """Shift beads axially to follow a tilted and curved focal surface.

    The axial shift at lateral offset ``(dy, dx)`` micrometres from the
    centre is ``tilt[0] * dy + tilt[1] * dx + curvature_per_um * (dy² + dx²)``
    micrometres.

    Parameters
    ----------
    beads
        Beads to shift.
    voxel_size_um
        Voxel size ``(z, y, x)`` in micrometres.
    centre_px
        Lateral centre ``(y, x)`` of the surface, in voxels.
    tilt
        Slope ``(dz/dy, dz/dx)``, dimensionless (micrometres per micrometre).
    curvature_per_um
        Quadratic coefficient, in micrometres of z per square micrometre.
    """
    vz, vy, vx = voxel_size_um
    shifted: list[BeadSpec] = []
    for bead in beads:
        z, y, x = bead.position_px
        dy = (y - centre_px[0]) * vy
        dx = (x - centre_px[1]) * vx
        dz_um = tilt[0] * dy + tilt[1] * dx + curvature_per_um * (dy * dy + dx * dx)
        shifted.append(replace(bead, position_px=(z + dz_um / vz, y, x)))
    return shifted


def shift_beads(
    beads: Sequence[BeadSpec],
    voxel_size_um: tuple[float, float, float],
    shift_um: tuple[float, float, float],
) -> list[BeadSpec]:
    """Return beads displaced by ``shift_um`` ``(z, y, x)``, e.g. for a second channel."""
    delta = tuple(s / v for s, v in zip(shift_um, voxel_size_um, strict=True))
    return [
        replace(
            bead,
            position_px=(
                bead.position_px[0] + delta[0],
                bead.position_px[1] + delta[1],
                bead.position_px[2] + delta[2],
            ),
        )
        for bead in beads
    ]


def make_bead_field(
    spec: FieldSpec,
    n_beads: int,
    fwhm_um: tuple[float, float, float],
    photons: float,
    rng: np.random.Generator,
    *,
    margin_fwhm: float = 3.0,
    min_separation_fwhm: float = 4.0,
    oversample: int = 1,
) -> SyntheticField:
    """Render a field of identical, well-separated beads at random positions.

    Parameters
    ----------
    spec
        Geometry and camera model.
    n_beads
        Number of beads.
    fwhm_um
        Bead FWHM ``(z, y, x)`` in micrometres.
    photons
        Total signal photons per bead.
    rng
        Random generator for positions and noise.
    margin_fwhm
        Minimum distance from the image border, in units of the FWHM along
        each axis.
    min_separation_fwhm
        Minimum separation between beads, in units of the FWHM along each
        axis.
    oversample
        See :func:`render_signal`.
    """
    fwhm_px = tuple(f / v for f, v in zip(fwhm_um, spec.voxel_size_um, strict=True))
    margin = (margin_fwhm * fwhm_px[0], margin_fwhm * fwhm_px[1], margin_fwhm * fwhm_px[2])
    separation = (
        min_separation_fwhm * fwhm_px[0],
        min_separation_fwhm * fwhm_px[1],
        min_separation_fwhm * fwhm_px[2],
    )
    positions = random_positions(
        spec.shape, n_beads, rng, margin_px=margin, min_separation_px=separation
    )
    beads = [BeadSpec(position_px=p, fwhm_um=fwhm_um, photons=photons) for p in positions]
    return render_field(spec, beads, rng, oversample=oversample)
