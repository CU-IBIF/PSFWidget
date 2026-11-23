"""Work-arounds for napari behaviour that affects PSFWidget (DEF-01).

napari's layer list draws a "loading" indicator for layers that are not yet
loaded. If the list is drawn while a layer is being removed or added, the
view can briefly hold an index for a row that no longer exists. napari then
reads the row's size as ``None`` and raises ``AttributeError: 'NoneType'
object has no attribute 'height'`` from ``LayerDelegate._paint_loading``,
seen at the facility on Windows when opening a file.

:func:`guard_layer_list` makes the delegate skip such stale rows; the view
draws them correctly on its next repaint, a moment later. It wraps napari's
method rather than copying it, does nothing if napari's internals change,
and can be removed once napari checks for this itself.
"""

from __future__ import annotations

import functools
from typing import Any

GUARD_MARK = "_psfwidget_stale_row_guard"


def guard_layer_list() -> bool:
    """Make napari's layer list skip rows that vanished while it was drawing.

    Returns ``True`` if the guard is in place (now or from an earlier call),
    ``False`` if napari's layer delegate could not be found.
    """
    try:
        from napari._qt.containers._layer_delegate import LayerDelegate
        from qtpy.QtCore import Qt
    except ImportError:
        return False
    paint = getattr(LayerDelegate, "paint", None)
    if paint is None:
        return False
    if getattr(paint, GUARD_MARK, False):
        return True
    size_role = Qt.ItemDataRole.SizeHintRole

    @functools.wraps(paint)
    def guarded_paint(self: Any, painter: Any, option: Any, index: Any) -> None:
        # A valid row always has a size hint; ``None`` means the row is gone.
        if index.isValid() and index.data(size_role) is None:
            return
        paint(self, painter, option, index)

    setattr(guarded_paint, GUARD_MARK, True)
    LayerDelegate.paint = guarded_paint
    return True
