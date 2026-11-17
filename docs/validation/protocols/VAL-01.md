# Validation Protocol VAL-01: Method comparison with MetroloJ_QC and/or PSFj (DRAFT)

| | |
|---|---|
| Protocol ID and version | VAL-01, draft 0.1 (2026-11-17) |
| Software version under test | PSFWidget release candidate (to fill in), with `psfwidget selftest` passed on each computer |
| Requirements | NFR-02, MET-01, MET-02, THE-03 |
| Author / date | |
| Approved before execution by (facility manager) / date | |

> Draft prepared from docs/VV_PLAN.md. Items marked **TBD** need the
> facility's input before approval.

## 1. Objective

Show that PSFWidget's FWHM values agree with an established tool (MetroloJ_QC and/or PSFj) on real bead data.

## 2. Scope

≥ 30 bead fields covering ≥ 3 instruments, ≥ 2 objectives, ≥ 2 wavelengths, and all four file formats (`.nd2`, `.lif`, `.czi`, TIFF/OME-TIFF). Reference tool(s) and versions: **TBD** (record exact versions).

## 3. Materials

100 nm TetraSpeck slide (catalogue number **TBD**); the facility instruments; Fiji with MetroloJ_QC and/or PSFj; PSFWidget with the instrument profiles.

## 4. Method

1. Acquire the fields per the facility's bead SOP (user manual chapter 2), Nyquist-sampled, unsaturated. Record a SHA-256 manifest.
2. Analyse each field with PSFWidget: `psfwidget analyze FILE --profile PROFILE --out results/` (bead-size correction **off** if the reference tool does not correct, i.e. `--setting bead_size_correction=false`; decide **TBD** and apply the same to both).
3. Analyse each field with the reference tool using its default bead detection; export the per-bead table as CSV.
4. Check the column mapping of `tools/method_comparison.py` against one real export of each tool; record the mapping file.
5. Write the manifest (field, PSFWidget beads CSV, reference CSV, tool, channel) and run `python tools/method_comparison.py manifest.csv --mapping mapping.toml --out val01/`.

## 5. Acceptance criteria

For each of FWHM x, y, z: mean relative bias ≤ 5%, and 95% limits of agreement within ±10% or within the inter-bead SD (NFR-02).

## 6. Analysis

Bland–Altman on relative differences of beads paired by position (mutual nearest neighbours within 0.5 µm). Also compare field medians. Report per instrument and overall. Beads found by only one tool are counted, not compared.

## 7. Records

Test record, manifest, mapping file, `val01/` outputs (pairs.csv, summary.md, Bland–Altman plots), anomalies.

## 8. Deviations

Any deviation is recorded in the test record with its reason and impact and approved by the facility manager.
