# GUI Test Script (T11.4)

**Purpose:** verify the napari GUI by hand on each supported platform
(UI-01 to UI-04, DET-07, QA-02, NFR-05). CI runs automated GUI tests on Linux
only; this script covers Windows 10/11 and the two latest macOS versions, and
the look and feel that automated tests cannot judge.

**Record:** copy the table in §3 for each platform, fill in every row, attach
screenshots where asked, and log every failure as a GitHub issue labelled
`anomaly` with its severity (VV_PLAN §6). The tester and a reviewer sign at the
end.

---

## 1. Set-up

| Item | Value (fill in) |
|---|---|
| Tester | |
| Date | |
| Computer, OS, and version | |
| Display (resolution, scaling) | |
| PSFWidget version (`psfwidget --version`) | |
| `psfwidget selftest` result and record file | |

Test data:

- the synthetic sample (*File → Open Sample → PSFWidget*);
- one facility bead stack per format available on the platform (`.nd2`,
  `.lif`, `.czi`, OME-TIFF), with its instrument profile;
- a plain TIFF without optics metadata (for missing-metadata steps);
- an empty folder for outputs, and a new results database path in it.

---

## 2. Steps

Each step lists what to do and what must happen. "Message" means the line at
the bottom of the PSF analysis widget.

### A. Start-up

| ID | Do | Expected |
|---|---|---|
| A1 | Start `napari` from the PSFWidget environment | napari opens without errors in the terminal |
| A2 | *Plugins → PSFWidget* | three entries: PSF analysis; PSF tracking (Levey-Jennings); Instrument profile editor |
| A3 | *File → Open Sample → PSFWidget → Synthetic 100 nm bead field* | an image layer "synthetic beads" appears; the scale bar (if shown) reads µm |
| A4 | Drag each of an `.nd2`, `.lif`, `.czi`, `.tif`, and `.ome.tif` bead file onto the napari window (IMP-11) | each opens in the *PSF analysis* widget (opened if needed) with one image layer per channel and its metadata; if napari asks for a plugin, PSFWidget is offered |
| A5 | Drag the SP8 `.lif` (6 series) onto napari; choose series 4 (IMP-02) | a dialog lists 6 series with names, channels, z-planes, size, and type; after *OK* the *Series* line reads `4 of 6: …`; *Cancel* on a second drop keeps series 4 open |

### B. Analysis widget (UI-01, UI-04)

| ID | Do | Expected |
|---|---|---|
| B1 | Open *PSF analysis*; *Browse…* to the plain TIFF; *Open* | the *Series* line shows the file's one series; an image layer appears; the metadata table shows NA, immersion RI, and emission as **missing** (italic red text, no cell fill); "Not ready: …" is shown; *Analyse* is disabled |
| B2 | Type `1.4` for numerical aperture, `1.518` for immersion RI, `widefield` for modality, and `520` for ch 0 emission | each row's text turns bold orange (*user*), with no cell fill, legible in both the dark and the light theme (*View → Theme*; UI-06); "Ready for analysis." appears; *Analyse* is enabled |
| B3 | Type `lots` as the numerical aperture | the value is refused with a message naming the field; the table is unchanged |
| B4 | Press *Analyse* and, while it runs, pan and zoom the image | the progress bar moves; the viewer stays responsive (UI-04) |
| B5 | When done | a *bead review* layer shows green circles (accepted) and red circles (rejected); the summary table shows medians, SD, n, and ratios; the message gives the accepted count |
| B6 | Hover over a bead | napari's status bar shows its id, status, FWHM, SNR, and reasons |
| B7 | Tick *3D view*; rotate | beads appear in 3D at the right aspect ratio; untick returns to 2D |
| B8 | Open a facility file **with** its profile | the metadata comes from *file* and *profile*; channels are named; analysis works for every channel; *Channel* switches the summary |

### C. Manual bead review (DET-07)

