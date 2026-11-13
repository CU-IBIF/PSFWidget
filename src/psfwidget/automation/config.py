"""The automation configuration file (AUT-05).

A TOML file that can be kept under version control. Relative paths are
relative to the file itself. Example::

    [database]
    path = "qc.sqlite"

    [output]
    root = "reports"
    session_template = "{instrument}/{yyyy}/{date}_{file_stem}_s{series}"
    trend_template = "{instrument}/trend/{date}_{instrument}_trend"
    reports = ["html", "pdf"]
    exports = ["csv", "xlsx"]
    page_size = "letter"            # or "a4"

    [branding]
    facility = "Light Microscopy Core"
    logo = "logo.png"
    footer = "Internal quality control"

    [[watch]]
    folder = "//server/qc/sp8"      # watched folder
    profile = "profiles/sp8.toml"   # instrument profile for files found here
    patterns = ["*.lif"]
    recursive = true
    series = "all"                  # or a list of indices, e.g. [0, 2]
    settle_seconds = 120            # file must be unchanged this long

    [schedule]
    ingest = "every 15 minutes"
    trend_report = "weekly monday 07:30"
    trend_days = 90

    [analysis]                      # optional analysis setting changes
    min_snr = 8

    [alerts]
    status_file = "STATUS.json"     # in the output root
    alert_file = "ALERT.txt"
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from psfwidget.automation.schedule import Schedule, ScheduleError, parse_schedule
from psfwidget.profiles import InstrumentProfile, ProfileError, load_profile
from psfwidget.report.document import Branding
from psfwidget.report.paths import TemplateError
from psfwidget.report.publish import (
    DEFAULT_SESSION_TEMPLATE,
    DEFAULT_TREND_TEMPLATE,
    EXPORT_FORMATS,
    REPORT_FORMATS,
    OutputSettings,
)
from psfwidget.settings import AnalysisSettings

_SECTIONS = {
    "database": {"path"},
    "output": {"root", "session_template", "trend_template", "reports", "exports", "page_size"},
    "branding": {"facility", "logo", "footer"},
    "schedule": {"ingest", "trend_report", "trend_days"},
    "alerts": {"status_file", "alert_file"},
}
_WATCH_KEYS = {"folder", "profile", "patterns", "recursive", "series", "settle_seconds"}
_TOP = {*_SECTIONS, "watch", "analysis"}


class ConfigError(ValueError):
    """Raised when the configuration file is invalid."""


@dataclass(frozen=True)
class WatchFolder:
    """A folder whose new files are analysed with one instrument profile (AUT-02)."""

    folder: Path
    profile_path: Path
    profile: InstrumentProfile
    patterns: tuple[str, ...]
    recursive: bool
    series: tuple[int, ...] | None
    """``None`` means every series in each file."""
    settle_seconds: float


@dataclass(frozen=True)
class AutomationConfig:
    """Everything a scheduled run needs."""

    path: Path
    database: Path
    output: OutputSettings
    watches: tuple[WatchFolder, ...]
    ingest: Schedule
    trend_report: Schedule
    trend_days: int
    analysis: AnalysisSettings
    status_file: Path
    alert_file: Path
    state_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_dir", self.output.root / "_psfwidget")

    def profiles_by_instrument(self) -> dict[str, InstrumentProfile]:
        """Return the watched instruments' profiles, keyed by instrument id."""
        return {w.profile.instrument_id: w.profile for w in self.watches}


