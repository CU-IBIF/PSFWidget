"""Instrument profiles: per-microscope defaults and values no file records (IMP-10).

A profile is a TOML file::

    [instrument]
    id = "sp8"
    name = "Leica SP8"

    [bead]
    diameter_nm = 100
    product = "TetraSpeck 0.1 um"

    [[objective]]
    id = "63x-oil"
    match = "63x/1.40"          # part of the objective name stored in files
    numerical_aperture = 1.4
    immersion_ri = 1.518

    [[channel]]
    id = "green"
    match = { excitation_nm = 488 }
    emission_nm = 515           # the bead dye's emission peak

    [spec]                      # optional acceptance limits (THE-06)
    lateral_ratio_max = 1.3

Profile values take precedence over values read from the file and are in
turn overridden by values the user enters.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from psfwidget.io.metadata import (
    ChannelOverrides,
    InvalidMetadataError,
    Overrides,
    coerce_value,
)
from psfwidget.io.model import AcquisitionMetadata, ChannelMetadata, Modality
from psfwidget.profiles.spec import SpecLimits, parse_spec

EXCITATION_MATCH_TOLERANCE_NM = 5.0

_INSTRUMENT_KEYS = {"id", "name", "modality", "resolution_enhancement", "pinhole_um"}
_BEAD_KEYS = {"diameter_nm", "product"}
_OBJECTIVE_KEYS = {"id", "match", "name", "numerical_aperture", "immersion_ri", "magnification"}
_CHANNEL_KEYS = {
    "id",
    "match",
    "name",
    "excitation_nm",
    "emission_nm",
    "pinhole_airy",
    "pinhole_um",
}
_CHANNEL_MATCH_KEYS = {"name", "excitation_nm", "index"}
_TOP_LEVEL_KEYS = {"instrument", "bead", "objective", "channel", "spec"}


class ProfileError(ValueError):
    """Raised when an instrument profile is malformed."""


@dataclass(frozen=True)
class ObjectivePreset:
    """Optics of one objective, selected by part of its name."""

    id: str
    match: str | None = None
    name: str | None = None
    numerical_aperture: float | None = None
    immersion_ri: float | None = None
    magnification: float | None = None

    def matches(self, objective_name: str) -> bool:
        """Whether ``match`` occurs in ``objective_name``, ignoring case and spacing."""
        if self.match is None:
            return False
        return _squash(self.match) in _squash(objective_name)


@dataclass(frozen=True)
class ChannelPreset:
    """Values for one channel, selected by name, excitation line, or index."""

    id: str
    match_name: str | None = None
    match_excitation_nm: float | None = None
    match_index: int | None = None
    name: str | None = None
    excitation_nm: float | None = None
    emission_nm: float | None = None
    pinhole_airy: float | None = None
    pinhole_um: float | None = None

    def matches(self, channel: ChannelMetadata) -> bool:
        """Whether every match criterion given holds for ``channel``."""
        if self.match_index is not None and self.match_index != channel.index:
            return False
        if self.match_name is not None and (
            channel.name is None or _squash(channel.name.value) != _squash(self.match_name)
        ):
            return False
        return self.match_excitation_nm is None or (
            channel.excitation_nm is not None
            and abs(channel.excitation_nm.value - self.match_excitation_nm)
            <= EXCITATION_MATCH_TOLERANCE_NM
        )


@dataclass(frozen=True)
class InstrumentProfile:
    """Defaults and fixed facts for one microscope."""

    instrument_id: str
    name: str | None = None
    modality: Modality | None = None
    resolution_enhancement: str | None = None
    pinhole_um: float | None = None
    bead_diameter_nm: float | None = None
    bead_product: str | None = None
    objectives: tuple[ObjectivePreset, ...] = ()
    channels: tuple[ChannelPreset, ...] = ()
    spec: SpecLimits = field(default_factory=SpecLimits)

    def select_objective(
        self, objective_name: str | None, objective_id: str | None = None
    ) -> tuple[ObjectivePreset | None, str | None]:
        """Choose the objective preset for a file; return it and a warning, if any."""
        if objective_id is not None:
            for preset in self.objectives:
                if preset.id == objective_id:
                    return preset, None
            known = ", ".join(p.id for p in self.objectives) or "none"
            raise ProfileError(
                f"profile {self.instrument_id} has no objective {objective_id!r}; known: {known}"
            )
        if not self.objectives:
            return None, None
        if objective_name is not None:
            for preset in self.objectives:
                if preset.matches(objective_name):
                    return preset, None
        defaults = [p for p in self.objectives if p.match is None]
        if defaults:
            return defaults[0], None
        if objective_name is None and len(self.objectives) == 1:
            return self.objectives[0], None
        if objective_name is None:
            return None, (
                f"the file does not name its objective and profile {self.instrument_id} "
                "has several; choose one explicitly"
            )
        return None, (
            f"objective {objective_name!r} matches no objective in profile {self.instrument_id}"
        )

    def overrides_for(
        self, metadata: AcquisitionMetadata, objective_id: str | None = None
    ) -> tuple[Overrides, list[str]]:
        """Return the values this profile sets for a file, and any warnings."""
        warnings: list[str] = []
        file_objective = (
            metadata.objective_name.value if metadata.objective_name is not None else None
        )
        objective, warning = self.select_objective(file_objective, objective_id)
        if warning is not None:
            warnings.append(warning)

        channels: dict[int, ChannelOverrides] = {}
        for channel in metadata.channels:
            preset = next((p for p in self.channels if p.matches(channel)), None)
            if preset is None and self.channels:
                label = channel.name.value if channel.name is not None else "unnamed"
                warnings.append(
                    f"channel {channel.index} ({label}) matches no channel in profile "
                    f"{self.instrument_id}"
                )
            pinhole_um = self.pinhole_um
            if preset is not None and preset.pinhole_um is not None:
                pinhole_um = preset.pinhole_um
            if preset is None and pinhole_um is None:
                continue
            channels[channel.index] = ChannelOverrides(
                name=None if preset is None else preset.name,
                excitation_nm=None if preset is None else preset.excitation_nm,
                emission_nm=None if preset is None else preset.emission_nm,
                pinhole_airy=None if preset is None else preset.pinhole_airy,
                pinhole_um=pinhole_um,
                preset_id=None if preset is None else preset.id,
            )

        overrides = Overrides(
            numerical_aperture=None if objective is None else objective.numerical_aperture,
            immersion_ri=None if objective is None else objective.immersion_ri,
            magnification=None if objective is None else objective.magnification,
            objective_name=None if objective is None else objective.name,
            objective_id=None if objective is None else objective.id,
            modality=self.modality,
            resolution_enhancement=self.resolution_enhancement,
            bead_diameter_nm=self.bead_diameter_nm,
            instrument_id=self.instrument_id,
            channels=channels,
        )
        return overrides, warnings


def _squash(text: str) -> str:
    return "".join(text.split()).lower()


def _table(data: dict[str, Any], key: str, allowed: set[str], where: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ProfileError(f"{where}: [{key}] must be a table")
    _check_keys(value, allowed, f"{where}: [{key}]")
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ProfileError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _identifier(table: dict[str, Any], where: str) -> str:
    value = table.get("id")
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{where}: 'id' is required and must be a non-empty string")
    return value.strip()


def _value(table: dict[str, Any], key: str, field: str, where: str) -> Any:
    if key not in table:
        return None
    try:
        return coerce_value(field, table[key])
    except InvalidMetadataError as exc:
        raise ProfileError(f"{where}: {exc}") from exc


def _array(data: dict[str, Any], key: str, where: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProfileError(f"{where}: use [[{key}]] tables")
    return value


def parse_profile(data: dict[str, Any], where: str = "profile") -> InstrumentProfile:
    """Build a profile from parsed TOML data.

    Raises
    ------
    ProfileError
        If a key is unknown, a value is invalid, or an identifier is repeated.
    """
    _check_keys(data, _TOP_LEVEL_KEYS, where)
    instrument = _table(data, "instrument", _INSTRUMENT_KEYS, where)
    if "instrument" not in data:
        raise ProfileError(f"{where}: an [instrument] table with an 'id' is required")
    bead = _table(data, "bead", _BEAD_KEYS, where)

    objectives: list[ObjectivePreset] = []
    for number, table in enumerate(_array(data, "objective", where), start=1):
        here = f"{where}: objective {number}"
        _check_keys(table, _OBJECTIVE_KEYS, here)
        match = table.get("match")
        if match is not None and (not isinstance(match, str) or not match.strip()):
            raise ProfileError(f"{here}: 'match' must be a non-empty string")
        objectives.append(
            ObjectivePreset(
                id=_identifier(table, here),
                match=match,
                name=_value(table, "name", "objective_name", here),
                numerical_aperture=_value(table, "numerical_aperture", "numerical_aperture", here),
                immersion_ri=_value(table, "immersion_ri", "immersion_ri", here),
                magnification=_value(table, "magnification", "magnification", here),
            )
        )

    channels: list[ChannelPreset] = []
    for number, table in enumerate(_array(data, "channel", where), start=1):
        here = f"{where}: channel {number}"
        _check_keys(table, _CHANNEL_KEYS, here)
        match = table.get("match", {})
        if not isinstance(match, dict) or not match:
            raise ProfileError(
                f"{here}: 'match' is required, e.g. match = {{ excitation_nm = 488 }}"
            )
        _check_keys(match, _CHANNEL_MATCH_KEYS, f"{here}: match")
        index = match.get("index")
        if index is not None and (
            isinstance(index, bool) or not isinstance(index, int) or index < 0
        ):
            raise ProfileError(f"{here}: match.index must be a whole number >= 0")
        channels.append(
            ChannelPreset(
                id=_identifier(table, here),
                match_name=_value(match, "name", "name", f"{here}: match"),
                match_excitation_nm=_value(
                    match, "excitation_nm", "excitation_nm", f"{here}: match"
                ),
                match_index=index,
                name=_value(table, "name", "name", here),
                excitation_nm=_value(table, "excitation_nm", "excitation_nm", here),
                emission_nm=_value(table, "emission_nm", "emission_nm", here),
                pinhole_airy=_value(table, "pinhole_airy", "pinhole_airy", here),
                pinhole_um=_value(table, "pinhole_um", "pinhole_um", here),
            )
        )

    for kind, presets in (("objective", objectives), ("channel", channels)):
        ids = [p.id for p in presets]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ProfileError(f"{where}: {kind} id(s) used more than once: {repeated}")

    spec_table = data.get("spec", {})
    if not isinstance(spec_table, dict):
        raise ProfileError(f"{where}: [spec] must be a table")
    try:
        spec = parse_spec(spec_table, where)
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc

    product = bead.get("product")
    if product is not None and not isinstance(product, str):
        raise ProfileError(f"{where}: [bead] product must be a string")
    name = instrument.get("name")
    if name is not None and not isinstance(name, str):
        raise ProfileError(f"{where}: [instrument] name must be a string")
    return InstrumentProfile(
        instrument_id=_identifier(instrument, f"{where}: [instrument]"),
        name=name,
        modality=_value(instrument, "modality", "modality", f"{where}: [instrument]"),
        resolution_enhancement=_value(
            instrument,
            "resolution_enhancement",
            "resolution_enhancement",
            f"{where}: [instrument]",
        ),
        pinhole_um=_value(instrument, "pinhole_um", "pinhole_um", f"{where}: [instrument]"),
        bead_diameter_nm=_value(bead, "diameter_nm", "bead_diameter_nm", f"{where}: [bead]"),
        bead_product=product,
        objectives=tuple(objectives),
        channels=tuple(channels),
        spec=spec,
    )


def load_profile(path: str | Path) -> InstrumentProfile:
    """Load an instrument profile from a TOML file.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ProfileError
        If the file is not valid TOML or not a valid profile.
    """
    path = Path(path)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path.name}: not valid TOML: {exc}") from exc
    return parse_profile(data, path.name)