| ID | Do | Expected |
|---|---|---|
| C1 | Select one green bead in the *bead review* layer (selection tool); leave *Reason* empty; *Exclude selected beads* | refused: "Give a reason and your name" |
| C2 | Enter a reason and your name; *Exclude selected beads* | the bead turns red; the accepted count and medians change; the message says "Excluded 1 bead(s)" |
| C3 | Select a red bead rejected as "poor fit"; *Include selected beads* | included if its FWHM was measured, else refused with the reason |
| C4 | *Undo review* | the automatic selection returns |

### D. Record, report, and approval (QA-01, QA-02)

| ID | Do | Expected |
|---|---|---|
| D1 | Choose the new database path; *Record in database* | "Recorded as session 1" |
| D2 | *Record in database* again | refused: already recorded |
| D3 | Choose a report folder; HTML and PDF ticked; *Write report* | both files written; open them: status, figures (bead overview, XY/XZ/YZ views, profile fits, field map), bead table, provenance with your name |
| D4 | In *Review and approval*, add a comment; *Approve* | message: approved by you, revision 1; a `.signature.json` file appears next to the report |
| D5 | *Check signature* | shows the approval |
| D6 | Edit the HTML report file in a text editor and save; *Check signature* | "is not signed, or has changed since it was signed" |
| D7 | *Choose…* another report file; *Reject* | recorded as rejected |

### E. Tracking widget (UI-02)

Use a database with at least 10 sessions (e.g. `python tools/demo_tracking.py OUT` makes one).

| ID | Do | Expected |
|---|---|---|
| E1 | Open *PSF tracking*; *Browse…* to the database | configurations listed with session counts and latest state |
| E2 | Choose a configuration and metric | a Levey-Jennings chart with mean, ±2/±3 SD, warnings, rejections, events, and baseline shading |
| E3 | Hover over points | the session details appear below the chart |
| E4 | Click a point; enter a reason and your name; *Exclude session* | the point becomes an open circle; limits recompute; *Include session* brings it back |
| E5 | Choose *service*, enter a description, tick *start new control limits*; *Add event now* | an event line appears; new limits start after it |
| E6 | *Save chart…* as PNG and PDF | files open and match the screen |

### F. Profile editor (UI-03)

| ID | Do | Expected |
|---|---|---|
| F1 | *Open…* an example profile | fields and tables filled |
| F2 | Set `lateral ratio max` to `tight`; *Check* | refused: not a number |
| F3 | Fix it; add a channel row matched by `excitation_nm`; *Save as…* | saved; `psfwidget info` with the new profile uses it |
| F4 | *New*; *Check* without an id | refused: an id is required |

### G. Look and robustness

| ID | Do | Expected |
|---|---|---|
| G1 | At 100% and 150–200% display scaling | text readable; no clipped buttons; widgets scroll if the window is small |
| G2 | Open a corrupt or non-image file | a clear message; napari keeps running |
| G3 | Close napari while an analysis is running | napari closes without hanging |
| G4 | Open five files one after another (drag and drop), then open one while another is still loading; analyse and review beads ten times (DEF-01) | no error dialog or traceback in the terminal; the layer list shows one image layer per channel and one bead layer per analysed channel |

---

## 3. Record

| ID | Pass / Fail | Notes, screenshot file, anomaly issue |
|---|---|---|
| A1 | | |
| A2 | | |
| A3 | | |
| A4 | | |
| A5 | | |
| B1 | | |
| B2 | | |
| B3 | | |
| B4 | | |
| B5 | | |
| B6 | | |
| B7 | | |
| B8 | | |
| C1 | | |
| C2 | | |
| C3 | | |
| C4 | | |
| D1 | | |
| D2 | | |
| D3 | | |
| D4 | | |
| D5 | | |
| D6 | | |
| D7 | | |
| E1 | | |
| E2 | | |
| E3 | | |
| E4 | | |
| E5 | | |
| E6 | | |
| F1 | | |
| F2 | | |
| F3 | | |
| F4 | | |
| G1 | | |
| G2 | | |
| G3 | | |
| G4 | | |

| | Name | Signature | Date |
|---|---|---|---|
| Tester | | | |
| Reviewer | | | |
