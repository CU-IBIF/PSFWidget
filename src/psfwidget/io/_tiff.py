"""TIFF and OME-TIFF reader, built on the ``tifffile`` library (BSD-3-Clause).

OME-TIFF files carry optics metadata in OME-XML. ImageJ TIFF files carry only
the voxel size. Other TIFF files carry no usable metadata, so every required
value must come from an instrument profile or the user.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

import numpy as np
import tifffile
from numpy.typing import NDArray

from psfwidget.io._common import (
    SeriesData,
    UnsupportedFileError,
    check_selection,
    file_value,
    to_zyx,
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

_LENGTH_TO_UM = {
    "µm": 1.0,
    "μm": 1.0,
    "um": 1.0,
    "micron": 1.0,
    "microns": 1.0,
    "micrometer": 1.0,
    "nm": 1e-3,
    "mm": 1e3,
    "cm": 1e4,
    "m": 1e6,
}
_MODALITIES = {
    "laserscanningconfocalmicroscopy": Modality.CONFOCAL,
    "spinningdiskconfocal": Modality.SPINNING_DISK,
    "widefield": Modality.WIDEFIELD,
}
_UNKNOWN_AXES = "IQ"


def _axes(series: tifffile.TiffPageSeries) -> tuple[str, str | None]:
    """Return the series axes with an unlabelled page axis read as Z.

    The second value is a warning when that assumption was made.
    """
    axes = str(series.axes)
    unknown = [a for a in axes if a in _UNKNOWN_AXES]
    if len(unknown) == 1 and "Z" not in axes:
        warning = "the TIFF does not label its page axis; pages are read as z-planes"
        return axes.replace(unknown[0], "Z"), warning
    return axes, None


def _sizes(series: tifffile.TiffPageSeries) -> dict[str, int]:
    axes, _ = _axes(series)
    sizes = {axis: int(size) for axis, size in zip(axes, series.shape, strict=True)}
    if sizes.get("S", 1) > 1:
        raise UnsupportedFileError("RGB TIFF files are not supported")
    sizes.pop("S", None)
    return sizes


def list_series(path: Path) -> list[SeriesInfo]:
    """List the image series in a TIFF file."""
    with tifffile.TiffFile(path) as handle:
        return [
            SeriesInfo(i, str(series.name) or f"Series {i + 1}", _sizes(series), str(series.dtype))
            for i, series in enumerate(handle.series)
        ]


def _strip_namespaces(root: Element) -> Element:
    for element in root.iter():
        element.tag = element.tag.rpartition("}")[2]
    return root


def _length_um(value: str | None, unit: str | None) -> float | None:
    number = positive(value)
    factor = _LENGTH_TO_UM.get((unit or "µm").strip().lower())
    return None if number is None or factor is None else number * factor


def _ome_acquired(text: str | None) -> Sourced[datetime] | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return None if moment.tzinfo is None else Sourced(moment.astimezone(UTC), "file")


def metadata_from_ome_xml(xml: str, series: int, n_channels: int, n_z: int) -> AcquisitionMetadata:
    """Read acquisition metadata for one image from an OME-XML document."""
    empty = AcquisitionMetadata(channels=tuple(ChannelMetadata(i) for i in range(n_channels)))
    try:
        root = _strip_namespaces(ElementTree.fromstring(xml))
    except ElementTree.ParseError:
        return empty.with_warning("the OME-XML metadata could not be parsed")
    images = root.findall("./Image")
    if series >= len(images):
        return empty.with_warning("the OME-XML metadata does not describe this image series")
    image = images[series]
    pixels = image.find("./Pixels")
    if pixels is None:
        return empty.with_warning("the OME-XML metadata has no Pixels element")

    channel_elements = pixels.findall("./Channel")
    channels = list(empty.channels)
    if len(channel_elements) == n_channels:
        channels = [
            ChannelMetadata(
                i,
                name=file_value(element.get("Name") or None),
                excitation_nm=file_value(positive(element.get("ExcitationWavelength"))),
                emission_nm=file_value(positive(element.get("EmissionWavelength"))),
                pinhole_um=file_value(
                    _length_um(element.get("PinholeSize"), element.get("PinholeSizeUnit"))
                ),
            )
            for i, element in enumerate(channel_elements)
        ]
    modes = {(element.get("AcquisitionMode") or "").lower() for element in channel_elements}
    modality = _MODALITIES.get(modes.pop()) if len(modes) == 1 else None

    settings = image.find("./ObjectiveSettings")
    objective = None
    if settings is not None:
        objective = next(
            (
                o
                for o in root.iterfind("./Instrument/Objective")
                if o.get("ID") == settings.get("ID")
            ),
            None,
        )
    bits = positive(pixels.get("SignificantBits"))
    metadata = replace(
        empty,
        voxel_z_um=file_value(
            _length_um(pixels.get("PhysicalSizeZ"), pixels.get("PhysicalSizeZUnit"))
            if n_z > 1
            else None
        ),
        voxel_y_um=file_value(
            _length_um(pixels.get("PhysicalSizeY"), pixels.get("PhysicalSizeYUnit"))
        ),
        voxel_x_um=file_value(
            _length_um(pixels.get("PhysicalSizeX"), pixels.get("PhysicalSizeXUnit"))
        ),
        numerical_aperture=file_value(
            None if objective is None else positive(objective.get("LensNA"))
        ),
        immersion_ri=file_value(
            None if settings is None else positive(settings.get("RefractiveIndex"))
        ),
        objective_name=file_value(None if objective is None else objective.get("Model") or None),
        magnification=file_value(
            None if objective is None else positive(objective.get("NominalMagnification"))
        ),
        modality=file_value(modality),
        acquired_utc=_ome_acquired(image.findtext("./AcquisitionDate")),
        significant_bits=file_value(None if bits is None else int(bits)),
    )

    # NIS-Elements exports carry the ND2 description text as annotations.
    notes = [
        annotation.findtext("./Value")
        for annotation in root.iterfind("./StructuredAnnotations/CommentAnnotation")
        if annotation.get("Namespace") == "ND2"
    ]
    text = parse_nikon_text(*notes)
    metadata = replace(metadata, channels=apply_nikon_text(channels, text))
    if text.resolution_enhancement is not None:
        metadata = replace(
            metadata, resolution_enhancement=Sourced(text.resolution_enhancement, "file")
        )
    return metadata


def _imagej_metadata(handle: tifffile.TiffFile, n_channels: int, n_z: int) -> AcquisitionMetadata:
    """Read the voxel size ImageJ stores; it stores no optics metadata."""
    metadata = AcquisitionMetadata(channels=tuple(ChannelMetadata(i) for i in range(n_channels)))
    info = handle.imagej_metadata or {}
    factor = _LENGTH_TO_UM.get(str(info.get("unit", "")).strip().lower())
    if factor is None:
        return metadata.with_warning("the ImageJ TIFF has no length unit; voxel size was not read")
    tags = handle.pages[0].tags  # type: ignore[union-attr]
    lateral: dict[str, float | None] = {}
    for axis, tag_name in (("x", "XResolution"), ("y", "YResolution")):
        tag = tags.get(tag_name)
        pixels_per_unit = None
        if tag is not None:
            numerator, denominator = tag.value
            pixels_per_unit = positive(numerator / denominator) if denominator else None
        lateral[axis] = None if pixels_per_unit is None else factor / pixels_per_unit
    spacing = positive(info.get("spacing"))
    return replace(
        metadata,
        voxel_z_um=file_value(None if spacing is None or n_z < 2 else spacing * factor),
        voxel_y_um=file_value(lateral["y"]),
        voxel_x_um=file_value(lateral["x"]),
    )


def open_series(path: Path, series: int, timepoint: int, tile: int) -> SeriesData:
    """Open one series and timepoint of a TIFF file."""
    with tifffile.TiffFile(path) as handle:
        if not 0 <= series < len(handle.series):
            raise UnsupportedFileError(
                f"series {series} is out of range; {path.name} has {len(handle.series)} series"
            )
        page_series = handle.series[series]
        axes, axis_warning = _axes(page_series)
        sizes = _sizes(page_series)
        check_selection(sizes, timepoint=timepoint, tile=tile)
        n_channels, n_z = sizes.get("C", 1), sizes.get("Z", 1)
        if handle.is_ome and handle.ome_metadata:
            metadata = metadata_from_ome_xml(handle.ome_metadata, series, n_channels, n_z)
        elif handle.is_imagej:
            metadata = _imagej_metadata(handle, n_channels, n_z)
        else:
            metadata = AcquisitionMetadata(
                channels=tuple(ChannelMetadata(i) for i in range(n_channels))
            )
        if axis_warning is not None:
            metadata = metadata.with_warning(axis_warning)
        info = SeriesInfo(
            series, str(page_series.name) or f"Series {series + 1}", sizes, str(page_series.dtype)
        )
        dtype = np.dtype(page_series.dtype)

    shape = (n_channels, n_z, sizes["Y"], sizes["X"])
    dims = [a for a in axes if a != "S"]

    def load_channel(channel: int) -> NDArray[Any]:
        with tifffile.TiffFile(path) as source:
            data = np.asarray(source.series[series].asarray())
        if "S" in axes:
            data = data.reshape([n for n, a in zip(data.shape, axes, strict=True) if a != "S"])
        index = tuple(
            slice(timepoint, timepoint + 1)
            if axis == "T"
            else slice(channel, channel + 1)
            if axis == "C"
            else slice(None)
            for axis in dims
        )
        return np.ascontiguousarray(to_zyx(data[index], dims))

    return SeriesData(
        info=info,
        shape=shape,
        dtype=dtype,
        metadata=metadata,
        load_channel=load_channel,
        reader=f"tifffile {version('tifffile')}",
    )
