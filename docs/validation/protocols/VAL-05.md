# Validation Protocol VAL-05: Parallel-run field trial (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-05, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | TRK-05, AUT-01 to AUT-07, NFR-06, NFR-11 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Show that unattended PSFWidget QC catches what the facility's current QC catches, without excessive false alarms.

## 2. Scope

1–2 production microscopes (**TBD**) for ≥ 4 weeks.

## 3. Materials

Acquisition PCs set up per the install guide §6 (local database, network reports and backups, scheduled runs); the current QC routine.

## 4. Method

1. Run the current QC routine and PSFWidget side by side on the same acquisitions.
2. Record every event (service, alignment, …) in both.
3. Weekly: review `STATUS.json`, `ALERT.txt`, trend reports, and run logs; note missed runs.

## 5. Acceptance criteria

Every event detected by the current routine is also detected; ≤ 1 Westgard rejection without an assignable cause per instrument per month (or justified); no missed scheduled runs; no unhandled errors.

## 6. Analysis

Side-by-side event table; false-alarm count; run-log summary.

## 7. Records

Test record, trend reports, run summaries, alert files, the audit review report (`psfwidget report audit`).

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
