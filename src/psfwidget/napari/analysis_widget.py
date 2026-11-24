"""The analysis dock widget (UI-01, UI-04, DET-07).

Load a file, check or override its metadata, analyse it in a background
thread, review accepted (green) and rejected (red) beads in 2D or 3D, include
or exclude beads by hand, see the metrics, then record the result and write
the report. All logic lives in :class:`~psfwidget.napari.session.AnalysisSession`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from psfwidget.automation.glp import DEFAULT_MEANING
from psfwidget.io.model import SeriesInfo
from psfwidget.napari._compat import guard_layer_list
from psfwidget.napari.series_dialog import choose_series
from psfwidget.napari.session import AnalysisSession, default_user

ACCEPTED_COLOUR = "#0ca30c"
REJECTED_COLOUR = "#e0331f"
FILE_FILTER = "Bead stacks (*.nd2 *.lif *.czi *.tif *.tiff);;All files (*)"
MISSING = QColor("#e0331f")
"""Text colour (italic) of a required value that is missing."""
OVERRIDDEN = QColor("#eb6834")
"""Text colour (bold) of a value the user changed. Only the text is styled, never the
fill, so the rows stay readable in napari's dark and light themes (UI-06); bold and
italic keep the two states apart, as the two colours are close."""
STRETCH = QHeaderView.ResizeMode.Stretch
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def _row(*widgets: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


class AnalysisWidget(QWidget):
    """Analyse one bead stack in napari."""

    def __init__(self, napari_viewer: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        guard_layer_list()  # DEF-01
        self.viewer = napari_viewer
        self.session = AnalysisSession()
        self.series_chooser: Callable[..., int | None] = choose_series
        """Asks the user for a series; replaced in tests."""
        self.series_index = 0
        self._series: list[SeriesInfo] = []
        self._series_path: str | None = None
        self._image_layers: list[Any] = []
        self._point_layers: dict[int, Any] = {}
        self._worker: Any = None
        self._filling = False

        layout = QVBoxLayout(self)

        # ---------------------------------------------------- file
        source = QGroupBox("1. File")
        form = QFormLayout(source)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("an .nd2, .lif, .czi, or .tif file")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_file)
        form.addRow("File", _row(self.path_edit, browse))
        self.series_label = QLabel("–")
        self.series_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.change_series_button = QPushButton("Change…")
        self.change_series_button.clicked.connect(self.change_series)
        self.change_series_button.setEnabled(False)
        form.addRow("Series", _row(self.series_label, self.change_series_button))
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("optional instrument profile (.toml)")
        profile_browse = QPushButton("Browse…")
        profile_browse.clicked.connect(self._browse_profile)
        form.addRow("Profile", _row(self.profile_edit, profile_browse))
        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self.open_file)
        form.addRow(self.open_button)
        layout.addWidget(source)

        # ---------------------------------------------------- metadata
        meta = QGroupBox("2. Metadata (edit a value to override it)")
        meta_layout = QVBoxLayout(meta)
        self.metadata_table = QTableWidget(0, 3)
        self.metadata_table.setHorizontalHeaderLabels(["Field", "Value", "From"])
        self.metadata_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.metadata_table.verticalHeader().setVisible(False)
        self.metadata_table.itemChanged.connect(self._value_edited)
        meta_layout.addWidget(self.metadata_table)
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        meta_layout.addWidget(self.problems_label)
        layout.addWidget(meta)

        # ---------------------------------------------------- analysis
        run = QGroupBox("3. Analyse")
        run_layout = QVBoxLayout(run)
        self.settings_edit = QLineEdit()
        self.settings_edit.setPlaceholderText("optional settings, e.g. min_snr=5, outlier_mad=off")
        run_layout.addWidget(self.settings_edit)
        self.analyze_button = QPushButton("Analyse")
        self.analyze_button.clicked.connect(self.analyze)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        run_layout.addLayout(_row(self.analyze_button, self.progress))
        layout.addWidget(run)

        # ---------------------------------------------------- results and review
        results = QGroupBox("4. Results and review")
        results_layout = QVBoxLayout(results)
        self.channel_combo = QComboBox()
        self.channel_combo.currentIndexChanged.connect(self._show_summary)
        results_layout.addLayout(_row(QLabel("Channel"), self.channel_combo))
        self.summary_table = QTableWidget(0, 5)
        self.summary_table.setHorizontalHeaderLabels(["Metric", "Median", "SD", "n", "÷ theory"])
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.summary_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        results_layout.addWidget(self.summary_table)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        results_layout.addWidget(self.status_label)
        self.view_3d = QCheckBox("3D view")
        self.view_3d.toggled.connect(
            lambda on: setattr(self.viewer.dims, "ndisplay", 3 if on else 2)
        )
        results_layout.addWidget(self.view_3d)
        self.reason_edit = QLineEdit()
        self.reason_edit.setPlaceholderText("reason, e.g. dust on the bead")
        self.reviewer_edit = QLineEdit(default_user())
        self.reviewer_edit.setPlaceholderText("your name")
        review_form = QFormLayout()
        review_form.addRow("Reason", self.reason_edit)
        review_form.addRow("Reviewer", self.reviewer_edit)
        results_layout.addLayout(review_form)
        self.exclude_button = QPushButton("Exclude selected beads")
        self.exclude_button.clicked.connect(lambda: self.review_selected(accept=False))
        self.include_button = QPushButton("Include selected beads")
        self.include_button.clicked.connect(lambda: self.review_selected(accept=True))
        self.undo_button = QPushButton("Undo review")
        self.undo_button.clicked.connect(self.undo_review)
        results_layout.addLayout(_row(self.exclude_button, self.include_button, self.undo_button))
        layout.addWidget(results)

        # ---------------------------------------------------- outputs
        outputs = QGroupBox("5. Record and report")
        out_form = QFormLayout(outputs)
        self.database_edit = QLineEdit()
        self.database_edit.setPlaceholderText("results database (.sqlite), optional")
        database_browse = QPushButton("Browse…")
        database_browse.clicked.connect(self._browse_database)
        out_form.addRow("Database", _row(self.database_edit, database_browse))
        self.record_button = QPushButton("Record in database")
        self.record_button.clicked.connect(self.record)
        out_form.addRow(self.record_button)
        self.report_edit = QLineEdit()
        self.report_edit.setPlaceholderText("folder for the report")
        report_browse = QPushButton("Browse…")
        report_browse.clicked.connect(self._browse_report_folder)
        out_form.addRow("Report folder", _row(self.report_edit, report_browse))
        self.html_box = QCheckBox("HTML")
        self.html_box.setChecked(True)
        self.pdf_box = QCheckBox("PDF")
        self.pdf_box.setChecked(True)
        self.report_button = QPushButton("Write report")
        self.report_button.clicked.connect(self.write_report)
        out_form.addRow(_row(self.html_box, self.pdf_box, self.report_button))
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(outputs)

        # ---------------------------------------------------- review and approval
        approval = QGroupBox("6. Review and approval (signature in the database)")
        approval_form = QFormLayout(approval)
        self.report_combo = QComboBox()
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_report)
        approval_form.addRow("Report", _row(self.report_combo, choose))
        self.meaning_edit = QLineEdit(DEFAULT_MEANING)
        approval_form.addRow("Meaning", self.meaning_edit)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("optional comment")
        approval_form.addRow("Comment", self.comment_edit)
        self.approve_button = QPushButton("Approve")
        self.approve_button.clicked.connect(lambda: self.approve_report(approved=True))
        self.reject_button = QPushButton("Reject")
        self.reject_button.clicked.connect(lambda: self.approve_report(approved=False))
        self.verify_button = QPushButton("Check signature")
        self.verify_button.clicked.connect(self.check_signature)
        approval_form.addRow(_row(self.approve_button, self.reject_button, self.verify_button))
        layout.addWidget(approval)
        layout.addWidget(self.message_label)
        layout.addStretch()
        self._update_buttons()

    # ------------------------------------------------------------ helpers

    def _toggle_3d(self, on: bool) -> None:
        self.viewer.dims.ndisplay = 3 if on else 2

    def _say(self, text: str, error: bool = False) -> None:
        self.message_label.setStyleSheet("color: #e0331f;" if error else "")
        self.message_label.setText(text)

    def _busy(self, busy: bool) -> None:
        self.progress.setRange(0, 0 if busy else 1)
        for widget in (
            self.open_button,
            self.analyze_button,
            self.record_button,
            self.report_button,
            self.exclude_button,
            self.include_button,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons()

    def _run(self, work: Callable[[], Any], done: Callable[[Any], None], what: str) -> None:
        """Run ``work`` in a worker thread so the viewer stays responsive (UI-04)."""
        from napari.qt.threading import create_worker

        self._busy(True)
        self._say(f"{what}…")
        worker = create_worker(work, _start_thread=False)

        def finish(value: Any) -> None:
            try:
                done(value)
            except Exception as exc:  # shown to the user rather than raised in the event loop
                self._say(f"{what} failed: {exc}", error=True)

        worker.returned.connect(finish)
        worker.errored.connect(lambda exc: self._say(f"{what} failed: {exc}", error=True))
        worker.finished.connect(lambda: self._busy(False))
        self._worker = worker
        worker.start()

    def _update_buttons(self) -> None:
        has_stack = self.session.stack is not None
        has_result = self.session.result is not None
        self.analyze_button.setEnabled(has_stack and not self.session.problems())
        for widget in (
            self.record_button,
            self.report_button,
            self.exclude_button,
            self.include_button,
            self.undo_button,
        ):
            widget.setEnabled(has_result)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open bead stack", "", FILE_FILTER)
        if path:
            self.select_file(path)

    def _browse_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Instrument profile", "", "TOML (*.toml)")
        if path:
            self.profile_edit.setText(path)

    def _browse_database(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Results database",
            "",
            "SQLite (*.sqlite *.db)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.database_edit.setText(path)

    def _browse_report_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Report folder")
        if path:
            self.report_edit.setText(path)

    def _show_series(self) -> None:
        chosen = [item for item in self._series if item.index == self.series_index]
        if chosen:
            item = chosen[0]
            sizes = " ".join(f"{k}{v}" for k, v in item.sizes.items())
            total = f" of {len(self._series)}" if len(self._series) > 1 else ""
            self.series_label.setText(f"{item.index}{total}: {item.name} ({sizes})")
        else:
            self.series_label.setText("–")
        self.change_series_button.setEnabled(len(self._series) > 1)

    def select_file(self, path: str | Path) -> bool:
        """Choose a file and, if it holds several series, ask which one (IMP-02).

        Returns ``False`` if the file cannot be read or the user cancels; the
        previous choice is then kept.
        """
        from psfwidget.io import list_series

        path = str(path)
        try:
            series = list_series(path)
        except Exception as exc:  # shown to the user rather than raised in the event loop
            self._say(f"Cannot read {Path(path).name}: {exc}", error=True)
            return False
        if not series:
            self._say(f"{Path(path).name} holds no image series.", error=True)
            return False
        index = series[0].index
        if len(series) > 1:
            chosen = self.series_chooser(Path(path).name, series, index, self)
            if chosen is None:
                self._say(f"No series chosen; {Path(path).name} was not selected.")
                return False
            index = chosen
        self.path_edit.setText(path)
        self._series, self._series_path, self.series_index = series, path, index
        self._show_series()
        return True

    def change_series(self) -> None:
        """Choose another series of the current file; *Open* loads it."""
        if self._series_path is None or len(self._series) < 2:
            return
        chosen = self.series_chooser(
            Path(self._series_path).name, self._series, self.series_index, self
        )
        if chosen is not None and chosen != self.series_index:
            self.series_index = chosen
            self._show_series()
            self._say(f"Series {chosen} chosen; press Open to load it.")

    def load_path(self, path: str | Path) -> None:
        """Select a file (asking for the series if needed) and open it.

        Used when a file is dropped onto napari or opened with *File → Open*
        (IMP-11).
        """
        if self.select_file(path):
            self.open_file()

    # ------------------------------------------------------------ actions

    def open_file(self) -> None:
        """Open the chosen file and series with the chosen profile."""
        path = self.path_edit.text().strip()
        if not path:
            self._say("Choose a file first.", error=True)
            return
        if path != self._series_path and not self.select_file(path):
            return
        series = self.series_index
        profile = self.profile_edit.text().strip() or None

        def work() -> AnalysisSession:
            self.session.profile = None
            self.session.profile_path = None
            self.session.open(path, series=series, profile=profile)
            return self.session

        self._run(work, lambda _: self._opened(), "Opening")

    def _opened(self) -> None:
        self._sync_images()
        # Keep the bead layers (empty until the next analysis), so the layer list
        # changes as little as possible while napari draws it (DEF-01).
        for layer in self._point_layers.values():
            if layer in self.viewer.layers:
                layer.selected_data = set()
                layer.data = np.empty((0, 3))
        self._fill_metadata()
        self.channel_combo.clear()
        self.summary_table.setRowCount(0)
        self.status_label.clear()
        stack = self.session.stack
        assert stack is not None
        self._say(f"Opened {stack.provenance.path.name}, series {stack.provenance.series_index}.")

    def _sync_images(self) -> None:
        """Show the open stack's channels, reusing the existing image layers.

        Removing and re-adding layers while napari's layer list is drawing can
        make napari raise on a stale row (DEF-01), so layers are updated in
        place; they are only added or removed when the number of channels
        changes.
        """
        specs = self.session.image_layers()
        existing = [layer for layer in self._image_layers if layer in self.viewer.layers]
        layers = []
        added = False
        for number, spec in enumerate(specs):
            layer = existing[number] if number < len(existing) else None
            if layer is not None and layer.data.ndim == spec.data.ndim:
                layer.data = spec.data
                layer.scale = spec.scale
                layer.name = spec.name
                layer.colormap = spec.colormap
                layer.reset_contrast_limits_range()
                layer.contrast_limits = spec.contrast_limits
            else:
                if layer is not None:
                    self.viewer.layers.remove(layer)
                layer = self.viewer.add_image(
                    spec.data,
                    name=spec.name,
                    scale=spec.scale,
                    colormap=spec.colormap,
                    contrast_limits=spec.contrast_limits,
                    blending="additive",
                )
                added = True
            layers.append(layer)
        for layer in existing[len(specs) :]:
            self.viewer.layers.remove(layer)
        self._image_layers = layers
        if added:  # keep the bead markers above the images
            for points in self._point_layers.values():
                if points in self.viewer.layers:
                    self.viewer.layers.move(
                        self.viewer.layers.index(points), len(self.viewer.layers)
                    )
        self.viewer.reset_view()

    def _fill_metadata(self) -> None:
        self._filling = True
        rows = self.session.metadata_rows()
        self.metadata_table.setRowCount(len(rows))
        for number, row in enumerate(rows):
            field = QTableWidgetItem(row.label)
            field.setFlags(field.flags() & ~Qt.ItemFlag.ItemIsEditable)
            field.setData(Qt.ItemDataRole.UserRole, row.key)
            if row.note:
                field.setToolTip(row.note)
            value = QTableWidgetItem(row.value)
            source = QTableWidgetItem(row.source or "missing")
            source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
            missing = row.required and not row.value
            changed = row.source == "user"
            if missing or changed:
                for item in (field, value, source):
                    item.setForeground(MISSING if missing else OVERRIDDEN)
                    font = item.font()
                    font.setItalic(missing)
                    font.setBold(not missing)
                    item.setFont(font)
            for column, item in enumerate((field, value, source)):
                self.metadata_table.setItem(number, column, item)
        self.metadata_table.resizeColumnToContents(0)
        problems = self.session.problems()
        self.problems_label.setStyleSheet("color: #e0331f;" if problems else "color: #0ca30c;")
        self.problems_label.setText(
            "Not ready: " + "; ".join(problems) if problems else "Ready for analysis."
        )
        self._filling = False
        self._update_buttons()

    def _value_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        label = self.metadata_table.item(item.row(), 0)
        if label is None:
            return
        key = label.data(Qt.ItemDataRole.UserRole)
        try:
            self.session.set_value(key, item.text())
        except Exception as exc:
            self._say(f"{key}: {exc}", error=True)
        else:
            self._say(f"{key} set." if item.text().strip() else f"{key} override removed.")
            if key.startswith("voxel"):
                self._sync_images()  # show the stack at its new scale
        self._fill_metadata()

    def analyze(self) -> None:
        """Analyse the open stack in a worker thread."""
        from psfwidget.automation.cli import parse_settings

        text = self.settings_edit.text().replace(",", " ").split()
        try:
            self.session.settings = parse_settings(text)
        except ValueError as exc:
            self._say(f"Settings: {exc}", error=True)
            return
        self._run(self.session.analyze, lambda _: self._analysed(), "Analysing")

    def _analysed(self) -> None:
        result = self.session.result
        assert result is not None
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for channel in result.channels:
            self.channel_combo.addItem(channel.name or f"channel {channel.index}", channel.index)
        self.channel_combo.blockSignals(False)
        self._show_points()
        self._show_summary()
        accepted = sum(len(c.accepted) for c in result.channels)
        total = sum(len(c.beads) for c in result.channels)
        self._say(f"Analysed: {accepted} of {total} beads accepted.")
        self._update_buttons()

    def _show_points(self) -> None:
        """Show each channel's beads, updating the existing layers in place (DEF-01)."""
        result = self.session.result
        assert result is not None
        layers: dict[int, Any] = {}
        for channel, points in zip(result.channels, self.session.bead_points(), strict=True):
            size = 3 * max(channel.expected_fwhm_um[0], 0.1)
            colours = [str(c) for c in np.where(points.accepted, ACCEPTED_COLOUR, REJECTED_COLOUR)]
            layer = self._point_layers.get(channel.index)
            if layer is not None and layer in self.viewer.layers:
                selected = set(layer.selected_data)
                layer.data = points.coordinates
                layer.features = points.features
                layer.size = size
                if colours:
                    layer.border_color = colours
                layer.name = points.name
                layer.selected_data = {i for i in selected if i < len(points.coordinates)}
            else:
                layer = self.viewer.add_points(
                    points.coordinates,
                    name=points.name,
                    features=points.features,
                    size=size,
                    face_color="transparent",
                    border_color=colours or "transparent",
                    border_width=0.12,
                    symbol="disc",
                )
            layers[channel.index] = layer
        for index, layer in self._point_layers.items():
            if index not in layers and layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        self._point_layers = layers

    def _show_summary(self) -> None:
        index = self.channel_combo.currentData()
        if index is None or self.session.result is None:
            return
        rows = self.session.summary_rows(index)
        self.summary_table.setRowCount(len(rows))
        for number, row in enumerate(rows):
            for column, text in enumerate(row):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.summary_table.setItem(number, column, item)
        channel = self.session.channel(index)
        lines = [f"{len(channel.accepted)} of {len(channel.beads)} beads accepted."]
        if channel.theory is not None:
            lines.append(
                f"Theory ({channel.theory.model}): lateral {channel.theory.lateral_um * 1000:.0f} "
                f"nm, axial {channel.theory.axial_um * 1000:.0f} nm."
            )
        elif channel.theory_unavailable:
            lines.append(f"No theory: {channel.theory_unavailable}")
        if channel.spec is not None:
            lines.append(f"Spec limits: {channel.spec.status.value}.")
        if channel.rejections:
            lines.append(
                "Rejected: " + ", ".join(f"{k} {v}" for k, v in channel.rejections.items()) + "."
            )
        lines += list(channel.warnings)
        self.status_label.setText("\n".join(lines))

    def selected_beads(self) -> dict[int, list[int]]:
        """Return the bead ids selected in each channel's points layer."""
        found = {}
        for index, layer in self._point_layers.items():
            ids = [int(layer.features["bead_id"][i]) for i in sorted(layer.selected_data)]
            if ids:
                found[index] = ids
        return found

    def review_selected(self, *, accept: bool) -> None:
        """Include or exclude the selected beads with the given reason (DET-07)."""
        selected = self.selected_beads()
        if not selected:
            self._say(
                "Select beads in a 'bead review' layer first (points layer, select mode).",
                error=True,
            )
            return
        reason = self.reason_edit.text().strip()
        reviewer = self.reviewer_edit.text().strip()
        if not reason or not reviewer:
            self._say("Give a reason and your name for the review.", error=True)
            return
        try:
            for channel, ids in selected.items():
                for bead_id in ids:
                    self.session.decide(
                        channel, bead_id, accept=accept, reason=reason, reviewer=reviewer
                    )
        except Exception as exc:
            self._say(str(exc), error=True)
        else:
            count = sum(len(ids) for ids in selected.values())
            self._say(f"{'Included' if accept else 'Excluded'} {count} bead(s); summary updated.")
        self._show_points()
        self._show_summary()

    def undo_review(self) -> None:
        """Return to the automatic bead selection."""
        self.session.undo_decisions()
        self._show_points()
        self._show_summary()
        self._say("Review undone; showing the automatic selection.")

    def record(self) -> None:
        """Record the reviewed result in the database."""
        database = self.database_edit.text().strip()
        if not database:
            self._say("Choose a results database first.", error=True)
            return
        user = self.reviewer_edit.text().strip()

        def work() -> int:
            return self.session.record(database, user)

        self._run(
            work, lambda sid: self._say(f"Recorded as session {sid} in {database}."), "Recording"
        )

    def write_report(self) -> None:
        """Write the session report to the chosen folder."""
        folder = self.report_edit.text().strip()
        formats = {
            f for f, box in (("html", self.html_box), ("pdf", self.pdf_box)) if box.isChecked()
        }
        if not folder or not formats:
            self._say("Choose a report folder and at least one format.", error=True)
            return
        database = self.database_edit.text().strip() or None
        operator = self.reviewer_edit.text().strip() or None

        def work() -> list[Path]:
            return self.session.write_report(folder, formats, database=database, operator=operator)

        self._run(work, self._reports_written, "Writing the report")

    def _reports_written(self, files: list[Path]) -> None:
        for path in files:
            self._add_report(path)
        self._say("Wrote " + ", ".join(str(f) for f in files))

    def _add_report(self, path: Path) -> None:
        if self.report_combo.findData(str(path)) < 0:
            self.report_combo.insertItem(0, path.name, str(path))
        self.report_combo.setCurrentIndex(self.report_combo.findData(str(path)))

    def _choose_report(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Report to sign", "", "Reports (*.pdf *.html);;All files (*)"
        )
        if path:
            self._add_report(Path(path))

    def _approval_inputs(self) -> tuple[Path, str] | None:
        report = self.report_combo.currentData()
        database = self.database_edit.text().strip()
        if not report or not database:
            self._say("Choose a report and a results database first.", error=True)
            return None
        return Path(report), database

    def approve_report(self, *, approved: bool) -> None:
        """Sign the chosen report as approved or rejected (QA-02)."""
        inputs = self._approval_inputs()
        if inputs is None:
            return
        report, database = inputs
        try:
            record = self.session.approve(
                report,
                database,
                approved=approved,
                meaning=self.meaning_edit.text(),
                comment=self.comment_edit.text(),
                reviewer=self.reviewer_edit.text(),
            )
        except Exception as exc:
            self._say(str(exc), error=True)
            return
        self._say(
            f"{report.name} {record['decision']} by {record['user']} at {record['utc']} "
            f"(revision {record['revision']}); signature saved in the database and next to "
            "the report."
        )

    def check_signature(self) -> None:
        """Show whether the chosen report is signed and unchanged since signing."""
        inputs = self._approval_inputs()
        if inputs is None:
            return
        report, database = inputs
        try:
            found = self.session.signatures(report, database)
        except Exception as exc:
            self._say(str(exc), error=True)
            return
        if not found:
            self._say(
                f"{report.name} is not signed, or has changed since it was signed.", error=True
            )
            return
        self._say(
            "; ".join(
                f"{a['decision']} by {a['user']} at {a['utc']} (revision {a['revision']})"
                for a in found
            )
        )
