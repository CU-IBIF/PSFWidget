"""Which metrics are tracked, with which Westgard rules and baseline length (TRK-04, TRK-05).

Set per instrument in the profile's ``[tracking]`` table::

    [tracking]
    baseline_sessions = 20                 # default baseline: first N valid sessions
    metrics = ["fwhm_x_um", "fwhm_z_um"]   # default: DEFAULT_METRICS
    rules = ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"]

    [tracking.metric_rules]                # per-metric rule sets
    integrated_intensity = ["1_3s"]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from psfwidget.metrics.field import CHANNEL_METRICS
from psfwidget.metrics.summary import SUMMARY_METRICS
from psfwidget.tracking.westgard import ALL_RULES, Rule, parse_rules

ACCEPTED_BEADS = "accepted_beads"
TRACKABLE_METRICS: tuple[str, ...] = (*SUMMARY_METRICS, ACCEPTED_BEADS, *CHANNEL_METRICS)

DEFAULT_METRICS: tuple[str, ...] = (
    "fwhm_x_um",
    "fwhm_y_um",
    "fwhm_z_um",
    "xy_major_um",
    "xy_minor_um",
    "xz_major_um",
    "yz_major_um",
    "ratio_x",
    "ratio_y",
    "ratio_z",
    "ellipticity_xy",
    "integrated_intensity",
    "sbr",
    ACCEPTED_BEADS,
)
"""The metrics listed for tracking in docs/PLAN.md §3.5."""

DEFAULT_BASELINE_SESSIONS = 20
MIN_BASELINE_SESSIONS = 5
"""Fewer valid sessions than this give no control limits at all."""

_KEYS = frozenset({"baseline_sessions", "metrics", "rules", "metric_rules"})


@dataclass(frozen=True)
class TrackingSettings:
    """How results of one instrument are tracked."""

    baseline_sessions: int = DEFAULT_BASELINE_SESSIONS
    metrics: tuple[str, ...] = DEFAULT_METRICS
    rules: tuple[Rule, ...] = ALL_RULES
    metric_rules: Mapping[str, tuple[Rule, ...]] = field(default_factory=dict)

    def rules_for(self, metric: str) -> tuple[Rule, ...]:
        """Return the Westgard rules applied to ``metric``."""
        return self.metric_rules.get(metric, self.rules)


def _metrics(values: Any, where: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{where} must be a non-empty list of metric names")
    unknown = sorted(set(map(str, values)) - set(TRACKABLE_METRICS))
    if unknown:
        raise ValueError(f"{where}: unknown metric(s) {unknown}")
    return tuple(dict.fromkeys(map(str, values)))


def _rules(values: Any, where: str) -> tuple[Rule, ...]:
    if not isinstance(values, list):
        raise ValueError(f"{where} must be a list of rule names")
    try:
        return parse_rules(values)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None


def parse_tracking(table: Mapping[str, Any], where: str) -> TrackingSettings:
    """Build tracking settings from a profile's ``[tracking]`` table.

    Raises
    ------
    ValueError
        If a key, metric, or rule is unknown or a value is invalid.
    """
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ValueError(
            f"{where}: [tracking]: unknown key(s) {unknown}; allowed: {sorted(_KEYS)}"
        )
    values: dict[str, Any] = {}
    if "baseline_sessions" in table:
        count = table["baseline_sessions"]
        if isinstance(count, bool) or not isinstance(count, int) or count < MIN_BASELINE_SESSIONS:
            raise ValueError(
                f"{where}: [tracking]: baseline_sessions must be a whole number >= "
                f"{MIN_BASELINE_SESSIONS}"
            )
        values["baseline_sessions"] = count
    if "metrics" in table:
        values["metrics"] = _metrics(table["metrics"], f"{where}: [tracking]: metrics")
    if "rules" in table:
        values["rules"] = _rules(table["rules"], f"{where}: [tracking]: rules")
    if "metric_rules" in table:
        per_metric = table["metric_rules"]
        if not isinstance(per_metric, dict):
            raise ValueError(f"{where}: [tracking.metric_rules] must be a table")
        _metrics(list(per_metric), f"{where}: [tracking.metric_rules]")
        values["metric_rules"] = {
            str(metric): _rules(rules, f"{where}: [tracking.metric_rules] {metric}")
            for metric, rules in per_metric.items()
        }
    return TrackingSettings(**values)
