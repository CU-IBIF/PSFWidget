"""Tests for ``psfwidget analyze --db`` and the ``psfwidget track`` commands (AUT-01, WP4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.automation import cli
from psfwidget.automation.track import EXIT_REJECTION, parse_time
from psfwidget.tracking.store import ResultsDatabase

T0 = datetime(2026, 2, 2, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def fields(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """Seven sessions: six stable, the last one much wider axially."""
    folder = tmp_path_factory.mktemp("sessions")
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    rng = np.random.default_rng(11)
    paths = []
    for i in range(7):
        axial = 0.60 + 0.01 * rng.normal() if i < 6 else 0.80
        field = synthetic.make_bead_field(
            spec, 4, (axial, 0.22, 0.22), 4e4, rng, margin_fwhm=2.0, min_separation_fwhm=4.0
        )
        path = folder / f"day{i}.tif"
        tifffile.imwrite(
            path,
            field.data,
            imagej=True,
            resolution=(20, 20),
            metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
        )
        paths.append(path)
    return paths


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "scope.toml"
    path.write_text(
        '[instrument]\nid = "scope"\nmodality = "widefield"\n'
        '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
        '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
        "[tracking]\nbaseline_sessions = 5\n"
        'metrics = ["fwhm_z_um", "fwhm_x_um", "accepted_beads"]\n',
        encoding="utf-8",
    )
    return path


def _analyze(path: Path, profile: Path, db: Path, out: Path, day: int, *extra: str) -> int:
    when = (T0 + timedelta(days=day)).isoformat()
    return cli.main(
        [
            "analyze",
            str(path),
            "--profile",
            str(profile),
            "--db",
            str(db),
            "--out",
            str(out),
            "--overwrite",
            "--set",
            f"acquired_utc={when}",
            "--user",
            "tester",
            *extra,
        ]
    )


@pytest.fixture
def recorded(
    fields: list[Path], profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    db = tmp_path / "results.sqlite"
    for day, path in enumerate(fields):
        assert _analyze(path, profile, db, tmp_path / "out", 7 * day) == cli.EXIT_OK
    capsys.readouterr()
    return db


@pytest.mark.req("AUT-01", "TRK-01", "TRK-05")
def test_analyze_records_and_reports_status(
    fields: list[Path], profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "r.sqlite"
    for day, path in enumerate(fields):
        assert _analyze(path, profile, db, tmp_path / "out", 7 * day) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"Recorded as session 7 in {db}" in out
    last = out[out.rindex("Recorded as session 7") :]
    assert "scope / 63x / green / widefield  (7 sessions)" in last
    assert "fwhm_z_um" in last
    assert "REJECTION (1_2s, 1_3s)" in last
    with ResultsDatabase(db) as database:
        assert database.audit_log()[0]["user"] == "tester"


def test_analyze_db_refusals(
    fields: list[Path], profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "r.sqlite"
    assert _analyze(fields[0], profile, db, tmp_path / "o", 0) == cli.EXIT_OK
    capsys.readouterr()
    assert _analyze(fields[0], profile, db, tmp_path / "o", 0) == cli.EXIT_ERROR
    assert "already recorded as session 1" in capsys.readouterr().err
    assert _analyze(fields[0], profile, db, tmp_path / "o", 0, "--record-again") == cli.EXIT_OK
    capsys.readouterr()
    assert _analyze(fields[0], profile, db, tmp_path / "o", 0, "--no-hash") == cli.EXIT_ERROR
    assert "--db needs the file hash" in capsys.readouterr().err
    code = cli.main(
        [
            "analyze",
            str(fields[0]),
            "--db",
            str(db),
            "--out",
            str(tmp_path / "p"),
            "--set",
            "numerical_aperture=1.4",
            "--set",
            "immersion_ri=1.518",
            "--set",
            "channel.0.emission_nm=520",
        ]
    )
    assert code == cli.EXIT_ERROR
    assert "files results under an instrument" in capsys.readouterr().err


@pytest.mark.req("TRK-05")
def test_status_command(recorded: Path, profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["track", "status", "--db", str(recorded), "--profile", str(profile)]) == 0
    out = capsys.readouterr().out
    assert "scope / 63x / green / widefield  (7 sessions)" in out
    assert "REJECTION" in out
    assert "accepted_beads" in out
    code = cli.main(
        [
            "track",
            "status",
            "--db",
            str(recorded),
            "--profile",
            str(profile),
            "--fail-on-rejection",
        ]
    )
    assert code == EXIT_REJECTION
    capsys.readouterr()
    assert cli.main(["track", "status", "--db", str(recorded), "--instrument", "nope"]) == 2
    assert "no recorded results match" in capsys.readouterr().err


@pytest.mark.req("TRK-03", "TRK-07", "TRK-06", "NFR-07")
def test_events_exclusions_charts_and_audit(
    recorded: Path, profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(recorded)
    assert (
        cli.main(["track", "exclude", "--db", db, "7", "--reason", "bad slide", "--user", "ana"])
        == 0
    )
    assert "Excluded session 7." in capsys.readouterr().out
    cli.main(["track", "status", "--db", db, "--profile", str(profile)])
    axial = next(line for line in capsys.readouterr().out.splitlines() if "fwhm_z_um" in line)
    assert "REJECTION" not in axial  # the wide session no longer counts
    assert cli.main(["track", "include", "--db", db, "7", "--reason", "slide was fine"]) == 0
    assert (
        cli.main(
            [
                "track",
                "event",
                "--db",
                db,
                "--instrument",
                "scope",
                "--kind",
                "service",
                "--description",
                "laser realigned",
                "--date",
                "2026-03-10",
                "--rebaseline",
                "--baseline-sessions",
                "5",
                "--user",
                "eng",
            ]
        )
        == 0
    )
    assert "starts a new baseline" in capsys.readouterr().out

    charts = tmp_path / "charts"
    assert (
        cli.main(
            [
                "track",
                "chart",
                "--db",
                db,
                "--profile",
                str(profile),
                "--out",
                str(charts),
                "--metric",
                "fwhm_z_um",
                "--format",
                "svg",
            ]
        )
        == 0
    )
    assert (charts / "scope_63x_green_widefield_fwhm_z_um.svg").exists()
    assert (
        cli.main(["track", "chart", "--db", db, "--out", str(charts), "--metric", "fwhm_q"]) == 2
    )

    capsys.readouterr()
    assert cli.main(["track", "audit", "--db", db]) == 0
    audit = capsys.readouterr().out.splitlines()
    assert [line.split()[2] for line in audit[-3:]] == ["exclude", "include", "event"]
    assert "ana" in audit[-3]
    assert cli.main(["track", "list", "--db", db]) == 0
    assert "7 sessions, 1 events" in capsys.readouterr().out


@pytest.mark.req("TRK-04")
def test_event_baseline_options(recorded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = [
        "track",
        "event",
        "--db",
        str(recorded),
        "--instrument",
        "scope",
        "--kind",
        "baseline",
        "--description",
        "reset",
    ]
    assert cli.main([*base, "--rebaseline", "--baseline-range", "2026-02-02", "2026-03-10"]) == 0
    assert cli.main([*base, "--rebaseline", "--target", "fwhm_z_um=0.6,0.02"]) == 0
    with ResultsDatabase(recorded) as db:
        events = db.events()
    assert events[0].baseline is not None
    assert events[0].baseline["method"] == "range"
    assert events[1].baseline == {
        "method": "target",
        "sessions": 20,
        "targets": {"fwhm_z_um": [0.6, 0.02]},
    }
    capsys.readouterr()
    for extra, message in (
        (["--baseline-sessions", "5"], "need --rebaseline"),
        (["--rebaseline", "--target", "fwhm_z_um=0.6"], "targets look like"),
        (["--rebaseline", "--target", "x=1,2"], "targets look like"),
        (["--rebaseline", "--baseline-sessions", "2"], "at least 5"),
        (["--rebaseline", "--baseline-range", "2026-03-01", "2026-02-01"], "end after"),
        (["--rebaseline", "--baseline-range", "a", "b"], "not an ISO date"),
        (
            [
                "--rebaseline",
                "--baseline-range",
                "2026-01-01",
                "2026-02-01",
                "--target",
                "fwhm_z_um=0.6,0.02",
            ],
            "not both",
        ),
    ):
        assert cli.main([*base, *extra]) == cli.EXIT_ERROR
        assert message in capsys.readouterr().err


@pytest.mark.req("TRK-10")
def test_export_and_backup_commands(
    recorded: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(["track", "export", "--db", str(recorded), "--out", str(tmp_path / "all.csv")])
        == 0
    )
    assert "rows to" in capsys.readouterr().out
    assert (tmp_path / "all.csv").read_text("utf-8").startswith("session_id,acquired_utc")
    assert (
        cli.main(
            ["track", "backup", "--db", str(recorded), "--out", str(tmp_path / "copy.sqlite")]
        )
        == 0
    )
    assert (
        cli.main(
            ["track", "backup", "--db", str(recorded), "--out", str(tmp_path / "copy.sqlite")]
        )
        == cli.EXIT_ERROR
    )


def test_empty_database_and_bad_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["track", "list", "--db", str(tmp_path / "new.sqlite")]) == 0
    assert "No results recorded yet." in capsys.readouterr().out
    bogus = tmp_path / "bogus.sqlite"
    bogus.write_bytes(b"\x00" * 4096)
    assert cli.main(["track", "list", "--db", str(bogus)]) == cli.EXIT_ERROR
    assert "psfwidget: error:" in capsys.readouterr().err


def test_parse_time() -> None:
    assert parse_time("2026-03-10") == datetime(2026, 3, 10, tzinfo=UTC)
    assert parse_time("2026-03-10T08:00:00-05:00").utcoffset() == timedelta(hours=-5)
    with pytest.raises(ValueError, match="not an ISO date"):
        parse_time("yesterday")
