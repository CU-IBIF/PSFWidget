"""GLP commands: report approval and verification, and the installation self-test.

Requirements: QA-02 (review and approval), QA-05 (installation and
operational qualification).

``psfwidget approve`` records a reviewer's electronic signature on a report
file: who, when, on which computer, approved or rejected, the meaning of the
signature, and a comment. The signature is stored append-only in the results
database and tied to the report's SHA-256, and a signature file
``<report>.signature.json`` is written next to the report, so a reader can
check with ``psfwidget verify`` that the report has not changed since it was
signed. A report is never regenerated in place: a re-issued report is a new
file, and its approval gets the next revision number.

``psfwidget selftest`` runs known-answer checks on synthetic data and records
the environment, writing an IQ/OQ record for the facility to sign.
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from psfwidget import __version__
from psfwidget.report.paths import unique
from psfwidget.tracking.store import ResultsDatabase, TrackingError, _user

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_APPROVED = 7
"""Exit status of ``verify`` when no approval matches the report's current content."""

DEFAULT_MEANING = "reviewed; results and report are complete and correct"
KEY_PACKAGES = (
    "numpy",
    "scipy",
    "nd2",
    "liffile",
    "czifile",
    "tifffile",
    "matplotlib",
    "reportlab",
    "openpyxl",
    "napari",
    "PySide6",
)


# ---------------------------------------------------------------- approval


def signature_path(report: Path) -> Path:
    """Return the path of a report's signature file."""
    return report.with_name(report.name + ".signature.json")


def approve(
    database: str | Path,
    report: str | Path,
    *,
    approved: bool,
    meaning: str,
    comment: str = "",
    user: str | None = None,
    session_id: int | None = None,
) -> tuple[dict[str, Any], Path]:
    """Record an approval and write the signature file; return the record and file."""
    path = Path(report)
    with ResultsDatabase(database) as db:
        record = db.approve(
            path,
            approved=approved,
            meaning=meaning,
            comment=comment,
            user=user,
            session_id=session_id,
        )
    target = unique(signature_path(path))
    content = {"report": path.name, "database": str(Path(database).resolve()), **record}
    target.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return record, target


def _run_approve(args: argparse.Namespace) -> int:
    record, target = approve(
        args.db,
        args.report,
        approved=not args.reject,
        meaning=args.meaning,
        comment=args.comment or "",
        user=args.user,
        session_id=args.session,
    )
    print(
        f"{record['decision'].capitalize()} by {record['user']} on {record['host']} at "
        f"{record['utc']} (revision {record['revision']}).\n"
        f"Report SHA-256 {record['report_sha256']}\nSignature file {target}"
    )
    return EXIT_OK


def _run_verify(args: argparse.Namespace) -> int:
    path = Path(args.report)
    with ResultsDatabase(args.db) as db:
        matches = db.approvals_for(path)
        named = [a for a in db.approvals() if a["report_name"] == path.name]
    if matches:
        for approval in matches:
            print(
                f"{path.name}: {approval['decision']} by {approval['user']} at "
                f"{approval['utc']} (revision {approval['revision']}): {approval['meaning']}"
            )
        return EXIT_OK
    if named:
        print(f"{path.name} has CHANGED since it was signed; no approval matches its content.")
    else:
        print(f"{path.name} has not been signed.")
    return EXIT_NOT_APPROVED


# ---------------------------------------------------------------- self-test


@dataclass(frozen=True)
class Check:
    """One self-test check and its outcome."""

    name: str
    requirement: str
    passed: bool
    detail: str
    seconds: float


def environment() -> dict[str, Any]:
    """Return the installation details recorded by the self-test (IQ)."""
    versions: dict[str, str | None] = {}
    for name in KEY_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "psfwidget": __version__,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "host": socket.gethostname(),
        "user": _user(None),
        "executable": sys.executable,
        "packages": versions,
    }


