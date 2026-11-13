"""Tests for the results database (TRK-01, TRK-02, TRK-06, TRK-07, TRK-10, NFR-07)."""

from __future__ import annotations

import csv
import os
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import FieldResult, analyze_stack
from psfwidget.io import parse_assignments, read_stack
from psfwidget.profiles.spec import SpecLimits
from psfwidget.tracking.schema import APPEND_ONLY_TABLES, SchemaError
from psfwidget.tracking.store import (
    Configuration,
    EventKind,
    ResultsDatabase,
    TrackingError,
    configurations_matching,
)

T0 = datetime(2026, 3, 2, 9, tzinfo=UTC)
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=confocal",
    "channel.0.emission_nm=520",
    "channel.0.excitation_nm=488",
    "channel.0.pinhole_airy=1.0",
    "channel.0.preset_id=green",
    "channel.1.emission_nm=600",
    "channel.1.excitation_nm=561",
    "channel.1.pinhole_airy=1.0",
    "channel.1.name=orange",
    "objective_id=63x",
]

Analyse = Callable[..., FieldResult]


@pytest.fixture(scope="module")
def tiffs(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    folder = tmp_path_factory.mktemp("fields")
    spec = synthetic.FieldSpec(
        shape=(25, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    rng = np.random.default_rng(7)
    paths = []
    for i in range(3):
        field = synthetic.make_bead_field(
            spec,
            3,
            (0.6, 0.22 + 0.01 * i, 0.22),
            4e4,
            rng,
            margin_fwhm=2.0,
            min_separation_fwhm=4.5,
        )
        data = np.stack([field.data, field.data], axis=1)
        path = folder / f"field{i}.tif"
        tifffile.imwrite(
            path,
            data,
            imagej=True,
            resolution=(20, 20),
            metadata={"axes": "ZCYX", "spacing": 0.15, "unit": "um"},
        )
        paths.append(path)
    return paths


@pytest.fixture
def analyse(tiffs: list[Path]) -> Analyse:
    def run(
        index: int,
        *,
        day: float | None = 0.0,
        instrument: str | None = "scope",
        extra: list[str] | None = None,
        hash_file: bool = True,
    ) -> FieldResult:
        values = list(OPTICS) + (extra or [])
        if instrument is not None:
            values.append(f"instrument_id={instrument}")
        if day is not None:
            values.append(f"acquired_utc={(T0 + timedelta(days=day)).isoformat()}")
        stack = read_stack(
            tiffs[index], overrides=parse_assignments(values), compute_hash=hash_file
        )
        return analyze_stack(stack, spec_limits=SpecLimits(lateral_ratio_max=1.5))

    return run


@pytest.fixture
def db(tmp_path: Path) -> Iterator[ResultsDatabase]:
    database = ResultsDatabase(tmp_path / "results.sqlite")
    yield database
    database.close()


@pytest.mark.req("TRK-01", "TRK-02")
def test_record_and_read_back(db: ResultsDatabase, analyse: Analyse) -> None:
    first = db.record(analyse(0, day=0), user="ana")
    second = db.record(analyse(1, day=7), user="ana")
    assert (first, second) == (1, 2)

    configurations = db.configurations()
    assert configurations == [
        Configuration("scope", "63x", "green", "confocal"),
        Configuration("scope", "63x", "orange", "confocal"),
    ]
    green = configurations[0]
    assert green.label == "scope / 63x / green / confocal"
    assert green.slug == "scope_63x_green_confocal"
    assert db.session_count(green) == 2

    points = db.points(green, "fwhm_x_um")
    assert [p.session_id for p in points] == [1, 2]
    assert [p.acquired_utc for p in points] == [T0, T0 + timedelta(days=7)]
    assert all(p.date_source == "metadata" and p.value is not None for p in points)
    assert points[1].value > points[0].value  # type: ignore[operator]
    assert db.points(green, "accepted_beads")[0].value == 3.0
    assert db.points(green, "no_such_metric")[0].value is None

    session = db.session(1)
    assert session["file_name"] == "field0.tif"
    assert len(session["file_sha256"]) == 64
    assert session["software_version"]
    assert '"profile_width_px": 3' in session["settings_json"]
    assert '"lateral_ratio_max": 1.5' in session["spec_limits_json"]
    assert db.latest_spec_limits(green) == SpecLimits(lateral_ratio_max=1.5)
    with pytest.raises(TrackingError, match="no session 9"):
        db.session(9)


@pytest.mark.req("TRK-01")
def test_beads_and_summaries_are_stored(db: ResultsDatabase, analyse: Analyse) -> None:
    session_id = db.record(analyse(0))
    connection = sqlite3.connect(db.path)
    beads = connection.execute(
        "SELECT COUNT(*) FROM beads b JOIN channel_results c ON c.id = b.channel_result_id "
        "WHERE c.session_id = ?",
        (session_id,),
    ).fetchone()[0]
    metrics = {row[0] for row in connection.execute("SELECT DISTINCT metric FROM metrics")}
    theory = connection.execute("SELECT theory_model FROM channel_results").fetchone()[0]
    connection.close()
    assert beads == 6
    assert {
        "fwhm_x_um",
        "fwhm_z_um",
        "ratio_x",
        "integrated_intensity",
        "accepted_beads",
    } <= metrics
    assert theory == "scalar confocal, pinhole 1.00 AU"


def test_record_requires_instrument_and_hash(db: ResultsDatabase, analyse: Analyse) -> None:
    with pytest.raises(TrackingError, match="under an instrument"):
        db.record(analyse(0, instrument=None))
    with pytest.raises(TrackingError, match="SHA-256"):
        db.record(analyse(0, hash_file=False))


def test_duplicates_are_refused_unless_requested(db: ResultsDatabase, analyse: Analyse) -> None:
    result = analyse(0)
    db.record(result)
    with pytest.raises(TrackingError, match="already recorded as session 1"):
        db.record(result)
    assert db.record(result, allow_duplicate=True) == 2
    assert db.audit_log()[-1]["details"]["duplicate_of"] == [1]


@pytest.mark.req("TRK-02")
def test_date_falls_back_to_the_file_system(
    db: ResultsDatabase, analyse: Analyse, tiffs: list[Path]
) -> None:
    stamp = datetime(2025, 12, 24, 8, tzinfo=UTC).timestamp()
    os.utime(tiffs[2], (stamp, stamp))
    db.record(analyse(2, day=None))
    (point,) = db.points(db.configurations()[0], "fwhm_x_um")
    assert point.date_source == "file-system"
    assert point.acquired_utc == datetime(2025, 12, 24, 8, tzinfo=UTC)


@pytest.mark.req("TRK-07", "NFR-07")
def test_exclusions_are_appended_and_latest_wins(db: ResultsDatabase, analyse: Analyse) -> None:
    db.record(analyse(0))
    green, orange = db.configurations()
    db.set_excluded(1, True, "dirty slide", user="ana")
    assert db.points(green, "fwhm_x_um")[0].excluded
    assert db.points(green, "fwhm_x_um")[0].exclusion_reason == "dirty slide"
    assert db.points(orange, "fwhm_x_um")[0].excluded
    db.set_excluded(1, False, "slide was fine", user="ben")
    assert not db.points(green, "fwhm_x_um")[0].excluded
    db.set_excluded(1, True, "orange laser unstable", channel_index=1)
    assert not db.points(green, "fwhm_x_um")[0].excluded
    assert db.points(orange, "fwhm_x_um")[0].excluded
    actions = [(e["user"], e["action"]) for e in db.audit_log()][1:3]
    assert actions == [("ana", "exclude"), ("ben", "include")]

    with pytest.raises(TrackingError, match="reason is required"):
        db.set_excluded(1, True, "  ")
    with pytest.raises(TrackingError, match="no session 5"):
        db.set_excluded(5, True, "x")
    with pytest.raises(TrackingError, match="has no channel 3"):
        db.set_excluded(1, True, "x", channel_index=3)


@pytest.mark.req("TRK-06")
def test_events(db: ResultsDatabase, analyse: Analyse) -> None:
    db.record(analyse(0))
    green, orange = db.configurations()
    db.add_event(
        occurred=T0 + timedelta(days=3),
        instrument_id="scope",
        kind=EventKind.SERVICE,
        description="laser realigned",
        rebaseline=True,
        user="eng",
        baseline={"method": "first_n", "sessions": 10},
    )
    db.add_event(
        occurred=T0,
        instrument_id="scope",
        kind=EventKind.OTHER,
        description="note for orange",
        channel_key="orange",
    )
    all_events = db.events()
    assert [e.kind for e in all_events] == ["other", "service"]
    service = all_events[1]
    assert service.rebaseline
    assert service.baseline == {"method": "first_n", "sessions": 10}
    assert service.created_by == "eng"
    assert [e.description for e in db.events(green)] == ["laser realigned"]
    assert len(db.events(orange)) == 2

    for kwargs, message in (
        ({"description": " "}, "needs a description"),
        ({"occurred": datetime(2026, 1, 1)}, "time zone"),
        ({"baseline": {"method": "first_n"}}, "only applies to a rebaselining"),
    ):
        arguments = {
            "occurred": T0,
            "instrument_id": "scope",
            "kind": EventKind.OTHER,
            "description": "x",
        } | kwargs
        with pytest.raises(TrackingError, match=message):
            db.add_event(**arguments)  # type: ignore[arg-type]


@pytest.mark.req("NFR-07")
@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_tables_are_append_only(db: ResultsDatabase, analyse: Analyse, table: str) -> None:
    db.record(analyse(0))
    db.add_event(occurred=T0, instrument_id="scope", kind=EventKind.OTHER, description="x")
    db.set_excluded(1, True, "x")
    connection = sqlite3.connect(db.path)
    try:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"DELETE FROM {table}")
        column = connection.execute(f"SELECT * FROM {table}").description[0][0]
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"UPDATE {table} SET {column} = {column}")
    finally:
        connection.close()


@pytest.mark.req("TRK-10")
def test_export_and_backup(db: ResultsDatabase, analyse: Analyse, tmp_path: Path) -> None:
    db.record(analyse(0))
    db.record(analyse(1, day=7))
    db.set_excluded(2, True, "drift test")
    count = db.export_csv(tmp_path / "export.csv")
    with (tmp_path / "export.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == count > 0
    assert {r["channel_key"] for r in rows} == {"green", "orange"}
    assert {r["excluded"] for r in rows if r["session_id"] == "2"} == {"True"}
    assert {r["exclusion_reason"] for r in rows if r["session_id"] == "2"} == {"drift test"}

    db.backup(tmp_path / "copy.sqlite")
    with ResultsDatabase(tmp_path / "copy.sqlite") as copy:
        assert copy.configurations() == db.configurations()
        assert len(copy.audit_log()) == len(db.audit_log())
    with pytest.raises(TrackingError, match="exists"):
        db.backup(tmp_path / "copy.sqlite")


def test_schema_checks(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite"
    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE something (x)")
    connection.commit()
    connection.close()
    with pytest.raises(SchemaError, match="not a PSFWidget results database"):
        ResultsDatabase(other)

    future = tmp_path / "future.sqlite"
    ResultsDatabase(future).close()
    connection = sqlite3.connect(future)
    connection.execute("INSERT INTO schema_version (version) VALUES (99)")
    connection.commit()
    connection.close()
    with pytest.raises(SchemaError, match="version 99 is not supported"):
        ResultsDatabase(future)

    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"not a database at all" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        ResultsDatabase(garbage)


def test_configuration_filter() -> None:
    items = [
        Configuration("a", "63x", "green", None),
        Configuration("a", "10x", "green", None),
        Configuration("b", "63x", "red", "widefield"),
    ]
    assert configurations_matching(items, instrument="a") == items[:2]
    assert configurations_matching(items, objective="63x", channel="red") == [items[2]]
    assert configurations_matching(items) == items
    assert items[2].label == "b / 63x / red / widefield"
    assert Configuration("a", None, "ex488-em?", None).label == "a / any objective / ex488-em?"
