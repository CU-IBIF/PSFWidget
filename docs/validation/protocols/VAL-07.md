# Validation Protocol VAL-07: Report fitness (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-07, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | REP-01 to REP-08, QA-02, QA-06 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Show that reports are understandable without the tool and that status, provenance, and signatures are clear.

## 2. Scope

Session, trend, and audit review reports from VAL-03/VAL-05 data.

## 3. Materials

The facility manager and one external reader.

## 4. Method

Each reader reviews one report of each kind with a checklist: status and reasons clear; metrics and theory understandable; provenance (software version, settings, file hash, operator) present; signature verifiable with `psfwidget verify`.

## 5. Acceptance criteria

Every checklist item answered "yes" by both readers, or the report changed and re-reviewed; sign-off recorded.

## 6. Analysis

Checklist results and comments; changes made.

## 7. Records

Test record, reviewed reports with signatures.

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
