"""Tests for parsing NIS-Elements description text (IMP-04).

The snippets reproduce the layout of facility ND2 files.
"""

from __future__ import annotations

from psfwidget.io import ChannelMetadata, Sourced
from psfwidget.io._nikon_text import NikonText, apply_nikon_text, parse_nikon_text

SPINNING_DISK = """Metadata:
Dimensions: Z(21) x λ(3)
Camera Name: Kinetix, SERIAL
Numerical Aperture: 1.4
Plane #1:
 Name: TRITC_SR
 Component Count: 1
 Modality: Widefield Fluorescence, Spinning Disk Confocal, \r
           Live-SR
 Camera Settings:   Binning: 1x1
 Microscope Settings:   Microscope: Ti2 Microscope
  NIDAQ, MultiLaser(AOTF):
     Line:3; ExW:550; Power: 40.0; On

Plane #2:
 Name: GFP_SR
 Modality: Widefield Fluorescence, Spinning Disk Confocal,
           Live-SR
  NIDAQ, MultiLaser(AOTF):
     Line:2; ExW:488; Power: 34.0; On

Plane #3:
 Name: Cy5_SR
  NIDAQ, MultiLaser(AOTF):
     Line:1; ExW:405; Power: 10.0; Off
     Line:4; ExW:640; Power: 43.0; On
"""

AX_CAPTURING = """AX
Configuration:
\tDetector mode: NSPARC
\tScanner: Galvano Unidirectional
\tNSPARC Mode: SR

Components:
Component AF405:
\tDetector: 1
\tEmission Range: 430 nm - 463 nm
\tLaser 405 nm:
\t\tEnabled

Component AF488:
\tDetector: 1
\tEmission Range: 503 nm - 545 nm
\tLaser 488 nm:
\t\tEnabled

Scan Area:
\tZoom: 5.90
"""


def test_spinning_disk_text_gives_laser_per_channel_and_live_sr() -> None:
    text = parse_nikon_text(SPINNING_DISK, None)
    assert text.lasers == {"TRITC_SR": 550.0, "GFP_SR": 488.0, "Cy5_SR": 640.0}
    assert text.bands == {}
    assert text.resolution_enhancement == "Live-SR"


def test_ax_text_gives_detection_bands_and_nsparc_mode() -> None:
    text = parse_nikon_text(None, AX_CAPTURING)
    assert text.bands == {"AF405": (430.0, 463.0), "AF488": (503.0, 545.0)}
    assert text.lasers == {}
    assert text.resolution_enhancement == "NSPARC SR"


def test_nsparc_without_mode_and_plain_text() -> None:
    assert parse_nikon_text("Detector mode: NSPARC").resolution_enhancement == "NSPARC"
    assert parse_nikon_text("Modality: Widefield Fluorescence\n Name: x") == NikonText()
    assert parse_nikon_text(None, None) == NikonText()
    assert parse_nikon_text("") == NikonText()


def test_plane_with_two_lasers_on_is_left_undecided() -> None:
    text = parse_nikon_text(
        "Plane #1:\n Name: dual\n  Line:1; ExW:488; Power: 1; On\n  Line:2; ExW:561; Power: 1; On\n"
    )
    assert text.lasers == {}


def test_wrong_excitation_is_replaced_and_explained() -> None:
    channels = [
        ChannelMetadata(0, name=Sourced("TRITC_SR"), excitation_nm=Sourced(550.0)),
        ChannelMetadata(1, name=Sourced("Cy5_SR"), excitation_nm=Sourced(550.0)),
        ChannelMetadata(2, name=Sourced("GFP_SR")),
        ChannelMetadata(3),
    ]
    fixed = apply_nikon_text(channels, parse_nikon_text(SPINNING_DISK))

    assert fixed[0] == channels[0]  # already agrees with the text
    assert fixed[1].excitation_nm is not None
    assert fixed[1].excitation_nm.value == 640.0
    assert "structured metadata reports 550 nm" in fixed[1].excitation_nm.note
    assert fixed[2].excitation_nm is not None
    assert fixed[2].excitation_nm.value == 488.0
    assert "no excitation wavelength" in fixed[2].excitation_nm.note
    assert fixed[3] == channels[3]  # unnamed channels cannot be matched


def test_emission_at_band_edge_is_replaced_by_band_centre() -> None:
    channels = [
        ChannelMetadata(0, name=Sourced("AF405"), emission_nm=Sourced(430.0)),
        ChannelMetadata(1, name=Sourced("AF488"), emission_nm=Sourced(520.0)),
        ChannelMetadata(2, name=Sourced("AF488")),
        ChannelMetadata(3, name=Sourced("AF405"), emission_nm=Sourced(700.0)),
        ChannelMetadata(4, name=Sourced("other"), emission_nm=Sourced(600.0)),
    ]
    fixed = apply_nikon_text(channels, parse_nikon_text(AX_CAPTURING))

    assert fixed[0].detection_band_nm == Sourced((430.0, 463.0))
    assert fixed[0].emission_nm is not None
    assert fixed[0].emission_nm.value == 446.5
    assert "reports 430 nm" in fixed[0].emission_nm.note
    # A value inside the band and away from its edge is kept.
    assert fixed[1].emission_nm == Sourced(520.0)
    assert fixed[1].detection_band_nm == Sourced((503.0, 545.0))
    assert fixed[2].emission_nm is not None
    assert fixed[2].emission_nm.value == 524.0
    assert "no emission wavelength" in fixed[2].emission_nm.note
    assert fixed[3].emission_nm is not None
    assert fixed[3].emission_nm.value == 446.5  # reported value was outside the band
    assert fixed[4] == channels[4]
