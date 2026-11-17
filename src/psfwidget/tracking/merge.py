"""Import (merge) another results database into this one (TRK-10).

Typical uses: bringing an acquisition PC's results together for review, or
restoring sessions recorded on a replacement PC. Everything is copied as
recorded: original users, hosts, and times are kept, and nothing in the
target is changed (the target stays append-only).

* Sessions and events are matched by their UUID; those already present are
  skipped, so importing the same file twice changes nothing.
* Channel results, metrics, and beads come with their session.
* Exclusions and approvals are matched by their content.
* The source's audit entries are copied, marked "(imported)", and the import
  itself is audited with the source file's SHA-256 and the counts.

The source is opened read-only and may be schema version 1 or 2.
"""

from __future__ import annotations

import socket
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from psfwidget.io._common import sha256_of
from psfwidget.tracking.schema import SCHEMA_VERSION
from psfwidget.tracking.store import ResultsDatabase, TrackingError, _dumps, _now, _user

TABLES = ("sessions", "events", "exclusions", "approvals", "audit_log")


@dataclass
class ImportSummary:
    """What an import copied and skipped."""

    source: Path
    source_sha256: str
    copied: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
    skipped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
    dry_run: bool = False

    def describe(self) -> str:
        """Return a one-paragraph summary."""
        verb = "would copy" if self.dry_run else "copied"
        copied = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in self.copied.items())
        skipped = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in self.skipped.items() if n)
        return f"{self.source.name}: {verb} {copied}" + (
            f"; already present: {skipped}" if skipped else ""
        )


def _rows(connection: sqlite3.Connection, query: str, *args: Any) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, args)]
    finally:
        connection.row_factory = None


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def _insert(db: sqlite3.Connection, table: str, row: dict[str, Any], columns: list[str]) -> int:
    values = {c: row.get(c) for c in columns if c != "id"}
    cursor = db.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )
    return int(cursor.lastrowid or 0)


def import_database(
    target: ResultsDatabase,
    source_path: str | Path,
    *,
    user: str | None = None,
    dry_run: bool = False,
) -> ImportSummary:
    """Copy what ``source_path`` has and ``target`` lacks; return what was done.

    Raises
    ------
    TrackingError
        If the source is missing, is the target itself, or is not a PSFWidget
        database of a supported version.
    """
    source = Path(source_path)
    if not source.is_file():
        raise TrackingError(f"no database at {source}")
    if source.resolve() == target.path.resolve():
        raise TrackingError("a database cannot be imported into itself")
    summary = ImportSummary(source, sha256_of(source), dry_run=dry_run)
    other = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "schema_version" not in tables:
            raise TrackingError(f"{source.name} is not a PSFWidget results database")
        (version,) = other.execute("SELECT MAX(version) FROM schema_version").fetchone()
        if version not in (1, SCHEMA_VERSION):
            raise TrackingError(f"{source.name} has unsupported schema version {version}")
        db = target._connection
        with db:
            session_map = _copy_sessions(db, other, summary)
            _copy_events(db, other, summary)
            _copy_exclusions(db, other, summary, session_map)
            if "approvals" in tables:
                _copy_approvals(db, other, summary, session_map)
            _copy_audit(db, other, summary)
            details = {
                "source": str(source.resolve()),
                "source_sha256": summary.source_sha256,
                "source_schema": version,
                "copied": summary.copied,
                "skipped": summary.skipped,
            }
            db.execute(
                "INSERT INTO audit_log (utc, user, action, details_json, host) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), _user(user), "import", _dumps(details), socket.gethostname()),
            )
            if dry_run:
                raise _RollbackError
    except _RollbackError:
        pass
    finally:
        other.close()
    return summary


class _RollbackError(Exception):
    """Raised inside the transaction to undo a dry run."""


