"""Table-driven tests of the Westgard rule engine (TRK-05, docs/VV_PLAN.md §2.1)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from psfwidget.tracking.westgard import ALL_RULES, Rule, evaluate, parse_rules, severity

R12, R13, R22, R4, R41, R10 = (
    Rule.R1_2S,
    Rule.R1_3S,
    Rule.R2_2S,
    Rule.R4S,
    Rule.R4_1S,
    Rule.R10X,
)

# (description, z-scores, {position: rules expected there}); unlisted positions: none.
CASES: list[tuple[str, Sequence[float | None], dict[int, set[Rule]]]] = [
    ("all in control", [0.1, -0.5, 1.2, -1.9, 0.0], {}),
    ("exactly 2 SD is not beyond 2 SD", [2.0, -2.0], {}),
    ("1_2s warning above and below", [2.01, 0.0, -2.5], {0: {R12}, 2: {R12}}),
    ("exactly 3 SD is a warning only", [3.0], {0: {R12}}),
    ("1_3s, and R_4s from the swing", [0.0, 3.01, -3.5], {1: {R12, R13}, 2: {R12, R13, R4}}),
    ("1_3s alone", [0.0, 3.01, 0.0, -3.5], {1: {R12, R13}, 3: {R12, R13}}),
    ("2_2s same side", [0.5, 2.1, 2.2], {1: {R12}, 2: {R12, R22}}),
    ("2_2s below", [-2.3, -2.1], {0: {R12}, 1: {R12, R22}}),
    ("2_2s needs consecutive sessions", [2.1, 0.0, 2.2], {0: {R12}, 2: {R12}}),
    ("R_4s across consecutive sessions", [2.1, -2.1], {0: {R12}, 1: {R12, R4}}),
    ("R_4s needs both beyond 2 SD", [2.5, -1.9], {0: {R12}}),
    ("4_1s above", [1.1, 1.2, 1.5, 1.01], {3: {R41}}),
    ("4_1s below", [-1.1, -1.2, -1.5, -1.01], {3: {R41}}),
    ("4_1s broken by a point inside 1 SD", [1.1, 1.2, 0.9, 1.5, 1.6], {}),
    ("4_1s mixed sides", [1.1, -1.2, 1.5, 1.6], {}),
    ("10_x above", [0.1] * 10, {9: {R10}}),
    ("10_x keeps firing", [-0.3] * 11, {9: {R10}, 10: {R10}}),
    ("10_x broken by the mean itself", [0.1] * 5 + [0.0] + [0.1] * 9, {}),
    (
        "10_x and 4_1s together",
        [1.2] * 10,
        {3: {R41}, 4: {R41}, 5: {R41}, 6: {R41}, 7: {R41}, 8: {R41}, 9: {R41, R10}},
    ),
    ("excluded sessions are skipped", [2.1, None, 2.2], {0: {R12}, 2: {R12, R22}}),
    ("excluded sessions are skipped for 4_1s", [1.1, 1.2, None, None, 1.5, 1.6], {5: {R41}}),
    ("excluded points get no violations", [None, 5.0, None], {1: {R12, R13}}),
]


@pytest.mark.req("TRK-05")
@pytest.mark.parametrize(
    ("z", "expected"), [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES]
)
def test_rules(z: list[float | None], expected: dict[int, set[Rule]]) -> None:
    result = evaluate(z)
    assert len(result) == len(z)
    assert {i: found for i, found in enumerate(result) if found} == expected


@pytest.mark.req("TRK-05")
def test_rule_subsets() -> None:
    z: list[float | None] = [2.5, 2.6, 3.5]
    assert evaluate(z, [R13]) == [set(), set(), {R13}]
    assert evaluate(z, []) == [set(), set(), set()]
    assert evaluate(z, ALL_RULES)[2] == {R12, R13, R22}


def test_parse_rules() -> None:
    assert parse_rules(["1_3s", "r_4S", "1_3s"]) == (R13, R4)
    assert parse_rules([]) == ()
    with pytest.raises(ValueError, match="unknown Westgard rule '2_3s'"):
        parse_rules(["2_3s"])


def test_severity() -> None:
    assert severity(set()) == "ok"
    assert severity({R12}) == "warning"
    assert severity({R12, R22}) == "rejection"
    assert severity({R10}) == "rejection"
    assert R12.is_warning
    assert not R13.is_warning
