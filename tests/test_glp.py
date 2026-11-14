"""Tests for the GLP features (QA-01 to QA-06) and the schema migration."""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from psfwidget.automation import cli
from psfwidget.automation.glp import approve, selftest, signature_path
from psfwidget.tracking import schema
from psfwidget.tracking.store import ResultsDatabase, TrackingError

V1_TABLES = schema._TABLES[: schema._TABLES.index("CREATE TABLE approvals")]


def _v1_database(path: Path) -> Path:
    """Create a version 1 database as M3 and M4 wrote them."""
    tables = V1_TABLES.replace(
        ",\n    host TEXT,\n    profile_sha256 TEXT,\n    config_sha256 TEXT", ""
    ).replace(",\n    host TEXT\n);", "\n);")
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(
            tables + schema._append_only_triggers(schema.APPEND_ONLY_TABLES[:-1])
        )
        connection.execute("INSERT INTO schema_version (version) VALUES (1)")
        connection.execute(
            "INSERT INTO audit_log (utc, user, action, details_json) VALUES "
            "('2026-01-01T00:00:00+00:00', 'old', 'record', '{}')"
        )
    connection.close()
    return path


@pytest.mark.req("QA-01", "NFR-07")
def test_version_1_database_is_migrated_in_place(tmp_path: Path) -> None:
    path = _v1_database(tmp_path / "old.sqlite")
    connection = sqlite3.connect(path)
    columns = {r[1] for r in connection.execute("PRAGMA table_info(sessions)")}
    connection.close()
    assert "host" not in columns
    with ResultsDatabase(path) as db:
        log = db.audit_log()
        assert log[0]["user"] == "old"
        assert log[0]["host"] is None  # old rows are not changed
        assert log[-1]["action"] == "schema migrated"
        assert log[-1]["host"] == socket.gethostname()
        signed = tmp_path / "r.html"
        signed.write_text("x", encoding="utf-8")
        db.approve(signed, approved=True, meaning="reviewed")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db._connection.execute("DELETE FROM approvals")
    with ResultsDatabase(path) as db:  # opening again does not migrate again
        assert [e["action"] for e in db.audit_log()].count("schema migrated") == 1


@pytest.fixture
def report(tmp_path: Path) -> Path:
    path = tmp_path / "session_report.html"
    path.write_text("<html>report</html>", encoding="utf-8")
    return path


@pytest.mark.req("QA-02", "QA-01")
def test_approval_is_tied_to_the_report_content(tmp_path: Path, report: Path) -> None:
    database = tmp_path / "qc.sqlite"
    record, signature = approve(
        database, report, approved=True, meaning="reviewed", comment="fine", user="ab"
    )
    assert record["decision"] == "approved"
    assert record["revision"] == 1
    assert record["host"] == socket.gethostname()
    assert signature == signature_path(report)
    assert json.loads(signature.read_text("utf-8"))["report_sha256"] == record["report_sha256"]
    with ResultsDatabase(database) as db:
        assert len(db.approvals_for(report)) == 1
        assert db.audit_log()[-1]["action"] == "report approved"
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db._connection.execute("UPDATE approvals SET decision = 'rejected'")
        with pytest.raises(TrackingError, match="meaning"):
            db.approve(report, approved=True, meaning=" ")
        with pytest.raises(TrackingError, match="no report"):
            db.approve(tmp_path / "missing.pdf", approved=True, meaning="x")
        with pytest.raises(TrackingError, match="no session"):
            db.approve(report, approved=True, meaning="x", session_id=99)
    # A second review gets the next revision and its own signature file.
    second, second_file = approve(database, report, approved=False, meaning="re-reviewed")
    assert second["revision"] == 2
    assert second["decision"] == "rejected"
    assert second_file != signature


@pytest.mark.req("QA-02")
def test_approve_and_verify_commands(
    tmp_path: Path, report: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = str(tmp_path / "qc.sqlite")
    assert cli.main(["verify", str(report), "--db", database]) == 7
    assert "has not been signed" in capsys.readouterr().out
    assert cli.main(["approve", str(report), "--db", database, "--user", "ab"]) == 0
    assert "Approved by ab" in capsys.readouterr().out
    assert cli.main(["verify", str(report), "--db", database]) == 0
    assert "approved by ab" in capsys.readouterr().out
    report.write_text("<html>edited</html>", encoding="utf-8")
    assert cli.main(["verify", str(report), "--db", database]) == 7
    assert "CHANGED" in capsys.readouterr().out
    assert cli.main(["approve", str(report), "--db", database, "--session", "4"]) == 2


@pytest.mark.req("QA-06")
def test_audit_export_for_a_period(
    tmp_path: Path, report: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "qc.sqlite"
    approve(database, report, approved=True, meaning="reviewed", user="ab")
    now = datetime.now(UTC)
    with ResultsDatabase(database) as db:
        assert len(db.audit_entries(now - timedelta(hours=1), now + timedelta(hours=1))) == 1
        assert db.audit_entries(now + timedelta(hours=1)) == []
    out = tmp_path / "audit.csv"
    assert (
        cli.main(
            [
                "track",
                "audit",
                "--db",
                str(database),
                "--csv",
                str(out),
                "--since",
                (now - timedelta(days=1)).date().isoformat(),
            ]
        )
        == 0
    )
    lines = out.read_text("utf-8").splitlines()
    assert lines[0] == "id,utc,user,host,action,details"
    assert len(lines) == 2
    assert cli.main(["track", "audit", "--db", str(database)]) == 0
    assert f"ab@{socket.gethostname()}" in capsys.readouterr().out


@pytest.mark.req("QA-03")
def test_profile_and_config_hashes(tmp_path: Path) -> None:
    import hashlib

    from psfwidget.automation.config import load_config
    from psfwidget.profiles import load_profile

    profile_path = tmp_path / "p.toml"
    profile_path.write_text('[instrument]\nid = "x"\n', encoding="utf-8")
    profile = load_profile(profile_path)
    assert profile.sha256 == hashlib.sha256(profile_path.read_bytes()).hexdigest()
    config_path = tmp_path / "c.toml"
    config_path.write_text('[output]\nroot = "out"\n', encoding="utf-8")
    config = load_config(config_path)
    assert config.sha256 == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert config.state_dir == tmp_path / "out" / "_psfwidget"


@pytest.mark.req("QA-05", "NFR-01", "NFR-03")
def test_selftest_passes_and_writes_a_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passed, record, target = selftest(tmp_path)
    assert passed, record["checks"]
    assert target is not None
    saved = json.loads(target.read_text("utf-8"))
    assert saved["result"] == "PASS"
    assert saved["environment"]["host"] == socket.gethostname()
    assert set(saved["sign_off"]) == {"performed_by", "reviewed_by", "date", "comments"}
    assert len(saved["checks"]) == 5
    assert cli.main(["selftest"]) == 0
    assert "Result: PASS" in capsys.readouterr().out
