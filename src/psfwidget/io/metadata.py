"""Resolve acquisition metadata from the file, a profile, and user overrides.

Precedence is file < profile < user (IMP-05). Each applied value records its
source, and values that are physically implausible produce warnings.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from psfwidget.io.model import (
    ACQUISITION_FIELDS,
    CHANNEL_FIELDS,
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    Source,
    Sourced,
)

_FLOAT_FIELDS = frozenset(
    {
        "voxel_z_um",
        "voxel_y_um",
        "voxel_x_um",
        "numerical_aperture",
        "immersion_ri",
        "magnification",
        "bead_diameter_nm",
        "excitation_nm",
        "emission_nm",
        "pinhole_airy",
        "pinhole_um",
    }
)
_STRING_FIELDS = frozenset(
    {
        "objective_name",
        "resolution_enhancement",
        "instrument_id",
        "objective_id",
        "name",
        "preset_id",
    }
)


class InvalidMetadataError(ValueError):
    """Raised when a supplied metadata value is not valid."""


@dataclass(frozen=True)
class ChannelOverrides:
    """Values to set on one channel. ``None`` leaves the field unchanged."""

    name: str | None = None
    excitation_nm: float | None = None
    emission_nm: float | None = None
    detection_band_nm: tuple[float, float] | None = None
    pinhole_airy: float | None = None
    pinhole_um: float | None = None
    preset_id: str | None = None


@dataclass(frozen=True)
class Overrides:
    """Values to set on the acquisition metadata. ``None`` leaves a field unchanged."""

    voxel_z_um: float | None = None
    voxel_y_um: float | None = None
    voxel_x_um: float | None = None
    numerical_aperture: float | None = None
    immersion_ri: float | None = None
    objective_name: str | None = None
    magnification: float | None = None
    modality: Modality | None = None
    resolution_enhancement: str | None = None
    acquired_utc: datetime | None = None
    significant_bits: int | None = None
    bead_diameter_nm: float | None = None
    instrument_id: str | None = None
    objective_id: str | None = None
    channels: Mapping[int, ChannelOverrides] = field(default_factory=dict)


def positive(value: object) -> float | None:
    """Return ``value`` as a float if it is a finite number above zero, else ``None``.

    Readers use this to treat zero, negative, and non-numeric file entries as
    missing.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def coerce_value(name: str, value: object) -> Any:
    """Convert and validate a value for the metadata field ``name``.

    Raises
    ------
    InvalidMetadataError
        If the field is unknown or the value is not valid for it.
    """
    try:
        return _coerce(name, value)
    except InvalidMetadataError:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidMetadataError(f"invalid value for {name}: {value!r} ({exc})") from exc


def _coerce(name: str, value: object) -> Any:
    if name in _FLOAT_FIELDS:
        number = positive(value)
        if number is None:
            raise InvalidMetadataError(f"{name} must be a positive number, got {value!r}")
        return number
    if name in _STRING_FIELDS:
        text = str(value).strip()
        if not text:
            raise InvalidMetadataError(f"{name} must not be empty")
        return text
    if name == "significant_bits":
        if isinstance(value, float) and not value.is_integer():
            raise InvalidMetadataError(f"significant_bits must be a whole number, got {value!r}")
        bits = int(value)  # type: ignore[call-overload]
        if not 1 <= bits <= 32:
            raise InvalidMetadataError(f"significant_bits must be between 1 and 32, got {bits}")
        return bits
    if name == "modality":
        if isinstance(value, Modality):
            return value
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        try:
            return Modality(text)
        except ValueError:
            choices = ", ".join(m.value for m in Modality)
            raise InvalidMetadataError(
                f"modality must be one of {choices}; got {value!r}"
            ) from None
    if name == "acquired_utc":
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            raise InvalidMetadataError(
                f"acquired_utc needs a time zone, e.g. 2026-11-11T18:29:37+00:00; got {value!r}"
            )
        return moment.astimezone(UTC)
    if name == "detection_band_nm":
        if isinstance(value, str):
            # Accept an en dash as well as a hyphen between the wavelengths.
            parts: Sequence[object] = value.replace("\u2013", "-").split("-")
        else:
            parts = list(value)  # type: ignore[call-overload]
        if len(parts) != 2:
            raise InvalidMetadataError(
                f"detection_band_nm needs two wavelengths, e.g. 500-550; got {value!r}"
            )
        low, high = positive(parts[0]), positive(parts[1])
        if low is None or high is None or low >= high:
            raise InvalidMetadataError(
                f"detection_band_nm must be two increasing wavelengths, got {value!r}"
            )
        return (low, high)
    raise InvalidMetadataError(f"unknown metadata field: {name}")


