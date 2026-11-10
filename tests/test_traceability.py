"""Tests for the requirements traceability tooling (docs/VV_PLAN.md §4)."""

from __future__ import annotations

import pytest

import traceability


def test_csv_is_in_sync_with_plan_documents() -> None:
    assert traceability.main(["--check"]) == 0


def test_every_requirement_has_a_verification_method() -> None:
    rows = traceability.build(
        traceability.PLAN.read_text("utf-8"), traceability.VV_PLAN.read_text("utf-8")
    )
    assert len(rows) == len({r.id for r in rows})
    for row in rows:
        methods = {m.strip() for m in row.methods.split(",")}
        assert methods, row.id
        assert methods <= traceability.VALID_METHODS, row.id


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("IMP-01", ["IMP-01"]),
        ("IMP-02, IMP-03", ["IMP-02", "IMP-03"]),
        ("DET-01 … DET-03, DET-08", ["DET-01", "DET-02", "DET-03", "DET-08"]),
        ("UI-01 ... UI-02", ["UI-01", "UI-02"]),
    ],
)
def test_expand_ids(spec: str, expected: list[str]) -> None:
    assert traceability.expand_ids(spec) == expected


@pytest.mark.parametrize("spec", ["DET-03 … DET-01", "DET-01 … MET-03", "not-an-id"])
def test_expand_ids_rejects_bad_input(spec: str) -> None:
    with pytest.raises(ValueError, match="requirement"):
        traceability.expand_ids(spec)


def test_build_reports_untraced_and_unknown_requirements() -> None:
    plan = "| AAA-01 | First. | M |\n| AAA-02 | Second. | S |\n"
    vv = "| AAA-01 | U | unit tests |\n| AAA-03 | I | ghost |\n"
    with pytest.raises(ValueError, match=r"AAA-02: in PLAN\.md") as exc:
        traceability.build(plan, vv)
    assert "AAA-03: in the VV matrix but not defined" in str(exc.value)


def test_duplicate_requirement_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        traceability.parse_plan("| AAA-01 | One. | M |\n| AAA-01 | Again. | M |\n")
