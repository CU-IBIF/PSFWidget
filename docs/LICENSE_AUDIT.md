# Dependency licence audit (T0.5, NFR-10)

**Date:** 2026-11-13 (updated for M2, M3, and M4 the same day)
**Method:** each package was installed from PyPI and its licence read from the
package metadata. `tools/check_licenses.py` repeats the check on the installed
dependency tree in CI and fails on any GPL or AGPL licence.

PSFWidget is MIT-licensed. A dependency under the GPL or AGPL would require
the combined work to be distributed under that licence, so those are
excluded. LGPL, MPL, BSD, MIT, Apache, and PSF licences are compatible.

## Result

No dependency adopted so far conflicts with MIT. Four libraries named in the
first draft of the plan are GPL and have been replaced or deferred.

### In use (core dependencies)

| Package | Version checked | Licence | Used for |
|---|---|---|---|
| `numpy` | 2.5.4 | BSD-3-Clause | Arrays |
| `nd2` | 0.12.0 | BSD-3-Clause | Nikon ND2 reader |
| `liffile` | 2026.11.8 | BSD-3-Clause | Leica LIF reader |
| `czifile` | 2026.11.8 | BSD-3-Clause | Zeiss CZI reader |
| `tifffile` | 2026.11.12 | BSD-3-Clause | TIFF and OME-TIFF reader |
| `scipy` | 1.18.1 | BSD-3-Clause | Fitting, filtering, Bessel functions (WP2, WP3) |
| `matplotlib` | 3.11.2 | PSF-based | Levey-Jennings charts (WP4) and report figures (WP5); optional `report` extra. Its dependencies (`pillow` MIT-CMU, `fonttools` MIT, `contourpy` BSD-3, `kiwisolver` BSD, `cycler` BSD, `pyparsing` MIT, `python-dateutil` BSD-3) all pass the check |
| `reportlab` | 5.1.0 | BSD-3-Clause | PDF reports (WP5); optional `report` extra. Its dependency `charset-normalizer` is MIT |
| `openpyxl` | 3.1.5 | MIT | XLSX export (WP5); optional `report` extra. Its dependency `et-xmlfile` is MIT |

The type-stub packages `types-reportlab` and `types-openpyxl` (Apache-2.0) are
used only for type checking in the `dev` extra and are not distributed.

`czifile`, `liffile`, and `tifffile` require Python 3.12 or newer, which
sets the project's minimum Python version.

### Planned for later work packages

| Package | Version checked | Licence | Planned for | Verdict |
|---|---|---|---|---|
| `scikit-image` | 0.26.0 | BSD-3-Clause | Not needed so far (WP2 used `scipy`) | OK |
| `lmfit` | 1.3.4 | BSD-3-Clause | Not needed so far (WP2 used `scipy`) | OK |
| `SQLAlchemy` | 2.1.8 | MIT | Not needed (WP4 uses the built-in `sqlite3`) | OK |
| `alembic` | 1.20.1 | MIT | Not needed (WP4 uses the built-in `sqlite3`) | OK |
| `plotly` | 7.1.0 | MIT | Interactive charts (WP5) | OK |
| `Jinja2` | 3.1.6 | BSD-3-Clause | Not needed (WP5 uses a block-based report model) | OK |
| `weasyprint` | 70.0 | BSD-3-Clause | Not needed (WP5 uses `reportlab`) | OK. Its dependency `pyphen` is offered under GPL-2+, LGPL-2+, or MPL-1.1; the LGPL or MPL option applies |
| `APScheduler` | 3.11.3 | MIT | Not needed (WP6 has its own small scheduler) | OK |
| `napari` | 0.10.0 | BSD-3-Clause | GUI (WP7) | OK |
| `magicgui` | 0.10.2 | MIT | GUI widgets (WP7) | OK |
| `qtpy` | 2.4.3 | MIT | Qt abstraction (WP7) | OK |
| `PySide6` | 6.12.0 | LGPL-3.0 (or GPL) | Qt binding (WP7) | OK under the LGPL option |
| `MicroscPSF-Py` | 0.2 | MIT | Gibson-Lanni PSF model (WP3) | OK; candidate replacement for `psfmodels` |

### Rejected

| Package | Version checked | Licence | Was planned for | Replacement |
|---|---|---|---|---|
| `bioio-czi` | 4.1.0 | GPL-3.0 | CZI reader | `czifile` |
| `aicspylibczi` | 4.0.1 | GPL-3.0-or-later | Dependency of `bioio-czi` | `czifile` |
| `readlif` | 0.6.6 | GPL-3.0 | LIF reader (used by `bioio-lif`) | `liffile` |
| `psfmodels` | 0.3.3 | GPL-3.0 | Gibson-Lanni and vectorial PSF models | See below |
| `PyQt5`, `PyQt6` | 5.15.11, 6.12.0 | GPL-3.0 | Qt binding | `PySide6` |

## Theoretical PSF models (decision needed in WP3)

`psfmodels` was the planned source for both the scalar Gibson-Lanni model and
the vectorial (Richards-Wolf) model (THE-02). It is GPL-3.0. Options:

1. **Implement the models in PSFWidget** (recommended). The scalar
   Gibson-Lanni model is a short numerical integral, and `MicroscPSF-Py`
   (MIT) can serve as an independent cross-check. The vectorial model is more
   work and is a "Should", so it can follow later. Keeps MIT, and the models
   are validated against the analytical formulas in the V&V plan anyway.
2. **Make `psfmodels` an optional backend** that a user may install
   themselves. PSFWidget would not depend on it or distribute it. This is a
   common arrangement, but whether it fully avoids GPL obligations is a legal
   question for the university, not an engineering one.
3. **Relicense PSFWidget under GPL-3.0.** This permits `psfmodels`,
   `bioio-czi`, and `readlif` directly. The cost is that other MIT- or
   BSD-licensed tools could not reuse PSFWidget code, and the choice is hard
   to reverse once outside contributors have added code.

The facility has said relicensing is possible if required. It is not
required: option 1 covers every requirement in the plan.

## Keeping this current

- CI runs `python tools/check_licenses.py` on every pull request.
- When adding a dependency, add it to the tables above with the version and
  licence found in its metadata.
- A package whose metadata has no licence field fails the check. Verify the
  licence in the project's repository and add it to `VERIFIED_BY_HAND` in the
  tool, with the licence found.
