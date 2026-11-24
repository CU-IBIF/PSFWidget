"""The session report: one acquisition, all channels (REP-01, REP-06, REP-07, THE-07)."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.io.model import ACQUISITION_FIELDS, CHANNEL_FIELDS, ImageStack, Sourced
from psfwidget.metrics.bead import BeadResult
from psfwidget.metrics.summary import rejection_counts
from psfwidget.report import document as doc
from psfwidget.report.status import ChannelStatus, MetricCheck, field_status, overall
from psfwidget.review import MANUAL_EXCLUSION
from psfwidget.serialize import plain
from psfwidget.tracking.chart import metric_label

if TYPE_CHECKING:  # figures needs matplotlib, an optional extra
    from psfwidget.report.figures import AverageImage

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
            "changed": doc.Status.WARNING,
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


def _field_values(channel: ChannelResult) -> doc.KeyValues | None:
    """Tilt, curvature, uniformity, chromatic shift, and the averaged-PSF comparison."""
    rows: list[tuple[str, str]] = []
    field = channel.field
    if field is not None and field.tilt_deg is not None:
        assert field.focal_range_um is not None
        rows.append(
            (
                "Focus-plane tilt",
                f"{field.tilt_deg:.2f}° (focus range "
                f"{field.focal_range_um * 1000:.0f} nm across the field, "
                f"{field.n_beads} beads)",
            )
        )
    if field is not None and field.curvature_sag_um is not None:
        rows.append(
            (
                "Field curvature",
                f"{field.curvature_sag_um * 1000:+.0f} nm sag at the "
                "corner relative to the centre",
            )
        )
    if field is not None and field.uniformity_corner_ratio is not None:
        rows.append(
            (
                "Illumination uniformity",
                f"bead intensity at the corner {field.uniformity_corner_ratio:.2f} × centre",
            )
        )
    if field is not None:
        rows += [("Note", note) for note in field.notes]
    metrics = channel.channel_metrics
    if "ratio_lateral_ism" in metrics:
        rows.append(
            (
                "Ratio to image-scanning theory",
                f"lateral {metrics['ratio_lateral_ism']:.2f}, axial "
                f"{metrics.get('ratio_axial_ism', float('nan')):.2f} (THE-09)",
            )
        )
    psf = channel.psf
    if psf is not None:
        rows.append(
            (
                "Averaged PSF",
                f"{psf.n_beads} beads; FWHM z {psf.fwhm_um[0] * 1000:.0f} "
                f"nm, y {psf.fwhm_um[1] * 1000:.0f} nm, x {psf.fwhm_um[2] * 1000:.0f} nm",
            )
        )
        rows.append(
            (
                "Strehl ratio estimate",
                f"{psf.strehl:.2f} (peak of the averaged PSF / theoretical peak at equal energy)",
            )
        )
        rows.append(("Similarity to theory", f"{psf.similarity:.3f} (Pearson r)"))
        rows.append(
            (
                "Axial asymmetry",
                f"skewness {psf.axial_skewness:+.2f}, energy above / "
                f"below focus {psf.axial_asymmetry:.2f} (0 and 1 when symmetric)",
            )
        )
    shift = channel.chromatic
    if shift is not None:
        rows.append(
            (
                f"Shift from channel {shift.reference}",
                f"x {shift.dx_nm:+.0f} nm, y {shift.dy_nm:+.0f} nm, z {shift.dz_nm:+.0f} "
                f"nm (lateral {shift.lateral_nm:.0f} nm; {shift.n_pairs} matched beads)",
            )
        )
    return doc.KeyValues(tuple(rows)) if rows else None


def _field_map(channel: ChannelResult) -> doc.Figure | None:
    """Return the field-map figure (MET-11) when matplotlib and the beads allow it."""
    if channel.field is None:
        return None
    try:
        from psfwidget.report.figures import field_map
    except ImportError:  # pragma: no cover - reports without the figures extra
        return None
    png = field_map(channel.beads, channel.field.field_size_um)
    if png is None:
        return None
    return doc.Figure(
        png,
        "Accepted beads across the field of view. Left: focus position relative to the median "
        "bead (blue below, red above), showing tilt and curvature. Middle: lateral FWHM "
        "(mean of x and y). Right: axial FWHM.",
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


def excluded_by_reviewer(bead: BeadResult) -> bool:
    """Return whether a reviewer, not the automatic checks, rejected the bead."""
    return not bead.accepted and any(r.startswith(MANUAL_EXCLUSION) for r in bead.reasons)


def _bead_table(channel: ChannelResult) -> doc.Table:
    """Return the table of accepted beads, then reviewer exclusions, by bead id (REP-01).

    Beads rejected by the automatic checks are only counted by reason, to keep
    the report short; every bead is in the bead export (CSV or Excel).
    """
    accepted = sorted(channel.accepted, key=lambda b: b.bead_id)
    excluded = sorted(
        (b for b in channel.beads if excluded_by_reviewer(b)), key=lambda b: b.bead_id
    )
    automatic = [b for b in channel.beads if not b.accepted and not excluded_by_reviewer(b)]
    rows = []
    for bead in (*accepted, *excluded):
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
    caption = f"{len(accepted)} accepted beads" if accepted else "No accepted beads"
    if excluded:
        caption += f", then {len(excluded)} excluded by a reviewer"
    caption += " (FWHM in µm)." if accepted or excluded else "."
    if automatic:
        counts = ", ".join(f"{kind} {n}" for kind, n in rejection_counts(automatic).items())
        caption += (
            f" Not listed: {len(automatic)} beads rejected by the automatic checks ({counts}); "
            "every bead, with its reasons, is in the bead export (CSV or Excel)."
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
        caption=caption,
    )


def _truncation_note(average: AverageImage | None) -> str:
    """Say when the average comes from a stack too short for the full box."""
    if average is None or not average.truncated:
        return ""
    return (
        f" Undersampled volume: the stack is too short for the full {average.full_planes}-plane "
        f"box, so the average covers only {average.planes} z-planes (its axial width may be "
        "cut short, and the Strehl ratio and similarity are not computed from it)."
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
    diameter = (
        None
        if result.metadata.bead_diameter_nm is None
        else result.metadata.bead_diameter_nm.value / 1000.0
    )
    if bead is not None:
        image, average = figures.orthogonal_comparison(
            volume, bead, channel, voxel, result.settings, bead_diameter_um=diameter
        )
        rows = [f"bead {bead.bead_id}, closest to the median lateral FWHM"]
        if average is not None:
            rows.append(
                f"the average PSF of the {average.count} accepted beads across the field of view"
            )
        if channel.theory is not None:
            rows.append("the model (theoretical PSF with the bead size)")
        blocks.append(
            doc.Figure(
                image,
                "XY, XZ, and YZ slices through the centre of "
                + ", ".join(rows[:-1])
                + (" and " if len(rows) > 1 else "")
                + rows[-1]
                + " (one row each), all at one scale and true aspect ratio, with z running "
                "horizontally in XZ and YZ. Each image is "
                "scaled to its own peak (the bead after subtracting its background). Hatching "
                "marks any part of the window outside the stack." + _truncation_note(average),
            )
        )
        # Describe only the curves drawn: the average and the model are left out of the
        # figure when there are too few beads to average or no theory (as the panel above).
        curves = []
        if channel.theory is not None:
            curves.append("the model (theoretical PSF with the bead size, dashed)")
        curves.append(
            f"the closest single bead ({bead.bead_id}, nearest the median lateral FWHM; data "
            "and Gaussian fit)"
        )
        if average is not None:
            curves.append(
                f"the average PSF of the {average.count} accepted beads across the field of view"
            )
        listed = curves[0] if len(curves) == 1 else ", ".join(curves[:-1]) + ", and " + curves[-1]
        measured = "bead and average" if average is not None else "bead"
        caption = (
            f"Profiles along x, y, and z, each centred and scaled to a peak of 1: {listed} "
            f"(THE-07, MET-14). FWHM in the titles: {measured} uncorrected for the bead size."
        )
        if average is None:
            caption += " No average PSF: too few beads could be averaged (see the note above)."
        else:
            caption += _truncation_note(average)
        blocks.append(
            doc.Figure(
                figures.profile_fits(
                    volume, bead, channel, voxel, result.settings, bead_diameter_um=diameter
                ),
                caption,
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


SAMPLE_BEADS = 10
"""Accepted beads shown in each channel section; the rest are in the appendices."""


def _sample_seed(result: FieldResult, channel: ChannelResult) -> int:
    """Seed the bead sample from the file and channel, so a report is reproducible."""
    source = result.provenance
    key = f"{source.sha256 or source.path.name}|{source.series_index}|{channel.index}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


def sample_beads(
    result: FieldResult, channel: ChannelResult, count: int = SAMPLE_BEADS
) -> list[BeadResult]:
    """Return up to ``count`` accepted beads chosen at random, in bead order.

    The choice is seeded from the file's SHA-256 (or name), series, and
    channel, so the same data always gives the same sample.
    """
    accepted = sorted(channel.accepted, key=lambda b: b.bead_id)
    if len(accepted) <= count:
        return accepted
    rng = np.random.default_rng(_sample_seed(result, channel))
    chosen = sorted(int(i) for i in rng.choice(len(accepted), size=count, replace=False))
    return [accepted[i] for i in chosen]


def _sample_table(result: FieldResult, channel: ChannelResult, appendix: str) -> doc.Table:
    """Return a table of a random sample of the channel's accepted beads."""
    beads = sample_beads(result, channel)
    rows = tuple(
        (
            str(bead.bead_id),
            f"{bead.z_px}, {bead.y_px}, {bead.x_px}",
            fmt(bead.fwhm_x_um, 3),
            fmt(bead.fwhm_y_um, 3),
            fmt(bead.fwhm_z_um, 3),
            fmt(bead.ratio_x, 3),
            fmt(bead.ratio_z, 3),
            fmt(bead.snr, 3),
        )
        for bead in beads
    )
    total = len(channel.accepted)
    which = (
        f"{len(beads)} of {total} accepted beads, chosen at random (the same beads each time "
        "this file is reported)"
        if len(beads) < total
        else f"All {total} accepted beads"
    )
    return doc.Table(
        ("Bead", "z, y, x (px)", "FWHM x", "FWHM y", "FWHM z", "Ratio x", "Ratio z", "SNR"),
        rows,
        frozenset({2, 3, 4, 5, 6, 7}),
        caption=f"{which}; FWHM in µm. All accepted beads are in {appendix}.",
    )


