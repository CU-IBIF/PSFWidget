"""Tests for the TIFF reader using files written during the test (IMP-01, IMP-04)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget.io import Modality, UnsupportedFileError, list_series, read_stack
from psfwidget.io._tiff import metadata_from_ome_xml

RNG = np.random.default_rng(5)
ZCYX = RNG.integers(0, 4000, size=(6, 3, 12, 10), dtype=np.uint16)

OME_NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"


def _ome_xml(
    *, annotations: str = "", pixels_extra: str = "", date: str = "2026-11-11T18:29:37Z"
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OME xmlns="{OME_NS}">
 <Instrument ID="Instrument:0">
  <Objective ID="Objective:0" LensNA="1.4" Model="Plan Apo 60x Oil" NominalMagnification="60"/>
  <Objective ID="Objective:1" LensNA="0.8" Model="other"/>
 </Instrument>
 <Image ID="Image:0">
  <AcquisitionDate>{date}</AcquisitionDate>
  <ObjectiveSettings ID="Objective:0" RefractiveIndex="1.515"/>
  <Pixels ID="Pixels:0" DimensionOrder="XYCZT" Type="uint16" SizeX="10" SizeY="12" SizeZ="6"
          SizeC="3" SizeT="1" SignificantBits="12" PhysicalSizeX="42.3788" PhysicalSizeXUnit="nm"
          PhysicalSizeY="0.0423788" PhysicalSizeZ="0.3" PhysicalSizeZUnit="µm" {pixels_extra}>
   <Channel ID="Channel:0:0" Name="TRITC_SR" AcquisitionMode="SpinningDiskConfocal"
            ExcitationWavelength="550" EmissionWavelength="603.5" PinholeSize="50"
            PinholeSizeUnit="µm" SamplesPerPixel="1"/>
   <Channel ID="Channel:0:1" Name="GFP_SR" AcquisitionMode="SpinningDiskConfocal"
            ExcitationWavelength="488" EmissionWavelength="535" PinholeSize="50" SamplesPerPixel="1"/>
   <Channel ID="Channel:0:2" Name="Cy5_SR" AcquisitionMode="SpinningDiskConfocal"
            ExcitationWavelength="550" PinholeSize="50" SamplesPerPixel="1"/>
   <TiffData/>
  </Pixels>
 </Image>
 {annotations}
</OME>"""


ND2_ANNOTATIONS = """<StructuredAnnotations>
 <CommentAnnotation ID="Annotation:0" Namespace="ND2"><Description>TextInfo:Description</Description>
  <Value>Plane #1:
 Name: TRITC_SR
 Modality: Widefield Fluorescence, Spinning Disk Confocal,
           Live-SR
     Line:3; ExW:550; Power: 40.0; On
Plane #3:
 Name: Cy5_SR
     Line:4; ExW:640; Power: 43.0; On
</Value></CommentAnnotation>
 <CommentAnnotation ID="Annotation:1" Namespace="other"><Value>NSPARC Mode: SR</Value></CommentAnnotation>
</StructuredAnnotations>"""


def _write_ome(path: Path, xml: str) -> Path:
    tifffile.imwrite(
        path, ZCYX, photometric="minisblack", description=xml.encode("utf-8"), metadata=None
    )
    return path


@pytest.mark.req("IMP-01", "IMP-04")
def test_ome_tiff_metadata_and_pixels(tmp_path: Path) -> None:
    path = _write_ome(tmp_path / "beads.ome.tif", _ome_xml(annotations=ND2_ANNOTATIONS))
    (series,) = list_series(path)
    assert series.sizes == {"Z": 6, "C": 3, "Y": 12, "X": 10}

    stack = read_stack(path)
    meta = stack.metadata
    assert stack.shape == (3, 6, 12, 10)
    np.testing.assert_array_equal(stack.asarray(), np.moveaxis(ZCYX, 1, 0))
    assert stack.provenance.format == "tiff"
    assert stack.voxel_size_um == pytest.approx((0.3, 0.0423788, 0.0423788))
    assert meta.numerical_aperture is not None
    assert meta.numerical_aperture.value == 1.4
    assert meta.immersion_ri is not None
    assert meta.immersion_ri.value == 1.515
    assert meta.objective_name is not None
    assert meta.objective_name.value == "Plan Apo 60x Oil"
    assert meta.magnification is not None
    assert meta.magnification.value == 60.0
    assert meta.modality is not None
    assert meta.modality.value is Modality.SPINNING_DISK
    assert meta.significant_bits is not None
    assert meta.significant_bits.value == 12
    assert meta.acquired_utc is not None
    assert meta.acquired_utc.value == datetime(2026, 11, 11, 18, 29, 37, tzinfo=UTC)
    # Only annotations in the ND2 namespace are used.
    assert meta.resolution_enhancement is not None
    assert meta.resolution_enhancement.value == "Live-SR"

    orange, green, far_red = meta.channels
    assert orange.name is not None
    assert orange.name.value == "TRITC_SR"
    assert orange.pinhole_um is not None
    assert orange.pinhole_um.value == 50.0
    assert green.emission_nm is not None
    assert green.emission_nm.value == 535.0
    assert far_red.excitation_nm is not None
    assert far_red.excitation_nm.value == 640.0  # corrected from the description text
    assert far_red.emission_nm is None
    assert stack.problems() == ["missing emission_nm for channel 2 (Cy5_SR)"]


