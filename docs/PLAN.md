# PSFWidget: Project Plan

**Status:** Draft v0.1, for review
**Date:** 2026-11-10
**Companion document:** [V&V Plan](VV_PLAN.md)

---

## 1. Purpose and scope

PSFWidget is a tool for microscope quality control based on the point spread function (PSF). It will:

1. **Analyze empirical PSFs** from 3D bead images (fields of sub-resolution beads).
2. **Compare empirical PSFs to theoretical PSFs** computed from the acquisition parameters.
3. **Track changes over time** for each microscope, objective, and channel, using Levey-Jennings (LJ) control charts with Westgard-style rules.
4. **Produce distributable reports** and export them to configured folders, either on demand or on a schedule.

### 1.1 In scope (v1.0)

- Import of 3D (and 3D multichannel) stacks from `.nd2`, `.lif`, `.czi`, and `.tif`/`.ome.tif`.
- Automated bead detection, quality filtering, and per-bead metrics.
- Theoretical PSF generation for widefield, point-scanning confocal, and spinning-disk confocal modalities.
- A persistent results database with LJ charts and rule-based flags.
- HTML and PDF reports, plus CSV and XLSX exports.
- Scheduled, unattended processing of a watch folder.
- Three interfaces: a Python API, a command-line interface (CLI), and a napari dock widget (GUI).

### 1.2 Out of scope (v1.0, candidates for later)

- Light-sheet, SIM, and STED PSF models. (Image-scanning modes such as NSPARC and Live-SR are in scope; see THE-08 and THE-09.)
- Email or Slack delivery of reports. v1.0 writes to folders only, so existing sharing tools handle distribution.
- A multi-user web server or dashboard.
- Deconvolution itself. v1.0 only exports averaged PSFs for use in deconvolution software.

### 1.3 Proposed technology baseline (to be confirmed)

| Concern | Proposed choice | Rationale |
|---|---|---|
| Language | Python ≥ 3.12 | Standard for bio-image analysis; napari ecosystem. The current BSD reader libraries (`czifile`, `liffile`, `tifffile`) require 3.12 or newer |
| File I/O | Direct BSD-licensed readers: `nd2`, `liffile`, `czifile`, `tifffile` | `bioio-czi` and `readlif` (used by `bioio-lif`) are GPL-3, which conflicts with NFR-10. The BSD readers were checked against the facility CZI and LIF files (see §7) |
| Numerics | `numpy`, `scipy` | Levenberg-Marquardt fits with analytic Jacobians give the fit uncertainties; `scikit-image` and `lmfit` turned out not to be needed |
| Theoretical PSF | Models implemented in PSFWidget (analytical formulas, then scalar Gibson-Lanni, then vectorial), cross-checked against `MicroscPSF-Py` (MIT) | `psfmodels` is GPL-3 (see `docs/LICENSE_AUDIT.md`) |
| Storage | SQLite through Python's built-in `sqlite3` | Single file, portable, no server needed. *Changed in M3* from SQLAlchemy + Alembic: the schema is small, a version table covers migrations, and two dependencies are avoided. Append-only rules are enforced by SQLite triggers |
| Plotting | `matplotlib` for static report figures (optional `report` extra); `plotly` for interactive HTML | Static figures for PDF, interactive charts for HTML |
| Reports | Jinja2 HTML templates → PDF with `weasyprint` | One template produces both outputs |
| Scheduling | Built-in scheduler (`APScheduler`) **and** a CLI that OS schedulers can call (cron, Windows Task Scheduler) | Works on facility PCs without a service running |
| GUI | napari plugin (npe2) | Fits the repository name and gives a 3D viewer for free |
| Packaging/CI | `pyproject.toml`, `pytest`, GitHub Actions, `pre-commit` (ruff, mypy) | |

---

## 2. Stakeholders and users

| Role | Needs |
|---|---|
| **Core facility manager** | Routine QC, trend tracking, early warning of degradation, distributable records for audits and user meetings |
| **Microscope user or researcher** | Quick check that the system performs as expected before an experiment; an empirical PSF for deconvolution |
| **Service engineer** | Before and after comparison around service visits; field-dependent metrics (tilt, curvature, chromatic shift) |
| **Administrator** | Unattended scheduled runs, predictable output locations, low maintenance |

---

## 3. Requirements

Requirement IDs are stable and are referenced by the V&V traceability matrix. Priority uses MoSCoW: **M** = Must, **S** = Should, **C** = Could.

### 3.1 Data import (IMP)

