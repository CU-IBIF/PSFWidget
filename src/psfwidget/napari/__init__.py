"""napari dock widgets: a thin GUI layer over the headless core (WP7).

* :class:`~psfwidget.napari.analysis_widget.AnalysisWidget`: analyse a bead
  stack, review beads, record, and report (UI-01, UI-04, DET-07).
* :class:`~psfwidget.napari.tracking_widget.TrackingWidget`: interactive
  Levey-Jennings charts, exclusions, and events (UI-02).
* :class:`~psfwidget.napari.profile_editor.ProfileEditor`: instrument profiles
  and spec limits (UI-03).

The widgets need ``pip install "psfwidget[napari]"``. Their logic is in
:mod:`psfwidget.napari.session`, :mod:`psfwidget.review`, and the core
packages, which do not need Qt.
"""
