"""A report as a list of blocks, rendered to HTML or PDF (REP-03).

Building reports from blocks keeps the HTML and PDF versions identical in
content: each renderer only decides how a block looks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    """Overall outcome shown on a report (REP-07)."""

    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT EVALUATED"

    @property
    def symbol(self) -> str:
        """A text symbol shown with the status, so it never relies on colour alone."""
        return {"PASS": "✓", "WARNING": "!", "FAIL": "✕", "NOT EVALUATED": "–"}[self.value]

    @property
    def rank(self) -> int:
        """Severity order: FAIL > WARNING > PASS > NOT EVALUATED."""
        return {"NOT EVALUATED": 0, "PASS": 1, "WARNING": 2, "FAIL": 3}[self.value]


def worst(statuses: Sequence[Status]) -> Status:
    """Return the most severe status, or NOT EVALUATED for none."""
    return max(statuses, key=lambda s: s.rank, default=Status.NOT_EVALUATED)


@dataclass(frozen=True)
class Heading:
    """A section heading; level 2 is a section, 3 a subsection."""

    text: str
    level: int = 2


@dataclass(frozen=True)
class Paragraph:
    """Running text; ``muted`` is used for notes."""

    text: str
    muted: bool = False


@dataclass(frozen=True)
class KeyValues:
    """Label-value pairs, e.g. provenance."""

    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Table:
    """A table. ``status_column`` holds :class:`Status` values to show as badges."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    numeric: frozenset[int] = frozenset()
    """Columns aligned right."""
    status_column: int | None = None
    caption: str = ""


@dataclass(frozen=True)
class Figure:
    """A PNG image with a caption."""

    png: bytes
    caption: str
    width: float = 1.0
    """Fraction of the text width."""


@dataclass(frozen=True)
class StatusLine:
    """A status badge with an explanation."""

    status: Status
    text: str


@dataclass(frozen=True)
class PageBreak:
    """Start a new page in the PDF; ignored in HTML."""


Block = Heading | Paragraph | KeyValues | Table | Figure | StatusLine | PageBreak


@dataclass(frozen=True)
class Branding:
    """Facility branding on every report (REP-08)."""

    facility: str = ""
    logo: Path | None = None
    """A PNG or JPEG file."""
    footer: str = ""


@dataclass(frozen=True)
class Report:
    """A complete report."""

    title: str
    subtitle: str
    status: Status | None
    generated_utc: datetime
    blocks: tuple[Block, ...]
    branding: Branding = field(default_factory=Branding)
