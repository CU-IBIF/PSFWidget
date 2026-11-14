"""Tests for database backups, the network-share check, and process priority (AUT-07, NFR-11)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from psfwidget.automation import backup, runner
from psfwidget.automation.config import ConfigError, load_config, parse_config
from psfwidget.tracking.store import ResultsDatabase

T0 = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)


def _database(path: Path) -> Path:
    with ResultsDatabase(path) as db:
        signed = path.with_name("r.html")
        signed.write_text("x", encoding="utf-8")
        db.approve(signed, approved=True, meaning="reviewed")
    return path


@pytest.mark.req("AUT-07", "QA-04")
def test_backup_is_verified_and_retention_applies(tmp_path: Path) -> None:
    database = _database(tmp_path / "qc.sqlite")
    folder = tmp_path / "share" / "backups"
    made = [backup.backup_database(database, folder, 2, T0 + timedelta(days=d)) for d in range(3)]
    assert made[0].path.name == "qc_20260302T010000Z.sqlite"
    remaining = sorted(p.name for p in folder.glob("*.sqlite"))
    assert remaining == [made[1].path.name, made[2].path.name]
    assert made[2].removed == (made[0].path,)
    assert not made[0].path.with_name(made[0].path.name + ".sha256").exists()
    assert backup.verify(made[2].path) == made[2].sha256
    with ResultsDatabase(made[2].path) as copy:
        assert len(copy.approvals()) == 1
    # Corruption of a stored backup is detected.
    data = bytearray(made[2].path.read_bytes())
    data[-10] ^= 0xFF
    made[2].path.write_bytes(bytes(data))
    with pytest.raises(backup.BackupError):
        backup.verify(made[2].path)
    (folder / "unrelated.sqlite").write_bytes(b"keep me")
    backup.backup_database(database, folder, 1, T0 + timedelta(days=9))
    assert (folder / "unrelated.sqlite").exists()  # only our own backups are removed
    with pytest.raises(backup.BackupError, match="no database"):
        backup.backup_database(tmp_path / "missing.sqlite", folder, 1, T0)


@pytest.mark.req("AUT-07", "AUT-04")
def test_scheduled_backup_job(tmp_path: Path) -> None:
    _database(tmp_path / "qc.sqlite")
    (tmp_path / "automation.toml").write_text(
        '[database]\npath = "qc.sqlite"\n[output]\nroot = "reports"\n'
        '[schedule]\ningest = "off"\nlow_priority = false\n'
        '[backup]\nfolder = "share"\nschedule = "daily 02:00"\nkeep = 3\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path / "automation.toml")
    assert config.backup.kind == "daily"
    assert config.backup_folder == tmp_path / "share"
    summaries = runner.run_due(config, now=T0, force=("backup",))
    assert [s.job for s in summaries] == ["backup"]
    assert summaries[0].errors == []
    written = Path(summaries[0].reports[0])
    assert written.parent == tmp_path / "share"
    record = json.loads(next((config.state_dir / "runs").glob("*_backup.json")).read_text())
    assert record["reports"] == [str(written)]
    # A failed backup is an error in the run summary and raises the alert.
    (tmp_path / "qc.sqlite").unlink()
    failed = runner.run_due(config, now=T0 + timedelta(days=1), force=("backup",))[0]
    assert "no database" in failed.errors[0]["error"]
    assert config.alert_file.exists()


@pytest.mark.req("AUT-07", "AUT-05")
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"backup": {"schedule": "daily 02:00"}}, "needs a 'folder'"),
        ({"backup": {"folder": "b", "keep": 0}}, "keep"),
        ({"backup": {"folder": "b", "schedule": "sometimes"}}, "backup"),
        ({"schedule": {"low_priority": "yes"}}, "low_priority"),
    ],
)
def test_backup_config_errors(
    tmp_path: Path, data: dict[str, dict[str, object]], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data, tmp_path / "automation.toml")


@pytest.mark.req("NFR-11")
def test_database_on_a_network_share_is_refused(tmp_path: Path) -> None:
    assert backup.is_network_path(Path("//server/qc/qc.sqlite"))
    assert backup.is_network_path(Path("\\\\server\\qc\\qc.sqlite"))
    assert not backup.is_network_path(tmp_path / "qc.sqlite")
    with pytest.raises(ConfigError, match="network share"):
        parse_config({"database": {"path": "//server/qc/qc.sqlite"}}, tmp_path / "a.toml")


@pytest.mark.req("NFR-11")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
def test_lower_priority_in_a_child_process() -> None:
    import subprocess

    code = (
        "import os; from psfwidget.automation.backup import lower_priority, NICE_INCREMENT; "
        "b = os.nice(0); ok = lower_priority(); print(b, os.nice(0), ok, NICE_INCREMENT)"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    ).stdout.split()
    before, after, ok, step = int(output[0]), int(output[1]), output[2], int(output[3])
    assert ok == "True"
    assert after == min(before + step, 19)
