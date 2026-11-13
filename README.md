# PSFWidget

PSFWidget is a microscope quality-control tool for point spread functions (PSFs). It will:

- analyze empirical PSFs from 3D bead images in `.nd2`, `.lif`, `.czi`, and `.tif` files;
- compare them with theoretical PSFs;
- track performance over time on Levey-Jennings charts;
- produce scheduled, distributable reports.

> **Status:** Pre-alpha. File import works (milestone M1): PSFWidget reads
> bead stacks in all four formats and resolves their acquisition metadata.
> Bead detection and PSF measurements (M2) are next; nothing is measured yet.

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

From Python:

```python
from psfwidget.io import read_stack
from psfwidget.profiles import load_profile

stack = read_stack("beads.lif", series=2, profile=load_profile("sp8.toml"))
stack.require_analyzable()  # raises if a required value is missing
green = stack.channel(0)  # (z, y, x) array, read on request
```

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