def _check_theory() -> str:
    from psfwidget.theory import analytic
    from psfwidget.theory.scalar import scalar_width

    widefield = analytic.widefield(520, 1.4, 1.518)
    assert abs(widefield.lateral_um - 0.51 * 0.52 / 1.4) < 1e-12
    scalar = scalar_width("widefield", None, 520, 1.4, 1.518)
    assert abs(scalar.lateral_um / widefield.lateral_um - 1) < 0.05, scalar
    two_photon = analytic.multiphoton(920, 1.05, 1.333)
    assert abs(two_photon.lateral_um - 0.337) < 0.002, two_photon
    return (
        f"widefield 520 nm NA 1.4: formula {widefield.lateral_um * 1000:.1f} nm, scalar "
        f"{scalar.lateral_um * 1000:.1f} nm; two-photon 920 nm NA 1.05: "
        f"{two_photon.lateral_um * 1000:.1f} nm"
    )


def _synthetic_stack(folder: Path) -> tuple[Any, tuple[float, float, float]]:
    import numpy as np
    import tifffile

    from psfwidget import synthetic
    from psfwidget.io import parse_assignments, read_stack

    truth = (0.60, 0.22, 0.22)
    spec = synthetic.FieldSpec(
        shape=(41, 160, 160),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec, 10, truth, 5e4, np.random.default_rng(1), margin_fwhm=2.0, min_separation_fwhm=4.0
    )
    path = folder / "selftest.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    overrides = parse_assignments(
        [
            "numerical_aperture=1.4",
            "immersion_ri=1.518",
            "modality=widefield",
            "channel.0.emission_nm=520",
            "instrument_id=selftest",
        ]
    )
    return read_stack(path, overrides=overrides), truth


def _check_measurement(folder: Path) -> str:
    from psfwidget.analysis import analyze_stack

    stack, truth = _synthetic_stack(folder)
    channel = analyze_stack(stack).channels[0]
    assert len(channel.accepted) >= 8, f"only {len(channel.accepted)} beads accepted"
    found = []
    for key, expected in zip(("fwhm_z_um", "fwhm_y_um", "fwhm_x_um"), truth, strict=True):
        median = channel.summary[key].median
        error = median / expected - 1
        assert abs(error) <= 0.03, f"{key} {median:.4f} vs {expected} ({error:+.1%})"
        found.append(f"{key} {error:+.1%}")
    return f"{len(channel.accepted)} beads; median error " + ", ".join(found)


def _check_reproducibility(folder: Path) -> str:
    from dataclasses import replace

    from psfwidget.analysis import analyze_stack

    stack, _ = _synthetic_stack(folder)
    first, second = analyze_stack(stack), analyze_stack(stack)
    assert replace(first, analysed_utc=second.analysed_utc) == second, "results differ"
    return "two analyses of the same stack are identical"


def _check_database(folder: Path) -> str:
    import sqlite3

    from psfwidget.analysis import analyze_stack

    stack, _ = _synthetic_stack(folder)
    result = analyze_stack(stack)
    from dataclasses import replace

    result = replace(result, provenance=replace(result.provenance, sha256="0" * 64))
    with ResultsDatabase(folder / "selftest.sqlite") as db:
        session_id = db.record(result, user="selftest")
        stored = db.session(session_id)
        assert stored["file_sha256"] == "0" * 64
        try:
            db._connection.execute("UPDATE sessions SET file_name = 'x'")
        except sqlite3.DatabaseError as exc:
            refused = str(exc)
        else:
            raise AssertionError("an UPDATE of a recorded session was not refused")
        assert db.audit_log()[-1]["action"] == "record"
    return f"recorded and read back; changes refused ({refused})"


def _check_report(folder: Path) -> str:
    from psfwidget.analysis import analyze_stack
    from psfwidget.report.publish import write_report
    from psfwidget.report.session import build_session_report

    stack, _ = _synthetic_stack(folder)
    report, _ = build_session_report(analyze_stack(stack), stack=stack)
    files = write_report(report, folder / "selftest_report", frozenset({"html", "pdf"}), "letter")
    sizes = {f.suffix: f.stat().st_size for f in files}
    assert all(size > 10_000 for size in sizes.values()), sizes
    assert files[1].read_bytes().startswith(b"%PDF")
    return (
        "HTML and PDF written (" + ", ".join(f"{k} {v // 1024} KB" for k, v in sizes.items()) + ")"
    )


