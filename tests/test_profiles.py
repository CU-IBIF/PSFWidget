"""Tests for instrument profiles (IMP-10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

from psfwidget.io import (
    AcquisitionMetadata,
    ChannelMetadata,
    Modality,
    Sourced,
    read_stack,
)
from psfwidget.profiles import (
    InstrumentProfile,
    ProfileError,
    load_profile,
    parse_profile,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "profiles"

PROFILE: dict[str, Any] = {
    "instrument": {
        "id": "scope",
        "name": "Test scope",
        "modality": "spinning_disk",
        "resolution_enhancement": "Live-SR",
        "pinhole_um": 50,
    },
    "bead": {"diameter_nm": 100, "product": "TetraSpeck"},
    "objective": [
        {"id": "60x", "match": "60x Oil", "numerical_aperture": 1.4, "immersion_ri": 1.515},
        {"id": "100x", "match": "100X/1.45", "numerical_aperture": 1.45},
    ],
    "channel": [
        {"id": "green", "match": {"excitation_nm": 488}, "emission_nm": 515},
        {"id": "far-red", "match": {"name": "cy5_sr"}, "emission_nm": 680, "pinhole_um": 70},
        {
            "id": "third",
            "match": {"index": 2, "excitation_nm": 561},
            "emission_nm": 580,
            "name": "orange",
            "excitation_nm": 560,
            "pinhole_airy": 1.2,
        },
    ],
}


def _metadata(objective: str | None = "Plan Apo λ 60x  Oil") -> AcquisitionMetadata:
    return AcquisitionMetadata(
        objective_name=None if objective is None else Sourced(objective),
        channels=(
            ChannelMetadata(0, name=Sourced("GFP_SR"), excitation_nm=Sourced(491.0)),
            ChannelMetadata(1, name=Sourced("Cy5_SR"), excitation_nm=Sourced(640.0)),
            ChannelMetadata(2, name=Sourced("x"), excitation_nm=Sourced(561.0)),
            ChannelMetadata(3, name=Sourced("unmatched"), excitation_nm=Sourced(561.0)),
        ),
    )


@pytest.mark.req("IMP-10")
def test_profile_sets_instrument_objective_bead_and_channels() -> None:
    profile = parse_profile(PROFILE)
    overrides, warnings = profile.overrides_for(_metadata())

    assert overrides.instrument_id == "scope"
    assert overrides.objective_id == "60x"
    assert overrides.numerical_aperture == 1.4
    assert overrides.immersion_ri == 1.515
    assert overrides.modality is Modality.SPINNING_DISK
    assert overrides.resolution_enhancement == "Live-SR"
    assert overrides.bead_diameter_nm == 100.0

    green, far_red, third, unmatched = (overrides.channels[i] for i in range(4))
    assert (green.preset_id, green.emission_nm, green.pinhole_um) == ("green", 515.0, 50.0)
    assert (far_red.preset_id, far_red.emission_nm, far_red.pinhole_um) == ("far-red", 680.0, 70.0)
    assert (third.preset_id, third.name, third.excitation_nm) == ("third", "orange", 560.0)
    assert third.pinhole_airy == 1.2
    # No preset matches, but the instrument-wide pinhole still applies.
    assert (unmatched.preset_id, unmatched.emission_nm, unmatched.pinhole_um) == (None, None, 50.0)
    assert warnings == ["channel 3 (unmatched) matches no channel in profile scope"]


@pytest.mark.req("IMP-10")
def test_excitation_matching_uses_a_tolerance() -> None:
    profile = parse_profile(PROFILE)
    near = AcquisitionMetadata(channels=(ChannelMetadata(0, excitation_nm=Sourced(493.0)),))
    far = AcquisitionMetadata(channels=(ChannelMetadata(0, excitation_nm=Sourced(494.0)),))
    none = AcquisitionMetadata(channels=(ChannelMetadata(0),))
    assert profile.overrides_for(near)[0].channels[0].preset_id == "green"
    assert profile.overrides_for(far)[0].channels[0].preset_id is None
    assert profile.overrides_for(none)[0].channels[0].preset_id is None
    assert "(unnamed)" in profile.overrides_for(none)[1][-1]


@pytest.mark.req("IMP-10")
def test_objective_selection() -> None:
    profile = parse_profile(PROFILE)
    assert profile.select_objective("CFI SR HP Apo TIRF 100x/1.45 oil")[0].id == "100x"  # type: ignore[union-attr]
    assert profile.select_objective(None, "100x")[0].id == "100x"  # type: ignore[union-attr]

    preset, warning = profile.select_objective("Plan Fluor 10x")
    assert preset is None
    assert warning is not None
    assert "matches no objective" in warning
    preset, warning = profile.select_objective(None)
    assert preset is None
    assert warning is not None
    assert "has several" in warning
    with pytest.raises(ProfileError, match="has no objective 'nope'; known: 60x, 100x"):
        profile.select_objective(None, "nope")

    overrides, warnings = profile.overrides_for(_metadata("Plan Fluor 10x"))
    assert overrides.objective_id is None
    assert overrides.numerical_aperture is None
    assert any("matches no objective" in w for w in warnings)

    with_default = parse_profile(PROFILE | {"objective": [*PROFILE["objective"], {"id": "any"}]})
    assert with_default.select_objective("Plan Fluor 10x")[0].id == "any"  # type: ignore[union-attr]
    assert with_default.select_objective(None)[0].id == "any"  # type: ignore[union-attr]
    single = parse_profile(
        {"instrument": {"id": "s"}, "objective": [{"id": "only", "match": "63x"}]}
    )
    assert single.select_objective(None)[0].id == "only"  # type: ignore[union-attr]
    assert parse_profile({"instrument": {"id": "s"}}).select_objective("x") == (None, None)


def test_minimal_profile() -> None:
    profile = parse_profile({"instrument": {"id": " scope "}})
    assert profile == InstrumentProfile(instrument_id="scope")
    overrides, warnings = profile.overrides_for(_metadata())
    assert overrides.instrument_id == "scope"
    assert overrides.channels == {}
    assert warnings == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"instrument": {}}, "'id' is required"),
        ({"instrument": {"id": ""}}, "'id' is required"),
        ({"instrument": {"id": "s", "colour": "red"}}, r"unknown key\(s\) \['colour'\]"),
        ({"instrument": {"id": "s", "name": 5}}, "name must be a string"),
        ({"instrument": {"id": "s", "modality": "lightsheet"}}, "modality must be one of"),
        ({"instrument": {"id": "s", "pinhole_um": -1}}, "pinhole_um must be a positive number"),
        ({"instrument": "sp8"}, r"\[instrument\] must be a table"),
        ({"extra": {}}, r"unknown key\(s\) \['extra'\]"),
        ({"bead": {"diameter_nm": "big"}}, "bead_diameter_nm must be a positive number"),
        ({"bead": {"product": 7}}, "product must be a string"),
        ({"objective": {"id": "x"}}, r"use \[\[objective\]\] tables"),
        ({"objective": [{"id": "a"}, {"id": "a"}]}, "objective id.*more than once"),
        ({"objective": [{"id": "a", "match": ""}]}, "'match' must be a non-empty string"),
        ({"objective": [{"id": "a", "numerical_aperture": 0}]}, "objective 1: numerical_aperture"),
        ({"channel": [{"id": "c"}]}, "'match' is required"),
        ({"channel": [{"id": "c", "match": {}}]}, "'match' is required"),
        ({"channel": [{"id": "c", "match": {"colour": "red"}}]}, "channel 1: match: unknown key"),
        ({"channel": [{"id": "c", "match": {"index": -1}}]}, "match.index must be a whole number"),
        (
            {"channel": [{"id": "c", "match": {"index": True}}]},
            "match.index must be a whole number",
        ),
        (
            {"channel": [{"id": "c", "match": {"index": 0}, "emission_nm": "x"}]},
            "channel 1: emission_nm",
        ),
        ({"channel": [{"match": {"index": 0}}]}, "channel 1: 'id' is required"),
        (
            {"channel": [{"id": "c", "match": {"index": 0}}, {"id": "c", "match": {"index": 1}}]},
            "channel id.*more than once",
        ),
    ],
)
def test_invalid_profiles_are_rejected(change: dict[str, Any], message: str) -> None:
    data = {"instrument": {"id": "s"}} | change
    with pytest.raises(ProfileError, match=message):
        parse_profile(data)


def test_profile_without_instrument_table_is_rejected() -> None:
    with pytest.raises(ProfileError, match=r"an \[instrument\] table"):
        parse_profile({"bead": {"diameter_nm": 100}})


def test_load_profile_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "absent.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[instrument\nid = 1", encoding="utf-8")
    with pytest.raises(ProfileError, match=r"broken.toml: not valid TOML"):
        load_profile(broken)
    wrong = tmp_path / "wrong.toml"
    wrong.write_text('[instrument]\nid = "s"\nwattage = 5\n', encoding="utf-8")
    with pytest.raises(ProfileError, match=r"wrong.toml: \[instrument\]: unknown key"):
        load_profile(wrong)


@pytest.mark.req("IMP-10")
@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.toml")), ids=lambda p: p.name)
def test_example_profiles_load(path: Path) -> None:
    profile = load_profile(path)
    assert profile.instrument_id
    assert profile.bead_diameter_nm == 100.0
    assert profile.objectives
    assert profile.channels
    assert all(c.emission_nm is not None for c in profile.channels)


@pytest.mark.req("IMP-05", "IMP-06", "IMP-10")
def test_profile_completes_a_metadata_free_tiff(tmp_path: Path) -> None:
    path = tmp_path / "export.tif"
    tifffile.imwrite(
        path,
        np.zeros((5, 3, 8, 8), dtype=np.uint16),
        imagej=True,
        resolution=(1 / 0.0424, 1 / 0.0424),
        metadata={"axes": "ZCYX", "spacing": 0.3, "unit": "um"},
    )
    assert len(read_stack(path).problems()) == 5

    stack = read_stack(path, profile=load_profile(EXAMPLES / "csu-livesr-tiff.toml"))
    meta = stack.metadata
    stack.require_analyzable()
    assert not meta.warnings
    assert meta.modality == Sourced(Modality.SPINNING_DISK, "profile", "profile csu-livesr")
    assert meta.numerical_aperture is not None
    assert (meta.numerical_aperture.value, meta.numerical_aperture.source) == (1.4, "profile")
    assert meta.voxel_z_um is not None
    assert meta.voxel_z_um.source == "file"
    assert [c.emission_nm.value for c in meta.channels if c.emission_nm] == [580.0, 515.0, 680.0]
    assert [c.pinhole_um.value for c in meta.channels if c.pinhole_um] == [50.0, 50.0, 50.0]
    assert [c.preset_id.value for c in meta.channels if c.preset_id] == [
        "orange",
        "green",
        "far-red",
    ]

    with pytest.raises(ProfileError, match="has no objective"):
        read_stack(path, profile=load_profile(EXAMPLES / "sp8.toml"), objective_id="10x")
