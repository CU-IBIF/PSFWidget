"""Zeiss CZI reader, built on the ``czifile`` library (BSD-3-Clause).

Metadata is read from the file's XML. Two points are handled deliberately:

* The channel's ``ExcitationWavelength`` is the dye's excitation maximum, not
  the laser line. The laser is taken from the channel's light-source
  settings instead, choosing the line just below the detection band when a
  track has several lasers on.
* The ``Microscope/System`` entry is not used. A facility LSM 980 file
  reports ``LSM 800`` there.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import czifile
import numpy as np
from numpy.typing import NDArray

from psfwidget.io._common import (
    SeriesData,
    UnsupportedFileError,
    check_selection,
    file_value,
    pick_excitation,
    to_zyx,
)
from psfwidget.io.metadata import positive
from psfwidget.io.model import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    SeriesInfo,
    Sourced,
)

_METRES_TO_UM = 1e6
_MODALITIES = {
    "laserscanningconfocalmicroscopy": Modality.CONFOCAL,
    "multiphotonmicroscopy": Modality.MULTIPHOTON,
    "spinningdiskconfocal": Modality.SPINNING_DISK,
    "widefield": Modality.WIDEFIELD,
}


def _text(element: Element | None, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _series_info(index: int, image: czifile.CziImage) -> SeriesInfo:
    sizes = {str(axis): int(size) for axis, size in image.sizes.items()}
    return SeriesInfo(index, str(image.name), sizes, str(image.dtype))


def _scenes(handle: czifile.CziFile) -> list[czifile.CziImage]:
    scenes = handle.scenes
    return [scenes[key] for key in sorted(scenes.keys())]


def list_series(path: Path) -> list[SeriesInfo]:
    """List the scenes in a CZI file."""
    with czifile.CziFile(path) as handle:
        return [_series_info(i, image) for i, image in enumerate(_scenes(handle))]


def _band(channel: Element) -> tuple[float, float] | None:
    ranges = _text(channel, "./DetectionWavelength/Ranges")
    if ranges is None:
        return None
    parts = ranges.split("-")
    if len(parts) != 2:
        return None
    low, high = positive(parts[0]), positive(parts[1])
    if low is None or high is None or low >= high:
        return None
    return (low, high)


def _channel(index: int, channel: Element) -> ChannelMetadata:
    band = _band(channel)
    dye_excitation = positive(_text(channel, "./ExcitationWavelength"))
    lasers = [
        wavelength
        for source in channel.iterfind("./LightSourcesSettings/LightSourceSettings")
        if (wavelength := positive(_text(source, "./Wavelength"))) is not None
    ]
    laser = pick_excitation(lasers, band, dye_excitation)
    if laser is not None:
        excitation = Sourced(laser, "file", "laser line from the channel's light-source settings")
    elif dye_excitation is not None:
        excitation = Sourced(
            dye_excitation, "file", "the dye's excitation maximum; no laser line was recorded"
        )
    else:
        excitation = None

    emission = positive(_text(channel, "./EmissionWavelength"))
    return ChannelMetadata(
        index,
        name=file_value(channel.get("Name") or _text(channel, "./Fluor")),
        excitation_nm=excitation,
        emission_nm=(
            None
            if emission is None
            else Sourced(emission, "file", "the dye's emission maximum as entered in ZEN")
        ),
        detection_band_nm=file_value(band),
        pinhole_airy=file_value(positive(_text(channel, "./PinholeSizeAiry"))),
        pinhole_um=file_value(positive(_text(channel, "./PinholeSize"))),
    )


def _acquired(text: str | None) -> Sourced[datetime] | None:
    if text is None:
        return None
    # ZEN writes seven fractional digits; datetime accepts at most six.
    head, dot, tail = text.partition(".")
    if dot:
        digits = "".join(c for c in tail if c.isdigit())
        suffix = tail[len(digits) :]
        text = f"{head}.{digits[:6]}{suffix}"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return Sourced(moment.astimezone(UTC), "file")


def metadata_from_xml(root: Element, n_channels: int, n_z: int) -> AcquisitionMetadata:
    """Read acquisition metadata from a CZI metadata XML tree."""
    base = root.find("./Metadata")
    if base is None:
        base = root
    image = base.find("./Information/Image")

    scaling: dict[str, float | None] = {}
    for distance in base.iterfind("./Scaling/Items/Distance"):
        value = positive(_text(distance, "./Value"))
        scaling[distance.get("Id", "")] = None if value is None else value * _METRES_TO_UM

    channel_elements = [] if image is None else image.findall("./Dimensions/Channels/Channel")
    metadata = AcquisitionMetadata(
        voxel_z_um=file_value(scaling.get("Z") if n_z > 1 else None),
        voxel_y_um=file_value(scaling.get("Y")),
        voxel_x_um=file_value(scaling.get("X")),
        acquired_utc=_acquired(_text(image, "./AcquisitionDateAndTime")),
    )
    if len(channel_elements) == n_channels:
        channels = tuple(_channel(i, element) for i, element in enumerate(channel_elements))
    else:
        channels = tuple(ChannelMetadata(i) for i in range(n_channels))
        metadata = metadata.with_warning(
            f"the file describes {len(channel_elements)} channel(s) but the image has "
            f"{n_channels}; channel metadata was not read"
        )

    modes = {(_text(element, "./AcquisitionMode") or "").lower() for element in channel_elements}
    modality = _MODALITIES.get(modes.pop()) if len(modes) == 1 else None

    objective = None
    reference = None if image is None else image.find("./ObjectiveSettings/ObjectiveRef")
    objectives = base.findall("./Information/Instrument/Objectives/Objective")
    if reference is not None:
        objective = next((o for o in objectives if o.get("Id") == reference.get("Id")), None)
    if objective is None and len(objectives) == 1:
        objective = objectives[0]

    refractive_index = positive(_text(image, "./ObjectiveSettings/RefractiveIndex")) or positive(
        _text(objective, "./ImmersionRefractiveIndex")
    )
    name = _text(objective, "./Manufacturer/Model") or (
        None if objective is None else objective.get("Name")
    )
    bits = positive(_text(image, "./ComponentBitCount"))
    return replace(
        metadata,
        channels=channels,
        numerical_aperture=file_value(positive(_text(objective, "./LensNA"))),
        immersion_ri=file_value(refractive_index),
        objective_name=file_value(name or None),
        magnification=file_value(positive(_text(objective, "./NominalMagnification"))),
        modality=file_value(modality),
        significant_bits=file_value(None if bits is None else int(bits)),
    )


def open_series(path: Path, series: int, timepoint: int, tile: int) -> SeriesData:
    """Open one scene and timepoint of a CZI file."""
    with czifile.CziFile(path) as handle:
        scenes = _scenes(handle)
        if not 0 <= series < len(scenes):
            raise UnsupportedFileError(
                f"series {series} is out of range; {path.name} has {len(scenes)} series"
            )
        image = scenes[series]
        info = _series_info(series, image)
        check_selection(info.sizes, timepoint=timepoint, tile=tile)
        n_channels, n_z = info.sizes.get("C", 1), info.sizes.get("Z", 1)
        root = handle.xml_element
        if root is None:
            root = Element("ImageDocument")
        metadata = metadata_from_xml(root, n_channels, n_z)
        dtype = np.dtype(image.dtype)

    shape = (n_channels, n_z, info.sizes["Y"], info.sizes["X"])
    wanted = {"T": timepoint, "M": tile}

    def load_channel(channel: int) -> NDArray[Any]:
        selection: dict[str, Any] = {
            axis: i for axis, i in {**wanted, "C": channel}.items() if axis in info.sizes
        }
        with czifile.CziFile(path) as source:
            selected = _scenes(source)[series](**selection)
            data = to_zyx(np.asarray(selected.asarray()), [str(d) for d in selected.dims])
            return np.ascontiguousarray(data)

    return SeriesData(
        info=info,
        shape=shape,
        dtype=dtype,
        metadata=metadata,
        load_channel=load_channel,
        reader=f"czifile {version('czifile')}",
    )