def test_ome_xml_edge_cases() -> None:
    plain = metadata_from_ome_xml(_ome_xml(date="2026-11-11T18:29:37"), 0, 3, 6)
    assert plain.acquired_utc is None  # no time zone given
    assert plain.resolution_enhancement is None
    assert plain.channels[2].excitation_nm is not None
    assert plain.channels[2].excitation_nm.value == 550.0

    assert metadata_from_ome_xml(_ome_xml(date="garbage"), 0, 3, 6).acquired_utc is None
    single_plane = metadata_from_ome_xml(_ome_xml(), 0, 3, 1)
    assert single_plane.voxel_z_um is None
    wrong_count = metadata_from_ome_xml(_ome_xml(), 0, 2, 6)
    assert all(c.name is None for c in wrong_count.channels)
    odd_unit = metadata_from_ome_xml(_ome_xml(pixels_extra='PhysicalSizeYUnit="furlong"'), 0, 3, 6)
    assert odd_unit.voxel_y_um is None

    assert any("could not be parsed" in w for w in metadata_from_ome_xml("<OME", 0, 1, 1).warnings)
    assert any(
        "does not describe" in w for w in metadata_from_ome_xml(_ome_xml(), 4, 3, 6).warnings
    )
    no_pixels = f'<OME xmlns="{OME_NS}"><Image ID="Image:0"/></OME>'
    assert any("no Pixels" in w for w in metadata_from_ome_xml(no_pixels, 0, 1, 1).warnings)


@pytest.mark.req("IMP-01", "IMP-06")
def test_imagej_tiff_keeps_voxel_size_only(tmp_path: Path) -> None:
    path = tmp_path / "beads.tif"
    tifffile.imwrite(
        path,
        ZCYX,
        imagej=True,
        resolution=(1 / 0.05, 1 / 0.04),
        metadata={"axes": "ZCYX", "spacing": 0.3, "unit": "micron"},
    )
    stack = read_stack(path)
    assert stack.shape == (3, 6, 12, 10)
    assert stack.voxel_size_um == pytest.approx((0.3, 0.04, 0.05))
    np.testing.assert_array_equal(stack.channel(1), ZCYX[:, 1])
    assert stack.metadata.numerical_aperture is None
    assert stack.problems() == [
        "missing numerical_aperture",
        "missing immersion_ri",
        "missing emission_nm for channel 0",
        "missing emission_nm for channel 1",
        "missing emission_nm for channel 2",
    ]


def test_imagej_tiff_without_unit_is_uncalibrated(tmp_path: Path) -> None:
    path = tmp_path / "pixels.tif"
    tifffile.imwrite(path, ZCYX[:, 0], imagej=True, metadata={"axes": "ZYX"})
    stack = read_stack(path)
    assert stack.shape == (1, 6, 12, 10)
    assert stack.metadata.voxel_x_um is None
    assert any("no length unit" in w for w in stack.metadata.warnings)
    with pytest.raises(Exception, match="voxel size unknown along z, y, x"):
        _ = stack.voxel_size_um


@pytest.mark.req("IMP-06")
def test_plain_multipage_tiff_is_read_as_z_stack_with_a_note(tmp_path: Path) -> None:
    path = tmp_path / "plain.tiff"
    tifffile.imwrite(path, ZCYX[:, 0], metadata=None)
    stack = read_stack(path)
    assert stack.shape == (1, 6, 12, 10)
    np.testing.assert_array_equal(stack.channel(0), ZCYX[:, 0])
    assert any("pages are read as z-planes" in w for w in stack.metadata.warnings)
    assert "missing voxel_z_um" in stack.problems()


def test_single_plane_tiff_is_not_a_stack(tmp_path: Path) -> None:
    path = tmp_path / "plane.tif"
    tifffile.imwrite(path, ZCYX[0, 0], metadata=None)
    stack = read_stack(path)
    assert stack.shape == (1, 1, 12, 10)
    assert "not a 3D stack (single z-plane)" in stack.problems()


@pytest.mark.req("IMP-07")
def test_tiff_timepoint_selection(tmp_path: Path) -> None:
    data = RNG.integers(0, 255, size=(3, 4, 2, 8, 9), dtype=np.uint8)  # TZCYX
    path = tmp_path / "time.tif"
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": "TZCYX"})
    assert list_series(path)[0].sizes == {"T": 3, "Z": 4, "C": 2, "Y": 8, "X": 9}
    stack = read_stack(path, timepoint=2)
    np.testing.assert_array_equal(stack.asarray(), np.moveaxis(data[2], 1, 0))
    with pytest.raises(UnsupportedFileError, match="timepoint 3 is out of range"):
        read_stack(path, timepoint=3)
    with pytest.raises(UnsupportedFileError, match="tile 1 is out of range"):
        read_stack(path, tile=1)
    with pytest.raises(UnsupportedFileError, match="series 2 is out of range"):
        read_stack(path, series=2)


def test_rgb_tiff_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, RNG.integers(0, 255, size=(8, 9, 3), dtype=np.uint8), photometric="rgb")
    with pytest.raises(UnsupportedFileError, match="RGB TIFF"):
        list_series(path)
