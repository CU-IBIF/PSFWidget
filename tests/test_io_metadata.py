"""Tests for metadata resolution, overrides, and validation (IMP-05, IMP-06)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from psfwidget.io import (
    AcquisitionMetadata,
    ChannelMetadata,
    ChannelOverrides,
    InvalidMetadataError,
    Modality,
    Overrides,
    Sourced,
    parse_assignments,
)
from psfwidget.io.metadata import (
    apply_overrides,
    coerce_value,
    plausibility_warnings,
    positive,
    resolve,
)
from psfwidget.io.model import missing_required


def _file_metadata() -> AcquisitionMetadata:
    return AcquisitionMetadata(
        voxel_z_um=Sourced(0.2),
        voxel_y_um=Sourced(0.05),
        voxel_x_um=Sourced(0.05),
        numerical_aperture=Sourced(1.4),
        channels=(
            ChannelMetadata(0, name=Sourced("green"), excitation_nm=Sourced(488.0)),
            ChannelMetadata(1, name=Sourced("red"), emission_nm=Sourced(600.0)),
        ),
    )


@pytest.mark.req("IMP-05")
def test_precedence_is_file_then_profile_then_user() -> None:
    profile = Overrides(
        numerical_aperture=1.3,
        immersion_ri=1.515,
        channels={0: ChannelOverrides(emission_nm=515.0)},
    )
    user = Overrides(numerical_aperture=1.45, channels={1: ChannelOverrides(emission_nm=610.0)})
    meta = resolve(_file_metadata(), profile, user, profile_note="profile x")

    assert meta.numerical_aperture == Sourced(1.45, "user")
    assert meta.immersion_ri == Sourced(1.515, "profile", "profile x")
    assert meta.voxel_x_um == Sourced(0.05, "file")
    assert meta.channels[0].emission_nm == Sourced(515.0, "profile", "profile x")
    assert meta.channels[0].excitation_nm == Sourced(488.0, "file")
    assert meta.channels[1].emission_nm == Sourced(610.0, "user")


@pytest.mark.req("IMP-05")
def test_none_overrides_leave_values_untouched() -> None:
    meta = _file_metadata()
    assert apply_overrides(meta, Overrides(), "user") == meta


@pytest.mark.req("IMP-06")
def test_missing_required_lists_each_gap() -> None:
    meta = _file_metadata()
    assert missing_required(meta) == [
        "missing immersion_ri",
        "missing emission_nm for channel 0 (green)",
    ]
    complete = resolve(
        meta, None, Overrides(immersion_ri=1.518, channels={0: ChannelOverrides(emission_nm=515)})
    )
    assert missing_required(complete) == []
    unnamed = AcquisitionMetadata(channels=(ChannelMetadata(0),))
    assert "missing emission_nm for channel 0" in missing_required(unnamed)


def test_override_for_unknown_channel_is_rejected() -> None:
    with pytest.raises(InvalidMetadataError, match="channel 5 does not exist"):
        apply_overrides(
            _file_metadata(), Overrides(channels={5: ChannelOverrides(emission_nm=500)}), "user"
        )


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("numerical_aperture", "1.4", 1.4),
        ("voxel_z_um", 0.2, 0.2),
        ("significant_bits", "12", 12),
        ("significant_bits", 12.0, 12),
        ("objective_name", "  Plan Apo 60x  ", "Plan Apo 60x"),
        ("modality", "Spinning-Disk", Modality.SPINNING_DISK),
        ("modality", Modality.CONFOCAL, Modality.CONFOCAL),
        ("detection_band_nm", "500-550", (500.0, 550.0)),
        ("detection_band_nm", [500, 550], (500.0, 550.0)),
    ],
)
def test_coerce_value_accepts_valid_input(name: str, value: object, expected: object) -> None:
    assert coerce_value(name, value) == expected


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("numerical_aperture", "-1", "positive number"),
        ("numerical_aperture", "abc", "positive number"),
        ("voxel_x_um", 0, "positive number"),
        ("emission_nm", float("nan"), "positive number"),
        ("emission_nm", True, "positive number"),
        ("significant_bits", 12.5, "whole number"),
        ("significant_bits", 64, "between 1 and 32"),
        ("significant_bits", "many", "invalid value"),
        ("objective_name", "   ", "must not be empty"),
        ("modality", "lightsheet", "must be one of"),
        ("acquired_utc", "2026-11-11T18:29:37", "time zone"),
        ("acquired_utc", "yesterday", "invalid value"),
        ("detection_band_nm", "550-500", "increasing"),
        ("detection_band_nm", "500", "two wavelengths"),
        ("detection_band_nm", 5, "invalid value"),
        ("no_such_field", 1, "unknown metadata field"),
    ],
)
def test_coerce_value_rejects_invalid_input(name: str, value: object, message: str) -> None:
    with pytest.raises(InvalidMetadataError, match=message):
        coerce_value(name, value)


def test_acquired_utc_is_converted_to_utc() -> None:
    local = datetime(2026, 11, 11, 12, 29, 37, tzinfo=timezone(timedelta(hours=-6)))
    assert coerce_value("acquired_utc", local) == datetime(2026, 11, 11, 18, 29, 37, tzinfo=UTC)
    assert coerce_value("acquired_utc", "2026-11-11T12:29:37-06:00") == datetime(
        2026, 11, 11, 18, 29, 37, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.5, 1.5),
        ("2", 2.0),
        (0, None),
        (-3, None),
        (None, None),
        ("x", None),
        (float("inf"), None),
    ],
)
def test_positive(value: object, expected: float | None) -> None:
    assert positive(value) == expected


@pytest.mark.req("IMP-05")
def test_parse_assignments() -> None:
    overrides = parse_assignments(
        [
            "numerical_aperture=1.4",
            " immersion_ri = 1.518 ",
            "modality=confocal",
            "channel.0.emission_nm=515",
            "channel.0.name=green",
            "channel.2.detection_band_nm=500-550",
        ]
    )
    assert overrides.numerical_aperture == 1.4
    assert overrides.immersion_ri == 1.518
    assert overrides.modality is Modality.CONFOCAL
    assert overrides.channels[0] == ChannelOverrides(name="green", emission_nm=515.0)
    assert overrides.channels[2].detection_band_nm == (500.0, 550.0)


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ("numerical_aperture", "expected key=value"),
        ("=1.4", "expected key=value"),
        ("numerical_aperture=", "expected key=value"),
        ("aperture=1.4", "unknown metadata field"),
        ("channel.0.colour=red", "channel overrides look like"),
        ("channel.x.emission_nm=515", "channel overrides look like"),
        ("channel.0=515", "channel overrides look like"),
        ("channel.0.emission_nm=-5", "positive number"),
    ],
)
def test_parse_assignments_rejects_bad_input(assignment: str, message: str) -> None:
    with pytest.raises(InvalidMetadataError, match=message):
        parse_assignments([assignment])


def test_plausibility_warnings() -> None:
    meta = AcquisitionMetadata(
        numerical_aperture=Sourced(1.9),
        immersion_ri=Sourced(0.9),
        channels=(
            ChannelMetadata(0, excitation_nm=Sourced(561.0), emission_nm=Sourced(520.0)),
            ChannelMetadata(1, emission_nm=Sourced(5000.0)),
        ),
    )
    warnings = plausibility_warnings(meta)
    assert len(warnings) == 5
    assert any("outside the plausible range" in w for w in warnings)
    assert any("outside 1.0-1.8" in w for w in warnings)
    assert any("exceeds the immersion refractive index" in w for w in warnings)
    assert any("channel 0: emission 520 nm is not longer" in w for w in warnings)
    assert any("channel 1: emission wavelength 5000 nm" in w for w in warnings)
    assert plausibility_warnings(_file_metadata()) == []


def test_resolve_adds_each_warning_once() -> None:
    meta = AcquisitionMetadata(numerical_aperture=Sourced(1.4), immersion_ri=Sourced(1.0))
    once = resolve(meta)
    assert len(once.warnings) == 1
    assert resolve(once).warnings == once.warnings
