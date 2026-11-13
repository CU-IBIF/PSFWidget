"""Westgard multi-rule quality control (TRK-05).

Rules for one control level, evaluated across consecutive sessions. ``z`` is
a session's deviation from the baseline mean in baseline SDs.

========  =========  ==============================================================
Rule      Severity   Triggered at a session when
========  =========  ==============================================================
1_2s      warning    abs(z) > 2
1_3s      rejection  abs(z) > 3
2_2s      rejection  it and the previous session are both beyond 2 SD on the same side
R_4s      rejection  it and the previous session are beyond 2 SD on opposite sides
4_1s      rejection  it and the previous 3 sessions are all beyond 1 SD on the same side
10_x      rejection  it and the previous 9 sessions are all on the same side of the mean
========  =========  ==============================================================

"Previous" means previous *included* sessions: excluded sessions and sessions
without a value are skipped, not counted. Sequences restart at every baseline
segment (after a rebaseline). A rule is reported at the session that
completes the pattern.

Reference: Westgard JO, Barry PL, Hunt MR, Groth T. A multi-rule Shewhart
chart for quality control in clinical chemistry. Clin Chem 1981;27:493-501.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum


class Rule(StrEnum):
    """A Westgard rule."""

    R1_2S = "1_2s"
    R1_3S = "1_3s"
    R2_2S = "2_2s"
    R4S = "R_4s"
    R4_1S = "4_1s"
    R10X = "10_x"

    @property
    def is_warning(self) -> bool:
        """Whether the rule only warns (1_2s) rather than rejects."""
        return self is Rule.R1_2S


ALL_RULES: tuple[Rule, ...] = tuple(Rule)


def parse_rules(names: Iterable[str]) -> tuple[Rule, ...]:
    """Return rules from their names, e.g. ``["1_3s", "R_4s"]``, case-insensitively.

    Raises
    ------
    ValueError
        If a name is not a rule.
    """
    by_name = {rule.value.lower(): rule for rule in Rule}
    rules = []
    for name in names:
        key = str(name).strip().lower()
        if key not in by_name:
            raise ValueError(f"unknown Westgard rule {name!r}; known: {', '.join(Rule)}")
        rules.append(by_name[key])
    return tuple(dict.fromkeys(rules))


def _same_side(values: Sequence[float], limit: float) -> bool:
    """Whether every value is beyond ``limit`` on the same side of zero."""
    return all(v > limit for v in values) or all(v < -limit for v in values)


def evaluate(
    z_scores: Sequence[float | None], rules: Iterable[Rule] = ALL_RULES
) -> list[set[Rule]]:
    """Return the rules violated at each position of ``z_scores``.

    ``None`` marks a session that is excluded or has no value; it gets no
    violations and is skipped when looking back.
    """
    active = set(rules)
    result: list[set[Rule]] = [set() for _ in z_scores]
    history: list[float] = []
    for index, z in enumerate(z_scores):
        if z is None:
            continue
        history.append(z)
        found = result[index]
        if Rule.R1_2S in active and abs(z) > 2:
            found.add(Rule.R1_2S)
        if Rule.R1_3S in active and abs(z) > 3:
            found.add(Rule.R1_3S)
        if Rule.R2_2S in active and len(history) >= 2 and _same_side(history[-2:], 2):
            found.add(Rule.R2_2S)
        if Rule.R4S in active and len(history) >= 2:
            previous = history[-2]
            if (z > 2 and previous < -2) or (z < -2 and previous > 2):
                found.add(Rule.R4S)
        if Rule.R4_1S in active and len(history) >= 4 and _same_side(history[-4:], 1):
            found.add(Rule.R4_1S)
        if Rule.R10X in active and len(history) >= 10 and _same_side(history[-10:], 0):
            found.add(Rule.R10X)
    return result


def severity(violations: set[Rule]) -> str:
    """Return ``"rejection"``, ``"warning"``, or ``"ok"`` for a session's violations."""
    if any(not rule.is_warning for rule in violations):
        return "rejection"
    if violations:
        return "warning"
    return "ok"