| ID | Requirement | Pri |
|---|---|---|
| IMP-01 | The system shall import 3D image stacks from Nikon `.nd2`, Leica `.lif`, Zeiss `.czi`, and `.tif`/`.ome.tif` files. | M |
| IMP-02 | The system shall support multi-series containers (multiple positions or series in `.lif`/`.nd2`/`.czi`) and let the user select one, several, or all series. | M |
| IMP-03 | The system shall support multichannel stacks and analyze each channel independently. | M |
| IMP-04 | The system shall extract voxel size (x, y, z) from file metadata, plus objective NA, immersion refractive index, emission and excitation wavelength, pinhole size, and modality where available. | M |
| IMP-05 | The system shall allow any metadata value to be entered or overridden manually, and shall record whether each value came from the file or from the user. | M |
| IMP-06 | The system shall refuse to analyze a stack that lacks a required parameter (voxel size, NA, wavelength, RI) and shall report which parameter is missing. | M |
| IMP-07 | The system shall support time-lapse or mosaic data by letting the user select timepoint or tile indices. | S |
| IMP-08 | The system shall handle stacks larger than RAM by lazy or chunked loading (e.g. dask). | S |
| IMP-09 | The system shall record a SHA-256 hash of each source file for provenance. | S |
| IMP-10 | The system shall support instrument profiles (microscope, objective, channel presets) that supply default metadata, including values no file records such as bead diameter and the bead dye's emission peak, and link results to a tracked configuration. | M |

### 3.2 Bead detection and selection (DET)

| ID | Requirement | Pri |
|---|---|---|
| DET-01 | The system shall automatically detect candidate beads in a 3D field (e.g. by LoG or DoG filtering followed by local maxima detection). | M |
| DET-02 | The system shall reject beads that are too close to the image border in xy or z for the configured crop size. | M |
| DET-03 | The system shall reject beads whose crop region overlaps another bead (minimum separation configurable, default ≥ 4× expected lateral FWHM laterally and ≥ 2× axial FWHM). | M |
| DET-04 | The system shall reject saturated beads (any voxel at the bit-depth maximum or at a configured threshold). | M |
| DET-05 | The system shall reject beads with SNR or SBR below configured thresholds. | M |
| DET-06 | The system shall reject outliers (e.g. aggregates or doublets) by integrated-intensity and FWHM outlier tests (median ± k·MAD). | S |
| DET-07 | The system shall let the user manually include or exclude beads in the GUI. | S |
| DET-08 | The system shall report the number of detected, rejected (with reason), and accepted beads. | M |

### 3.3 PSF metrics (MET)

All metrics are computed per bead and summarized per field as n, mean, SD, median, and IQR.

| ID | Requirement | Pri |
|---|---|---|
| MET-01 | **FWHM X, Y, Z** from 1D Gaussian fits (with offset) to line profiles through the sub-pixel bead centroid. | M |
| MET-02 | **FWHM in the XY, XZ, and YZ planes** from 2D Gaussian fits to the central orthogonal slices (or max projections, configurable). Reports major and minor axis FWHM and orientation angle. | M |
| MET-03 | **3D Gaussian fit** reporting FWHM x/y/z and the sub-pixel centroid. | S |
| MET-04 | **Integrated intensity (integrated density)**: background-subtracted sum within a defined volume (ellipsoid scaled to k × FWHM, or a fixed box), reported in raw counts and normalized to exposure or gain where metadata permits. | M |
| MET-05 | **Peak intensity** (fit amplitude and maximum voxel). | M |
| MET-06 | **Background** level and noise (local shell or annulus; mean, median, SD). | M |
| MET-07 | **Signal-to-background ratio (SBR)** and **signal-to-noise ratio (SNR)**. | M |
| MET-08 | **Lateral asymmetry / ellipticity**: FWHM ratio (minor/major), plus XY orientation angle. | M |
| MET-09 | **Axial asymmetry**: skewness of the axial profile, and the ratio of intensity above vs. below focus (indicator of spherical aberration or RI mismatch). | S |
| MET-10 | **Fit quality**: R², reduced χ², and parameter standard errors; beads below a fit-quality threshold are flagged. | M |
| MET-11 | **Field-dependent maps**: FWHM, intensity, and z-centroid vs. (x, y) position across the field, with a fitted plane for **stage or sample tilt** and a quadratic surface for **field curvature**. | S |
| MET-12 | **Field illumination uniformity**: integrated intensity vs. position (center-to-edge ratio). | S |
| MET-13 | **Chromatic registration** for multichannel bead data (e.g. TetraSpeck): lateral and axial centroid shifts between channels, in nm. | S |
| MET-14 | **Averaged empirical PSF**: sub-pixel registration and averaging of accepted beads; export as OME-TIFF with metadata, suitable for deconvolution. | S |
| MET-15 | **Bead size correction**: optional correction of FWHM for finite bead diameter (quadrature subtraction or model-based), with the correction method recorded. *Implemented:* the bead is replaced by the Gaussian of equal variance (FWHM 0.527 × diameter) and subtracted in quadrature; on by default when the diameter is known. | S |
| MET-16 | **Sampling check**: warn when voxel size exceeds Nyquist for the theoretical PSF. | M |
| MET-17 | **Encircled or ensquared energy** (fraction of energy within a radius of 1× and 2× the theoretical FWHM). | C |
| MET-18 | **MTF / OTF estimate** from the averaged PSF (lateral cutoff frequency vs. theoretical). | C |

