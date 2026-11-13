"""Write a session's reports and exports under an output root (REP-03 to REP-05)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from psfwidget.analysis import FieldResult
from psfwidget.io.model import ImageStack
from psfwidget.report import paths
from psfwidget.report.document import Branding, Report
from psfwidget.report.status import ChannelStatus, MetricCheck

REPORT_FORMATS = frozenset({"html", "pdf"})
EXPORT_FORMATS = frozenset({"csv", "xlsx"})
DEFAULT_SESSION_TEMPLATE = "{instrument}/{yyyy}/{date}_{file_stem}_s{series}"
DEFAULT_TREND_TEMPLATE = "{instrument}/trend/{date}_{instrument}_trend"


@dataclass(frozen=True)
class OutputSettings:
    """Where and how reports and exports are written."""

    root: Path
    session_template: str = DEFAULT_SESSION_TEMPLATE
    trend_template: str = DEFAULT_TREND_TEMPLATE
    reports: frozenset[str] = frozenset({"html", "pdf"})
    exports: frozenset[str] = frozenset({"csv"})
    page_size: str = "letter"
    branding: Branding = field(default_factory=Branding)

    def __post_init__(self) -> None:
        paths.check_template(self.session_template)
        paths.check_template(self.trend_template)
        if not self.reports <= REPORT_FORMATS:
            raise ValueError(f"report formats must be among {sorted(REPORT_FORMATS)}")
        if not self.exports <= EXPORT_FORMATS:
            raise ValueError(f"export formats must be among {sorted(EXPORT_FORMATS)}")


def write_report(
    report: Report, base: Path, formats: frozenset[str], page_size: str
) -> list[Path]:
    """Write ``report`` to ``base`` plus each format's extension, never overwriting."""
    from psfwidget.report.render_html import write_html

    written = []
    if "html" in formats:
        written.append(write_html(report, paths.unique(base.with_name(base.name + ".html"))))
    if "pdf" in formats:
        from psfwidget.report.render_pdf import render_pdf

        written.append(
            render_pdf(
                report, paths.unique(base.with_name(base.name + ".pdf")), page_size=page_size
            )
        )
    return written


def session_base(result: FieldResult, settings: OutputSettings, session_id: int | None) -> Path:
    """Return the output path, without extension, for a session's files."""
    meta = result.metadata
    acquired = meta.acquired_utc.value if meta.acquired_utc is not None else result.analysed_utc
    values = paths.token_values(
        instrument=meta.instrument_id.value if meta.instrument_id is not None else "unknown",
        objective=None if meta.objective_id is None else meta.objective_id.value,
        acquired=acquired,
        file_stem=Path(result.provenance.path).stem.removesuffix(".ome"),
        series=result.provenance.series_index,
        session=session_id,
        kind="session",
    )
    return paths.render(settings.session_template, settings.root, values)


def publish_session(
    result: FieldResult,
    settings: OutputSettings,
    *,
    stack: ImageStack | None = None,
    checks: Mapping[int, Sequence[MetricCheck]] | None = None,
    session_id: int | None = None,
    operator: str | None = None,
) -> tuple[list[Path], list[ChannelStatus]]:
    """Write a session's reports and exports; return the files and channel statuses."""
    from psfwidget.automation.analyze import write_outputs, write_xlsx
    from psfwidget.report.session import build_session_report

    base = session_base(result, settings, session_id)
    report, statuses = build_session_report(
        result,
        stack=stack,
        checks=checks,
        session_id=session_id,
        operator=operator,
        branding=settings.branding,
    )
    written = write_report(report, base, settings.reports, settings.page_size)
    if "csv" in settings.exports:
        stem = paths.unique(base.with_name(base.name + "_summary.csv")).name.removesuffix(
            "_summary.csv"
        )
        written += write_outputs(result, base.parent, stem=stem)
    if "xlsx" in settings.exports:
        written.append(write_xlsx(result, paths.unique(base.with_name(base.name + ".xlsx"))))
    return written, statuses


def trend_base(settings: OutputSettings, instrument: str, when: datetime | None = None) -> Path:
    """Return the output path, without extension, for a trend report."""
    moment = when or datetime.now(UTC)
    values = paths.token_values(
        instrument=instrument,
        objective=None,
        acquired=moment,
        file_stem=instrument,
        series=0,
        session=None,
        kind="trend",
    )
    return paths.render(settings.trend_template, settings.root, values)
