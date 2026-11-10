# PSFWidget

PSFWidget is a microscope quality-control tool for point spread functions (PSFs). It will:

- analyze empirical PSFs from 3D bead images in `.nd2`, `.lif`, `.czi`, and `.tif` files;
- compare them with theoretical PSFs;
- track performance over time on Levey-Jennings charts;
- produce scheduled, distributable reports.

> **Status:** Pre-alpha. Milestone M0 (foundation) is in progress. The package
> skeleton, CI, and requirements traceability are in place; no analysis
> features exist yet.

## Planning documents

- [Project Plan, Requirements, Tasks & Milestones](docs/PLAN.md)
- [Verification & Validation Plan](docs/VV_PLAN.md)
- [Requirements traceability matrix](docs/traceability.csv)

## Development

You need Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
pre-commit install
python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks, coding standards, how
tests are linked to requirements, and the release process.

## License

MIT. See [LICENSE](LICENSE).