### 3.4 Theoretical PSF comparison (THE)

| ID | Requirement | Pri |
|---|---|---|
| THE-01 | The system shall compute theoretical lateral and axial FWHM from analytical approximations for **widefield**, **point-scanning confocal**, and **spinning-disk confocal** modalities, with the formula used shown in the report. The confocal models depend on pinhole size: set per channel for point scanning, and fixed by the disk (physical pinhole diameter and magnification to the disk, from the instrument profile) for spinning disk. | M |
| THE-02 | The system shall generate a theoretical 3D PSF at the acquisition voxel size with a scalar Gibson-Lanni model and/or a vectorial (Richards-Wolf) model, including immersion/sample RI mismatch and coverslip parameters. | S |
| THE-03 | The system shall report **measured/theoretical ratios** for every FWHM metric. | M |
| THE-04 | The system shall report a **Strehl ratio estimate** (normalized peak of the averaged empirical PSF relative to the theoretical PSF of equal integrated energy). | S |
| THE-05 | The system shall report a **similarity score** between the averaged empirical PSF and the theoretical PSF (e.g. normalized cross-correlation or Pearson r after alignment). | S |
| THE-06 | The system shall support user-defined **specification limits** (e.g. lateral ratio ≤ 1.2, axial ratio ≤ 1.3) and report pass/fail. | M |
| THE-07 | The system shall show side-by-side XY, XZ, and YZ views with overlaid profiles of the empirical vs. theoretical PSF. | M |
| THE-08 | For configurations that use a resolution-enhancing mode (e.g. Nikon NSPARC SR, Live-SR), the system shall keep the theoretical comparison: it shall report measured/theoretical ratios against the model of the base modality (point-scanning or spinning-disk confocal) and label the result with the enhancement mode. | M |
| THE-09 | For resolution-enhancing modes based on image scanning (pixel reassignment), the system shall also compute a theoretical PSF from an image-scanning-microscopy model and report ratios against it. | S |

### 3.5 Tracking and statistical process control (TRK)

| ID | Requirement | Pri |
|---|---|---|
| TRK-01 | The system shall persist all per-bead and per-field results, the analysis parameters, the software version, and provenance in a local database. | M |
| TRK-02 | The system shall key results by **instrument → objective → channel (or wavelength) → modality**, with the acquisition date taken from the file's metadata (falling back to the file-system date only when the metadata has none; dates in file names are never used). | M |
| TRK-03 | The system shall plot **Levey-Jennings charts** for any tracked metric: session values over time with the mean and ±1, ±2, ±3 SD lines. | M |
| TRK-04 | The system shall establish control limits from a user-selected **baseline period** (default: first N = 20 valid sessions; configurable) or from user-entered target mean and SD. | M |
| TRK-05 | The system shall evaluate **Westgard rules** (1₂ₛ warning; 1₃ₛ, 2₂ₛ, R₄ₛ, 4₁ₛ, 10ₓ rejection) per metric, with configurable rule sets per metric. | M |
| TRK-06 | The system shall let the user **re-baseline** after documented events (service, objective replacement, laser change). Events shall be annotated on charts and preserved in history. | M |
| TRK-07 | The system shall let the user exclude a session from statistics with a recorded reason, without deleting it. | M |
| TRK-08 | The system shall show specification limits (THE-06) together with statistical limits on the LJ chart. | S |
| TRK-09 | The system shall provide a trend summary (e.g. slope over the last N sessions) to detect gradual drift. | C |
| TRK-10 | The system shall support database backup and export or import, to move results between machines. | S |

**Default tracked metrics:** FWHM X, Y, Z; XY/XZ/YZ major and minor FWHM; measured/theoretical ratios; integrated intensity (normalized); SBR; ellipticity; number of accepted beads; tilt; chromatic shift (if multichannel).

### 3.6 Reporting and export (REP)

