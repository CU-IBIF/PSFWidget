"""Tests for importing (merging) results databases (TRK-10)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import FieldResult, analyze_stack
from psfwidget.automation import cli
from psfwidget.io import parse_assignments, read_stack
from psfwidget.tracking.merge import import_database
from psfwidget.tracking.store import EventKind, ResultsDatabase, TrackingError

T0 = datetime(2026, 9, 1, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> FieldResult:
    folder = tmp_path_factory.mktemp("merge")
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        5,
        (0.6, 0.22, 0.22),
        4e4,
        np.random.default_rng(2),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    path = folder / "beads.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    stack = read_stack(
        path,
        overrides=parse_assignments(
            [
                "numerical_aperture=1.4",
                "immersion_ri=1.518",
                "modality=widefield",
                "channel.0.emission_nm=520",
                "instrument_id=scope",
            ]
        ),
    )
    return analyze_stack(stack)


def _session(result: FieldResult, number: int) -> FieldResult:
    meta = parse_assignments([f"acquired_utc={(T0 + timedelta(days=number)).isoformat()}"])
    from psfwidget.io.metadata import apply_overrides

    return replace(
        result,
        metadata=apply_overrides(result.metadata, meta, "user"),
        provenance=replace(result.provenance, sha256=f"{number:064d}"),
    )


def _populate(path: Path, result: FieldResult, numbers: range, user: str) -> None:
    with ResultsDatabase(path) as db:
        for number in numbers:
            db.record(_session(result, number), user=user)
        db.add_event(
            occurred=T0 + timedelta(days=numbers[0], hours=1),
            instrument_id="scope",
            kind=EventKind.SERVICE,
            description=f"service by {user}",
            user=user,
        )


@pytest.mark.req("TRK-10", "NFR-07", "QA-01")
def test_import_merges_and_is_idempotent(result: FieldResult, tmp_path: Path) -> None:
    source, target = tmp_path / "pc1.sqlite", tmp_path / "pc2.sqlite"
    _populate(source, result, range(0, 3), "alice")
    report = tmp_path / "r.html"
    report.write_text("report", encoding="utf-8")
    with ResultsDatabase(source) as db:
        db.set_excluded(2, True, "dirty slide", user="alice")
        db.approve(report, approved=True, meaning="reviewed", user="alice", session_id=1)
    _populate(target, result, range(3, 5), "bob")

    with ResultsDatabase(target) as db:
        dry = import_database(db, source, user="carol", dry_run=True)
        assert dry.copied["sessions"] == 3
        assert len(db.audit_log()) == 3  # nothing written
        summary = import_database(db, source, user="carol")
        assert summary.copied == {
            "sessions": 3,
            "events": 1,
            "exclusions": 1,
            "approvals": 1,
            "audit_log": 6,
        }
        configuration = db.configurations()[0]
        points = db.points(configuration, "fwhm_x_um")
        assert len(points) == 5
        assert [p.excluded for p in points].count(True) == 1
        assert db.approvals_for(report)[0]["user"] == "alice"
        imported = db.session(points[0].session_id)
        assert imported["host"] is not None
        log = db.audit_log()
        assert log[-1]["action"] == "import"
        assert log[-1]["user"] == "carol"
        assert log[-1]["details"]["source_sha256"] == summary.source_sha256
        assert any(e["action"] == "record (imported)" and e["user"] == "alice" for e in log)
        again = import_database(db, source, user="carol")
        assert sum(again.copied.values()) == 0
        assert again.skipped["sessions"] == 3
        assert len(db.points(configuration, "fwhm_x_um")) == 5
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db._connection.execute("DELETE FROM sessions")


@pytest.mark.req("TRK-10")
def test_backup_imported_into_an_empty_database_is_complete(
    result: FieldResult, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "pc1.sqlite"
    _populate(source, result, range(0, 4), "alice")
    copy = tmp_path / "backup.sqlite"
    with ResultsDatabase(source) as db:
        db.backup(copy)
    empty = tmp_path / "new.sqlite"
    assert cli.main(["track", "import", "--db", str(empty), "--from", str(copy)]) == 0
    assert "copied 4 sessions" in capsys.readouterr().out
    with ResultsDatabase(source) as a, ResultsDatabase(empty) as b:
        uuids = [
            sorted(r[0] for r in d._connection.execute("SELECT uuid FROM sessions"))
            for d in (a, b)
        ]
        assert uuids[0] == uuids[1]
        for table in ("channel_results", "metrics", "beads", "events"):
            counts = [
                d._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for d in (a, b)
            ]
            assert counts[0] == counts[1], table


@pytest.mark.req("TRK-10")
def test_import_errors(result: FieldResult, tmp_path: Path) -> None:
    target = tmp_path / "t.sqlite"
    with ResultsDatabase(target) as db:
        with pytest.raises(TrackingError, match="no database"):
            import_database(db, tmp_path / "missing.sqlite")
        with pytest.raises(TrackingError, match="into itself"):
            import_database(db, target)
        other = tmp_path / "other.sqlite"
        sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
        with pytest.raises(TrackingError, match="not a PSFWidget"):
            import_database(db, other)
