# Validation Protocol VAL-02: Known-perturbation sensitivity (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-02, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | MET-09, MET-11, MET-12, MET-16, THE-06, TRK-05 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Show that deliberate degradations change the expected metrics and are flagged.

## 2. Scope

One well-aligned confocal and one widefield or spinning-disk configuration (**TBD**).

## 3. Materials

Bead slide; a second slide in a mismatched mountant (**TBD**); a tilting shim.

## 4. Method

For each configuration, after ≥ 5 normal sessions (baseline), acquire:

| Perturbation | Expected change |
|---|---|
| Correction collar off by a set amount | axial FWHM ↑, axial asymmetry, Strehl ↓ |
| Wrong immersion medium / RI-mismatched mountant | axial FWHM ↑, focus shift, asymmetry; agrees with the THE-02 model when `sample_ri` and depth are set |
| Tilted slide (shim) | tilt angle and focus range ↑ (MET-11) |
| Pinhole opened (confocal) | lateral and axial FWHM ↑ towards the open-pinhole theory |
| Undersampled z step | Nyquist warning (MET-16) |

Analyse each with `psfwidget analyze … --db qc.sqlite --report html`.

## 5. Acceptance criteria

Each perturbation moves its metric in the expected direction by more than the VAL-03 repeatability (2 SD), and is flagged by a spec limit, a Westgard rule, or a warning.

## 6. Analysis

Table of metric change vs. baseline mean and SD; screenshots of the Levey-Jennings charts.

## 7. Records

Test record, data manifest, reports, chart files.

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