| ID | Requirement | Pri |
|---|---|---|
| REP-01 | The system shall generate a **session report** (one acquisition): metadata, bead overview (MIP with accepted and rejected beads marked), metric summary tables, per-bead tables, orthogonal views, profile fits, theoretical comparison, field maps, and pass/fail status. | M |
| REP-02 | The system shall generate a **trend report** (one instrument or configuration over a date range): LJ charts, rule violations, events, and a summary table. | M |
| REP-03 | Reports shall be available as **HTML** (self-contained, one file) and **PDF**. | M |
| REP-04 | Raw results shall be exportable as **CSV** and **XLSX**, and the averaged PSF as **OME-TIFF**. | M |
| REP-05 | Output shall follow a configurable **folder and filename template** (e.g. `{root}/{instrument}/{objective}/{YYYY}/{YYYY-MM-DD}_{channel}_report.pdf`). | M |
| REP-06 | Each report shall include provenance: software version, analysis parameters, source file name and hash, date of analysis, and operator. | M |
| REP-07 | Reports shall carry a clear overall status (PASS / WARNING / FAIL) derived from the spec limits and Westgard rules. | M |
| REP-08 | Users or facilities shall be able to customize report branding (logo, facility name, footer) through templates. | C |

### 3.7 Automation and scheduling (AUT)

| ID | Requirement | Pri |
|---|---|---|
| AUT-01 | The system shall provide a **CLI** to run analysis, update the database, and generate reports headlessly. | M |
| AUT-02 | The system shall support **watch-folder ingestion**: process new files in configured folders, assign them to an instrument profile by folder or filename pattern, and skip files already processed (by hash). | M |
| AUT-03 | The system shall support **scheduled jobs** (daily, weekly, monthly, or cron expression) for ingestion and for trend-report generation, through the built-in scheduler or OS schedulers calling the CLI. | M |
| AUT-04 | The system shall log every scheduled run (start, end, files processed, errors) and write a run summary file. Failures shall not stop the processing of other files. | M |
| AUT-05 | The system shall take all automation settings from a version-controllable **config file** (YAML or TOML), validated on load with clear error messages. | M |
| AUT-06 | The system shall write a flag or status file (e.g. `ALERT.txt` or a JSON status) to the output folder when a run produces FAIL or Westgard rejection results, so downstream tools can notify staff. | S |

### 3.8 User interfaces (UI)

| ID | Requirement | Pri |
|---|---|---|
| UI-01 | The system shall provide a **napari dock widget** to load a file, set or override metadata, run detection, review accepted and rejected beads in 3D, view metrics, and generate a report. | M |
| UI-02 | The GUI shall provide a **tracking view** to browse instruments and configurations and display LJ charts interactively. | S |
| UI-03 | The GUI shall provide an editor for instrument profiles and spec limits. | S |
| UI-04 | Long-running operations shall show progress and shall not freeze the UI (worker threads). | M |
| UI-05 | A documented **Python API** shall expose all core functions. | M |

### 3.9 Non-functional requirements (NFR)

| ID | Requirement | Pri |
|---|---|---|
| NFR-01 | **Accuracy:** on synthetic Gaussian or theoretical PSFs sampled at or above Nyquist with SNR ≥ 20, the per-bead FWHM bias shall be ≤ 1%, and the error of the median FWHM of a field of 10 beads shall be ≤ 3% (mean) and ≤ 5% (95th percentile). *Amended 2026-11-13:* the original target applied the 3%/5% limits to single beads, which photon noise rules out at SNR 20; the field median is the value that is tracked. Per-bead precision is published in `docs/validation/fitting_accuracy.md`. | M |
| NFR-02 | **Agreement:** on reference bead datasets, FWHM results shall agree with an established tool (MetroloJ_QC and/or PSFj) within 5% or within the inter-bead SD, whichever is larger. | M |
| NFR-03 | **Reproducibility:** re-analysis of the same file with the same parameters and version shall give bit-identical results. | M |
| NFR-04 | **Performance:** a 1024×1024×100 single-channel field with about 50 beads shall be analyzed in under 60 s on a 4-core, 16 GB workstation (excluding the vectorial PSF model). | S |
| NFR-05 | **Platforms:** Windows 10/11, macOS (latest two versions), and Linux. | M |
| NFR-06 | **Robustness:** corrupt or unsupported files produce a clear error and do not crash batch runs. | M |
| NFR-07 | **Data integrity:** the database is never silently modified. Exclusions and re-baselines are append-only and audited, with user and time recorded. | M |
| NFR-08 | **Usability:** a new user can complete a session analysis from the GUI in 10 minutes or less using the quick-start guide. | S |
| NFR-09 | **Maintainability:** ≥ 85% line coverage on the core package, typed public API, and documented modules. | S |
| NFR-10 | **Licensing:** all dependencies are compatible with distribution under the repository's MIT license. GPL dependencies are flagged before adoption. | M |

