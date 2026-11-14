"""The session report: one acquisition, all channels (REP-01, REP-06, REP-07, THE-07)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.io.model import ACQUISITION_FIELDS, CHANNEL_FIELDS, ImageStack, Sourced
from psfwidget.report import document as doc
from psfwidget.report.status import ChannelStatus, MetricCheck, field_status, overall
from psfwidget.serialize import plain
from psfwidget.tracking.chart import metric_label

SUMMARY_ROWS: tuple[tuple[str, str | None, str | None], ...] = (
    ("fwhm_x_um", "lateral", "ratio_x"),
    ("fwhm_y_um", "lateral", "ratio_y"),
    ("fwhm_z_um", "axial", "ratio_z"),
    ("xy_major_um", None, None),
    ("xy_minor_um", None, None),
    ("xz_major_um", None, None),
    ("xz_minor_um", None, None),
    ("yz_major_um", None, None),
    ("yz_minor_um", None, None),
    ("ellipticity_xy", None, None),
    ("integrated_intensity", None, None),
    ("background", None, None),
    ("sbr", None, None),
    ("snr", None, None),
)


def fmt(value: object, digits: int = 4) -> str:
    """Format a value for a report table."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return f"{value:.{digits}g}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    if isinstance(value, tuple | list):
        return " - ".join(fmt(v, digits) for v in value)
    return str(plain(value))


def _sourced(item: Sourced[object] | None) -> tuple[str, str]:
    if item is None:
        return "-", ""
    note = f" ({item.note})" if item.note else ""
    return fmt(item.value), f"{item.source}{note}"


def _summary_table(channel: ChannelResult) -> doc.Table:
    rows = []
    theory = channel.theory
    for metric, which, ratio in SUMMARY_ROWS:
        stats = channel.summary.get(metric)
        if stats is None:
            continue
        reference = None
        if theory is not None and which is not None:
            reference = theory.lateral_um if which == "lateral" else theory.axial_um
        ratio_stats = channel.summary.get(ratio) if ratio else None
        rows.append(
            (
                metric_label(metric),
                fmt(stats.median),
                fmt(stats.iqr),
                str(stats.n),
                fmt(reference),
                fmt(None if ratio_stats is None else ratio_stats.median, 3),
            )
        )
    return doc.Table(
        ("Metric", "Median", "IQR", "n", "Theory", "Ratio"),
        tuple(rows),
        frozenset({1, 2, 3, 4, 5}),
        caption="Over accepted beads; FWHM corrected for bead size where known.",
    )


def _theory_text(channel: ChannelResult) -> str:
    theory = channel.theory
    if theory is None:
        return f"No theoretical PSF: {channel.theory_unavailable}."
    text = (
        f"Theoretical PSF: {theory.model}, NA {theory.numerical_aperture:g}, "
        f"n {theory.refractive_index:g}"
        + ("" if theory.emission_nm is None else f", emission {theory.emission_nm:g} nm")
    )
    if theory.excitation_nm is not None:
        text += f", excitation {theory.excitation_nm:g} nm"
    text += f": lateral {theory.lateral_um * 1000:.0f} nm, axial {theory.axial_um * 1000:.0f} nm."
    closed = theory.analytic
    if closed is not None:
        text += (
            f" Published formula ({closed.regime}): lateral "
            f"{closed.lateral_um * 1000:.0f} nm ({closed.lateral_formula}), axial "
            f"{closed.axial_um * 1000:.0f} nm ({closed.axial_formula})."
        )
    return text


def _tracking_table(checks: Sequence[MetricCheck]) -> doc.Table:
    rows = []
    for check in checks:
        status = {
            "rejection": doc.Status.FAIL,
            "warning": doc.Status.WARNING,
            "ok": doc.Status.PASS,
        }.get(check.severity, doc.Status.NOT_EVALUATED)
        rows.append(
            (
                metric_label(check.metric),
                fmt(check.value),
                "-" if check.z is None else f"{check.z:+.2f}",
                status.value,
                ", ".join(check.rules) or "-",
                check.limits,
            )
        )
    return doc.Table(
        ("Metric", "Value", "z", "Status", "Rules broken", "Control limits"),
        tuple(rows),
        frozenset({1, 2}),
        status_column=3,
        caption="This session against the instrument's control limits (Westgard).",
    )


