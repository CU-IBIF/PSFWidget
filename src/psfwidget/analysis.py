"""Analyse a bead field: detect, filter, measure, summarise, and compare (WP2).

Requirements: DET-01 to DET-06, DET-08, MET-01, MET-02, MET-04 to MET-08,
MET-10, MET-15, MET-16, THE-01, THE-03, THE-06, THE-08.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import numpy as np

from psfwidget import __version__
from psfwidget.detect.finder import Candidate, Detection, find_candidates, remove_hot_pixels
from psfwidget.io.model import AcquisitionMetadata, ChannelMetadata, ImageStack, Provenance
from psfwidget.metrics.bead import (
    BeadResult,
    Geometry,
    border_margin,
    far_from_border,
    half_box,
    measure_bead,
)
from psfwidget.metrics.field import (
    MATCH_RADIUS_FWHM,
    ChromaticShift,
    FieldGeometry,
    chromatic_shift,
    field_geometry,
)
from psfwidget.metrics.gaussian import BEAD_FWHM_PER_DIAMETER
from psfwidget.metrics.summary import Stats, flag_outliers, rejection_counts, summarize
from psfwidget.profiles.spec import SpecLimits, SpecResult, evaluate
from psfwidget.settings import AnalysisSettings
from psfwidget.theory import analytic
from psfwidget.theory.model import (
    Theory,
    TheoryUnavailableError,
    channel_theory,
    nyquist_voxel_um,
)

NM_PER_UM = 1000.0
MAX_BOX_ADAPTATIONS = 2
"""Times a channel is re-measured with a box sized from the measured FWHM."""
ADAPT_ABOVE = 1.25
"""Re-measure when the median fitted FWHM exceeds the box's size by this factor."""
MAX_BOX_GROWTH = 4.0
"""The box never grows beyond this multiple of the theoretical size."""
MIN_BEADS_FOR_ADAPTATION = 3
PLATEAU_VOXELS = 3
"""Voxels at a 2ⁿ − 1 maximum that count as a clipping plateau."""


@dataclass(frozen=True)
class ChannelResult:
    """Analysis of one channel of a field."""

    index: int
    name: str | None
    theory: Theory | None
    theory_unavailable: str | None
    """Why no theoretical PSF was computed, if none was."""
    expected_fwhm_um: tuple[float, float]
    """``(lateral, axial)`` size used for detection and the measurement box."""
    saturation_level: float
    detection: Detection
    beads: tuple[BeadResult, ...]
    summary: dict[str, Stats]
    rejections: dict[str, int]
    spec: SpecResult | None
    warnings: tuple[str, ...]
    field: FieldGeometry | None = None
    """Tilt, curvature, and uniformity (MET-11, MET-12)."""
    chromatic: ChromaticShift | None = None
    """Shift relative to the first analysed channel (MET-13); ``None`` for that channel."""

    @property
    def channel_metrics(self) -> dict[str, float]:
        """Channel-level metrics (one value per session), tracked like bead summaries."""
        values = {} if self.field is None else self.field.metrics()
        if self.chromatic is not None:
            values["chromatic_lateral_nm"] = self.chromatic.lateral_nm
            values["chromatic_axial_nm"] = self.chromatic.dz_nm
        return values

    @property
    def accepted(self) -> tuple[BeadResult, ...]:
        """Beads that passed every check."""
        return tuple(b for b in self.beads if b.accepted)


@dataclass(frozen=True)
class FieldResult:
    """Analysis of every channel of one image series."""

    provenance: Provenance
    metadata: AcquisitionMetadata
    settings: AnalysisSettings
    spec_limits: SpecLimits
    software_version: str
    analysed_utc: datetime
    channels: tuple[ChannelResult, ...]


