"""Leica LIF reader, built on the ``liffile`` library (BSD-3-Clause).

Optics metadata is taken from the image's own XML. Leica stores one
``ATLConfocalSettingDefinition`` block per sequential scan, plus a current
and a master block that do not describe the image channels, and every block
lists all detector slots. Image channels therefore map to the *active*
detectors of the sequential blocks, in order.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import liffile
import numpy as np
from numpy.typing import NDArray

from psfwidget.io._common import (
    SeriesData,
    UnsupportedFileError,
    band_centre,
    check_selection,
    file_value,
    pick_excitation,
    step_from_coordinates,
)
from psfwidget.io.metadata import positive
from psfwidget.io.model import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    SeriesInfo,
    Sourced,
)

_SETTING = "ATLConfocalSettingDefinition"
_HARDWARE = "./Data/Image/Attachment[@Name='HardwareSetting']"
_METRES_TO_UM = 1e6


def _series_info(index: int, image: liffile.LifImageABC) -> SeriesInfo:
    sizes = {str(axis): int(size) for axis, size in image.sizes.items()}
    return SeriesInfo(index, str(image.path), sizes, str(image.dtype))


def list_series(path: Path) -> list[SeriesInfo]:
    """List the images in a LIF file."""
    with liffile.LifFile(path) as handle:
        return [_series_info(i, image) for i, image in enumerate(handle.images)]


def _channels_of_setting(setting: Element) -> list[ChannelMetadata]:
    """Return one channel per active detector of a settings block."""
    bands = {
        band.get("Channel"): (positive(band.get("LeftWorld")), positive(band.get("RightWorld")))
        for band in setting.iter("MultiBand")
    }
    dyes = {band.get("Channel"): (band.get("DyeName") or "") for band in setting.iter("MultiBand")}
    lasers = [
        wavelength
        for line in setting.iter("LaserLineSetting")
        if (positive(line.get("IntensityDev")) is not None)
        and (wavelength := positive(line.get("LaserLine"))) is not None
    ]
    pinhole_airy = positive(setting.get("PinholeAiry"))
    pinhole_m = positive(setting.get("Pinhole"))

    channels: list[ChannelMetadata] = []
    for detector in setting.iter("Detector"):
        if detector.get("IsActive") != "1":
            continue
        slot = detector.get("Channel")
        low, high = bands.get(slot, (None, None))
        band = (low, high) if low is not None and high is not None and low < high else None
        excitation = pick_excitation(lasers, band)
        dye = dyes.get(slot, "").split("/")[-1].strip()
        name = dye or (detector.get("Name") or "").strip() or None
        channels.append(
            ChannelMetadata(
                index=-1,
                name=file_value(name),
                excitation_nm=file_value(excitation),
                emission_nm=(
                    None
                    if band is None
                    else Sourced(
                        band_centre(band),
                        "file",
                        f"centre of the detection band {band[0]:g}-{band[1]:g} nm",
                    )
                ),
                detection_band_nm=file_value(band),
                pinhole_airy=file_value(pinhole_airy),
                pinhole_um=file_value(None if pinhole_m is None else pinhole_m * _METRES_TO_UM),
            )
        )
    return channels


def optics_from_xml(element: Element, n_channels: int) -> AcquisitionMetadata:
    """Read optics and channel metadata from a LIF image's XML element."""
    metadata = AcquisitionMetadata(channels=tuple(ChannelMetadata(i) for i in range(n_channels)))
    hardware = element.find(_HARDWARE)
    main = None if hardware is None else hardware.find(_SETTING)
    if hardware is None or main is None:
        return metadata.with_warning(
            "no confocal settings block found in the LIF image; optics metadata was not read"
        )

    sequential = hardware.findall(f"./LDM_Block_Sequential/LDM_Block_Sequential_List/{_SETTING}")
    from_sequences = [c for setting in sequential for c in _channels_of_setting(setting)]
    from_main = _channels_of_setting(main)
    if len(from_sequences) == n_channels:
        channels = from_sequences
    elif len(from_main) == n_channels:
        channels = from_main
    else:
        channels = []
        metadata = metadata.with_warning(
            f"could not match the image's {n_channels} channel(s) to detector settings "
            f"({len(from_sequences)} in sequential scans, {len(from_main)} in the main block); "
            "channel wavelengths were not read"
        )
    if channels:
        metadata = replace(
            metadata, channels=tuple(replace(c, index=i) for i, c in enumerate(channels))
        )

    objective = " ".join((main.get("ObjectiveName") or "").split())
    enhancement = "STED" if main.get("IsSTEDActive") == "1" else None
    return replace(
        metadata,
        numerical_aperture=file_value(positive(main.get("NumericalAperture"))),
        immersion_ri=file_value(positive(main.get("RefractionIndex"))),
        objective_name=file_value(objective or None),
        magnification=file_value(positive(main.get("Magnification"))),
        modality=Sourced(Modality.CONFOCAL, "file"),
        resolution_enhancement=file_value(enhancement),
    )


