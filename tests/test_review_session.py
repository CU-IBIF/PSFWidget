"""Tests for manual bead review and the GUI's analysis session (DET-07, UI-01, UI-05)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from psfwidget import synthetic
from psfwidget.io import InvalidMetadataError
from psfwidget.napari.session import AnalysisSession
from psfwidget.review import MANUAL_EXCLUSION, Decision, ReviewError, review, reviewed_beads

PROFILE = (
    '[instrument]\nid = "scope"\nmodality = "widefield"\n'
    '[[objective]]\nid = "63x"\nnumerical_aperture = 1.4\nimmersion_ri = 1.518\n'
    '[[channel]]\nid = "green"\nmatch = { index = 0 }\nemission_nm = 520\n'
    "[spec]\nlateral_ratio_max = 1.3\n"
)


@pytest.fixture(scope="module")
def stack_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("review")
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
    path = folder / "beads.tif"
    tifffile.imwrite(
        path,
        field.data,
        imagej=True,
        resolution=(20, 20),
        metadata={"axes": "ZYX", "spacing": 0.15, "unit": "um"},
    )
    (folder / "scope.toml").write_text(PROFILE, encoding="utf-8")
    return path


@pytest.fixture
def session(stack_file: Path) -> AnalysisSession:
    session = AnalysisSession()
    session.open(stack_file, profile=stack_file.parent / "scope.toml")
    session.analyze()
    return session


@pytest.mark.req("DET-07")
def test_exclusion_recomputes_the_summary(session: AnalysisSession) -> None:
    automatic = session.channel(0)
    first = automatic.accepted[0]
    result = session.decide(0, first.bead_id, accept=False, reason="dust", reviewer="ab")
    channel = result.channels[0]
    assert len(channel.accepted) == len(automatic.accepted) - 1
    assert channel.summary["fwhm_x_um"].n == automatic.summary["fwhm_x_um"].n - 1
    bead = next(b for b in channel.beads if b.bead_id == first.bead_id)
    assert bead.reasons[0] == f"{MANUAL_EXCLUSION} ab: dust"
    assert channel.spec is not None
    assert reviewed_beads(result) == [(0, first.bead_id, bead.reasons[0])]
    # Include it again: back to accepted, with the history kept in the notes.
    again = session.decide(0, first.bead_id, accept=True, reason="fine after all", reviewer="ab")
    assert len(again.channels[0].accepted) == len(automatic.accepted)
    assert session.automatic is not None
    assert session.automatic.channels[0] is automatic  # the automatic result is unchanged
    session.undo_decisions()
    assert session.result is session.automatic


@pytest.mark.req("DET-07")
def test_review_errors(session: AnalysisSession) -> None:
    assert session.result is not None
    first = session.channel(0).accepted[0].bead_id
    with pytest.raises(ReviewError, match="reason"):
        session.decide(0, first, accept=False, reason=" ", reviewer="ab")
    with pytest.raises(ReviewError, match="unknown bead"):
        session.decide(0, 999, accept=False, reason="x", reviewer="ab")
    with pytest.raises(ReviewError, match="no analysed channel"):
        review(session.result, {3: {first: Decision(False, "x", "ab")}})


@pytest.mark.req("UI-01", "UI-05")
def test_session_metadata_overrides(stack_file: Path) -> None:
    session = AnalysisSession()
    session.open(stack_file)
    rows = {r.key: r for r in session.metadata_rows()}
    assert rows["numerical_aperture"].value == ""
    assert rows["numerical_aperture"].required
    assert session.problems()
    for key, value in (
        ("numerical_aperture", "1.4"),
        ("immersion_ri", "1.518"),
        ("modality", "widefield"),
        ("channel.0.emission_nm", "520"),
    ):
        session.set_value(key, value)
    rows = {r.key: r for r in session.metadata_rows()}
    assert rows["numerical_aperture"].value == "1.4"
    assert rows["numerical_aperture"].source == "user"
    assert session.problems() == []
    with pytest.raises(InvalidMetadataError):
        session.set_value("numerical_aperture", "lots")
    session.set_value("numerical_aperture", "")
    assert "numerical_aperture" not in session.assignments
    assert session.stack is not None
    assert session.stack.provenance.sha256


@pytest.mark.req("UI-01")
def test_session_layers_and_outputs(session: AnalysisSession, tmp_path: Path) -> None:
    images = session.image_layers()
    assert len(images) == 1
    assert images[0].scale == (0.15, 0.05, 0.05)
    points = session.bead_points()[0]
    assert points.coordinates.shape == (len(session.channel(0).beads), 3)
    assert set(points.features) >= {"bead_id", "status", "fwhm_x_um", "reasons"}
    assert next(label for label, *_ in session.summary_rows(0)) == "FWHM x (µm)"
    database = tmp_path / "qc.sqlite"
    session_id = session.record(database, "ab")
    files = session.write_report(tmp_path, {"html"}, database=database, operator="ab")
    assert files[0].suffix == ".html"
    assert f"Session {session_id}" in files[0].read_text("utf-8") or str(session_id) in files[
        0
    ].read_text("utf-8")
