"""GUI test for the profile editor (UI-03). Skipped without Qt and pytest-qt."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")
pytest.importorskip("pytestqt")

from psfwidget.napari.profile_editor import ProfileEditor
from psfwidget.profiles import load_profile

EXAMPLES = sorted((Path(__file__).parents[1] / "examples" / "profiles").glob("*.toml"))


@pytest.mark.req("UI-03")
def test_profile_editor(qtbot: Any, tmp_path: Path) -> None:
    editor = ProfileEditor()
    qtbot.addWidget(editor)
    source = next(p for p in EXAMPLES if p.name == "sp8.toml")
    editor.load(source)
    assert editor.fields["id"].text() == "sp8"
    assert editor.channels.rowCount() == 2
    assert editor.check()
    original = tomllib.loads(source.read_text("utf-8"))
    assert editor.to_data() == original

    # Add spec limits and tracking, then save and read back.
    editor.fields["lateral_ratio_max"].setText("1.3")
    editor.fields["min_beads"].setText("5")
    editor.fields["baseline_sessions"].setText("10")
    editor.fields["metrics"].setText("fwhm_z_um, ratio_z")
    saved = editor.save(tmp_path / "sp8-edited.toml")
    assert saved is not None
    profile = load_profile(saved)
    assert profile.spec.lateral_ratio_max == 1.3
    assert profile.spec.min_beads == 5
    assert profile.tracking.metrics == ("fwhm_z_um", "ratio_z")

    # Invalid values are reported and nothing is saved.
    editor.fields["lateral_ratio_max"].setText("tight")
    assert editor.save(tmp_path / "bad.toml") is None
    assert "not a number" in editor.message.text()
    editor.fields["lateral_ratio_max"].setText("")
    editor.fields["metrics"].setText("sharpness")
    assert not editor.check()
    assert "sharpness" in editor.message.text()

    editor.new()
    assert not editor.check()  # an id is required
    editor.fields["id"].setText("lsm")
    editor._add_row(editor.objectives, ["63x", "", "", "1.4", "1.518"])
    editor._add_row(editor.channels, ["green", "excitation_nm", "488", "", "", "520"])
    assert editor.check()
    data = editor.to_data()
    assert data["channel"][0]["match"] == {"excitation_nm": 488}
    assert data["objective"][0]["numerical_aperture"] == 1.4
