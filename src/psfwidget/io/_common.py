"""Helpers shared by the format readers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from psfwidget.io.model import AcquisitionMetadata, SeriesInfo, Sourced

_UNIX_EPOCH_JULIAN_DAY = 2440587.5
_LASER_BAND_TOLERANCE_NM = 2.0


class UnsupportedFileError(ValueError):
    """Raised when a file or a selection within it cannot be read."""


@dataclass(frozen=True)
class SeriesData:
    """What a format reader returns for one selected series."""

    info: SeriesInfo
    shape: tuple[int, int, int, int]
    """``(channels, z, y, x)`` after selecting the timepoint and tile."""
    dtype: np.dtype[Any]
    metadata: AcquisitionMetadata
    load_channel: Callable[[int], NDArray[Any]]
    reader: str


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def julian_day_to_utc(julian_day: float) -> datetime:
    """Convert a Julian day number to a UTC datetime."""
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=julian_day - _UNIX_EPOCH_JULIAN_DAY)


def band_centre(band: tuple[float, float]) -> float:
    """Return the centre wavelength of a detection band."""
    return (band[0] + band[1]) / 2.0


def pick_excitation(
    lasers: Sequence[float],
    band: tuple[float, float] | None = None,
    hint: float | None = None,
) -> float | None:
    """Choose the laser line that excites a channel when several are active.

    A lone laser is returned as is. Otherwise the longest wavelength that
    does not lie above the start of the detection band is chosen; failing
    that, the laser nearest to ``hint`` (e.g. the dye's excitation maximum).
    Returns ``None`` when no choice can be justified.
    """
    unique = sorted(set(lasers))
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    if band is not None:
        below = [w for w in unique if w <= band[0] + _LASER_BAND_TOLERANCE_NM]
        if below:
            return below[-1]
    if hint is not None:
        return min(unique, key=lambda w: abs(w - hint))
    return None


def step_from_coordinates(values: Sequence[float]) -> float | None:
    """Return the spacing of an evenly sampled axis, or ``None`` for one sample."""
    if len(values) < 2:
        return None
    return (float(values[-1]) - float(values[0])) / (len(values) - 1)


def check_selection(
    sizes: dict[str, int], *, timepoint: int, tile: int, extra_allowed: str = ""
) -> None:
    """Validate a timepoint and tile selection against a series' axis sizes.

    Raises
    ------
    UnsupportedFileError
        If an index is out of range or the series has an axis that cannot be
        selected.
    """
    known = set("TMCZYX") | set(extra_allowed)
    unknown = [axis for axis, size in sizes.items() if axis not in known and size > 1]
    if unknown:
        raise UnsupportedFileError(
            f"unsupported image axes {unknown} with sizes "
            f"{ {a: sizes[a] for a in unknown} }; only T, M (tiles), C, Z, Y, X are handled"
        )
    for label, axis, index in (("timepoint", "T", timepoint), ("tile", "M", tile)):
        size = sizes.get(axis, 1)
        if not 0 <= index < size:
            raise UnsupportedFileError(
                f"{label} {index} is out of range; the series has {size} {label}(s)"
            )


def file_value[T](value: T | None, note: str = "") -> Sourced[T] | None:
    """Wrap a value read from the file, passing ``None`` through."""
    return None if value is None else Sourced(value, "file", note)


def to_zyx(data: NDArray[Any], dims: Sequence[str]) -> NDArray[Any]:
    """Reorder an array with named axes to ``(z, y, x)``.

    Axes other than Z, Y, and X must have length 1 and are dropped. A missing
    Z axis is added with length 1.

    Raises
    ------
    UnsupportedFileError
        If Y or X is missing or another axis has more than one element.
    """
    if data.ndim != len(dims):
        raise UnsupportedFileError(f"array of shape {data.shape} does not match axes {list(dims)}")
    extra = [d for i, d in enumerate(dims) if d not in "ZYX" and data.shape[i] != 1]
    if extra or "Y" not in dims or "X" not in dims:
        raise UnsupportedFileError(
            f"cannot reduce axes {list(dims)} with shape {data.shape} to a z-stack"
        )
    kept = [i for i, d in enumerate(dims) if d in "ZYX"]
    data = data.reshape([data.shape[i] for i in kept])
    present = [dims[i] for i in kept]
    data = data.transpose([present.index(axis) for axis in "ZYX" if axis in present])
    return data if "Z" in present else data[np.newaxis]
