"""Write instrument profiles as TOML (UI-03).

Python reads TOML (``tomllib``) but does not write it; profiles only use
tables, arrays of tables, inline tables, strings, numbers, booleans, and
lists, so a small writer is enough. Comments in a file that was read are not
kept.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SECTION_ORDER = ("instrument", "bead", "objective", "channel", "spec", "tracking")


def _key(name: str) -> str:
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return _string(name)


def _string(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def value(item: Any) -> str:
    """Return a TOML literal for a scalar, list, or inline table.

    Raises
    ------
    TypeError
        If the value cannot be written as TOML.
    ValueError
        If a number is not finite.
    """
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, float):
        if not math.isfinite(item):
            raise ValueError(f"cannot write {item!r} to a profile")
        return repr(item)
    if isinstance(item, str):
        return _string(item)
    if isinstance(item, list | tuple):
        return "[" + ", ".join(value(v) for v in item) + "]"
    if isinstance(item, Mapping):
        return "{ " + ", ".join(f"{_key(k)} = {value(v)}" for k, v in item.items()) + " }"
    raise TypeError(f"cannot write {type(item).__name__} to a profile")


def _is_table_array(item: Any) -> bool:
    return isinstance(item, list) and bool(item) and all(isinstance(v, Mapping) for v in item)


def _table(lines: list[str], header: str, table: Mapping[str, Any]) -> None:
    lines.append(header)
    nested = []
    for name, item in table.items():
        if (
            isinstance(item, Mapping)
            and item
            and any(isinstance(v, Mapping) for v in item.values())
        ):
            nested.append((name, item))
        else:
            lines.append(f"{_key(name)} = {value(item)}")
    lines.append("")
    for name, item in nested:
        prefix = header.strip("[]")
        _table(lines, f"[{prefix}.{_key(name)}]", item)


def dumps(data: Mapping[str, Any]) -> str:
    """Return a profile (as read by ``tomllib``) as TOML text."""
    lines: list[str] = []
    names = [n for n in SECTION_ORDER if n in data] + [n for n in data if n not in SECTION_ORDER]
    for name in names:
        item = data[name]
        if _is_table_array(item):
            for entry in item:
                _table(lines, f"[[{_key(name)}]]", entry)
        elif isinstance(item, Mapping):
            _table(lines, f"[{_key(name)}]", item)
        else:
            lines.insert(0, f"{_key(name)} = {value(item)}")
    return "\n".join(lines).rstrip() + "\n"


def save(data: Mapping[str, Any], path: str | Path) -> Path:
    """Validate a profile and write it to ``path``.

    Raises
    ------
    psfwidget.profiles.ProfileError
        If the profile is invalid; nothing is written.
    """
    import tomllib

    from psfwidget.profiles.model import parse_profile

    text = dumps(data)
    parse_profile(tomllib.loads(text), where=str(path))
    target = Path(path)
    target.write_text(text, encoding="utf-8")
    return target
