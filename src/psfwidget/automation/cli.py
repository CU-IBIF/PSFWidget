"""Command-line entry point (``psfwidget``).

Commands so far: ``info``. The analysis, ingest, report, baseline, event,
and database commands (T6.1, AUT-01) are added as their work packages land.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from psfwidget import __version__
from psfwidget.automation import info
from psfwidget.io import (
    InvalidMetadataError,
    UnsupportedFileError,
    list_series,
    parse_assignments,
    read_stack,
)
from psfwidget.profiles import ProfileError, load_profile

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_ERROR = 2


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
    show.add_argument("path", help="an .nd2, .lif, .czi, .tif, or .tiff file")
    show.add_argument("--series", type=int, default=0, help="series index (default: 0)")
    show.add_argument("--timepoint", type=int, default=0, help="timepoint index (default: 0)")
    show.add_argument("--tile", type=int, default=0, help="mosaic tile index (default: 0)")
    show.add_argument("--profile", help="instrument profile (TOML file)")
    show.add_argument("--objective", help="id of the profile objective to use")
    show.add_argument(
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
    show.add_argument("--json", action="store_true", help="print JSON instead of text")
    show.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 if the stack is not ready for analysis",
    )
    show.add_argument("--no-hash", action="store_true", help="skip the SHA-256 hash")
    show.set_defaults(run=_run_info)
    return parser


def _run_info(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile) if args.profile else None
    overrides = parse_assignments(args.assignments) if args.assignments else None
    series = list_series(args.path)
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
    description = info.describe(stack, series)
    print(info.format_json(description) if args.json else info.format_text(description))
    return EXIT_NOT_READY if args.strict and not description["analyzable"] else EXIT_OK


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
        FileNotFoundError,
        UnsupportedFileError,
        InvalidMetadataError,
        ProfileError,
    ) as exc:
        print(f"psfwidget: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