CHECKS: tuple[tuple[str, str, Callable[[Path], str]], ...] = (
    ("theoretical PSF formulas and model", "THE-01, THE-10", lambda _: _check_theory()),
    ("FWHM of synthetic beads within 3%", "NFR-01, MET-01", _check_measurement),
    ("bit-identical re-analysis", "NFR-03", _check_reproducibility),
    ("database record and append-only", "TRK-01, NFR-07", _check_database),
    ("session report in HTML and PDF", "REP-01, REP-03", _check_report),
)


def selftest(out: str | Path | None = None) -> tuple[bool, dict[str, Any], Path | None]:
    """Run the self-test; return whether it passed, the record, and the record file."""
    started = datetime.now(UTC).replace(microsecond=0)
    checks = []
    with tempfile.TemporaryDirectory(prefix="psfwidget-selftest-") as temp:
        folder = Path(temp)
        for name, requirement, run in CHECKS:
            began = time.perf_counter()
            try:
                detail, passed = run(folder), True
            except Exception as exc:  # every check reports, none stops the others
                detail, passed = f"{type(exc).__name__}: {exc}", False
            checks.append(
                Check(name, requirement, passed, detail, round(time.perf_counter() - began, 2))
            )
    record: dict[str, Any] = {
        "record": "PSFWidget installation and operational qualification (IQ/OQ)",
        "started_utc": started.isoformat(),
        "environment": environment(),
        "checks": [check.__dict__ for check in checks],
        "result": "PASS" if all(c.passed for c in checks) else "FAIL",
        "sign_off": {"performed_by": "", "reviewed_by": "", "date": "", "comments": ""},
    }
    target = None
    if out is not None:
        folder = Path(out)
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"selftest_{record['environment']['host']}_{started:%Y%m%dT%H%M%SZ}"
        target = unique(folder / f"{stem}.json")
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record["result"] == "PASS", record, target


def _run_selftest(args: argparse.Namespace) -> int:
    passed, record, target = selftest(args.out)
    env = record["environment"]
    print(f"PSFWidget {env['psfwidget']} on {env['host']} ({env['os']}, Python {env['python']})")
    for check in record["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"  {mark}  {check['name']} [{check['requirement']}]: {check['detail']}")
    print(f"Result: {record['result']}")
    if target is not None:
        print(f"Record: {target} (fill in the sign-off section)")
    return EXIT_OK if passed else EXIT_FAILED


def add_parsers(commands: Any) -> None:
    """Add the approve, verify, and selftest commands."""
    sign = commands.add_parser(
        "approve",
        help="sign a report as approved (or rejected) in the results database",
        description=(
            "Record an electronic signature on a report file: user, time, computer, "
            "decision, meaning, and comment, tied to the file's SHA-256 (QA-02)."
        ),
    )
    sign.add_argument("report", help="the report file (HTML or PDF)")
    sign.add_argument("--db", required=True, metavar="FILE", help="results database")
    sign.add_argument("--session", type=int, help="the session the report is for")
    sign.add_argument("--reject", action="store_true", help="record a rejection instead")
    sign.add_argument(
        "--meaning",
        default=DEFAULT_MEANING,
        help=f"meaning of the signature (default: {DEFAULT_MEANING!r})",
    )
    sign.add_argument("--comment", help="optional comment")
    sign.add_argument("--user", help="your name (default: login name)")
    sign.set_defaults(run=_run_approve)

    check = commands.add_parser(
        "verify", help="check that a report is signed and unchanged since signing"
    )
    check.add_argument("report", help="the report file")
    check.add_argument("--db", required=True, metavar="FILE", help="results database")
    check.set_defaults(run=_run_verify)

    test = commands.add_parser(
        "selftest",
        help="run the installation self-test and write an IQ/OQ record",
        description=(
            "Check the installation on synthetic data with known answers and record the "
            "environment (QA-05). Exit status 1 if any check fails."
        ),
    )
    test.add_argument("--out", metavar="DIR", help="folder for the JSON record")
    test.set_defaults(run=_run_selftest)


__all__ = ["TrackingError", "add_parsers", "approve", "environment", "selftest"]
