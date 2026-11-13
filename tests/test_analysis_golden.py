"""End-to-end analysis of the facility bead files (VAL-04 evidence, local only).

These files are not redistributable, so the tests run only where they are in
tests/data/cache. They check that every bead channel is measured and that
the results are physically plausible, not exact values.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from psfwidget.analysis import analyze_stack
from psfwidget.io import read_stack
from psfwidget.profiles import load_profile

PROFILES = Path(__file__).resolve().parents[1] / "examples" / "profiles"

# File, profile, series, channels expected to give a measurement (DAPI on the
# LSM 980 and the dim 405 channel on the AX R carry almost no signal).
CASES = [
    ("20261111_QC_980.czi", "lsm980", 0, (0, 1, 3)),
    ("20261106_QC_SP8.lif", "sp8", 0, (0, 1)),
    ("20261106_QC_SP8.lif", "sp8", 3, (0, 1)),
    ("20261012_QC_AxR_NSPARC_crop.nd2", "axr-nsparc", 0, (1, 2, 3)),
    ("20261111_psfbeads_SPD_1x_SR_crop.nd2", "csu-livesr", 0, (0, 1, 2)),
    ("20261111_psfbeads_SPD_1x_SR_crop.ome.tif", "csu-livesr", 0, (0, 1, 2)),
]


@pytest.mark.req("DET-01", "MET-01", "THE-03", "THE-08")
@pytest.mark.parametrize(
    ("name", "profile", "series", "measured"), CASES, ids=[f"{c[0]}-s{c[2]}" for c in CASES]
)
def test_facility_files_are_measured(
    name: str,
    profile: str,
    series: int,
    measured: tuple[int, ...],
    golden_file: Callable[[str], Path],
) -> None:
    stack = read_stack(
        golden_file(name),
        series=series,
        profile=load_profile(PROFILES / f"{profile}.toml"),
        compute_hash=False,
    )
    result = analyze_stack(stack)
    enhanced = stack.metadata.resolution_enhancement is not None
    for channel in result.channels:
        if channel.index not in measured:
            continue
        assert channel.accepted, f"channel {channel.index}: {channel.rejections}"
        assert channel.theory is not None
        stats = channel.summary
        assert 0.08 < stats["fwhm_x_um"].median < 0.5
        assert 0.08 < stats["fwhm_y_um"].median < 0.5
        assert 0.25 < stats["fwhm_z_um"].median < 2.0
        assert stats["fwhm_z_um"].median > stats["fwhm_x_um"].median
        low = 0.5 if enhanced else 0.85  # THE-08: enhanced modes may beat the base model
        assert low < stats["ratio_x"].median < 2.0
        assert stats["snr"].median >= 10


def test_sp8_axial_aberration_is_detected(golden_file: Callable[[str], Path]) -> None:
    """The SP8 beads are about three times longer axially than theory (spherical aberration)."""
    stack = read_stack(
        golden_file("20261106_QC_SP8.lif"),
        profile=load_profile(PROFILES / "sp8.toml"),
        compute_hash=False,
    )
    for channel in analyze_stack(stack).channels:
        assert channel.summary["ratio_z"].median > 2.5
        assert any(
            "measurement box was sized from the measured FWHM" in w for w in channel.warnings
        )


@pytest.mark.req("NFR-03")
def test_facility_reanalysis_is_identical(golden_file: Callable[[str], Path]) -> None:
    stack = read_stack(
        golden_file("20261111_QC_980.czi"),
        profile=load_profile(PROFILES / "lsm980.toml"),
        compute_hash=False,
    )
    first, second = analyze_stack(stack, channels=[0]), analyze_stack(stack, channels=[0])
    assert first.channels[0].beads == second.channels[0].beads
