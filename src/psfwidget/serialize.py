"""Convert results and metadata to JSON-ready values."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from psfwidget.io.model import ACQUISITION_FIELDS, CHANNEL_FIELDS, AcquisitionMetadata, Sourced


def plain(value: Any) -> Any:
    """Convert enums, dates, paths, non-finite floats, and containers for JSON."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


def sourced(item: Sourced[Any] | None) -> dict[str, Any] | None:
    """Return a metadata value with its origin, or ``None``."""
    if item is None:
        return None
    return {"value": plain(item.value), "source": item.source, "note": item.note}


def metadata_record(meta: AcquisitionMetadata) -> dict[str, Any]:
    """Return metadata values with their origins, ready for JSON."""
    return {
        "values": {name: sourced(getattr(meta, name)) for name in ACQUISITION_FIELDS},
        "channels": [
            {"index": channel.index}
            | {name: sourced(getattr(channel, name)) for name in CHANNEL_FIELDS}
            for channel in meta.channels
        ],
        "warnings": list(meta.warnings),
    }
