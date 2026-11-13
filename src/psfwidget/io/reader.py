"""Open image files of any supported format as :class:`ImageStack` objects."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from psfwidget.io import _czi, _lif, _nd2, _tiff
from psfwidget.io._common import SeriesData, UnsupportedFileError, sha256_of
from psfwidget.io.metadata import Overrides, resolve
from psfwidget.io.model import ImageStack, Provenance, SeriesInfo

if TYPE_CHECKING:
    from psfwidget.profiles import InstrumentProfile

_READERS: dict[str, tuple[str, ModuleType]] = {
    ".nd2": ("nd2", _nd2),
    ".lif": ("lif", _lif),
    ".czi": ("czi", _czi),
    ".tif": ("tiff", _tiff),
    ".tiff": ("tiff", _tiff),
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(_READERS)


def _reader_for(path: Path) -> tuple[str, ModuleType]:
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    try:
        return _READERS[path.suffix.lower()]
    except KeyError:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileError(
            f"unsupported file type {path.suffix!r} for {path.name}; supported: {supported}"
        ) from None


def _guard[T](path: Path, action: Callable[[], T]) -> T:
    """Run a reader call, turning library failures into :class:`UnsupportedFileError`."""
    try:
        return action()
    except (UnsupportedFileError, FileNotFoundError):
        raise
    except Exception as exc:
        raise UnsupportedFileError(
            f"could not read {path.name}: {type(exc).__name__}: {exc}"
        ) from exc


def list_series(path: str | Path) -> list[SeriesInfo]:
    """List the image series in a file (IMP-02).

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    UnsupportedFileError
        If the format is not supported or the file cannot be read.
    """
    path = Path(path)
    _, module = _reader_for(path)
    series: list[SeriesInfo] = _guard(path, lambda: module.list_series(path))
    return series


def read_stack(
    path: str | Path,
    *,
    series: int = 0,
    timepoint: int = 0,
    tile: int = 0,
    profile: InstrumentProfile | None = None,
    objective_id: str | None = None,
    overrides: Overrides | None = None,
    compute_hash: bool = True,
) -> ImageStack:
    """Open one series of an image file with resolved metadata.

    Pixel data is not read until a channel is requested.

    Parameters
    ----------
    path
        An ``.nd2``, ``.lif``, ``.czi``, ``.tif``, or ``.tiff`` file.
    series
        Index of the position, scene, or image to open (see :func:`list_series`).
    timepoint
        Timepoint to use when the series is a time-lapse.
    tile
        Tile to use when the series is a mosaic.
    profile
        Instrument profile supplying defaults and values no file records.
    objective_id
        Identifier of the profile objective to use, when the file's objective
        name does not select one.
    overrides
        User-entered values; these take precedence over the file and profile.
    compute_hash
        Whether to record the file's SHA-256 hash.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    UnsupportedFileError
        If the file or the selection cannot be read.
    psfwidget.io.metadata.InvalidMetadataError
        If an override value is invalid.
    psfwidget.profiles.ProfileError
        If ``objective_id`` is not in the profile.
    """
    path = Path(path)
    format_name, module = _reader_for(path)
    data: SeriesData = _guard(path, lambda: module.open_series(path, series, timepoint, tile))

    profile_overrides = None
    profile_note = ""
    metadata = data.metadata
    if profile is not None:
        profile_overrides, profile_warnings = profile.overrides_for(metadata, objective_id)
        profile_note = f"profile {profile.instrument_id}"
        for warning in profile_warnings:
            metadata = metadata.with_warning(warning)
    metadata = resolve(metadata, profile_overrides, overrides, profile_note=profile_note)

    provenance = Provenance(
        path=path,
        format=format_name,
        size_bytes=path.stat().st_size,
        sha256=sha256_of(path) if compute_hash else None,
        series_index=series,
        series_name=data.info.name,
        timepoint=timepoint,
        tile=tile,
        reader=data.reader,
    )
    return ImageStack(
        shape=data.shape,
        dtype=data.dtype,
        metadata=metadata,
        provenance=provenance,
        _load_channel=data.load_channel,
    )
