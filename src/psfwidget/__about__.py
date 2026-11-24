"""The package version, derived from git when the package is built (QA-03).

A tagged commit ``vX.Y.Z`` gives ``X.Y.Z``. Any other commit gives the next
version's development release plus the commit, for example
``0.1.dev187+g1b89b85``, so results record the exact code that produced them.
The build writes the version to ``_version.py``; an installed package without
that file falls back to the installed distribution's metadata.
"""

from __future__ import annotations

try:
    from psfwidget._version import __version__
except ImportError:  # running from a source tree that was never built or installed
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("psfwidget")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
