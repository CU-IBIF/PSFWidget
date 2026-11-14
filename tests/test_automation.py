"""Tests for the automation config, schedules, unattended runs, and alerts (WP6).

Requirements: AUT-01 to AUT-06, NFR-06.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.automation import cli, runner
from psfwidget.automation.config import ConfigError, load_config, parse_config
from psfwidget.automation.schedule import ScheduleError, parse_schedule
from psfwidget.tracking.store import ResultsDatabase

T0 = datetime(2026, 3, 2, 6, 0, tzinfo=UTC)  # a Monday
runner._PRIORITY_LOWERED = True  # do not renice the test process (tested in test_backup.py)

PROFILE = (
    '[instrument]\nid = "scope"\nmodality = "widefield"\n'
    '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
    '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
    "[tracking]\nbaseline_sessions = 5\n"
    'metrics = ["fwhm_z_um", "fwhm_x_um"]\n'
)


# ---------------------------------------------------------------- schedules


@pytest.mark.req("AUT-03")
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("every 15 minutes", "interval"),
        ("Every 2 Hours", "interval"),
        ("every 1 day", "interval"),
        ("daily 07:30", "daily"),
        ("weekly monday 7:30", "weekly"),
        ("monthly 1 23:59", "monthly"),
        ("off", "off"),
        ("", "off"),
    ],
)
def test_parse_schedule(text: str, kind: str) -> None:
    assert parse_schedule(text).kind == kind


@pytest.mark.req("AUT-03")
@pytest.mark.parametrize(
    "text",
    [
        "hourly",
        "every 0 minutes",
        "daily 25:00",
        "weekly someday 07:00",
        "monthly 31 07:00",
        "daily",
        "every minutes",
    ],
)
def test_parse_schedule_rejects(text: str) -> None:
    with pytest.raises(ScheduleError):
        parse_schedule(text)


@pytest.mark.req("AUT-03")
def test_interval_due() -> None:
    schedule = parse_schedule("every 15 minutes")
    assert schedule.is_due(T0, None, T0)
    assert not schedule.is_due(T0 + timedelta(minutes=14), T0, T0)
    assert schedule.is_due(T0 + timedelta(minutes=15), T0, T0)


@pytest.mark.req("AUT-03")
def test_calendar_schedules_wait_for_their_time() -> None:
    weekly = parse_schedule("weekly monday 07:30")
    start = datetime(2026, 3, 2, 6, 0)  # Monday 06:00, naive local
    assert not weekly.is_due(start, None, start)  # not yet 07:30
    at = datetime(2026, 3, 2, 7, 31)
    assert weekly.is_due(at, None, start)
    assert not weekly.is_due(at + timedelta(days=3), at, start)  # already ran this week
    assert weekly.is_due(at + timedelta(days=7), at, start)
    monthly = parse_schedule("monthly 15 08:00")
    assert monthly.latest_occurrence(datetime(2026, 3, 10)) == datetime(2026, 2, 15, 8, 0)
    assert monthly.latest_occurrence(datetime(2026, 1, 10)) == datetime(2025, 12, 15, 8, 0)
    daily = parse_schedule("daily 07:30")
    assert daily.latest_occurrence(datetime(2026, 3, 2, 7, 0)) == datetime(2026, 3, 1, 7, 30)
    assert not parse_schedule("off").is_due(at, None, start)


# ---------------------------------------------------------------- config


def _write_config(folder: Path, body: str = "", watch: str | None = None) -> Path:
    (folder / "scope.toml").write_text(PROFILE, encoding="utf-8")
    (folder / "incoming").mkdir(exist_ok=True)
    watch = (
        watch
        if watch is not None
        else (
            '[[watch]]\nfolder = "incoming"\nprofile = "scope.toml"\npatterns = ["*.tif"]\n'
            "settle_seconds = 60\n"
        )
    )
    path = folder / "automation.toml"
    path.write_text(
        '[database]\npath = "qc.sqlite"\n[output]\nroot = "reports"\nreports = ["html"]\n'
        'exports = ["csv", "xlsx"]\n' + watch + body,
        encoding="utf-8",
    )
    return path


@pytest.mark.req("AUT-05")
def test_load_config_resolves_relative_paths(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, '[schedule]\ntrend_report = "daily 07:00"\n'))
    assert config.database == tmp_path / "qc.sqlite"
    assert config.output.root == tmp_path / "reports"
    assert config.watches[0].folder == tmp_path / "incoming"
    assert config.watches[0].profile.instrument_id == "scope"
    assert config.watches[0].series is None
    assert config.ingest.text == "every 15 minutes"
    assert config.trend_report.kind == "daily"
    assert config.status_file == tmp_path / "reports" / "STATUS.json"
    assert config.state_dir == tmp_path / "reports" / "_psfwidget"


@pytest.mark.req("AUT-05")
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"bogus": {}}, "unknown table"),
        ({"output": {"colour": "red"}}, "unknown key"),
        ({"output": {"reports": ["docx"]}}, "unknown reports"),
        ({"output": {"page_size": "legal"}}, "page_size"),
        ({"output": {"session_template": "{nope}"}}, "[output]"),
        ({"schedule": {"ingest": "sometimes"}}, "[schedule]"),
        ({"schedule": {"trend_days": 0}}, "trend_days"),
        ({"analysis": {"min_snr": "high"}}, "[analysis]"),
        ({"watch": [{"folder": "x"}]}, "'profile'"),
        ({"watch": [{"folder": "x", "profile": "missing.toml"}]}, "not found"),
        ({"watch": [{"folder": "x", "profile": "scope.toml", "series": "some"}]}, "'series'"),
        ({"watch": [{"folder": "x", "profile": "scope.toml", "patterns": []}]}, "'patterns'"),
        ({"watch": [{"folder": "x", "profile": "scope.toml", "settle_seconds": -1}]}, "settle"),
        ({"branding": {"logo": "nope.png"}}, "logo"),
    ],
)
def test_config_errors_are_clear(tmp_path: Path, data: dict[str, Any], message: str) -> None:
    (tmp_path / "scope.toml").write_text(PROFILE, encoding="utf-8")
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_config(data, tmp_path / "automation.toml")


@pytest.mark.req("AUT-05")
def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[database\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"broken\.toml"):
        load_config(path)


# ---------------------------------------------------------------- unattended runs


SPEC = synthetic.FieldSpec(
    shape=(31, 96, 96),
    voxel_size_um=(0.15, 0.05, 0.05),
    background_photons=20,
    offset=100,
    read_noise=2,
    bit_depth=12,
)


def _drop(folder: Path, name: str, when: datetime, axial: float = 0.6, seed: int = 0) -> Path:
    """Write a synthetic bead field whose file time is ``when`` (used as acquisition date)."""
    rng = np.random.default_rng(seed)
    field = synthetic.make_bead_field(
        SPEC, 4, (axial, 0.22, 0.22), 4e4, rng, margin_fwhm=2.0, min_separation_fwhm=4.0
    )
    path = folder / name
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


@pytest.mark.req("AUT-02", "AUT-04", "AUT-06", "NFR-06")
def test_ingest_records_reports_and_isolates_errors(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    incoming = tmp_path / "incoming"
    _drop(incoming, "day0.tif", T0 - timedelta(hours=1))
    (incoming / "corrupt.tif").write_bytes(b"not a tiff at all")
    os.utime(incoming / "corrupt.tif", ((T0 - timedelta(hours=1)).timestamp(),) * 2)
    _drop(incoming, "fresh.tif", T0 - timedelta(seconds=10), seed=1)  # still settling
    (incoming / "notes.txt").write_text("ignored", encoding="utf-8")

    summaries = runner.run_due(config, now=T0)
    assert [s.job for s in summaries] == ["ingest"]
    ingest = summaries[0]
    assert [Path(s["file"]).name for s in ingest.sessions] == ["day0.tif"]
    assert [Path(e["file"]).name for e in ingest.errors] == ["corrupt.tif"]
    assert any(s["reason"] == "still being written" for s in ingest.skipped)
    outputs = [Path(p) for p in ingest.sessions[0]["outputs"]]
    assert {p.suffix for p in outputs} == {".html", ".csv", ".json", ".xlsx"}
    assert all(p.is_file() and p.is_relative_to(config.output.root / "scope") for p in outputs)

    runs = sorted((config.state_dir / "runs").glob("*_ingest.json"))
    assert len(runs) == 1
    record = json.loads(runs[0].read_text("utf-8"))
    assert record["job"] == "ingest"
    assert len(record["errors"]) == 1
    assert "day0.tif" in (config.state_dir / "psfwidget.log").read_text("utf-8")
    status = json.loads(config.status_file.read_text("utf-8"))
    assert status["run_errors"] == 1
    assert "corrupt.tif" in config.alert_file.read_text("utf-8")
    assert not (config.state_dir / "run.lock").exists()

    # Not due again for 15 minutes; then the settled file is picked up and the
    # corrupt one is not retried (it has not changed), so the alert clears.
    assert runner.run_due(config, now=T0 + timedelta(minutes=5)) == []
    later = runner.run_due(config, now=T0 + timedelta(minutes=16))
    assert [Path(s["file"]).name for s in later[0].sessions] == ["fresh.tif"]
    assert later[0].errors == []
    assert not config.alert_file.exists()


@pytest.mark.req("AUT-02")
def test_ingest_skips_files_already_in_the_database(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    path = _drop(tmp_path / "incoming", "day0.tif", T0 - timedelta(hours=1))
    runner.run_due(config, now=T0)
    copy = tmp_path / "incoming" / "copy.tif"
    copy.write_bytes(path.read_bytes())
    os.utime(copy, ((T0 - timedelta(hours=1)).timestamp(),) * 2)
    summary = runner.run_due(config, now=T0 + timedelta(hours=1))[0]
    assert summary.sessions == []
    assert any(s.get("reason") == "already recorded" for s in summary.skipped)


@pytest.mark.req("AUT-04", "AUT-06")
def test_missing_watch_folder_is_an_error_not_a_crash(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    (tmp_path / "incoming").rmdir()
    summary = runner.run_due(load_config(path), now=T0)[0]
    assert "does not exist" in summary.errors[0]["error"]
    assert load_config(path).alert_file.exists()


@pytest.mark.req("AUT-03")
def test_overlapping_runs_are_refused(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.state_dir.mkdir(parents=True)
    lock = config.state_dir / "run.lock"
    lock.write_text(json.dumps({"since": T0.isoformat()}), encoding="utf-8")
    with pytest.raises(runner.RunLockedError):
        runner.run_due(config, now=T0 + timedelta(minutes=1))
    # A lock left by a crashed run is taken over once stale.
    assert runner.run_due(config, now=T0 + timedelta(hours=7))
    with pytest.raises(ValueError, match="unknown job"):
        runner.run_due(config, now=T0, force=("cleanup",))


@pytest.mark.req("AUT-02", "AUT-03", "AUT-04", "AUT-06", "REP-02")
def test_four_weeks_unattended(tmp_path: Path) -> None:
    """A compressed soak test: daily acquisitions, ingest every 15 minutes, weekly trends.

    The fifth week's acquisition is much wider axially, so it breaks a
    Westgard rejection rule and raises the alert.
    """
    path = _write_config(tmp_path, '[schedule]\ntrend_report = "weekly monday 07:30"\n')
    config = load_config(path)
    incoming = tmp_path / "incoming"
    rng = np.random.default_rng(5)
    runs = 0
    clock = T0
    for day in range(29):
        acquired = T0 + timedelta(days=day, hours=1)
        axial = 0.80 if day == 28 else 0.60 + 0.004 * rng.normal()
        _drop(incoming, f"day{day:02d}.tif", acquired, axial=axial, seed=day)
        # The scheduler wakes every 15 minutes through the day.
        end = T0 + timedelta(days=day + 1)
        while clock < end:
            if os.path.getmtime(incoming / f"day{day:02d}.tif") <= clock.timestamp():
                runs += len(runner.run_due(config, now=clock))
            clock += timedelta(hours=4)
    with ResultsDatabase(config.database) as db:
        assert db.session_count(db.configurations()[0]) == 29
    trends = sorted(config.output.root.glob("scope/trend/*_trend.html"))
    assert len(trends) == 4  # four Mondays after the first one
    status = json.loads(config.status_file.read_text("utf-8"))
    assert status["overall"] == "FAIL"
    assert "Westgard" in config.alert_file.read_text("utf-8")
    run_files = list((config.state_dir / "runs").glob("*.json"))
    assert len(run_files) == runs


# ---------------------------------------------------------------- CLI


@pytest.mark.req("AUT-01", "AUT-05")
def test_cli_run_and_check_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_config(tmp_path)
    _drop(tmp_path / "incoming", "day0.tif", datetime.now(UTC) - timedelta(hours=1))
    assert cli.main(["check-config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "valid" in out
    assert "due now:      ingest" in out
    assert cli.main(["run", "--config", str(path)]) == 0
    assert "session 1: day0.tif series 0" in capsys.readouterr().out
    assert cli.main(["run", "--config", str(path), "--job", "trend_report"]) == 0
    assert "trend_report: 0 session(s)" in capsys.readouterr().out
    assert list((tmp_path / "reports" / "scope" / "trend").glob("*.html"))
    assert cli.main(["check-config", str(tmp_path / "missing.toml")]) == 2


@pytest.mark.req("AUT-03")
def test_watch_loop_runs_until_stopped(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    seen: list[list[runner.RunSummary]] = []
    sleeps: list[float] = []
    runner.watch(config, poll_seconds=30, iterations=3, on_run=seen.append, sleep=sleeps.append)
    assert len(seen) == 1  # ingest ran once; not due again within the loop
    assert sleeps == [30, 30]


@pytest.mark.req("AUT-05")
def test_example_config_is_valid() -> None:
    example = Path(__file__).parents[1] / "examples" / "automation" / "qc.toml"
    config = load_config(example)
    assert [w.profile.instrument_id for w in config.watches] == ["sp8", "axr-nsparc"]
    assert config.trend_report.kind == "weekly"
