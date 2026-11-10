"""Build and check the requirements traceability matrix.

Reads requirement rows from ``docs/PLAN.md`` (``| ID | text | priority |``) and
the verification rows from the matrix in ``docs/VV_PLAN.md`` (``| IDs | methods
| activities |``), and writes ``docs/traceability.csv``.

Usage::

    python tools/traceability.py           # regenerate docs/traceability.csv
    python tools/traceability.py --check   # exit 1 if the CSV is stale or inconsistent
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "PLAN.md"
VV_PLAN = ROOT / "docs" / "VV_PLAN.md"
CSV_PATH = ROOT / "docs" / "traceability.csv"

REQ_ID = r"[A-Z]{2,4}-\d{2}"
_PLAN_ROW = re.compile(rf"^\|\s*({REQ_ID})\s*\|(.+)\|\s*([MSC])\s*\|\s*$")
_VV_ROW = re.compile(rf"^\|\s*({REQ_ID}[^|]*)\|([^|]+)\|([^|]+)\|\s*$")
_RANGE = re.compile(rf"^({REQ_ID})\s*(?:…|\.\.\.)\s*({REQ_ID})$")
VALID_METHODS = frozenset("UISVR")
FIELDS = ("id", "priority", "requirement", "methods", "activities")


@dataclass(frozen=True)
class Requirement:
    """One row of the traceability matrix."""

    id: str
    priority: str
    requirement: str
    methods: str
    activities: str


def expand_ids(spec: str) -> list[str]:
    """Expand ``"DET-01 … DET-03, DET-08"`` into individual requirement IDs."""
    ids: list[str] = []
    for part in (p.strip() for p in spec.split(",")):
        match = _RANGE.match(part)
        if match is None:
            if not re.fullmatch(REQ_ID, part):
                raise ValueError(f"not a requirement ID or range: {part!r}")
            ids.append(part)
            continue
        start, end = match.groups()
        prefix, lo = start.rsplit("-", 1)
        end_prefix, hi = end.rsplit("-", 1)
        if prefix != end_prefix or int(lo) > int(hi):
            raise ValueError(f"invalid requirement range: {part!r}")
        ids.extend(f"{prefix}-{n:02d}" for n in range(int(lo), int(hi) + 1))
    return ids


def parse_plan(text: str) -> dict[str, tuple[str, str]]:
    """Return ``{id: (priority, requirement text)}`` from PLAN.md."""
    reqs: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        match = _PLAN_ROW.match(line)
        if match is None:
            continue
        req_id, body, priority = match.groups()
        if req_id in reqs:
            raise ValueError(f"duplicate requirement ID in PLAN.md: {req_id}")
        reqs[req_id] = (priority, body.strip())
    return reqs


def parse_vv(text: str) -> dict[str, tuple[str, str]]:
    """Return ``{id: (methods, activities)}`` from the VV_PLAN.md matrix."""
    rows: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        match = _VV_ROW.match(line)
        if match is None:
            continue
        spec, methods, activities = (g.strip() for g in match.groups())
        method_set = {m.strip() for m in methods.split(",")}
        if not method_set <= VALID_METHODS:
            continue  # a different table whose first column happens to hold an ID
        for req_id in expand_ids(spec):
            if req_id in rows:
                raise ValueError(f"requirement listed twice in VV matrix: {req_id}")
            rows[req_id] = (methods, activities)
    return rows


def build(plan_text: str, vv_text: str) -> list[Requirement]:
    """Join the two documents; raise if any requirement is untraced or unknown."""
    plan = parse_plan(plan_text)
    vv = parse_vv(vv_text)
    errors = [f"{r}: in PLAN.md but missing from the VV matrix" for r in plan if r not in vv]
    errors += [f"{r}: in the VV matrix but not defined in PLAN.md" for r in vv if r not in plan]
    if errors:
        raise ValueError("traceability errors:\n  " + "\n  ".join(errors))
    return [Requirement(r, p, text, *vv[r]) for r, (p, text) in plan.items()]


def to_csv(rows: list[Requirement]) -> str:
    """Serialise rows to CSV text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow([row.id, row.priority, row.requirement, row.methods, row.activities])
    return buffer.getvalue()


def load_ids(path: Path = CSV_PATH) -> set[str]:
    """Return the requirement IDs listed in a traceability CSV."""
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["id"] for row in csv.DictReader(handle)}


def expected_csv() -> str:
    """Return the CSV text the current documents should produce."""
    return to_csv(build(PLAN.read_text("utf-8"), VV_PLAN.read_text("utf-8")))


def main(argv: list[str] | None = None) -> int:
    """Regenerate or check ``docs/traceability.csv``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the CSV is out of date")
    args = parser.parse_args(argv)
    try:
        expected = expected_csv()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.check:
        current = CSV_PATH.read_text("utf-8") if CSV_PATH.exists() else ""
        if current != expected:
            print(
                f"{CSV_PATH.relative_to(ROOT)} is stale; run: python tools/traceability.py",
                file=sys.stderr,
            )
            return 1
        return 0
    CSV_PATH.write_text(expected, encoding="utf-8", newline="")
    print(f"wrote {CSV_PATH.relative_to(ROOT)} ({expected.count(chr(10)) - 1} requirements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
