"""Build a demonstration results database from simulated weekly QC sessions.

Simulates 36 weekly bead acquisitions of one instrument: stable for 24 weeks
(with one dirty-slide session that is excluded), a gradual axial drift over
6 weeks, then a service visit that resets the baseline. Every session goes
through the real analysis pipeline and is recorded, so the database, status
report, and charts are exactly what real data would give.

Usage::

    python tools/demo_tracking.py OUT_DIR             # database and charts in OUT_DIR
    psfwidget track status --db OUT_DIR/demo.sqlite   # then explore it
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.automation.track import series_for
from psfwidget.io import parse_assignments, read_stack
from psfwidget.tracking.chart import draw
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import EventKind, ResultsDatabase

START = datetime(2026, 1, 5, 9, tzinfo=UTC)
SESSIONS = 36
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
    "channel.0.name=green",
    "instrument_id=demo-scope",
    "objective_id=63x",
    "bead_diameter_nm=100",
]


def axial_fwhm(week: int, rng: np.random.Generator) -> float:
    """True axial FWHM of the simulated instrument in a given week."""
    if week >= 30:
        return 0.56 + 0.012 * rng.normal()  # after service
    value = 0.60 + 0.012 * rng.normal()
    if week >= 24:
        value += 0.004 * (week - 23) ** 1.6  # gradual drift
    if week == 12:
        value += 0.06  # one-off disturbance
    return value


def build(out: Path) -> Path:
    """Create ``out/demo.sqlite`` and charts in ``out``; return the database path."""
    out.mkdir(parents=True, exist_ok=True)
    database = out / "demo.sqlite"
    if database.exists():
        raise FileExistsError(f"{database} exists; choose an empty folder")
    images = out / "images"
    images.mkdir(exist_ok=True)
    rng = np.random.default_rng(3)
    spec = synthetic.FieldSpec(
        shape=(31, 160, 160),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    with ResultsDatabase(database) as db:
        for week in range(SESSIONS):
            field = synthetic.make_bead_field(
                spec, 10, (axial_fwhm(week, rng), 0.22, 0.22), 4e4, rng
            )
            path = images / f"week{week:02d}.tif"
            tifffile.imwrite(
                path,
                field.data,
                imagej=True,
                resolution=(20, 20),
                metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
            )
            when = (START + timedelta(days=7 * week)).isoformat()
            stack = read_stack(
                path, overrides=parse_assignments([*OPTICS, f"acquired_utc={when}"])
            )
            session = db.record(analyze_stack(stack), user="demo")
            if week == 18:
                db.set_excluded(session, True, "slide was dirty", user="demo")
        db.add_event(
            occurred=START + timedelta(days=7 * 9 + 2),
            instrument_id="demo-scope",
            kind=EventKind.OTHER,
            description="room temperature excursion",
            user="demo",
        )
        db.add_event(
            occurred=START + timedelta(days=7 * 29 + 3),
            instrument_id="demo-scope",
            kind=EventKind.SERVICE,
            description="laser realigned by engineer",
            rebaseline=True,
            user="demo",
        )
        (configuration,) = db.configurations()
        for metric in ("fwhm_z_um", "fwhm_x_um", "integrated_intensity"):
            series = series_for(db, configuration, metric, TrackingSettings())
            draw(series, out / f"levey_jennings_{metric}.png")
    return database


def main(argv: list[str] | None = None) -> int:
    """Build the demo database."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("out", type=Path, help="empty output folder")
    args = parser.parse_args(argv)
    database = build(args.out)
    print(f"wrote {database} and charts in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