def _voxel(image: liffile.LifImage, axis: str) -> Sourced[float] | None:
    coords = image.coords.get(axis)
    if coords is None:
        return None
    step = step_from_coordinates([float(v) for v in np.asarray(coords)])
    if step is None or step == 0:
        return None
    note = "the file stores a negative step; the absolute value is used" if step < 0 else ""
    return Sourced(abs(step) * _METRES_TO_UM, "file", note)


def _acquired(image: liffile.LifImage) -> Sourced[datetime] | None:
    stamps = image.timestamps
    if stamps is None or len(stamps) == 0:
        return None
    seconds = np.datetime64(stamps[0], "us").astype("int64") / 1e6
    return Sourced(datetime.fromtimestamp(float(seconds), tz=UTC), "file")


def _significant_bits(element: Element) -> int | None:
    resolutions = {c.get("Resolution") for c in element.iter("ChannelDescription")}
    if len(resolutions) != 1:
        return None
    value = positive(resolutions.pop())
    return None if value is None else int(value)


def open_series(path: Path, series: int, timepoint: int, tile: int) -> SeriesData:
    """Open one image, timepoint, and tile of a LIF file."""
    with liffile.LifFile(path) as handle:
        images = handle.images
        if not 0 <= series < len(images):
            raise UnsupportedFileError(
                f"series {series} is out of range; {path.name} has {len(images)} series"
            )
        image = images[series]
        if not isinstance(image, liffile.LifImage):
            raise UnsupportedFileError(
                f"series {series} of {path.name} is not a plain image (e.g. FLIM data)"
            )
        info = _series_info(series, image)
        check_selection(info.sizes, timepoint=timepoint, tile=tile)
        n_channels, n_z = info.sizes.get("C", 1), info.sizes.get("Z", 1)
        metadata = replace(
            optics_from_xml(image.xml_element, n_channels),
            voxel_z_um=_voxel(image, "Z"),
            voxel_y_um=_voxel(image, "Y"),
            voxel_x_um=_voxel(image, "X"),
            acquired_utc=_acquired(image),
            significant_bits=file_value(_significant_bits(image.xml_element)),
        )
        dtype = np.dtype(image.dtype)

    shape = (n_channels, n_z, info.sizes["Y"], info.sizes["X"])
    fixed = {"T": timepoint, "M": tile}

    def load_channel(channel: int) -> NDArray[Any]:
        with liffile.LifFile(path) as source:
            target = source.images[series]
            assert isinstance(target, liffile.LifImage)  # checked when the series was opened
            planes = []
            for z in range(n_z):
                wanted = {**fixed, "C": channel, "Z": z}
                indices: dict[str, Any] = {
                    axis: i for axis, i in wanted.items() if axis in info.sizes
                }
                planes.append(np.asarray(target.frame(**indices)))
            # Frames can be views of the memory-mapped file: copy before it closes.
            return np.stack(planes)

    return SeriesData(
        info=info,
        shape=shape,
        dtype=dtype,
        metadata=metadata,
        load_channel=load_channel,
        reader=f"liffile {version('liffile')}",
    )
