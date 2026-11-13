"""SQLite schema of the results database (TRK-01, TRK-02, NFR-07).

Every table that holds results or history is append-only: triggers abort any
UPDATE or DELETE. Corrections are made by adding rows (an exclusion, an
inclusion, an event), never by changing old ones.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "sessions",
    "channel_results",
    "metrics",
    "beads",
    "events",
    "exclusions",
    "audit_log",
)

_TABLES = """
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    recorded_utc TEXT NOT NULL,
    acquired_utc TEXT NOT NULL,
    date_source TEXT NOT NULL,          -- 'metadata' or 'file-system'
    instrument_id TEXT NOT NULL,
    objective_id TEXT,
    modality TEXT,
    resolution_enhancement TEXT,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    series_index INTEGER NOT NULL,
    series_name TEXT NOT NULL,
    timepoint INTEGER NOT NULL,
    tile INTEGER NOT NULL,
    reader TEXT NOT NULL,
    software_version TEXT NOT NULL,
    analysed_utc TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    spec_limits_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX sessions_file ON sessions (file_sha256, series_index, timepoint, tile);

CREATE TABLE channel_results (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (id),
    channel_index INTEGER NOT NULL,
    channel_name TEXT,
    channel_key TEXT NOT NULL,
    excitation_nm REAL,
    emission_nm REAL,
    candidates INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    theory_model TEXT,
    theory_lateral_um REAL,
    theory_axial_um REAL,
    spec_status TEXT,
    spec_json TEXT,
    rejections_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    UNIQUE (session_id, channel_index)
);
CREATE INDEX channel_results_key ON channel_results (channel_key);

CREATE TABLE metrics (
    channel_result_id INTEGER NOT NULL REFERENCES channel_results (id),
    metric TEXT NOT NULL,
    n INTEGER NOT NULL,
    mean REAL,
    sd REAL,
    median REAL,
    q25 REAL,
    q75 REAL,
    PRIMARY KEY (channel_result_id, metric)
);

CREATE TABLE beads (
    channel_result_id INTEGER NOT NULL REFERENCES channel_results (id),
    bead_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (channel_result_id, bead_id)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    occurred_utc TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    objective_id TEXT,                  -- NULL: every objective
    channel_key TEXT,                   -- NULL: every channel
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    rebaseline INTEGER NOT NULL,
    baseline_json TEXT,                 -- how to set limits after a rebaseline
    created_utc TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE exclusions (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (id),
    channel_index INTEGER,              -- NULL: every channel of the session
    action TEXT NOT NULL CHECK (action IN ('exclude', 'include')),
    reason TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    utc TEXT NOT NULL,
    user TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL
);
"""


def _append_only_triggers() -> str:
    statements = []
    for table in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
            )
    return "\n".join(statements)


class SchemaError(RuntimeError):
    """Raised when a database has an unknown or newer schema."""


def create(connection: sqlite3.Connection) -> None:
    """Create the schema in an empty database."""
    with connection:
        connection.executescript(_TABLES + _append_only_triggers())
        connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def check(connection: sqlite3.Connection) -> None:
    """Create the schema if the database is empty, or check that it is current.

    Raises
    ------
    SchemaError
        If the database is not a PSFWidget database or has a different version.
    """
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not tables:
        create(connection)
        return
    if "schema_version" not in tables:
        raise SchemaError("not a PSFWidget results database (no schema_version table)")
    row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    version = row[0] if row else None
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"database schema version {version} is not supported by this version of "
            f"PSFWidget (expects {SCHEMA_VERSION})"
        )
