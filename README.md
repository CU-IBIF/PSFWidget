# PSFWidget

PSFWidget is a microscope quality-control tool for point spread functions (PSFs). It will:

- analyze empirical PSFs from 3D bead images in `.nd2`, `.lif`, `.czi`, and `.tif` files;
- compare them with theoretical PSFs;
- track performance over time on Levey-Jennings charts;
- produce scheduled, distributable reports.

> **Status:** Pre-alpha. File import (M1), bead measurement (M2), and
> tracking over time (M3) work: PSFWidget reads bead stacks in all four
> formats, measures each bead, compares the results with a theoretical PSF,
> and tracks them on Levey-Jennings charts with Westgard rules. Reports and
> scheduled runs (M4) are next.

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

### Tracking performance over time

Add `--db` to record each analysis in a results database, filed by
instrument, objective, channel, and modality (the instrument comes from the
profile):

```bash
psfwidget analyze beads.nd2 --profile examples/profiles/axr-nsparc.toml --db qc.sqlite --out results/
```

After recording, it prints how the session compares with each channel's
control limits. Control limits come from the first 20 sessions by default
(provisional until then), and each session is checked against the Westgard
rules 1₂ₛ (warning), 1₃ₛ, 2₂ₛ, R₄ₛ, 4₁ₛ, and 10ₓ (rejection).

```bash
psfwidget track status --db qc.sqlite                       # latest session vs limits
psfwidget track chart  --db qc.sqlite --out charts/         # Levey-Jennings charts
psfwidget track event  --db qc.sqlite --instrument axr --kind service \
    --description "laser realigned" --rebaseline            # new limits from here on
psfwidget track exclude --db qc.sqlite 17 --reason "dirty slide"
psfwidget track export --db qc.sqlite --out qc.csv
psfwidget track audit  --db qc.sqlite                       # who changed what, when
```

Nothing in the database is ever edited or deleted. Exclusions, re-inclusions,
and events are added as new rows and recorded in the audit log.

![Levey-Jennings chart of simulated weekly sessions](docs/images/levey_jennings_example.png)

*Simulated weekly sessions from `python tools/demo_tracking.py OUT_DIR`: a
stable baseline, an excluded dirty-slide session, a drift caught by the
Westgard rules, and a service visit that starts new (provisional) limits.*

Charts need matplotlib: `pip install "psfwidget[report]"`.

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
