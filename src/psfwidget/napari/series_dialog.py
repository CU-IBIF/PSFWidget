"""Dialog to choose one series of a multi-series file (IMP-02).

LIF projects, multi-scene CZI files, and multi-position ND2 files hold several
image series. When a file with more than one series is dropped onto napari or
chosen with *Browse*, this dialog lists them so the user picks the one to
analyse.
"""

from __future__ import annotations

from collections.abc import Sequence

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from psfwidget.io.model import SeriesInfo

COLUMNS = ("#", "Name", "Channels", "Z planes", "Y × X", "Type")
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def series_cells(item: SeriesInfo) -> tuple[str, ...]:
    """Return the table cells describing one series."""
    sizes = item.sizes
    extra = ", ".join(f"{k}{v}" for k, v in sizes.items() if k in ("T", "M") and v > 1)
    name = f"{item.name} ({extra})" if extra else item.name
    return (
        str(item.index),
        name,
        str(sizes.get("C", 1)),
        str(sizes.get("Z", 1)),
        f"{sizes.get('Y', 0)} × {sizes.get('X', 0)}",
        item.dtype,
    )


class SeriesDialog(QDialog):
    """List the series of a file and let the user choose one."""

    def __init__(
        self,
        file_name: str,
        series: Sequence[SeriesInfo],
        current: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a series")
        self.series = list(series)
        layout = QVBoxLayout(self)
        label = QLabel(f"{file_name} holds {len(self.series)} series. Choose one to analyse:")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.table = QTableWidget(len(self.series), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for row, item in enumerate(self.series):
            for column, text in enumerate(series_cells(item)):
                cell = QTableWidgetItem(text)
                if column != 1:
                    cell.setTextAlignment(RIGHT)
                self.table.setItem(row, column, cell)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        rows = [i for i, item in enumerate(self.series) if item.index == current]
        self.table.selectRow(rows[0] if rows else 0)
        self.table.doubleClicked.connect(self.accept)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, min(140 + 30 * len(self.series), 520))

    def chosen(self) -> int | None:
        """Return the index of the selected series, or ``None``."""
        rows = self.table.selectionModel().selectedRows()
        return self.series[rows[0].row()].index if rows else None


def choose_series(
    file_name: str,
    series: Sequence[SeriesInfo],
    current: int = 0,
    parent: QWidget | None = None,
) -> int | None:
    """Show the dialog and return the chosen series index, or ``None`` if cancelled."""
    dialog = SeriesDialog(file_name, series, current, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen()
