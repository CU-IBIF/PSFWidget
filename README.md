# PSFWidget

PSFWidget is a microscope quality-control tool for point spread functions
(PSFs). From 3D images of sub-resolution beads it:

- reads Nikon `.nd2`, Leica `.lif`, Zeiss `.czi`, and TIFF / OME-TIFF files;
- finds the beads, measures each one (FWHM in x, y, and z and in the XY, XZ,
  and YZ planes, intensity, signal-to-noise, fit quality), and rejects
  unsuitable beads with a reason;
- compares the results with the theoretical PSF of the objective and
  modality (widefield, point-scanning and spinning-disk confocal,
  multiphoton, and image-scanning modes such as NSPARC and Live-SR);
- measures the field: tilt, curvature, illumination uniformity, and
  chromatic shifts between channels; averages the beads into an empirical
  PSF for deconvolution;
- tracks every metric over time on Levey-Jennings charts with Westgard rules;
- writes session and trend reports (HTML, PDF, CSV, Excel) to network
  folders, on a schedule if wanted, with review and electronic approval;
- keeps GLP-style records: who, when, on which computer, with which
  settings, in an append-only database with an audit trail.

It has a napari GUI, a command-line interface for scheduled runs, and a
Python API.

> **Status:** pre-release (0.0.1.dev0). Milestones M0 to M4 are merged and
> M5 (GUI and the remaining "should" requirements) is in review. Validation
> with facility instruments and users (M6) has not started. See
> [docs/STATUS.md](docs/STATUS.md) for what is done and what is outstanding.

## Documentation

| Document | For |
|---|---|
| [Install guide](docs/manual/INSTALL.md) | Installing on Windows, macOS, or Linux, checking the installation, and setting up scheduled runs |
| [User manual](docs/manual/USER_MANUAL.md) | Using the GUI and the command line, profiles, tracking, reports, automation, approvals, and troubleshooting |
| [Status and outstanding items](docs/STATUS.md) | What works, what is missing, and what is waiting on whom |
| [Project plan](docs/PLAN.md) | Requirements, tasks, milestones, risks, and decisions |
| [V&V plan](docs/VV_PLAN.md) | How each requirement is verified and validated |
| [Traceability matrix](docs/traceability.csv) | Requirement → verification method → activity |
| [Fitting accuracy](docs/validation/fitting_accuracy.md) | Bias and precision of the FWHM measurement on synthetic beads |
| [Licence audit](docs/LICENSE_AUDIT.md) | Licences of all dependencies |
| [Example profiles](examples/profiles/README.md) and [automation config](examples/automation/qc.toml) | Starting points for your instruments |
| [CHANGELOG](CHANGELOG.md) | What changed |
| [CONTRIBUTING](CONTRIBUTING.md) | Developer setup, checks, and conventions |

## Quick start

Install (Python 3.12 or newer; see the [install guide](docs/manual/INSTALL.md)):

```bash
python -m pip install "psfwidget[napari] @ git+https://github.com/CU-IBIF/PSFWidget.git"
```

Check the installation:

```bash
psfwidget selftest
```

Use the GUI: start `napari`, then open *Plugins → PSFWidget → PSF analysis*.
*File → Open Sample → PSFWidget* gives a synthetic bead field to try.

Or the command line:

```bash
psfwidget analyze beads.lif --series 2 --profile examples/profiles/sp8.toml \
    --db qc.sqlite --out results/ --report html,pdf
psfwidget track status --db qc.sqlite
psfwidget track chart --db qc.sqlite --out charts/
```

![Levey-Jennings chart of simulated weekly sessions](docs/images/levey_jennings_example.png)

*Simulated weekly sessions from `python tools/demo_tracking.py OUT_DIR`: a
stable baseline, an excluded dirty-slide session, a drift caught by the
Westgard rules, and a service visit that starts new (provisional) limits.*

## Development

```bash
python -m pip install -e ".[dev]"
pre-commit install
python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks, the GUI tests, coding
standards, how tests are linked to requirements, and the release process.

## License

MIT. See [LICENSE](LICENSE).
