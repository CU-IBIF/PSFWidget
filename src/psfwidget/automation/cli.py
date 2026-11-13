"""Command-line entry point (``psfwidget``).

Commands so far: ``info`` and ``analyze``. The ingest, report, baseline,
event, and database commands (T6.1, AUT-01) are added as their work packages
land.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from psfwidget import __version__
from psfwidget.analysis import analyze_stack
from psfwidget.automation import analyze, info
from psfwidget.io import (
    ImageStack,
    InvalidMetadataError,
    UnsupportedFileError,
    list_series,
    parse_assignments,
    read_stack,
)
from psfwidget.profiles import InstrumentProfile, ProfileError, load_profile
from psfwidget.profiles.spec import Status
from psfwidget.settings import AnalysisSettings

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_ERROR = 2
EXIT_SPEC_FAIL = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="psfwidget",
        description="PSF analysis and tracking for microscope quality control.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    show = commands.add_parser(
        "info",
        help="show a file's series and resolved metadata",
        description=(
            "Show the image series in a file and the acquisition metadata PSFWidget "
            "would use, with where each value came from (file, profile, or user)."
        ),
    )
    _add_input_arguments(show)
    show.add_argument("--json", action="store_true", help="print JSON instead of text")
    show.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 if the stack is not ready for analysis",
    )
    show.set_defaults(run=_run_info)

    run = commands.add_parser(
        "analyze",
        help="measure the beads in a file and write CSV and JSON results",
        description=(
            "Detect beads, measure their FWHM, intensity, and quality, compare with the "
            "theoretical PSF, and write <name>_beads.csv, _summary.csv, _channels.csv, and "
            "_run.json to the output folder."
        ),
    )
    _add_input_arguments(run)
    run.add_argument(
        "--out", default=".", metavar="DIR", help="output folder (default: current folder)"
    )
    run.add_argument(
        "--channel",
        dest="channels",
        type=int,
        action="append",
        metavar="INDEX",
        help="analyse only this channel; may be repeated (default: all)",
    )
    run.add_argument(
        "--setting",
        dest="settings",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="change an analysis setting, e.g. --setting min_snr=5; may be repeated",
    )
    run.add_argument("--overwrite", action="store_true", help="replace existing output files")
    run.add_argument(
        "--fail-on-spec",
        action="store_true",
        help=f"exit with status {EXIT_SPEC_FAIL} if any channel fails its spec limits",
    )
    run.set_defaults(run=_run_analyze)
    return parser


def _add_input_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("path", help="an .nd2, .lif, .czi, .tif, or .tiff file")
    command.add_argument("--series", type=int, default=0, help="series index (default: 0)")
    command.add_argument("--timepoint", type=int, default=0, help="timepoint index (default: 0)")
    command.add_argument("--tile", type=int, default=0, help="mosaic tile index (default: 0)")
    command.add_argument("--profile", help="instrument profile (TOML file)")
    command.add_argument("--objective", help="id of the profile objective to use")
    command.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "override a metadata value, e.g. --set numerical_aperture=1.4 or "
            "--set channel.0.emission_nm=515; may be repeated"
        ),
    )
    command.add_argument("--no-hash", action="store_true", help="skip the SHA-256 hash")


def _open(args: argparse.Namespace) -> tuple[ImageStack, InstrumentProfile | None]:
    profile = load_profile(args.profile) if args.profile else None
    overrides = parse_assignments(args.assignments) if args.assignments else None
    stack = read_stack(
        args.path,
        series=args.series,
        timepoint=args.timepoint,
        tile=args.tile,
        profile=profile,
        objective_id=args.objective,
        overrides=overrides,
        compute_hash=not args.no_hash,
    )
    return stack, profile


def parse_settings(assignments: list[str]) -> AnalysisSettings:
    """Build analysis settings from ``key=value`` strings.

    Raises
    ------
    ValueError
        If a key is unknown or a value has the wrong type.
    """
    defaults = AnalysisSettings()
    values: dict[str, Any] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        key, raw = key.strip(), raw.strip()
        if not separator or not key or not raw:
            raise ValueError(f"expected key=value, got {assignment!r}")
        if not hasattr(defaults, key):
            raise ValueError(f"unknown analysis setting: {key}")
        current = getattr(defaults, key)
        if raw.lower() in ("none", "off"):
            values[key] = None
        elif isinstance(current, bool):
            if raw.lower() not in ("true", "false", "yes", "no", "on", "1", "0"):
                raise ValueError(f"{key} must be true or false, got {raw!r}")
            values[key] = raw.lower() in ("true", "yes", "on", "1")
        elif isinstance(current, int):
            values[key] = int(raw)
        else:
            values[key] = float(raw)
    return AnalysisSettings.from_dict(defaults.to_dict() | values)


def _run_info(args: argparse.Namespace) -> int:
    series = list_series(args.path)
    stack, _ = _open(args)
    description = info.describe(stack, series)
    print(info.format_json(description) if args.json else info.format_text(description))
    return EXIT_NOT_READY if args.strict and not description["analyzable"] else EXIT_OK


def _run_analyze(args: argparse.Namespace) -> int:
    settings = parse_settings(args.settings)
    stack, profile = _open(args)
    problems = stack.problems()
    if problems:
        print(f"psfwidget: {Path(args.path).name} is not ready for analysis:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Run 'psfwidget info' to see the metadata, then supply the missing values "
            "with --profile or --set.",
            file=sys.stderr,
        )
        return EXIT_NOT_READY
    spec = profile.spec if profile is not None else None
    result = analyze_stack(stack, settings, spec, channels=args.channels)
    written = analyze.write_outputs(result, Path(args.out), overwrite=args.overwrite)
    print(analyze.format_text(result))
    print("\nWrote " + ", ".join(str(p) for p in written))
    failed = any(c.spec is not None and c.spec.status is Status.FAIL for c in result.channels)
    return EXIT_SPEC_FAIL if args.fail_on_spec and failed else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    try:
        return int(args.run(args))
    except (
        FileExistsError,
        FileNotFoundError,
        IndexError,
        UnsupportedFileError,
        InvalidMetadataError,
        ProfileError,
        ValueError,
    ) as exc:
        print(f"psfwidget: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
