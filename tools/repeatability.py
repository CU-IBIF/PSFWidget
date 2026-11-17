"""Repeatability and reproducibility summary from a results database (VAL-03).

For each configuration (instrument / objective / channel / modality) and
metric, over the sessions in a period: number of sessions, mean, SD, and
coefficient of variation, overall and per operator (the user who recorded
each session), checked against the VAL-03 criteria (VV_PLAN §2.4): CV of
FWHM ≤ 5% laterally and ≤ 7% axially. Excluded sessions are left out.

Usage::

    python tools/repeatability.py --db qc.sqlite [--since 2026-11-20] [--until 2026-11-27]
        [--instrument sp8] [--out val03/]

Writes ``repeatability.csv`` and ``repeatability.md`` with ``--out``. Exit
status 0 if every FWHM metric meets its criterion, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import datetime
from pathlib import Path

from psfwidget.automation.track import parse_time
from psfwidget.tracking.store import ResultsDatabase

LIMITS = {"fwhm_x_um": 0.05, "fwhm_y_um": 0.05, "fwhm_z_um": 0.07}
"""Maximum CV per FWHM metric (VAL-03)."""
METRICS = ("fwhm_x_um", "fwhm_y_um", "fwhm_z_um", "ratio_z", "integrated_intensity", "strehl")


def operators(db: ResultsDatabase) -> dict[int, str]:
    """Return who recorded each session, from the audit trail."""
    return {
        int(entry["details"]["session_id"]): entry["user"]
        for entry in db.audit_log()
        if entry["action"] in ("record", "record (imported)") and "session_id" in entry["details"]
    }


def summarise(
    db: ResultsDatabase,
    since: datetime | None,
    until: datetime | None,
    instrument: str | None,
) -> list[dict[str, object]]:
    """Return one row per configuration, metric, and group (all, or one operator)."""
    who = operators(db)
    rows: list[dict[str, object]] = []
    for configuration in db.configurations():
        if instrument and configuration.instrument_id != instrument:
            continue
        for metric in METRICS:
            points = [
                p
                for p in db.points(configuration, metric)
                if p.value is not None
                and not p.excluded
                and (since is None or p.acquired_utc >= since)
                and (until is None or p.acquired_utc <= until)
            ]
            groups: dict[str, list[float]] = {"all": [p.value for p in points]}  # type: ignore[misc]
            for point in points:
                groups.setdefault(f"operator {who.get(point.session_id, '?')}", []).append(
                    point.value  # type: ignore[arg-type]
                )
            for group, values in groups.items():
                if len(values) < 2:
                    continue
                mean = statistics.fmean(values)
                sd = statistics.stdev(values)
                cv = sd / mean if mean else float("nan")
                limit = LIMITS.get(metric)
                rows.append(
                    {
                        "configuration": configuration.label,
                        "metric": metric,
                        "group": group,
                        "n": len(values),
                        "mean": mean,
                        "sd": sd,
                        "cv": cv,
                        "limit": limit,
                        "passed": None if limit is None else cv <= limit,
                    }
                )
    return rows


def main(argv: list[str] | None = None) -> int:
    """Print (and optionally write) the summary; return 0 if every criterion is met."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--instrument")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    since = parse_time(args.since) if args.since else None
    until = parse_time(args.until) if args.until else None
    with ResultsDatabase(args.db) as db:
        rows = summarise(db, since, until, args.instrument)
    lines = [
        "# VAL-03 repeatability and reproducibility",
        "",
        "| Configuration | Metric | Group | n | Mean | SD | CV | Limit | Result |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        result = "-" if row["passed"] is None else ("PASS" if row["passed"] else "FAIL")
        limit = "-" if row["limit"] is None else f"{row['limit']:.0%}"
        lines.append(
            f"| {row['configuration']} | {row['metric']} | {row['group']} | {row['n']} | "
            f"{row['mean']:.4g} | {row['sd']:.2g} | {row['cv']:.1%} | {limit} | {result} |"
        )
    print("\n".join(lines))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "repeatability.csv").open("w", newline="", encoding="utf-8") as h:
            writer = csv.DictWriter(h, fieldnames=list(rows[0]) if rows else ["configuration"])
            writer.writeheader()
            writer.writerows(rows)
        (args.out / "repeatability.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    checked = [r for r in rows if r["passed"] is not None]
    return 0 if checked and all(r["passed"] for r in checked) else 1


if __name__ == "__main__":
    sys.exit(main())
