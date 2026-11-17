"""Tests for exposure settings, normalised intensity, and settings-change notes (MET-04)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.analysis import analyze_stack, effective_exposure_ms
from psfwidget.io import parse_assignments, read_stack
from psfwidget.io._nikon_text import parse_nikon_text
from psfwidget.io.model import ChannelMetadata, Sourced
from psfwidget.profiles import load_profile
from psfwidget.report import document as doc
from psfwidget.report.status import channel_status, session_checks
from psfwidget.tracking.store import ResultsDatabase

GoldenFile = Callable[[str], Path]
OPTICS = [
    "numerical_aperture=1.4",
    "immersion_ri=1.518",
    "modality=widefield",
    "channel.0.emission_nm=520",
    "instrument_id=scope",
]


@pytest.mark.req("MET-04")
def test_effective_exposure() -> None:
    camera = ChannelMetadata(0, exposure_ms=Sourced(100.0), dwell_us=Sourced(2.0))
    assert effective_exposure_ms(camera) == 100.0
    scanner = ChannelMetadata(0, dwell_us=Sourced(2.0), accumulation=Sourced(4.0))
    assert effective_exposure_ms(scanner) == pytest.approx(0.008)
    assert effective_exposure_ms(ChannelMetadata(0, dwell_us=Sourced(2.0))) == 0.002
    assert effective_exposure_ms(ChannelMetadata(0)) is None


@pytest.mark.req("MET-04", "IMP-04")
def test_nikon_text_exposure_and_dwell() -> None:
    assert parse_nikon_text("Exposure: 1.5 s").exposure_ms == 1500.0
    assert parse_nikon_text("Exposure: 100 ms\nExposure: 100 ms").exposure_ms == 100.0
    assert parse_nikon_text("Exposure: 100 ms\nExposure: 50 ms").exposure_ms is None
    assert parse_nikon_text("Dwell Time: 0.4 usec").dwell_us == pytest.approx(0.4)


@pytest.mark.req("MET-04", "IMP-04")
@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("20261111_QC_980.czi", "dwell_us", 2.7552),
        ("20261106_QC_SP8.lif", "dwell_us", 19.525),
        ("20261012_QC_AxR_NSPARC_crop.nd2", "dwell_us", 0.4),
        ("20261111_psfbeads_SPD_1x_SR_crop.nd2", "exposure_ms", 100.0),
        ("20261111_psfbeads_SPD_1x_SR_crop.ome.tif", "exposure_ms", 100.0),
    ],
)
def test_settings_read_from_facility_files(
    name: str, field: str, value: float, golden_file: GoldenFile
) -> None:
    stack = read_stack(golden_file(name), compute_hash=False)
    for channel in stack.metadata.channels:
        item = getattr(channel, field)
        assert item is not None
        assert item.value == pytest.approx(value, rel=1e-3)
        assert item.source == "file"


def _field(path: Path, photons: float) -> Path:
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=16,
    )
    field = synthetic.make_bead_field(
        spec,
        5,
        (0.6, 0.22, 0.22),
        photons,
        np.random.default_rng(4),
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


@pytest.mark.req("MET-04")
def test_intensity_normalised_to_exposure(tmp_path: Path) -> None:
    short = read_stack(
        _field(tmp_path / "a.tif", 3e4),
        overrides=parse_assignments([*OPTICS, "channel.0.exposure_ms=50"]),
    )
    long = read_stack(
        _field(tmp_path / "b.tif", 6e4),
        overrides=parse_assignments([*OPTICS, "channel.0.exposure_ms=100"]),
    )
    a = analyze_stack(short).channels[0]
    b = analyze_stack(long).channels[0]
    assert b.summary["integrated_intensity"].median == pytest.approx(
        2 * a.summary["integrated_intensity"].median, rel=0.05
    )
    assert b.channel_metrics["intensity_per_ms"] == pytest.approx(
        a.channel_metrics["intensity_per_ms"], rel=0.05
    )
    unknown = analyze_stack(read_stack(tmp_path / "a.tif", overrides=parse_assignments(OPTICS)))
    assert "intensity_per_ms" not in unknown.channels[0].channel_metrics


@pytest.mark.req("MET-04", "TRK-05", "REP-07")
def test_settings_change_is_reported(tmp_path: Path) -> None:
    profile_path = tmp_path / "scope.toml"
    profile_path.write_text(
        '[instrument]\nid = "scope"\nmodality = "widefield"\n[[objective]]\nid = "63x"\n'
        'numerical_aperture = 1.4\nimmersion_ri = 1.518\n[[channel]]\nid = "green"\n'
        "match = { index = 0 }\nemission_nm = 520\n",
        encoding="utf-8",
    )
    profile = load_profile(profile_path)
    database = tmp_path / "qc.sqlite"
    results = []
    with ResultsDatabase(database) as db:
        for day, (exposure, gain) in enumerate([(100, 10), (100, 10), (50, 12)]):
            path = _field(tmp_path / f"d{day}.tif", 3e4)
            stack = read_stack(
                path,
                profile=profile,
                overrides=parse_assignments(
                    [
                        f"channel.0.exposure_ms={exposure}",
                        f"channel.0.detector_gain={gain}",
                        f"acquired_utc=2026-11-0{day + 1}T09:00:00+00:00",
                    ]
                ),
            )
            result = analyze_stack(stack, spec_limits=profile.spec)
            result = replace(result, provenance=replace(result.provenance, sha256=str(day) * 64))
            results.append((db.record(result), result))
        unchanged = session_checks(db, results[1][1], results[1][0], profile.tracking)[0]
        changed = session_checks(db, results[2][1], results[2][0], profile.tracking)[0]
    assert not [c for c in unchanged if c.severity == "changed"]
    note = next(c for c in changed if c.severity == "changed")
    assert "exposure 100 → 50 ms" in note.limits
    assert "detector gain 10 → 12" in note.limits
    status = channel_status(results[2][1].channels[0], changed)
    assert status.status is doc.Status.WARNING
    assert any("acquisition settings changed" in r for r in status.reasons)
