"""Build the API reference (UI-05) with pdoc.

The public Python API is the list of modules below. Everything they define
without a leading underscore is public; command-line implementations and the
Qt widgets are not part of the API (the widgets' logic is in
``psfwidget.napari.session``).

Usage::

    python tools/build_api_docs.py [OUT_DIR]     # default: site/api

Warnings from pdoc (for example an unresolvable cross-reference) make the
build fail, so CI keeps the reference complete.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

TYPE_PARAMETERS = ("T",)
PUBLIC_MODULES: tuple[str, ...] = (
    "psfwidget",
    "psfwidget.io",
    "psfwidget.profiles",
    "psfwidget.profiles.spec",
    "psfwidget.profiles.write",
    "psfwidget.settings",
    "psfwidget.analysis",
    "psfwidget.review",
    "psfwidget.synthetic",
    "psfwidget.detect.finder",
    "psfwidget.metrics.bead",
    "psfwidget.metrics.gaussian",
    "psfwidget.metrics.summary",
    "psfwidget.metrics.field",
    "psfwidget.theory",
    "psfwidget.theory.analytic",
    "psfwidget.theory.scalar",
    "psfwidget.theory.optics",
    "psfwidget.theory.model",
    "psfwidget.theory.psf3d",
    "psfwidget.average",
    "psfwidget.tracking.store",
    "psfwidget.tracking.merge",
    "psfwidget.tracking.control",
    "psfwidget.tracking.westgard",
    "psfwidget.tracking.settings",
    "psfwidget.tracking.chart",
    "psfwidget.report.document",
    "psfwidget.report.session",
    "psfwidget.report.trend",
    "psfwidget.report.audit",
    "psfwidget.report.status",
    "psfwidget.report.publish",
    "psfwidget.report.paths",
    "psfwidget.automation.config",
    "psfwidget.automation.schedule",
    "psfwidget.automation.runner",
    "psfwidget.automation.backup",
    "psfwidget.automation.glp",
    "psfwidget.napari.session",
)


def main(argv: list[str] | None = None) -> int:
    """Build the reference; return 0 on success."""
    import pdoc
    import pdoc.render

    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0] if args else "site/api")
    pdoc.render.configure(
        docformat="numpy",
        footer_text="PSFWidget API reference",
        search=True,
        show_source=True,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pdoc.pdoc(*PUBLIC_MODULES, output_directory=out)
    problems = [
        str(w.message)
        for w in caught
        # pdoc cannot resolve PEP 695 type parameters (``class Sourced[T]``) when
        # annotations are postponed; that is a pdoc limitation, not a doc gap.
        if not any(f"Import of {name} failed" in str(w.message) for name in TYPE_PARAMETERS)
    ]
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)
    pages = len(list(out.rglob("*.html")))
    print(f"wrote {pages} pages to {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