def _appendix(number: int) -> str:
    return f"Appendix {chr(ord('A') + number)}"


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

    for number, (channel, channel_status) in enumerate(
        zip(result.channels, statuses, strict=True)
    ):
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
        geometry = _field_values(channel)
        if geometry is not None:
            blocks.append(doc.Heading("Field geometry and registration", 3))
            blocks.append(geometry)
            figure = _field_map(channel)
            if figure is not None:
                blocks.append(figure)
        spec = _spec_table(channel)
        if spec is not None:
            blocks.append(spec)
        for warning in channel.warnings:
            blocks.append(doc.Paragraph(f"Note: {warning}", muted=True))
        if stack is not None:
            blocks.extend(_figures(stack, channel, result))
        blocks.append(doc.Heading("Sample of accepted beads", 3))
        if channel.accepted:
            blocks.append(_sample_table(result, channel, _appendix(number)))
        else:
            blocks.append(
                doc.Paragraph(
                    f"No accepted beads; the rejections are summarised in {_appendix(number)}."
                )
            )

    blocks.append(doc.PageBreak())
    blocks.append(doc.Heading("Provenance"))
    blocks.append(_provenance(result, operator))
    blocks.append(doc.Heading("Metadata"))
    blocks.extend(_metadata_tables(result))
    for number, channel in enumerate(result.channels):
        blocks.append(doc.PageBreak())  # each appendix starts on its own page
        name = f"channel {channel.index}" + (f" – {channel.name}" if channel.name else "")
        blocks.append(doc.Heading(f"{_appendix(number)}: beads of {name}"))
        blocks.append(
            doc.Paragraph(
                "Accepted beads, then any beads a reviewer excluded, each group in bead "
                "order. Beads rejected by the automatic checks are counted below the table, "
                "not listed.",
                muted=True,
            )
        )
        table = _bead_table(channel)
        # A channel with nothing to list gets the counts alone, not an empty table.
        blocks.append(table if table.rows else doc.Paragraph(table.caption or ""))
    report = doc.Report(
        title="PSF quality control: session report",
        subtitle=subtitle,
        status=status,
        generated_utc=datetime.now(UTC),
        blocks=tuple(blocks),
        branding=branding or doc.Branding(),
    )
    return report, statuses
