# PSFWidget: Project Status and Outstanding Items

**Date:** 2026-11-14
**Version:** 0.0.1.dev0 (pre-release)
**Milestones:** M0 to M4 merged; M5 in review ([pull request 8](https://github.com/CU-IBIF/PSFWidget/pull/8)); M6 (validation and v1.0) not started.
**Companion documents:** [Project Plan](PLAN.md), [V&V Plan](VV_PLAN.md), [traceability matrix](traceability.csv), [Install guide](manual/INSTALL.md), [User manual](manual/USER_MANUAL.md)

This page is the single place to see what is done and what is not. The
"Tests" column counts the automated tests linked to each requirement with
`pytest.mark.req` (passed / run, on 2026-11-14, with the GUI tests under a
virtual display). Automated tests verify the software against its
requirements; they do not replace the validation activities (VAL-01 to
VAL-07) with real instruments and users.

---

## 1. Outstanding items

Items marked **⚠ Gate** block a milestone exit. Owners are proposed; please
confirm.

### 1.1 Waiting on the facility

| # | Item | Needed for | Proposed owner |
|---|---|---|---|
| F1 | **⚠ Gate** Choose the first instrument (and objective) for the M5 beta | M5 exit | Facility manager |
| F2 | **⚠ Gate** GUI usability session with 3–5 users (VAL-06, NFR-08) | M5 exit | Facility manager + developer |
| F3 | **⚠ Gate** Beta on 1–2 production microscopes, with the scheduled watch-folder run | M5 exit | Facility |
| F4 | **⚠ Gate** 7-day unattended soak on a Windows and a Linux machine | M4 exit (carried) | Facility IT + developer |
| F5 | TetraSpeck catalogue number (exact bead size and dye spectra in the profiles) | Bead-size correction, chromatic metrics | Facility (expected 2026-11-16) |
| F6 | Name the multiphoton system and supply a bead stack from it | THE-10 validation; LIF multiphoton detection | Facility |
| F7 | A multi-position ND2 file | IMP-02 golden test | Facility |
| F8 | Facility spec limits for each instrument (`[spec]` in the profiles) | PASS/FAIL statuses in reports | Facility manager |
| F9 | Reference datasets REF-1 and REF-2 (≥ 30 fields; deliberately perturbed acquisitions) | VAL-01 to VAL-04 | Facility |
| F10 | Procedural GLP controls: user accounts, clock synchronisation, folder permissions, backup retention policy | QA-07 validation records | Facility IT / QA |

### 1.2 Development work not done

| # | Item | Requirement | Priority | Note |
|---|---|---|---|---|
| D1 | Vectorial (Richards–Wolf) and refractive-index-mismatch (Gibson–Lanni) PSF model | THE-02 | S | Scalar model and 3D theoretical image exist; the mismatch matters little for beads on the coverslip |
| D2 | Normalise integrated intensity to exposure and gain | MET-04 | M (part) | Readers do not extract exposure yet |
| D3 | Merge another results database (import) | TRK-10 | S (part) | Backup and CSV export work |
| D4 | Full cron expressions in schedules | AUT-03 | M (part) | Plain-word schedules (`daily 07:30`, `weekly monday 07:30`, …) cover the planned jobs |
| D5 | Audit-trail review **report** (CSV export exists) | QA-06 | S (part) | |
| D6 | Overriding the report layout (branding works) | REP-08 | C (part) | |
| D7 | Published API reference (docstrings and the manual's API chapter exist) | UI-05 | M (part) | Generate with Sphinx or pdoc for v1.0 |
| D8 | Multiphoton detection in Leica LIF files | T3.7 | S | Set `modality = "multiphoton"` in the profile meanwhile |
| D9 | Encircled energy and MTF | MET-17, MET-18 | C | Could-haves; not planned for v1.0 |
| D10 | Per-bead axial asymmetry (computed on the averaged PSF only) | MET-09 | S (part) | |
| D11 | Automated performance benchmark in CI (measured by hand: 25 s, limit 60 s) | NFR-04 | S | |
| D12 | PDF text-extraction check in the report tests | REP-03 | — | PDFs are checked for validity and size |
| D13 | Package release: version 1.0, PyPI/conda packaging, signed installers | WP8 | M | Install is from source today |

### 1.3 Verification and validation not done

| # | Activity | Requirement | Milestone |
|---|---|---|---|
| V1 | **⚠ Gate** VAL-01 method comparison with MetroloJ_QC and/or PSFj (Bland–Altman) | NFR-02 | M6 |
| V2 | VAL-02 known-perturbation sensitivity (collar, RI mismatch, tilt, pinhole, undersampling) | MET-09, MET-11 | M6 |
| V3 | VAL-03 repeatability and reproducibility (sets realistic control limits) | TRK-04 | M5–M6 |
| V4 | VAL-04 theoretical-comparison sanity on well-aligned objectives | THE-03 to THE-10 | M5–M6 |
| V5 | VAL-05 ≥ 4-week parallel run against the current QC routine | TRK-05, AUT-* | M6 |
| V6 | VAL-07 report fitness review and sign-off | REP-* , QA-02 | M6 |
| V7 | Backup restore drill | AUT-07, QA-04 | M5–M6 |
| V8 | Manual GUI test script on Windows and macOS (CI runs the GUI tests on Linux only) | UI-01 to UI-04, NFR-05 | M5 |
| V9 | **⚠ Gate** Validation records: protocols, test records, summary report, signed (OECD GLP No. 17) | QA-07 | M6 |

### 1.4 Known limitations of the current results

- The LSM 980 test stack has 13 z-planes: too short for an averaged PSF, so no
  Strehl value; most AF568 beads are too close to the stack ends for an
  integrated intensity.
- The cropped NSPARC stack has 2–3 beads per channel, too few to judge it
  against the image-scanning model.
- The SP8 beads are about three times longer axially than theory (Strehl 0.12,
  more energy below focus): consistent with spherical aberration, to be
  investigated with the facility.

---

## 2. Requirement status

Status: **Done** = implemented and verified by automated tests; **Part** =
implemented in part (see the note); **Open** = not implemented; **V&V** =
implemented, verification relies on a validation activity still to do.

| ID | Pri | Status | Tests | Note |
|---|---|---|---|---|
| IMP-01 | M | Done | 38/38 | ND2, LIF, CZI, TIFF, OME-TIFF |
| IMP-02 | M | Done | 13/13 | Multi-position ND2 golden file still missing (F7) |
| IMP-03 | M | Done | 22/22 | |
| IMP-04 | M | Done | 13/13 | |
| IMP-05 | M | Done | 13/13 | `--set`, profiles, GUI table |
| IMP-06 | M | Done | 15/15 | Multiphoton needs `excitation_nm` instead of `emission_nm` |
| IMP-07 | S | Done | 3/3 | |
| IMP-08 | S | Done | 12/12 | |
| IMP-09 | S | Done | 11/11 | |
| IMP-10 | M | Done | 17/17 | |
| DET-01 to DET-06, DET-08 | M/S | Done | 1/1 each and more | |
| DET-07 | S | Done | 4/4 | GUI review with reason and reviewer |
| MET-01, MET-02 | M | Done | 13/13, 16/16 | |
| MET-03 | S | Done | 1/1 | |
| MET-04 | M | Part | 1/1 | No exposure/gain normalisation (D2) |
| MET-05 to MET-08 | M | Done | | |
| MET-09 | S | Part | 1/1 | On the averaged PSF only (D10) |
| MET-10 | M | Done | 4/4 | |
| MET-11 | S | Done | 4/4 | Fitted tilt and curvature, field-map figure |
| MET-12 | S | Done | 1/1 | Corner-to-centre ratio of a fitted surface |
| MET-13 | S | Done | 2/2 | |
| MET-14 | S | Done | 4/4 | `analyze --psf` |
| MET-15, MET-16 | S/M | Done | 2/2, 2/2 | |
| MET-17, MET-18 | C | Open | – | Not planned for v1.0 (D9) |
| THE-01 | M | Done | 8/8 | |
| THE-02 | S | Part | 4/4 | Scalar model only (D1) |
| THE-03 | M | Done | 8/8 | |
| THE-04, THE-05 | S | Done | 2/2, 3/3 | |
| THE-06 | M | Done | 4/4 | No limits shipped; facility sets them (F8) |
| THE-07 | M | Done | 1/1 | Orthogonal views and profile fits in session reports |
| THE-08, THE-09 | M/S | Done | 8/8, 2/2 | |
| THE-10 | M | Done | 10/10 | No facility multiphoton stack yet (F6) |
| TRK-01 to TRK-08 | M/S | Done | | |
| TRK-09 | C | Done | 1/1 | |
| TRK-10 | S | Part | 2/2 | No import (D3) |
| REP-01 to REP-07 | M | Done | | |
| REP-08 | C | Part | 1/1 | Branding only (D6) |
| AUT-01, AUT-02 | M | Done | 11/11, 3/3 | |
| AUT-03 | M | Part | 20/20 | No cron expressions (D4); 7-day soak open (F4) |
| AUT-04 to AUT-07 | M/S | Done | | |
| UI-01 to UI-04 | M/S | Done | | Usability validation open (F2) |
| UI-05 | M | Part | 1/1 | API chapter in the manual; no generated reference (D7) |
| NFR-01 | M | Done | 4/4 | |
| NFR-02 | M | V&V | – | VAL-01 (V1) |
| NFR-03 | M | Done | 3/3 | |
| NFR-04 | S | Done (manual) | – | 25 s on 4 cores; automate (D11) |
| NFR-05 | M | Done (CI) | – | CI on Windows, macOS, Linux; GUI tests on Linux only (V8) |
| NFR-06, NFR-07 | M | Done | 6/6, 11/11 | |
| NFR-08 | S | V&V | – | VAL-06 (F2) |
| NFR-09 | S | Done (CI) | – | 95% core coverage; 87% GUI |
| NFR-10 | M | Done | 1/1 | 146 packages checked, no GPL |
| NFR-11 | M | Done | 2/2 | |
| QA-01 to QA-05 | M | Done | | |
| QA-06 | S | Part | 1/1 | CSV only (D5) |
| QA-07 | M | Open | – | Validation records (V9) |

---

## 3. Milestones

| Milestone | State | What remains |
|---|---|---|
| M0 Foundation | Merged | Multi-position ND2 (F7) |
| M1 Import | Merged | – |
| M2 Core metrics | Merged | Exposure normalisation (D2) |
| M3 Tracking | Merged | Database import (D3) |
| M4 Reporting and automation | Merged | 7-day soak (F4), PDF text check (D12) |
| M5 GUI and Shoulds | In review | THE-02 (D1); beta (F1, F3); usability (F2) |
| M6 Validation and v1.0 | Not started | V1–V9, D7, D13 |
