"""Unattended runs: watch-folder ingestion, trend reports, logs, and alerts (AUT-02 to AUT-06).

``run_due`` runs whatever jobs are due and returns; operating-system
schedulers (cron, Windows Task Scheduler) call it through ``psfwidget run``.
``watch`` calls it in a loop for the built-in scheduler. Both keep their
state in ``<output root>/_psfwidget/``:

* ``state.json``: when each job last ran, and every file already handled
  (by path, size, and modification time), so unchanged files are not
  re-read or retried every run.
* ``runs/<time>_<job>.json``: a summary of each run (AUT-04).
* ``psfwidget.log``: a running log.
* ``run.lock``: present while a run is in progress, so two runs never overlap.

After every run ``STATUS.json`` describes the latest state of every tracked
configuration, and ``ALERT.txt`` exists exactly when something needs
attention: a FAIL or a Westgard rejection in a latest session, or an error
in the run (AUT-06).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import socket
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psfwidget import __version__
from psfwidget.analysis import analyze_stack
from psfwidget.automation.config import AutomationConfig, WatchFolder
from psfwidget.io import list_series, read_stack
from psfwidget.io._common import sha256_of
from psfwidget.report import document as doc
from psfwidget.report.publish import publish_session, trend_base, write_report
from psfwidget.report.status import session_checks
from psfwidget.tracking.control import series_from_database
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import ResultsDatabase

JOBS = ("ingest", "trend_report")
LOCK_STALE_AFTER = timedelta(hours=6)
OPERATOR = "psfwidget-scheduler"

log = logging.getLogger("psfwidget.automation")


@dataclass
class RunSummary:
    """What one run of one job did (AUT-04)."""

    job: str
    started_utc: datetime
    finished_utc: datetime | None = None
    sessions: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the summary as JSON-ready data."""
        return {
            "job": self.job,
            "started_utc": self.started_utc.isoformat(),
            "finished_utc": None if self.finished_utc is None else self.finished_utc.isoformat(),
            "software_version": __version__,
            "host": socket.gethostname(),
            "sessions": self.sessions,
            "skipped": self.skipped,
            "errors": self.errors,
            "reports": self.reports,
        }


class RunLockedError(RuntimeError):
    """Raised when another run holds the lock."""


# ---------------------------------------------------------------------- state


def _state_path(config: AutomationConfig) -> Path:
    return config.state_dir / "state.json"


def load_state(config: AutomationConfig, now: datetime) -> dict[str, Any]:
    """Return the saved state, creating it on first use."""
    path = _state_path(config)
    if path.exists():
        state: dict[str, Any] = json.loads(path.read_text("utf-8"))
    else:
        state = {"created": now.isoformat(), "jobs": {}, "files": {}}
    state.setdefault("jobs", {})
    state.setdefault("files", {})
    state.setdefault("created", now.isoformat())
    return state