---

## 4. Architecture overview

```
psfwidget/
├── io/            # Format readers → unified ImageStack(data, voxel_size, channels, metadata, provenance)
├── profiles/      # Instrument / objective / channel profiles, spec limits (YAML)
├── detect/        # Bead detection, filtering, cropping
├── metrics/       # Fitting (1D/2D/3D Gaussian), intensity, SBR/SNR, asymmetry, field maps, chromatic
├── theory/        # Analytical FWHM formulas, Gibson-Lanni / vectorial PSF wrappers, comparison
├── average/       # Sub-pixel registration + averaged PSF export
├── tracking/      # SQLAlchemy models, baselines, Westgard engine, events
├── report/        # Jinja2 templates, figure generation, HTML/PDF/CSV/XLSX exporters, path templating
├── automation/    # Watch-folder ingestion, scheduler, run logs, CLI (typer/click)
├── napari/        # Dock widgets (analysis, tracking, profiles) – thin layer over the core
└── api.py         # Public, stable Python API
```

**Design principles**

- **Headless core.** All logic lives in the core packages. The GUI and CLI are thin layers, which makes scheduling and testing straightforward.
- **One pipeline.** `load → resolve metadata → detect → measure → compare → persist → report`. Each stage has typed inputs and outputs (dataclasses or pydantic models) and can be unit-tested on its own.
- **Configuration as data.** Analysis parameters are a versioned, serializable object stored with every result, which gives NFR-03 reproducibility.
- **Database schema** (initial): `Instrument`, `Objective`, `ChannelConfig`, `Session` (one acquisition), `Field` (series or position), `Bead` (per-bead metrics), `SessionSummary` (aggregated metrics), `Baseline`, `Event`, `Exclusion`, `RuleViolation`, `Run` (automation log). Migrations use Alembic.

---

## 5. Work breakdown (tasks)

Task IDs map to requirements (in brackets) for traceability.

### WP0: Project foundation
- T0.1 Repository scaffolding: `pyproject.toml`, src layout, ruff, mypy, pre-commit, and a GitHub Actions matrix (Windows, macOS, Linux; Python 3.12–3.14).
- T0.2 Contributor docs, coding standards, branching and release policy.
- T0.3 Collect a **reference dataset** of real bead stacks in each format from facility instruments, with known acquisition parameters. Small files are listed in `tests/data/manifest.json` and kept out of git; full-size files stay in external storage with checksums. **Status (2026-11-12):** bead files received for all four formats: CZI (LSM 980), LIF (SP8), ND2 (AX R NSPARC and CSU spinning disk), and an OME-TIFF export of the spinning-disk stack. Public OME samples cover the remaining ND2 reader cases. A plain ImageJ TIFF of the same stack covers the missing-metadata case. All facility stacks use the same 100 nm TetraSpeck slide. Still missing: a multi-position ND2.
- T0.4 Synthetic data generator: Gaussian and theoretical PSF bead fields with known ground truth, noise (Poisson and Gaussian read noise), background, tilt, and saturation. *(Serves NFR-01 and many V&V tests.)* **Done (2026-11-13)** for Gaussian fields and defects (`psfwidget.synthetic`); theoretical-PSF fields follow with WP3.
- T0.5 License audit of candidate dependencies [NFR-10]. **Done (2026-11-13):** `docs/LICENSE_AUDIT.md`, with `tools/check_licenses.py` enforcing it in CI.

### WP1: Data import
- T1.1 `ImageStack` data model and `MetadataResolver` (file → profile → user override precedence, with source tagging) [IMP-04, IMP-05, IMP-06].
- T1.2 Readers for ND2, LIF, CZI, and TIFF/OME-TIFF using `nd2`, `liffile`, `czifile`, and `tifffile` [IMP-01].
- T1.3 Multi-series, multichannel, time, and tile selection [IMP-02, IMP-03, IMP-07].
- T1.4 Lazy or chunked loading [IMP-08]. **Partly done:** pixel data is read one channel at a time, on request. A single channel larger than memory is not yet chunked.
- T1.5 File hashing and provenance [IMP-09].
- T1.6 Instrument profile schema and loader [IMP-10].

