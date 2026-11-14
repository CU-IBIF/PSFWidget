"""The ``psfwidget track`` commands: status, charts, events, exclusions, export (WP4).

Requirements: TRK-03 to TRK-10, NFR-07; AUT-01 in part.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psfwidget.profiles import load_profile
from psfwidget.tracking.control import BaselineMethod, ControlSeries, series_from_database
from psfwidget.tracking.settings import TRACKABLE_METRICS, TrackingSettings
from psfwidget.tracking.store import (
    Configuration,
    EventKind,
    ResultsDatabase,
    TrackingError,
    configurations_matching,
)

EXIT_OK = 0
EXIT_REJECTION = 4
"""Exit status of ``status --fail-on-rejection`` when a latest session breaks a rejection rule."""

SEVERITY_ORDER = {"rejection": 3, "warning": 2, "ok": 1, "none": 0}


def parse_time(text: str) -> datetime:
    """Parse an ISO date or date-time; a bare date or naive time is taken as UTC.

    Raises
    ------
    ValueError
        If the text is not an ISO date.
    """
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"not an ISO date or date-time: {text!r}") from None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _settings(args: argparse.Namespace) -> TrackingSettings:
    if getattr(args, "profile", None):
        return load_profile(args.profile).tracking
    return TrackingSettings()


def _selected(db: ResultsDatabase, args: argparse.Namespace) -> list[Configuration]:
    found = configurations_matching(
        db.configurations(),
        instrument=getattr(args, "instrument", None),
        objective=getattr(args, "objective", None),
        channel=getattr(args, "channel_key", None),
    )
    if not found:
        raise TrackingError(
            "no recorded results match the given instrument, objective, and channel"
        )
    return found


def series_for(
    db: ResultsDatabase, configuration: Configuration, metric: str, settings: TrackingSettings
) -> ControlSeries:
    """Build the control series of one metric of one configuration."""
    return series_from_database(db, configuration, metric, settings)


def status_lines(
    db: ResultsDatabase, configurations: Sequence[Configuration], settings: TrackingSettings
) -> tuple[list[str], str]:
    """Return a status report and the worst severity of any latest session."""
    lines: list[str] = []
    worst = "none"
    for configuration in configurations:
        lines.append(f"{configuration.label}  ({db.session_count(configuration)} sessions)")
        for metric in settings.metrics:
            series = series_for(db, configuration, metric, settings)
            latest = series.latest
            if latest is None:
                continue
            state = latest.severity
            if SEVERITY_ORDER[state] > SEVERITY_ORDER[worst]:
                worst = state
            z = "" if latest.z is None else f"  z {latest.z:+.2f}"
            rules = ", ".join(sorted(r.value for r in latest.violations))
            segment = series.segments[latest.segment]
            label = {"none": segment.status, "ok": "ok"}.get(state, state.upper())
            if state != "none" and segment.baseline is not None and segment.baseline.provisional:
                label += f" (limits provisional: {segment.status.split('(')[-1].rstrip(')')})"
            value = latest.session.value
            lines.append(
                f"  {metric:22} {value:>12.4g}{z:<10}  {label}{f' ({rules})' if rules else ''}"
            )
        lines.append("")
    return lines, worst


def _run_list(args: argparse.Namespace) -> int:
    with ResultsDatabase(args.db) as db:
        configurations = db.configurations()
        if not configurations:
            print("No results recorded yet.")
            return EXIT_OK
        for configuration in configurations:
            events = db.events(configuration)
            print(
                f"{configuration.label}: {db.session_count(configuration)} sessions, "
                f"{len(events)} events"
            )
    return EXIT_OK


def _run_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with ResultsDatabase(args.db) as db:
        lines, worst = status_lines(db, _selected(db, args), settings)
    print("\n".join(lines).rstrip())
    return EXIT_REJECTION if args.fail_on_rejection and worst == "rejection" else EXIT_OK


def _run_chart(args: argparse.Namespace) -> int:
    from psfwidget.tracking.chart import ChartError, draw

    settings = _settings(args)
    metrics = args.metrics or list(settings.metrics)
    unknown = sorted(set(metrics) - set(TRACKABLE_METRICS))
    if unknown:
        raise TrackingError(f"unknown metric(s) {unknown}")
    written = []
    with ResultsDatabase(args.db) as db:
        for configuration in _selected(db, args):
            for metric in metrics:
                series = series_for(db, configuration, metric, settings)
                path = Path(args.out) / f"{configuration.slug}_{metric}.{args.format}"
                try:
                    written.append(draw(series, path))
                except ChartError as exc:
                    print(f"skipped {configuration.label} {metric}: {exc}")
    for path in written:
        print(f"wrote {path}")
    return EXIT_OK


def _baseline(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.baseline_range and args.targets:
        raise TrackingError("choose either --baseline-range or --target, not both")
    sessions = args.baseline_sessions or 20
    if args.baseline_range:
        start, end = (parse_time(v) for v in args.baseline_range)
        method = BaselineMethod("range", sessions, start=start, end=end)
    elif args.targets:
        targets: dict[str, list[float]] = {}
        for item in args.targets:
            metric, _, values = item.partition("=")
            mean, _, sd = values.partition(",")
            if metric not in TRACKABLE_METRICS or not sd:
                raise TrackingError(f"targets look like fwhm_z_um=0.62,0.03; got {item!r}")
            targets[metric] = [float(mean), float(sd)]
        method = BaselineMethod.from_dict(
            {"method": "target", "sessions": sessions, "targets": targets}
        )
    elif args.baseline_sessions:
        method = BaselineMethod("first_n", sessions)
    else:
        return None
    validated = BaselineMethod.from_dict(method.to_dict())
    return validated.to_dict()


def _run_event(args: argparse.Namespace) -> int:
    baseline = _baseline(args)
    if baseline is not None and not args.rebaseline:
        raise TrackingError(
            "--baseline-sessions, --baseline-range, and --target need --rebaseline"
        )
    occurred = parse_time(args.date) if args.date else datetime.now(UTC)
    with ResultsDatabase(args.db) as db:
        event_id = db.add_event(
            occurred=occurred,
            instrument_id=args.instrument,
            kind=EventKind(args.kind),
            description=args.description,
            objective_id=args.objective,
            channel_key=args.channel_key,
            rebaseline=args.rebaseline,
            baseline=baseline,
            user=args.user,
        )
    reset = " and starts a new baseline" if args.rebaseline else ""
    print(f"Recorded event {event_id} ({args.kind}, {occurred.date()}){reset}.")
    return EXIT_OK


def _run_exclusion(args: argparse.Namespace, excluded: bool) -> int:
    with ResultsDatabase(args.db) as db:
        db.set_excluded(
            args.session, excluded, args.reason, channel_index=args.channel_index, user=args.user
        )
    scope = "" if args.channel_index is None else f" channel {args.channel_index}"
    verb = "Excluded" if excluded else "Included"
    print(f"{verb} session {args.session}{scope}.")
    return EXIT_OK


def _run_export(args: argparse.Namespace) -> int:
    with ResultsDatabase(args.db) as db:
        count = db.export_csv(args.out)
    print(f"wrote {count} rows to {args.out}")
    return EXIT_OK


def _run_backup(args: argparse.Namespace) -> int:
    with ResultsDatabase(args.db) as db:
        db.backup(args.out)
    print(f"backed up {args.db} to {args.out}")
    return EXIT_OK


def _run_audit(args: argparse.Namespace) -> int:
    since = parse_time(args.since) if args.since else None
    until = parse_time(args.until) if args.until else None
    with ResultsDatabase(args.db) as db:
        if args.csv:
            count = db.export_audit_csv(args.csv, since, until)
            print(f"Wrote {count} audit entries to {args.csv}")
            return EXIT_OK
        for entry in db.audit_entries(since, until):
            details = ", ".join(
                f"{k}={v}" for k, v in entry["details"].items() if v not in (None, [])
            )
            host = f"@{entry['host']}" if entry["host"] else ""
            print(f"{entry['utc']}  {entry['user'] + host:<24} {entry['action']:<16} {details}")
    return EXIT_OK


def _filters(command: argparse.ArgumentParser) -> None:
    command.add_argument("--instrument", help="instrument id")
    command.add_argument("--objective", help="objective id")
    command.add_argument(
        "--channel",
        dest="channel_key",
        metavar="KEY",
        help="channel key (the profile's channel id, or the channel name)",
    )


def _database(command: argparse.ArgumentParser) -> None:
    command.add_argument("--db", required=True, help="results database file (SQLite)")


def _user(command: argparse.ArgumentParser) -> None:
    command.add_argument("--user", help="name recorded in the audit log (default: login name)")


def add_parser(commands: Any) -> None:
    """Add the ``track`` command and its subcommands to the CLI."""
    track = commands.add_parser(
        "track",
        help="results database: status, Levey-Jennings charts, events, exclusions",
        description="Work with the results database written by 'psfwidget analyze --db'.",
    )
    sub = track.add_subparsers(dest="track_command", metavar="command", required=True)

    listing = sub.add_parser("list", help="list tracked configurations")
    _database(listing)
    listing.set_defaults(run=_run_list)

    status = sub.add_parser(
        "status",
        help="latest session of each configuration against its control limits",
    )
    _database(status)
    _filters(status)
    status.add_argument("--profile", help="instrument profile with [tracking] settings")
    status.add_argument(
        "--fail-on-rejection",
        action="store_true",
        help=f"exit with status {EXIT_REJECTION} if a latest session breaks a rejection rule",
    )
    status.set_defaults(run=_run_status)

    chart = sub.add_parser("chart", help="draw Levey-Jennings charts")
    _database(chart)
    _filters(chart)
    chart.add_argument("--profile", help="instrument profile with [tracking] settings")
    chart.add_argument(
        "--metric",
        dest="metrics",
        action="append",
        metavar="NAME",
        help="metric to chart; may be repeated (default: the tracked metrics)",
    )
    chart.add_argument("--out", default=".", metavar="DIR", help="output folder")
    chart.add_argument("--format", choices=("png", "svg", "pdf"), default="png")
    chart.set_defaults(run=_run_chart)

    event = sub.add_parser("event", help="record an event, optionally starting a new baseline")
    _database(event)
    event.add_argument("--instrument", required=True, help="instrument id")
    event.add_argument("--objective", help="only this objective (default: all)")
    event.add_argument(
        "--channel", dest="channel_key", metavar="KEY", help="only this channel (default: all)"
    )
    event.add_argument("--kind", required=True, choices=[k.value for k in EventKind])
    event.add_argument("--description", required=True)
    event.add_argument("--date", help="when it happened, ISO format (default: now)")
    event.add_argument(
        "--rebaseline", action="store_true", help="start new control limits from this event"
    )
    event.add_argument(
        "--baseline-sessions",
        type=int,
        metavar="N",
        help="new limits from the first N sessions after the event (default 20)",
    )
    event.add_argument(
        "--baseline-range",
        nargs=2,
        metavar=("START", "END"),
        help="new limits from the sessions acquired between two dates",
    )
    event.add_argument(
        "--target",
        dest="targets",
        action="append",
        metavar="METRIC=MEAN,SD",
        help="new limits entered directly; may be repeated",
    )
    _user(event)
    event.set_defaults(run=_run_event)

    for name, excluded in (("exclude", True), ("include", False)):
        command = sub.add_parser(
            name,
            help=(
                "leave a session out of the statistics (kept in the database)"
                if excluded
                else "bring an excluded session back into the statistics"
            ),
        )
        _database(command)
        command.add_argument("session", type=int, help="session id")
        command.add_argument(
            "--channel",
            dest="channel_index",
            type=int,
            metavar="INDEX",
            help="only this channel of the session",
        )
        command.add_argument("--reason", required=True)
        _user(command)
        command.set_defaults(run=lambda a, e=excluded: _run_exclusion(a, e))

    export = sub.add_parser("export", help="write every session's metrics to a CSV file")
    _database(export)
    export.add_argument("--out", required=True, help="CSV file to write")
    export.set_defaults(run=_run_export)

    backup = sub.add_parser("backup", help="copy the database to a new file")
    _database(backup)
    backup.add_argument("--out", required=True, help="new database file")
    backup.set_defaults(run=_run_backup)

    audit = sub.add_parser("audit", help="print or export the audit log (QA-06)")
    _database(audit)
    audit.add_argument("--since", help="first date or time to include (ISO)")
    audit.add_argument("--until", help="last date or time to include (ISO)")
    audit.add_argument("--csv", metavar="FILE", help="write CSV instead of printing")
    audit.set_defaults(run=_run_audit)
