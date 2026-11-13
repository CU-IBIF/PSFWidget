"""The results database: record analyses, events, and exclusions (TRK-01, TRK-02, TRK-06, TRK-07).

Results are filed under a *configuration*: instrument → objective → channel →
modality (TRK-02). Everything written is append-only and audited (NFR-07).
"""

from __future__ import annotations

import csv
import getpass
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.io.model import ChannelMetadata
from psfwidget.metrics.summary import SUMMARY_METRICS
from psfwidget.profiles.spec import SpecLimits
from psfwidget.serialize import metadata_record, plain
from psfwidget.tracking import schema
from psfwidget.tracking.settings import ACCEPTED_BEADS


class TrackingError(ValueError):
    """Raised when a tracking request cannot be carried out."""


class EventKind(StrEnum):
    """What happened to an instrument."""

    SERVICE = "service"
    ALIGNMENT = "alignment"
    OBJECTIVE = "objective"
    LASER = "laser"
    BASELINE = "baseline"
    """No physical change; only the control limits are reset."""
    OTHER = "other"


@dataclass(frozen=True)
class Configuration:
    """What a series of results is tracked under (TRK-02)."""

    instrument_id: str
    objective_id: str | None
    channel_key: str
    modality: str | None

    @property
    def label(self) -> str:
        """Human-readable name, e.g. ``sp8 / 63x-oil / green / confocal``."""
        parts = [self.instrument_id, self.objective_id or "any objective", self.channel_key]
        if self.modality:
            parts.append(self.modality)
        return " / ".join(parts)

    @property
    def slug(self) -> str:
        """File-name-safe form of the label."""
        text = "_".join(
            p
            for p in (self.instrument_id, self.objective_id, self.channel_key, self.modality)
            if p
        )
        return "".join(c if c.isalnum() or c in "-." else "_" for c in text)


@dataclass(frozen=True)
class SessionPoint:
    """One session's value of one metric for one configuration."""

    session_id: int
    channel_index: int
    acquired_utc: datetime
    date_source: str
    file_name: str
    value: float | None
    n_beads: int
    excluded: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class Event:
    """Something that happened to an instrument, possibly resetting the control limits."""

    id: int
    occurred_utc: datetime
    instrument_id: str
    objective_id: str | None
    channel_key: str | None
    kind: str
    description: str
    rebaseline: bool
    baseline: dict[str, Any] | None
    created_utc: datetime
    created_by: str

    def applies_to(self, configuration: Configuration) -> bool:
        """Whether this event concerns ``configuration``."""
        return (
            self.instrument_id == configuration.instrument_id
            and self.objective_id in (None, configuration.objective_id)
            and self.channel_key in (None, configuration.channel_key)
        )


def channel_key(channel: ChannelMetadata) -> str:
    """Return the channel identifier results are filed under.

    The instrument profile's channel preset id when one matched, else the
    channel name, else the wavelengths.
    """
    if channel.preset_id is not None:
        return channel.preset_id.value
    if channel.name is not None:
        return channel.name.value
    excitation = "?" if channel.excitation_nm is None else f"{channel.excitation_nm.value:g}"
    emission = "?" if channel.emission_nm is None else f"{channel.emission_nm.value:g}"
    return f"ex{excitation}-em{emission}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _user(user: str | None) -> str:
    if user:
        return user
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def _dumps(value: Any) -> str:
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True)