def saturation_level(stack: ImageStack) -> float:
    """Return the count at which the detector saturates, as far as the metadata says.

    Uses the significant bits from the file when they fit the data type,
    otherwise the data type's maximum. Floating-point data never saturates.
    """
    if stack.dtype.kind not in "ui":
        return math.inf
    maximum = float(np.iinfo(stack.dtype).max)
    bits = stack.metadata.significant_bits
    if bits is not None and 2**bits.value - 1 <= maximum:
        return float(2**bits.value - 1)
    return maximum


def inferred_saturation(volume: np.ndarray[Any, Any], level: float) -> float | None:
    """Return a lower saturation level when the data shows a clipping plateau.

    Detectors clip at 2ⁿ − 1. If the channel's maximum is such a value below
    ``level`` and occurs in at least ``PLATEAU_VOXELS`` voxels, the detector
    has clipped there.
    """
    if volume.dtype.kind not in "ui" or volume.size == 0:
        return None
    peak = int(volume.max())
    if peak <= 0 or float(peak) >= level or (peak + 1) & peak:
        return None
    if int(np.count_nonzero(volume == peak)) < PLATEAU_VOXELS:
        return None
    return float(peak)


def _expected_fwhm(
    theory: Theory | None, channel: ChannelMetadata, metadata: AcquisitionMetadata
) -> tuple[float, float]:
    """Return the expected bead image size: theory widened by the bead itself."""
    if theory is not None:
        lateral, axial = theory.lateral_um, theory.axial_um
    else:
        assert metadata.numerical_aperture is not None
        assert metadata.immersion_ri is not None
        na, n = metadata.numerical_aperture.value, metadata.immersion_ri.value
        if channel.emission_nm is not None:
            rough = analytic.widefield(channel.emission_nm.value, na, n)
        else:  # multiphoton: only the excitation wavelength is required
            assert channel.excitation_nm is not None
            rough = analytic.multiphoton(channel.excitation_nm.value, na, n)
        lateral, axial = rough.lateral_um, rough.axial_um
    if metadata.bead_diameter_nm is not None:
        bead = BEAD_FWHM_PER_DIAMETER * metadata.bead_diameter_nm.value / NM_PER_UM
        lateral, axial = math.hypot(lateral, bead), math.hypot(axial, bead)
    return lateral, axial


def _nyquist_warnings(
    theory: Theory | None, voxel: tuple[float, float, float], settings: AnalysisSettings
) -> list[str]:
    if theory is None:
        return []
    lateral_max, axial_max = nyquist_voxel_um(theory, settings.nyquist_samples_per_fwhm)
    found = []
    if max(voxel[1], voxel[2]) > lateral_max:
        found.append(
            f"undersampled laterally: pixel {max(voxel[1], voxel[2]) * NM_PER_UM:.0f} nm, "
            f"needs <= {lateral_max * NM_PER_UM:.0f} nm "
            f"({settings.nyquist_samples_per_fwhm:g} per theoretical FWHM)"
        )
    if voxel[0] > axial_max:
        found.append(
            f"undersampled axially: z step {voxel[0] * NM_PER_UM:.0f} nm, "
            f"needs <= {axial_max * NM_PER_UM:.0f} nm "
            f"({settings.nyquist_samples_per_fwhm:g} per theoretical FWHM)"
        )
    return found


def _screen(
    candidates: Sequence[Candidate],
    half: tuple[int, int, int],
    margin: tuple[int, int, int],
    shape: tuple[int, ...],
) -> list[BeadResult]:
    """Turn candidates into beads; reject those near the edge or overlapping (DET-02, DET-03)."""
    beads: list[BeadResult] = []
    positions = np.asarray([c.position_px for c in candidates], dtype=np.int64).reshape(-1, 3)
    limit = np.asarray(half, dtype=np.int64)
    for number, candidate in enumerate(candidates, start=1):
        z, y, x = candidate.position_px
        bead = BeadResult(bead_id=number, z_px=z, y_px=y, x_px=x)
        if not far_from_border(candidate.position_px, margin, shape):
            bead = bead.reject("too close to the image border")
        offsets = np.abs(positions - positions[number - 1])
        neighbours = np.all(offsets <= limit, axis=1)
        neighbours[number - 1] = False
        if np.any(neighbours):
            bead = bead.reject("another bead inside the measurement box")
        beads.append(bead)
    return beads


