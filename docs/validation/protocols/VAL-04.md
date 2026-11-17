# Validation Protocol VAL-04: Theoretical comparison sanity (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-04, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | THE-01, THE-02, THE-03, THE-08, THE-09, THE-10 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Check that measured/theoretical ratios are physically plausible on well-aligned objectives.

## 2. Scope

The VAL-03 data, plus NSPARC, Live-SR, and multiphoton configurations (multiphoton system **TBD**).

## 3. Materials

As VAL-03.

## 4. Method

From the VAL-03 sessions, collect the median ratios per configuration (`psfwidget track export`). For resolution-enhanced modes, also the image-scanning ratios. Where beads are not on the coverslip or the mountant differs, set `sample_ri` and `bead_depth_um` in the profile and compare with the refined model.

## 5. Acceptance criteria

Lateral ratio 1.0–1.3; axial ratio 1.0–1.5 (base modality). Values below 1.0 (other than expected for resolution-enhanced modes) trigger a check of metadata and bead-size handling.

## 6. Analysis

Table per configuration; investigation notes for any value outside the range.

## 7. Records

Test record, export CSV.

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
