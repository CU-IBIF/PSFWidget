"""Tests for session and trend reports, output templates, and exports (WP5).

Requirements: REP-01 to REP-08.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import replace
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
from psfwidget.report.session import SAMPLE_BEADS, build_session_report, fmt, sample_beads
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
@pytest.mark.parametrize(
    "template",
    ["", "{colour}", "/abs/{date}", "\\\\server\\{date}", "C:/x/{date}", "../{date}", "{date"],
)
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


@pytest.mark.req("REP-01", "REP-04", "REP-05", "REP-06", "REP-07", "THE-07")
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
        "Scalar theory",
        "Vectorial theory",
        "Both models with these settings",
        "The scalar model is the reference for spec limits and tracking",
        "PSFWidget",
    ):
        assert expected in html, expected
    # The summary table gives both models; the reference ratio is the per-bead median.
    summary = next(
        b for b in report.blocks if isinstance(b, doc.Table) and b.header[0] == "Metric"
    )
    fwhm_x = next(row for row in summary.rows if row[0] == "FWHM x (µm)")
    channel = result.channels[0]
    assert channel.theory is not None
    assert channel.theory.vectorial_lateral_um is not None
    assert fwhm_x[4] == fmt(channel.theory.scalar_lateral_um)
    assert fwhm_x[6] == fmt(channel.theory.vectorial_lateral_um)
    # Ratios are shown to three significant figures.
    assert float(fwhm_x[5]) == pytest.approx(channel.summary["ratio_x"].median, abs=0.006)
    assert float(fwhm_x[7]) == pytest.approx(
        channel.summary["fwhm_x_um"].median / channel.theory.vectorial_lateral_um, abs=0.006
    )
    assert float(fwhm_x[7]) < float(fwhm_x[5])  # the vectorial focus is wider laterally
    assert sum(isinstance(b, doc.Figure) for b in report.blocks) == 4  # with the field map
    assert re.search(r"average PSF of the \d+ accepted beads", html)
    assert "slices through the centre of bead" in html
    assert "accepted beads across the field of view" in html
    assert "Right: axial FWHM" in html
    # Each channel's full bead table is an appendix after the provenance, accepted first;
    # the channel section shows a reproducible random sample of accepted beads.
    headings = [b.text for b in report.blocks if isinstance(b, doc.Heading)]
    appendix = next(h for h in headings if h.startswith("Appendix A: beads of channel 0"))
    assert headings.index(appendix) > headings.index("Provenance")
    assert headings.count("Sample of accepted beads") == len(result.channels)
    sample_table = next(
        b
        for b in report.blocks
        if isinstance(b, doc.Table) and b.header[:2] == ("Bead", "z, y, x (px)")
    )
    channel = result.channels[0]
    accepted = {b.bead_id for b in channel.accepted}
    sampled = [int(row[0]) for row in sample_table.rows]
    assert len(sampled) == min(len(accepted), SAMPLE_BEADS)
    assert set(sampled) <= accepted
    assert sampled == sorted(sampled)
    assert "Appendix A" in (sample_table.caption or "")
    bead_table = next(
        b for b in report.blocks if isinstance(b, doc.Table) and b.header[:2] == ("Bead", "Status")
    )
    # Only accepted beads are listed (no reviewer here); automatic rejections are counted.
    assert {row[1] for row in bead_table.rows} == {"accepted"}
    assert len(bead_table.rows) == len(result.channels[0].accepted)
    rejected = len(result.channels[0].beads) - len(result.channels[0].accepted)
    if rejected:
        assert f"Not listed: {rejected} beads rejected" in (bead_table.caption or "")
    accepted_ids = [int(row[0]) for row in bead_table.rows if row[1] == "accepted"]
    assert accepted_ids == sorted(accepted_ids)
    assert "Accepted beads across the field" in html

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


@pytest.mark.req("THE-07", "MET-14")
def test_orthogonal_comparison_rows(tracked: tuple[Path, Path, list[int]]) -> None:
    from psfwidget.report import figures

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    settings = AnalysisSettings()
    result = analyze_stack(stack, settings, profile.spec)
    channel = result.channels[0]
    bead = figures.representative_bead(channel)
    assert bead is not None
    volume, voxel = stack.channel(channel.index), stack.voxel_size_um
    image, average = figures.orthogonal_comparison(volume, bead, channel, voxel, settings)
    assert image.startswith(b"\x89PNG")
    assert average is not None
    assert average.count >= 3
    assert not average.truncated
    assert average.label == f"{average.count} beads"
    # Without enough beads to average, the average row is left out.
    alone = replace(channel, beads=(bead,))
    image, average = figures.orthogonal_comparison(volume, bead, alone, voxel, settings)
    assert image.startswith(b"\x89PNG")
    assert average is None


@pytest.mark.req("REP-01")
def test_bead_sample_is_random_but_reproducible(tracked: tuple[Path, Path, list[int]]) -> None:
    from dataclasses import replace

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    channel = result.channels[0]
    accepted = sorted(b.bead_id for b in channel.accepted)
    assert sample_beads(result, channel, 3) == sample_beads(result, channel, 3)
    chosen = [b.bead_id for b in sample_beads(result, channel, 3)]
    assert len(chosen) == 3
    assert set(chosen) <= set(accepted)
    assert chosen == sorted(chosen)
    # A different file (checksum) gives a different draw, for at least one of a few sizes.
    other = replace(result, provenance=replace(result.provenance, sha256="0" * 64))
    assert any(
        sample_beads(result, channel, k) != sample_beads(other, channel, k)
        for k in range(1, len(accepted))
    )
    # Fewer accepted beads than the sample size: all of them, in order.
    assert [b.bead_id for b in sample_beads(result, channel, len(accepted) + 5)] == accepted


@pytest.mark.req("REP-03")
def test_pdf_headings_stay_with_what_follows() -> None:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table

    from psfwidget.report.render_pdf import keep_headings_with_content

    def para(text: str, style: str) -> Paragraph:
        return Paragraph(text, ParagraphStyle(style))

    table = Table([["a", "b"]])
    story = [
        para("intro", "body"),
        para("Channel", "h2"),
        para("Sample", "h3"),  # consecutive headings travel together
        KeepTogether([table]),
        para("Appendix", "h2"),
        para("caption", "caption"),
        Table([["x"]] * 40),  # a long table: caption and table go with the heading
        Spacer(1, 6),
        para("Last", "h3"),
        PageBreak(),  # a heading never swallows a page break
    ]
    grouped = keep_headings_with_content(story)
    kinds = [type(f).__name__ for f in grouped]
    assert kinds == [
        "Paragraph",
        "KeepTogether",
        "KeepTogether",
        "Spacer",
        "KeepTogether",
        "PageBreak",
    ]
    sizes = [len(getattr(f, "_content", ())) for f in grouped]
    assert sizes == [0, 3, 3, 0, 1, 0]


@pytest.mark.req("REP-01", "DET-07", "QA-01")
def test_appendix_lists_reviewer_exclusions_but_not_automatic_rejections(
    tracked: tuple[Path, Path, list[int]], tmp_path: Path
) -> None:
    from dataclasses import replace

    from psfwidget.review import Decision, review

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    channel = result.channels[0]
    # One bead fails an automatic check (the field's beads are all clean otherwise).
    dim = channel.accepted[-1]
    beads = tuple(b.reject("SNR 2.1 below 5") if b is dim else b for b in channel.beads)
    result = replace(result, channels=(replace(channel, beads=beads), *result.channels[1:]))
    target = channel.accepted[0].bead_id
    reviewed = review(result, {0: {target: Decision(False, "dust on the bead", "ab")}})
    report, _ = build_session_report(reviewed, stack=stack)
    table = next(
        b for b in report.blocks if isinstance(b, doc.Table) and b.header[:2] == ("Bead", "Status")
    )
    rows = {int(row[0]): row for row in table.rows}
    assert target in rows  # the reviewer's exclusion stays in the record
    assert rows[target][1] == "rejected"
    assert "excluded by reviewer ab: dust on the bead" in rows[target][-1]
    assert list(rows)[-1] == target  # after the accepted beads
    automatic = [b for b in reviewed.channels[0].beads if not b.accepted and b.bead_id != target]
    assert automatic  # the test field has beads the automatic checks reject
    for bead in automatic:
        assert bead.bead_id not in rows
    assert f"Not listed: {len(automatic)} beads rejected" in (table.caption or "")
    assert "low SNR 1" in (table.caption or "")  # counted by reason
    # The bead export keeps every bead, with its reasons.
    import csv

    from psfwidget.automation.analyze import write_outputs

    exported = next(p for p in write_outputs(reviewed, tmp_path) if p.name.endswith("_beads.csv"))
    with exported.open(encoding="utf-8") as handle:
        statuses = {
            int(r["bead_id"]): r["status"] for r in csv.DictReader(handle) if r["channel"] == "0"
        }
    assert len(statuses) == len(reviewed.channels[0].beads)
    assert statuses[dim.bead_id] == "rejected"
    assert "1 excluded by a reviewer" in (table.caption or "")


@pytest.mark.req("REP-01")
def test_appendix_of_a_channel_without_accepted_beads_has_no_empty_table(
    tracked: tuple[Path, Path, list[int]],
) -> None:
    from dataclasses import replace

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    channel = result.channels[0]
    beads = tuple(b.reject("SNR 2.1 below 5") for b in channel.beads)
    empty = replace(result, channels=(replace(channel, beads=beads),))
    report, _ = build_session_report(empty)
    assert not any(
        isinstance(b, doc.Table) and b.header[:2] == ("Bead", "Status") for b in report.blocks
    )
    texts = [b.text for b in report.blocks if isinstance(b, doc.Paragraph)]
    assert any(t.startswith("No accepted beads. Not listed: ") for t in texts)


@pytest.mark.req("THE-07", "MET-14", "REP-01")
def test_figure_captions_name_only_the_curves_drawn(
    tracked: tuple[Path, Path, list[int]],
) -> None:
    """With too few beads to average (as on a short LSM 980 stack), no caption claims one."""
    from dataclasses import replace

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    channel = result.channels[0]
    keep = {b.bead_id for b in channel.accepted[:2]}  # an average needs at least 3
    beads = tuple(
        b if b.bead_id in keep or not b.accepted else b.reject("SNR 2.1 below 5")
        for b in channel.beads
    )
    few = replace(result, channels=(replace(channel, beads=beads),))
    report, _ = build_session_report(few, stack=stack)
    captions = [b.caption for b in report.blocks if isinstance(b, doc.Figure)]
    profile_caption = next(c for c in captions if c.startswith("Profiles along x, y, and z"))
    panel_caption = next(c for c in captions if c.startswith("XY, XZ, and YZ slices"))
    for caption in (profile_caption, panel_caption):
        assert "average PSF of" not in caption
    assert "No average PSF: too few beads could be averaged" in profile_caption
    assert "FWHM in the titles: bead uncorrected" in profile_caption
    assert "the model (theoretical PSF with the bead size, dashed)" in profile_caption

    # With enough beads the average is named, with its bead count.
    report, _ = build_session_report(result, stack=stack)
    captions = [b.caption for b in report.blocks if isinstance(b, doc.Figure)]
    profile_caption = next(c for c in captions if c.startswith("Profiles along x, y, and z"))
    assert re.search(r"and the average PSF of the \d+ accepted beads", profile_caption)
    assert "No average PSF" not in profile_caption


@pytest.mark.req("THE-07", "MET-14")
def test_short_stack_gets_a_truncated_average_marked_as_undersampled(
    tracked: tuple[Path, Path, list[int]],
) -> None:
    """A stack too short for the full box (like the 13-plane LSM 980 stack) still gets an
    average for the figures, over fewer z-planes, labelled as an undersampled volume."""
    from dataclasses import replace

    from psfwidget.analysis import averaged_psf, truncated_averaged_psf
    from psfwidget.average import AverageError
    from psfwidget.report import figures

    database, profile_path, _ = tracked
    profile = load_profile(profile_path)
    stack = read_stack(database.parent / "day6.tif", profile=profile)
    result = analyze_stack(stack, AnalysisSettings(), profile.spec)
    channel = result.channels[0]
    volume, voxel = stack.channel(channel.index), stack.voxel_size_um
    # A box twice as tall as the stack: the full average cannot be made.
    tall = replace(AnalysisSettings(), crop_axial_fwhm=2 * volume.shape[0])
    with pytest.raises(AverageError, match="too close to the stack's edges"):
        averaged_psf(volume, channel.beads, voxel, channel.expected_fwhm_um, tall)
    average, used, half = truncated_averaged_psf(
        volume, channel.beads, voxel, channel.expected_fwhm_um, tall
    )
    assert used >= 3
    assert average.shape[0] == 2 * half[0] + 1 < volume.shape[0]

    bead = figures.representative_bead(channel)
    assert bead is not None
    image, shown = figures.orthogonal_comparison(volume, bead, channel, voxel, tall)
    assert image.startswith(b"\x89PNG")
    assert shown is not None
    assert shown.truncated
    assert shown.planes == average.shape[0]
    assert "undersampled volume: z truncated to" in shown.label

    # The report says so in both figure captions, and the metrics are not taken from it.
    report, _ = build_session_report(replace(result, settings=tall), stack=stack)
    captions = [b.caption for b in report.blocks if isinstance(b, doc.Figure)]
    for start in ("XY, XZ, and YZ slices", "Profiles along x, y, and z"):
        caption = next(c for c in captions if c.startswith(start))
        assert "Undersampled volume: the stack is too short" in caption
        assert "Strehl ratio and similarity are not computed from it" in caption
