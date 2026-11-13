"""Tests for the ``psfwidget analyze`` command (AUT-01, REP-04 in part)."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.automation import cli
from psfwidget.metrics.bead import BEAD_FIELDS

VOXEL = (0.15, 0.05, 0.05)
OPTICS = [
    "--set",
    "numerical_aperture=1.4",
    "--set",
    "immersion_ri=1.518",
    "--set",
    "modality=widefield",
    "--set",
    "channel.0.emission_nm=520",
]


@pytest.fixture(scope="module")
def beads_tif(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = synthetic.FieldSpec(
        shape=(31, 160, 160),
        voxel_size_um=VOXEL,
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(spec, 8, (0.6, 0.22, 0.22), 4e4, np.random.default_rng(1))
    path = tmp_path_factory.mktemp("in") / "beads.ome.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    return path


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.req("AUT-01")
def test_analyze_writes_four_files(
    beads_tif: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["analyze", str(beads_tif), *OPTICS, "--out", str(tmp_path), "--no-hash"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "Channel 0 (unnamed): 8 of 8 candidate beads accepted" in out
    assert "FWHM z (µm)" in out
    assert "theory: scalar widefield (emission PSF)" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "beads_beads.csv",
        "beads_channels.csv",
        "beads_run.json",
        "beads_summary.csv",
    ]

    beads = _read(tmp_path / "beads_beads.csv")
    assert len(beads) == 8
    assert list(beads[0]) == ["channel", "channel_name", *BEAD_FIELDS]
    assert {b["status"] for b in beads} == {"accepted"}
    assert 0.2 < float(beads[0]["fwhm_x_um"]) < 0.24

    summary = {row["metric"]: row for row in _read(tmp_path / "beads_summary.csv")}
    assert summary["fwhm_z_um"]["n"] == "8"
    assert float(summary["fwhm_z_um"]["median"]) == pytest.approx(0.6, rel=0.05)

    (channel,) = _read(tmp_path / "beads_channels.csv")
    assert channel["accepted"] == "8"
    assert channel["theory_model"] == "scalar widefield (emission PSF)"
    assert channel["analytic_regime"] == "widefield"

    run = json.loads((tmp_path / "beads_run.json").read_text("utf-8"))
    assert run["software"]["name"] == "psfwidget"
    assert run["provenance"]["sha256"] is None
    assert run["settings"]["profile_width_px"] == 3
    assert run["metadata"]["values"]["numerical_aperture"] == {
        "value": 1.4,
        "source": "user",
        "note": "",
    }
    assert len(run["channels"][0]["beads"]) == 8
    assert run["channels"][0]["theory"]["modality"] == "widefield"


def test_existing_output_is_protected(
    beads_tif: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["analyze", str(beads_tif), *OPTICS, "--out", str(tmp_path), "--no-hash"]
    assert cli.main(args) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(args) == cli.EXIT_ERROR
    assert "use --overwrite" in capsys.readouterr().err
    assert cli.main([*args, "--overwrite"]) == cli.EXIT_OK


@pytest.mark.req("IMP-06")
def test_stack_without_metadata_is_refused(
    beads_tif: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["analyze", str(beads_tif), "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == cli.EXIT_NOT_READY
    assert "is not ready for analysis" in err
    assert "  - missing numerical_aperture" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.req("THE-06")
def test_spec_failure_sets_the_exit_code_on_request(
    beads_tif: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "scope.toml"
    profile.write_text(
        '[instrument]\nid = "scope"\n[spec]\nlateral_ratio_max = 1.05\nmin_beads = 3\n',
        encoding="utf-8",
    )
    base = [
        "analyze",
        str(beads_tif),
        *OPTICS,
        "--profile",
        str(profile),
        "--no-hash",
        "--out",
        str(tmp_path / "out"),
    ]
    assert cli.main(base) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "spec: FAIL" in out
    assert "FAIL          median ratio_x" in out
    assert cli.main([*base, "--overwrite", "--fail-on-spec"]) == cli.EXIT_SPEC_FAIL


def test_settings_and_channel_options(
    beads_tif: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "analyze",
            str(beads_tif),
            *OPTICS,
            "--out",
            str(tmp_path),
            "--no-hash",
            "--setting",
            "min_snr=1000",
            "--setting",
            "outlier_mad=none",
            "--setting",
            "bead_size_correction=no",
            "--channel",
            "0",
        ]
    )
    assert code == cli.EXIT_OK
    run = json.loads((tmp_path / "beads_run.json").read_text("utf-8"))
    assert run["settings"]["min_snr"] == 1000.0
    assert run["settings"]["outlier_mad"] is None
    assert run["settings"]["bead_size_correction"] is False
    assert run["channels"][0]["accepted"] == 0
    assert "low SNR 8" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--setting", "colour=red"], "unknown analysis setting: colour"),
        (["--setting", "min_snr"], "expected key=value"),
        (["--setting", "min_snr=lots"], "could not convert"),
        (["--setting", "bead_size_correction=maybe"], "must be true or false"),
        (["--setting", "profile_width_px=2"], "odd number"),
        (["--channel", "4"], "channel 4 out of range"),
    ],
)
def test_bad_options_exit_with_status_2(
    extra: list[str],
    message: str,
    beads_tif: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["analyze", str(beads_tif), *OPTICS, "--out", str(tmp_path), *extra])
    assert code == cli.EXIT_ERROR
    assert message in capsys.readouterr().err


def test_output_names_include_the_selection() -> None:
    from dataclasses import replace

    from psfwidget.automation.analyze import output_stem
    from psfwidget.io.model import Provenance

    source = Provenance(Path("a/b/field.ome.tif"), "tiff", 1, None, 0, "x", 0, 0, "r")
    result = type("R", (), {"provenance": source})()
    assert output_stem(result) == "field"
    result.provenance = replace(source, path=Path("f.lif"), series_index=3, timepoint=1, tile=2)
    assert output_stem(result) == "f_s3_t1_m2"


@pytest.mark.req("AUT-01")
def test_analyze_facility_lif_series(
    golden_file: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = golden_file("20261106_QC_SP8.lif")
    profile = Path(__file__).resolve().parents[1] / "examples" / "profiles" / "sp8.toml"
    code = cli.main(
        ["analyze", str(path), "--series", "3", "--profile", str(profile), "--out", str(tmp_path)]
    )
    assert code == cli.EXIT_OK
    assert (tmp_path / "20261106_QC_SP8_s3_summary.csv").exists()
    assert "Channel 1 (ALEXA 568)" in capsys.readouterr().out
