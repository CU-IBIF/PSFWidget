"""Tests for the audit review report (QA-06)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from psfwidget.automation import cli
from psfwidget.report import document as doc
from psfwidget.report.audit import build_audit_report
from psfwidget.report.render_html import render_html
from psfwidget.tracking.store import EventKind, ResultsDatabase


@pytest.mark.req("QA-06", "QA-02")
def test_audit_report_lists_every_kind_of_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "qc.sqlite"
    report_file = tmp_path / "r.html"
    report_file.write_text("x", encoding="utf-8")
    with ResultsDatabase(database) as db:
        db.add_event(
            occurred=datetime(2026, 11, 1, tzinfo=UTC),
            instrument_id="sp8",
            kind=EventKind.SERVICE,
            description="laser realigned",
            user="ab",
        )
        db.approve(
            report_file, approved=False, meaning="reviewed", comment="wrong slide", user="cd"
        )
        report = build_audit_report(db, since=datetime.now(UTC) - timedelta(days=1))
        empty = build_audit_report(db, until=datetime(2000, 1, 1, tzinfo=UTC))
    html = render_html(report)
    for text in (
        "Audit trail review",
        "laser realigned",
        "report rejected",
        "wrong slide",
        "Exclusions and inclusions",
        "None in this period",
        "psfwidget approve",
    ):
        assert text in html, text
    tables = [b for b in report.blocks if isinstance(b, doc.Table)]
    assert ("event", "1") in tables[0].rows  # counts by action
    assert "No audit entries" in render_html(empty)
    assert (
        cli.main(
            [
                "report",
                "audit",
                "--db",
                str(database),
                "--out",
                str(tmp_path / "out"),
                "--until",
                datetime.now(UTC).date().isoformat(),
                "--format",
                "html,pdf",
            ]
        )
        == 0
    )
    written = sorted((tmp_path / "out").iterdir())
    assert [p.suffix for p in written] == [".html", ".pdf"]
    assert "laser realigned" in written[0].read_text("utf-8")
    capsys.readouterr()
