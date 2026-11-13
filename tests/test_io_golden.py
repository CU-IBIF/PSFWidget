"""Reader tests against real files (GOLD-IO, docs/VV_PLAN.md §3).

Expected values come from ``tests/data/manifest.json``. They were taken from
the acquisition settings and from the raw reader libraries, not from
PSFWidget. Tests for files that are not in the local cache are skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import fetch_test_data
from psfwidget.io import Sourced, list_series, read_stack
from psfwidget.profiles import load_profile

MANIFEST = fetch_test_data.load_manifest()
NAMES = sorted(MANIFEST)
PROFILES = Path(__file__).resolve().parents[1] / "examples" / "profiles"

GoldenFile = Callable[[str], Path]


def _value(item: Sourced[Any] | None) -> Any:
    return None if item is None else item.value


def _close(actual: Any, expected: Any) -> bool:
    if expected is None or actual is None:
        return actual is expected
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _close(a, e) for a, e in zip(actual, expected, strict=True)
        )
    if isinstance(expected, str):
        return bool(actual == expected)
    return bool(actual == pytest.approx(expected, rel=1e-6))


@pytest.mark.req("IMP-01", "IMP-02")
@pytest.mark.parametrize("name", NAMES)
def test_series_listing(name: str, golden_file: GoldenFile) -> None:
    expected = MANIFEST[name].expected
    series = list_series(golden_file(name))
    assert len(series) == expected["n_series"]
    assert [s.index for s in series] == list(range(len(series)))
    assert series[0].sizes == expected["series_sizes"]
    assert series[0].dtype == expected["dtype"]
    if "series_names" in expected:
        assert [s.name for s in series] == expected["series_names"]


@pytest.mark.req("IMP-01", "IMP-03", "IMP-04")
@pytest.mark.parametrize("name", NAMES)
def test_file_metadata(name: str, golden_file: GoldenFile) -> None:
    expected = MANIFEST[name].expected
    stack = read_stack(golden_file(name), compute_hash=False)
    meta = stack.metadata

    assert list(stack.shape) == expected["shape"]
    assert str(stack.dtype) == expected["dtype"]
    voxel = expected["voxel_size_um"]
    for axis, item in (("z", meta.voxel_z_um), ("y", meta.voxel_y_um), ("x", meta.voxel_x_um)):
        assert _close(_value(item), voxel[axis]), f"voxel {axis}: {_value(item)}"
    for field in (
        "numerical_aperture",
        "immersion_ri",
        "objective_name",
        "resolution_enhancement",
        "significant_bits",
    ):
        assert _close(_value(getattr(meta, field)), expected[field]), field
    modality = _value(meta.modality)
    assert (None if modality is None else modality.value) == expected["modality"]

    acquired = _value(meta.acquired_utc)
    if expected["acquired_utc"] is None:
        assert acquired is None
    else:
        wanted = datetime.fromisoformat(expected["acquired_utc"])
        assert acquired.utcoffset().total_seconds() == 0
        assert abs((acquired - wanted).total_seconds()) < 1.0

    assert len(meta.channels) == len(expected["channels"])
    for channel, wanted_channel in zip(meta.channels, expected["channels"], strict=True):
        for field, wanted_value in wanted_channel.items():
            actual = _value(getattr(channel, field))
            assert _close(actual, wanted_value), f"channel {channel.index} {field}: {actual}"

    assert all(
        item is None or item.source == "file"
        for item in (meta.voxel_x_um, meta.numerical_aperture, meta.modality)
    )
    assert stack.problems() == expected["problems"]
    for fragment in expected.get("warnings_contain", []):
        assert any(fragment in warning for warning in meta.warnings), fragment


@pytest.mark.req("IMP-01", "IMP-03", "IMP-08")
@pytest.mark.parametrize("name", NAMES)
def test_pixel_data(name: str, golden_file: GoldenFile) -> None:
    expected = MANIFEST[name].expected
    stack = read_stack(golden_file(name), compute_hash=False)
    maxima = []
    for index in range(stack.n_channels):
        channel = stack.channel(index)
        assert channel.shape == stack.shape[1:]
        assert channel.dtype == stack.dtype
        assert channel.flags.c_contiguous
        maxima.append(int(channel.max()))
    assert maxima == expected["channel_max"]
    assert stack.asarray().shape == stack.shape


@pytest.mark.req("IMP-01")
@pytest.mark.parametrize("name", [n for n in NAMES if "same_pixels_as" in MANIFEST[n].expected])
def test_exports_have_identical_pixels(name: str, golden_file: GoldenFile) -> None:
    reference = MANIFEST[name].expected["same_pixels_as"]
    exported = read_stack(golden_file(name), compute_hash=False).asarray()
    original = read_stack(golden_file(reference), compute_hash=False).asarray()
    np.testing.assert_array_equal(exported, original)


@pytest.mark.req("IMP-09")
@pytest.mark.parametrize("name", NAMES)
def test_provenance_records_hash_and_selection(name: str, golden_file: GoldenFile) -> None:
    path = golden_file(name)
    source = read_stack(path).provenance
    assert source.sha256 == MANIFEST[name].sha256
    assert source.size_bytes == MANIFEST[name].size_bytes
    assert source.path == path
    assert (source.series_index, source.timepoint, source.tile) == (0, 0, 0)
    assert source.reader.split()[0] in {"nd2", "liffile", "czifile", "tifffile"}


@pytest.mark.req("IMP-05", "IMP-06", "IMP-10")
@pytest.mark.parametrize("name", [n for n in NAMES if "profile" in MANIFEST[n].expected])
def test_facility_profile_completes_metadata(name: str, golden_file: GoldenFile) -> None:
    expected = MANIFEST[name].expected
    profile = load_profile(PROFILES / expected["profile"])
    stack = read_stack(golden_file(name), profile=profile, compute_hash=False)
    meta = stack.metadata

    stack.require_analyzable()
    assert not meta.warnings
    assert _value(meta.bead_diameter_nm) == expected["sample"]["bead_diameter_nm"]
    assert meta.instrument_id is not None
    assert meta.instrument_id.source == "profile"
    assert meta.objective_id is not None
    peaks = expected["sample"]["bead_emission_peak_nm"]
    for channel, peak in zip(meta.channels, peaks, strict=True):
        assert channel.emission_nm is not None
        assert channel.emission_nm.value == peak
        assert channel.emission_nm.source == "profile"
        assert channel.preset_id is not None
    # Values the profile does not set keep their file origin.
    assert meta.voxel_x_um is not None
    assert meta.voxel_x_um.source == "file"


@pytest.mark.req("IMP-07")
def test_nd2_timepoint_selection(golden_file: GoldenFile) -> None:
    import nd2

    path = golden_file("header_test2.nd2")
    with nd2.ND2File(path) as handle:
        reference = handle.asarray()  # (T, Z, Y, X)
    for timepoint in (0, 3):
        stack = read_stack(path, timepoint=timepoint, compute_hash=False)
        np.testing.assert_array_equal(stack.channel(0), reference[timepoint])
    assert not np.array_equal(reference[0], reference[3])


@pytest.mark.req("IMP-07")
def test_lif_timepoint_and_tile_selection(golden_file: GoldenFile) -> None:
    import liffile

    path = golden_file("PR2729_frameOrderCombinedScanTypes.lif")
    with liffile.LifFile(path) as handle:
        reference = handle.images[0].asarray()  # (T, M, Z, C, Y, X)
    for timepoint, tile in ((0, 0), (1, 3), (0, 2)):
        stack = read_stack(path, timepoint=timepoint, tile=tile, compute_hash=False)
        wanted = np.moveaxis(reference[timepoint, tile], 1, 0)  # (C, Z, Y, X)
        np.testing.assert_array_equal(stack.asarray(), wanted)
        assert (stack.provenance.timepoint, stack.provenance.tile) == (timepoint, tile)


@pytest.mark.req("IMP-02")
def test_lif_series_selection(golden_file: GoldenFile) -> None:
    import liffile

    path = golden_file("20261106_QC_SP8.lif")
    with liffile.LifFile(path) as handle:
        reference = handle.images[4].asarray()  # (Z, C, Y, X)
    stack = read_stack(path, series=4, compute_hash=False)
    assert stack.provenance.series_name == "1AU_field5_avg_uni"
    np.testing.assert_array_equal(stack.asarray(), np.moveaxis(reference, 1, 0))


@pytest.mark.req("IMP-06")
def test_two_dimensional_file_is_refused_for_analysis(golden_file: GoldenFile) -> None:
    from psfwidget.io import StackNotAnalyzableError

    stack = read_stack(golden_file("BF007.nd2"), compute_hash=False)
    with pytest.raises(StackNotAnalyzableError, match="not a 3D stack") as caught:
        stack.require_analyzable()
    assert "missing voxel_z_um" in caught.value.problems
