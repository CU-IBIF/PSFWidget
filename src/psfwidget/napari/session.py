"""The state behind the analysis widget, without any Qt (UI-01, DET-07, UI-05).

Everything the widget does goes through :class:`AnalysisSession`, so the
same steps can be scripted and tested without a display:

>>> session = AnalysisSession()
>>> session.open("beads.lif", series=2, profile="sp8.toml")      # doctest: +SKIP
>>> session.set_value("numerical_aperture", "1.4")              # doctest: +SKIP
>>> result = session.analyze()                                  # doctest: +SKIP
>>> session.decide(0, 7, accept=False, reason="dust", reviewer="ab")  # doctest: +SKIP
>>> session.write_report("reports/", {"html"})                  # doctest: +SKIP
"""

from __future__ import annotations

import getpass
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from psfwidget.analysis import ChannelResult, FieldResult, analyze_stack
from psfwidget.io import (
    ImageStack,
    SeriesInfo,
    list_series,
    parse_assignments,
    read_stack,
)
from psfwidget.io.model import ACQUISITION_FIELDS, CHANNEL_FIELDS
from psfwidget.profiles import InstrumentProfile, load_profile
from psfwidget.review import Decision, review
from psfwidget.serialize import metadata_record, plain
from psfwidget.settings import AnalysisSettings

CHANNEL_COLOURS = ("green", "magenta", "cyan", "yellow", "red", "blue")
SUMMARY_METRICS = (
    ("fwhm_x_um", "FWHM x (µm)", "ratio_x"),
    ("fwhm_y_um", "FWHM y (µm)", "ratio_y"),
    ("fwhm_z_um", "FWHM z (µm)", "ratio_z"),
    ("ellipticity_xy", "Ellipticity xy", None),
    ("integrated_intensity", "Integrated intensity", None),
    ("sbr", "SBR", None),
    ("snr", "SNR", None),
)