def apply_overrides(
    metadata: AcquisitionMetadata,
    overrides: Overrides,
    source: Source,
    note: str = "",
) -> AcquisitionMetadata:
    """Return ``metadata`` with every non-``None`` override applied and tagged.

    Raises
    ------
    InvalidMetadataError
        If a value is invalid or a channel index does not exist.
    """
    changes: dict[str, Any] = {}
    for name in ACQUISITION_FIELDS:
        value = getattr(overrides, name)
        if value is not None:
            changes[name] = Sourced(coerce_value(name, value), source, note)

    channels = list(metadata.channels)
    for index, channel_overrides in overrides.channels.items():
        if not 0 <= index < len(channels):
            raise InvalidMetadataError(
                f"channel {index} does not exist; the image has {len(channels)} channel(s)"
            )
        channel_changes: dict[str, Any] = {}
        for name in CHANNEL_FIELDS:
            value = getattr(channel_overrides, name)
            if value is not None:
                channel_changes[name] = Sourced(coerce_value(name, value), source, note)
        channels[index] = replace(channels[index], **channel_changes)

    return replace(metadata, channels=tuple(channels), **changes)


def parse_assignments(assignments: Sequence[str]) -> Overrides:
    """Build overrides from ``key=value`` strings.

    Keys are metadata field names, e.g. ``numerical_aperture=1.4``. Channel
    fields are addressed as ``channel.<index>.<field>``, e.g.
    ``channel.0.emission_nm=515``.

    Raises
    ------
    InvalidMetadataError
        If an assignment is malformed, names an unknown field, or has an
        invalid value.
    """
    top: dict[str, Any] = {}
    per_channel: dict[int, dict[str, Any]] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        key, raw = key.strip(), raw.strip()
        if not separator or not key or not raw:
            raise InvalidMetadataError(f"expected key=value, got {assignment!r}")
        if key.startswith("channel."):
            parts = key.split(".")
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in CHANNEL_FIELDS:
                raise InvalidMetadataError(
                    f"channel overrides look like channel.0.emission_nm=515; got {key!r}"
                )
            per_channel.setdefault(int(parts[1]), {})[parts[2]] = coerce_value(parts[2], raw)
        elif key in ACQUISITION_FIELDS:
            top[key] = coerce_value(key, raw)
        else:
            raise InvalidMetadataError(f"unknown metadata field: {key}")
    return Overrides(
        channels={index: ChannelOverrides(**values) for index, values in per_channel.items()},
        **top,
    )


def plausibility_warnings(metadata: AcquisitionMetadata) -> list[str]:
    """Return warnings for values that are present but physically implausible."""
    found: list[str] = []
    na, ri = metadata.numerical_aperture, metadata.immersion_ri
    if na is not None and not 0.05 <= na.value <= 1.7:
        found.append(f"numerical aperture {na.value:g} is outside the plausible range 0.05-1.7")
    if ri is not None and not 1.0 <= ri.value <= 1.8:
        found.append(f"immersion refractive index {ri.value:g} is outside 1.0-1.8")
    if na is not None and ri is not None and na.value > ri.value:
        found.append(
            f"numerical aperture {na.value:g} exceeds the immersion refractive index "
            f"{ri.value:g}; one of them is wrong"
        )
    for channel in metadata.channels:
        found.extend(_channel_warnings(channel))
    return found


def _channel_warnings(channel: ChannelMetadata) -> list[str]:
    found: list[str] = []
    where = f"channel {channel.index}"
    for label, item in (("excitation", channel.excitation_nm), ("emission", channel.emission_nm)):
        if item is not None and not 300.0 <= item.value <= 1100.0:
            found.append(f"{where}: {label} wavelength {item.value:g} nm is outside 300-1100 nm")
    if (
        channel.excitation_nm is not None
        and channel.emission_nm is not None
        and channel.emission_nm.value <= channel.excitation_nm.value
    ):
        found.append(
            f"{where}: emission {channel.emission_nm.value:g} nm is not longer than "
            f"excitation {channel.excitation_nm.value:g} nm"
        )
    return found


def resolve(
    file_metadata: AcquisitionMetadata,
    profile_overrides: Overrides | None = None,
    user_overrides: Overrides | None = None,
    *,
    profile_note: str = "",
) -> AcquisitionMetadata:
    """Combine file metadata with profile and user overrides and add warnings."""
    metadata = file_metadata
    if profile_overrides is not None:
        metadata = apply_overrides(metadata, profile_overrides, "profile", profile_note)
    if user_overrides is not None:
        metadata = apply_overrides(metadata, user_overrides, "user")
    for warning in plausibility_warnings(metadata):
        metadata = metadata.with_warning(warning)
    return metadata
