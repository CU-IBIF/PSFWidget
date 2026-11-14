# PSFWidget User Manual

**Draft** for version 0.0.1.dev0 (pre-release), 2026-11-14.
Companion: [Install guide](INSTALL.md). What is done and outstanding: [STATUS.md](../STATUS.md).

> This is a draft written before validation. Screens and outputs may change
> before v1.0, and the results have not yet been compared with an
> established tool (VAL-01). Please report anything unclear or wrong.

## Contents

1. [Introduction](#1-introduction)
2. [Preparing and acquiring bead images](#2-preparing-and-acquiring-bead-images)
3. [The napari GUI](#3-the-napari-gui)
4. [Instrument profiles](#4-instrument-profiles)
5. [The command line](#5-the-command-line)
6. [Understanding the results](#6-understanding-the-results)
7. [Tracking over time](#7-tracking-over-time)
8. [Reports and exports](#8-reports-and-exports)
9. [Unattended runs](#9-unattended-runs)
10. [Records, review, and approval (GLP)](#10-records-review-and-approval-glp)
11. [Python API](#11-python-api)
12. [Troubleshooting](#12-troubleshooting)
13. [Reference](#13-reference)

---

## 1. Introduction

PSFWidget measures the point spread function (PSF) of a microscope from 3D
images of sub-resolution fluorescent beads, compares it with the theoretical
PSF, and tracks it over time so that changes are noticed early.

### 1.1 The workflow

1. **Acquire** a z-stack of a bead slide with the instrument's QC settings.
2. **Analyse** it: PSFWidget reads the file and its metadata, finds the
   beads, measures each one, rejects unsuitable beads with a reason, and
   compares the accepted beads with theory and with the facility's
   acceptance limits.
3. **Record** the result in the instrument's results database.
4. **Review** the Levey-Jennings charts: each session is checked against
   control limits learned from earlier sessions (Westgard rules).
5. **Report**: a session report for the acquisition and periodic trend
   reports, which can be reviewed and signed.

Steps 2 to 5 can run unattended on a schedule (chapter 9).

### 1.2 Words used in this manual

| Term | Meaning |
|---|---|
| **Session** | One analysed acquisition (one file, series, timepoint, and tile). |
| **Configuration** | What results are tracked under: instrument / objective / channel / modality, e.g. `sp8 / 63x-oil-1.40 / green / confocal`. |
| **Channel key** | The channel part of a configuration: the profile's channel id if one matched, else the channel name, else its wavelengths. |
| **Profile** | A TOML file with an instrument's fixed facts and settings (chapter 4). |
| **Theory** | The theoretical PSF for the acquisition's objective, wavelengths, modality, and pinhole. |
| **Ratio** | Measured FWHM divided by the theoretical FWHM; 1.0 is diffraction-limited. |
| **Spec limits** | The facility's acceptance limits in the profile (PASS / FAIL). |
| **Control limits** | Mean ± 2 and 3 SD of a baseline of earlier sessions, used by the Westgard rules. |

---

## 2. Preparing and acquiring bead images

Good QC data makes everything downstream reliable.

- **Beads:** sub-resolution beads (the facility uses 100 nm TetraSpeck on one
  slide for all instruments). Record the bead diameter in the profile so the
  FWHM can be corrected for the bead's own size.
- **Density:** beads well separated: each needs an empty box of about
  2 FWHM laterally and axially around it. 10 to 50 good beads per field give
  stable medians.
- **Field:** spread beads over the whole field if you want tilt, curvature,
  and uniformity (chapter 6.3).
- **Z range:** cover the bead's axial PSF and its tails: at least about
  6 axial FWHM in total (e.g. ±2 µm for a confocal at NA 1.4), with the
  beads near the middle. Too short a stack leaves beads unmeasurable or
  without an averaged PSF.
- **Sampling:** at least Nyquist for the theoretical PSF; PSFWidget warns
  when the pixel size or z step is too coarse (MET-16).
- **Exposure:** no saturated voxels (saturated beads are rejected) and an SNR
  of at least 10 (the default threshold).
- **Same settings every time:** keep the objective, zoom, pixel size, z step,
  pinhole, and detector settings constant for a configuration, or the
  Levey-Jennings chart mixes different things.
- **Metadata:** save in the instrument's own format (`.nd2`, `.lif`, `.czi`)
  or OME-TIFF, which keep the voxel size and optics. Plain TIFF exports need
  a profile with the missing values.

---

## 3. The napari GUI

Start `napari` from the environment where PSFWidget is installed (see the
install guide). The widgets are under **Plugins → PSFWidget**:

- **PSF analysis**: analyse one file, review beads, record, report, and sign;
- **PSF tracking (Levey-Jennings)**: charts, exclusions, and events;
- **Instrument profile editor**: create and edit profiles.

**File → Open Sample → PSFWidget → Synthetic 100 nm bead field** opens a
synthetic stack for practice.

### 3.1 PSF analysis

The widget is a column of numbered sections. Long steps run in the background
(the progress bar moves) and the viewer stays usable.

**1. File**

1. *Browse…* to the bead stack. The *Series* list fills with the file's
   series (positions, scenes) and their sizes.
2. Choose the *Series*.
3. Optionally *Browse…* to an instrument *Profile*. It is needed to record
   results (it names the instrument) and supplies values the file lacks.
4. Press *Open*. Each channel appears as an image layer at its physical
   scale (µm).

**2. Metadata**

The table lists every value PSFWidget will use, with where it came from
(*file*, *profile*, *user*, or *missing*). Hover over a field for notes.

- Missing **required** values are highlighted in red, and the line below
  the table says *Not ready* and why.
- To set or correct a value, type it in the *Value* column (e.g.
  `numerical_aperture` = `1.4`, `ch 0: emission nm` = `520`). Your values are
  marked *user* and highlighted blue. Clear a value to remove your override.
- Invalid values are refused with a message at the bottom of the widget.

Required: the voxel size, numerical aperture, immersion refractive index,
and for each channel the emission wavelength, or for **multiphoton** the
excitation (laser) wavelength.

**3. Analyse**

Optionally change analysis settings in the text box, e.g. `min_snr=5,
outlier_mad=off` (see §13.3), then press *Analyse*.

**4. Results and review**

- Each analysed channel gets a **bead review** points layer: accepted beads
  as green circles, rejected beads as red circles. Hover over a bead to see
  its bead id, status, FWHM, SNR, and rejection reasons in napari's status
  bar.
- *Channel* chooses which channel's summary is shown: median, SD, number of
  beads, and ratio to theory for each main metric, followed by the theory,
  spec status, rejection counts, and any warnings.
- Tick *3D view* to inspect the beads in 3D.

**Manual review (DET-07).** To exclude a bead (for example dust or a
doublet the filters missed) or include a rejected one:

1. Select the *bead review* layer, choose the selection tool in the layer
   controls, and click or drag over the beads.
2. Enter a *Reason* and your name (*Reviewer*).
3. Press *Exclude selected beads* or *Include selected beads*.

The summary, spec checks, field metrics, and averaged-PSF comparison are
recomputed at once. Each decision is kept with the bead ("excluded by
reviewer *name*: *reason*", or "included by reviewer …, was rejected: …").
A bead whose FWHM could not be measured cannot be included. *Undo review*
returns to the automatic selection.

**5. Record and report**

- *Database*: the instrument's results database (`.sqlite`; created if it
  does not exist). Keep it on the PC's local disk.
- *Record in database* stores the reviewed result, with your name, the
  computer, the time, and the profile's fingerprint. The same file and series
  cannot be recorded twice by accident.
- *Report folder*, tick *HTML* and/or *PDF*, then *Write report*. If the
  session was recorded first, the report includes its tracking checks
  (Westgard status against earlier sessions).

**6. Review and approval**

Signing a report is described in chapter 10. In short: the report you just
wrote is selected in *Report* (or *Choose…* any report file), check the
*Meaning*, add an optional *Comment*, and press *Approve* or *Reject*. Your
name is taken from *Reviewer* above. *Check signature* shows who signed the
report and whether it has changed since.

Messages for every step appear at the bottom of the widget.

### 3.2 PSF tracking (Levey-Jennings)

1. *Browse…* to a results database (it loads at once), and optionally a
   *Profile* for that instrument's tracking settings (which metrics, which
   rules, baseline length); press *Load* after changing it.
2. The list shows each configuration, its number of sessions, and the state
   of its latest session: *OK*, *WARNING*, *FAIL*, or *no limits yet*.
3. Choose a configuration and a *Metric*. The Levey-Jennings chart shows
   (see §7.3 for the symbols) the session values, mean and ±2/±3 SD limits,
   warnings, rejections with the rules broken, excluded sessions, events,
   and spec limits. Use the toolbar to zoom and pan.
4. **Hover** over a point to see its session id, channel, file, date, value,
   z-score, and rule violations. **Click** to select it.
5. To leave a selected session out of the statistics, enter a *Reason* and
   your name (*User*) and press *Exclude session*; *Include session* brings an
   excluded one back. The session stays in the database and on the chart (as
   an open circle).
6. To record an event (service visit, alignment, objective change, laser
   change, …) for the selected configuration's instrument: choose the kind,
   enter a description in *Reason*, tick *start new control limits* if the
   instrument changed, and press *Add event now*.
7. *Save chart…* writes PNG, SVG, or PDF.

### 3.3 Instrument profile editor

- *New* starts an empty profile; *Open…* loads one.
- Fill in the instrument (id is required), beads, objectives, channels, spec
  limits, and tracked metrics (comma-separated metric names; empty means the
  defaults). Use *Add* / *Remove selected* for objective and channel rows.
- For each channel, *match by* says how it is recognised in files: by
  `index` (position), `excitation_nm` (laser line, within 5 nm), or `name`.
- *Check* validates the profile with the same rules as the command line.
- *Save as…* writes the TOML file; only a valid profile is saved.
- Hand-written tracking rules in a file (`rules`, `metric_rules`) are kept.
  Comments in the file are not.

---

## 4. Instrument profiles

A profile is a TOML text file. Values in a profile **override** the file's
metadata, and values you enter (`--set` or the GUI table) override the
profile. Examples: [`examples/profiles/`](../../examples/profiles/README.md).

```toml
[instrument]
id = "sp8"                       # required; results are filed under it
name = "Leica SP8"
modality = "confocal"            # widefield | confocal | spinning_disk | multiphoton
# resolution_enhancement = "NSPARC SR"   # for image-scanning modes
# pinhole_um = 50.0              # physical pinhole (spinning disk)

[bead]
diameter_nm = 100
product = "TetraSpeck 0.1 um"

[[objective]]
id = "63x-oil-1.40"
match = "63x/1.40"               # part of the objective name in the files
numerical_aperture = 1.4         # set only if the files lack or misstate it
immersion_ri = 1.518
# magnification = 63

[[channel]]
id = "green"
match = { excitation_nm = 488 }  # or { index = 0 } or { name = "GFP" }
emission_nm = 515                # the bead dye's emission peak
# excitation_nm = 920            # multiphoton: the laser wavelength
# pinhole_airy = 1.0             # pinhole in Airy units

[spec]                           # optional acceptance limits
lateral_ratio_max = 1.3
axial_ratio_max = 1.5
ellipticity_min = 0.8
min_beads = 5

[tracking]                       # optional; see chapter 7
baseline_sessions = 20
metrics = ["fwhm_x_um", "fwhm_y_um", "fwhm_z_um", "ratio_z", "strehl"]
rules = ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"]

[tracking.metric_rules]
integrated_intensity = ["1_3s"]
```

Notes:

- `emission_nm` is the **bead dye's** emission peak, not the detector band,
  which is usually much wider.
- No spec limits are shipped; the numbers above are placeholders. Without
  limits and before enough sessions for control limits, statuses are
  *NOT EVALUATED*.
- **Multiphoton:** set `modality = "multiphoton"` and each channel's
  `excitation_nm` to the laser wavelength (e.g. 920, not 460). ND2, CZI, and
  OME-TIFF files that record a multiphoton mode are recognised; Leica LIF
  files need the profile setting. See `multiphoton-template.toml`.
- Unknown keys and wrong types are refused with the table and key named.
- Each result stores the profile's SHA-256 fingerprint, so any change to the
  file is visible in the records.

---

## 5. The command line

Every command has `--help`. Activate the PSFWidget environment first.

### 5.1 Inspect a file: `info`

```bash
psfwidget info beads.nd2 --profile sp8.toml
psfwidget info beads.lif --series 2 --set numerical_aperture=1.4 --json
```

Lists the series and each metadata value with its origin, and whether the
stack is ready for analysis. `--strict` exits with status 1 if it is not.

Common options (also for `analyze`): `--series`, `--timepoint`, `--tile`,
`--profile`, `--objective` (a profile objective id), `--set KEY=VALUE`
(repeatable; channel values as `channel.0.emission_nm=515`), `--no-hash`.

### 5.2 Analyse: `analyze`

```bash
psfwidget analyze beads.lif --series 2 --profile sp8.toml --out results/
psfwidget analyze beads.lif --series 2 --profile sp8.toml --out results/ \
    --db qc.sqlite --report html,pdf --xlsx --psf
```

| Option | Effect |
|---|---|
| `--out DIR` | output folder (default: current folder) |
| `--channel N` | analyse only this channel (repeatable) |
| `--setting KEY=VALUE` | change an analysis setting (§13.3), repeatable |
| `--overwrite` | replace existing CSV/JSON outputs |
| `--db FILE` | record in a results database (needs an instrument: use `--profile` or `--set instrument_id=…`) |
| `--record-again` | record even if this file and series is already recorded |
| `--user NAME` | name recorded (default: login name) |
| `--report html,pdf` | write a session report; `--page letter|a4` |
| `--xlsx` | write an Excel workbook (Summary, Channels, Beads, Metadata sheets) |
| `--psf` | write each channel's averaged PSF as OME-TIFF (chapter 8.4) |
| `--fail-on-spec` | exit with status 3 if a channel fails its spec limits |

The command prints a summary per channel and writes:

| File | Contents |
|---|---|
| `<name>_beads.csv` | one row per bead: position, FWHM (raw and bead-corrected; 1D, plane, and 3D fits), intensity, SBR, SNR, fit quality, status, reasons |
| `<name>_summary.csv` | n, mean, SD, median, and quartiles of each metric over accepted beads |
| `<name>_channels.csv` | per channel: bead counts, theory, spec status, field and comparison metrics, warnings |
| `<name>_run.json` | everything above plus settings, metadata, and provenance |

`<name>` is the file name plus `_s<series>`, `_t<timepoint>`, `_m<tile>` when
not 0.

### 5.3 Other commands

| Command | Purpose | Chapter |
|---|---|---|
| `psfwidget track …` | status, charts, events, exclusions, export, backup, audit | 7 |
| `psfwidget report trend …` | trend report from the database | 8 |
| `psfwidget run`, `watch`, `check-config` | unattended runs | 9 |
| `psfwidget approve`, `verify` | sign and check reports | 10 |
| `psfwidget selftest` | installation check and IQ/OQ record | install guide §4.2 |

---

## 6. Understanding the results

### 6.1 Per-bead measurements

| Metric | How it is measured |
|---|---|
| FWHM x, y, z | 1D Gaussian fits (with offset) to profiles through the bead centre, each the mean of a 3×3 bundle of lines; corrected for the bead diameter when known (`_raw` values are uncorrected) |
| XY, XZ, YZ major/minor FWHM and angle | 2D rotated-Gaussian fits to the central planes |
| FWHM x, y, z (3D fit) | an axis-aligned 3D Gaussian fit to the bead's box |
| Ellipticity xy | minor / major FWHM in the XY plane (1 = round) |
| Ratio x, y, z | FWHM / theoretical FWHM |
| Integrated intensity | background-subtracted sum inside an ellipsoid of 1.5 FWHM (not measured if it would extend past the stack) |
| Peak, background, SNR, SBR | peak voxel; mean and SD of the box outside the ellipsoid; (peak − background) / background SD; peak / background |
| R² | fit quality of each 1D fit |

The channel summary is the **median** over accepted beads (with SD and
quartiles). How accurate the FWHM is on synthetic beads is documented in
[fitting_accuracy.md](../validation/fitting_accuracy.md).

### 6.2 Why beads are rejected

| Reason | Meaning |
|---|---|
| too close to the image border | the centre is within 1 FWHM of an edge |
| another bead inside the measurement box | overlapping beads |
| saturated | a voxel near the centre at the detector maximum |
| SNR … below … / SBR below … | too faint (thresholds in §13.3) |
| too few background voxels in the measurement box | the box is mostly bead |
| fit failed / poor fit (R² … below 0.8) | the bead is not Gaussian-like (dust, aggregate) |
| measured width not larger than the bead | implausible fit |
| outlier: integrated intensity / lateral FWHM high or low | far from the other beads (aggregates, doublets) |
| excluded by reviewer *name*: *reason* | manual review |

### 6.3 Field and channel metrics

Computed per channel from the accepted beads and tracked like the bead
summaries:

| Metric | Meaning |
|---|---|
| Focus-plane tilt (°) and focus range (µm) | plane fitted to bead focus positions; a tilted slide or stage |
| Field-curvature sag (µm) | focus at the field corner relative to the centre |
| Bead intensity, corner / centre | illumination uniformity |
| Lateral and axial chromatic shift (nm) | median shift of this channel's beads from the first analysed channel (e.g. TetraSpeck) |
| Strehl ratio estimate | peak of the averaged PSF / theoretical peak at equal energy; < 1 means aberrated |
| Similarity to theory | Pearson correlation of the averaged and theoretical PSFs |
| Axial skewness, energy above / below focus | asymmetry of the averaged PSF; 0 and 1 when symmetric (spherical aberration shows here) |
| Ratio to image-scanning theory | for NSPARC, Live-SR, and similar modes |

These need enough beads: tilt at least 3 spread over the field, curvature
and uniformity at least 6, the averaged PSF at least 3 whose box fits in
the stack. When not computed, the report says why.

### 6.4 The theoretical PSF

- **Widefield:** the emission PSF.
- **Confocal and spinning disk:** excitation PSF × emission PSF through the
  pinhole (in Airy units, from the file or profile).
- **Multiphoton:** the squared excitation PSF at the laser wavelength
  (Zipfel et al. 2003).
- **Image-scanning modes** (NSPARC, Live-SR): compared with the base model
  (ratios below 1 are expected) and with an image-scanning model.

All use a scalar high-NA model with matched refractive indices; the
published closed-form values are shown alongside. A vectorial and
refractive-index-mismatch model is not yet included (STATUS D1).

### 6.5 Status

Each channel and the whole session get one status, shown on reports:

| Status | When |
|---|---|
| **FAIL** | no bead accepted, a spec limit failed, or a Westgard rejection rule broken |
| **WARNING** | a Westgard warning (1₂ₛ) |
| **PASS** | spec limits and/or control limits were checked and passed |
| **NOT EVALUATED** | no spec limits set and no control limits yet |

---

## 7. Tracking over time

### 7.1 Baselines and control limits

For each configuration and metric, control limits (mean, ±2 SD, ±3 SD) come
from a **baseline**: by default the first 20 valid sessions. With fewer than
20 the limits are *provisional*; with fewer than 5 there are none. After an
event with *new control limits*, a new baseline starts from the next
sessions (or a date range, or entered targets).

### 7.2 Westgard rules

| Rule | Type | Broken when the session … |
|---|---|---|
| 1₂ₛ | warning | is beyond 2 SD |
| 1₃ₛ | rejection | is beyond 3 SD |
| 2₂ₛ | rejection | and the previous one are beyond 2 SD on the same side |
| R₄ₛ | rejection | and the previous one are beyond 2 SD on opposite sides |
| 4₁ₛ | rejection | and the previous 3 are beyond 1 SD on the same side |
| 10ₓ | rejection | and the previous 9 are on the same side of the mean |

Choose rules per metric in the profile (`[tracking]`).

### 7.3 Reading a Levey-Jennings chart

Blue line and dots: session values. Gray line: baseline mean; dashed yellow
±2 SD; dashed red ±3 SD. Shaded: the baseline sessions. Yellow triangles:
warnings. Red diamonds: rejections, labelled with the rules broken. Open
circles: excluded sessions. Vertical dashed lines: events (solid: new
baseline). Dash-dot line: spec limit.

### 7.4 Command line

```bash
psfwidget track list   --db qc.sqlite
psfwidget track status --db qc.sqlite --profile sp8.toml [--fail-on-rejection]
psfwidget track chart  --db qc.sqlite --out charts/ [--metric fwhm_z_um] [--format svg]
psfwidget track event  --db qc.sqlite --instrument sp8 --kind service \
    --description "laser realigned" --rebaseline [--baseline-sessions 10]
psfwidget track exclude --db qc.sqlite 17 --reason "dirty slide" [--channel 1]
psfwidget track include --db qc.sqlite 17 --reason "slide was fine"
psfwidget track export --db qc.sqlite --out qc.csv
psfwidget track backup --db qc.sqlite --out qc_copy.sqlite
psfwidget track audit  --db qc.sqlite [--since 2026-10-01] [--until 2026-12-31] [--csv audit.csv]
```

`--instrument`, `--objective`, and `--channel` narrow `status` and `chart`.
Event kinds: `service`, `alignment`, `objective`, `laser`, `baseline` (limits
only), `other`.

**Nothing in the database is ever changed or deleted.** Exclusions,
inclusions, events, and approvals are added as new rows and written to the
audit log with the user, computer, and time.

---

## 8. Reports and exports

### 8.1 Session report

One acquisition: overall status and reasons; acquisition settings; for each
channel the metric summary against theory, the theory used, the Westgard
checks against earlier sessions (when recorded), the spec checks, field
geometry and registration with a **field map** (bead focus offsets and
lateral FWHM across the field), the averaged-PSF comparison, a bead overview
(maximum projection with accepted and rejected beads), XY/XZ/YZ views and
profile fits of a representative bead, and a table of every bead; then
provenance (software version, settings, file name and SHA-256, operator) and
all metadata with origins.

### 8.2 Trend report

One instrument over a period: the latest status of each configuration,
every rule violation, events, and Levey-Jennings charts.

```bash
psfwidget report trend --db qc.sqlite --instrument sp8 --profile sp8.toml \
    --days 90 --out reports/ [--format html,pdf] [--page a4] [--facility "Core"]
```

### 8.3 Formats

HTML reports are a single self-contained file (images embedded): e-mail or
share them as they are. PDF uses letter or A4. CSV and Excel hold the raw
numbers. Existing files are never overwritten: `_2`, `_3`, … is added.

### 8.4 Averaged PSF for deconvolution

`psfwidget analyze … --psf` writes `<name>_ch<N>_psf.ome.tif` (32-bit,
normalised to a sum of 1, centred, with the physical voxel size in the OME
metadata) and `<name>_ch<N>_psf.json` (source file and hash, channel, bead
count, comparison with theory, settings).

---

## 9. Unattended runs

### 9.1 The configuration file

One TOML file per acquisition PC; paths are relative to the file. Example:
[`examples/automation/qc.toml`](../../examples/automation/qc.toml).

| Table / key | Meaning | Default |
|---|---|---|
| `[database] path` | results database, **on the local disk** | `qc.sqlite` |
| `[output] root` | report folder (network) | `reports` |
| `session_template` | session file path inside the root | `{instrument}/{yyyy}/{date}_{file_stem}_s{series}` |
| `trend_template` | trend report path | `{instrument}/trend/{date}_{instrument}_trend` |
| `reports` | `["html", "pdf"]` | both |
| `exports` | `["csv", "xlsx"]` | `["csv"]` |
| `page_size` | `letter` or `a4` | `letter` |
| `[branding] facility`, `logo`, `footer` | on every report | none |
| `[[watch]] folder` | a folder to watch (one table per instrument) | – |
| `profile` | that instrument's profile | – |
| `patterns` | file patterns | `*.nd2 *.lif *.czi *.tif *.tiff` |
| `recursive` | include subfolders | `true` |
| `series` | `"all"` or a list such as `[0, 2]` | `"all"` |
| `settle_seconds` | a file must be unchanged this long | 120 |
| `[schedule] ingest` | when to look for new files | `every 15 minutes` |
| `trend_report` | when to write trend reports | `off` |
| `trend_days` | trend report period | 90 |
| `low_priority` | run at reduced process priority | `true` |
| `[alerts] status_file`, `alert_file` | in the output root | `STATUS.json`, `ALERT.txt` |
| `[backup] folder` | backup folder (network) | none |
| `schedule` | when to back up | `daily 02:00` |
| `keep` | newest backups kept | 30 |
| `[analysis]` | analysis settings (§13.3) | defaults |

Template tokens: `{instrument}`, `{objective}`, `{date}`, `{yyyy}`, `{mm}`,
`{dd}`, `{file_stem}`, `{series}`, `{session}`, `{kind}`.

**Schedules** are written in plain words: `every 15 minutes`, `every 2
hours`, `every 1 day`, `daily 07:30`, `weekly monday 07:30`, `monthly 1
07:30` (day 1 to 28), or `off`, in the computer's local time. A calendar job
first runs at its first scheduled time after the configuration is first
used.

### 9.2 Commands

```bash
psfwidget check-config qc.toml          # validate and show what would happen
psfwidget run --config qc.toml          # run what is due, then exit
psfwidget run --config qc.toml --job trend_report   # also run a job now (or --job all)
psfwidget watch --config qc.toml        # keep running; check every minute
```

`run --fail-on-alert` exits with status 5 when an alert is raised. Setting
up Task Scheduler or cron is in the install guide §6.3.

### 9.3 What a run does

- **ingest:** analyses new files in each watched folder (once they stop
  changing), records them, and writes session reports and exports. Files
  already in the database are skipped; a file that cannot be read is logged
  and does not stop the others; files are not retried until they change.
- **trend_report:** writes a trend report per instrument.
- **backup:** copies the database to the backup folder, verifies the copy
  (SQLite integrity check, `.sha256` file), and removes the oldest of
  PSFWidget's own backups beyond `keep`.

### 9.4 Checking on it

In the output root:

- **`STATUS.json`** (after every run): overall status, each configuration's
  latest status and reasons, and the last runs.
- **`ALERT.txt`** exists only while something needs attention: a latest
  session FAILs, or the last run had errors (unreadable file, missing folder,
  failed backup). It lists what and where. It is removed when both clear, so
  a folder watcher or monitoring script only needs to check for the file.
- **`_psfwidget/psfwidget.log`**: a running log. **`_psfwidget/runs/`**: one
  JSON summary per run (sessions, skipped files, errors, configuration
  fingerprint). **`_psfwidget/state.json`**: what has been processed.

---

## 10. Records, review, and approval (GLP)

PSFWidget is used for internal QC but is designed to conform to GLP (OECD
GLP principles and Advisory Document No. 17). What the software does:

- **Attributable records:** every session, exclusion, inclusion, event,
  approval, and database upgrade records the user, computer, and UTC time.
  Scheduled runs record `psfwidget-scheduler (<OS account>)`.
- **Configuration control:** results record the software version, the
  analysis settings, and the SHA-256 of the profile and automation
  configuration used.
- **Raw data linkage:** results keep the source file's name, path, and
  SHA-256. Reports and exports are never overwritten.
- **Append-only:** recorded data cannot be changed or deleted; corrections
  are new, audited rows.

What the facility must provide (procedural controls, see STATUS F10):
personal user accounts, synchronised clocks, folder permissions, a backup
retention policy, and the validation records.

### 10.1 Signing a report

A signature records who reviewed a report, when, on which computer, whether
it was **approved** or **rejected**, the **meaning** of the signature
(default: "reviewed; results and report are complete and correct"), and an
optional comment. It is tied to the report file's SHA-256: if the file is
changed afterwards, the signature no longer matches. A second review of the
same report (or of a re-issued report for the same session) is the next
**revision**; earlier signatures remain.

In the GUI, use section 6 of *PSF analysis* (§3.1). On the command line:

```bash
psfwidget approve reports/sp8/2026/2026-11-14_beads_s0.pdf --db qc.sqlite --session 12 \
    [--reject] [--meaning "…"] [--comment "…"] [--user "A. Name"]
psfwidget verify reports/sp8/2026/2026-11-14_beads_s0.pdf --db qc.sqlite
```

The signature is stored in the database and in
`<report>.signature.json` next to the report. `verify` exits with status 7
if no signature matches the report's current content.

### 10.2 Audit review

```bash
psfwidget track audit --db qc.sqlite --since 2026-10-01 --until 2026-12-31 --csv audit.csv
```

### 10.3 Installation qualification

Run `psfwidget selftest --out <folder>` after installing or upgrading and
sign the record (install guide §4.2).

---

## 11. Python API

The same functions the GUI and command line use are available from Python:

```python
from psfwidget.analysis import analyze_stack
from psfwidget.io import list_series, read_stack, parse_assignments
from psfwidget.profiles import load_profile
from psfwidget.tracking.store import ResultsDatabase

profile = load_profile("sp8.toml")
print([s.name for s in list_series("beads.lif")])
stack = read_stack("beads.lif", series=2, profile=profile,
                   overrides=parse_assignments(["channel.0.emission_nm=515"]))
print(stack.problems())                     # [] when ready
result = analyze_stack(stack, spec_limits=profile.spec)
for channel in result.channels:
    print(channel.name, channel.summary["fwhm_z_um"].median, channel.channel_metrics)

with ResultsDatabase("qc.sqlite") as db:
    session_id = db.record(result, user="A. Name", profile_sha256=profile.sha256)
```

Scripted GUI-equivalent work, including manual review, uses
`psfwidget.napari.session.AnalysisSession` (no Qt needed): `open`,
`set_value`, `analyze`, `decide`, `record`, `write_report`, `approve`. All
public functions have docstrings (`help(analyze_stack)`). A generated API
reference is planned for v1.0 (STATUS D7).

---

## 12. Troubleshooting

| Symptom | What to do |
|---|---|
| "not ready for analysis: missing …" | Supply the value with a profile, `--set`, or the GUI table. `psfwidget info` shows what is missing and where values came from. |
| Few or no beads accepted | Read the rejection counts. "SNR … below" → brighter acquisition or `--setting min_snr=…`; "border" or "another bead" → lower density or larger field; "poor fit" → check for dust and aggregates. |
| Ratios far below 1 | Check the metadata (NA, wavelengths, pinhole, voxel size) and the bead size. For NSPARC or Live-SR, ratios below 1 against the base model are expected. |
| Ratios well above 1, axially | Possible spherical aberration (refractive-index mismatch, correction collar); look at the axial asymmetry and Strehl. |
| "beads cover too little of the field" / no tilt | Beads must spread over at least a quarter of the field in x and y. |
| "no averaged PSF: … too close to the stack's edges" | The z-stack is too short for the averaging box; acquire a longer stack. |
| "already recorded as session N" | The same file and series is in the database. Use `--record-again` only on purpose. |
| `ALERT.txt` appeared | Open it: it names the failing configuration or the file/folder with an error. Details are in `STATUS.json` and `_psfwidget/runs/`. |
| "another run has held … run.lock" | A run is in progress, or one crashed less than 6 hours ago (the lock is then taken over automatically). |
| A file in the watched folder is never analysed | It is still being written (settle time), does not match `patterns`, failed before (see the run summaries; it is retried when the file changes), or is already recorded. |
| `verify` says the report has changed | The file was edited or replaced after signing. Use the original, or review and sign the new version. |
| GUI problems | See the install guide §8. |

---

## 13. Reference

### 13.1 Exit status of commands

| Status | Meaning |
|---|---|
| 0 | success |
| 1 | `info --strict`: not ready for analysis; `analyze`: not ready; `selftest`: a check failed |
| 2 | error (bad input, unreadable file, invalid profile or configuration, database error) |
| 3 | `analyze --fail-on-spec`: a channel failed its spec limits |
| 4 | `track status --fail-on-rejection`: a latest session broke a rejection rule |
| 5 | `run --fail-on-alert`: an alert was raised |
| 6 | `run`: another run is in progress |
| 7 | `verify`: no signature matches the report |

### 13.2 Metadata keys (`--set`, GUI table, profiles)

Acquisition: `voxel_z_um`, `voxel_y_um`, `voxel_x_um`, `numerical_aperture`,
`immersion_ri`, `objective_name`, `magnification`, `modality`,
`resolution_enhancement`, `acquired_utc`, `significant_bits`,
`bead_diameter_nm`, `instrument_id`, `objective_id`.
Per channel (`channel.<index>.<key>`): `name`, `excitation_nm`,
`emission_nm`, `detection_band_nm`, `pinhole_airy`, `pinhole_um`,
`preset_id`.

### 13.3 Analysis settings (`--setting`, GUI box, `[analysis]`)

| Setting | Default | Meaning |
|---|---|---|
| `detection_threshold_sd` | 8.0 | candidates must exceed background by this many noise SDs |
| `detection_relative_threshold` | 0.05 | … and this fraction of the median candidate height |
| `detection_median_px` | 3 | in-plane median filter against hot pixels (1 = off) |
| `crop_lateral_fwhm`, `crop_axial_fwhm` | 2.0 | half-size of the measurement box, in expected FWHM |
| `min_border_fwhm` | 1.0 | minimum distance of a bead centre from the image edges |
| `integration_radius_fwhm` | 1.5 | radius of the integration ellipsoid |
| `min_background_voxels` | 20 | background voxels needed in the box |
| `min_snr` | 10.0 | minimum SNR |
| `min_sbr` | off | minimum SBR |
| `min_r_squared` | 0.8 | minimum R² of the worst 1D fit |
| `outlier_mad` | 4.0 | outlier threshold in robust SDs (`off` to disable) |
| `min_beads_for_outliers` | 5 | outliers are only flagged with at least this many beads |
| `profile_width_px` | 3 | lines averaged per 1D profile (3 = 3×3 bundle) |
| `bead_size_correction` | true | correct FWHM for the bead diameter |
| `nyquist_samples_per_fwhm` | 2.3 | samples per theoretical FWHM below which undersampling is warned |
| `compare_averaged_psf` | true | average the beads and compare with theory |

### 13.4 Trackable metrics

Bead summaries: `fwhm_x_um`, `fwhm_y_um`, `fwhm_z_um` (and `_raw_um`),
`xy_major_um`, `xy_minor_um`, `xz_major_um`, `xz_minor_um`, `yz_major_um`,
`yz_minor_um`, `ellipticity_xy`, `ratio_x`, `ratio_y`, `ratio_z`,
`fwhm_x_3d_um`, `fwhm_y_3d_um`, `fwhm_z_3d_um`, `integrated_intensity`,
`peak`, `background`, `snr`, `sbr`, `accepted_beads`.
Channel-level: `tilt_deg`, `focal_range_um`, `curvature_sag_um`,
`uniformity_corner_ratio`, `chromatic_lateral_nm`, `chromatic_axial_nm`,
`strehl`, `psf_similarity`, `axial_skewness`, `axial_asymmetry`,
`ratio_lateral_ism`, `ratio_axial_ism`.
Tracked by default: `fwhm_x_um`, `fwhm_y_um`, `fwhm_z_um`, `xy_major_um`,
`xy_minor_um`, `xz_major_um`, `yz_major_um`, `ratio_x`, `ratio_y`,
`ratio_z`, `ellipticity_xy`, `integrated_intensity`, `sbr`,
`accepted_beads`.

### 13.5 References

- Cole RW, Jinadasa T, Brown CM (2011). Measuring and interpreting point spread functions to determine confocal microscope resolution and ensure quality control. *Nat. Protoc.* 6:1929–1941.
- Zipfel WR, Williams RM, Webb WW (2003). Nonlinear magic: multiphoton microscopy in the biosciences. *Nat. Biotechnol.* 21:1369–1377.
- Sheppard CJR (1988). Super-resolution in confocal imaging. *Optik* 80:53–54; Müller CB, Enderlein J (2010). Image scanning microscopy. *Phys. Rev. Lett.* 104:198101.
- Westgard JO et al. (1981). A multi-rule Shewhart chart for quality control in clinical chemistry. *Clin. Chem.* 27:493–501.
- OECD (2016). Application of GLP Principles to Computerised Systems. Series on GLP and Compliance Monitoring No. 17.
