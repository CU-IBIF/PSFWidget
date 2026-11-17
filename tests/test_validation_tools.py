"""Tests for the validation tools (VAL-01 method comparison, VAL-03 repeatability)."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile

import method_comparison
import repeatability
from psfwidget import synthetic
from psfwidget.analysis import analyze_stack
from psfwidget.automation import cli
from psfwidget.io import parse_assignments, read_stack
from psfwidget.io.metadata import apply_overrides
from psfwidget.tracking.store import ResultsDatabase

OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
    "instrument_id=scope",
]


def _stack(path: Path, seed: int) -> Path:
    spec = synthetic.FieldSpec(
        shape=(31, 128, 128),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        8,
        (0.6, 0.22, 0.22),
        5e4,
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
    return path


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize(("scale", "passes"), [(1.01, True), (1.12, False)])
def test_method_comparison(tmp_path: Path, scale: float, passes: bool) -> None:
    path = _stack(tmp_path / "f.tif", 1)
    out = tmp_path / "ours"
    arguments = ["analyze", str(path), "--out", str(out)]
    for option in OPTICS:
        arguments += ["--set", option]
    assert cli.main(arguments) == 0
    ours = method_comparison.read_psfwidget(out / "f_beads.csv", 0)
    assert len(ours) >= 6
    # A "reference tool" export: same beads, positions jittered, FWHM scaled, in nm.
    rng = np.random.default_rng(0)
    reference = tmp_path / "ref.csv"
    with reference.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["X", "Y", "FX", "FY", "FZ"])
        for bead in ours:
            noise = 1 + 0.01 * rng.normal(size=3)
            writer.writerow(
                [
                    (bead.x + 0.02) * 1000,
                    bead.y * 1000,
                    *(
                        bead.fwhm[m] * scale * n * 1000
                        for m, n in zip(method_comparison.METRICS, noise, strict=True)
                    ),
                ]
            )
    mapping = tmp_path / "map.toml"
    mapping.write_text(
        '[mine]\nx = "X"\ny = "Y"\nfwhm_x = "FX"\nfwhm_y = "FY"\nfwhm_z = "FZ"\nunit = "nm"\n',
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "field,psfwidget,reference,tool,channel\nf,ours/f_beads.csv,ref.csv,mine,0\n",
        encoding="utf-8",
    )
    status = method_comparison.main(
        [str(manifest), "--out", str(tmp_path / "val01"), "--mapping", str(mapping)]
    )
    assert status == (0 if passes else 1)
    summary = (tmp_path / "val01" / "summary.md").read_text("utf-8")
    assert summary.count("PASS" if passes else "FAIL") >= 1
    assert (tmp_path / "val01" / "pairs.csv").read_text("utf-8").count("\n") == len(ours) + 1
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Fix the column mapping"):
        method_comparison.read_reference(bad, method_comparison.DEFAULT_MAPPINGS["psfj"])


@pytest.mark.req("TRK-04")
def test_repeatability_summary(tmp_path: Path) -> None:
    stack = read_stack(_stack(tmp_path / "f.tif", 2), overrides=parse_assignments(OPTICS))
    base = analyze_stack(stack)
    start = datetime(2026, 11, 20, 9, tzinfo=UTC)
    with ResultsDatabase(tmp_path / "qc.sqlite") as db:
        for day in range(6):
            when = parse_assignments([f"acquired_utc={(start + timedelta(days=day)).isoformat()}"])
            result = replace(
                base,
                metadata=apply_overrides(base.metadata, when, "user"),
                provenance=replace(base.provenance, sha256=f"{day:064d}"),
            )
            db.record(result, user="ann" if day % 2 else "ben")
        rows = repeatability.summarise(db, None, None, None)
    fwhm_x = [r for r in rows if r["metric"] == "fwhm_x_um"]
    assert {r["group"] for r in fwhm_x} == {"all", "operator ann", "operator ben"}
    overall = next(r for r in fwhm_x if r["group"] == "all")
    assert overall["n"] == 6
    assert overall["passed"] is True  # identical results: CV 0
    assert (
        repeatability.main(["--db", str(tmp_path / "qc.sqlite"), "--out", str(tmp_path / "val03")])
        == 0
    )
    assert (tmp_path / "val03" / "repeatability.md").exists()
