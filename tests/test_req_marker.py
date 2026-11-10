"""Tests for the ``req`` marker and ``--req-report`` option in conftest.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

CONFTEST = Path(__file__).with_name("conftest.py").read_text("utf-8")


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST.replace('pytest_plugins = ["pytester"]\n', ""))
    return pytester


def test_unknown_requirement_id_fails_collection(suite: pytest.Pytester) -> None:
    suite.makepyfile(
        """
        import pytest

        @pytest.mark.req("XYZ-99")
        def test_x():
            pass
        """
    )
    result = suite.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown requirement ID 'XYZ-99'*"])


def test_req_report_lists_each_requirement_and_outcome(suite: pytest.Pytester) -> None:
    suite.makepyfile(
        """
        import pytest

        @pytest.mark.req("MET-01", "NFR-01")
        def test_pass():
            pass

        @pytest.mark.req("MET-02")
        def test_fail():
            assert False

        @pytest.mark.req("MET-03")
        @pytest.mark.skip(reason="later")
        def test_skip():
            pass

        def test_untagged():
            pass
        """
    )
    report = suite.path / "req.csv"
    suite.runpytest(f"--req-report={report}").assert_outcomes(passed=2, failed=1, skipped=1)
    with report.open(newline="", encoding="utf-8") as handle:
        rows = {
            (r["requirement"], r["test"].split("::")[-1], r["outcome"])
            for r in csv.DictReader(handle)
        }
    assert rows == {
        ("MET-01", "test_pass", "passed"),
        ("NFR-01", "test_pass", "passed"),
        ("MET-02", "test_fail", "failed"),
        ("MET-03", "test_skip", "skipped"),
    }
