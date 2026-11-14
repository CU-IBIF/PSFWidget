"""The tracking dock widget: interactive Levey-Jennings charts (UI-02, TRK-03, TRK-06, TRK-07).

Choose a results database, a configuration (instrument / objective /
channel / modality), and a metric. Hovering over a point shows the session;
clicking selects it so it can be excluded or included again with a reason.
Events (service, alignment, …) can be added, optionally starting new control
limits. Every change goes through the append-only database and its audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from psfwidget.napari.session import default_user
from psfwidget.tracking.chart import ChartError, metric_label, render
from psfwidget.tracking.control import ControlPoint, ControlSeries, series_from_database
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import Configuration, EventKind, ResultsDatabase

SEVERITY_TEXT = {"rejection": "FAIL", "warning": "WARNING", "ok": "OK", "none": "no limits yet"}
SEVERITY_ORDER = {"rejection": 3, "warning": 2, "ok": 1, "none": 0}
HOVER_PIXELS = 8.0


@dataclass(frozen=True)
class ConfigurationState:
    """A configuration with the worst severity of its latest session."""

    configuration: Configuration
    sessions: int
    severity: str


def configuration_states(
    database: str | Path, settings: TrackingSettings
) -> list[ConfigurationState]:
    """Return every configuration in a database with its latest state."""
    states = []
    with ResultsDatabase(database) as db:
        for configuration in db.configurations():
            worst = "none"
            for metric in settings.metrics:
                latest = series_from_database(db, configuration, metric, settings).latest
                if latest is not None and SEVERITY_ORDER[latest.severity] > SEVERITY_ORDER[worst]:
                    worst = latest.severity
            states.append(
                ConfigurationState(configuration, db.session_count(configuration), worst)
            )
    return states


def _rules(point: ControlPoint) -> str:
    return f": {', '.join(sorted(r.value for r in point.violations))}" if point.violations else ""


def describe_point(point: ControlPoint, metric: str) -> str:
    """Return a few lines describing one session on a chart."""
    session = point.session
    lines = [
        f"Session {session.session_id}, channel {session.channel_index}: {session.file_name}",
        f"{session.acquired_utc:%Y-%m-%d %H:%M} UTC ({session.date_source} date)",
        f"{metric_label(metric)}: {session.value:.4g}"
        + ("" if point.z is None else f"  (z {point.z:+.2f})"),
        f"{session.n_beads} beads; {SEVERITY_TEXT[point.severity]}"
        + (
            f": {', '.join(sorted(r.value for r in point.violations))}" if point.violations else ""
        ),
    ]
    if session.excluded:
        lines.append(f"Excluded: {session.exclusion_reason}")
    return "\n".join(lines)


def nearest_point(
    axes: Any, points: list[ControlPoint], x_pixel: float, y_pixel: float
) -> ControlPoint | None:
    """Return the plotted point within :data:`HOVER_PIXELS` of a display position."""
    from matplotlib.dates import date2num

    best, distance = None, HOVER_PIXELS
    for point in points:
        x, y = axes.transData.transform(
            (date2num(point.session.acquired_utc), point.session.value)  # type: ignore[no-untyped-call]
        )
        found = ((x - x_pixel) ** 2 + (y - y_pixel) ** 2) ** 0.5
        if found <= distance:
            best, distance = point, found
    return best


class TrackingWidget(QWidget):
    """Browse configurations and their Levey-Jennings charts."""

    def __init__(self, napari_viewer: Any = None, parent: QWidget | None = None) -> None:
        from matplotlib.backends.backend_qtagg import (  # type: ignore[attr-defined]
            FigureCanvasQTAgg,
            NavigationToolbar2QT,
        )
        from matplotlib.figure import Figure

        super().__init__(parent)
        self.viewer = napari_viewer
        self.settings = TrackingSettings()
        self.states: list[ConfigurationState] = []
        self.series: ControlSeries | None = None
        self.points: list[ControlPoint] = []
        self.selected: ControlPoint | None = None
        self._axes: Any = None

        layout = QVBoxLayout(self)
        source = QGroupBox("Results database")
        form = QFormLayout(source)
        self.database_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_database)
        form.addRow("Database", self._row(self.database_edit, browse))
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("optional profile for the tracking settings")
        profile_browse = QPushButton("Browse…")
        profile_browse.clicked.connect(self._browse_profile)
        form.addRow("Profile", self._row(self.profile_edit, profile_browse))
        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(lambda: self.load())
        form.addRow(self.load_button)
        layout.addWidget(source)

        self.configuration_list = QListWidget()
        self.configuration_list.currentRowChanged.connect(lambda _: self.show_chart())
        layout.addWidget(self.configuration_list)
        self.metric_combo = QComboBox()
        self.metric_combo.currentIndexChanged.connect(lambda _: self.show_chart())
        layout.addLayout(self._row(QLabel("Metric"), self.metric_combo))

        self.figure = Figure(figsize=(6, 3.4))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        self.canvas.setMinimumHeight(300)
        self.canvas.mpl_connect("motion_notify_event", self._hover)
        self.canvas.mpl_connect("button_press_event", self._click)
        layout.addWidget(NavigationToolbar2QT(self.canvas, self))  # type: ignore[no-untyped-call]
        layout.addWidget(self.canvas)
        self.info_label = QLabel("Hover over a point to see the session; click to select it.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        actions = QGroupBox("Selected session and events")
        action_form = QFormLayout(actions)
        self.reason_edit = QLineEdit()
        self.reason_edit.setPlaceholderText("reason or event description")
        action_form.addRow("Reason", self.reason_edit)
        self.user_edit = QLineEdit(default_user())
        action_form.addRow("User", self.user_edit)
        self.exclude_button = QPushButton("Exclude session")
        self.exclude_button.clicked.connect(lambda: self.set_excluded(True))
        self.include_button = QPushButton("Include session")
        self.include_button.clicked.connect(lambda: self.set_excluded(False))
        action_form.addRow(self._row(self.exclude_button, self.include_button))
        self.event_kind = QComboBox()
        for kind in EventKind:
            self.event_kind.addItem(kind.value, kind.value)
        self.rebaseline_box = QCheckBox("start new control limits")
        self.event_button = QPushButton("Add event now")
        self.event_button.clicked.connect(self.add_event)
        action_form.addRow(self._row(self.event_kind, self.rebaseline_box, self.event_button))
        self.save_button = QPushButton("Save chart…")
        self.save_button.clicked.connect(self._save_chart)
        action_form.addRow(self.save_button)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        action_form.addRow(self.message_label)
        layout.addWidget(actions)
        self._update_buttons()

    @staticmethod
    def _row(*widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row.addWidget(widget)
        return row

    def _say(self, text: str, error: bool = False) -> None:
        self.message_label.setStyleSheet("color: #e0331f;" if error else "")
        self.message_label.setText(text)

    def _update_buttons(self) -> None:
        loaded = bool(self.states)
        self.exclude_button.setEnabled(
            self.selected is not None and not self.selected.session.excluded
        )
        self.include_button.setEnabled(
            self.selected is not None and self.selected.session.excluded
        )
        self.event_button.setEnabled(loaded)
        self.save_button.setEnabled(self.series is not None)

    def _browse_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Results database", "", "SQLite (*.sqlite *.db);;All files (*)"
        )
        if path:
            self.database_edit.setText(path)
            self.load()

    def _browse_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Instrument profile", "", "TOML (*.toml)")
        if path:
            self.profile_edit.setText(path)

    def _database(self) -> Path:
        return Path(self.database_edit.text().strip())

    # ------------------------------------------------------------ loading

    def load(self, message: str | None = None) -> None:
        """Load the configurations in the database, then show ``message`` if given."""
        from psfwidget.profiles import load_profile

        database = self._database()
        if not database.is_file():
            self._say(f"No database at {database}.", error=True)
            return
        try:
            profile = self.profile_edit.text().strip()
            self.settings = load_profile(profile).tracking if profile else TrackingSettings()
            self.states = configuration_states(database, self.settings)
        except Exception as exc:
            self._say(str(exc), error=True)
            return
        current = self.metric_combo.currentData()
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for metric in self.settings.metrics:
            self.metric_combo.addItem(metric_label(metric), metric)
        if current in self.settings.metrics:
            self.metric_combo.setCurrentIndex(self.settings.metrics.index(current))
        self.metric_combo.blockSignals(False)
        row = max(self.configuration_list.currentRow(), 0)
        self.configuration_list.blockSignals(True)
        self.configuration_list.clear()
        for state in self.states:
            item = QListWidgetItem(
                f"{state.configuration.label}  ·  {state.sessions} sessions  ·  "
                f"{SEVERITY_TEXT[state.severity]}"
            )
            self.configuration_list.addItem(item)
        self.configuration_list.blockSignals(False)
        if self.states:
            self.configuration_list.setCurrentRow(min(row, len(self.states) - 1))
        self._say(message or f"{len(self.states)} configuration(s).")
        self.show_chart()

    def show_chart(self) -> None:
        """Draw the chart of the selected configuration and metric."""
        row = self.configuration_list.currentRow()
        metric = self.metric_combo.currentData()
        self.selected = None
        if row < 0 or row >= len(self.states) or metric is None:
            self._update_buttons()
            return
        configuration = self.states[row].configuration
        with ResultsDatabase(self._database()) as db:
            self.series = series_from_database(db, configuration, metric, self.settings)
        try:
            self._axes, self.points = render(self.series, self.figure)
        except ChartError as exc:
            self.figure.clear()
            self._axes, self.points = None, []
            self.info_label.setText(str(exc))
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]
        self._update_buttons()

    # ------------------------------------------------------------ interaction

    def _hover(self, event: Any) -> None:
        if self._axes is None or event.inaxes is not self._axes:
            return
        point = nearest_point(self._axes, self.points, event.x, event.y)
        if point is not None and self.series is not None:
            self.info_label.setText(describe_point(point, self.series.metric))

    def _click(self, event: Any) -> None:
        if self._axes is None or event.inaxes is not self._axes or self.series is None:
            return
        self.select(nearest_point(self._axes, self.points, event.x, event.y))

    def select(self, point: ControlPoint | None) -> None:
        """Select a session on the chart (``None`` clears the selection)."""
        self.selected = point
        if point is not None and self.series is not None:
            self.info_label.setText("Selected:\n" + describe_point(point, self.series.metric))
        self._update_buttons()

    def set_excluded(self, excluded: bool) -> None:
        """Exclude or include the selected session's channel, with a reason (TRK-07)."""
        point = self.selected
        reason = self.reason_edit.text().strip()
        user = self.user_edit.text().strip()
        if point is None:
            self._say("Click a point on the chart first.", error=True)
            return
        if not reason or not user:
            self._say("Give a reason and your name.", error=True)
            return
        try:
            with ResultsDatabase(self._database()) as db:
                db.set_excluded(
                    point.session.session_id,
                    excluded,
                    reason,
                    channel_index=point.session.channel_index,
                    user=user,
                )
        except Exception as exc:
            self._say(str(exc), error=True)
            return
        verb = "Excluded" if excluded else "Included"
        self.load(f"{verb} session {point.session.session_id}.")

    def add_event(self) -> None:
        """Record an event for the selected configuration's instrument now (TRK-06)."""
        row = self.configuration_list.currentRow()
        description = self.reason_edit.text().strip()
        user = self.user_edit.text().strip()
        if row < 0 or not description or not user:
            self._say("Choose a configuration and give a description and your name.", error=True)
            return
        configuration = self.states[row].configuration
        try:
            with ResultsDatabase(self._database()) as db:
                db.add_event(
                    occurred=datetime.now(UTC).replace(microsecond=0),
                    instrument_id=configuration.instrument_id,
                    kind=EventKind(self.event_kind.currentData()),
                    description=description,
                    rebaseline=self.rebaseline_box.isChecked(),
                    user=user,
                )
        except Exception as exc:
            self._say(str(exc), error=True)
            return
        self.load(f"Event recorded for {configuration.instrument_id}.")

    def _save_chart(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save chart", "", "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)"
        )
        if path:
            self.figure.savefig(path, dpi=150)
            self._say(f"Saved {path}.")