def _copy_sessions(
    db: sqlite3.Connection, other: sqlite3.Connection, summary: ImportSummary
) -> dict[int, int]:
    """Copy new sessions with their channels, metrics, and beads; map source to target ids."""
    session_columns = _columns(db, "sessions")
    channel_columns = _columns(db, "channel_results")
    existing = dict(db.execute("SELECT uuid, id FROM sessions").fetchall())
    mapping: dict[int, int] = {}
    for session in _rows(other, "SELECT * FROM sessions ORDER BY id"):
        if session["uuid"] in existing:
            mapping[session["id"]] = existing[session["uuid"]]
            summary.skipped["sessions"] += 1
            continue
        new_id = _insert(db, "sessions", session, session_columns)
        mapping[session["id"]] = new_id
        summary.copied["sessions"] += 1
        for channel in _rows(
            other, "SELECT * FROM channel_results WHERE session_id = ? ORDER BY id", session["id"]
        ):
            old_channel = channel["id"]
            channel["session_id"] = new_id
            new_channel = _insert(db, "channel_results", channel, channel_columns)
            for table in ("metrics", "beads"):
                columns = _columns(db, table)
                for row in _rows(
                    other, f"SELECT * FROM {table} WHERE channel_result_id = ?", old_channel
                ):
                    row["channel_result_id"] = new_channel
                    _insert(db, table, row, columns)
    return mapping


def _copy_events(
    db: sqlite3.Connection, other: sqlite3.Connection, summary: ImportSummary
) -> None:
    columns = _columns(db, "events")
    existing = {r[0] for r in db.execute("SELECT uuid FROM events")}
    for event in _rows(other, "SELECT * FROM events ORDER BY id"):
        if event["uuid"] in existing:
            summary.skipped["events"] += 1
            continue
        _insert(db, "events", event, columns)
        summary.copied["events"] += 1


def _copy_by_content(
    db: sqlite3.Connection,
    other: sqlite3.Connection,
    summary: ImportSummary,
    table: str,
    session_map: dict[int, int],
) -> None:
    columns = _columns(db, table)
    key_columns = [c for c in columns if c != "id"]
    existing = {tuple(row[c] for c in key_columns) for row in _rows(db, f"SELECT * FROM {table}")}
    for row in _rows(other, f"SELECT * FROM {table} ORDER BY id"):
        if row.get("session_id") is not None:
            row["session_id"] = session_map[row["session_id"]]
        key = tuple(row.get(c) for c in key_columns)
        if key in existing:
            summary.skipped[table] += 1
            continue
        _insert(db, table, row, columns)
        existing.add(key)
        summary.copied[table] += 1


def _copy_exclusions(
    db: sqlite3.Connection,
    other: sqlite3.Connection,
    summary: ImportSummary,
    session_map: dict[int, int],
) -> None:
    _copy_by_content(db, other, summary, "exclusions", session_map)


def _copy_approvals(
    db: sqlite3.Connection,
    other: sqlite3.Connection,
    summary: ImportSummary,
    session_map: dict[int, int],
) -> None:
    _copy_by_content(db, other, summary, "approvals", session_map)


def _copy_audit(db: sqlite3.Connection, other: sqlite3.Connection, summary: ImportSummary) -> None:
    existing = {
        (row["utc"], row["user"], row["action"], row["details_json"])
        for row in _rows(db, "SELECT * FROM audit_log")
    }
    for row in _rows(other, "SELECT * FROM audit_log ORDER BY id"):
        action = (
            row["action"]
            if row["action"].endswith("(imported)")
            else (f"{row['action']} (imported)")
        )
        key = (row["utc"], row["user"], action, row["details_json"])
        if key in existing:
            summary.skipped["audit_log"] += 1
            continue
        db.execute(
            "INSERT INTO audit_log (utc, user, action, details_json, host) VALUES (?, ?, ?, ?, ?)",
            (row["utc"], row["user"], action, row["details_json"], row.get("host")),
        )
        existing.add(key)
        summary.copied["audit_log"] += 1
