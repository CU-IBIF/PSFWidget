"""Write analysis results to CSV and JSON, and print a text summary (AUT-01, REP-04 in part).

For each analysed series, four files are written next to each other::

    <name>_beads.csv      one row per bead and channel, accepted or not, with reasons
    <name>_summary.csv    statistics of each metric over accepted beads, per channel
    <name>_channels.csv   per channel: counts, theory, sampling, and spec status
    <name>_run.json       everything above plus settings, metadata, and provenance
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.metrics.bead import BEAD_FIELDS
from psfwidget.metrics.field import CHANNEL_METRICS
from psfwidget.metrics.summary import SUMMARY_METRICS
from psfwidget.serialize import metadata_record, plain

CHANNEL_FIELDS: tuple[str, ...] = (
    "channel",
    "name",
    "candidates",
    "accepted",
    "rejected",
    "rejections",
    "theory_model",
    "theory_lateral_um",
    "theory_axial_um",
    "analytic_regime",
    "analytic_lateral_um",
    "analytic_axial_um",
    "theory_unavailable",
    "excitation_nm",
    "emission_nm",
    "pinhole_au",
    "measurement_lateral_um",
    "measurement_axial_um",
    "spec_status",
    *CHANNEL_METRICS,
    "warnings",
)
SUMMARY_FIELDS: tuple[str, ...] = (
    "channel",
    "name",
    "metric",
    "n",
    "mean",
    "sd",
    "median",
    "q25",
    "q75",
)


def output_stem(result: FieldResult) -> str:
    """Return the base name for a result's files: the file stem plus the selection."""
    source = result.provenance
    stem = Path(source.path).stem
    if stem.endswith(".ome"):
        stem = stem[: -len(".ome")]
    parts = [stem]
    if source.series_index:
        parts.append(f"s{source.series_index}")
    if source.timepoint:
        parts.append(f"t{source.timepoint}")
    if source.tile:
        parts.append(f"m{source.tile}")
    return "_".join(parts)


def _number(value: float | None) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def channel_row(channel: ChannelResult) -> dict[str, Any]:
    """Return the per-channel table row."""
    theory = channel.theory
    closed_form = None if theory is None else theory.analytic
    return {
        "channel": channel.index,
        "name": channel.name,
        "candidates": len(channel.detection.candidates),
        "accepted": len(channel.accepted),
        "rejected": len(channel.beads) - len(channel.accepted),
        "rejections": "; ".join(f"{k}: {v}" for k, v in channel.rejections.items()),
        "theory_model": None if theory is None else theory.model,
        "theory_lateral_um": None if theory is None else theory.lateral_um,
        "theory_axial_um": None if theory is None else theory.axial_um,
        "analytic_regime": None if closed_form is None else closed_form.regime,
        "analytic_lateral_um": None if closed_form is None else closed_form.lateral_um,
        "analytic_axial_um": None if closed_form is None else closed_form.axial_um,
        "theory_unavailable": channel.theory_unavailable,
        "excitation_nm": None if theory is None else theory.excitation_nm,
        "emission_nm": None if theory is None else theory.emission_nm,
        "pinhole_au": None if theory is None else theory.pinhole_au,
        "measurement_lateral_um": channel.expected_fwhm_um[0],
        "measurement_axial_um": channel.expected_fwhm_um[1],
        "spec_status": None if channel.spec is None else channel.spec.status.value,
        **{metric: channel.channel_metrics.get(metric) for metric in CHANNEL_METRICS},
        "warnings": "; ".join(channel.warnings),
    }


def summary_rows(channel: ChannelResult) -> list[dict[str, Any]]:
    """Return one row per summarised metric."""
    rows = []
    for metric in SUMMARY_METRICS:
        stats = channel.summary.get(metric)
        if stats is None:
            continue
        row: dict[str, Any] = {"channel": channel.index, "name": channel.name, "metric": metric}
        row.update({k: _number(v) for k, v in asdict(stats).items()})
        rows.append(row)
    return rows


def run_record(result: FieldResult) -> dict[str, Any]:
    """Return a complete, JSON-ready record of an analysis."""
    channels = []
    for channel in result.channels:
        channels.append(
            channel_row(channel)
            | {
                "theory": None if channel.theory is None else asdict(channel.theory),
                "detection": {
                    "background": channel.detection.background,
                    "noise": channel.detection.noise,
                    "threshold": channel.detection.threshold,
                },
                "saturation_level": channel.saturation_level,
                "summary": {k: asdict(v) for k, v in channel.summary.items()},
                "spec": None if channel.spec is None else asdict(channel.spec),
                "warnings": list(channel.warnings),
                "beads": [b.to_row() for b in channel.beads],
            }
        )
    record = {
        "software": {"name": "psfwidget", "version": result.software_version},
        "analysed_utc": result.analysed_utc,
        "provenance": asdict(result.provenance),
        "metadata": metadata_record(result.metadata),
        "settings": result.settings.to_dict(),
        "spec_limits": asdict(result.spec_limits),
        "channels": channels,
    }
    return dict(plain(record))


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _number(v) if isinstance(v, float) else v for k, v in row.items()})


