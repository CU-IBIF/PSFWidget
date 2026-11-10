# Contributing to PSFWidget

## Development setup

You need Python 3.11 or newer.

```bash
git clone https://github.com/CU-IBIF/PSFWidget.git
cd PSFWidget
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pre-commit install
```

## Checks

CI runs all of these on every pull request. Run them locally before pushing:

```bash
ruff check .
ruff format --check .
mypy
python tools/traceability.py --check
python -m pytest --cov
```

The pre-commit hooks run the same tools on each commit.

## Project layout

```
src/psfwidget/      package (headless core + napari GUI layer); see docs/PLAN.md §4
tests/              pytest suite
tools/              developer scripts (traceability matrix)
docs/               plan, V&V plan, traceability matrix
```

Keep all analysis logic in the headless core. The CLI (`psfwidget.automation`)
and GUI (`psfwidget.napari`) should only call the core. Optional dependencies
(format readers, theory models, reporting, napari) belong in the matching extra
in `pyproject.toml` and are imported only inside the subpackage that needs them,
so `import psfwidget` works with the core dependencies alone.

## Coding standards

- Type hints on everything. mypy runs in strict mode.
- NumPy-style docstrings on public functions, classes, and modules.
- Line length 99, enforced by ruff.
- Physical quantities carry their unit in the name (`fwhm_nm`, `voxel_size_um`,
  `wavelength_nm`), and conversions happen at the boundary of the `io` module.
- No hidden randomness. Anything stochastic takes an explicit seed or
  `numpy.random.Generator` (NFR-03).

## Tests and requirements

Every requirement in `docs/PLAN.md` §3 is traced to its verification method
in `docs/traceability.csv`. Tag each test with the requirement IDs it verifies:

```python
@pytest.mark.req("MET-01", "NFR-01")
def test_fwhm_x_recovers_ground_truth() -> None: ...
```

- An unknown ID fails the test run.
- `pytest --req-report=requirement-coverage.csv` writes a requirement → test →
  outcome table. CI uploads it with each run.
- The coverage gate is 85% on the package (NFR-09).

### Changing requirements

Requirements change through a pull request that edits `docs/PLAN.md` and the
matrix in `docs/VV_PLAN.md` §4 together. Then regenerate the CSV:

```bash
python tools/traceability.py
```

CI fails if any requirement has no verification method, if the matrix names
an undefined ID, or if the CSV is out of date.

Changes to how a **tracked metric** is computed also need a version note and
an entry in `CHANGELOG.md` (docs/VV_PLAN.md §8). Otherwise old and new values
would get mixed on a Levey-Jennings chart.

## Branches, commits, and pull requests

- `main` is always releasable. Work on a short-lived branch and open a pull
  request. CI must pass and one reviewer must approve before merging.
- Write commit subjects in the imperative mood ("Add ND2 reader") and keep
  them under about 72 characters. Reference requirement or task IDs (e.g.
  `T1.2`, `IMP-01`) in the body where they help.
- Log test failures and validation findings as issues labelled `anomaly`,
  with severity Critical, Major, or Minor (docs/VV_PLAN.md §6).

## Test data

Do not commit full-size bead stacks. The pre-commit hook rejects files over
1 MB. Small golden files for reader tests will go under `tests/data/`.
Reference datasets live in external storage with a SHA-256 manifest
(docs/VV_PLAN.md §3).

## Releases

- Versions follow [semantic versioning](https://semver.org/). The version
  lives in `src/psfwidget/__about__.py` and is stored with every result.
- Update `CHANGELOG.md` under *Unreleased* as you go. On release, move the
  entries under the new version heading and tag the commit `vX.Y.Z`.
