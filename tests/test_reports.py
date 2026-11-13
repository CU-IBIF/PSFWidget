"""Tests for session and trend reports, output templates, and exports (WP5).

Requirements: REP-01 to REP-08.
"""

from __future__ import annotations

import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.automation import cli
from psfwidget.automation.analyze import write_xlsx
from psfwidget.io import Overrides, parse_assignments, read_stack
from psfwidget.profiles import load_profile
from psfwidget.report import document as doc
from psfwidget.report import paths
from psfwidget.report.publish import OutputSettings, publish_session, session_base
from psfwidget.report.render_html import render_html
from psfwidget.report.render_pdf import pdf_text, render_pdf
from psfwidget.report.session import build_session_report
from psfwidget.report.status import MetricCheck, channel_status, session_checks
from psfwidget.report.trend import DEFAULT_CHART_METRICS, build_trend_report
from psfwidget.settings import AnalysisSettings
from psfwidget.tracking.store import EventKind, ResultsDatabase

T0 = datetime(2026, 2, 2, 9, tzinfo=UTC)
PROFILE = (
    '[instrument]\nid = "scope"\nmodality = "widefield"\n'
    '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
    '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
    "[spec]\nlateral_ratio_max = 1.3\naxial_ratio_max = 1.6\n"
    "[tracking]\nbaseline_sessions = 5\n"
    'metrics = ["fwhm_z_um", "fwhm_x_um"]\n'
)
SPEC = synthetic.FieldSpec(
    shape=(31, 96, 96),
    voxel_size_um=(0.15, 0.05, 0.05),
    background_photons=20,
    offset=100,
    read_noise=2,
    bit_depth=12,
)


