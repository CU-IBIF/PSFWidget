"""Data model for imported image stacks and their acquisition metadata.

Every metadata value carries its origin (IMP-05): read from the ``file``,
supplied by an instrument ``profile``, or entered by the ``user``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import DTypeLike, NDArray

Source = Literal["file", "profile", "user"]


class Modality(StrEnum):
    """Imaging modality, which selects the theoretical PSF model."""

    WIDEFIELD = "widefield"
    CONFOCAL = "confocal"
    """Point-scanning confocal."""
    SPINNING_DISK = "spinning_disk"
    MULTIPHOTON = "multiphoton"
    """Two-photon excitation with non-descanned detection (THE-10)."""


@dataclass(frozen=True)
class Sourced[T]:
    """A metadata value with its origin.

    Attributes
    ----------
    value
        The value.
    source
        Where it came from.
    note
        Optional explanation, e.g. how a reader derived or corrected it.
    """

    value: T
    source: Source = "file"
    note: str = ""


@dataclass(frozen=True)
class ChannelMetadata:
    """Acquisition metadata of one channel. ``None`` means not known."""

    index: int
    name: Sourced[str] | None = None
    excitation_nm: Sourced[float] | None = None
    emission_nm: Sourced[float] | None = None
    detection_band_nm: Sourced[tuple[float, float]] | None = None
    pinhole_airy: Sourced[float] | None = None
    pinhole_um: Sourced[float] | None = None
    preset_id: Sourced[str] | None = None
    """Identifier of the instrument-profile channel preset that matched."""


@dataclass(frozen=True)
class AcquisitionMetadata:
    """Acquisition metadata of one image stack. ``None`` means not known."""

    voxel_z_um: Sourced[float] | None = None
    voxel_y_um: Sourced[float] | None = None
    voxel_x_um: Sourced[float] | None = None
    numerical_aperture: Sourced[float] | None = None
    immersion_ri: Sourced[float] | None = None
    objective_name: Sourced[str] | None = None
    magnification: Sourced[float] | None = None
    modality: Sourced[Modality] | None = None
    resolution_enhancement: Sourced[str] | None = None
    """Resolution-enhancing mode, e.g. ``"NSPARC SR"`` or ``"Live-SR"`` (THE-08)."""
    acquired_utc: Sourced[datetime] | None = None
    significant_bits: Sourced[int] | None = None
    bead_diameter_nm: Sourced[float] | None = None
    instrument_id: Sourced[str] | None = None
    objective_id: Sourced[str] | None = None
    channels: tuple[ChannelMetadata, ...] = ()
    warnings: tuple[str, ...] = ()

    def with_warning(self, message: str) -> AcquisitionMetadata:
        """Return a copy with ``message`` appended to the warnings."""
        if message in self.warnings:
            return self
        return replace(self, warnings=(*self.warnings, message))


ACQUISITION_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(AcquisitionMetadata) if f.name not in ("channels", "warnings")
)
CHANNEL_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(ChannelMetadata) if f.name != "index"
)

REQUIRED_ACQUISITION_FIELDS: tuple[str, ...] = (
    "voxel_z_um",
    "voxel_y_um",
    "voxel_x_um",
    "numerical_aperture",
    "immersion_ri",
)
"""Fields without which a stack cannot be analysed (IMP-06)."""

REQUIRED_CHANNEL_FIELDS: tuple[str, ...] = ("emission_nm",)
MULTIPHOTON_CHANNEL_FIELDS: tuple[str, ...] = ("excitation_nm",)
"""Multiphoton theory depends on the excitation (laser) wavelength, not the emission."""


def required_channel_fields(metadata: AcquisitionMetadata) -> tuple[str, ...]:
    """Return the channel fields analysis needs for this acquisition's modality."""
    if metadata.modality is not None and metadata.modality.value is Modality.MULTIPHOTON:
        return MULTIPHOTON_CHANNEL_FIELDS
    return REQUIRED_CHANNEL_FIELDS


@dataclass(frozen=True)
class SeriesInfo:
    """One selectable image series (position, scene, or image) in a file."""

    index: int
    name: str
    sizes: dict[str, int]
    """Size of each axis, keyed by ``T``, ``M`` (tile), ``C``, ``Z``, ``Y``, ``X``."""
    dtype: str


