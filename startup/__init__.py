"""Launch-time concerns: making a startup failure observable, and keeping it fast.

Two things have to happen before the application proper exists, and neither
belongs to any pane:

* :mod:`startup.boot_diag` opens a per-launch **boot log** on disk and arms
  ``faulthandler`` and an excepthook onto it, so a launch that dies at the C
  level — a native crash inside ``import PySide6``, a Qt platform-plugin abort —
  or one that simply *hangs* still leaves a trail naming the step that never
  returned. ``allwell.py`` imports it first, before PySide6.
* :mod:`startup.mpl_bootstrap` restricts matplotlib's font discovery to the
  fonts in ``assets/fonts`` and pins its cache, so the first launch on a machine
  does not spend minutes parsing every font installed on it.

Both are deliberately outside :mod:`util`: that package is Qt-free *general*
helpers, uploaded wholesale to a remote worker by
``services.remote_manifest``, and a headless pipeline run has no launch to
diagnose and no fonts to draw with. Both run before almost anything else, so
neither may import from a pane.
"""

from __future__ import annotations