def _detect_and_measure(
    volume: np.ndarray[Any, Any], filtered: np.ndarray[Any, Any], geometry: Geometry
) -> tuple[Detection, list[BeadResult]]:
    settings = geometry.settings
    detection = find_candidates(
        filtered,
        geometry.voxel_size_um,
        geometry.expected_fwhm_um,
        threshold_sd=settings.detection_threshold_sd,
        relative_threshold=settings.detection_relative_threshold,
    )
    margin = border_margin(geometry.voxel_size_um, geometry.expected_fwhm_um, settings)
    beads = [
        measure_bead(volume, bead, geometry) if not bead.reasons else bead
        for bead in _screen(detection.candidates, geometry.half_box_px, margin, volume.shape)
    ]
    return detection, beads


def _measured_size(
    beads: Sequence[BeadResult],
    expected: tuple[float, float],
    base: tuple[float, float],
) -> tuple[float, float] | None:
    """Return a larger expected size if fitted beads are clearly wider than ``expected``.

    Uses the median raw FWHM of every bead that was fitted, accepted or not,
    capped at ``MAX_BOX_GROWTH`` times the theoretical size. Returns ``None``
    when no change is needed.
    """
    fitted = [
        b
        for b in beads
        if b.fwhm_x_raw_um is not None
        and b.fwhm_y_raw_um is not None
        and b.fwhm_z_raw_um is not None
    ]
    if len(fitted) < MIN_BEADS_FOR_ADAPTATION:
        return None
    lateral = float(np.median([(b.fwhm_x_raw_um + b.fwhm_y_raw_um) / 2 for b in fitted]))  # type: ignore[operator]
    axial = float(np.median([b.fwhm_z_raw_um for b in fitted]))
    grown = (
        min(max(expected[0], lateral), MAX_BOX_GROWTH * base[0]),
        min(max(expected[1], axial), MAX_BOX_GROWTH * base[1]),
    )
    if grown[0] <= ADAPT_ABOVE * expected[0] and grown[1] <= ADAPT_ABOVE * expected[1]:
        return None
    return grown


def analyze_channel(
    stack: ImageStack,
    index: int,
    settings: AnalysisSettings,
    spec_limits: SpecLimits,
) -> ChannelResult:
    """Detect and measure the beads in one channel."""
    metadata = stack.metadata
    channel = metadata.channels[index]
    voxel = stack.voxel_size_um
    theory: Theory | None = None
    unavailable: str | None = None
    try:
        theory = channel_theory(channel, metadata)
    except TheoryUnavailableError as exc:
        unavailable = str(exc)
    warnings = _nyquist_warnings(theory, voxel, settings)
    if theory is not None:
        warnings.extend(theory.notes)

    base = _expected_fwhm(theory, channel, metadata)
    volume = stack.channel(index)
    level = saturation_level(stack)
    if stack.metadata.significant_bits is None:
        clipped = inferred_saturation(volume, level)
        if clipped is not None:
            level = clipped
            warnings.append(
                f"the file does not state its bit depth; saturation taken as {clipped:g}, "
                "where the data has a clipping plateau"
            )
    bead_diameter = (
        None if metadata.bead_diameter_nm is None else metadata.bead_diameter_nm.value / NM_PER_UM
    )
    if bead_diameter is None and settings.bead_size_correction:
        warnings.append("bead diameter unknown; FWHM values are not corrected for bead size")

    filtered = remove_hot_pixels(volume, settings.detection_median_px)
    expected = base
    for attempt in range(MAX_BOX_ADAPTATIONS + 1):
        geometry = Geometry(
            voxel_size_um=voxel,
            expected_fwhm_um=expected,
            half_box_px=half_box(voxel, expected, settings),
            saturation_level=level,
            bead_diameter_um=bead_diameter,
            theory_fwhm_um=None if theory is None else (theory.lateral_um, theory.axial_um),
            settings=settings,
        )
        detection, beads = _detect_and_measure(volume, filtered, geometry)
        wider = _measured_size(beads, expected, base)
        if wider is None or attempt == MAX_BOX_ADAPTATIONS:
            break
        expected = wider
    if expected != base:
        warnings.append(
            f"beads are wider than theory, so the measurement box was sized from the measured "
            f"FWHM (lateral {expected[0]:.3f} µm, axial {expected[1]:.3f} µm)"
        )
    if settings.outlier_mad is not None:
        beads = flag_outliers(beads, settings.outlier_mad, settings.min_beads_for_outliers)
    summary = summarize(beads)
    accepted = sum(1 for b in beads if b.accepted)
    if not detection.candidates:
        warnings.append("no beads were detected")
    elif accepted == 0:
        warnings.append("no bead passed every check")
    return ChannelResult(
        index=index,
        name=None if channel.name is None else channel.name.value,
        theory=theory,
        theory_unavailable=unavailable,
        expected_fwhm_um=expected,
        saturation_level=level,
        detection=detection,
        beads=tuple(beads),
        summary=summary,
        rejections=rejection_counts(beads),
        spec=None if spec_limits.empty else evaluate(spec_limits, summary, accepted),
        warnings=tuple(warnings),
    )


