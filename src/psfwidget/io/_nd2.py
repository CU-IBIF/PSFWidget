"""Nikon ND2 reader, built on the ``nd2`` library (BSD-3-Clause)."""

from __future__ import annotations

from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any

import nd2
import numpy as np
from numpy.typing import NDArray

from psfwidget.io._common import (
    SeriesData,
    UnsupportedFileError,
    check_selection,
    file_value,
    julian_day_to_utc,
)
from psfwidget.io._nikon_text import apply_nikon_text, parse_nikon_text
from psfwidget.io.metadata import positive
from psfwidget.io.model import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    SeriesInfo,
    Sourced,
)

_SERIES_AXIS = "P"


def _open(path: Path) -> nd2.ND2File:
    handle = nd2.ND2File(path)
    if handle.is_legacy:
        handle.close()
        raise UnsupportedFileError(
            f"{path.name} is a legacy (JPEG 2000) ND2 file, which is not supported; "
            "re-save it from NIS-Elements in the current ND2 format"
        )
    return handle


def _sizes(handle: nd2.ND2File) -> dict[str, int]:
    sizes = {str(axis): int(size) for axis, size in handle.sizes.items()}
    if sizes.get("S", 1) > 1:
        raise UnsupportedFileError("RGB ND2 files are not supported")
    return sizes


def _series_sizes(sizes: dict[str, int]) -> dict[str, int]:
    return {axis: sizes[axis] for axis in "TCZYX" if axis in sizes}


def _series_list(path: Path, sizes: dict[str, int], dtype: str) -> list[SeriesInfo]:
    count = sizes.get(_SERIES_AXIS, 1)
    names = [path.stem] if count == 1 else [f"Position {i + 1}" for i in range(count)]
    return [SeriesInfo(i, name, _series_sizes(sizes), dtype) for i, name in enumerate(names)]


def list_series(path: Path) -> list[SeriesInfo]:
    """List the positions in an ND2 file."""
    with _open(path) as handle:
        return _series_list(path, _sizes(handle), str(handle.dtype))


def _modality(flags: list[str]) -> Modality | None:
    if "spinningDiskConfocal" in flags:
        return Modality.SPINNING_DISK
    if "laserScanConfocal" in flags:
        return Modality.CONFOCAL
    if "fluorescence" in flags:
        return Modality.WIDEFIELD
    return None


def _acquired(handle: nd2.ND2File, first_frame: int) -> Sourced[Any] | None:
    try:
        frame = handle.frame_metadata(first_frame)
        julian_day = frame.channels[0].time.absoluteJulianDayNumber  # type: ignore[union-attr]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None
    if not julian_day or julian_day <= 0:
        return None
    return Sourced(julian_day_to_utc(float(julian_day)), "file")


def _metadata(handle: nd2.ND2File, sizes: dict[str, int], first_frame: int) -> AcquisitionMetadata:
    n_channels = sizes.get("C", 1)
    structured = getattr(handle.metadata, "channels", None) or []
    channels: list[ChannelMetadata] = []
    for index in range(n_channels):
        if index >= len(structured):
            channels.append(ChannelMetadata(index))
            continue
        info = structured[index].channel
        scope = structured[index].microscope
        channels.append(
            ChannelMetadata(
                index,
                name=file_value(info.name or None),
                excitation_nm=file_value(positive(info.excitationLambdaNm)),
                emission_nm=file_value(positive(info.emissionLambdaNm)),
                pinhole_um=file_value(positive(scope.pinholeDiameterUm)),
            )
        )

    metadata = AcquisitionMetadata(channels=tuple(channels))
    if structured:
        scope = structured[0].microscope
        volume = structured[0].volume
        x_um, y_um, z_um = (
            positive(size) if calibrated else None
            for size, calibrated in zip(volume.axesCalibration, volume.axesCalibrated, strict=True)
        )
        if sizes.get("Z", 1) < 2:
            z_um = None
        flags = [str(flag) for flag in scope.modalityFlags or []]
        modality = _modality(flags)
        metadata = replace(
            metadata,
            voxel_z_um=file_value(z_um),
            voxel_y_um=file_value(y_um),
            voxel_x_um=file_value(x_um),
            numerical_aperture=file_value(positive(scope.objectiveNumericalAperture)),
            immersion_ri=file_value(positive(scope.immersionRefractiveIndex)),
            objective_name=file_value(scope.objectiveName or None),
            magnification=file_value(positive(scope.objectiveMagnification)),
            modality=file_value(modality),
            resolution_enhancement=file_value("Live-SR" if "liveSR" in flags else None),
        )

    text_info = handle.text_info or {}
    text = parse_nikon_text(text_info.get("description"), text_info.get("capturing"))
    metadata = replace(metadata, channels=apply_nikon_text(metadata.channels, text))
    if text.resolution_enhancement is not None:
        metadata = replace(
            metadata, resolution_enhancement=Sourced(text.resolution_enhancement, "file")
        )

    bits = getattr(handle.attributes, "bitsPerComponentSignificant", None)
    return replace(
        metadata,
        significant_bits=file_value(int(bits) if bits else None),
        acquired_utc=_acquired(handle, first_frame),
    )


def _frame_lookup(handle: nd2.ND2File) -> dict[tuple[tuple[str, int], ...], int]:
    """Map each loop-index combination to its frame sequence number."""
    return {
        tuple(sorted((str(axis), int(i)) for axis, i in indices.items())): number
        for number, indices in enumerate(handle.loop_indices)
    }


def _frame_number(
    lookup: dict[tuple[tuple[str, int], ...], int],
    sizes: dict[str, int],
    *,
    series: int,
    timepoint: int,
    z: int,
) -> int:
    wanted = {_SERIES_AXIS: series, "T": timepoint, "Z": z}
    key = tuple(sorted((axis, i) for axis, i in wanted.items() if axis in sizes))
    return lookup.get(key, 0)


def open_series(path: Path, series: int, timepoint: int, tile: int) -> SeriesData:
    """Open one position and timepoint of an ND2 file."""
    with _open(path) as handle:
        sizes = _sizes(handle)
        infos = _series_list(path, sizes, str(handle.dtype))
        if not 0 <= series < len(infos):
            raise UnsupportedFileError(
                f"series {series} is out of range; {path.name} has {len(infos)} series"
            )
        check_selection(sizes, timepoint=timepoint, tile=tile, extra_allowed=_SERIES_AXIS + "S")
        lookup = _frame_lookup(handle)
        first = _frame_number(lookup, sizes, series=series, timepoint=timepoint, z=0)
        metadata = _metadata(handle, sizes, first)
        dtype = np.dtype(handle.dtype)

    n_channels, n_z = sizes.get("C", 1), sizes.get("Z", 1)
    shape = (n_channels, n_z, sizes["Y"], sizes["X"])

    def load_channel(channel: int) -> NDArray[Any]:
        with _open(path) as source:
            planes = []
            for z in range(n_z):
                number = _frame_number(lookup, sizes, series=series, timepoint=timepoint, z=z)
                frame = np.asarray(source.read_frame(number))
                planes.append(frame[channel] if frame.ndim == 3 else frame)
            # Frames can be views of the memory-mapped file: copy before it closes.
            return np.stack(planes)

    return SeriesData(
        info=infos[series],
        shape=shape,
        dtype=dtype,
        metadata=metadata,
        load_channel=load_channel,
        reader=f"nd2 {version('nd2')}",
    )
