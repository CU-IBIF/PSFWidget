"""Tests for the reader front-end, shared helpers, and ImageStack (IMP-01, IMP-06, IMP-08)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget.io import (
    SUPPORTED_EXTENSIONS,
    StackNotAnalyzableError,
    UnsupportedFileError,
    list_series,
    read_stack,
)
from psfwidget.io._common import (
    band_centre,
    check_selection,
    julian_day_to_utc,
    pick_excitation,
    sha256_of,
    step_from_coordinates,
    to_zyx,
)


@pytest.mark.req("IMP-01")
def test_supported_extensions() -> None:
    assert set(SUPPORTED_EXTENSIONS) == {".nd2", ".lif", ".czi", ".tif", ".tiff"}


@pytest.mark.req("NFR-06")
def test_missing_and_unsupported_files_give_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no such file"):
        read_stack(tmp_path / "absent.nd2")
    with pytest.raises(FileNotFoundError, match="no such file"):
        list_series(tmp_path)  # a directory
    other = tmp_path / "image.png"
    other.write_bytes(b"x")
    with pytest.raises(UnsupportedFileError, match=r"unsupported file type '.png'"):
        list_series(other)


@pytest.mark.req("NFR-06")
@pytest.mark.parametrize("suffix", [".nd2", ".lif", ".czi", ".tif"])
def test_corrupt_files_are_reported_not_crashed_on(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"corrupt{suffix}"
    path.write_bytes(b"this is not a microscope file" * 20)
    with pytest.raises(UnsupportedFileError, match="could not read corrupt"):
        list_series(path)
    with pytest.raises(UnsupportedFileError, match="could not read corrupt"):
        read_stack(path)


def test_extension_matching_ignores_case(tmp_path: Path) -> None:
    path = tmp_path / "UPPER.TIF"
    tifffile.imwrite(
        path, np.zeros((3, 4, 5), dtype=np.uint8), photometric="minisblack", metadata=None
    )
    assert read_stack(path).shape == (1, 3, 4, 5)


@pytest.mark.req("IMP-08")
def test_pixel_data_is_read_only_on_request(tmp_path: Path) -> None:
    path = tmp_path / "stack.tif"
    tifffile.imwrite(
        path,
        np.arange(120, dtype=np.uint8).reshape(2, 3, 4, 5),
        imagej=True,
        metadata={"axes": "ZCYX"},
    )
    stack = read_stack(path, compute_hash=False)
    assert stack.provenance.sha256 is None
    path.unlink()  # metadata is already read; nothing has touched the pixels
    assert stack.shape == (3, 2, 4, 5)
    with pytest.raises(FileNotFoundError):
        stack.channel(0)


def test_channel_index_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "stack.tif"
    tifffile.imwrite(path, np.zeros((2, 4, 5), dtype=np.uint8), metadata=None)
    stack = read_stack(path)
    assert stack.n_channels == 1
    with pytest.raises(IndexError, match="channel 1 out of range"):
        stack.channel(1)
    with pytest.raises(IndexError, match="channel -1 out of range"):
        stack.channel(-1)


@pytest.mark.req("IMP-05", "IMP-06")
def test_overrides_make_a_bare_tiff_analyzable(tmp_path: Path) -> None:
    from psfwidget.io import parse_assignments

    path = tmp_path / "stack.tif"
    tifffile.imwrite(
        path, np.zeros((4, 6, 5), dtype=np.uint16), photometric="minisblack", metadata=None
    )
    bare = read_stack(path)
    with pytest.raises(StackNotAnalyzableError) as caught:
        bare.require_analyzable()
    assert len(caught.value.problems) == 6

    overrides = parse_assignments(
        [
            "voxel_z_um=0.2",
            "voxel_y_um=0.05",
            "voxel_x_um=0.05",
            "numerical_aperture=1.4",
            "immersion_ri=1.518",
            "channel.0.emission_nm=515",
        ]
    )
    stack = read_stack(path, overrides=overrides)
    stack.require_analyzable()
    assert stack.voxel_size_um == (0.2, 0.05, 0.05)
    assert stack.metadata.numerical_aperture is not None
    assert stack.metadata.numerical_aperture.source == "user"
    assert bare.with_metadata(stack.metadata).problems() == []


def test_sha256_of(tmp_path: Path) -> None:
    path = tmp_path / "abc"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_julian_day_conversion() -> None:
    assert julian_day_to_utc(2440587.5) == datetime(1970, 1, 1, tzinfo=UTC)
    moment = julian_day_to_utc(2461355.4079541666)
    assert moment.replace(microsecond=0) == datetime(2026, 11, 10, 21, 47, 27, tzinfo=UTC)


def test_band_centre_and_axis_step() -> None:
    assert band_centre((500.0, 550.0)) == 525.0
    assert step_from_coordinates([0.0, 0.5, 1.0]) == 0.5
    assert step_from_coordinates([4.0, 3.0, 2.0]) == -1.0
    assert step_from_coordinates([1.0]) is None
    assert step_from_coordinates([]) is None


@pytest.mark.parametrize(
    ("lasers", "band", "hint", "expected"),
    [
        ([], None, None, None),
        ([488.0], None, None, 488.0),
        ([488.0, 488.0], None, None, 488.0),
        ([488.0, 639.0], (496.0, 570.0), None, 488.0),
        ([488.0, 639.0], (643.0, 735.0), None, 639.0),
        ([405.0, 488.0, 561.0], (570.0, 640.0), None, 561.0),
        ([561.0, 639.0], (500.0, 550.0), 493.0, 561.0),  # nothing below the band: use the hint
        ([488.0, 639.0], None, 653.0, 639.0),
        ([488.0, 639.0], None, None, None),
        ([561.0, 639.0], (500.0, 550.0), None, None),
    ],
)
def test_pick_excitation(
    lasers: list[float],
    band: tuple[float, float] | None,
    hint: float | None,
    expected: float | None,
) -> None:
    assert pick_excitation(lasers, band, hint) == expected


def test_to_zyx_reorders_and_drops_singleton_axes() -> None:
    data = np.arange(24).reshape(1, 4, 3, 2)  # C, X, Y, Z
    out = to_zyx(data, ["C", "X", "Y", "Z"])
    assert out.shape == (2, 3, 4)
    assert out[1, 2, 3] == data[0, 3, 2, 1]
    assert to_zyx(np.zeros((5, 6)), ["Y", "X"]).shape == (1, 5, 6)
    with pytest.raises(UnsupportedFileError, match="cannot reduce axes"):
        to_zyx(np.zeros((2, 5, 6)), ["C", "Y", "X"])
    with pytest.raises(UnsupportedFileError, match="cannot reduce axes"):
        to_zyx(np.zeros((2, 5)), ["Z", "Y"])
    with pytest.raises(UnsupportedFileError, match="does not match axes"):
        to_zyx(np.zeros((2, 5)), ["Z", "Y", "X"])


def test_check_selection() -> None:
    sizes = {"T": 2, "M": 3, "C": 2, "Z": 5, "Y": 8, "X": 8, "H": 1}
    check_selection(sizes, timepoint=1, tile=2)
    with pytest.raises(UnsupportedFileError, match="timepoint 2 is out of range"):
        check_selection(sizes, timepoint=2, tile=0)
    with pytest.raises(UnsupportedFileError, match="tile -1 is out of range"):
        check_selection(sizes, timepoint=0, tile=-1)
    with pytest.raises(UnsupportedFileError, match=r"unsupported image axes \['H'\]"):
        check_selection(sizes | {"H": 4}, timepoint=0, tile=0)
    check_selection(sizes | {"H": 4}, timepoint=0, tile=0, extra_allowed="H")
