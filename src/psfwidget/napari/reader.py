"""napari reader: open bead files by drag and drop or *File → Open* (IMP-11).

Dropping an ND2, LIF, CZI, or TIFF file onto napari opens the PSF analysis
widget (docking it if needed) and loads the file there, asking for the series
when the file holds several (IMP-02). The widget adds the image layers itself,
so the reader returns napari's "nothing more to add" value, ``[(None,)]``.

Without a viewer (napari used from a script with no window), the reader
returns the channels of the first series as image layers instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

EXTENSIONS = (".nd2", ".lif", ".czi", ".tif", ".tiff")
"""Suffixes the reader claims; ``.ome.tif`` ends in ``.tif``."""
PLUGIN = "psfwidget"
WIDGET = "PSF analysis"

LayerData = tuple[Any, ...]


def claims(path: Any) -> bool:
    """Return whether PSFWidget reads ``path``."""
    return isinstance(path, (str, Path)) and str(path).lower().endswith(EXTENSIONS)


def get_reader(path: Any) -> Callable[[Any], list[LayerData]] | None:
    """Return the reader for ``path``, or ``None`` if PSFWidget does not read it.

    napari passes a list when several files are dropped together; each file is
    then offered to the reader separately, so lists are declined here.
    """
    return read if claims(path) else None


def read(path: Any) -> list[LayerData]:
    """Load ``path`` into the PSF analysis widget of the current viewer."""
    import napari

    from psfwidget.napari._compat import guard_layer_list

    guard_layer_list()  # DEF-01
    viewer = napari.current_viewer()
    if viewer is None or getattr(viewer, "window", None) is None:
        return _layers(path)
    _, widget = viewer.window.add_plugin_dock_widget(PLUGIN, WIDGET)
    widget.load_path(str(path))
    return [(None,)]


def _layers(path: Any) -> list[LayerData]:
    """Return the first series' channels as napari image layer data."""
    from psfwidget.napari.session import AnalysisSession

    session = AnalysisSession()
    session.open(str(path))
    return [
        (
            spec.data,
            {
                "name": spec.name,
                "scale": spec.scale,
                "colormap": spec.colormap,
                "contrast_limits": spec.contrast_limits,
                "blending": "additive",
            },
            "image",
        )
        for spec in session.image_layers()
    ]
