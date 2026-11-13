"""Smoke test of the tracking demonstration script."""

from __future__ import annotations

from pathlib import Path

import pytest

import demo_tracking
from psfwidget.tracking.store import ResultsDatabase


def test_demo_builds_database_and_charts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_tracking, "SESSIONS", 6)
    assert demo_tracking.main([str(tmp_path / "demo")]) == 0
    with ResultsDatabase(tmp_path / "demo" / "demo.sqlite") as db:
        (configuration,) = db.configurations()
        assert db.session_count(configuration) == 6
        assert len(db.events()) == 2
    assert (tmp_path / "demo" / "levey_jennings_fwhm_z_um.png").exists()
    with pytest.raises(FileExistsError, match="choose an empty folder"):
        demo_tracking.build(tmp_path / "demo")
