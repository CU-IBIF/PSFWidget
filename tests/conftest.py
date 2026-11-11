"""Shared pytest configuration.

Provides the ``req`` marker that links tests to requirement IDs
(docs/VV_PLAN.md §4)::

    @pytest.mark.req("MET-01", "NFR-01")
    def test_fwhm_x_matches_ground_truth(): ...

Unknown IDs fail collection. ``--req-report=PATH`` writes a CSV of
requirement, test, and outcome after the run.

Also provides the ``golden_file`` fixture, which returns the path of a file
listed in ``tests/data/manifest.json`` (see ``tools/fetch_test_data.py``).
"""

from __future__ import annotations

import csv
import os
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

import fetch_test_data
import traceability

pytest_plugins = ["pytester"]

_RESULTS = pytest.StashKey[list[tuple[str, str, str]]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--req-report",
        metavar="PATH",
        default=None,
        help="write a requirement-to-test coverage CSV to PATH",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "req(*ids): requirement IDs this test verifies")
    config.stash[_RESULTS] = []


def _req_ids(item: pytest.Item) -> list[str]:
    return [rid for mark in item.iter_markers("req") for rid in mark.args]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    known = traceability.load_ids()
    errors = [
        f"{item.nodeid}: unknown requirement ID {rid!r}"
        for item in items
        for rid in _req_ids(item)
        if rid not in known
    ]
    if errors:
        raise pytest.UsageError("\n".join(errors))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    # Record the test-body result, or a setup failure/skip that prevented it.
    if report.when == "call" or (report.when == "setup" and not report.passed):
        item.config.stash[_RESULTS].extend(
            (rid, item.nodeid, report.outcome) for rid in _req_ids(item)
        )
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    path = session.config.getoption("--req-report")
    if not path:
        return
    results = session.config.stash[_RESULTS]
    rows = sorted(results)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("requirement", "test", "outcome"))
        writer.writerows(rows)


@pytest.fixture(scope="session")
def golden_file() -> Callable[[str], Path]:
    """Return a function mapping a manifest file name to its verified local path.

    A file that is not in the cache skips the test. With
    ``PSFWIDGET_REQUIRE_TEST_DATA=1`` (set in CI) a missing downloadable file
    fails instead, so a broken download cannot silently disable tests.
    Local-only files (no URL) always skip when absent.
    """
    manifest = fetch_test_data.load_manifest()
    verified: dict[str, Path] = {}

    def get(name: str) -> Path:
        if name in verified:
            return verified[name]
        entry = manifest[name]
        state = fetch_test_data.status(entry)
        if state == "corrupt":
            pytest.fail(f"{name}: cached file does not match the manifest sha256")
        if state == "missing":
            message = f"{name} not in tests/data/cache; run: python tools/fetch_test_data.py"
            if entry.is_public and os.environ.get("PSFWIDGET_REQUIRE_TEST_DATA") == "1":
                pytest.fail(message)
            pytest.skip(message)
        verified[name] = entry.path
        return entry.path

    return get
