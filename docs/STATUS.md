# PSFWidget: Project Status and Outstanding Items

**Date:** 2026-11-20 (updated after milestone M5.4a)
**Version:** 0.0.1.dev0 (pre-release)
**Milestones:** M0 to M5 merged ([pull request 8](https://github.com/CU-IBIF/PSFWidget/pull/8)); M5.1 to M5.3 in review ([pull request 9](https://github.com/CU-IBIF/PSFWidget/pull/9)); M6 (validation and v1.0): tools, templates, and draft protocols ready, studies not started.
**Companion documents:** [Project Plan](PLAN.md), [V&V Plan](VV_PLAN.md), [traceability matrix](traceability.csv), [Install guide](manual/INSTALL.md), [User manual](manual/USER_MANUAL.md), [Theoretical PSF](THEORY.md)

This page is the single place to see what is done and what is not. The
"Tests" column counts the automated tests linked to each requirement with
`pytest.mark.req` (passed / run, on 2026-11-17, with the GUI tests under a
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

### 1.2 Development work

The development tasks of 2026-11-17 (WP10, docs/PLAN.md §5) are done:

| Task | Requirement | Done |
|---|---|---|
| T10.1 Vectorial and refractive-index-mismatch model | THE-02 | `[theory]` profile keys; agrees with the exact Gibson–Lanni integration (MicroscPSF-Py) within 0.3% |
| T10.2 Exposure normalisation | MET-04 | exposure, dwell, accumulation read from all four formats; `intensity_per_ms`; warning when settings change |
| T10.3 Database import | TRK-10 | `psfwidget track import` |
| T10.4 Cron schedules | AUT-03 | `cron M H DOM MON DOW` |
| T10.5 Audit review report | QA-06 | `psfwidget report audit` |
| T10.6 API reference | UI-05 | `tools/build_api_docs.py`, built in CI; published to Pages from `main` |
| T10.7 Packaging (preparation) | WP8 | wheel and sdist built, checked, and self-tested on 3 OSes in CI; release workflow; validated pins; [release checklist](RELEASE_CHECKLIST.md) |

Still open:

| # | Item | Requirement | Priority | Note |
|---|---|---|---|---|
| D1 | **⚠ Gate (M6)** Release v1.0.0: version, tag, PyPI or facility index, Pages | WP8 | M | Workflows ready; untested until the first tag and merge to `main`; needs PyPI trusted publishing or IT's index, and Pages enabled |
| D2 | Multiphoton detection in Leica LIF files | T3.7 | S | Needs a multiphoton LIF file (F6); set `modality = "multiphoton"` in the profile meanwhile |
| D3 | Detector gain and laser power read from files (only from profiles/`--set` today) | MET-04 | S (part) | Formats store them per detector or laser, not per channel |
| D4 | Exposure settings per channel when channels differ (one value for the acquisition today) | MET-04 | S (part) | |
| D5 | Overriding the report layout (branding works) | REP-08 | C (part) | |
| D6 | Encircled energy and MTF | MET-17, MET-18 | C | Not planned for v1.0 |
| D7 | Per-bead axial asymmetry (on the averaged PSF only) | MET-09 | S (part) | |
| D8 | Automated performance benchmark in CI (25 s by hand, limit 60 s) | NFR-04 | S | |
| D9 | PDF text-extraction check in the report tests | REP-03 | — | |
| D10 | Supercritical-angle fluorescence and Fresnel transmission in the mismatch model | THE-02 | C | Documented limitation |

### 1.3 Verification and validation not done

| # | Activity | Requirement | Milestone |
|---|---|---|---|
| V1 | **⚠ Gate** VAL-01 method comparison with MetroloJ_QC and/or PSFj (Bland–Altman; `tools/method_comparison.py` ready; check its column mapping on a real export) | NFR-02 | M6 |
| V2 | VAL-02 known-perturbation sensitivity (collar, RI mismatch, tilt, pinhole, undersampling) | MET-09, MET-11 | M6 |
| V3 | VAL-03 repeatability and reproducibility (sets realistic control limits; `tools/repeatability.py` ready) | TRK-04 | M5–M6 |
| V4 | VAL-04 theoretical-comparison sanity on well-aligned objectives | THE-03 to THE-10 | M5–M6 |
| V5 | VAL-05 ≥ 4-week parallel run against the current QC routine | TRK-05, AUT-* | M6 |
| V6 | VAL-07 report fitness review and sign-off | REP-* , QA-02 | M6 |
| V7 | Backup restore drill (procedure and `psfwidget track restore-check` ready, T11.3) | AUT-07, QA-04 | M5.3 |
| V8 | Manual GUI test script on Windows and macOS ([script ready](validation/GUI_TEST_SCRIPT.md), T11.4). CI runs the GUI tests on Linux; on Windows, CI runs them without blocking, because the 9 that draw images fail on the CI machines' software OpenGL | UI-01 to UI-04, NFR-05 | M5 |
| V9 | **⚠ Gate** Validation records: [draft protocols](validation/protocols/README.md) to complete and approve before each study, then [test records, anomaly log, and V&V summary](validation/templates/) signed (OECD GLP No. 17) | QA-07 | M6 |

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
| IMP-02 | M | Done | 24/24 | GUI series dialog (M5.4a); multi-position ND2 golden file still missing (F7) |
| IMP-03 | M | Done | 22/22 | |
| IMP-04 | M | Done | 13/13 | |
| IMP-05 | M | Done | 13/13 | `--set`, profiles, GUI table |
| IMP-06 | M | Done | 15/15 | Multiphoton needs `excitation_nm` instead of `emission_nm` |
| IMP-07 | S | Done | 3/3 | |
| IMP-08 | S | Done | 12/12 | |
| IMP-09 | S | Done | 11/11 | |
| IMP-10 | M | Done | 17/17 | |
| IMP-11 | M | Done | 11/11 | Drag and drop (M5.4a); manual check on the facility PC open (A4) |
| DET-01 to DET-06, DET-08 | M/S | Done | 1/1 each and more | |
| DET-07 | S | Done | 4/4 | GUI review with reason and reviewer |
| MET-01, MET-02 | M | Done | 13/13, 16/16 | |
| MET-03 | S | Done | 1/1 | |
| MET-04 | M | Done | 10/10 | Normalised to exposure; gain and power from profiles only (D3, D4) |
| MET-05 to MET-08 | M | Done | | |
| MET-09 | S | Part | 1/1 | On the averaged PSF only (D7) |
| MET-10 | M | Done | 4/4 | |
| MET-11 | S | Done | 4/4 | Fitted tilt and curvature, field-map figure |
| MET-12 | S | Done | 1/1 | Corner-to-centre ratio of a fitted surface |
| MET-13 | S | Done | 2/2 | |
| MET-14 | S | Done | 4/4 | `analyze --psf` |
| MET-15, MET-16 | S/M | Done | 2/2, 2/2 | |
| MET-17, MET-18 | C | Open | – | Not planned for v1.0 (D6) |
| THE-01 | M | Done | 8/8 | |
| THE-02 | S | Done | 17/17 | Vectorial and Gibson–Lanni mismatch models; SAF and Fresnel terms not modelled (D10) |
| THE-03 | M | Done | 8/8 | |
| THE-04, THE-05 | S | Done | 2/2, 3/3 | |
| THE-06 | M | Done | 4/4 | No limits shipped; facility sets them (F8) |
| THE-07 | M | Done | 1/1 | Image panel (closest bead, average PSF, model) and profile fits in session reports |
| THE-08, THE-09 | M/S | Done | 8/8, 2/2 | |
| THE-10 | M | Done | 10/10 | No facility multiphoton stack yet (F6) |
| TRK-01 to TRK-08 | M/S | Done | | |
| TRK-09 | C | Done | 1/1 | |
| TRK-10 | S | Done | 5/5 | Backup, CSV export, and import |
| REP-01 to REP-07 | M | Done | | |
| REP-08 | C | Part | 1/1 | Branding only (D5) |
| AUT-01, AUT-02 | M | Done | 11/11, 3/3 | |
| AUT-03 | M | Done | 40/40 | Plain-word and cron schedules; 7-day soak open (F4) |
| AUT-04 to AUT-07 | M/S | Done | | |
| UI-01 to UI-04 | M/S | Done | | Usability validation open (F2) |
| UI-05 | M | Done | 41/41 | Generated API reference; every public name documented |
| NFR-01 | M | Done | 4/4 | |
| NFR-02 | M | V&V | – | VAL-01 (V1) |
| NFR-03 | M | Done | 3/3 | |
| NFR-04 | S | Done (manual) | – | 25 s on 4 cores; automate (D8) |
| NFR-05 | M | Done (CI) | – | CI on Windows, macOS, Linux; GUI tests blocking on Linux, non-blocking on Windows (V8) |
| NFR-06, NFR-07 | M | Done | 6/6, 11/11 | |
| NFR-08 | S | V&V | – | VAL-06 (F2) |
| NFR-09 | S | Done (CI) | – | 95% core coverage; 87% GUI |
| NFR-10 | M | Done | 1/1 | 146 packages checked, no GPL |
| NFR-11 | M | Done | 2/2 | |
| QA-01 to QA-05 | M | Done | | |
| QA-06 | S | Done | 2/2 | CSV export and the audit review report |
| QA-07 | M | Open | – | Templates and draft protocols ready; records to be produced (V9) |

---

## 3. Milestones

| Milestone | State | What remains |
|---|---|---|
| M0 Foundation | Merged | Multi-position ND2 (F7) |
| M1 Import | Merged | – |
| M2 Core metrics | Merged | – |
| M3 Tracking | Merged | – |
| M4 Reporting and automation | Merged | 7-day soak (F4), PDF text check (D9) |
| M5 GUI and Shoulds | Merged | beta (F1, F3); usability (F2) |
| M5.1 Theory and intensity | In review | – |
| M5.2 Records and automation | In review | – |
| M5.3 Release readiness | In review | restore drill and GUI checks to run (V7, V8) |
| M5.4 GUI revision (facility feedback, crash on load) | [Plan](M5.4_GUI_REVISION_PLAN.md); M5.4a done, in review | Facility re-test of M5.4a (A4, A5, G4); M5.4b review workflow; M5.4c reports |
| M6 Validation and v1.0 | Prepared | studies V1–V6, records V9, release D1 |