def _spec_table(channel: ChannelResult) -> doc.Table | None:
    if channel.spec is None:
        return None
    rows = tuple(
        (
            c.name,
            fmt(c.value, 3),
            c.limit,
            c.status.value.replace("NOT EVALUATED", doc.Status.NOT_EVALUATED.value),
        )
        for c in channel.spec.checks
    )
    return doc.Table(
        ("Check", "Value", "Limit", "Status"),
        rows,
        frozenset({1}),
        status_column=3,
        caption="Specification limits from the instrument profile.",
    )


def _bead_table(channel: ChannelResult) -> doc.Table:
    rows = []
    for bead in channel.beads:
        rows.append(
            (
                str(bead.bead_id),
                bead.status,
                f"{bead.z_px}, {bead.y_px}, {bead.x_px}",
                fmt(bead.fwhm_x_um, 3),
                fmt(bead.fwhm_y_um, 3),
                fmt(bead.fwhm_z_um, 3),
                fmt(bead.ratio_x, 3),
                fmt(bead.ratio_z, 3),
                fmt(bead.snr, 3),
                "; ".join(bead.reasons + bead.notes) or "-",
            )
        )
    return doc.Table(
        (
            "Bead",
            "Status",
            "z, y, x (px)",
            "FWHM x",
            "FWHM y",
            "FWHM z",
            "Ratio x",
            "Ratio z",
            "SNR",
            "Reasons and notes",
        ),
        tuple(rows),
        frozenset({3, 4, 5, 6, 7, 8}),
        caption="Every candidate bead (FWHM in µm).",
    )


def _figures(stack: ImageStack, channel: ChannelResult, result: FieldResult) -> list[doc.Block]:
    from psfwidget.report import figures

    volume = stack.channel(channel.index)
    voxel = stack.voxel_size_um
    blocks: list[doc.Block] = [
        doc.Figure(
            figures.bead_overview(volume, channel.beads, voxel),
            "Maximum projection along z, with accepted beads numbered.",
            0.8,
        )
    ]
    bead = figures.representative_bead(channel)
    if bead is not None:
        blocks.append(
            doc.Figure(
                figures.orthogonal_views(volume, bead, channel, voxel, result.settings),
                f"Bead {bead.bead_id}, closest to the median lateral FWHM: slices through its "
                "centre at true aspect ratio.",
            )
        )
        blocks.append(
            doc.Figure(
                figures.profile_fits(volume, bead, channel, voxel, result.settings),
                f"Bead {bead.bead_id}: measured profiles, Gaussian fits, and a Gaussian of the "
                "theoretical width for comparison (THE-07).",
            )
        )
    return blocks


def _provenance(result: FieldResult, operator: str | None) -> doc.KeyValues:
    source = result.provenance
    settings = ", ".join(f"{k}={v}" for k, v in result.settings.to_dict().items())
    limits = ", ".join(f"{k}={v}" for k, v in asdict(result.spec_limits).items() if v is not None)
    return doc.KeyValues(
        (
            ("Software", f"PSFWidget {result.software_version}"),
            ("Analysed", result.analysed_utc.strftime("%Y-%m-%d %H:%M:%S UTC")),
            ("Operator", operator or "-"),
            ("File", source.path.name),
            ("Location", str(source.path)),
            ("SHA-256", source.sha256 or "not computed"),
            ("Size", f"{source.size_bytes:,} bytes"),
            ("Reader", source.reader),
            (
                "Selection",
                f"series {source.series_index} ({source.series_name}), timepoint "
                f"{source.timepoint}, tile {source.tile}",
            ),
            ("Analysis settings", settings),
            ("Spec limits", limits or "none"),
        )
    )


def _metadata_tables(result: FieldResult) -> list[doc.Block]:
    meta = result.metadata
    rows = [(name, *_sourced(getattr(meta, name))) for name in ACQUISITION_FIELDS]
    blocks: list[doc.Block] = [
        doc.Table(
            ("Field", "Value", "Source"),
            tuple(rows),
            caption="Acquisition metadata and where each value came from.",
        )
    ]
    for channel in meta.channels:
        channel_rows = [(name, *_sourced(getattr(channel, name))) for name in CHANNEL_FIELDS]
        blocks.append(
            doc.Table(
                ("Field", "Value", "Source"),
                tuple(channel_rows),
                caption=f"Channel {channel.index}",
            )
        )
    return blocks


