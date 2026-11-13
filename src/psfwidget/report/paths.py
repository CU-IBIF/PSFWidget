"""Output file names from templates (REP-05).

A template is a relative path with ``{token}`` placeholders, for example
``{instrument}/{yyyy}/{date}_{file_stem}_session``. The file extension is
added for each format. Tokens:

==============  ==============================================
``instrument``  instrument id
``objective``   objective id (``any`` when unknown)
``date``        acquisition date, ``YYYY-MM-DD``
``yyyy``        acquisition year
``mm``          acquisition month, two digits
``dd``          acquisition day, two digits
``file_stem``   source file name without extension
``series``      series index
``session``     session id in the results database (``0`` if none)
``kind``        ``session`` or ``trend``
==============  ==============================================

Values are made safe for file names on Windows, macOS, and Linux. Existing
files are never overwritten: ``_2``, ``_3``, … is added instead.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

TOKENS = frozenset(
    {
        "instrument",
        "objective",
        "date",
        "yyyy",
        "mm",
        "dd",
        "file_stem",
        "series",
        "session",
        "kind",
    }
)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class TemplateError(ValueError):
    """Raised when an output template is invalid."""


def check_template(template: str) -> None:
    """Raise :class:`TemplateError` unless ``template`` is a valid relative template."""
    if not template.strip():
        raise TemplateError("the output template is empty")
    try:
        fields = [name for _, name, _, _ in string.Formatter().parse(template) if name is not None]
    except ValueError as exc:
        raise TemplateError(f"invalid template {template!r}: {exc}") from None
    unknown = sorted(set(fields) - TOKENS)
    if unknown:
        raise TemplateError(
            f"unknown token(s) {unknown} in {template!r}; known: {', '.join(sorted(TOKENS))}"
        )
    path = Path(template.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise TemplateError(
            f"template {template!r} must be a relative path inside the output root"
        )


def safe(value: object) -> str:
    """Return ``value`` as text usable in a file name."""
    text = _UNSAFE.sub("-", str(value)).strip(" .")
    return text or "-"


def token_values(
    *,
    instrument: str,
    objective: str | None,
    acquired: datetime,
    file_stem: str,
    series: int,
    session: int | None,
    kind: str,
) -> dict[str, str]:
    """Return the values of every token."""
    return {
        "instrument": instrument,
        "objective": objective or "any",
        "date": acquired.strftime("%Y-%m-%d"),
        "yyyy": acquired.strftime("%Y"),
        "mm": acquired.strftime("%m"),
        "dd": acquired.strftime("%d"),
        "file_stem": file_stem,
        "series": str(series),
        "session": str(session or 0),
        "kind": kind,
    }


def render(template: str, root: Path, values: Mapping[str, str]) -> Path:
    """Return ``root`` joined with the filled-in template, without an extension.

    Raises
    ------
    TemplateError
        If the template is invalid.
    """
    check_template(template)
    filled = template.replace("\\", "/").format(**{k: safe(v) for k, v in values.items()})
    return root.joinpath(*[part for part in filled.split("/") if part])


def unique(path: Path) -> Path:
    """Return ``path``, or the first free ``<stem>_N<suffix>`` if it exists."""
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1
