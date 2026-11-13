# PSFWidget: Verification & Validation (V&V) Plan

**Status:** Draft v0.1, for review
**Date:** 2026-11-10
**Companion document:** [Project Plan & Requirements](PLAN.md)

---

## 1. Purpose

This plan defines how PSFWidget will be shown to:

- **Verification:** *"Did we build it right?"* The software meets every requirement in [PLAN.md §3](PLAN.md#3-requirements).
- **Validation:** *"Did we build the right thing?"* The tool reliably detects real changes in microscope performance and fits facility QC workflows.

The structure follows a V-model, adapted for a research and core facility tool. It can be scaled up (formal IQ/OQ/PQ sign-off) if open question #5 in the plan shows that a regulated quality system applies.

```
 Stakeholder needs ───────────────────────────────► Validation (UAT, field trial, method comparison)
   Requirements (PLAN §3) ─────────────────────► System tests (end-to-end, CLI/GUI, scheduled runs)
     Architecture (PLAN §4) ──────────────────► Integration tests (pipeline stages, DB, reports)
       Module design ────────────────────────► Unit tests (fitting, rules, readers, formulas)
```

---

## 2. V&V levels

### 2.1 Level 1: Unit verification (automated, every commit)

| Area | Approach | Pass criteria |
|---|---|---|
| Gaussian fitting (1D/2D/3D) | Fit noiseless analytic Gaussians across FWHM 1.5–10 px and sub-pixel offsets; fit noisy variants (Poisson, SNR 10–100) in Monte Carlo runs (≥ 500 realizations per condition) | Noiseless: \|error\| < 0.1%. SNR ≥ 20 and ≥ Nyquist: per-bead bias ≤ 1%; median of a 10-bead field: mean error ≤ 3%, P95 ≤ 5% (NFR-01). Bias and precision tables are generated into `docs/validation/fitting_accuracy.md` |
| 2D rotated fits (XY/XZ/YZ) | Elliptical Gaussians at known angles 0–180° and ellipticities 1.0–3.0 | Angle error < 2° for ellipticity ≥ 1.2; axis FWHM within NFR-01 |
| Integrated intensity | Synthetic beads of known total photon count on known background | Recovered within 2% (noiseless) and within the expected Poisson SD (noisy) |
| Background, SBR, and SNR | Known background mean and noise | Within 2% / statistical tolerance |
| Detection and filters | Synthetic fields with known bead positions, plus injected doublets, edge beads, and saturated beads | Recall ≥ 98%, precision ≥ 98% on valid beads; 100% correct rejection reason for injected defects |
| Theoretical formulas | Compare against hand-calculated values from cited references for a table of (λ, NA, n, pinhole) | Exact agreement to 4 significant figures |
| Theoretical PSF model | Scalar model: widefield lateral FWHM against the exact Airy value; point-detector confocal against the closed-pinhole formulas; open-pinhole limit against the open formulas. Gibson-Lanni model (WP3): against analytic values and `MicroscPSF-Py` | Lateral within 1% (Airy within 0.2%); axial within 6%, the known error of the 0.88 approximation |
| Westgard engine | Table-driven tests: hand-built sequences that should and should not trigger each rule (1₂ₛ, 1₃ₛ, 2₂ₛ, R₄ₛ, 4₁ₛ, 10ₓ), including boundaries, excluded points, and re-baselines | 100% of cases match expected flags |
| Baseline statistics | Known datasets | Mean and SD match `numpy` reference to 1e-12 |
| Metadata resolver | Precedence (file < profile < user override), source tagging, missing-parameter errors | All cases pass |
| Path templating | Templates with all tokens, illegal characters, collisions | Valid paths; no overwrite without explicit flag |

### 2.2 Level 2: Integration verification (automated, every PR)

| Area | Approach | Pass criteria |
|---|---|---|
| Readers ↔ data model | **Golden files**: ≥ 2 small real files per format (ND2, LIF, CZI, TIFF, OME-TIFF), including multi-series and multichannel. Expected shape, dtype, voxel size, channel names, and wavelengths are stored as JSON fixtures | Exact match to fixtures |
| Cross-format consistency | Same acquisition exported in several formats (e.g. CZI → OME-TIFF) | Identical pixel data and voxel size; metrics agree to bit level |
| Full pipeline on synthetic field | load → detect → measure → compare → persist → report | All stages succeed; DB rows equal in-memory results; report contains all required sections |
| Database | Record and read back; schema version check; append-only enforcement on every history table (UPDATE and DELETE refused); CSV export and backup round trip | No data loss; audit entries created for every record, exclusion, inclusion, and event |
| Report rendering | HTML and PDF generation from fixture results; snapshot test of the HTML structure; PDF page count and text extraction checks | Required sections present (REP-01, REP-02, REP-06, REP-07) |
| Reproducibility | Run the same file twice | Bit-identical results (NFR-03) |

### 2.3 Level 3: System verification (automated where possible; manual checklist otherwise; per release)

| Area | Approach | Pass criteria |
|---|---|---|
| CLI | Scripted scenarios for each command against a temp workspace | Exit codes, outputs, and logs as specified |
| Watch folder and scheduler | Drop files over time into a watched folder; include duplicates, a corrupt file, and an unknown-profile file; run the scheduler at a compressed interval | Every valid file processed exactly once; the corrupt file is logged and does not halt the run (NFR-06); trend report written on schedule (AUT-02 … AUT-04) |
| Unattended soak test | Built-in scheduler running for 7 days on Windows and Linux test machines | Zero missed runs; no memory growth > 10% |
| Alerts | Inject a session that violates 1₃ₛ | Status file and FAIL status in report (AUT-06, REP-07) |
| GUI | Manual test script (open each format, override metadata, include or exclude beads, generate report, view LJ), plus `pytest-qt` smoke tests | All steps complete; UI stays responsive (UI-04) |
| Cross-platform | CI matrix plus manual install from the released package on Windows, macOS, and Linux | Install and the smoke test succeed (NFR-05) |
| Performance | Benchmark dataset (1024×1024×100, about 50 beads) | ≤ 60 s on the reference hardware (NFR-04) |

### 2.4 Level 4: Validation (with facility staff; M5–M6)

| ID | Activity | Method | Acceptance criteria |
|---|---|---|---|
| VAL-01 | **Method comparison** | Analyze ≥ 30 real bead fields (≥ 3 instruments, ≥ 2 objectives, ≥ 2 wavelengths, all four formats) with PSFWidget and with MetroloJ_QC and/or PSFj. Bland-Altman analysis per FWHM metric | Mean bias ≤ 5%; 95% limits of agreement within ±10% or within the inter-bead SD (NFR-02) |
| VAL-02 | **Known-perturbation sensitivity** | Acquire beads under deliberately degraded conditions: wrong correction-collar setting, wrong immersion medium or RI mismatch, tilted slide, pinhole opened, deliberately undersampled z | Each perturbation produces the expected metric change (e.g. increased axial FWHM and axial asymmetry for RI mismatch, a tilt plane for a tilted slide) and is flagged by spec limits or Westgard rules |
| VAL-03 | **Repeatability and reproducibility** | Same bead slide imaged 10× in one day (repeatability) and by 3 operators over 5 days (reproducibility) | CV of FWHM ≤ 5% laterally and ≤ 7% axially. This establishes the realistic noise floor that sets LJ limits |
| VAL-04 | **Theoretical comparison sanity** | For a well-aligned high-NA objective, measured/theoretical ratios fall in the physically plausible range | Lateral ratio 1.0–1.3; axial ratio 1.0–1.5. Values < 1.0 trigger investigation of metadata or bead-size handling |
| VAL-05 | **Parallel-run field trial** | ≥ 4 weeks of scheduled operation on 1–2 production microscopes alongside the facility's existing QC routine | Every event detected by the existing routine is also detected; false-alarm rate (Westgard rejection with no assignable cause) ≤ 1 per instrument per month, or justified |
| VAL-06 | **Usability** | 3–5 representative users (manager, researcher, engineer) perform scripted tasks; think-aloud protocol and SUS questionnaire | Task completion ≥ 90%; session analysis ≤ 10 min (NFR-08); SUS ≥ 70 |
| VAL-07 | **Report fitness** | Facility manager and one external reader review session and trend reports | Reports are understandable without the tool; status and provenance are clear; sign-off recorded |

---

## 3. Test data strategy

| Dataset | Content | Source | Storage |
|---|---|---|---|
| **SYN-A** | Synthetic Gaussian bead fields; grid of FWHM, SNR, sampling, and offsets | T0.4 generator (seeded) | Generated in CI, not stored |
| **SYN-B** | Synthetic theoretical-PSF bead fields (Gibson-Lanni/vectorial) with tilt, curvature, chromatic shift, and RI mismatch | T0.4 generator | Generated in CI |
| **SYN-C** | Defect fields: doublets, aggregates, saturation, edge beads, hot pixels | T0.4 generator | Generated in CI |
| **GOLD-IO** | Small real files per format, multi-series and multichannel variants | Facility instruments / public samples (license checked) | Not in git. Listed in `tests/data/manifest.json` with SHA-256. Public files (< 100 MB in total) download on demand and are cached in CI; facility files are local-only |
| **REF-1** | Full-size real bead fields for VAL-01, VAL-03, VAL-04 | Facility acquisition per SOP | External storage with SHA-256 manifest |
| **REF-2** | Deliberately perturbed acquisitions for VAL-02 | Facility acquisition | External storage with manifest |
| **LJ-SIM** | Simulated longitudinal series with injected shifts, trends, and increased variance | Hand-built series in `tests/test_control.py` and `tests/test_westgard.py`; full pipeline in `tools/demo_tracking.py` | Generated in CI |

All synthetic generators use fixed seeds. Each external dataset has a manifest (file, hash, instrument, objective, settings, expected outcome).

---

## 4. Requirements traceability matrix (initial)

Legend: **U** = Unit, **I** = Integration, **S** = System, **V** = Validation, **R** = Review or inspection.

| Requirement | Verification method | Test / activity reference |
|---|---|---|
| IMP-01 | I, S | GOLD-IO reader tests; cross-platform install test |
| IMP-02, IMP-03 | I | GOLD-IO multi-series and multichannel fixtures |
| IMP-04, IMP-05, IMP-06 | U, I | Metadata resolver tests; GOLD-IO metadata fixtures |
| IMP-07, IMP-08 | I, S | Time/tile selection tests; large-file lazy-load test (memory cap) |
| IMP-09 | U | Hash tests |
| IMP-10 | U, I | Profile schema validation; profile → session linking |
| DET-01 … DET-06, DET-08 | U | SYN-A/SYN-C detection and filter tests |
| DET-07 | S | GUI test script |
| MET-01, MET-02, MET-03, MET-08, MET-10 | U, V | Fitting Monte Carlo (SYN-A); VAL-01 |
| MET-04 … MET-07 | U | Intensity, background, SBR/SNR tests |
| MET-09 | U, V | SYN-B RI-mismatch; VAL-02 |
| MET-11, MET-12 | U, V | SYN-B tilt and curvature; VAL-02 tilted slide |
| MET-13 | U, V | SYN-B chromatic shift; TetraSpeck REF-1 |
| MET-14 | U, I | Averaged PSF of SYN-B ≈ ground truth (NCC ≥ 0.98); OME-TIFF round-trip |
| MET-15, MET-16 | U | Correction formula tests; Nyquist warning tests |
| MET-17, MET-18 | U | Analytic Airy encircled-energy / OTF cutoff tests |
| THE-01 | U, R | Formula table vs. hand calculation; citation review |
| THE-02 | U | Model FWHM vs. analytic |
| THE-03 … THE-05 | U, V | Ratio, Strehl, and NCC tests; VAL-04 |
| THE-06 | U, S | Spec evaluation tests; alert scenario |
| THE-07 | I, R | Report snapshot; visual review |
| THE-08 | U, I | Mode detection on the NSPARC and Live-SR golden files; ratio tests against the base-modality model |
| THE-09 | U, V | Image-scanning model FWHM vs. published reassignment factors; VAL-04 on NSPARC and Live-SR stacks |
| TRK-01, TRK-02 | I | DB persistence round-trip |
| TRK-03, TRK-08 | I, R | LJ figure snapshot; visual review |
| TRK-04, TRK-05 | U, V | Baseline and Westgard table tests; LJ-SIM; VAL-05 |
| TRK-06, TRK-07 | U, I | Event, re-baseline, and exclusion audit tests |
| TRK-09 | U | LJ-SIM trend detection |
| TRK-10 | I | Export/import round-trip |
| REP-01 … REP-07 | I, R, V | Report generation tests; VAL-07 |
| REP-08 | I | Template override test |
| AUT-01 … AUT-06 | S | CLI scenarios; watch-folder and scheduler soak; alert test |
| UI-01 … UI-04 | S, V | GUI script; pytest-qt smoke; VAL-06 |
| UI-05 | R, I | API docs review; API usage tests |
| NFR-01 | U | Fitting Monte Carlo |
| NFR-02 | V | VAL-01 |
| NFR-03 | I | Reproducibility test |
| NFR-04 | S | Benchmark |
| NFR-05 | S | CI matrix + manual install |
| NFR-06 | S | Corrupt-file scenario |
| NFR-07 | I, R | Audit tests; schema review |
| NFR-08 | V | VAL-06 |
| NFR-09 | R | Coverage report (CI gate ≥ 85%) and docs review |
| NFR-10 | R | License audit (T0.5) + CI license check |

The matrix will be maintained as a machine-readable file (`docs/traceability.csv`). Tests will reference requirement IDs with pytest markers (e.g. `@pytest.mark.req("MET-01")`), so the CI can generate a coverage-by-requirement report automatically.

---

## 5. V&V by milestone

| Milestone | V&V activities | Gate |
|---|---|---|
| M0 | Requirements review (R); synthetic generator self-tests; CI pipeline live | Requirements approved by stakeholders |
| M1 | GOLD-IO integration tests; metadata resolver unit tests | 100% IMP Musts verified |
| M2 | Fitting Monte Carlo (NFR-01); detection and filter tests; formula review | NFR-01 met; DET/MET Musts verified |
| M3 | Westgard and baseline table tests; LJ-SIM; DB integration | TRK Musts verified |
| M4 | Report tests; CLI scenarios; 7-day soak test | REP and AUT Musts verified; soak test passed |
| M5 | GUI script; VAL-01 started; VAL-03; VAL-06 usability | Beta acceptance by facility manager |
| M6 | VAL-01 … VAL-07 complete; full traceability report; V&V summary report | All Musts verified and validated; open anomalies triaged (no open Critical/Major) |

---

## 6. Anomaly management

- Every failed test or validation finding is logged as a GitHub issue with the label `anomaly` and a severity:
  - **Critical:** wrong metric values, data loss, or a silent failure.
  - **Major:** a requirement is not met, with a workaround available.
  - **Minor:** cosmetic or usability issue.
- A release cannot ship with open Critical or Major anomalies unless the facility manager signs a documented risk acceptance.
- Each fix links to a regression test.

---

## 7. Deliverables and records

| Record | When | Owner |
|---|---|---|
| Approved requirements (PLAN.md, versioned) | M0 | Project lead + facility manager |
| Traceability matrix (`docs/traceability.csv`) + auto-generated coverage-by-requirement report | Continuous; frozen per release | Developer |
| CI test reports and coverage | Every PR | CI |
| Fitting accuracy report (bias and precision tables), `docs/validation/fitting_accuracy.md`, generated by `tools/fitting_accuracy.py` | M2, each release | Developer |
| Validation protocols (VAL-01 … VAL-07), written **before** execution | M4 | Project lead |
| Validation reports with raw data manifests | M6 | Project lead + facility staff |
| V&V summary report and release sign-off | M6 / each release | Facility manager |

---

## 8. Change control

- Changes to requirements go through a PR that updates `PLAN.md` and the traceability matrix together.
- Changes to the computation of any **tracked metric** (fitting method, ROI definition, background model) are versioned. Such changes require one of:
  - a re-analysis of the historical data, or
  - an automatic `Event` on affected LJ charts with a recommended re-baseline.

  This keeps trend lines from mixing incompatible values.
- Every release has a semantic version, and that version is stored with every result (TRK-01, REP-06).
