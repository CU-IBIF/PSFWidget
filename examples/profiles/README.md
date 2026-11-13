# Example instrument profiles

An instrument profile tells PSFWidget what a microscope's files do not
record, or record wrongly: the bead size, the bead dye's emission peak for
each channel, and where needed the objective's optics or the pinhole.

These examples match the CU-IBIF test acquisitions of 100 nm TetraSpeck
beads (emission peaks near 430, 515, 580, and 680 nm). Copy one and edit it
for your own instrument.

| Profile | Instrument | Notes |
|---|---|---|
| `lsm980.toml` | Zeiss LSM 980 | |
| `sp8.toml` | Leica SP8 | |
| `axr-nsparc.toml` | Nikon AX R with NSPARC | The file names the NSPARC mode itself |
| `csu-livesr.toml` | Nikon Ti2 with CSU spinning disk and Live-SR | For ND2 and OME-TIFF files |
| `csu-livesr-tiff.toml` | Same instrument | For plain TIFF exports, which carry no optics metadata; channels are matched by position |

Check what a profile does to a file with:

```bash
psfwidget info path/to/file.nd2 --profile examples/profiles/csu-livesr.toml
```

Values are applied in this order: file, then profile, then `--set` overrides.
The `info` output shows where each value came from.

## Notes on the examples

- **`axr-nsparc.toml`:** the NSPARC detector records no pinhole size, but the
  confocal model that NSPARC SR is compared with needs one. The profile
  assumes **1 AU**; replace it with the effective size for your setting.
- **`csu-livesr.toml`:** the objective magnification (60) is set so the 50 µm
  disk pinhole can be converted to Airy units for OME-TIFF exports, which do
  not record the magnification. This assumes no extra magnification between
  the objective and the disk.

## Acceptance limits

A profile can carry pass/fail limits, checked against each channel's median
over accepted beads:

```toml
[spec]
lateral_ratio_max = 1.3   # FWHM x and y over the theoretical value
axial_ratio_max = 1.5     # FWHM z over the theoretical value
ellipticity_min = 0.8     # minor/major FWHM in the XY plane
min_beads = 5             # accepted beads needed for a result
```

The examples set no limits; these numbers are placeholders, not
recommendations. `psfwidget analyze --fail-on-spec` exits with status 3 when a
channel fails.