### WP2: Bead detection and metrics
- T2.1 Candidate detection (LoG/DoG + peak finding, sub-pixel centroid) [DET-01]. **Done:** matched Gaussian filter after an in-plane median filter for hot pixels; sub-voxel centres from the fits.
- T2.2 Filters: border, overlap, saturation, SNR, outliers; rejection reasons [DET-02 … DET-06, DET-08]. **Done**, plus inference of the saturation level from a clipping plateau when the file does not state its bit depth.
- T2.3 1D Gaussian profile fitting X/Y/Z [MET-01, MET-10]. **Done** (3×3 line bundles; R², reduced χ², and FWHM standard error).
- T2.4 2D Gaussian fits for XY/XZ/YZ planes (rotated elliptical) [MET-02, MET-08]. **Done.**
- T2.5 3D Gaussian fit [MET-03].
- T2.6 Intensity metrics: integrated density, peak, background, SBR/SNR [MET-04 … MET-07]. **Done**, except normalisation to exposure or gain (MET-04), which needs exposure metadata the readers do not extract yet.
- T2.7 Axial asymmetry metrics [MET-09].
- T2.8 Field maps, tilt and curvature surface fits, illumination uniformity [MET-11, MET-12].
- T2.9 Chromatic registration [MET-13].
- T2.10 Bead size correction and Nyquist check [MET-15, MET-16]. **Done.**
- T2.11 Averaged PSF (registration, averaging, OME-TIFF export) [MET-14].
- T2.12 Encircled energy and MTF (stretch) [MET-17, MET-18].

### WP3: Theoretical PSF and comparison
- T3.1 Analytical FWHM formulas (widefield and confocal, with pinhole in Airy units) with citations [THE-01]. **Done**, together with a numerical scalar model (any pinhole size) that the ratios use; see `psfwidget/theory/`.
- T3.2 Gibson-Lanni and vectorial PSF models at the acquisition sampling, implemented in PSFWidget [THE-02].
- T3.3 Ratios, Strehl estimate, similarity score [THE-03 … THE-05].
- T3.4 Spec limit configuration and pass/fail evaluation [THE-06]. **Done** (`[spec]` table in instrument profiles).
- T3.5 Comparison figures [THE-07].
- T3.6 Resolution-enhanced modes: detect the mode from metadata or the profile, compare against the base-modality model, and add an image-scanning-microscopy model [THE-08, THE-09].

### WP4: Tracking database and SPC
- T4.1 Database schema and version check [TRK-01, TRK-02]. **Done** with `sqlite3`; migrations will be added when the schema first changes.
- T4.2 Persistence of sessions, fields, beads, and summaries, with parameters and versions [TRK-01]. **Done.**
- T4.3 Baseline computation (first N sessions, date range, manual target) [TRK-04]. **Done.** Fewer than 5 valid sessions give no limits; fewer than N give provisional limits.
- T4.4 Westgard rule engine (pure functions, configurable per metric) [TRK-05]. **Done.**
- T4.5 Events, re-baselining, exclusions, and audit trail [TRK-06, TRK-07, NFR-07]. **Done.**
- T4.6 Levey-Jennings plotting (static and interactive), with spec-limit overlay and event markers [TRK-03, TRK-08]. **Static charts done** (PNG, SVG, PDF); interactive charts come with the HTML reports in WP5.
- T4.7 Drift and trend summary [TRK-09]. **Done** (least-squares slope over the last 10 sessions of the current baseline).
- T4.8 Backup and export/import [TRK-10]. **Backup and CSV export done**; merging another database (import) is not implemented.

### WP5: Reporting and export
- T5.1 Figure library (bead overview, orthogonal views, profile fits, field maps, LJ) [REP-01, REP-02].
- T5.2 Jinja2 templates for session and trend reports; HTML (self-contained) and PDF output [REP-01 … REP-03].
- T5.3 CSV/XLSX/OME-TIFF exporters [REP-04].
- T5.4 Output path templating and collision handling [REP-05].
- T5.5 Provenance block and status logic [REP-06, REP-07].
- T5.6 Branding and template override [REP-08].

### WP6: Automation
- T6.1 CLI commands: `analyze`, `ingest`, `report session|trend`, `baseline`, `event add`, `db export|import` [AUT-01]. **`info` and `analyze` done.**
- T6.2 Config file schema and validation [AUT-05].
- T6.3 Watch-folder ingestion with deduplication and profile matching [AUT-02].
- T6.4 Scheduler (built-in), plus documented cron and Windows Task Scheduler recipes [AUT-03].
- T6.5 Run logging, error isolation, alert or status files [AUT-04, AUT-06].

