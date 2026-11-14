"""GUI tests for the analysis dock widget in a headless napari viewer (UI-01, UI-04, DET-07).

Skipped when napari and a Qt binding are not installed (``pip install psfwidget[napari]``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
napari = pytest.importorskip("napari")
pytest.importorskip("pytestqt")

from psfwidget import synthetic  # noqa: E402
from psfwidget.napari.analysis_widget import AnalysisWidget  # noqa: E402

PROFILE = (
    '[instrument]\nid = "scope"\nmodality = "widefield"\n'
    '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
    '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
)


@pytest.fixture
def stack_file(tmp_path: Path) -> Path:
    spec = synthetic.FieldSpec(
        shape=(31, 96, 96),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    field = synthetic.make_bead_field(
        spec,
        5,
        (0.6, 0.22, 0.22),
        4e4,
        np.random.default_rng(4),
        margin_fwhm=2.0,
        min_separation_fwhm=4.0,
    )
    path = tmp_path / "beads.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    (tmp_path / "scope.toml").write_text(PROFILE, encoding="utf-8")
    return path


def _wait(qtbot: Any, widget: AnalysisWidget, condition: Callable[[], bool]) -> None:
    qtbot.waitUntil(lambda: widget.progress.maximum() == 1 and condition(), timeout=60_000)


@pytest.mark.req("UI-01", "UI-04", "DET-07")
def test_analysis_widget_end_to_end(
    make_napari_viewer: Any, qtbot: Any, stack_file: Path, tmp_path: Path
) -> None:
    viewer = make_napari_viewer()
    widget = AnalysisWidget(viewer)
    viewer.window.add_dock_widget(widget)

    # Open without a profile: metadata is missing, so analysis is not offered.
    widget.path_edit.setText(str(stack_file))
    widget.open_file()
    _wait(qtbot, widget, lambda: widget.session.stack is not None)
    assert len(viewer.layers) == 1
    assert tuple(viewer.layers[0].scale) == pytest.approx((0.15, 0.05, 0.05))
    assert "Not ready" in widget.problems_label.text()
    assert not widget.analyze_button.isEnabled()

    # Fill the missing values in the metadata table.
    def cell(row: int, column: int) -> Any:
        item = widget.metadata_table.item(row, column)
        assert item is not None
        return item

    rows = {cell(r, 0).data(0x0100): r for r in range(widget.metadata_table.rowCount())}
    for key, value in (
        ("numerical_aperture", "1.4"),
        ("immersion_ri", "1.518"),
        ("modality", "widefield"),
        ("channel.0.emission_nm", "520"),
    ):
        cell(rows[key], 1).setText(value)
    assert widget.problems_label.text() == "Ready for analysis."
    cell(rows["numerical_aperture"], 1).setText("lots")
    assert "numerical_aperture" in widget.message_label.text()  # rejected, shown

    widget.analyze()
    assert widget.progress.maximum() == 0  # busy indicator while the worker runs
    _wait(qtbot, widget, lambda: widget.session.result is not None)
    assert "Analysed" in widget.message_label.text(), widget.message_label.text()
    points = viewer.layers["bead review [channel 0]"]
    accepted = len(widget.session.channel(0).accepted)
    assert accepted >= 3
    assert widget.summary_table.rowCount() == 7

    # Exclude one bead by selecting it in the points layer.
    first = next(i for i, s in enumerate(points.features["status"]) if s == "accepted")
    points.selected_data = {first}
    widget.reason_edit.setText("dust")
    widget.reviewer_edit.setText("ab")
    widget.review_selected(accept=False)
    assert len(widget.session.channel(0).accepted) == accepted - 1
    assert "Excluded 1" in widget.message_label.text()
    widget.undo_review()
    assert len(widget.session.channel(0).accepted) == accepted

    # Record (with an instrument from a profile) and write the report.
    widget.profile_edit.setText(str(stack_file.parent / "scope.toml"))
    widget.open_file()
    _wait(qtbot, widget, lambda: widget.session.profile is not None)
    widget.analyze()
    _wait(qtbot, widget, lambda: widget.session.result is not None)
    widget.database_edit.setText(str(tmp_path / "qc.sqlite"))
    widget.record()
    _wait(qtbot, widget, lambda: widget.session.session_id is not None)
    widget.report_edit.setText(str(tmp_path / "reports"))
    widget.pdf_box.setChecked(False)
    widget.write_report()
    _wait(qtbot, widget, lambda: "Wrote" in widget.message_label.text())
    assert list((tmp_path / "reports").glob("*_report.html"))
    widget.view_3d.setChecked(True)
    assert viewer.dims.ndisplay == 3