class ResultsDatabase:
    """A PSFWidget results database in one SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        try:
            schema.check(self._connection)
        except (schema.SchemaError, sqlite3.DatabaseError):
            self._connection.close()
            raise

    def close(self) -> None:
        """Close the database."""
        self._connection.close()

    def __enter__(self) -> ResultsDatabase:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection:
            yield self._connection

    def _audit(
        self, db: sqlite3.Connection, user: str, action: str, details: dict[str, Any]
    ) -> None:
        db.execute(
            "INSERT INTO audit_log (utc, user, action, details_json) VALUES (?, ?, ?, ?)",
            (_now(), user, action, _dumps(details)),
        )

    # ------------------------------------------------------------------ record

    def find_recorded(self, sha256: str, series: int, timepoint: int, tile: int) -> list[int]:
        """Return the ids of sessions already recorded from the same file and selection."""
        rows = self._connection.execute(
            "SELECT id FROM sessions WHERE file_sha256 = ? AND series_index = ? "
            "AND timepoint = ? AND tile = ? ORDER BY id",
            (sha256, series, timepoint, tile),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def record(
        self, result: FieldResult, *, user: str | None = None, allow_duplicate: bool = False
    ) -> int:
        """Store an analysis and return its session id (TRK-01).

        Raises
        ------
        TrackingError
            If the result has no instrument id or file hash, or the same file
            and selection was recorded before and ``allow_duplicate`` is false.
        """
        source, meta = result.provenance, result.metadata
        if meta.instrument_id is None:
            raise TrackingError(
                "results can only be recorded under an instrument; analyse with an "
                "instrument profile (--profile)"
            )
        if source.sha256 is None:
            raise TrackingError("results can only be recorded with the file's SHA-256 hash")
        earlier = self.find_recorded(
            source.sha256, source.series_index, source.timepoint, source.tile
        )
        if earlier and not allow_duplicate:
            raise TrackingError(
                f"{source.path.name} (series {source.series_index}) is already recorded as "
                f"session {', '.join(map(str, earlier))}; record it again only on purpose"
            )
        if meta.acquired_utc is not None:
            acquired, date_source = meta.acquired_utc.value, "metadata"
        else:
            stamp = os.stat(source.path).st_mtime
            acquired, date_source = datetime.fromtimestamp(stamp, tz=UTC), "file-system"
        who = _user(user)
        modality = None if meta.modality is None else meta.modality.value.value
        enhancement = (
            None if meta.resolution_enhancement is None else meta.resolution_enhancement.value
        )
        with self._transaction() as db:
            cursor = db.execute(
                """INSERT INTO sessions (uuid, recorded_utc, acquired_utc, date_source,
                instrument_id, objective_id, modality, resolution_enhancement, file_name,
                file_path, file_sha256, file_size, series_index, series_name, timepoint, tile,
                reader, software_version, analysed_utc, settings_json, spec_limits_json,
                metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    _now(),
                    acquired.isoformat(),
                    date_source,
                    meta.instrument_id.value,
                    None if meta.objective_id is None else meta.objective_id.value,
                    modality,
                    enhancement,
                    source.path.name,
                    str(source.path.resolve()),
                    source.sha256,
                    source.size_bytes,
                    source.series_index,
                    source.series_name,
                    source.timepoint,
                    source.tile,
                    source.reader,
                    result.software_version,
                    result.analysed_utc.isoformat(),
                    _dumps(result.settings.to_dict()),
                    _dumps(asdict(result.spec_limits)),
                    _dumps(metadata_record(meta)),
                ),
            )
            session_id = int(cursor.lastrowid or 0)
            for channel in result.channels:
                self._record_channel(db, session_id, channel, meta.channels[channel.index])
            self._audit(
                db,
                who,
                "record",
                {
                    "session_id": session_id,
                    "file": source.path.name,
                    "sha256": source.sha256,
                    "series": source.series_index,
                    "duplicate_of": earlier,
                },
            )
        return session_id

    def _record_channel(
        self,
        db: sqlite3.Connection,
        session_id: int,
        channel: ChannelResult,
        metadata: ChannelMetadata,
    ) -> None:
        theory = channel.theory
        cursor = db.execute(
            """INSERT INTO channel_results (session_id, channel_index, channel_name, channel_key,
            excitation_nm, emission_nm, candidates, accepted, theory_model, theory_lateral_um,
            theory_axial_um, spec_status, spec_json, rejections_json, warnings_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                channel.index,
                channel.name,
                channel_key(metadata),
                None if metadata.excitation_nm is None else metadata.excitation_nm.value,
                None if metadata.emission_nm is None else metadata.emission_nm.value,
                len(channel.detection.candidates),
                len(channel.accepted),
                None if theory is None else theory.model,
                None if theory is None else theory.lateral_um,
                None if theory is None else theory.axial_um,
                None if channel.spec is None else channel.spec.status.value,
                None if channel.spec is None else _dumps(asdict(channel.spec)),
                _dumps(channel.rejections),
                _dumps(list(channel.warnings)),
            ),
        )
        row_id = int(cursor.lastrowid or 0)
        accepted = len(channel.accepted)
        db.execute(
            "INSERT INTO metrics (channel_result_id, metric, n, mean, sd, median, q25, q75) "
            "VALUES (?, ?, 1, ?, NULL, ?, ?, ?)",
            (row_id, ACCEPTED_BEADS, accepted, accepted, accepted, accepted),
        )
        for metric in SUMMARY_METRICS:
            stats = channel.summary.get(metric)
            if stats is None:
                continue
            db.execute(
                "INSERT INTO metrics (channel_result_id, metric, n, mean, sd, median, q25, q75) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    metric,
                    stats.n,
                    stats.mean,
                    plain(stats.sd),
                    stats.median,
                    stats.q25,
                    stats.q75,
                ),
            )
        db.executemany(
            "INSERT INTO beads (channel_result_id, bead_id, status, data_json) "
            "VALUES (?, ?, ?, ?)",
            [(row_id, b.bead_id, b.status, _dumps(b.to_row())) for b in channel.beads],
        )

    # ------------------------------------------------------------------ queries

    def configurations(self) -> list[Configuration]:
        """Return every configuration with recorded results."""
        rows = self._connection.execute(
            """SELECT DISTINCT s.instrument_id, s.objective_id, c.channel_key,
            s.modality, s.resolution_enhancement FROM channel_results c
            JOIN sessions s ON s.id = c.session_id
            ORDER BY s.instrument_id, s.objective_id, c.channel_key, s.modality"""
        ).fetchall()
        return [Configuration(r[0], r[1], r[2], _modality_label(r[3], r[4])) for r in rows]

    def session_count(self, configuration: Configuration) -> int:
        """Return how many sessions are recorded for a configuration."""
        return len(self._rows(configuration))

    def _rows(self, configuration: Configuration) -> list[sqlite3.Row]:
        self._connection.row_factory = sqlite3.Row
        try:
            rows = self._connection.execute(
                """SELECT s.id AS session_id, s.acquired_utc, s.date_source, s.file_name,
                s.modality, s.resolution_enhancement, c.id AS channel_result_id,
                c.channel_index, c.accepted
                FROM channel_results c JOIN sessions s ON s.id = c.session_id
                WHERE s.instrument_id = ? AND s.objective_id IS ? AND c.channel_key = ?
                ORDER BY s.acquired_utc, s.id""",
                (
                    configuration.instrument_id,
                    configuration.objective_id,
                    configuration.channel_key,
                ),
            ).fetchall()
        finally:
            self._connection.row_factory = None
        return [
            r
            for r in rows
            if _modality_label(r["modality"], r["resolution_enhancement"])
            == configuration.modality
        ]

    def points(self, configuration: Configuration, metric: str) -> list[SessionPoint]:
        """Return each session's median of ``metric`` for a configuration, oldest first."""
        excluded = self._exclusion_state()
        result = []
        for row in self._rows(configuration):
            value_row = self._connection.execute(
                "SELECT median FROM metrics WHERE channel_result_id = ? AND metric = ?",
                (row["channel_result_id"], metric),
            ).fetchone()
            value = None if value_row is None or value_row[0] is None else float(value_row[0])
            reason = excluded.get((row["session_id"], row["channel_index"]))
            if reason is None:
                reason = excluded.get((row["session_id"], None))
            result.append(
                SessionPoint(
                    session_id=row["session_id"],
                    channel_index=row["channel_index"],
                    acquired_utc=_parse_time(row["acquired_utc"]),
                    date_source=row["date_source"],
                    file_name=row["file_name"],
                    value=value,
                    n_beads=row["accepted"],
                    excluded=reason is not None,
                    exclusion_reason=reason,
                )
            )
        return result

    def _exclusion_state(self) -> dict[tuple[int, int | None], str]:
        """Return the reason for every currently excluded (session, channel) pair."""
        state: dict[tuple[int, int | None], str | None] = {}
        for session_id, channel, action, reason in self._connection.execute(
            "SELECT session_id, channel_index, action, reason FROM exclusions ORDER BY id"
        ):
            key = (int(session_id), None if channel is None else int(channel))
            state[key] = reason if action == "exclude" else None
            if action == "include" and channel is None:
                for other in [k for k in state if k[0] == key[0]]:
                    state[other] = None
        return {k: v for k, v in state.items() if v is not None}

    def latest_session(self, configuration: Configuration) -> tuple[int, str | None] | None:
        """Return the latest session id of a configuration and its spec status, if any."""
        rows = self._rows(configuration)
        if not rows:
            return None
        row = self._connection.execute(
            "SELECT spec_status FROM channel_results WHERE id = ?",
            (rows[-1]["channel_result_id"],),
        ).fetchone()
        return int(rows[-1]["session_id"]), None if row is None else row[0]

    def latest_spec_limits(self, configuration: Configuration) -> SpecLimits:
        """Return the spec limits stored with the configuration's most recent session."""
        rows = self._rows(configuration)
        if not rows:
            return SpecLimits()
        stored = self._connection.execute(
            "SELECT spec_limits_json FROM sessions WHERE id = ?", (rows[-1]["session_id"],)
        ).fetchone()
        return SpecLimits(**json.loads(stored[0]))

    def events(self, configuration: Configuration | None = None) -> list[Event]:
        """Return events, oldest first, optionally only those concerning a configuration."""
        rows = self._connection.execute(
            """SELECT id, occurred_utc, instrument_id, objective_id, channel_key, kind,
            description, rebaseline, baseline_json, created_utc, created_by
            FROM events ORDER BY occurred_utc, id"""
        ).fetchall()
        events = [
            Event(
                id=r[0],
                occurred_utc=_parse_time(r[1]),
                instrument_id=r[2],
                objective_id=r[3],
                channel_key=r[4],
                kind=r[5],
                description=r[6],
                rebaseline=bool(r[7]),
                baseline=None if r[8] is None else json.loads(r[8]),
                created_utc=_parse_time(r[9]),
                created_by=r[10],
            )
            for r in rows
        ]
        if configuration is None:
            return events
        return [e for e in events if e.applies_to(configuration)]

    def session(self, session_id: int) -> dict[str, Any]:
        """Return a session's stored fields.

        Raises
        ------
        TrackingError
            If there is no such session.
        """
        self._connection.row_factory = sqlite3.Row
        try:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        finally:
            self._connection.row_factory = None
        if row is None:
            raise TrackingError(f"no session {session_id}")
        return dict(row)

    def audit_log(self) -> list[dict[str, Any]]:
        """Return the audit log, oldest first."""
        return [
            {"id": r[0], "utc": r[1], "user": r[2], "action": r[3], "details": json.loads(r[4])}
            for r in self._connection.execute(
                "SELECT id, utc, user, action, details_json FROM audit_log ORDER BY id"
            )
        ]

    # ------------------------------------------------------------------ history

    def add_event(
        self,
        *,
        occurred: datetime,
        instrument_id: str,
        kind: EventKind,
        description: str,
        objective_id: str | None = None,
        channel_key: str | None = None,
        rebaseline: bool = False,
        baseline: dict[str, Any] | None = None,
        user: str | None = None,
    ) -> int:
        """Record an event (TRK-06); with ``rebaseline`` it starts new control limits.

        Raises
        ------
        TrackingError
            If the description is empty, the time has no time zone, or a
            baseline method is given without ``rebaseline``.
        """
        if not description.strip():
            raise TrackingError("an event needs a description")
        if occurred.tzinfo is None:
            raise TrackingError("the event time needs a time zone")
        if baseline is not None and not rebaseline:
            raise TrackingError("a baseline method only applies to a rebaselining event")
        who = _user(user)
        with self._transaction() as db:
            cursor = db.execute(
                """INSERT INTO events (uuid, occurred_utc, instrument_id, objective_id,
                channel_key, kind, description, rebaseline, baseline_json, created_utc,
                created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    occurred.astimezone(UTC).isoformat(),
                    instrument_id,
                    objective_id,
                    channel_key,
                    kind.value,
                    description.strip(),
                    int(rebaseline),
                    None if baseline is None else _dumps(baseline),
                    _now(),
                    who,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
            self._audit(
                db,
                who,
                "event",
                {
                    "event_id": event_id,
                    "kind": kind.value,
                    "instrument_id": instrument_id,
                    "rebaseline": rebaseline,
                    "baseline": baseline,
                },
            )
        return event_id

    def set_excluded(
        self,
        session_id: int,
        excluded: bool,
        reason: str,
        *,
        channel_index: int | None = None,
        user: str | None = None,
    ) -> None:
        """Exclude a session (or one channel of it) from statistics, or include it again (TRK-07).

        Raises
        ------
        TrackingError
            If the session or channel does not exist or no reason is given.
        """
        if not reason.strip():
            raise TrackingError("a reason is required")
        self.session(session_id)
        if channel_index is not None:
            found = self._connection.execute(
                "SELECT 1 FROM channel_results WHERE session_id = ? AND channel_index = ?",
                (session_id, channel_index),
            ).fetchone()
            if found is None:
                raise TrackingError(f"session {session_id} has no channel {channel_index}")
        who = _user(user)
        action = "exclude" if excluded else "include"
        with self._transaction() as db:
            db.execute(
                "INSERT INTO exclusions (session_id, channel_index, action, reason, created_utc, "
                "created_by) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, channel_index, action, reason.strip(), _now(), who),
            )
            self._audit(
                db,
                who,
                action,
                {
                    "session_id": session_id,
                    "channel_index": channel_index,
                    "reason": reason,
                },
            )

    # ------------------------------------------------------------------ export

    def export_csv(self, path: str | Path) -> int:
        """Write every session's metrics as a long CSV table; return the row count (TRK-10)."""
        excluded = self._exclusion_state()
        query = """SELECT s.id, s.acquired_utc, s.date_source, s.instrument_id, s.objective_id,
            s.modality, s.resolution_enhancement, c.channel_index, c.channel_name,
            c.channel_key, s.file_name, s.series_index, s.software_version, m.metric, m.n,
            m.mean, m.sd, m.median, m.q25, m.q75
            FROM metrics m JOIN channel_results c ON c.id = m.channel_result_id
            JOIN sessions s ON s.id = c.session_id
            ORDER BY s.acquired_utc, s.id, c.channel_index, m.metric"""
        header = (
            "session_id",
            "acquired_utc",
            "date_source",
            "instrument_id",
            "objective_id",
            "modality",
            "resolution_enhancement",
            "channel_index",
            "channel_name",
            "channel_key",
            "file_name",
            "series_index",
            "software_version",
            "metric",
            "n",
            "mean",
            "sd",
            "median",
            "q25",
            "q75",
            "excluded",
            "exclusion_reason",
        )
        count = 0
        with Path(path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            for row in self._connection.execute(query):
                reason = excluded.get((row[0], row[7]), excluded.get((row[0], None)))
                writer.writerow([*row, reason is not None, reason or ""])
                count += 1
        return count

    def backup(self, path: str | Path) -> None:
        """Copy the whole database to ``path`` safely, even while it is open (TRK-10).

        Raises
        ------
        TrackingError
            If ``path`` exists.
        """
        target = Path(path)
        if target.exists():
            raise TrackingError(f"{target} exists; choose a new file for the backup")
        destination = sqlite3.connect(target)
        try:
            self._connection.backup(destination)
        finally:
            destination.close()


def _modality_label(modality: str | None, enhancement: str | None) -> str | None:
    if modality is None:
        return None
    return f"{modality} ({enhancement})" if enhancement else modality


def configurations_matching(
    configurations: Sequence[Configuration],
    *,
    instrument: str | None = None,
    objective: str | None = None,
    channel: str | None = None,
) -> list[Configuration]:
    """Filter configurations by instrument, objective, and channel key."""
    return [
        c
        for c in configurations
        if (instrument is None or c.instrument_id == instrument)
        and (objective is None or c.objective_id == objective)
        and (channel is None or c.channel_key == channel)
    ]
