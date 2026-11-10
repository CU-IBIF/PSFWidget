"""Command-line entry point (``psfwidget``).

Only ``--version`` exists so far. The analysis, ingest, report, baseline,
event, and database commands (T6.1, AUT-01) are added as their work packages
land.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from psfwidget import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="psfwidget",
        description="PSF analysis and tracking for microscope quality control.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