def analyze_stack(
    stack: ImageStack,
    settings: AnalysisSettings | None = None,
    spec_limits: SpecLimits | None = None,
    *,
    channels: Sequence[int] | None = None,
) -> FieldResult:
    """Analyse every channel (or the given ``channels``) of a stack.

    Raises
    ------
    psfwidget.io.StackNotAnalyzableError
        If the stack is not 3D or lacks required metadata.
    IndexError
        If a requested channel does not exist.
    """
    stack.require_analyzable()
    settings = settings or AnalysisSettings()
    spec_limits = spec_limits or SpecLimits()
    selected = range(stack.n_channels) if channels is None else channels
    for index in selected:
        if not 0 <= index < stack.n_channels:
            raise IndexError(f"channel {index} out of range for {stack.n_channels} channel(s)")
    _, _, height, width = stack.shape
    _, voxel_y, voxel_x = stack.voxel_size_um
    channels_done = tuple(
        analyze_channel(stack, index, settings, spec_limits) for index in selected
    )
    return add_field_metrics(
        FieldResult(
            provenance=stack.provenance,
            metadata=stack.metadata,
            settings=settings,
            spec_limits=spec_limits,
            software_version=__version__,
            analysed_utc=datetime.now(UTC).replace(microsecond=0),
            channels=channels_done,
        ),
        (width * voxel_x, height * voxel_y),
    )


def add_field_metrics(
    result: FieldResult, field_size_um: tuple[float, float] | None = None
) -> FieldResult:
    """Return ``result`` with field geometry and chromatic shifts (re)computed.

    ``field_size_um`` is the field width and height; if omitted, the size
    stored with the channels' previous geometry is used (as after a review).
    """
    channels = []
    for channel in result.channels:
        size = field_size_um or (None if channel.field is None else channel.field.field_size_um)
        geometry = None if size is None else field_geometry(channel.beads, size)
        channels.append(replace(channel, field=geometry, chromatic=None))
    if len(channels) > 1:
        reference = channels[0]
        radius = MATCH_RADIUS_FWHM * reference.expected_fwhm_um[0]
        for number, channel in enumerate(channels[1:], start=1):
            shift = chromatic_shift(
                reference.beads,
                channel.beads,
                reference_index=reference.index,
                other_index=channel.index,
                match_radius_um=radius,
            )
            channels[number] = replace(channel, chromatic=shift)
    return replace(result, channels=tuple(channels))