def _field(path: Path, axial: float, seed: int, when: datetime) -> Path:
    field = synthetic.make_bead_field(
        SPEC,
        4,
        (axial, 0.22, 0.22),
        4e4,
        np.random.default_rng(seed),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


@pytest.fixture(scope="module")
def tracked(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, list[int]]:
    """A database of seven sessions; the last is much wider axially."""
    folder = tmp_path_factory.mktemp("tracked")
    profile_path = folder / "scope.toml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    profile = load_profile(profile_path)
    database = folder / "qc.sqlite"
    rng = np.random.default_rng(3)
    ids = []
    with ResultsDatabase(database) as db:
        for day in range(7):
            axial = 0.80 if day == 6 else 0.60 + 0.005 * rng.normal()
            path = _field(folder / f"day{day}.tif", axial, day, T0 + timedelta(days=day))
            stack = read_stack(path, profile=profile)
            result = analyze_stack(stack, AnalysisSettings(), profile.spec)
            ids.append(db.record(result, user="tester"))
        db.add_event(
            occurred=T0 + timedelta(days=3, hours=2),
            instrument_id="scope",
            kind=EventKind.SERVICE,
            description="objective cleaned",
            user="tester",
        )
    return database, profile_path, ids


# ---------------------------------------------------------------- building blocks


@pytest.mark.req("REP-07")
def test_status_order_and_symbols() -> None:
    assert doc.worst([doc.Status.PASS, doc.Status.WARNING]) is doc.Status.WARNING
    assert doc.worst([doc.Status.FAIL, doc.Status.NOT_EVALUATED]) is doc.Status.FAIL
    assert doc.worst([]) is doc.Status.NOT_EVALUATED
    assert len({s.symbol for s in doc.Status}) == 4


@pytest.mark.req("REP-03")
def test_pdf_text_keeps_to_the_base_font() -> None:
    assert pdf_text("FWHM ≤ 1.3 µm, σ → λ") == "FWHM <= 1.3 µm, sigma -> lambda"
    assert pdf_text("日本") == "??"
    assert pdf_text("  ") == ""


@pytest.mark.req("REP-03", "REP-08")
def test_renderers_show_every_block(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(_png())
    rows = tuple((str(i), f"{i * 0.1:.1f}", "PASS" if i % 2 else "FAIL") for i in range(40))
    report = doc.Report(
        title="Bench <test>",
        subtitle="sub",
        status=doc.Status.WARNING,
        generated_utc=T0,
        blocks=(
            doc.Heading("Section"),
            doc.Paragraph("text & more"),
            doc.KeyValues((("Key", "Value"),)),
            doc.StatusLine(doc.Status.FAIL, "why"),
            doc.Table(("n", "v", "status"), rows, numeric=frozenset({1}), status_column=2),
            doc.PageBreak(),
            doc.Figure(_png(), "a figure"),
        ),
        branding=doc.Branding("Core Facility", logo, "Internal QC"),
    )
    html = render_html(report)
    assert "Bench &lt;test&gt;" in html
    assert "text &amp; more" in html
    assert html.count("data:image/png;base64,") == 2  # figure and logo, self-contained
    assert "Core Facility" in html
    assert "Internal QC" in html
    assert "39" in html  # every row, no truncation
    pdf = render_pdf(report, tmp_path / "r.pdf", page_size="a4")
    assert pdf.read_bytes().startswith(b"%PDF")
    assert render_pdf(report, tmp_path / "l.pdf").stat().st_size > 1000


def _png() -> bytes:
    from psfwidget.report.figures import _figure
    from psfwidget.report.figures import _png as to_png

    figure = _figure(1, 1)
    figure.add_subplot().plot([0, 1])
    return to_png(figure, dpi=30)


# ---------------------------------------------------------------- templates


@pytest.mark.req("REP-05")
def test_templates_fill_tokens_and_never_overwrite(tmp_path: Path) -> None:
    values = paths.token_values(
        instrument="SP8/left",
        objective=None,
        acquired=T0,
        file_stem="beads:1",
        series=2,
        session=7,
        kind="session",
    )
    base = paths.render(
        "{instrument}/{yyyy}/{mm}/{date}_{file_stem}_s{series}_{session}", tmp_path, values
    )
    assert base == tmp_path / "SP8-left" / "2026" / "02" / "2026-02-02_beads-1_s2_7"
    target = tmp_path / "a.pdf"
    assert paths.unique(target) == target
    target.write_bytes(b"x")
    (tmp_path / "a_2.pdf").write_bytes(b"x")
    assert paths.unique(target) == tmp_path / "a_3.pdf"


@pytest.mark.req("REP-05")
@pytest.mark.parametrize("template", ["", "{colour}", "/abs/{date}", "../{date}", "{date"])
def test_bad_templates_are_rejected(template: str) -> None:
    with pytest.raises(paths.TemplateError):
        paths.check_template(template)


# ---------------------------------------------------------------- status


def _check(severity: str) -> MetricCheck:
    return MetricCheck(
        "fwhm_z_um", 0.6, 1.0, severity, ("1_2s",) if severity != "ok" else (), "0.5-0.7"
    )


@pytest.mark.req("REP-07")
def test_channel_status_combines_spec_and_westgard(tracked: tuple[Path, Path, list[int]]) -> None:
    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day0.tif", profile=profile)
    channel = analyze_stack(stack, AnalysisSettings(), profile.spec).channels[0]
    assert channel_status(channel).status is doc.Status.PASS  # spec passes
    assert channel_status(channel, [_check("ok")]).status is doc.Status.PASS
    assert channel_status(channel, [_check("warning")]).status is doc.Status.WARNING
    assert channel_status(channel, [_check("rejection")]).status is doc.Status.FAIL
    bare = analyze_stack(
        read_stack(database.parent / "day0.tif", overrides=_optics()), AnalysisSettings(), None
    ).channels[0]
    assert channel_status(bare).status is doc.Status.NOT_EVALUATED


def _optics() -> Overrides:
    return parse_assignments(
        [
            "numerical_aperture=1.4",
            "immersion_ri=1.518",
            "modality=widefield",
            "channel.0.emission_nm=520",
        ]
    )


# ---------------------------------------------------------------- session reports


@pytest.mark.req("REP-01", "REP-04", "REP-05", "REP-06", "REP-07")
def test_session_report_and_exports(tracked: tuple[Path, Path, list[int]], tmp_path: Path) -> None:
    database, profile_path, ids = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    with ResultsDatabase(database) as db:
        checks = session_checks(db, result, ids[-1], profile.tracking)
    report, statuses = build_session_report(
        result, stack=stack, checks=checks, session_id=ids[-1], operator="tester"
    )
    assert report.status is doc.Status.FAIL  # the wide session breaks a rejection rule
    assert any("Westgard" in r for r in statuses[0].reasons)
    html = render_html(report)
    for expected in (
        "day6.tif",
        str(result.provenance.sha256),
        "tester",
        "XY",
        "fwhm_z_um",
        "Theory",
        "PSFWidget",
    ):
        assert expected in html, expected
    assert sum(isinstance(b, doc.Figure) for b in report.blocks) == 3

    settings = OutputSettings(
        root=tmp_path, reports=frozenset({"html", "pdf"}), exports=frozenset({"csv", "xlsx"})
    )
    files, _ = publish_session(
        result, settings, stack=stack, checks=checks, session_id=ids[-1], operator="tester"
    )
    base = session_base(result, settings, ids[-1])
    assert base.parent == tmp_path / "scope" / "2026"
    assert {f.suffix for f in files} == {".html", ".pdf", ".csv", ".json", ".xlsx"}
    again, _ = publish_session(result, settings, session_id=ids[-1])
    assert not set(files) & set(again)  # second run never overwrites


@pytest.mark.req("REP-04")
def test_xlsx_has_one_sheet_per_table(
    tracked: tuple[Path, Path, list[int]], tmp_path: Path
) -> None:
    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    result = analyze_stack(
        read_stack(database.parent / "day0.tif", profile=profile), AnalysisSettings(), profile.spec
    )
    path = write_xlsx(result, tmp_path / "r.xlsx")
    with zipfile.ZipFile(path) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
    for sheet in ("Summary", "Channels", "Beads", "Metadata"):
        assert f'name="{sheet}"' in workbook


# ---------------------------------------------------------------- trend reports


@pytest.mark.req("REP-02", "REP-07")
def test_trend_report(tracked: tuple[Path, Path, list[int]]) -> None:
    database, profile_path, _ = tracked
    settings = load_profile(profile_path).tracking
    with ResultsDatabase(database) as db:
        configurations = db.configurations()
        report = build_trend_report(db, configurations, settings)
        early = build_trend_report(
            db, configurations, settings, since=T0, until=T0 + timedelta(days=4)
        )
    assert report.status is doc.Status.FAIL
    html = render_html(report)
    assert "objective cleaned" in html  # events in the period are listed
    assert sum(isinstance(b, doc.Figure) for b in report.blocks) == len(DEFAULT_CHART_METRICS)
    assert early.status is not doc.Status.FAIL  # the bad session is outside the period


@pytest.mark.req("AUT-01", "REP-02", "REP-03")
def test_cli_reports(
    tracked: tuple[Path, Path, list[int]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, profile_path, _ = tracked
    assert (
        cli.main(
            [
                "report",
                "trend",
                "--db",
                str(database),
                "--out",
                str(tmp_path),
                "--profile",
                str(profile_path),
                "--instrument",
                "scope",
                "--format",
                "html",
                "--facility",
                "Core",
            ]
        )
        == 0
    )
    assert len(list(tmp_path.glob("*_scope_trend.html"))) == 1
    assert (
        cli.main(
            [
                "analyze",
                str(database.parent / "day0.tif"),
                "--profile",
                str(profile_path),
                "--out",
                str(tmp_path),
                "--report",
                "html,pdf",
                "--xlsx",
                "--no-hash",
            ]
        )
        == 0
    )
    assert {p.suffix for p in tmp_path.glob("day0*")} >= {".html", ".pdf", ".xlsx", ".csv"}
    assert (
        cli.main(
            [
                "analyze",
                str(database.parent / "day0.tif"),
                "--profile",
                str(profile_path),
                "--out",
                str(tmp_path),
                "--report",
                "docx",
            ]
        )
        == 2
    )
    capsys.readouterr()