def default_user() -> str:
    """Return the login name, or an empty string if it cannot be found."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ""


@dataclass(frozen=True)
class MetadataRow:
    """One line of the metadata table."""

    key: str
    """Override key, e.g. ``numerical_aperture`` or ``channel.0.emission_nm``."""
    label: str
    value: str
    source: str
    """``file``, ``profile``, ``user``, or empty when missing."""
    note: str
    required: bool


@dataclass(frozen=True)
class ImageLayer:
    """What napari needs to show one channel."""

    name: str
    data: NDArray[Any]
    scale: tuple[float, float, float]
    colormap: str
    contrast_limits: tuple[float, float]


@dataclass(frozen=True)
class BeadPoints:
    """Bead centres of one channel as a napari points layer, in µm."""

    name: str
    coordinates: NDArray[np.float64]
    features: dict[str, list[Any]]
    accepted: NDArray[np.bool_]


def _text(value: Any) -> str:
    value = plain(value)
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        return "-".join(_text(v) for v in value)
    return str(value)


class AnalysisSession:
    """A file being analysed: its metadata, overrides, result, and review decisions."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.series: list[SeriesInfo] = []
        self.series_index = 0
        self.profile: InstrumentProfile | None = None
        self.profile_path: Path | None = None
        self.objective_id: str | None = None
        self.assignments: dict[str, str] = {}
        self.stack: ImageStack | None = None
        self.settings = AnalysisSettings()
        self.automatic: FieldResult | None = None
        """The result before review."""
        self.decisions: dict[int, dict[int, Decision]] = {}
        self.result: FieldResult | None = None
        """The result after review; what is reported and recorded."""
        self.session_id: int | None = None

    # ------------------------------------------------------------ loading

    def open(
        self,
        path: str | Path,
        *,
        series: int = 0,
        profile: str | Path | InstrumentProfile | None = None,
        objective_id: str | None = None,
        compute_hash: bool = True,
    ) -> ImageStack:
        """Open a file (and optionally a profile), keeping any overrides already set."""
        self.path = Path(path)
        self.series = list_series(self.path)
        self.series_index = series
        if isinstance(profile, InstrumentProfile):
            self.profile, self.profile_path = profile, None
        elif profile is not None:
            self.profile_path = Path(profile)
            self.profile = load_profile(self.profile_path)
        self.objective_id = objective_id
        return self.reload(compute_hash=compute_hash)

    def set_profile(self, profile: str | Path | None) -> None:
        """Use (or, with ``None``, stop using) an instrument profile."""
        self.profile_path = None if profile is None else Path(profile)
        self.profile = None if profile is None else load_profile(profile)
        if self.path is not None:
            self.reload(compute_hash=False)

    def reload(self, *, compute_hash: bool = False) -> ImageStack:
        """Read the stack again with the current series, profile, and overrides."""
        if self.path is None:
            raise ValueError("no file is open")
        hashed = None if self.stack is None else self.stack.provenance.sha256
        overrides = (
            parse_assignments([f"{k}={v}" for k, v in self.assignments.items()])
            if self.assignments
            else None
        )
        stack = read_stack(
            self.path,
            series=self.series_index,
            profile=self.profile,
            objective_id=self.objective_id,
            overrides=overrides,
            compute_hash=compute_hash,
        )
        if (
            not compute_hash
            and hashed is not None
            and self.stack is not None
            and (
                self.stack.provenance.path == stack.provenance.path
                and self.stack.provenance.series_index == stack.provenance.series_index
            )
        ):
            from dataclasses import replace

            stack = replace(stack, provenance=replace(stack.provenance, sha256=hashed))
        self.stack = stack
        self.automatic = self.result = None
        self.decisions = {}
        self.session_id = None
        return stack

    # ------------------------------------------------------------ metadata

    def metadata_rows(self) -> list[MetadataRow]:
        """Return every metadata value with its origin, acquisition values first."""
        if self.stack is None:
            return []
        from psfwidget.io.model import REQUIRED_ACQUISITION_FIELDS

        record = metadata_record(self.stack.metadata)
        rows = []
        for name in ACQUISITION_FIELDS:
            entry = record["values"][name]
            rows.append(
                MetadataRow(
                    key=name,
                    label=name.replace("_", " "),
                    value="" if entry is None else _text(entry["value"]),
                    source="" if entry is None else entry["source"],
                    note="" if entry is None else entry["note"],
                    required=name in REQUIRED_ACQUISITION_FIELDS,
                )
            )
        for channel in record["channels"]:
            for name in CHANNEL_FIELDS:
                entry = channel[name]
                rows.append(
                    MetadataRow(
                        key=f"channel.{channel['index']}.{name}",
                        label=f"ch {channel['index']}: {name.replace('_', ' ')}",
                        value="" if entry is None else _text(entry["value"]),
                        source="" if entry is None else entry["source"],
                        note="" if entry is None else entry["note"],
                        required=name == "emission_nm",
                    )
                )
        return rows

    def set_value(self, key: str, text: str) -> None:
        """Override a metadata value; an empty ``text`` removes the override.

        Raises
        ------
        psfwidget.io.InvalidMetadataError
            If the key is unknown or the value is invalid.
        """
        text = text.strip()
        if not text:
            self.assignments.pop(key, None)
        else:
            parse_assignments([f"{key}={text}"])  # validate before keeping it
            self.assignments[key] = text
        if self.path is not None:
            self.reload(compute_hash=False)

    def problems(self) -> list[str]:
        """Return why the stack cannot be analysed yet (empty when it can)."""
        return ["no file is open"] if self.stack is None else self.stack.problems()

    # ------------------------------------------------------------ analysis

    def analyze(self, channels: Sequence[int] | None = None) -> FieldResult:
        """Analyse the stack with the current settings and the profile's spec limits."""
        if self.stack is None:
            raise ValueError("no file is open")
        spec = self.profile.spec if self.profile is not None else None
        self.automatic = analyze_stack(self.stack, self.settings, spec, channels=channels)
        self.decisions = {}
        self.result = self.automatic
        self.session_id = None
        return self.result

    def decide(
        self, channel: int, bead_id: int, *, accept: bool, reason: str, reviewer: str
    ) -> FieldResult:
        """Include or exclude a bead by hand and recompute the summary (DET-07).

        Raises
        ------
        psfwidget.review.ReviewError
            If the decision is invalid.
        """
        if self.automatic is None:
            raise ValueError("analyse the stack first")
        decisions = {c: dict(d) for c, d in self.decisions.items()}
        decisions.setdefault(channel, {})[bead_id] = Decision(accept, reason, reviewer)
        self.result = review(self.automatic, decisions)
        self.decisions = decisions
        self.session_id = None
        return self.result

    def undo_decisions(self) -> None:
        """Return to the automatic result."""
        self.decisions = {}
        self.result = self.automatic
        self.session_id = None

    # ------------------------------------------------------------ display

    def image_layers(self) -> list[ImageLayer]:
        """Return one image layer per channel, at physical scale."""
        if self.stack is None:
            return []
        layers = []
        stem = self.stack.provenance.path.stem
        for index in range(self.stack.n_channels):
            data = self.stack.channel(index)
            low, high = (
                float(v) for v in np.percentile(data[:: max(1, data.shape[0] // 8)], [0.5, 99.95])
            )
            name = (
                self.stack.metadata.channels[index].name
                if index < len(self.stack.metadata.channels)
                else None
            )
            label = name.value if name is not None else f"channel {index}"
            layers.append(
                ImageLayer(
                    name=f"{stem} [{label}]",
                    data=data,
                    scale=self.stack.voxel_size_um,
                    colormap=CHANNEL_COLOURS[index % len(CHANNEL_COLOURS)]
                    if self.stack.n_channels > 1
                    else "gray",
                    contrast_limits=(low, high if high > low else low + 1),
                )
            )
        return layers

    def bead_points(self) -> list[BeadPoints]:
        """Return the bead centres of each analysed channel, with their measurements."""
        if self.result is None or self.stack is None:
            return []
        vz, vy, vx = self.stack.voxel_size_um
        points = []
        for channel in self.result.channels:
            beads = channel.beads
            coordinates = np.array(
                [
                    (
                        b.centre_z_um if b.centre_z_um is not None else b.z_px * vz,
                        b.centre_y_um if b.centre_y_um is not None else b.y_px * vy,
                        b.centre_x_um if b.centre_x_um is not None else b.x_px * vx,
                    )
                    for b in beads
                ],
                dtype=np.float64,
            ).reshape(-1, 3)
            features: dict[str, list[Any]] = {
                "bead_id": [b.bead_id for b in beads],
                "status": [b.status for b in beads],
                "fwhm_x_um": [b.fwhm_x_um for b in beads],
                "fwhm_y_um": [b.fwhm_y_um for b in beads],
                "fwhm_z_um": [b.fwhm_z_um for b in beads],
                "snr": [b.snr for b in beads],
                "reasons": ["; ".join(b.reasons) for b in beads],
            }
            label = channel.name or f"channel {channel.index}"
            points.append(
                BeadPoints(
                    name=f"bead review [{label}]",
                    coordinates=coordinates,
                    features=features,
                    accepted=np.array([b.accepted for b in beads], dtype=bool),
                )
            )
        return points

    def channel(self, index: int) -> ChannelResult:
        """Return the reviewed result of one analysed channel."""
        if self.result is None:
            raise ValueError("analyse the stack first")
        for channel in self.result.channels:
            if channel.index == index:
                return channel
        raise IndexError(f"channel {index} was not analysed")

    def summary_rows(self, index: int) -> list[tuple[str, str, str, str, str]]:
        """Return (metric, median, SD, n, ratio to theory) rows for one channel."""
        channel = self.channel(index)
        rows = []
        for key, label, ratio_key in SUMMARY_METRICS:
            stats = channel.summary.get(key)
            ratio = channel.summary.get(ratio_key) if ratio_key else None
            rows.append(
                (
                    label,
                    "-" if stats is None else f"{stats.median:.4g}",
                    "-" if stats is None else f"{stats.sd:.2g}",
                    "0" if stats is None else str(stats.n),
                    "-" if ratio is None else f"{ratio.median:.2f}",
                )
            )
        return rows

    # ------------------------------------------------------------ outputs

    def record(self, database: str | Path, user: str) -> int:
        """Record the reviewed result in a results database; return the session id.

        Raises
        ------
        ValueError
            If there is no result, the file was not hashed, or no instrument is known.
        """
        from psfwidget.tracking.store import ResultsDatabase

        result = self._require_result()
        if result.provenance.sha256 is None:
            raise ValueError("the file has no hash; open it again with hashing on")
        if result.metadata.instrument_id is None:
            raise ValueError("choose an instrument profile (or set instrument_id) to record")
        with ResultsDatabase(database) as db:
            self.session_id = db.record(result, user=user or None)
        return self.session_id

    def write_report(
        self,
        folder: str | Path,
        formats: Collection[str],
        *,
        database: str | Path | None = None,
        operator: str | None = None,
        page_size: str = "letter",
    ) -> list[Path]:
        """Write the session report (and, with ``database``, its tracking checks)."""
        from psfwidget.automation.analyze import output_stem
        from psfwidget.report.publish import write_report
        from psfwidget.report.session import build_session_report
        from psfwidget.report.status import session_checks
        from psfwidget.tracking.settings import TrackingSettings
        from psfwidget.tracking.store import ResultsDatabase

        result = self._require_result()
        checks = None
        if database is not None and self.session_id is not None:
            settings = self.profile.tracking if self.profile is not None else TrackingSettings()
            with ResultsDatabase(database) as db:
                checks = session_checks(db, result, self.session_id, settings)
        report, _ = build_session_report(
            result,
            stack=self.stack,
            checks=checks,
            session_id=self.session_id,
            operator=operator,
        )
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        return write_report(
            report, folder / f"{output_stem(result)}_report", frozenset(formats), page_size
        )

    def _require_result(self) -> FieldResult:
        if self.result is None:
            raise ValueError("analyse the stack first")
        return self.result
