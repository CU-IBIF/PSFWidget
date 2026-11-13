"""Analysis parameters, stored with every result (NFR-03)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class AnalysisSettings:
    """Parameters of bead detection, filtering, and measurement.

    Sizes given "in FWHM" are multiples of the expected FWHM along that axis:
    the theoretical FWHM widened by the bead's own size.
    """

    detection_threshold_sd: float = 8.0
    """Candidates must exceed the smoothed image's background by this many noise SDs."""
    detection_relative_threshold: float = 0.05
    """…and by this fraction of the median candidate height (robust to bright aggregates)."""
    detection_median_px: int = 3
    """In-plane median filter applied before detection, removing hot pixels; 1 turns it off."""
    crop_lateral_fwhm: float = 2.0
    """Half-width of the measurement box laterally (DET-02, DET-03)."""
    crop_axial_fwhm: float = 2.0
    """Half-height of the measurement box axially."""
    min_border_fwhm: float = 1.0
    """A bead's centre must be at least this far from every image edge (DET-02).

    The measurement box is clipped where it would leave the image, so short
    stacks can still be measured.
    """
    integration_radius_fwhm: float = 1.5
    """Radius of the ellipsoid summed for integrated intensity (MET-04).

    Voxels outside it, within the measurement box, are background.
    """
    min_background_voxels: int = 20
    min_snr: float = 10.0
    """Beads with a lower signal-to-noise ratio are rejected (DET-05)."""
    min_sbr: float | None = None
    """Beads with a lower signal-to-background ratio are rejected; ``None`` turns the check off."""
    min_r_squared: float = 0.8
    """Beads whose worst 1D fit has a lower R² are rejected (MET-10)."""
    outlier_mad: float | None = 4.0
    """Reject beads further than this many robust SDs from the median (DET-06).

    ``None`` turns outlier rejection off.
    """
    min_beads_for_outliers: int = 5
    profile_width_px: int = 3
    """Lines averaged into each 1D profile: 1 is a single line through the peak, 3 a 3×3 bundle.

    Averaging 3×3 lines cuts the per-bead FWHM scatter about threefold without bias for
    separable PSFs (see docs/validation/fitting_accuracy.md).
    """
    bead_size_correction: bool = True
    """Remove the bead's own width from FWHM values when its diameter is known (MET-15)."""
    nyquist_samples_per_fwhm: float = 2.3
    """Voxels needed across the theoretical FWHM to count as adequately sampled (MET-16)."""

    def __post_init__(self) -> None:
        positive = (
            "detection_threshold_sd",
            "crop_lateral_fwhm",
            "crop_axial_fwhm",
            "integration_radius_fwhm",
            "min_border_fwhm",
            "nyquist_samples_per_fwhm",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.detection_relative_threshold < 1:
            raise ValueError("detection_relative_threshold must be in [0, 1)")
        if not 0 <= self.min_r_squared <= 1:
            raise ValueError("min_r_squared must be in [0, 1]")
        if self.profile_width_px < 1 or self.profile_width_px % 2 == 0:
            raise ValueError("profile_width_px must be an odd number >= 1")
        if self.integration_radius_fwhm > min(self.crop_lateral_fwhm, self.crop_axial_fwhm):
            raise ValueError("integration_radius_fwhm must fit inside the measurement box")
        if self.min_border_fwhm > min(self.crop_lateral_fwhm, self.crop_axial_fwhm):
            raise ValueError("min_border_fwhm must not exceed the measurement box")
        if self.detection_median_px < 1 or self.detection_median_px % 2 == 0:
            raise ValueError("detection_median_px must be an odd number >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Return the settings as a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> AnalysisSettings:
        """Build settings from a dictionary, rejecting unknown keys."""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown analysis setting(s): {unknown}")
        return cls(**values)
