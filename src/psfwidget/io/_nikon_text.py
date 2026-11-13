"""Parse the free-text acquisition descriptions NIS-Elements stores in ND2 files.

The same text is carried into OME-TIFF exports as annotations. It corrects
two faults in the structured metadata seen in facility files:

* AX confocal files report the lower edge of the detection band as the
  emission wavelength.
* Spinning-disk files can report the wrong excitation wavelength, and no
  emission wavelength, for a channel.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from psfwidget.io._common import band_centre
from psfwidget.io.model import ChannelMetadata, Sourced

_PLANE = re.compile(r"^\s*Plane #\d+:\s*$", re.MULTILINE)
_NAME = re.compile(r"^\s*Name:\s*(.+?)\s*$", re.MULTILINE)
_LASER_ON = re.compile(r"Line:\s*\d+;\s*ExW:\s*([\d.]+);[^\n]*;\s*On\b")
_COMPONENT = re.compile(r"^\s*Component (.+?):\s*$", re.MULTILINE)
_EMISSION_RANGE = re.compile(r"Emission Range:\s*([\d.]+)\s*nm\s*-\s*([\d.]+)\s*nm")
_NSPARC_MODE = re.compile(r"NSPARC Mode:\s*(\S+)")
_NSPARC_DETECTOR = re.compile(r"Detector mode:\s*NSPARC")
_LIVE_SR = re.compile(r"Modality:[^:]*\bLive-SR\b")

_EXCITATION_TOLERANCE_NM = 5.0
_BAND_EDGE_TOLERANCE_NM = 0.5


@dataclass(frozen=True)
class NikonText:
    """Facts extracted from NIS-Elements description text."""

    lasers: dict[str, float] = field(default_factory=dict)
    """Laser line switched on for each channel, keyed by channel name."""
    bands: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Detection band of each channel, keyed by channel name."""
    resolution_enhancement: str | None = None


def _blocks(pattern: re.Pattern[str], text: str) -> list[tuple[re.Match[str], str]]:
    """Split ``text`` into the blocks that follow each match of ``pattern``."""
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    ends = [m.start() for m in matches[1:]] + [len(text)]
    return [(m, text[m.end() : end]) for m, end in zip(matches, ends, strict=True)]


def parse_nikon_text(*texts: str | None) -> NikonText:
    """Extract lasers, detection bands, and enhancement mode from description text."""
    text = "\n".join(t.replace("\r\n", "\n") for t in texts if t)
    lasers: dict[str, float] = {}
    for _, block in _blocks(_PLANE, text):
        name = _NAME.search(block)
        on = {float(w) for w in _LASER_ON.findall(block)}
        if name is not None and len(on) == 1:
            lasers.setdefault(name.group(1), on.pop())

    bands: dict[str, tuple[float, float]] = {}
    for match, block in _blocks(_COMPONENT, text):
        emission = _EMISSION_RANGE.search(block)
        if emission is not None:
            low, high = float(emission.group(1)), float(emission.group(2))
            if low < high:
                bands.setdefault(match.group(1), (low, high))

    enhancement: str | None = None
    mode = _NSPARC_MODE.search(text)
    if mode is not None:
        enhancement = f"NSPARC {mode.group(1)}"
    elif _NSPARC_DETECTOR.search(text):
        enhancement = "NSPARC"
    elif _LIVE_SR.search(text):
        enhancement = "Live-SR"
    return NikonText(lasers=lasers, bands=bands, resolution_enhancement=enhancement)


def apply_nikon_text(
    channels: Sequence[ChannelMetadata], text: NikonText
) -> tuple[ChannelMetadata, ...]:
    """Correct structured channel metadata using the description text."""
    corrected: list[ChannelMetadata] = []
    for channel in channels:
        name = channel.name.value if channel.name is not None else None
        if name is None:
            corrected.append(channel)
            continue

        band = text.bands.get(name)
        if band is not None:
            channel = replace(channel, detection_band_nm=Sourced(band, "file"))
            reported = channel.emission_nm.value if channel.emission_nm is not None else None
            at_lower_edge = (
                reported is not None and abs(reported - band[0]) <= _BAND_EDGE_TOLERANCE_NM
            )
            outside = reported is not None and not band[0] <= reported <= band[1]
            if reported is None or at_lower_edge or outside:
                was = "no emission wavelength" if reported is None else f"{reported:g} nm"
                note = (
                    f"centre of the detection band {band[0]:g}-{band[1]:g} nm; "
                    f"the structured metadata reports {was}"
                )
                channel = replace(channel, emission_nm=Sourced(band_centre(band), "file", note))

        laser = text.lasers.get(name)
        if laser is not None:
            reported = channel.excitation_nm.value if channel.excitation_nm is not None else None
            if reported is None or abs(reported - laser) > _EXCITATION_TOLERANCE_NM:
                was = "no excitation wavelength" if reported is None else f"{reported:g} nm"
                note = (
                    "laser line switched on in the acquisition description; "
                    f"the structured metadata reports {was}"
                )
                channel = replace(channel, excitation_nm=Sourced(laser, "file", note))
        corrected.append(channel)
    return tuple(corrected)