def write_outputs(
    result: FieldResult, directory: Path, *, overwrite: bool = False, stem: str | None = None
) -> list[Path]:
    """Write the four output files and return their paths.

    ``stem`` replaces the default file-name stem (the source file name and selection).

    Raises
    ------
    FileExistsError
        If a file exists and ``overwrite`` is false.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stem = stem or output_stem(result)
    paths = {
        "beads": directory / f"{stem}_beads.csv",
        "summary": directory / f"{stem}_summary.csv",
        "channels": directory / f"{stem}_channels.csv",
        "run": directory / f"{stem}_run.json",
    }
    existing = [p for p in paths.values() if p.exists()]
    if existing and not overwrite:
        names = ", ".join(p.name for p in existing)
        raise FileExistsError(f"output exists: {names}; use --overwrite to replace it")

    bead_rows = [
        {"channel": c.index, "channel_name": c.name} | b.to_row()
        for c in result.channels
        for b in c.beads
    ]
    _write_csv(paths["beads"], ("channel", "channel_name", *BEAD_FIELDS), bead_rows)
    _write_csv(
        paths["summary"], SUMMARY_FIELDS, [row for c in result.channels for row in summary_rows(c)]
    )
    _write_csv(paths["channels"], CHANNEL_FIELDS, [channel_row(c) for c in result.channels])
    paths["run"].write_text(
        json.dumps(run_record(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return list(paths.values())


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_text(result: FieldResult) -> str:
    """Return a short human-readable summary of an analysis."""
    source = result.provenance
    lines = [
        f"{Path(source.path).name}  series {source.series_index} ({source.series_name})",
    ]
    for channel in result.channels:
        theory = channel.theory
        accepted = len(channel.accepted)
        lines += [
            "",
            f"Channel {channel.index} ({channel.name or 'unnamed'}): {accepted} of "
            f"{len(channel.detection.candidates)} candidate beads accepted",
        ]
        if channel.rejections:
            lines.append(
                "  rejected: " + ", ".join(f"{k} {v}" for k, v in channel.rejections.items())
            )
        if theory is None:
            lines.append(f"  theory: not available ({channel.theory_unavailable})")
        else:
            lines.append(f"  theory: {theory.model}")
        lines.append(f"  {'':16}{'median':>10}{'IQR':>10}{'theory':>10}{'ratio':>8}")
        rows = (
            ("FWHM x (µm)", "fwhm_x_um", None if theory is None else theory.lateral_um, "ratio_x"),
            ("FWHM y (µm)", "fwhm_y_um", None if theory is None else theory.lateral_um, "ratio_y"),
            ("FWHM z (µm)", "fwhm_z_um", None if theory is None else theory.axial_um, "ratio_z"),
            ("ellipticity xy", "ellipticity_xy", None, None),
            ("SBR", "sbr", None, None),
            ("SNR", "snr", None, None),
        )
        for label, metric, reference, ratio in rows:
            stats = channel.summary.get(metric)
            ratio_stats = None if ratio is None else channel.summary.get(ratio)
            digits = 1 if metric in ("sbr", "snr") else 3
            lines.append(
                f"  {label:16}{_fmt(None if stats is None else stats.median, digits):>10}"
                f"{_fmt(None if stats is None else stats.iqr, digits):>10}"
                f"{_fmt(reference):>10}"
                f"{_fmt(None if ratio_stats is None else ratio_stats.median, 2):>8}"
            )
        if channel.spec is not None:
            lines.append(f"  spec: {channel.spec.status.value}")
            for check in channel.spec.checks:
                value = "-" if check.value is None else f"{check.value:.3g}"
                lines.append(f"    {check.status.value:<14}{check.name} {value} ({check.limit})")
        for warning in channel.warnings:
            lines.append(f"  note: {warning}")
    return "\n".join(lines)


def write_xlsx(result: FieldResult, path: Path) -> Path:
    """Write the analysis as an Excel workbook (REP-04).

    Sheets: Summary, Channels, Beads, and Metadata. Needs openpyxl
    (``pip install psfwidget[report]``).

    Raises
    ------
    RuntimeError
        If openpyxl is not installed.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("XLSX export needs openpyxl: pip install 'psfwidget[report]'") from exc

    def cell(value: Any) -> Any:
        value = plain(value)
        if isinstance(value, list | dict):
            return json.dumps(value, ensure_ascii=False)
        return value

    workbook = Workbook()
    sheets: list[tuple[str, tuple[str, ...], list[dict[str, Any]]]] = [
        ("Summary", SUMMARY_FIELDS, [r for c in result.channels for r in summary_rows(c)]),
        ("Channels", CHANNEL_FIELDS, [channel_row(c) for c in result.channels]),
        (
            "Beads",
            ("channel", "channel_name", *BEAD_FIELDS),
            [
                {"channel": c.index, "channel_name": c.name} | b.to_row()
                for c in result.channels
                for b in c.beads
            ],
        ),
    ]
    first = True
    for title, fields, rows in sheets:
        sheet = workbook.active if first else workbook.create_sheet()
        first = False
        assert sheet is not None
        sheet.title = title
        sheet.append(list(fields))
        for header in sheet[1]:
            header.font = Font(bold=True)
        for row in rows:
            sheet.append([cell(row.get(field)) for field in fields])
        sheet.freeze_panes = "A2"
    meta = workbook.create_sheet("Metadata")
    meta.append(["scope", "field", "value", "source", "note"])
    for header in meta[1]:
        header.font = Font(bold=True)
    record = metadata_record(result.metadata)
    for name, entry in record["values"].items():
        meta.append(["acquisition", name, *_entry_cells(entry, cell)])
    for channel in record["channels"]:
        for name, entry in channel.items():
            if name != "index":
                meta.append([f"channel {channel['index']}", name, *_entry_cells(entry, cell)])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _entry_cells(entry: dict[str, Any] | None, cell: Any) -> list[Any]:
    if entry is None:
        return [None, None, None]
    return [cell(entry["value"]), entry["source"], entry["note"] or None]
