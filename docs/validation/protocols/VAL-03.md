# Validation Protocol VAL-03: Repeatability and reproducibility (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-03, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | TRK-04, NFR-03 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Measure the realistic variability of the metrics, which sets the control limits.

## 2. Scope

Each instrument in the beta (**TBD**), its standard QC objective and channels.

## 3. Materials

Bead slide; 3 operators.

## 4. Method

1. Repeatability: one operator images the same slide 10 times in one day (re-focusing and re-selecting a field each time), recording each into the database (`--db`, `--user`).
2. Reproducibility: 3 operators each image the slide once a day for 5 days.
3. Run `python tools/repeatability.py --db qc.sqlite --since … --until … --out val03/` for each part.

## 5. Acceptance criteria

CV of FWHM ≤ 5% laterally and ≤ 7% axially (overall and per operator).

## 6. Analysis

CV per configuration and metric, overall and per operator; operator effect discussed. Use the reproducibility data as the baseline for VAL-05.

## 7. Records

Test record, `val03/` outputs, database backup with SHA-256.

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
