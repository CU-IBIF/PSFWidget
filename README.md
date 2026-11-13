# PSFWidget

PSFWidget is a microscope quality-control tool for point spread functions (PSFs). It will:

- analyze empirical PSFs from 3D bead images in `.nd2`, `.lif`, `.czi`, and `.tif` files;
- compare them with theoretical PSFs;
- track performance over time on Levey-Jennings charts;
- produce scheduled, distributable reports.

> **Status:** Pre-alpha. File import (M1) and bead measurement (M2) work:
> PSFWidget reads bead stacks in all four formats, measures each bead, and
> compares the results with a theoretical PSF. Tracking over time (M3) and
> reports (M4) are next.

## Planning documents

- [Project Plan, Requirements, Tasks & Milestones](docs/PLAN.md)
- [Verification & Validation Plan](docs/VV_PLAN.md)
- [Requirements traceability matrix](docs/traceability.csv)
- [Dependency licence audit](docs/LICENSE_AUDIT.md)

## What works today

Inspect a file and the metadata PSFWidget would use for it:

```bash
psfwidget info beads.nd2 --profile examples/profiles/csu-livesr.toml
```

The output lists the image series, each metadata value with its origin
(file, profile, or user), and whether the stack is ready for analysis. Use
`--set numerical_aperture=1.4` or `--set channel.0.emission_nm=515` to
override a value, and `--json` for machine-readable output.

An instrument profile supplies what files do not record, such as the bead
size and the bead dye's emission peaks. See
[examples/profiles](examples/profiles/README.md).

Measure the beads and write the results:

```bash
psfwidget analyze beads.lif --series 2 --profile examples/profiles/sp8.toml --out results/
```

For each channel this prints the median FWHM in x, y, and z with the
theoretical value and their ratio, plus ellipticity, signal-to-background,
signal-to-noise, why beads were rejected, and any spec-limit failures. It
writes four files to `results/`:

| File | Contents |
|---|---|
| `<name>_beads.csv` | One row per bead: position, FWHM (raw and bead-size corrected), plane fits, intensity, SBR, SNR, fit quality, status, and rejection reasons |
| `<name>_summary.csv` | n, mean, SD, median, and quartiles of each metric over accepted beads |
| `<name>_channels.csv` | Per channel: bead counts, theoretical PSF and model, spec status, notes |
| `<name>_run.json` | All of the above plus the settings, metadata, and file provenance |

Analysis settings can be changed with `--setting`, e.g. `--setting min_snr=5`;
see `psfwidget.settings.AnalysisSettings` for the full list.

From Python:

```python
from psfwidget.analysis import analyze_stack
from psfwidget.io import read_stack
from psfwidget.profiles import load_profile

profile = load_profile("sp8.toml")
stack = read_stack("beads.lif", series=2, profile=profile)
result = analyze_stack(stack, spec_limits=profile.spec)
for channel in result.channels:
    print(channel.name, channel.summary["fwhm_z_um"].median)
```

How accurate the FWHM measurement is on synthetic beads is documented in
[docs/validation/fitting_accuracy.md](docs/validation/fitting_accuracy.md).

## Development

You need Python 3.12 or newer.

```bash
python -m pip install -e ".[dev]"
pre-commit install
python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks, coding standards, how
tests are linked to requirements, and the release process.

## License

MIT. See [LICENSE](LICENSE).