### WP7: napari GUI
- T7.1 Analysis widget: load, metadata panel, detect, review beads (points layer), metrics table, report button [UI-01, DET-07, UI-04].
- T7.2 Tracking widget with interactive LJ charts [UI-02].
- T7.3 Profile and spec editor [UI-03].
- T7.4 npe2 manifest, sample data contribution.

### WP8: Documentation and release
- T8.1 User guide: acquisition SOP for bead slides (bead size, mounting, sampling, exposure without saturation), quick-start, and metric definitions.
- T8.2 API reference (mkdocs or sphinx).
- T8.3 Packaging: PyPI and napari hub, with an optional standalone installer (e.g. conda constructor).
- T8.4 Release notes and V&V summary report.

---

## 6. Milestones

Durations are indicative estimates for roughly one developer, with facility staff available for validation. They will be refined after M1.

| Milestone | Content | Exit criteria | Est. end |
|---|---|---|---|
| **M0: Foundation** | WP0 | CI green on 3 OSes; synthetic generator produces ground-truth fields; ≥ 1 reference stack per format collected; requirements baselined (this document approved) | Week 2 |
| **M1: Import** | WP1 | All four formats load with correct voxel size and metadata on the reference set; I/O verification tests pass | Week 5 |
| **M2: Core metrics (MVP analysis)** | WP2 Musts + T3.1, T3.3 (ratios), T3.4 | FWHM X/Y/Z and XY/XZ/YZ, integrated intensity, SBR/SNR, and ratios meet NFR-01 on synthetic data; CLI `analyze` writes CSV | Week 10 |
| **M3: Tracking and LJ** | WP4 | Sessions persist; baselines and Westgard rules pass rule-engine tests; LJ charts render with events | Week 13 |
| **M4: Reporting and automation (alpha)** | WP5, WP6 | Session and trend reports in HTML and PDF; scheduled watch-folder run works end to end unattended for 1 week on a test machine | Week 17 |
| **M5: GUI and Shoulds (beta)** | WP7, remaining Shoulds in WP2 and WP3 (field maps, chromatic, averaged PSF, vectorial model, Strehl, image-scanning model) | GUI usability session completed; beta deployed on 1–2 facility microscopes | Week 22 |
| **M6: Validation and v1.0 release** | WP8, V&V execution | All Must requirements verified; NFR-02 agreement study passed; ≥ 4 weeks of parallel run against the current QC method; V&V summary report signed off | Week 28 |

**Progress (2026-11-13):**

- **M0 is complete** apart from one data gap: no multi-position ND2 file yet.
- **M1 (import) is complete.** All four formats load with correct voxel size and metadata on the reference set; CI passes on Windows, macOS, and Linux.
- **M2 (core metrics) is implemented.** `psfwidget analyze` detects beads, measures FWHM in X/Y/Z and the XY/XZ/YZ planes, integrated intensity, SBR, SNR, and ellipticity, compares with theory, checks spec limits, and writes CSV and JSON. NFR-01 (as amended) is met on synthetic data, and NFR-04 is met (31 s for a 1024 × 1024 × 100 field with 50 beads on 4 cores). Open within M2's scope: exposure normalisation of intensity (MET-04), the 3D Gaussian fit (MET-03, a Should), and pixel-integration bias for camera data (about +4% at the Nyquist limit, documented in the accuracy report).
- **M3 (tracking) is implemented.** `psfwidget analyze --db` records results in an append-only SQLite database filed by instrument, objective, channel, and modality. `psfwidget track` reports each configuration's latest session against Westgard rules, draws Levey-Jennings charts with events and spec limits, records events (optionally resetting the baseline), excludes and re-includes sessions with a reason, exports CSV, backs up the database, and prints the audit log. Not done: merging databases (TRK-10 import) and interactive charts (WP5).
- **Early finding on facility data:** the SP8 beads are about three times longer axially than theory, with a secondary lobe below focus, consistent with spherical aberration (for example a refractive-index or coverslip mismatch).

