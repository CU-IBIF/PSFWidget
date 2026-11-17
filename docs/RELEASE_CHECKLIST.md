# Release Checklist (T10.7, QA-07)

Follow this for every release that will be used for QC records (v1.0.0 and
later). Tick each item in the release's pull request or issue.

## Before tagging

- [ ] All milestone tasks for the release are done or explicitly deferred in `docs/STATUS.md`.
- [ ] No open `anomaly` issue of severity Critical or Major, or a signed risk acceptance for each (VV_PLAN §6).
- [ ] CI is green on `main`: lint, types, traceability, licences, tests on Windows, macOS, and Linux (Python 3.12–3.14), GUI, API reference, and packages.
- [ ] Validation records for this release are complete and signed (VV_PLAN §7; `docs/validation/`).
- [ ] Changes to how any **tracked metric** is computed are listed in `CHANGELOG.md` with their expected effect on Levey-Jennings charts (VV_PLAN §8).
- [ ] `CHANGELOG.md`: move *Unreleased* entries under the new version and date.
- [ ] Set the version in `src/psfwidget/__about__.py` (e.g. `1.0.0`).
- [ ] Regenerate the validated dependency pins and commit them:

  ```bash
  uv pip compile pyproject.toml --extra report --universal --python-version 3.12 -o requirements/validated-report.txt
  uv pip compile pyproject.toml --extra napari --universal --python-version 3.12 -o requirements/validated-napari.txt
  ```

- [ ] Run `python tools/check_licenses.py` with the pinned versions installed.
- [ ] Update `docs/manual/INSTALL.md` and `docs/manual/USER_MANUAL.md` (version line, any changed steps).

## Tag and publish

- [ ] Merge to `main`; tag the merge commit `vX.Y.Z` and push the tag.
- [ ] The *Release* workflow builds and checks the packages, installs the wheel and runs `psfwidget selftest` on Windows, macOS, and Linux, and creates the GitHub release with the packages, pins, and self-test records.
- [ ] A maintainer approves the `pypi` environment to publish to PyPI (first release only: set up trusted publishing for the repository on PyPI, or point the workflow at the facility's own index if IT prefers).
- [ ] The *Documentation* workflow publishes the API reference to GitHub Pages (first time: *Settings → Pages → Source: GitHub Actions*).

## After release

- [ ] Install the release on one acquisition PC from the pins:

  ```bash
  python -m pip install -r requirements/validated-report.txt "psfwidget[report]==X.Y.Z"
  ```

- [ ] Run `psfwidget selftest --out <qualification folder>` and sign the IQ/OQ record.
- [ ] Back up each results database before the upgrade; open it once (upgrades are in place and audited).
- [ ] Record the version in the instrument's QC log; add a tracking event if a metric's computation changed.
- [ ] Roll out to the remaining PCs the same way.
