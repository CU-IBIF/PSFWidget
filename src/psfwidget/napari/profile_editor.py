"""The instrument profile and spec-limit editor (UI-03).

Edits the values of a profile TOML file in forms and tables, checks them with
the same validation as every other part of PSFWidget, and saves only a valid
profile. Tracking rules set by hand in the file (``rules`` and
``metric_rules``) are kept unchanged. Comments in the file are not kept.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from psfwidget.io.model import Modality
from psfwidget.profiles.model import ProfileError, parse_profile
from psfwidget.profiles.write import dumps

OBJECTIVE_COLUMNS = ("id", "match", "name", "numerical_aperture", "immersion_ri", "magnification")
CHANNEL_COLUMNS = (
    "id",
    "match by",
    "match value",
    "name",
    "excitation_nm",
    "emission_nm",
    "pinhole_airy",
    "pinhole_um",
)
MATCH_KINDS = ("index", "excitation_nm", "name")
TEXT_KEYS = frozenset({"id", "match", "name", "product", "resolution_enhancement", "match value"})
SPEC_FIELDS = ("lateral_ratio_max", "axial_ratio_max", "ellipticity_min", "min_beads")


class EditorError(ValueError):
    """Raised when a value in the editor cannot be read."""


def number(text: str, where: str) -> int | float:
    """Read a number typed by the user.

    Raises
    ------
    EditorError
        If ``text`` is not a number.
    """
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        raise EditorError(f"{where}: {text!r} is not a number") from None


def _cell(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text().strip()


class ProfileEditor(QWidget):
    """Create or edit an instrument profile."""

    def __init__(self, napari_viewer: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewer = napari_viewer
        self.path: Path | None = None
        self._kept_tracking: dict[str, Any] = {}

        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        for label, slot in (
            ("New", self.new),
            ("Open…", self._open),
            ("Check", self.check),
            ("Save as…", self._save_as),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.path_label = QLabel("New profile")
        layout.addWidget(self.path_label)

        instrument = QGroupBox("Instrument and beads")
        form = QFormLayout(instrument)
        self.fields: dict[str, QLineEdit] = {}
        for key, label in (
            ("id", "Id (required)"),
            ("name", "Name"),
            ("resolution_enhancement", "Resolution enhancement"),
            ("pinhole_um", "Pinhole (µm)"),
            ("diameter_nm", "Bead diameter (nm)"),
            ("product", "Bead product"),
        ):
            self.fields[key] = QLineEdit()
            form.addRow(label, self.fields[key])
        self.modality = QComboBox()
        self.modality.addItem("(from file)", "")
        for modality in Modality:
            self.modality.addItem(modality.value, modality.value)
        form.addRow("Modality", self.modality)
        layout.addWidget(instrument)

        self.objectives = self._table_group(layout, "Objectives", OBJECTIVE_COLUMNS)
        self.channels = self._table_group(layout, "Channels (bead dye values)", CHANNEL_COLUMNS)

        limits = QGroupBox("Spec limits (empty: not checked) and tracking")
        limit_form = QFormLayout(limits)
        for key in SPEC_FIELDS:
            self.fields[key] = QLineEdit()
            limit_form.addRow(key.replace("_", " "), self.fields[key])
        self.fields["baseline_sessions"] = QLineEdit()
        limit_form.addRow("baseline sessions", self.fields["baseline_sessions"])
        self.fields["metrics"] = QLineEdit()
        self.fields["metrics"].setPlaceholderText("default metrics; or e.g. fwhm_z_um, ratio_z")
        limit_form.addRow("tracked metrics", self.fields["metrics"])
        layout.addWidget(limits)

        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch()

    def _table_group(
        self, layout: QVBoxLayout, title: str, columns: Sequence[str]
    ) -> QTableWidget:
        group = QGroupBox(title)
        inner = QVBoxLayout(group)
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([c.replace("_", " ") for c in columns])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        inner.addWidget(table)
        add, remove = QPushButton("Add"), QPushButton("Remove selected")
        add.clicked.connect(lambda: self._add_row(table))
        remove.clicked.connect(lambda: self._remove_rows(table))
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        inner.addLayout(row)
        layout.addWidget(group)
        return table

    def _add_row(self, table: QTableWidget, values: Sequence[str] = ()) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column in range(table.columnCount()):
            text = values[column] if column < len(values) else ""
            if table is self.channels and CHANNEL_COLUMNS[column] == "match by":
                combo = QComboBox()
                combo.addItems(MATCH_KINDS)
                combo.setCurrentText(text or "index")
                table.setCellWidget(row, column, combo)
            else:
                table.setItem(row, column, QTableWidgetItem(text))

    def _remove_rows(self, table: QTableWidget) -> None:
        for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

    def _say(self, text: str, error: bool = False) -> None:
        self.message.setStyleSheet("color: #e0331f;" if error else "color: #0ca30c;")
        self.message.setText(text)

    # ------------------------------------------------------------ data in

    def new(self) -> None:
        """Start an empty profile."""
        self.load_data({"instrument": {"id": ""}})
        self.path = None
        self.path_label.setText("New profile")

    def load(self, path: str | Path) -> None:
        """Open a profile file.

        Raises
        ------
        ProfileError
            If the file is not valid TOML.
        """
        path = Path(path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProfileError(f"{path.name}: not valid TOML: {exc}") from None
        self.load_data(data)
        self.path = path
        self.path_label.setText(str(path))

    def load_data(self, data: dict[str, Any]) -> None:
        """Show parsed profile data in the editor."""
        instrument = data.get("instrument", {})
        bead = data.get("bead", {})
        spec = data.get("spec", {})
        tracking = dict(data.get("tracking", {}))
        values = {**instrument, **bead, **spec}
        values["diameter_nm"] = bead.get("diameter_nm", "")
        values["baseline_sessions"] = tracking.pop("baseline_sessions", "")
        metrics = tracking.pop("metrics", None)
        values["metrics"] = ", ".join(metrics) if metrics else ""
        self._kept_tracking = tracking
        for key, edit in self.fields.items():
            item = values.get(key, "")
            edit.setText("" if item is None else str(item))
        self.modality.setCurrentIndex(
            max(self.modality.findData(instrument.get("modality", "")), 0)
        )
        for table in (self.objectives, self.channels):
            table.setRowCount(0)
        for objective in data.get("objective", []):
            self._add_row(self.objectives, [str(objective.get(c, "")) for c in OBJECTIVE_COLUMNS])
        for channel in data.get("channel", []):
            match = channel.get("match", {})
            kind = next(iter(match), "index")
            row = []
            for column in CHANNEL_COLUMNS:
                if column == "match by":
                    row.append(kind)
                elif column == "match value":
                    row.append(str(match.get(kind, "")))
                else:
                    row.append(str(channel.get(column, "")))
            self._add_row(self.channels, row)
        self._say(
            "Hand-written tracking rules are kept." if self._kept_tracking else "", error=False
        )

    # ------------------------------------------------------------ data out

    def _text(self, key: str) -> str:
        return str(self.fields[key].text()).strip()

    def to_data(self) -> dict[str, Any]:
        """Return the profile as TOML-ready data.

        Raises
        ------
        EditorError
            If a number field holds text that is not a number.
        """
        instrument: dict[str, Any] = {"id": self._text("id")}
        for key in ("name", "resolution_enhancement"):
            if self._text(key):
                instrument[key] = self._text(key)
        if self.modality.currentData():
            instrument["modality"] = self.modality.currentData()
        if self._text("pinhole_um"):
            instrument["pinhole_um"] = number(self._text("pinhole_um"), "pinhole")
        data: dict[str, Any] = {"instrument": instrument}
        bead: dict[str, Any] = {}
        if self._text("diameter_nm"):
            bead["diameter_nm"] = number(self._text("diameter_nm"), "bead diameter")
        if self._text("product"):
            bead["product"] = self._text("product")
        if bead:
            data["bead"] = bead

        objectives = []
        for row in range(self.objectives.rowCount()):
            entry: dict[str, Any] = {}
            for column, key in enumerate(OBJECTIVE_COLUMNS):
                text = _cell(self.objectives, row, column)
                if text:
                    entry[key] = (
                        text if key in TEXT_KEYS else number(text, f"objective {row + 1}: {key}")
                    )
            if entry:
                objectives.append(entry)
        if objectives:
            data["objective"] = objectives

        channels = []
        for row in range(self.channels.rowCount()):
            entry = {}
            kind, match_value = "index", ""
            for column, key in enumerate(CHANNEL_COLUMNS):
                if key == "match by":
                    widget = self.channels.cellWidget(row, column)
                    kind = widget.currentText() if isinstance(widget, QComboBox) else "index"
                    continue
                text = _cell(self.channels, row, column)
                if key == "match value":
                    match_value = text
                elif text:
                    entry[key] = (
                        text if key in TEXT_KEYS else number(text, f"channel {row + 1}: {key}")
                    )
            if match_value:
                entry["match"] = {
                    kind: match_value
                    if kind == "name"
                    else number(match_value, f"channel {row + 1}: match")
                }
            if entry:
                channels.append(entry)
        if channels:
            data["channel"] = channels

        spec = {key: number(self._text(key), key) for key in SPEC_FIELDS if self._text(key)}
        if spec:
            data["spec"] = spec
        tracking: dict[str, Any] = {}
        if self._text("baseline_sessions"):
            tracking["baseline_sessions"] = number(
                self._text("baseline_sessions"), "baseline sessions"
            )
        if self._text("metrics"):
            tracking["metrics"] = [
                m.strip() for m in self._text("metrics").split(",") if m.strip()
            ]
        tracking.update(self._kept_tracking)
        if tracking:
            data["tracking"] = tracking
        return data

    def check(self) -> bool:
        """Validate the profile and show the result; return whether it is valid."""
        try:
            data = self.to_data()
            profile = parse_profile(tomllib.loads(dumps(data)), where="profile")
        except (EditorError, ProfileError, ValueError, TypeError) as exc:
            self._say(str(exc), error=True)
            return False
        self._say(
            f"Valid: {profile.instrument_id} with {len(profile.objectives)} objective(s) and "
            f"{len(profile.channels)} channel(s)."
        )
        return True

    def save(self, path: str | Path) -> Path | None:
        """Save the profile if it is valid; return the path, or ``None`` if not saved."""
        if not self.check():
            return None
        target = Path(path)
        target.write_text(dumps(self.to_data()), encoding="utf-8")
        self.path = target
        self.path_label.setText(str(target))
        self._say(f"Saved {target}.")
        return target

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open profile", "", "TOML (*.toml)")
        if path:
            try:
                self.load(path)
            except ProfileError as exc:
                self._say(str(exc), error=True)

    def _save_as(self) -> None:
        start = "" if self.path is None else str(self.path)
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", start, "TOML (*.toml)")
        if path:
            self.save(path)