def _table(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = sorted(set(value) - _SECTIONS[name])
    if unknown:
        raise ConfigError(
            f"[{name}]: unknown key(s) {unknown}; allowed: {sorted(_SECTIONS[name])}"
        )
    return value


def _text(table: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: '{key}' must be a non-empty string")
    return value.strip()


def _path(base: Path, text: str) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else (base / path)


def _formats(
    table: Mapping[str, Any], key: str, allowed: frozenset[str], default: set[str], where: str
) -> frozenset[str]:
    value = table.get(key, sorted(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list such as {sorted(allowed)}")
    found = {v.lower() for v in value}
    unknown = sorted(found - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown {key} {unknown}; allowed: {sorted(allowed)}")
    return frozenset(found)


def _watch(entry: Any, number: int, base: Path) -> WatchFolder:
    where = f"[[watch]] {number}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")
    unknown = sorted(set(entry) - _WATCH_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(_WATCH_KEYS)}")
    folder = _path(base, _text(entry, "folder", where))
    profile_path = _path(base, _text(entry, "profile", where))
    try:
        profile = load_profile(profile_path)
    except FileNotFoundError:
        raise ConfigError(f"{where}: profile {profile_path} not found") from None
    except ProfileError as exc:
        raise ConfigError(f"{where}: {exc}") from None
    patterns = entry.get("patterns", ["*.nd2", "*.lif", "*.czi", "*.tif", "*.tiff"])
    if (
        not isinstance(patterns, list)
        or not patterns
        or not all(isinstance(p, str) and p.strip() for p in patterns)
    ):
        raise ConfigError(f"{where}: 'patterns' must be a non-empty list such as ['*.nd2']")
    recursive = entry.get("recursive", True)
    if not isinstance(recursive, bool):
        raise ConfigError(f"{where}: 'recursive' must be true or false")
    series_value = entry.get("series", "all")
    series: tuple[int, ...] | None
    if series_value == "all":
        series = None
    elif (
        isinstance(series_value, list)
        and series_value
        and all(isinstance(s, int) and not isinstance(s, bool) and s >= 0 for s in series_value)
    ):
        series = tuple(dict.fromkeys(series_value))
    else:
        raise ConfigError(f"{where}: 'series' must be \"all\" or a list of indices like [0, 2]")
    settle = entry.get("settle_seconds", 120)
    if isinstance(settle, bool) or not isinstance(settle, int | float) or settle < 0:
        raise ConfigError(f"{where}: 'settle_seconds' must be a number >= 0")
    return WatchFolder(
        folder,
        profile_path,
        profile,
        tuple(p.strip() for p in patterns),
        recursive,
        series,
        float(settle),
    )


def parse_config(data: Mapping[str, Any], path: Path) -> AutomationConfig:
    """Build and validate a configuration from parsed TOML.

    Raises
    ------
    ConfigError
        If anything is missing, unknown, or invalid.
    """
    unknown = sorted(set(data) - _TOP)
    if unknown:
        raise ConfigError(f"unknown table(s) {unknown}; allowed: {sorted(_TOP)}")
    base = path.parent
    database = _table(data, "database")
    output = _table(data, "output")
    branding = _table(data, "branding")
    schedule = _table(data, "schedule")
    alerts = _table(data, "alerts")

    root = _path(base, _text(output, "root", "[output]", "reports"))
    logo = None
    if "logo" in branding:
        logo = _path(base, _text(branding, "logo", "[branding]"))
        if not logo.is_file():
            raise ConfigError(f"[branding]: logo {logo} not found")
    page_size = _text(output, "page_size", "[output]", "letter").lower()
    if page_size not in ("letter", "a4"):
        raise ConfigError('[output]: \'page_size\' must be "letter" or "a4"')
    try:
        settings = OutputSettings(
            root=root,
            session_template=_text(
                output, "session_template", "[output]", DEFAULT_SESSION_TEMPLATE
            ),
            trend_template=_text(output, "trend_template", "[output]", DEFAULT_TREND_TEMPLATE),
            reports=_formats(output, "reports", REPORT_FORMATS, {"html", "pdf"}, "[output]"),
            exports=_formats(output, "exports", EXPORT_FORMATS, {"csv"}, "[output]"),
            page_size=page_size,
            branding=Branding(
                facility=str(branding.get("facility", "")),
                logo=logo,
                footer=str(branding.get("footer", "")),
            ),
        )
    except TemplateError as exc:
        raise ConfigError(f"[output]: {exc}") from None

    watches_raw = data.get("watch", [])
    if not isinstance(watches_raw, list):
        raise ConfigError("use [[watch]] tables for watched folders")
    watches = tuple(_watch(entry, number, base) for number, entry in enumerate(watches_raw, 1))

    try:
        ingest = parse_schedule(str(schedule.get("ingest", "every 15 minutes")))
        trend = parse_schedule(str(schedule.get("trend_report", "off")))
    except ScheduleError as exc:
        raise ConfigError(f"[schedule]: {exc}") from None
    trend_days = schedule.get("trend_days", 90)
    if isinstance(trend_days, bool) or not isinstance(trend_days, int) or trend_days < 1:
        raise ConfigError("[schedule]: 'trend_days' must be a whole number >= 1")

    analysis_raw = data.get("analysis", {})
    if not isinstance(analysis_raw, dict):
        raise ConfigError("[analysis] must be a table")
    defaults = AnalysisSettings().to_dict()
    for key, value in analysis_raw.items():
        if key not in defaults:
            raise ConfigError(f"[analysis]: unknown setting {key!r}")
        current = defaults[key]
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if (isinstance(current, bool) and not isinstance(value, bool)) or (
            not isinstance(current, bool) and current is not None and not numeric
        ):
            raise ConfigError(f"[analysis]: {key} must be like {current!r}, got {value!r}")
    try:
        analysis = AnalysisSettings.from_dict(defaults | analysis_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[analysis]: {exc}") from None

    return AutomationConfig(
        path=path,
        database=_path(base, _text(database, "path", "[database]", "qc.sqlite")),
        output=settings,
        watches=watches,
        ingest=ingest,
        trend_report=trend,
        trend_days=trend_days,
        analysis=analysis,
        status_file=root / _text(alerts, "status_file", "[alerts]", "STATUS.json"),
        alert_file=root / _text(alerts, "alert_file", "[alerts]", "ALERT.txt"),
    )


def load_config(path: str | Path) -> AutomationConfig:
    """Load and validate a configuration file.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ConfigError
        If it is not valid TOML or not a valid configuration.
    """
    path = Path(path).resolve()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: not valid TOML: {exc}") from exc
    try:
        return parse_config(data, path)
    except ConfigError as exc:
        raise ConfigError(f"{path.name}: {exc}") from None
