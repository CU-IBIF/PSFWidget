"""Tests for the ``psfwidget info`` command (AUT-01)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget.automation import cli

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "profiles"


@pytest.fixture
def export(tmp_path: Path) -> Path:
    path = tmp_path / "export.tif"
    tifffile.imwrite(
        path,
        np.arange(5 * 3 * 8 * 8, dtype=np.uint16).reshape(5, 3, 8, 8),
        imagej=True,
        resolution=(1 / 0.0424, 1 / 0.0424),
        metadata={"axes": "ZCYX", "spacing": 0.3, "unit": "um"},
    )
    return path


@pytest.mark.req("AUT-01", "IMP-06")
def test_info_reports_missing_metadata(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info", str(export)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "File      export.tif  (tiff, 0.0 MB)" in out
    assert "Selected  series 0, timepoint 0, tile 0:  C3 Z5 Y8 X8  uint16" in out
    assert "voxel_z_um" in out
    assert "Status: NOT ready for analysis" in out
    assert "  - missing numerical_aperture" in out
    assert "  - missing emission_nm for channel 2" in out

    assert cli.main(["info", str(export), "--strict", "--no-hash"]) == cli.EXIT_NOT_READY
    assert "SHA-256   not computed" in capsys.readouterr().out


@pytest.mark.req("AUT-01", "IMP-05", "IMP-10")
def test_info_with_profile_and_override(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "info",
            str(export),
            "--profile",
            str(EXAMPLES / "csu-livesr-tiff.toml"),
            "--set",
            "channel.1.emission_nm=520",
            "--set",
            "numerical_aperture=1.45",
            "--strict",
        ]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "Status: ready for analysis" in out
    lines = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    assert "user" in lines["numerical_aperture"]
    assert "profile  (profile csu-livesr)" in lines["immersion_ri"]
    assert "file" in lines["voxel_z_um"]
    assert "Channel 2" in out


@pytest.mark.req("AUT-01")
def test_info_json_output(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info", str(export), "--json", "--set", "immersion_ri=1.515"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["file"]["name"] == "export.tif"
    assert len(data["file"]["sha256"]) == 64
    assert data["series"] == [
        {
            "index": 0,
            "name": data["series"][0]["name"],
            "sizes": {"Z": 5, "C": 3, "Y": 8, "X": 8},
            "dtype": "uint16",
        }
    ]
    assert data["shape"] == {"C": 3, "Z": 5, "Y": 8, "X": 8}
    assert data["metadata"]["immersion_ri"] == {"value": 1.515, "source": "user", "note": ""}
    assert data["metadata"]["numerical_aperture"] is None
    assert data["metadata"]["voxel_z_um"]["source"] == "file"
    assert [c["index"] for c in data["channels"]] == [0, 1, 2]
    assert data["analyzable"] is False
    assert "missing numerical_aperture" in data["problems"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["{missing}"], "no such file"),
        (["{file}", "--set", "aperture=1"], "unknown metadata field: aperture"),
        (["{file}", "--set", "numerical_aperture=-2"], "must be a positive number"),
        (["{file}", "--series", "3"], "series 3 is out of range"),
        (["{file}", "--timepoint", "1"], "timepoint 1 is out of range"),
        (["{file}", "--profile", "{missing}"], "No such file"),
        (["{file}", "--profile", "{bad_profile}"], "not valid TOML"),
        (["{file}", "--profile", "{sp8}", "--objective", "10x"], "has no objective '10x'"),
        (["{text}"], "unsupported file type '.txt'"),
    ],
)
def test_info_errors_exit_with_status_2(
    arguments: list[str],
    message: str,
    export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad_profile = tmp_path / "bad.toml"
    bad_profile.write_text("[instrument", encoding="utf-8")
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    values = {
        "file": str(export),
        "missing": str(tmp_path / "absent.nd2"),
        "bad_profile": str(bad_profile),
        "sp8": str(EXAMPLES / "sp8.toml"),
        "text": str(text),
    }
    code = cli.main(["info", *(a.format(**values) for a in arguments)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_ERROR
    assert captured.out == ""
    assert captured.err.startswith("psfwidget: error: ")
    assert message in captured.err


@pytest.mark.req("AUT-01", "IMP-02")
def test_info_marks_the_selected_series_and_shows_notes(
    golden_file: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path = golden_file("Exception_2.nd2")
    assert cli.main(["info", str(path), "--no-hash"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  * [0] Exception_2  Z31 Y520 X696  uint16" in out
    assert "Notes\n  - numerical aperture 1.2 exceeds the immersion refractive index 1" in out
    assert "objective_name          Plan Apochromat 60x WI/C" in out
