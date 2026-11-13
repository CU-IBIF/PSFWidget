"""Tests for tracking settings in instrument profiles (TRK-04, TRK-05)."""

from __future__ import annotations

from typing import Any

import pytest

from psfwidget.profiles import ProfileError, parse_profile
from psfwidget.tracking.settings import DEFAULT_METRICS, TrackingSettings, parse_tracking
from psfwidget.tracking.westgard import ALL_RULES, Rule


@pytest.mark.req("TRK-04", "TRK-05")
def test_tracking_table() -> None:
    settings = parse_tracking(
        {
            "baseline_sessions": 12,
            "metrics": ["fwhm_z_um", "ratio_z", "fwhm_z_um"],
            "rules": ["1_2s", "1_3s"],
            "metric_rules": {"integrated_intensity": ["1_3s"]},
        },
        "p",
    )
    assert settings.baseline_sessions == 12
    assert settings.metrics == ("fwhm_z_um", "ratio_z")
    assert settings.rules_for("fwhm_z_um") == (Rule.R1_2S, Rule.R1_3S)
    assert settings.rules_for("integrated_intensity") == (Rule.R1_3S,)


def test_defaults() -> None:
    settings = TrackingSettings()
    assert settings.baseline_sessions == 20
    assert settings.metrics == DEFAULT_METRICS
    assert "accepted_beads" in settings.metrics
    assert settings.rules_for("anything") == ALL_RULES
    profile = parse_profile({"instrument": {"id": "s"}})
    assert profile.tracking == TrackingSettings()
    profile = parse_profile({"instrument": {"id": "s"}, "tracking": {"baseline_sessions": 8}})
    assert profile.tracking.baseline_sessions == 8


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"sessions": 3}, "unknown key"),
        ({"baseline_sessions": 3}, "whole number >= 5"),
        ({"baseline_sessions": True}, "whole number >= 5"),
        ({"metrics": []}, "non-empty list"),
        ({"metrics": "fwhm_z_um"}, "non-empty list"),
        ({"metrics": ["fwhm_q"]}, r"unknown metric\(s\) \['fwhm_q'\]"),
        ({"rules": "1_3s"}, "list of rule names"),
        ({"rules": ["1_4s"]}, "unknown Westgard rule"),
        ({"metric_rules": ["1_3s"]}, "must be a table"),
        ({"metric_rules": {"fwhm_q": ["1_3s"]}}, "unknown metric"),
        ({"metric_rules": {"fwhm_z_um": ["9_9s"]}}, "unknown Westgard rule"),
    ],
)
def test_invalid_tracking_tables(table: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_tracking(table, "p")
    with pytest.raises(ProfileError, match=message):
        parse_profile({"instrument": {"id": "s"}, "tracking": table})


def test_tracking_must_be_a_table() -> None:
    with pytest.raises(ProfileError, match=r"\[tracking\] must be a table"):
        parse_profile({"instrument": {"id": "s"}, "tracking": 5})
