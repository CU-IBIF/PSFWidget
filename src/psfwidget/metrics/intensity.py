"""Image-mode normalisation of intensity (MET-04).

Images of the same slide recorded at different bit depths (for example 12-
and 16-bit camera modes) have intensities on different scales, so their raw
integrated and peak intensities cannot share a Levey-Jennings chart. Dividing
each bead's intensity by the **mode** of its image (the most common voxel
value, i.e. the background level of a bead image, mostly empty space)
removes a common scale factor: if the 16-bit image is the 12-bit one times a
constant, background and beads scale together and the ratio is unchanged.

Conditions, stated in the user manual:

* The mode must be well above zero. Photon-counting and zero-offset
  detectors (many point-scanning confocals) record most background voxels as
  0, so the mode is 0 and the normalisation is not defined; it is then left
  out with a warning.
* On cameras the mode is mostly the camera offset. The normalisation is
  exact when the offset scales with the signal between the two set-ups (the
  same data at a different bit depth); if a camera keeps the same offset
  but changes gain, the ratio still changes, and the tracked mode shows it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

FLOAT_BINS = 4096
"""Histogram bins used to find the mode of floating-point data."""


def image_mode(volume: NDArray[Any]) -> float:
    """Return the most common voxel value of an image (lowest value on ties).

    Integer data is counted exactly; floating-point data uses a
    :data:`FLOAT_BINS`-bin histogram and returns the centre of the fullest bin.
    """
    data = np.asarray(volume)
    if np.issubdtype(data.dtype, np.integer):
        flat = data.ravel()
        low = int(flat.min())
        counts = np.bincount(flat.astype(np.int64) - low)
        return float(int(np.argmax(counts)) + low)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return float("nan")
    counts, edges = np.histogram(finite, bins=FLOAT_BINS)
    index = int(np.argmax(counts))
    return float((edges[index] + edges[index + 1]) / 2.0)