def save_state(config: AutomationConfig, state: dict[str, Any]) -> None:
    """Write the state atomically."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _file_key(path: Path, stat: os.stat_result) -> str:
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


# ---------------------------------------------------------------------- lock


class _Lock:
    def __init__(self, path: Path, now: datetime) -> None:
        self.path = path
        self.now = now

    def __enter__(self) -> _Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                taken = datetime.fromisoformat(json.loads(self.path.read_text("utf-8"))["since"])
            except (ValueError, KeyError, json.JSONDecodeError):
                taken = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
            if self.now - taken < LOCK_STALE_AFTER:
                raise RunLockedError(f"another run has held {self.path} since {taken.isoformat()}")
            log.warning("removing stale lock %s from %s", self.path, taken.isoformat())
            self.path.unlink()
        handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                {"since": self.now.isoformat(), "pid": os.getpid(), "host": socket.gethostname()},
                stream,
            )
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------- logging


def _configure_logging(config: AutomationConfig) -> logging.Handler:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(config.state_dir / "psfwidget.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


# ---------------------------------------------------------------------- ingest


def _candidates(watch: WatchFolder) -> Iterator[Path]:
    if not watch.folder.is_dir():
        raise FileNotFoundError(f"watched folder {watch.folder} does not exist")
    files = watch.folder.rglob("*") if watch.recursive else watch.folder.iterdir()
    for path in sorted(files):
        if path.is_file() and any(
            fnmatch.fnmatch(path.name.lower(), p.lower()) for p in watch.patterns
        ):
            yield path


def _ingest_file(
    path: Path,
    watch: WatchFolder,
    config: AutomationConfig,
    db: ResultsDatabase,
    summary: RunSummary,
) -> str:
    """Analyse, record, and report every selected series of one file; return the outcome."""
    profile = watch.profile
    available = [s.index for s in list_series(path)]
    selected = available if watch.series is None else [s for s in watch.series if s in available]
    if not selected:
        summary.skipped.append({"file": str(path), "reason": "none of the configured series"})
        return "no matching series"
    digest = sha256_of(path)
    outcomes = []
    for series in selected:
        if db.find_recorded(digest, series, 0, 0):
            summary.skipped.append(
                {"file": str(path), "series": series, "reason": "already recorded"}
            )
            outcomes.append("already recorded")
            continue
        stack = read_stack(path, series=series, profile=profile, compute_hash=False)
        stack = replace(stack, provenance=replace(stack.provenance, sha256=digest))
        problems = stack.problems()
        if problems:
            summary.skipped.append(
                {
                    "file": str(path),
                    "series": series,
                    "reason": "not ready for analysis",
                    "problems": problems,
                }
            )
            log.warning(
                "%s series %d not ready for analysis: %s", path, series, "; ".join(problems)
            )
            outcomes.append("not analyzable")
            continue
        result = analyze_stack(stack, config.analysis, profile.spec)
        session_id = db.record(result, user=OPERATOR)
        checks = session_checks(db, result, session_id, profile.tracking)
        files, statuses = publish_session(
            result,
            config.output,
            stack=stack,
            checks=checks,
            session_id=session_id,
            operator=OPERATOR,
        )
        summary.sessions.append(
            {
                "session_id": session_id,
                "file": str(path),
                "series": series,
                "instrument": profile.instrument_id,
                "status": doc.worst([s.status for s in statuses]).value,
                "channels": [
                    {
                        "index": s.index,
                        "name": s.name,
                        "status": s.status.value,
                        "reasons": list(s.reasons),
                    }
                    for s in statuses
                ],
                "outputs": [str(f) for f in files],
            }
        )
        log.info(
            "recorded %s series %d as session %d (%s)",
            path.name,
            series,
            session_id,
            summary.sessions[-1]["status"],
        )
        outcomes.append("recorded")
    return ", ".join(dict.fromkeys(outcomes))


def run_ingest(config: AutomationConfig, state: dict[str, Any], now: datetime) -> RunSummary:
    """Process new files in every watched folder (AUT-02, AUT-04)."""
    summary = RunSummary("ingest", now)
    settle_limit = now.timestamp()
    with ResultsDatabase(config.database) as db:
        for watch in config.watches:
            try:
                files = list(_candidates(watch))
            except OSError as exc:
                summary.errors.append({"folder": str(watch.folder), "error": str(exc)})
                log.error("cannot read %s: %s", watch.folder, exc)
                continue
            for path in files:
                try:
                    stat = path.stat()
                except OSError:
                    continue  # removed while scanning
                if settle_limit - stat.st_mtime < watch.settle_seconds:
                    summary.skipped.append({"file": str(path), "reason": "still being written"})
                    continue
                key = _file_key(path, stat)
                if key in state["files"]:
                    continue
                try:
                    outcome = _ingest_file(path, watch, config, db, summary)
                except Exception as exc:  # isolate every file (AUT-04, NFR-06)
                    outcome = "error"
                    summary.errors.append(
                        {
                            "file": str(path),
                            "error": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc(limit=5),
                        }
                    )
                    log.error("failed on %s: %s", path, exc)
                state["files"][key] = {"outcome": outcome, "when": now.isoformat()}
    return summary


# ---------------------------------------------------------------------- trend


def run_trend_report(config: AutomationConfig, now: datetime) -> RunSummary:
    """Write a trend report for each instrument in the database (REP-02)."""
    from psfwidget.report.trend import build_trend_report

    summary = RunSummary("trend_report", now)
    profiles = config.profiles_by_instrument()
    with ResultsDatabase(config.database) as db:
        configurations = db.configurations()
        for instrument in sorted({c.instrument_id for c in configurations}):
            try:
                profile = profiles.get(instrument)
                settings = profile.tracking if profile is not None else TrackingSettings()
                report = build_trend_report(
                    db,
                    [c for c in configurations if c.instrument_id == instrument],
                    settings,
                    since=now - timedelta(days=config.trend_days),
                    until=now,
                    branding=config.output.branding,
                )
                base = trend_base(config.output, instrument, now)
                written = write_report(
                    report, base, config.output.reports, config.output.page_size
                )
                summary.reports += [str(p) for p in written]
                log.info(
                    "wrote trend report for %s (%s)",
                    instrument,
                    report.status.value if report.status else "-",
                )
            except Exception as exc:
                summary.errors.append(
                    {"instrument": instrument, "error": f"{type(exc).__name__}: {exc}"}
                )
                log.error("trend report for %s failed: %s", instrument, exc)
    return summary


# ---------------------------------------------------------------------- status


_SEVERITY_STATUS = {
    "rejection": doc.Status.FAIL,
    "warning": doc.Status.WARNING,
    "ok": doc.Status.PASS,
    "none": doc.Status.NOT_EVALUATED,
}


def current_status(config: AutomationConfig) -> list[dict[str, Any]]:
    """Return the latest state of every tracked configuration."""
    profiles = config.profiles_by_instrument()
    entries = []
    with ResultsDatabase(config.database) as db:
        for configuration in db.configurations():
            profile = profiles.get(configuration.instrument_id)
            settings = profile.tracking if profile is not None else TrackingSettings()
            latest = db.latest_session(configuration)
            reasons: list[str] = []
            statuses: list[doc.Status] = []
            if latest is not None and latest[1] == "FAIL":
                statuses.append(doc.Status.FAIL)
                reasons.append("spec limits failed")
            elif latest is not None and latest[1] == "PASS":
                statuses.append(doc.Status.PASS)
            for metric in settings.metrics:
                point = series_from_database(db, configuration, metric, settings).latest
                if point is None:
                    continue
                status = _SEVERITY_STATUS[point.severity]
                statuses.append(status)
                if status in (doc.Status.FAIL, doc.Status.WARNING):
                    rules = ", ".join(sorted(r.value for r in point.violations))
                    reasons.append(f"{metric}: Westgard {rules}")
            entries.append(
                {
                    "configuration": configuration.label,
                    "latest_session": None if latest is None else latest[0],
                    "status": doc.worst(statuses).value,
                    "reasons": reasons,
                }
            )
    return entries


def write_status(config: AutomationConfig, summaries: list[RunSummary], now: datetime) -> bool:
    """Write STATUS.json and create or remove ALERT.txt; return whether an alert is raised."""
    configurations = current_status(config) if config.database.exists() else []
    errors = [e for s in summaries for e in s.errors]
    failing = [c for c in configurations if c["status"] == doc.Status.FAIL.value]
    overall = doc.worst([doc.Status(c["status"]) for c in configurations])
    status = {
        "updated_utc": now.isoformat(),
        "overall": overall.value,
        "run_errors": len(errors),
        "configurations": configurations,
        "last_runs": [
            s.to_dict() | {"sessions": len(s.sessions), "errors": len(s.errors)} for s in summaries
        ],
    }
    config.status_file.parent.mkdir(parents=True, exist_ok=True)
    config.status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
    if not failing and not errors:
        config.alert_file.unlink(missing_ok=True)
        return False
    lines = [f"PSFWidget alert, {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]
    for entry in failing:
        lines.append(
            f"FAIL  {entry['configuration']} (session {entry['latest_session']}): "
            + "; ".join(entry["reasons"])
        )
    for error in errors:
        where = error.get("file") or error.get("folder") or error.get("instrument") or "?"
        lines.append(f"ERROR {where}: {error['error']}")
    lines += ["", f"Details: {config.status_file}"]
    config.alert_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------- driver


def _last_run(state: dict[str, Any], job: str) -> datetime | None:
    value = state["jobs"].get(job)
    return None if value is None else datetime.fromisoformat(value)


def due_jobs(config: AutomationConfig, state: dict[str, Any], now: datetime) -> list[str]:
    """Return the jobs due at ``now``."""
    local = now.astimezone()
    first_seen = datetime.fromisoformat(state["created"]).astimezone()
    schedules = {"ingest": config.ingest, "trend_report": config.trend_report}
    due = []
    for job in JOBS:
        last = _last_run(state, job)
        if schedules[job].is_due(local, None if last is None else last.astimezone(), first_seen):
            due.append(job)
    return due


def run_due(
    config: AutomationConfig, *, now: datetime | None = None, force: tuple[str, ...] = ()
) -> list[RunSummary]:
    """Run the jobs that are due (and any in ``force``); return their summaries.

    Raises
    ------
    RunLockedError
        If another run is in progress.
    ValueError
        If ``force`` names an unknown job.
    """
    unknown = sorted(set(force) - set(JOBS))
    if unknown:
        raise ValueError(f"unknown job(s) {unknown}; jobs are {', '.join(JOBS)}")
    now = now or datetime.now(UTC)
    handler = _configure_logging(config)
    try:
        with _Lock(config.state_dir / "run.lock", now):
            state = load_state(config, now)
            jobs = list(dict.fromkeys([*due_jobs(config, state, now), *force]))
            summaries: list[RunSummary] = []
            for job in jobs:
                log.info("starting %s", job)
                summary = (
                    run_ingest(config, state, now)
                    if job == "ingest"
                    else run_trend_report(config, now)
                )
                summary.finished_utc = datetime.now(UTC)
                state["jobs"][job] = now.isoformat()
                save_state(config, state)
                runs = config.state_dir / "runs"
                runs.mkdir(parents=True, exist_ok=True)
                stamp = now.strftime("%Y%m%dT%H%M%SZ")
                (runs / f"{stamp}_{job}.json").write_text(
                    json.dumps(summary.to_dict(), indent=2), encoding="utf-8"
                )
                log.info(
                    "finished %s: %d sessions, %d skipped, %d errors",
                    job,
                    len(summary.sessions),
                    len(summary.skipped),
                    len(summary.errors),
                )
                summaries.append(summary)
            save_state(config, state)
            write_status(config, summaries, now)
            return summaries
    finally:
        log.removeHandler(handler)
        handler.close()


def watch(
    config: AutomationConfig,
    *,
    poll_seconds: float = 60.0,
    iterations: int | None = None,
    on_run: Callable[[list[RunSummary]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run due jobs every ``poll_seconds`` until stopped (the built-in scheduler, AUT-03)."""
    count = 0
    while iterations is None or count < iterations:
        try:
            summaries = run_due(config)
            if on_run is not None and summaries:
                on_run(summaries)
        except RunLockedError as exc:
            log.warning("%s", exc)
        count += 1
        if iterations is None or count < iterations:
            sleep(poll_seconds)
