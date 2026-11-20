"""Drag and drop, the series dialog, and the crash-on-load fix (M5.4a).

Covers IMP-11 (the napari reader), IMP-02 (choosing a series in the GUI), and
defect DEF-01 (napari raising while layers change). Run on a headless Linux
machine with ``QT_QPA_PLATFORM=xcb xvfb-run -a python -m pytest tests/test_gui_reader.py``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

napari = pytest.importorskip("napari")
pytest.importorskip("pytestqt")
if sys.platform.startswith("linux") and not (
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
):
    pytest.skip(
        "the napari viewer needs an OpenGL display; on a headless Linux machine run "
        "QT_QPA_PLATFORM=xcb xvfb-run -a python -m pytest tests/test_gui_reader.py",
        allow_module_level=True,
    )

from psfwidget import synthetic  # noqa: E402
from psfwidget.io.model import SeriesInfo  # noqa: E402
from psfwidget.napari import analysis_widget, reader  # noqa: E402
from psfwidget.napari._compat import guard_layer_list  # noqa: E402
from psfwidget.napari.analysis_widget import AnalysisWidget  # noqa: E402
from psfwidget.napari.series_dialog import SeriesDialog, series_cells  # noqa: E402

PROFILE = (
    '[instrument]\nid = "scope"\nmodality = "widefield"\n'
    '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
    '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
    '[[channel]]\nid = "red"\nmatch = { index = 1 }\nemission_nm = 600\n'
)

DROPPED = (
    # (golden file, series to choose if asked)
    ("BF007.nd2", None),
    ("PR2729_frameOrderCombinedScanTypes.lif", None),
    ("Plate1-Blue-A-25-Scene-1-P1-F5-01.czi", None),
    ("20261111_psfbeads_SPD_1x_SR_crop.ome.tif", None),
    ("20261111_psfbeads_SPD_1x_SR_crop.tif", None),
    ("20261106_QC_SP8.lif", 4),
    ("20261111_QC_980.czi", None),
    ("20261012_QC_AxR_NSPARC_crop.nd2", None),
)


def _bead_field(channels: int, seed: int) -> np.ndarray:
    spec = synthetic.FieldSpec(
        shape=(25, 80, 80),
        voxel_size_um=(0.15, 0.05, 0.05),
        background_photons=20,
        offset=100,
        read_noise=2,
        bit_depth=12,
    )
    planes = [
        synthetic.make_bead_field(
            spec,
            4,
            (0.6, 0.22, 0.22),
            4e4,
            np.random.default_rng(seed + c),
            margin_fwhm=2.0,
            min_separation_fwhm=4.0,
        ).data
        for c in range(channels)
    ]
    return np.stack(planes, axis=1)  # ZCYX


def _write(path: Path, channels: int, seed: int) -> Path:
    tifffile.imwrite(
        path,
        _bead_field(channels, seed),
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZCYX", "spacing": 0.15, "unit": "um"},
    )
    return path


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    """One- and two-channel stacks, a three-series OME-TIFF, and a profile."""
    multi = tmp_path / "positions.ome.tif"
    with tifffile.TiffWriter(multi, ome=True) as writer:
        for index in range(3):
            writer.write(
                _bead_field(1, 10 + index)[:, 0],
                metadata={
                    "axes": "ZYX",
                    "Name": f"position {index}",
                    "PhysicalSizeX": 0.05,
                    "PhysicalSizeY": 0.05,
                    "PhysicalSizeZ": 0.15,
                },
            )
    (tmp_path / "scope.toml").write_text(PROFILE, encoding="utf-8")
    return {
        "one": _write(tmp_path / "one_channel.tif", 1, 1),
        "two": _write(tmp_path / "two_channels.tif", 2, 5),
        "multi": multi,
        "profile": tmp_path / "scope.toml",
    }


class Chooser:
    """Stands in for the series dialog: records each call and gives a fixed answer."""

    def __init__(self, answer: int | None) -> None:
        self.answer = answer
        self.calls: list[list[SeriesInfo]] = []

    def __call__(
        self, name: str, series: list[SeriesInfo], current: int, parent: Any
    ) -> int | None:
        self.calls.append(list(series))
        return self.answer


@pytest.fixture
def chooser(monkeypatch: pytest.MonkeyPatch) -> Callable[[int | None], Chooser]:
    """Replace the series dialog in widgets created after the call."""

    def install(answer: int | None) -> Chooser:
        fake = Chooser(answer)
        monkeypatch.setattr(analysis_widget, "choose_series", fake)
        return fake

    return install


def _ready(qtbot: Any, widget: AnalysisWidget, condition: Callable[[], bool]) -> None:
    qtbot.waitUntil(lambda: widget.progress.maximum() == 1 and condition(), timeout=60_000)


def _opened_from(widget: AnalysisWidget) -> Path | None:
    stack = widget.session.stack
    return None if stack is None else stack.provenance.path


def _opened_series(widget: AnalysisWidget) -> int | None:
    stack = widget.session.stack
    return None if stack is None else stack.provenance.series_index


def _dropped_widget(viewer: Any) -> AnalysisWidget:
    _, widget = viewer.window.add_plugin_dock_widget(reader.PLUGIN, reader.WIDGET)
    assert isinstance(widget, AnalysisWidget)
    return widget


# ------------------------------------------------------------------ reader (IMP-11)


@pytest.mark.req("IMP-11")
def test_reader_claims_the_bead_formats(tmp_path: Path) -> None:
    for name in ("a.nd2", "a.lif", "a.czi", "a.tif", "a.tiff", "a.ome.tif", "A.ND2", "b.OME.TIFF"):
        assert reader.get_reader(str(tmp_path / name)) is reader.read, name
        assert reader.get_reader(tmp_path / name) is reader.read, name
    for other in ("a.png", "a.zarr", "a.tif.txt", "notes"):
        assert reader.get_reader(str(tmp_path / other)) is None, other
    assert reader.get_reader([str(tmp_path / "a.nd2")]) is None


@pytest.mark.req("IMP-11")
def test_manifest_declares_the_reader() -> None:
    from npe2 import PluginManifest

    manifest = PluginManifest.from_distribution("psfwidget")
    (contribution,) = manifest.contributions.readers or []
    assert contribution.command == "psfwidget.get_reader"
    assert set(contribution.filename_patterns) == {"*.nd2", "*.lif", "*.czi", "*.tif", "*.tiff"}
    assert not contribution.accepts_directories


@pytest.mark.req("IMP-11", "IMP-02")
@pytest.mark.parametrize(("name", "series"), DROPPED)
def test_dropped_file_opens_in_the_widget(
    make_napari_viewer: Any,
    qtbot: Any,
    golden_file: Callable[[str], Path],
    chooser: Callable[[int | None], Chooser],
    name: str,
    series: int | None,
) -> None:
    path = golden_file(name)
    fake = chooser(series)
    viewer = make_napari_viewer()
    viewer.open(str(path), plugin="psfwidget")  # what napari does on a drop
    widget = _dropped_widget(viewer)
    _ready(qtbot, widget, lambda: widget.session.stack is not None)
    stack = widget.session.stack
    assert stack is not None
    assert stack.provenance.path == path
    assert stack.provenance.series_index == (series or 0)
    assert len(fake.calls) == (1 if series is not None else 0)  # asked only if several
    assert len(viewer.layers) == stack.n_channels
    assert widget.metadata_table.rowCount() > 0
    assert widget.path_edit.text() == str(path)


@pytest.mark.req("IMP-11")
def test_reader_without_a_viewer_returns_layers(files: dict[str, Path]) -> None:
    layers = reader._layers(files["two"])
    assert len(layers) == 2
    data, meta, kind = layers[0]
    assert kind == "image"
    assert data.shape == (25, 80, 80)
    assert meta["scale"] == pytest.approx((0.15, 0.05, 0.05))


# ------------------------------------------------------------------ series (IMP-02)


@pytest.mark.req("IMP-02")
def test_series_dialog_lists_every_series(qtbot: Any) -> None:
    series = [
        SeriesInfo(0, "field 1", {"C": 2, "Z": 48, "Y": 128, "X": 128}, "uint8"),
        SeriesInfo(1, "field 2", {"T": 3, "C": 2, "Z": 48, "Y": 128, "X": 128}, "uint8"),
        SeriesInfo(2, "field 3", {"Z": 10, "Y": 64, "X": 32}, "uint16"),
    ]
    dialog = SeriesDialog("beads.lif", series, current=1)
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 3
    assert dialog.chosen() == 1  # the current series is preselected
    assert series_cells(series[1]) == ("1", "field 2 (T3)", "2", "48", "128 × 128", "uint8")
    assert series_cells(series[2])[2] == "1"  # no C axis: one channel
    dialog.table.selectRow(2)
    assert dialog.chosen() == 2


@pytest.mark.req("IMP-02")
def test_multi_series_file_asks_which_series(
    make_napari_viewer: Any,
    qtbot: Any,
    files: dict[str, Path],
    chooser: Callable[[int | None], Chooser],
) -> None:
    fake = chooser(2)
    viewer = make_napari_viewer()
    viewer.open(str(files["multi"]), plugin="psfwidget")
    widget = _dropped_widget(viewer)
    _ready(qtbot, widget, lambda: widget.session.stack is not None)
    assert [s.name for s in fake.calls[0]] == ["position 0", "position 1", "position 2"]
    assert widget.session.stack is not None
    assert widget.session.stack.provenance.series_index == 2
    assert widget.series_label.text().startswith("2 of 3: position 2")
    assert widget.change_series_button.isEnabled()

    # Change the series: chosen now, loaded with Open.
    fake.answer = 0
    widget.change_series()
    assert widget.series_index == 0
    assert "press Open" in widget.message_label.text()
    widget.open_file()
    _ready(qtbot, widget, lambda: _opened_series(widget) == 0)

    # Cancelling the dialog keeps the open file and series.
    fake.answer = None
    widget.load_path(files["multi"])
    assert "No series chosen" in widget.message_label.text()
    assert widget.series_index == 0

    # A single-series file opens without asking.
    asked = len(fake.calls)
    widget.load_path(files["one"])
    _ready(qtbot, widget, lambda: _opened_from(widget) == files["one"])
    assert len(fake.calls) == asked
    assert not widget.change_series_button.isEnabled()


@pytest.mark.req("IMP-02")
def test_browse_and_typed_paths_also_ask(
    make_napari_viewer: Any,
    qtbot: Any,
    files: dict[str, Path],
    chooser: Callable[[int | None], Chooser],
) -> None:
    fake = chooser(1)
    viewer = make_napari_viewer()
    widget = AnalysisWidget(viewer)
    viewer.window.add_dock_widget(widget)
    assert widget.select_file(files["multi"])  # what Browse does
    assert len(fake.calls) == 1
    assert widget.session.stack is None  # Browse chooses; Open loads
    widget.open_file()
    _ready(qtbot, widget, lambda: widget.session.stack is not None)
    assert _opened_series(widget) == 1

    widget.path_edit.setText(str(files["one"]))  # a typed path is listed on Open
    widget.open_file()
    _ready(qtbot, widget, lambda: _opened_from(widget) == files["one"])

    widget.path_edit.setText(str(files["one"].with_name("missing.tif")))
    widget.open_file()
    assert "Cannot read missing.tif" in widget.message_label.text()


# ------------------------------------------------------------------ DEF-01


@pytest.mark.req("UI-01")
def test_layer_list_skips_vanished_rows(qtbot: Any) -> None:
    """The guard stops napari raising on a row that is gone (the facility traceback)."""
    from napari._qt.containers._layer_delegate import LayerDelegate
    from qtpy.QtCore import QRect
    from qtpy.QtGui import QImage, QPainter, QStandardItem, QStandardItemModel
    from qtpy.QtWidgets import QStyleOptionViewItem

    assert guard_layer_list()
    assert guard_layer_list()  # a second call does not wrap twice
    assert not hasattr(LayerDelegate.paint.__wrapped__, "__wrapped__")

    # An index whose size hint and loaded state read as None, as for a row
    # removed while the view draws it.
    model = QStandardItemModel()
    model.appendRow(QStandardItem("gone"))
    index = model.index(0, 0)
    assert index.data(0x000D) is None  # SizeHintRole
    image = QImage(200, 40, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 200, 34)
    try:
        LayerDelegate().paint(painter, option, index)  # must not raise
    finally:
        painter.end()


@pytest.mark.req("UI-01", "UI-04")
@pytest.mark.parametrize("asynchronous", [False, True])
def test_reopening_and_reviewing_update_layers_in_place(
    make_napari_viewer: Any, qtbot: Any, files: dict[str, Path], asynchronous: bool
) -> None:
    """Open, re-open, and review repeatedly in a shown viewer, without errors (DEF-01).

    pytest-qt fails the test on any exception raised inside Qt, such as the
    one napari raised at the facility.
    """
    from napari.settings import get_settings

    settings = get_settings().experimental
    previous = settings.async_
    settings.async_ = asynchronous
    try:
        viewer = make_napari_viewer(show=True)
        widget = AnalysisWidget(viewer)
        viewer.window.add_dock_widget(widget)
        widget.profile_edit.setText(str(files["profile"]))

        def open_(path: Path) -> None:
            widget.load_path(path)
            _ready(qtbot, widget, lambda: _opened_from(widget) == path)
            qtbot.wait(20)

        open_(files["two"])
        first = list(viewer.layers)
        for _ in range(3):
            open_(files["two"])
        assert list(viewer.layers) == first  # same layer objects, updated in place
        open_(files["one"])
        assert len(viewer.layers) == 1
        open_(files["two"])
        assert len(viewer.layers) == 2

        widget.analyze()
        _ready(qtbot, widget, lambda: widget.session.result is not None)
        assert len(viewer.layers) == 4  # two images and two bead layers
        points = next(layer for layer in viewer.layers if layer.name.startswith("bead review"))
        accepted = len(widget.session.channel(0).accepted)
        first_accepted = next(
            i for i, s in enumerate(points.features["status"]) if s == "accepted"
        )
        for round_ in range(10):
            points.selected_data = {first_accepted}
            widget.reason_edit.setText("dust")
            widget.reviewer_edit.setText("ab")
            widget.review_selected(accept=bool(round_ % 2))
            qtbot.wait(10)
            assert points in viewer.layers
        assert len(widget.session.channel(0).accepted) == accepted
        assert list(points.border_color[first_accepted][:3]) == pytest.approx(
            [12 / 255, 163 / 255, 12 / 255], abs=0.01
        )

        open_(files["two"])  # re-opening empties the bead layers but keeps them
        assert len(viewer.layers) == 4
        assert len(points.data) == 0
        widget.analyze()
        _ready(qtbot, widget, lambda: widget.session.result is not None)
        assert len(points.data) == len(widget.session.channel(0).beads)
        open_(files["one"])  # fewer channels: the extra layers go
        widget.analyze()
        _ready(qtbot, widget, lambda: widget.session.result is not None)
        assert len(viewer.layers) == 2
        assert viewer.layers[-1].name.startswith("bead review")  # markers above images
    finally:
        settings.async_ = previous
