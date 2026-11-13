"""Tests for reading optics metadata from vendor XML (IMP-04).

The XML snippets reproduce the structure of facility LIF and CZI files, so
these paths are tested in CI where the facility files are not available.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest

from psfwidget.io import Modality
from psfwidget.io._czi import metadata_from_xml
from psfwidget.io._lif import optics_from_xml


def _setting(
    name: str,
    lasers: dict[int, float],
    bands: dict[int, tuple[float, float, str]],
    active: list[int],
    extra: str = "",
) -> str:
    detectors = "".join(
        f'<Detector Name="HyD {slot}" Channel="{slot}" IsActive="{1 if slot in active else 0}"/>'
        for slot in (1, 2, 3, 4)
    )
    multibands = "".join(
        f'<MultiBand Channel="{slot}" LeftWorld="{low}" RightWorld="{high}" DyeName="{dye}"/>'
        for slot, (low, high, dye) in bands.items()
    )
    laser_lines = "".join(
        f'<LaserLineSetting LaserLine="{line}" IntensityDev="{power}"/>'
        for line, power in lasers.items()
    )
    return (
        f'<ATLConfocalSettingDefinition UserSettingName="{name}" NumericalAperture="1.4" '
        f'RefractionIndex="1.518" ObjectiveName="HC PL APO CS2    63x/1.40 OIL " '
        f'Magnification="63" PinholeAiry="1.0" Pinhole="1.0E-04" IsSTEDActive="0" {extra}>'
        f"{detectors}{multibands}{laser_lines}</ATLConfocalSettingDefinition>"
    )


PARKED = {2: (761.0, 766.0, ""), 3: (766.0, 784.0, ""), 4: (794.0, 799.0, "")}
SEQ_GREEN = _setting(
    "Seq. 2", {405: 0, 488: 14.5, 552: 0}, {1: (507, 686, "Leica/ALEXA 488"), **PARKED}, [1]
)
SEQ_ORANGE = _setting(
    "Seq. 3", {488: 0, 552: 2.9}, {1: (558, 761, "Leica/ALEXA 568"), **PARKED}, [1]
)
CURRENT = _setting("S107", {552: 2.9}, {1: (558, 761, "Leica/ALEXA 568"), **PARKED}, [1])
MASTER = _setting(
    "S25",
    {488: 14.5, 552: 2.9, 638: 5.6},
    {1: (411, 612, "Leica/DAPI"), 2: (612, 617, ""), 3: (643, 784, ""), 4: (794, 799, "")},
    [1, 2, 3, 4],
)


def _lif_image(hardware: str) -> ElementTree.Element:
    return ElementTree.fromstring(
        '<Element Name="field"><Data><Image>'
        f'<Attachment Name="HardwareSetting">{hardware}</Attachment>'
        "</Image></Data></Element>"
    )


def test_lif_sequential_scans_map_to_channels_in_order() -> None:
    hardware = (
        f"{CURRENT}<LDM_Block_Sequential>"
        f"<LDM_Block_Sequential_Master>{MASTER}</LDM_Block_Sequential_Master>"
        f"<LDM_Block_Sequential_List>{SEQ_GREEN}{SEQ_ORANGE}</LDM_Block_Sequential_List>"
        "</LDM_Block_Sequential>"
    )
    meta = optics_from_xml(_lif_image(hardware), 2)

    assert meta.numerical_aperture is not None
    assert meta.numerical_aperture.value == 1.4
    assert meta.immersion_ri is not None
    assert meta.immersion_ri.value == 1.518
    assert meta.objective_name is not None
    assert meta.objective_name.value == "HC PL APO CS2 63x/1.40 OIL"
    assert meta.modality is not None
    assert meta.modality.value is Modality.CONFOCAL
    assert meta.resolution_enhancement is None
    assert not meta.warnings

    green, orange = meta.channels
    assert (green.index, orange.index) == (0, 1)
    assert green.name is not None
    assert green.name.value == "ALEXA 488"
    assert green.excitation_nm is not None
    assert green.excitation_nm.value == 488.0
    assert green.detection_band_nm is not None
    assert green.detection_band_nm.value == (507.0, 686.0)
    assert green.emission_nm is not None
    assert green.emission_nm.value == 596.5
    assert "centre of the detection band" in green.emission_nm.note
    assert green.pinhole_airy is not None
    assert green.pinhole_airy.value == 1.0
    assert green.pinhole_um is not None
    assert green.pinhole_um.value == pytest.approx(100.0)
    assert orange.excitation_nm is not None
    assert orange.excitation_nm.value == 552.0
    assert orange.name is not None
    assert orange.name.value == "ALEXA 568"


def test_lif_without_sequences_uses_active_detectors_of_main_block() -> None:
    main = _setting(
        "S6", {}, {2: (511.25, 563.75, ""), 3: (616.25, 668.75, "")}, [2, 3], 'Extra="1"'
    )
    meta = optics_from_xml(_lif_image(main), 2)
    first, second = meta.channels
    assert first.name is not None
    assert first.name.value == "HyD 2"
    assert first.excitation_nm is None  # no laser recorded as on
    assert first.detection_band_nm is not None
    assert first.detection_band_nm.value == (511.25, 563.75)
    assert second.emission_nm is not None
    assert second.emission_nm.value == 642.5


def test_lif_with_several_lasers_picks_the_line_below_the_band() -> None:
    main = _setting("S1", {405: 3, 488: 5, 638: 2}, {1: (500, 550, "")}, [1])
    (channel,) = optics_from_xml(_lif_image(main), 1).channels
    assert channel.excitation_nm is not None
    assert channel.excitation_nm.value == 488.0


def test_lif_channel_count_mismatch_is_reported_not_guessed() -> None:
    meta = optics_from_xml(_lif_image(CURRENT), 3)
    assert len(meta.channels) == 3
    assert all(c.excitation_nm is None and c.emission_nm is None for c in meta.channels)
    assert any("could not match" in warning for warning in meta.warnings)
    assert meta.numerical_aperture is not None  # objective data is still read


def test_lif_without_confocal_settings_is_reported() -> None:
    element = ElementTree.fromstring("<Element><Data><Image/></Data></Element>")
    meta = optics_from_xml(element, 2)
    assert len(meta.channels) == 2
    assert meta.numerical_aperture is None
    assert any("no confocal settings" in warning for warning in meta.warnings)


def test_lif_sted_is_flagged_as_resolution_enhancement() -> None:
    sted = CURRENT.replace('IsSTEDActive="0"', 'IsSTEDActive="1"')
    meta = optics_from_xml(_lif_image(sted), 1)
    assert meta.resolution_enhancement is not None
    assert meta.resolution_enhancement.value == "STED"


def _czi(
    channels: str, *, objective_settings: str = "", extra_objective: str = ""
) -> ElementTree.Element:
    return ElementTree.fromstring(
        "<ImageDocument><Metadata>"
        "<Information><Image>"
        "<AcquisitionDateAndTime>2026-11-11T16:55:57.0457374Z</AcquisitionDateAndTime>"
        "<ComponentBitCount>8</ComponentBitCount>"
        f"{objective_settings}"
        f"<Dimensions><Channels>{channels}</Channels></Dimensions>"
        "</Image><Instrument>"
        "<Microscopes><Microscope><System>LSM 800</System></Microscope></Microscopes>"
        "<Objectives>"
        '<Objective Id="Objective:1" Name="named-in-attribute"><LensNA>1.4</LensNA>'
        "<NominalMagnification>63</NominalMagnification>"
        "<ImmersionRefractiveIndex>1.518</ImmersionRefractiveIndex>"
        "<Manufacturer><Model>Plan-Apochromat 63x/1.40 Oil DIC M27</Model></Manufacturer>"
        f"</Objective>{extra_objective}</Objectives></Instrument></Information>"
        "<Scaling><Items>"
        '<Distance Id="X"><Value>7.05e-08</Value></Distance>'
        '<Distance Id="Y"><Value>7.05e-08</Value></Distance>'
        '<Distance Id="Z"><Value>1.9e-07</Value></Distance>'
        "</Items></Scaling></Metadata></ImageDocument>"
    )


def _czi_channel(name: str, dye_ex: int, dye_em: int, band: str, lasers: list[int]) -> str:
    sources = "".join(
        f"<LightSourceSettings><Wavelength>{w}</Wavelength></LightSourceSettings>" for w in lasers
    )
    return (
        f'<Channel Id="Channel:0" Name="{name}">'
        f"<ExcitationWavelength>{dye_ex}</ExcitationWavelength>"
        f"<EmissionWavelength>{dye_em}</EmissionWavelength>"
        "<PinholeSize>42.99</PinholeSize><PinholeSizeAiry>0.847</PinholeSizeAiry>"
        "<AcquisitionMode>LaserScanningConfocalMicroscopy</AcquisitionMode>"
        f"<DetectionWavelength><Ranges>{band}</Ranges></DetectionWavelength>"
        f"<LightSourcesSettings>{sources}</LightSourcesSettings></Channel>"
    )


TRACK = _czi_channel("AF488-T1", 493, 517, "496.08-570.15", [488, 639]) + _czi_channel(
    "AF647-T1", 653, 668, "643-735", [488, 639]
)


def test_czi_confocal_metadata_and_laser_choice_within_a_track() -> None:
    settings = (
        '<ObjectiveSettings><ObjectiveRef Id="Objective:1"/>'
        "<RefractiveIndex>1.518</RefractiveIndex></ObjectiveSettings>"
    )
    meta = metadata_from_xml(_czi(TRACK, objective_settings=settings), 2, 13)

    assert meta.voxel_x_um is not None
    assert meta.voxel_x_um.value == pytest.approx(0.0705)
    assert meta.voxel_z_um is not None
    assert meta.voxel_z_um.value == pytest.approx(0.19)
    assert meta.numerical_aperture is not None
    assert meta.numerical_aperture.value == 1.4
    assert meta.immersion_ri is not None
    assert meta.immersion_ri.value == 1.518
    assert meta.magnification is not None
    assert meta.magnification.value == 63.0
    assert meta.objective_name is not None
    assert meta.objective_name.value == "Plan-Apochromat 63x/1.40 Oil DIC M27"
    assert meta.modality is not None
    assert meta.modality.value is Modality.CONFOCAL
    assert meta.significant_bits is not None
    assert meta.significant_bits.value == 8
    assert meta.acquired_utc is not None
    assert meta.acquired_utc.value == datetime(2026, 11, 11, 16, 55, 57, 45737, tzinfo=UTC)
    assert meta.instrument_id is None  # the System entry is deliberately not used

    green, far_red = meta.channels
    assert green.name is not None
    assert green.name.value == "AF488-T1"
    assert green.excitation_nm is not None
    assert green.excitation_nm.value == 488.0  # not the dye maximum, 493
    assert "laser line" in green.excitation_nm.note
    assert far_red.excitation_nm is not None
    assert far_red.excitation_nm.value == 639.0
    assert green.emission_nm is not None
    assert green.emission_nm.value == 517.0
    assert green.detection_band_nm is not None
    assert green.detection_band_nm.value == (496.08, 570.15)
    assert green.pinhole_airy is not None
    assert green.pinhole_airy.value == 0.847
    assert green.pinhole_um is not None
    assert green.pinhole_um.value == 42.99


def test_czi_widefield_without_lasers_or_refractive_index() -> None:
    channels = (
        '<Channel Id="Channel:1"><ExcitationWavelength>508</ExcitationWavelength>'
        "<EmissionWavelength>524</EmissionWavelength><Fluor>TagYFP</Fluor>"
        "<AcquisitionMode>WideField</AcquisitionMode></Channel>"
        '<Channel Id="Channel:3"><Fluor>TL DIC</Fluor>'
        "<AcquisitionMode>WideField</AcquisitionMode></Channel>"
    )
    root = _czi(channels)
    objective = root.find(".//Objective")
    assert objective is not None
    index = objective.find("ImmersionRefractiveIndex")
    assert index is not None
    objective.remove(index)
    meta = metadata_from_xml(root, 2, 21)

    assert meta.modality is not None
    assert meta.modality.value is Modality.WIDEFIELD
    assert meta.immersion_ri is None
    assert meta.numerical_aperture is not None  # the only objective is used without a reference
    fluorescence, transmitted = meta.channels
    assert fluorescence.name is not None
    assert fluorescence.name.value == "TagYFP"
    assert fluorescence.excitation_nm is not None
    assert fluorescence.excitation_nm.value == 508.0
    assert "dye's excitation maximum" in fluorescence.excitation_nm.note
    assert fluorescence.detection_band_nm is None
    assert transmitted.excitation_nm is None
    assert transmitted.emission_nm is None


def test_czi_single_plane_has_no_z_spacing_and_two_objectives_need_a_reference() -> None:
    other = '<Objective Id="Objective:2"><LensNA>0.8</LensNA></Objective>'
    meta = metadata_from_xml(_czi(TRACK, extra_objective=other), 2, 1)
    assert meta.voxel_z_um is None
    assert meta.voxel_x_um is not None
    assert meta.numerical_aperture is None


def test_czi_channel_count_mismatch_and_mixed_modes() -> None:
    meta = metadata_from_xml(_czi(TRACK), 3, 13)
    assert len(meta.channels) == 3
    assert all(c.name is None for c in meta.channels)
    assert any("describes 2 channel(s)" in warning for warning in meta.warnings)

    mixed = TRACK.replace("LaserScanningConfocalMicroscopy", "WideField", 1)
    assert metadata_from_xml(_czi(mixed), 2, 13).modality is None


def test_czi_unparseable_values_are_treated_as_missing() -> None:
    broken = TRACK.replace("496.08-570.15", "n/a").replace(
        "<PinholeSizeAiry>0.847</PinholeSizeAiry>", "<PinholeSizeAiry>-1</PinholeSizeAiry>", 1
    )
    root = _czi(broken)
    date = root.find(".//AcquisitionDateAndTime")
    assert date is not None
    date.text = "not a date"
    meta = metadata_from_xml(root, 2, 13)
    assert meta.acquired_utc is None
    assert meta.channels[0].detection_band_nm is None
    assert meta.channels[0].pinhole_airy is None
    # Without a band the dye maximum (493) picks the nearest laser.
    assert meta.channels[0].excitation_nm is not None
    assert meta.channels[0].excitation_nm.value == 488.0
