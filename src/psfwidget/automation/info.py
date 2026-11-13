"""The ``psfwidget info`` command: show a file's series and resolved metadata."""

from __future__ import annotations

import json
from typing import Any

from psfwidget.io.model import ImageStack, SeriesInfo
from psfwidget.serialize import metadata_record as describe_metadata
from psfwidget.serialize import plain


def describe(stack: ImageStack, series: list[SeriesInfo]) -> dict[str, Any]:
    """Return a JSON-ready description of a stack and its file."""
    source = stack.provenance
    described = describe_metadata(stack.metadata)
    problems = stack.problems()
    return {
        "file": {
            "name": source.path.name,
            "format": source.format,
            "size_bytes": source.size_bytes,
            "sha256": source.sha256,
            "reader": source.reader,
        },
        "series": [
            {"index": s.index, "name": s.name, "sizes": s.sizes, "dtype": s.dtype} for s in series
        ],
        "selection": {
            "series": source.series_index,
            "series_name": source.series_name,
            "timepoint": source.timepoint,
            "tile": source.tile,
        },
        "shape": {
            "C": stack.shape[0],
            "Z": stack.shape[1],
            "Y": stack.shape[2],
            "X": stack.shape[3],
        },
        "dtype": str(stack.dtype),
        "metadata": described["values"],
        "channels": described["channels"],
        "warnings": described["warnings"],
        "problems": problems,
        "analyzable": not problems,
    }


def _text(value: object) -> str:
    converted = plain(value)
    if isinstance(converted, float):
        return f"{converted:.6g}"
    if isinstance(converted, list):
        return " - ".join(_text(item) for item in converted)
    return str(converted)


def _rows(entries: dict[str, dict[str, Any] | None], indent: str) -> list[str]:
    lines = []
    for name, entry in entries.items():
        if entry is None:
            lines.append(f"{indent}{name:<24}{'-':<34}")
            continue
        note = f"  ({entry['note']})" if entry["note"] else ""
        lines.append(f"{indent}{name:<24}{_text(entry['value']):<34}{entry['source']}{note}")
    return lines


def format_text(description: dict[str, Any]) -> str:
    """Render :func:`describe` output as plain text."""
    file, selection = description["file"], description["selection"]
    megabytes = file["size_bytes"] / 1e6
    lines = [
        f"File      {file['name']}  ({file['format']}, {megabytes:.1f} MB)",
        f"SHA-256   {file['sha256'] or 'not computed'}",
        f"Reader    {file['reader']}",
        f"Series    {len(description['series'])}",
    ]
    for item in description["series"]:
        sizes = " ".join(f"{axis}{size}" for axis, size in item["sizes"].items())
        marker = "*" if item["index"] == selection["series"] else " "
        lines.append(f"  {marker} [{item['index']}] {item['name']}  {sizes}  {item['dtype']}")
    shape = " ".join(f"{axis}{size}" for axis, size in description["shape"].items())
    lines += [
        f"Selected  series {selection['series']}, timepoint {selection['timepoint']}, "
        f"tile {selection['tile']}:  {shape}  {description['dtype']}",
        "",
        f"  {'Field':<24}{'Value':<34}Source",
    ]
    lines += _rows(description["metadata"], "  ")
    for channel in description["channels"]:
        lines.append(f"\n  Channel {channel['index']}")
        entries = {k: v for k, v in channel.items() if k != "index"}
        lines += _rows(entries, "    ")
    if description["warnings"]:
        lines.append("\nNotes")
        lines += [f"  - {warning}" for warning in description["warnings"]]
    if description["analyzable"]:
        lines.append("\nStatus: ready for analysis")
    else:
        lines.append("\nStatus: NOT ready for analysis")
        lines += [f"  - {problem}" for problem in description["problems"]]
    return "\n".join(lines)


def format_json(description: dict[str, Any]) -> str:
    """Render :func:`describe` output as JSON."""
    return json.dumps(description, indent=2, ensure_ascii=False)
