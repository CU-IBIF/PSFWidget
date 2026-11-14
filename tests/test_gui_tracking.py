"""GUI tests for the tracking dock widget (UI-02, TRK-06, TRK-07).

Skipped when napari and a Qt binding are not installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("napari")
pytest.importorskip("pytestqt")

import demo_tracking
from psfwidget.napari.tracking_widget import (
    TrackingWidget,
    configuration_states,
    nearest_point,
)
from psfwidget.tracking.settings import TrackingSettings
from psfwidget.tracking.store import ResultsDatabase


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return demo_tracking.build(tmp_path_factory.mktemp("demo"))


@pytest.mark.req("UI-02")
def test_configuration_states(database: Path, tmp_path: Path) -> None:
    copy = tmp_path / "qc.sqlite"
    copy.write_bytes(database.read_bytes())
    states = configuration_states(copy, TrackingSettings())
    assert len(states) == 1
    assert states[0].sessions > 20


@pytest.mark.req("UI-02", "TRK-06", "TRK-07")
def test_tracking_widget(qtbot: Any, database: Path, tmp_path: Path) -> None:
    copy = tmp_path / "qc.sqlite"
    copy.write_bytes(database.read_bytes())
    widget = TrackingWidget()
    qtbot.addWidget(widget)
    widget.database_edit.setText(str(copy))
    widget.load()
    assert widget.configuration_list.count() == 1
    assert widget.metric_combo.count() == len(TrackingSettings().metrics)
    assert widget.points  # chart drawn
    assert widget._axes is not None

    # Hovering and clicking find the session under the cursor.
    widget.canvas.draw()  # type: ignore[no-untyped-call]
    target = widget.points[3]
    from matplotlib.dates import date2num

    x, y = widget._axes.transData.transform(
        (date2num(target.session.acquired_utc), target.session.value)  # type: ignore[no-untyped-call]
    )
    assert nearest_point(widget._axes, widget.points, x + 2, y - 2) is target
    assert nearest_point(widget._axes, widget.points, x + 500, y + 500) is None
    widget.select(target)
    assert f"Session {target.session.session_id}" in widget.info_label.text()

    # Excluding needs a reason; then it is recorded with the user.
    widget.set_excluded(True)
    assert "reason" in widget.message_label.text()
    widget.reason_edit.setText("dirty slide")
    widget.user_edit.setText("ab")
    widget.select(target)
    widget.set_excluded(True)
    assert f"Excluded session {target.session.session_id}" in widget.message_label.text()
    with ResultsDatabase(copy) as db:
        assert any(
            entry["action"].startswith("exclu") and entry["user"] == "ab"
            for entry in db.audit_log()
        )
    excluded = next(p for p in widget.points if p.session.session_id == target.session.session_id)
    assert excluded.session.excluded
    widget.select(excluded)
    assert widget.include_button.isEnabled()
    assert not widget.exclude_button.isEnabled()

    widget.reason_edit.setText("laser realigned")
    widget.rebaseline_box.setChecked(True)
    events = len(widget.series.events) if widget.series else 0
    widget.add_event()
    assert "Event recorded" in widget.message_label.text()
    assert widget.series is not None
    assert len(widget.series.events) == events + 1

    widget.load()
    widget.database_edit.setText(str(tmp_path / "missing.sqlite"))
    widget.load()
    assert "No database" in widget.message_label.text()
