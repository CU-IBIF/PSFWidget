"""Manual review of beads: include or exclude beads by hand (DET-07).

A review returns a new :class:`~psfwidget.analysis.FieldResult`; the original
is unchanged. Each changed bead keeps a record of the decision in its reasons
or notes (who, why, and what the automatic decision was), and the channel's
summary, rejection counts, and spec evaluation are recomputed from the new
set of accepted beads, so reports and the database show the reviewed result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from psfwidget.analysis import ChannelResult, FieldResult
from psfwidget.metrics.bead import ACCEPTED, REJECTED, BeadResult
from psfwidget.metrics.summary import rejection_counts, summarize
from psfwidget.profiles.spec import evaluate

MANUAL_EXCLUSION = "excluded by reviewer"
MANUAL_INCLUSION = "included by reviewer"


class ReviewError(ValueError):
    """Raised when a review decision cannot be applied."""


@dataclass(frozen=True)
class Decision:
    """A reviewer's decision about one bead."""

    accept: bool
    reason: str
    reviewer: str


def measurable(bead: BeadResult) -> bool:
    """Whether a bead has the FWHM values needed to be accepted."""
    return None not in (bead.fwhm_x_um, bead.fwhm_y_um, bead.fwhm_z_um)


def _apply(bead: BeadResult, decision: Decision) -> BeadResult:
    if not decision.reason.strip():
        raise ReviewError(f"bead {bead.bead_id}: a review decision needs a reason")
    who = decision.reviewer.strip() or "unknown reviewer"
    if decision.accept:
        if bead.accepted:
            return bead
        if not measurable(bead):
            raise ReviewError(
                f"bead {bead.bead_id} cannot be included: its FWHM could not be measured "
                f"({'; '.join(bead.reasons) or 'no fit'})"
            )
        note = (
            f"{MANUAL_INCLUSION} {who}: {decision.reason.strip()} "
            f"(was rejected: {'; '.join(bead.reasons)})"
        )
        return replace(bead, status=ACCEPTED, reasons=(), notes=(*bead.notes, note))
    if not bead.accepted:
        return bead
    reason = f"{MANUAL_EXCLUSION} {who}: {decision.reason.strip()}"
    return replace(bead, status=REJECTED, reasons=(reason, *bead.reasons))


def review_channel(
    channel: ChannelResult, decisions: Mapping[int, Decision], result: FieldResult
) -> ChannelResult:
    """Apply decisions (keyed by bead id) to one channel and recompute its summary.

    Raises
    ------
    ReviewError
        If a bead id is unknown, a reason is missing, or an unmeasurable bead
        is included.
    """
    known = {b.bead_id for b in channel.beads}
    unknown = sorted(set(decisions) - known)
    if unknown:
        raise ReviewError(f"channel {channel.index}: unknown bead id(s) {unknown}")
    beads = tuple(
        _apply(b, decisions[b.bead_id]) if b.bead_id in decisions else b for b in channel.beads
    )
    if beads == channel.beads:
        return channel
    summary = summarize(beads)
    accepted = sum(1 for b in beads if b.accepted)
    limits = result.spec_limits
    return replace(
        channel,
        beads=beads,
        summary=summary,
        rejections=rejection_counts(beads),
        spec=None if limits.empty else evaluate(limits, summary, accepted),
    )


def review(result: FieldResult, decisions: Mapping[int, Mapping[int, Decision]]) -> FieldResult:
    """Apply decisions, keyed by channel index then bead id, to a field result.

    Raises
    ------
    ReviewError
        If a channel index is not in the result, or a decision is invalid.
    """
    indices = {c.index for c in result.channels}
    unknown = sorted(set(decisions) - indices)
    if unknown:
        raise ReviewError(f"no analysed channel(s) {unknown}")
    return replace(
        result,
        channels=tuple(
            review_channel(c, decisions[c.index], result) if c.index in decisions else c
            for c in result.channels
        ),
    )


def reviewed_beads(result: FieldResult) -> list[tuple[int, int, str]]:
    """Return (channel, bead id, decision text) for every bead changed by a reviewer."""
    found = []
    for channel in result.channels:
        for bead in channel.beads:
            texts = [
                t
                for t in (*bead.reasons, *bead.notes)
                if t.startswith((MANUAL_EXCLUSION, MANUAL_INCLUSION))
            ]
            if texts:
                found.append((channel.index, bead.bead_id, texts[0]))
    return found
