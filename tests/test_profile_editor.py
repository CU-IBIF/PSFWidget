"""Tests for the profile writer and the profile editor (UI-03)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from psfwidget.profiles import ProfileError, load_profile
from psfwidget.profiles.write import dumps, save, value

EXAMPLES = sorted((Path(__file__).parents[1] / "examples" / "profiles").glob("*.toml"))


@pytest.mark.req("UI-03")
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_writer_round_trips_the_example_profiles(path: Path) -> None:
    data = tomllib.loads(path.read_text("utf-8"))
    assert tomllib.loads(dumps(data)) == data


@pytest.mark.req("UI-03")
def test_writer_values_and_validation(tmp_path: Path) -> None:
    assert value('a "b"\\') == '"a \\"b\\"\\\\"'
    assert value([1, 2.5, True]) == "[1, 2.5, true]"
    assert value({"excitation_nm": 488}) == "{ excitation_nm = 488 }"
    with pytest.raises(ValueError, match="cannot write"):
        value(float("nan"))
    with pytest.raises(TypeError):
        value(object())
    nested = {"instrument": {"id": "x"}, "tracking": {"metric_rules": {"fwhm_z_um": ["1_3s"]}}}
    assert tomllib.loads(dumps(nested)) == nested
    with pytest.raises(ProfileError):
        save({"instrument": {"id": "x", "colour": "red"}}, tmp_path / "bad.toml")
    assert not (tmp_path / "bad.toml").exists()
    assert (
        load_profile(save({"instrument": {"id": "x"}}, tmp_path / "ok.toml")).instrument_id == "x"
    )
