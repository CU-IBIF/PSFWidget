# Golden test files (GOLD-IO)

Small real microscope files used to test the format readers. See
`docs/VV_PLAN.md` §3.

The files themselves are **not stored in git**. `manifest.json` lists each
file with its SHA-256 hash, licence, attribution, and the values a reader is
expected to return. Files are kept in `tests/data/cache/`, which git ignores.

```bash
python tools/fetch_test_data.py            # download public files, verify all
python tools/fetch_test_data.py --status   # show what is present
```

Tests get a file through the `golden_file` fixture. A test whose file is
absent is skipped locally. In CI a missing public file fails the run.

## Public files

Downloaded from the [OME sample image collection](https://downloads.openmicroscopy.org/images/ND2/).
All are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and are used unmodified.

| File | Author | Used for |
|---|---|---|
| `BF007.nd2` | Maxime Woringer | A single 2D plane with complete optics metadata; must be rejected as not a 3D stack |
| `Exception_2.nd2` | Jonas Dorn | A 3D stack with uncalibrated pixel size; missing-metadata handling |
| `header_test2.nd2` | Jonas Dorn | A time series of calibrated z-stacks; timepoint selection |

None of these is a bead image, and none is a multichannel or multi-position
ND2. A facility ND2 bead stack is still needed for those cases.

## Local-only files

These have no download URL. Copy them into `tests/data/cache/` under the
name in the manifest; the fetch tool then verifies the hash.

| File | Source | Used for |
|---|---|---|
| `20261111_QC_980.czi` | CU-IBIF, Zeiss LSM 980 | Real bead field, 4 channels |
| `20261106_QC_SP8.lif` | CU-IBIF, Leica SP8 | Real bead fields, 6 series × 2 channels; z step stored negative |

They are local-only by facility decision (2026-11-11). Their embedded
metadata contains Windows account names and an instrument serial number,
and this repository is public. Do not commit them, attach them to a
release, or upload them anywhere else. Tests that need them run only where
the file has been copied into the cache, and are skipped everywhere else,
including CI.

## Adding a file

1. Check the licence allows redistribution and record it in the manifest.
2. Inspect the embedded metadata for personal data before publishing.
3. Add a manifest entry with the hash, size, and the expected reader values,
   taken from the acquisition software and not from the reader under test.
4. Keep the total under 50 MB.
