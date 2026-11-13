"""Specification limits and pass/fail evaluation (THE-06).

Limits live in an instrument profile's ``[spec]`` table::

    [spec]
    lateral_ratio_max = 1.3     # median FWHM x and y over the theoretical value
    axial_ratio_max = 1.5       # median FWHM z over the theoretical value
    ellipticity_min = 0.8       # median minor/major FWHM in the XY plane
    min_beads = 5               # accepted beads needed for a result

Each limit is checked against the channel's median over accepted beads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from psfwidget.metrics.summary import Stats

_FLOAT_LIMITS = ("lateral_ratio_max", "axial_ratio_max", "ellipticity_min")
SPEC_KEYS = frozenset({*_FLOAT_LIMITS, "min_beads"})


class Status(StrEnum):
    """Outcome of a check or of all checks."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT EVALUATED"


@dataclass(frozen=True)
class SpecLimits:
    """Acceptance limits for one instrument. ``None`` means no limit."""

    lateral_ratio_max: float | None = None
    axial_ratio_max: float | None = None
    ellipticity_min: float | None = None
    min_beads: int | None = None

    @property
    def empty(self) -> bool:
        """Whether no limit is set."""
        return all(getattr(self, name) is None for name in (*_FLOAT_LIMITS, "min_beads"))


@dataclass(frozen=True)
class Check:
    """One limit compared with one measured value."""

    name: str
    limit: str
    value: float | None
    status: Status


@dataclass(frozen=True)
class SpecResult:
    """All checks for one channel and their overall status."""

    checks: tuple[Check, ...]

    @property
    def status(self) -> Status:
        """FAIL if any check fails; PASS if all pass; otherwise NOT EVALUATED."""
        statuses = {c.status for c in self.checks}
        if Status.FAIL in statuses:
            return Status.FAIL
        if statuses == {Status.PASS}:
            return Status.PASS
        return Status.NOT_EVALUATED


def parse_spec(table: Mapping[str, Any], where: str) -> SpecLimits:
    """Build limits from a profile's ``[spec]`` table.

    Raises
    ------
    ValueError
        If a key is unknown or a value is not a positive number.
    """
    unknown = sorted(set(table) - SPEC_KEYS)
    if unknown:
        raise ValueError(
            f"{where}: [spec]: unknown key(s) {unknown}; allowed: {sorted(SPEC_KEYS)}"
        )
    values: dict[str, Any] = {}
    for name in _FLOAT_LIMITS:
        if name in table:
            value = table[name]
            if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                raise ValueError(f"{where}: [spec]: {name} must be a positive number")
            values[name] = float(value)
    if "min_beads" in table:
        count = table["min_beads"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"{where}: [spec]: min_beads must be a whole number >= 1")
        values["min_beads"] = count
    if "ellipticity_min" in values and values["ellipticity_min"] > 1:
        raise ValueError(f"{where}: [spec]: ellipticity_min must not exceed 1")
    return SpecLimits(**values)


def evaluate(limits: SpecLimits, summary: Mapping[str, Stats], accepted: int) -> SpecResult:
    """Compare a channel's medians with the limits."""
    checks: list[Check] = []

    def median(metric: str) -> float | None:
        stats = summary.get(metric)
        return None if stats is None else stats.median

    def add(name: str, limit: str, value: float | None, ok: bool) -> None:
        status = Status.NOT_EVALUATED if value is None else (Status.PASS if ok else Status.FAIL)
        checks.append(Check(name, limit, value, status))

    if limits.min_beads is not None:
        add(
            "accepted beads",
            f">= {limits.min_beads}",
            float(accepted),
            accepted >= limits.min_beads,
        )
    enough = limits.min_beads is None or accepted >= limits.min_beads
    if limits.lateral_ratio_max is not None:
        for metric in ("ratio_x", "ratio_y"):
            value = median(metric) if enough else None
            add(
                f"median {metric}",
                f"<= {limits.lateral_ratio_max:g}",
                value,
                value is not None and value <= limits.lateral_ratio_max,
            )
    if limits.axial_ratio_max is not None:
        value = median("ratio_z") if enough else None
        add(
            "median ratio_z",
            f"<= {limits.axial_ratio_max:g}",
            value,
            value is not None and value <= limits.axial_ratio_max,
        )
    if limits.ellipticity_min is not None:
        value = median("ellipticity_xy") if enough else None
        add(
            "median ellipticity_xy",
            f">= {limits.ellipticity_min:g}",
            value,
            value is not None and value >= limits.ellipticity_min,
        )
    return SpecResult(tuple(checks))
