"""Method comparison of PSFWidget with MetroloJ_QC or PSFj (VAL-01, NFR-02).

Pairs PSFWidget's per-bead results with another tool's by bead position and
reports Bland–Altman statistics per FWHM metric, against the VAL-01
acceptance criteria (VV_PLAN §2.4): mean bias ≤ 5% and 95% limits of
agreement within ±10% (or within the inter-bead SD).

Input is a manifest CSV with one row per field::

    field,psfwidget,reference,tool,channel
    sp8_day1,results/sp8_day1_beads.csv,metroloj/sp8_day1.csv,metroloj,0

* ``psfwidget``: a ``<name>_beads.csv`` written by ``psfwidget analyze``.
* ``reference``: the other tool's per-bead table, exported as CSV.
* ``tool``: a key of the column mapping (``metroloj``, ``psfj``, or one added
  with ``--mapping``).
* ``channel``: the PSFWidget channel index to compare (default 0).

**Column mapping.** The other tools' column names depend on their version
and export settings, so they come from a mapping; the built-in ones below
are a starting point and **must be checked against an actual export** before
the study (record the check in the VAL-01 protocol). Supply your own with
``--mapping mapping.toml``::

    [metroloj]
    x = "X (µm)"            # bead centre, µm (or set unit = "px" and pixel = 0.065)
    y = "Y (µm)"
    fwhm_x = "FWHM X (µm)"
    fwhm_y = "FWHM Y (µm)"
    fwhm_z = "FWHM Z (µm)"
    unit = "um"            # unit of the FWHM and position columns: "um" or "nm"

Usage::

    python tools/method_comparison.py manifest.csv --out val01/ [--mapping m.toml]
        [--match-um 0.5]

Writes ``pairs.csv`` (every matched bead), ``summary.csv`` (per metric), and
``summary.md``; with matplotlib, ``bland_altman_<metric>.png``. Exit status 0
if every metric meets the criteria, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

METRICS = ("fwhm_x", "fwhm_y", "fwhm_z")
MAX_BIAS = 0.05
MAX_LIMIT = 0.10
DEFAULT_MAPPINGS: dict[str, dict[str, str]] = {
    # Starting points only: check against a real export (see the module docstring).
    "metroloj": {
        "x": "X (µm)",
        "y": "Y (µm)",
        "fwhm_x": "FWHM X (µm)",
        "fwhm_y": "FWHM Y (µm)",
        "fwhm_z": "FWHM Z (µm)",
        "unit": "um",
    },
    "psfj": {
        "x": "x [µm]",
        "y": "y [µm]",
        "fwhm_x": "FWHM x [µm]",
        "fwhm_y": "FWHM y [µm]",
        "fwhm_z": "FWHM z [µm]",
        "unit": "um",
    },
}
OURS = {
    "x": "centre_x_um",
    "y": "centre_y_um",
    "fwhm_x": "fwhm_x_um",
    "fwhm_y": "fwhm_y_um",
    "fwhm_z": "fwhm_z_um",
}


@dataclass(frozen=True)
class Bead:
    """One bead's position and FWHM in µm."""

    x: float
    y: float
    fwhm: dict[str, float]


@dataclass(frozen=True)
class Agreement:
    """Bland–Altman statistics of one metric, as fractions of the pair mean."""

    metric: str
    n: int
    bias: float
    sd: float
    inter_bead_sd: float
    """Relative SD of the reference values across beads (the alternative LoA criterion)."""

    @property
    def limits(self) -> tuple[float, float]:
        """95% limits of agreement."""
        return self.bias - 1.96 * self.sd, self.bias + 1.96 * self.sd

    @property
    def passed(self) -> bool:
        """VAL-01: |bias| ≤ 5% and limits within ±10% or within the inter-bead SD."""
        bound = max(MAX_LIMIT, 1.96 * self.inter_bead_sd)
        low, high = self.limits
        return abs(self.bias) <= MAX_BIAS and -bound <= low and high <= bound


