"""PSFWidget: PSF analysis and tracking for microscope quality control.

The package is organised as a headless core (``io``, ``profiles``, ``detect``,
``metrics``, ``theory``, ``average``, ``tracking``, ``report``, ``automation``)
with a thin napari GUI layer (``psfwidget.napari``). See ``docs/PLAN.md`` §4.
"""

from psfwidget.__about__ import __version__

__all__ = ["__version__"]