**Critical path:** reference data (T0.3) → import (WP1) → metrics (WP2) → tracking (WP4) → automation (WP6) → validation (M6). LJ charts become meaningful only after about 20 sessions, so **baseline data collection should begin at M2** with the CLI, before the GUI exists.

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Incomplete or vendor-specific metadata (NA, RI, wavelength missing in TIFF or some CZI/LIF) | Wrong theoretical comparison | Instrument profiles + mandatory-parameter check (IMP-06); source tagging in report |
| Reader library gaps or format changes (e.g. new ND2/CZI versions) | Import failures | Thin reader layer over one library per format, so a library can be swapped; per-format regression files in CI |
| Poor bead samples (aggregates, saturation, undersampling) | Noisy trends, false alarms | Strict filters, sampling warnings, acquisition SOP (T8.1) |
| Day-to-day variance from sample prep rather than the instrument | LJ false positives | Use a fixed, long-lived bead slide; track the accepted-bead count and SBR as covariates; median-based summaries |
| Integrated intensity depends on laser power, exposure, and gain | Non-comparable intensity trends | Normalize where metadata allows; require fixed acquisition settings per profile; flag setting changes |
| Vectorial PSF computation is slow | Performance | Cache theoretical PSFs per configuration; analytical fallback |
| GPL-licensed dependency adopted unknowingly | Licensing conflict | T0.5 audit; CI license check. **Found 2026-11-11:** `bioio-czi` 4.1.0 (GPL-3, depends on `aicspylibczi`, GPL-3) and `readlif` 0.6.6 (GPL-3). Replaced in §1.3 by `czifile` and `liffile` (BSD-3) |
| Test files carry personal data in embedded metadata (account names, serial numbers) | Publishing personal data in a public repository | Golden files stay out of git; inspect metadata before publishing any file (`tests/data/README.md`) |
| Real PSF much wider than theory (aberration), so a box sized from theory truncates the bead | Biased FWHM | The measurement box is re-sized from the measured median FWHM (up to 4× theory) and the channel re-measured; noted in the results |
| Vendor metadata quirks (Leica stores a negative z step; Leica sequential scans store one setting block per sequence plus stale blocks, each listing all four detector slots, so channel wavelengths must come from the active detector and laser of the matching `Seq.` block. Nikon AX files report the lower edge of the detection band as the emission wavelength and record no pinhole with the NSPARC detector. A Nikon spinning-disk file gave the wrong excitation and no emission for one channel in the structured metadata while the free-text description was right. The NIS-Elements OME-TIFF export rounds pixel size to six significant figures) | Wrong voxel size or flipped stacks | Reader normalizes sign and records it; golden-file expectations cover each quirk found |
| Scheduled jobs fail silently on facility PCs | Missed QC | Run logs, status files, "last successful run" shown in the trend report |

---

## 8. Open questions for stakeholders

1. **Modalities:** *Partly answered 2026-11-11:* point-scanning and spinning-disk confocal are in scope (the facility is providing bead stacks from both). Still open: are multiphoton or light-sheet systems also needed?
   *Answered 2026-11-12:* resolution-enhanced modes (NSPARC SR, Live-SR) keep a theoretical comparison (THE-08, THE-09).
2. **Beads:** What bead types and sizes are in use (e.g. 100 nm or 175 nm PS-Speck, TetraSpeck)? This affects bead size correction and chromatic metrics.
3. **Instruments:** Which instruments and objectives should be onboarded first? Can sample files be provided for T0.3?
4. **Distribution:** Is writing to network or shared folders enough for v1.0, or is email or Teams/Slack delivery required?
5. **Regulatory context:** Is this for internal facility QC, or must it support an ISO 9001, GLP, or clinical-style quality system? This sets the rigor of the audit trail and V&V sign-off.
6. **Spec limits:** Should default acceptance limits be the facility's own, or taken from published guidance (e.g. QUAREP-LiMi recommendations)?
7. **Deployment:** Will the tool run on each acquisition PC, a central analysis server, or both? This affects whether the database is shared.
8. **Spelling:** The chart is formally the *Levey-Jennings* chart. Confirm this naming for the UI and reports.

---

## 9. References (for implementation)

- Cole, R. W., Jinadasa, T., & Brown, C. M. (2011). Measuring and interpreting point spread functions to determine confocal microscope resolution and ensure quality control. *Nature Protocols*, 6, 1929–1941.
- Gibson, S. F., & Lanni, F. (1992). Experimental test of an analytical model of aberration in an oil-immersion objective lens used in three-dimensional light microscopy. *JOSA A*, 9(1), 154–166.
- Theer, P., Mongis, C., & Knop, M. (2014). PSFj: know your fluorescence microscope. *Nature Methods*, 11, 981–982.
- Faklaris, O., et al. (2022). Quality assessment in light microscopy for routine use through simple tools and robust metrics. *J. Cell Biol.*, 221(11). (MetroloJ_QC)
- QUAREP-LiMi working group publications on microscope QC.
- Westgard, J. O., et al. (1981). A multi-rule Shewhart chart for quality control in clinical chemistry. *Clin. Chem.*, 27(3), 493–501.
- Levey, S., & Jennings, E. R. (1950). The use of control charts in the clinical laboratory. *Am. J. Clin. Pathol.*, 20(11), 1059–1066.