def build_session_report(
    result: FieldResult,
    *,
    stack: ImageStack | None = None,
    checks: Mapping[int, Sequence[MetricCheck]] | None = None,
    session_id: int | None = None,
    operator: str | None = None,
    branding: doc.Branding | None = None,
) -> tuple[doc.Report, list[ChannelStatus]]:
    """Build the session report; return it with the per-channel statuses.

    ``stack`` adds the image figures; ``checks`` adds the comparison with the
    instrument's control limits.
    """
    meta = result.metadata
    statuses = field_status(result, checks)
    status = overall(statuses)
    instrument = (
        meta.instrument_id.value if meta.instrument_id is not None else "unknown instrument"
    )
    acquired = (
        meta.acquired_utc.value.strftime("%Y-%m-%d") if meta.acquired_utc else "unknown date"
    )
    subtitle = f"{instrument} · {result.provenance.path.name} · acquired {acquired}"
    if session_id is not None:
        subtitle += f" · session {session_id}"

    blocks: list[doc.Block] = [doc.Heading("Summary")]
    for channel_status in statuses:
        label = f"Channel {channel_status.index}" + (
            f" ({channel_status.name})" if channel_status.name else ""
        )
        blocks.append(
            doc.StatusLine(channel_status.status, f"{label}: {'; '.join(channel_status.reasons)}")
        )
    enhancement = meta.resolution_enhancement
    modality = _sourced(meta.modality)[0] + (f" with {enhancement.value}" if enhancement else "")
    blocks.append(
        doc.KeyValues(
            (
                ("Instrument", instrument),
                (
                    "Objective",
                    _sourced(meta.objective_name)[0]
                    + (f" (profile id {meta.objective_id.value})" if meta.objective_id else ""),
                ),
                ("Modality", modality),
                (
                    "NA / immersion index",
                    f"{_sourced(meta.numerical_aperture)[0]} / {_sourced(meta.immersion_ri)[0]}",
                ),
                (
                    "Voxel (z, y, x)",
                    " × ".join(_sourced(getattr(meta, f"voxel_{a}_um"))[0] for a in "zyx") + " µm",
                ),
                ("Bead diameter", _sourced(meta.bead_diameter_nm)[0] + " nm"),
                ("Acquired", _sourced(meta.acquired_utc)[0]),
            )
        )
    )

    for channel, channel_status in zip(result.channels, statuses, strict=True):
        blocks.append(doc.PageBreak())
        title = f"Channel {channel.index}" + (f" – {channel.name}" if channel.name else "")
        blocks.append(doc.Heading(title))
        blocks.append(doc.StatusLine(channel_status.status, "; ".join(channel_status.reasons)))
        counts = ", ".join(f"{k} {v}" for k, v in channel.rejections.items())
        blocks.append(
            doc.Paragraph(
                f"{len(channel.accepted)} of {len(channel.detection.candidates)} candidate beads "
                f"accepted" + (f"; rejected: {counts}." if counts else ".")
            )
        )
        blocks.append(_summary_table(channel))
        blocks.append(doc.Paragraph(_theory_text(channel)))
        channel_checks = (checks or {}).get(channel.index)
        if channel_checks:
            blocks.append(_tracking_table(channel_checks))
        spec = _spec_table(channel)
        if spec is not None:
            blocks.append(spec)
        for warning in channel.warnings:
            blocks.append(doc.Paragraph(f"Note: {warning}", muted=True))
        if stack is not None:
            blocks.extend(_figures(stack, channel, result))
        blocks.append(doc.Heading("Beads", 3))
        blocks.append(_bead_table(channel))

    blocks.append(doc.PageBreak())
    blocks.append(doc.Heading("Provenance"))
    blocks.append(_provenance(result, operator))
    blocks.append(doc.Heading("Metadata"))
    blocks.extend(_metadata_tables(result))
    report = doc.Report(
        title="PSF quality control: session report",
        subtitle=subtitle,
        status=status,
        generated_utc=datetime.now(UTC),
        blocks=tuple(blocks),
        branding=branding or doc.Branding(),
    )
    return report, statuses