def _number(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def read_psfwidget(path: Path, channel: int) -> list[Bead]:
    """Read the accepted beads of one channel from a PSFWidget ``_beads.csv``."""
    beads = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "accepted" or int(row.get("channel", 0)) != channel:
                continue
            values = {key: _number(row.get(column)) for key, column in OURS.items()}
            if any(values[k] is None for k in ("x", "y")):
                continue
            fwhm = {m: v for m in METRICS if (v := values[m]) is not None}
            beads.append(Bead(values["x"], values["y"], fwhm))  # type: ignore[arg-type]
    return beads


def read_reference(path: Path, mapping: dict[str, str]) -> list[Bead]:
    """Read another tool's per-bead table with a column mapping."""
    scale = {"um": 1.0, "nm": 1e-3}[mapping.get("unit", "um")]
    beads = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [
            c for k, c in mapping.items() if k in OURS and c not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(
                f"{path.name}: columns {missing} not found; columns are {reader.fieldnames}. "
                "Fix the column mapping (--mapping)."
            )
        for row in reader:
            x, y = _number(row.get(mapping["x"])), _number(row.get(mapping["y"]))
            if x is None or y is None:
                continue
            fwhm = {
                m: v * scale
                for m in METRICS
                if m in mapping and (v := _number(row.get(mapping[m]))) is not None
            }
            beads.append(Bead(x * scale, y * scale, fwhm))
    return beads


def pair(ours: list[Bead], theirs: list[Bead], radius_um: float) -> list[tuple[Bead, Bead]]:
    """Pair beads that are each other's nearest neighbour within ``radius_um``."""
    if not ours or not theirs:
        return []
    a = np.array([(b.x, b.y) for b in ours])
    b = np.array([(t.x, t.y) for t in theirs])
    distance = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    forward, backward = distance.argmin(axis=1), distance.argmin(axis=0)
    return [
        (ours[i], theirs[j])
        for i, j in enumerate(forward)
        if backward[j] == i and distance[i, j] <= radius_um
    ]


def agreement(pairs: list[tuple[Bead, Bead]], metric: str) -> Agreement | None:
    """Return Bland–Altman statistics of relative differences (ours − theirs) / mean."""
    values = [
        (p.fwhm[metric], q.fwhm[metric]) for p, q in pairs if metric in p.fwhm and metric in q.fwhm
    ]
    if len(values) < 3:
        return None
    ours, theirs = np.array(values).T
    relative = (ours - theirs) / ((ours + theirs) / 2.0)
    return Agreement(
        metric,
        len(values),
        float(relative.mean()),
        float(relative.std(ddof=1)),
        float(theirs.std(ddof=1) / theirs.mean()),
    )


def _plot(pairs: list[tuple[Bead, Bead]], result: Agreement, path: Path) -> None:
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError:
        return
    values = np.array(
        [
            (p.fwhm[result.metric], q.fwhm[result.metric])
            for p, q in pairs
            if result.metric in p.fwhm and result.metric in q.fwhm
        ]
    )
    mean = values.mean(axis=1) * 1000
    relative = (values[:, 0] - values[:, 1]) / values.mean(axis=1) * 100
    figure = Figure(figsize=(6, 4))
    FigureCanvasAgg(figure)
    axes = figure.add_subplot()
    axes.scatter(mean, relative, s=18, color="#2a78d6")
    axes.axhline(result.bias * 100, color="#52514e")
    for limit in result.limits:
        axes.axhline(limit * 100, color="#d03b3b", linestyle="--")
    axes.axhspan(-MAX_LIMIT * 100, MAX_LIMIT * 100, color="#f0efec", zorder=0)
    axes.set_xlabel(f"mean {result.metric} (nm)")
    axes.set_ylabel("difference, PSFWidget − reference (% of mean)")
    axes.set_title(f"Bland–Altman, {result.metric}: bias {result.bias:+.1%}, n {result.n}")
    figure.tight_layout()
    figure.savefig(path, dpi=150)


def main(argv: list[str] | None = None) -> int:
    """Run the comparison; return 0 if every metric meets the criteria."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, default=Path("method_comparison"))
    parser.add_argument("--mapping", type=Path, help="TOML column mapping per tool")
    parser.add_argument(
        "--match-um", type=float, default=0.5, help="maximum distance between paired beads (µm)"
    )
    args = parser.parse_args(argv)
    mappings = dict(DEFAULT_MAPPINGS)
    if args.mapping:
        mappings.update(tomllib.loads(args.mapping.read_text("utf-8")))
    base = args.manifest.parent
    all_pairs: list[tuple[str, Bead, Bead]] = []
    with args.manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ours = read_psfwidget(base / row["psfwidget"], int(row.get("channel") or 0))
            theirs = read_reference(base / row["reference"], mappings[row["tool"]])
            found = pair(ours, theirs, args.match_um)
            print(
                f"{row['field']}: {len(ours)} PSFWidget beads, {len(theirs)} reference, "
                f"{len(found)} paired"
            )
            all_pairs += [(row["field"], p, q) for p, q in found]
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "pairs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "field",
                "x_um",
                "y_um",
                *[f"{m}_{who}" for m in METRICS for who in ("psfwidget", "reference")],
            ]
        )
        for field, p, q in all_pairs:
            writer.writerow(
                [
                    field,
                    f"{p.x:.3f}",
                    f"{p.y:.3f}",
                    *[v for m in METRICS for v in (p.fwhm.get(m), q.fwhm.get(m))],
                ]
            )
    pairs = [(p, q) for _, p, q in all_pairs]
    results = [r for m in METRICS if (r := agreement(pairs, m)) is not None]
    lines = [
        "# VAL-01 method comparison",
        "",
        f"{len({f for f, _, _ in all_pairs})} fields, {len(pairs)} paired beads.",
        "",
        "| Metric | n | Bias | 95% limits of agreement | Inter-bead SD | Result |",
        "|---|---|---|---|---|---|",
    ]
    with (args.out / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "n", "bias", "loa_low", "loa_high", "inter_bead_sd", "passed"])
        for result in results:
            low, high = result.limits
            writer.writerow(
                [
                    result.metric,
                    result.n,
                    f"{result.bias:.4f}",
                    f"{low:.4f}",
                    f"{high:.4f}",
                    f"{result.inter_bead_sd:.4f}",
                    result.passed,
                ]
            )
            lines.append(
                f"| {result.metric} | {result.n} | {result.bias:+.1%} | "
                f"{low:+.1%} to {high:+.1%} | {result.inter_bead_sd:.1%} | "
                f"{'PASS' if result.passed else 'FAIL'} |"
            )
            _plot(pairs, result, args.out / f"bland_altman_{result.metric}.png")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if results and all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