@dataclass(frozen=True)
class Provenance:
    """Where an image stack came from (IMP-09, REP-06)."""

    path: Path
    format: str
    size_bytes: int
    sha256: str | None
    series_index: int
    series_name: str
    timepoint: int
    tile: int
    reader: str
    """Reader library and version, e.g. ``"nd2 0.12.0"``."""


class StackNotAnalyzableError(ValueError):
    """Raised when a stack lacks something analysis requires (IMP-06)."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("stack cannot be analysed: " + "; ".join(problems))


@dataclass(frozen=True)
class ImageStack:
    """A 3D multichannel image with resolved metadata.

    Pixel data is loaded one channel at a time, on request (IMP-08).

    Attributes
    ----------
    shape
        ``(channels, z, y, x)``.
    dtype
        Pixel data type.
    metadata
        Acquisition metadata, with the origin of each value.
    provenance
        Source file and selection.
    """

    shape: tuple[int, int, int, int]
    dtype: np.dtype[Any]
    metadata: AcquisitionMetadata
    provenance: Provenance
    _load_channel: Callable[[int], NDArray[Any]] = field(repr=False, compare=False)

    @property
    def n_channels(self) -> int:
        """Number of channels."""
        return self.shape[0]

    @property
    def voxel_size_um(self) -> tuple[float, float, float]:
        """Voxel size ``(z, y, x)`` in micrometres.

        Raises
        ------
        StackNotAnalyzableError
            If any voxel dimension is unknown.
        """
        meta = self.metadata
        values = (meta.voxel_z_um, meta.voxel_y_um, meta.voxel_x_um)
        if values[0] is None or values[1] is None or values[2] is None:
            missing = [
                name for name, value in zip(("z", "y", "x"), values, strict=True) if value is None
            ]
            raise StackNotAnalyzableError([f"voxel size unknown along {', '.join(missing)}"])
        return (values[0].value, values[1].value, values[2].value)

    def channel(self, index: int) -> NDArray[Any]:
        """Read one channel as a ``(z, y, x)`` array."""
        if not 0 <= index < self.n_channels:
            raise IndexError(f"channel {index} out of range for {self.n_channels} channel(s)")
        data = np.asarray(self._load_channel(index))
        expected = self.shape[1:]
        if data.shape != expected:
            raise RuntimeError(
                f"reader returned shape {data.shape} for channel {index}, expected {expected}"
            )
        return data

    def asarray(self) -> NDArray[Any]:
        """Read all channels as a ``(c, z, y, x)`` array."""
        return np.stack([self.channel(c) for c in range(self.n_channels)])

    def with_metadata(self, metadata: AcquisitionMetadata) -> ImageStack:
        """Return the same stack with different metadata."""
        return replace(self, metadata=metadata)

    def problems(self) -> list[str]:
        """Return the reasons this stack cannot be analysed; empty if it can."""
        found: list[str] = []
        if self.shape[1] < 2:
            found.append("not a 3D stack (single z-plane)")
        found.extend(missing_required(self.metadata))
        return found

    def require_analyzable(self) -> None:
        """Raise :class:`StackNotAnalyzableError` unless analysis can proceed."""
        found = self.problems()
        if found:
            raise StackNotAnalyzableError(found)


def missing_required(metadata: AcquisitionMetadata) -> list[str]:
    """Return a description of each required value that is missing (IMP-06)."""
    missing = [
        f"missing {name}"
        for name in REQUIRED_ACQUISITION_FIELDS
        if getattr(metadata, name) is None
    ]
    for channel in metadata.channels:
        label = channel.name.value if channel.name is not None else ""
        where = f"channel {channel.index}" + (f" ({label})" if label else "")
        missing.extend(
            f"missing {name} for {where}"
            for name in required_channel_fields(metadata)
            if getattr(channel, name) is None
        )
    return missing


def make_dtype(value: DTypeLike) -> np.dtype[Any]:
    """Return a NumPy dtype object for ``value``."""
    return np.dtype(value)
