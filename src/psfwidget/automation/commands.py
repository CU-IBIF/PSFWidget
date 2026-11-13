"""The ``psfwidget run``, ``watch``, ``check-config``, and ``report`` commands (WP5, WP6).

Requirements: AUT-01 to AUT-06, REP-02 to REP-05.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psfwidget.automation.config import load_config
from psfwidget.automation.runner import (
    JOBS,
    RunLockedError,
    RunSummary,
    current_status,
    due_jobs,
    load_state,
    run_due,
    watch,
)
from psfwidget.automation.track import parse_time
from psfwidget.profiles import load_profile
from psfwidget.report.document import Branding
from psfwidget.report.publish import REPORT_FORMATS, write_report
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import ResultsDatabase, configurations_matching

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_ALERT = 5
"""Exit status of ``run`` when the run raised an alert (a FAIL or an error)."""
EXIT_LOCKED = 6
"""Exit status of ``run`` when another run is in progress."""


def _print_summaries(summaries: list[RunSummary]) -> None:
    if not summaries:
        print("Nothing was due.")
    for summary in summaries:
        print(
            f"{summary.job}: {len(summary.sessions)} session(s), {len(summary.skipped)} "
            f"skipped, {len(summary.errors)} error(s), {len(summary.reports)} report file(s)"
        )
        for session in summary.sessions:
            print(
                f"  session {session['session_id']}: {Path(session['file']).name} "
                f"series {session['series']}: {session['status']}"
            )
        for error in summary.errors:
            where = error.get("file") or error.get("folder") or error.get("instrument")
            print(f"  error: {where}: {error['error']}")


def formats(text: str, allowed: frozenset[str]) -> frozenset[str]:
    """Parse a comma-separated format list.

    Raises
    ------
    ValueError
        If a format is not allowed.
    """
    found = frozenset(part.strip().lower() for part in text.split(",") if part.strip())
    unknown = sorted(found - allowed)
    if unknown or not found:
        raise ValueError(f"formats must be among {sorted(allowed)}, got {text!r}")
    return found


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    force = tuple(JOBS) if args.job == ["all"] else tuple(args.job or ())
    try:
        summaries = run_due(config, force=force)
    except RunLockedError as exc:
        print(f"psfwidget: {exc}")
        return EXIT_LOCKED
    _print_summaries(summaries)
    alert = config.alert_file.exists()
    print(f"Status: {config.status_file}" + (f"\nALERT: {config.alert_file}" if alert else ""))
    return EXIT_ALERT if alert and args.fail_on_alert else EXIT_OK


def _watch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(
        f"Watching {len(config.watches)} folder(s); ingest {config.ingest.text}, trend "
        f"report {config.trend_report.text}. Checking every {args.poll:g} s; Ctrl+C to stop."
    )
    try:
        watch(config, poll_seconds=args.poll, on_run=_print_summaries)
    except KeyboardInterrupt:
        print("Stopped.")
    return EXIT_OK


def _check_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"{config.path}: valid")
    print(f"  database:     {config.database}")
    print(f"  output root:  {config.output.root}")
    print(f"  reports:      {', '.join(sorted(config.output.reports)) or '-'}")
    print(f"  exports:      {', '.join(sorted(config.output.exports)) or '-'}")
    print(f"  ingest:       {config.ingest.text}")
    print(f"  trend report: {config.trend_report.text} (last {config.trend_days} days)")
    for number, item in enumerate(config.watches, 1):
        found = "" if item.folder.is_dir() else "  (folder not found)"
        print(
            f"  watch {number}: {item.folder}{found}\n"
            f"    profile {item.profile_path.name} ({item.profile.instrument_id}), "
            f"patterns {', '.join(item.patterns)}, settle {item.settle_seconds:g} s"
        )
    now = datetime.now(UTC)
    state = load_state(config, now)
    due = due_jobs(config, state, now)
    print(f"  due now:      {', '.join(due) or 'nothing'}")
    if config.database.exists():
        for entry in current_status(config):
            print(f"  {entry['status']:<14} {entry['configuration']}")
    return EXIT_OK


def _report_trend(args: argparse.Namespace) -> int:
    from psfwidget.report.trend import build_trend_report

    settings = load_profile(args.profile).tracking if args.profile else TrackingSettings()
    until = parse_time(args.until) if args.until else datetime.now(UTC)
    since = parse_time(args.since) if args.since else until - timedelta(days=args.days)
    with ResultsDatabase(args.db) as db:
        found = configurations_matching(
            db.configurations(), instrument=args.instrument, objective=None, channel=None
        )
        if not found:
            raise ValueError("no tracked configurations match")
        report = build_trend_report(
            db,
            found,
            settings,
            since=since,
            until=until,
            branding=Branding(facility=args.facility or ""),
        )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{until:%Y-%m-%d}_{args.instrument or 'all'}_trend"
    written = write_report(report, out / name, formats(args.format, REPORT_FORMATS), args.page)
    print("Wrote " + ", ".join(str(p) for p in written))
    return EXIT_OK


def add_parsers(commands: Any) -> None:
    """Add the automation and report commands to the top-level parser."""
    run = commands.add_parser(
        "run",
        help="run the scheduled jobs that are due (for cron or Task Scheduler)",
        description=(
            "Run every job that is due under the configuration's schedule, write the run "
            "logs, STATUS.json, and (when something needs attention) ALERT.txt, then exit."
        ),
    )
    run.add_argument("--config", required=True, metavar="FILE", help="automation TOML file")
    run.add_argument(
        "--job",
        action="append",
        choices=[*JOBS, "all"],
        help="also run this job now even if it is not due; may be repeated",
    )
    run.add_argument(
        "--fail-on-alert",
        action="store_true",
        help=f"exit with status {EXIT_ALERT} when an alert is raised",
    )
    run.set_defaults(run=_run)

    loop = commands.add_parser(
        "watch",
        help="keep running and run jobs as they fall due (built-in scheduler)",
        description="Check the schedule every --poll seconds and run what is due.",
    )
    loop.add_argument("--config", required=True, metavar="FILE", help="automation TOML file")
    loop.add_argument("--poll", type=float, default=60.0, metavar="SECONDS", help="default: 60")
    loop.set_defaults(run=_watch)

    check = commands.add_parser(
        "check-config",
        help="validate an automation configuration and show what it would do",
    )
    check.add_argument("config", help="automation TOML file")
    check.set_defaults(run=_check_config)

    report = commands.add_parser("report", help="write reports from the results database")
    kinds = report.add_subparsers(dest="report_kind", metavar="kind", required=True)
    trend = kinds.add_parser(
        "trend",
        help="write a trend report (status, violations, events, charts)",
    )
    trend.add_argument("--db", required=True, metavar="FILE", help="results database")
    trend.add_argument("--out", default=".", metavar="DIR", help="output folder")
    trend.add_argument("--instrument", help="only this instrument")
    trend.add_argument("--profile", help="instrument profile for the tracking settings")
    trend.add_argument("--since", help="start of the period (ISO date)")
    trend.add_argument("--until", help="end of the period (ISO date; default: now)")
    trend.add_argument(
        "--days", type=int, default=90, help="period length when --since is not given"
    )
    trend.add_argument("--format", default="html,pdf", help="html, pdf, or html,pdf")
    trend.add_argument("--page", choices=["letter", "a4"], default="letter")
    trend.add_argument("--facility", help="facility name for the report header")
    trend.set_defaults(run=_report_trend)
