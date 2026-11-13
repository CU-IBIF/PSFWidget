"""Tests for specification limits (THE-06)."""

from __future__ import annotations

from typing import Any

import pytest

from psfwidget.metrics.summary import Stats
from psfwidget.profiles import ProfileError, parse_profile
from psfwidget.profiles.spec import Check, SpecLimits, SpecResult, Status, evaluate, parse_spec


def _stats(median: float) -> Stats:
    return Stats(n=10, mean=median, sd=0.0, median=median, q25=median, q75=median)


SUMMARY = {
    "ratio_x": _stats(1.1),
    "ratio_y": _stats(1.25),
    "ratio_z": _stats(1.4),
    "ellipticity_xy": _stats(0.9),
}


@pytest.mark.req("THE-06")
def test_limits_from_profile() -> None:
    profile = parse_profile(
        {
            "instrument": {"id": "s"},
            "spec": {
                "lateral_ratio_max": 1.3,
                "axial_ratio_max": 1.5,
                "ellipticity_min": 0.8,
                "min_beads": 5,
            },
        }
    )
    assert profile.spec == SpecLimits(1.3, 1.5, 0.8, 5)
    assert not profile.spec.empty
    assert parse_profile({"instrument": {"id": "s"}}).spec.empty


@pytest.mark.req("THE-06")
def test_evaluation_passes_fails_and_reports_each_check() -> None:
    result = evaluate(SpecLimits(1.3, 1.5, 0.8, 5), SUMMARY, accepted=10)
    assert result.status is Status.PASS
    assert [(c.name, c.limit, c.value) for c in result.checks] == [
        ("accepted beads", ">= 5", 10.0),
        ("median ratio_x", "<= 1.3", 1.1),
        ("median ratio_y", "<= 1.3", 1.25),
        ("median ratio_z", "<= 1.5", 1.4),
        ("median ellipticity_xy", ">= 0.8", 0.9),
    ]
    failed = evaluate(SpecLimits(lateral_ratio_max=1.2), SUMMARY, accepted=10)
    assert failed.status is Status.FAIL
    assert [c.status for c in failed.checks] == [Status.PASS, Status.FAIL]


def test_missing_values_are_not_evaluated() -> None:
    result = evaluate(SpecLimits(axial_ratio_max=1.5, ellipticity_min=0.8), {}, accepted=3)
    assert result.status is Status.NOT_EVALUATED
    assert all(c.status is Status.NOT_EVALUATED and c.value is None for c in result.checks)
    assert SpecResult(()).status is Status.NOT_EVALUATED
    mixed = SpecResult(
        (Check("a", "", 1.0, Status.PASS), Check("b", "", None, Status.NOT_EVALUATED))
    )
    assert mixed.status is Status.NOT_EVALUATED


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"lateral_ratio": 1.3}, "unknown key"),
        ({"lateral_ratio_max": 0}, "positive number"),
        ({"axial_ratio_max": "1.5"}, "positive number"),
        ({"axial_ratio_max": True}, "positive number"),
        ({"ellipticity_min": 1.2}, "must not exceed 1"),
        ({"min_beads": 0}, "whole number"),
        ({"min_beads": 2.5}, "whole number"),
    ],
)
def test_invalid_limits_are_rejected(table: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_spec(table, "test")
    with pytest.raises(ProfileError, match=message):
        parse_profile({"instrument": {"id": "s"}, "spec": table})


def test_spec_must_be_a_table() -> None:
    with pytest.raises(ProfileError, match=r"\[spec\] must be a table"):
        parse_profile({"instrument": {"id": "s"}, "spec": 1.3})
