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

from psfwidget.napari.session import AnalysisSession, default_user

ACCEPTED_COLOUR = "#0ca30c"
REJECTED_COLOUR = "#e0331f"
FILE_FILTER = "Bead stacks (*.nd2 *.lif *.czi *.tif *.tiff);;All files (*)"
MISSING = QColor("#f6d8d3")
OVERRIDDEN = QColor("#dbe8f8")
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
        self.viewer = napari_viewer
        self.session = AnalysisSession()
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
        self.series_combo = QComboBox()
        form.addRow("Series", self.series_combo)
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
        out_form.addRow(self.message_label)
        layout.addWidget(outputs)
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
        worker.returned.connect(done)
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
            self.path_edit.setText(path)
            self._list_series(path)

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

    def _list_series(self, path: str) -> None:
        from psfwidget.io import list_series

        self.series_combo.clear()
        try:
            for item in list_series(path):
                sizes = " ".join(f"{k}{v}" for k, v in item.sizes.items())
                self.series_combo.addItem(f"{item.index}: {item.name} ({sizes})", item.index)
        except Exception as exc:  # shown to the user rather than raised in the event loop
            self._say(str(exc), error=True)

    # ------------------------------------------------------------ actions

    def open_file(self) -> None:
        """Open the chosen file and series with the chosen profile."""
        path = self.path_edit.text().strip()
        if not path:
            self._say("Choose a file first.", error=True)
            return
        if self.series_combo.count() == 0:
            self._list_series(path)
        series = self.series_combo.currentData() or 0
        profile = self.profile_edit.text().strip() or None

        def work() -> AnalysisSession:
            self.session.profile = None
            self.session.profile_path = None
            self.session.open(path, series=series, profile=profile)
            return self.session

        self._run(work, lambda _: self._opened(), "Opening")

    def _opened(self) -> None:
        for layer in self._image_layers + list(self._point_layers.values()):
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        self._point_layers = {}
        self._image_layers = [
            self.viewer.add_image(
                spec.data,
                name=spec.name,
                scale=spec.scale,
                colormap=spec.colormap,
                contrast_limits=spec.contrast_limits,
                blending="additive",
            )
            for spec in self.session.image_layers()
        ]
        self._fill_metadata()
        self.channel_combo.clear()
        self.summary_table.setRowCount(0)
        self.status_label.clear()
        stack = self.session.stack
        assert stack is not None
        self._say(f"Opened {stack.provenance.path.name}, series {stack.provenance.series_index}.")

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
            if row.required and not row.value:
                for item in (field, value, source):
                    item.setBackground(MISSING)
            elif row.source == "user":
                for item in (field, value, source):
                    item.setBackground(OVERRIDDEN)
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
        selected = {index: set(layer.selected_data) for index, layer in self._point_layers.items()}
        for layer in self._point_layers.values():
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        self._point_layers = {}
        result = self.session.result
        assert result is not None
        for channel, points in zip(result.channels, self.session.bead_points(), strict=True):
            size = 3 * max(channel.expected_fwhm_um[0], 0.1)
            colours = np.where(points.accepted, ACCEPTED_COLOUR, REJECTED_COLOUR)
            layer = self.viewer.add_points(
                points.coordinates,
                name=points.name,
                features=points.features,
                size=size,
                face_color="transparent",
                border_color=list(colours),
                border_width=0.12,
                symbol="disc",
            )
            layer.selected_data = selected.get(channel.index, set())
            self._point_layers[channel.index] = layer

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

        self._run(
            work,
            lambda files: self._say("Wrote " + ", ".join(str(f) for f in files)),
            "Writing the report",
        )
