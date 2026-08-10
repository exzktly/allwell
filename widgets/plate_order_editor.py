"""PlateOrderEditor — a click-to-define acquisition-ordering plate.

An 8×12 microplate that shows, and lets the user edit, the **order** in which a
scan visited its wells. It is a :class:`~widgets.well_plate_selector.WellPlateSelector`
driven in its ordered ``"path"`` selection model, so it is the same control as
every other plate in the app — same painting, same geometry, same sizing rule —
and this class adds only the two things the shared widget has no opinion about:

* **Dirty ownership.** Every click marks the widget *user-owned*, which the
  embedding page uses to stop overwriting a hand-drawn order with the
  rectangular grid preview it would otherwise keep pushing. The first such click
  also *replaces* the preview rather than extending it — see
  :meth:`PlateOrderEditor._on_path_changed`.
* **The ``orderChanged`` name** its host already listens for.

Everything visible — the numbered wells, the alpha-blended arrow tracing the
visit order, the lit/recessed well bodies, the headers, the size caps — lives in
``well_plate_selector`` and is shared with every other plate. This module used
to carry its own copy of all of it, which is how the two plates drifted into
different cell sizes and different alignment on screen.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from widgets.well_plate_selector import WellPlateSelector


class PlateOrderEditor(WellPlateSelector):
    """8×12 plate whose model is an ordered acquisition path (click to edit)."""

    orderChanged = Signal(list)   # new order as a list of well ids, e.g. ["A01", …]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Deliberately keeps the inherited ``WellPlateSelector`` object name:
        # the panel background, border and radius are QSS rules keyed to it,
        # and a plate that renames itself loses the card every other plate in
        # the app is drawn on.
        self.setSelectionModel("path")
        # The path is the whole model here, so the inherited All/Invert/Clear
        # row would act on a selection set this widget never uses. The host
        # page carries its own "Generate path from grid" / "Clear path"
        # buttons; those are the actions that mean something for an order.
        self.setActionsVisible(False)
        self.setRowColumnSelectable(False)
        self._dirty = False
        # The wells currently on screen only because a preview put them there.
        # Kept so the first click can tell them from wells the user chose.
        self._preview: list[str] = []
        self.pathChanged.connect(self._on_path_changed)

    # ── model API (the contract datasets/ome_import/view.py uses) ────────────
    def order(self) -> list[str]:
        """The acquisition path as canonical well ids, in visit order."""
        return self.pathWellIds()

    def set_order(self, labels) -> None:
        """Set the path from *labels* as a user-owned (dirty) order, no signal."""
        self._set_path(labels)
        self._preview = []
        self._dirty = True

    def set_preview(self, labels) -> None:
        """Display *labels* as a non-dirty preview (e.g. the grid's serpentine).

        Ignored once the widget is dirty — a hand-drawn order is never
        overwritten by a preview. Emits nothing and does not mark the widget
        dirty, so the page can track the grid without stealing ownership.
        """
        if self._dirty:
            return
        self._preview = [str(w) for w in labels]
        self._set_path(labels)

    def is_dirty(self) -> bool:
        return self._dirty

    def clear(self) -> None:
        """Empty the path and drop dirty state, emitting the (empty) order."""
        if not self.pathWellIds() and not self._dirty:
            return
        self._set_path([])
        self._preview = []
        self._dirty = False
        self.orderChanged.emit([])

    # ── internals ────────────────────────────────────────────────────────────
    def _set_path(self, labels) -> bool:
        """Replace the path, dropping any progress dials it was wearing.

        A dial describes how far through *this* order a conversion got, so a
        different order makes it a statement about wells that are no longer the
        ones on screen. Without this the plate mixes a finished run's fills
        with a freshly edited path — the well the user just removed keeps its
        full accent disc, because the paint path draws a well with a fraction
        as the dial and never reaches the depressed branch. Subset's picker
        carries the same guard for the same reason.
        """
        changed = self.setPathWellIds(labels)
        if changed:
            self.clearWellFractions()
        return changed

    def _on_path_changed(self, ids: list) -> None:
        """A click on the plate is the user taking ownership of the order.

        The first such click **replaces** the preview instead of extending it.
        A preview is the host's guess, drawn in the same path the user clicks
        into, so the plate's own append-on-click would silently make the guess's
        wells positions 1..n and push every clicked well one place later — a
        conversion where each well receives the previous stage position's
        images, and no count on screen that looks wrong. Wells the user did
        click survive, in the order they were clicked; if the click was on a
        previewed well (the plate's remove gesture) the path is simply emptied,
        which is the same "start from nothing" that taking over means.
        """
        ids = list(ids)
        if not self._dirty and self._preview:
            preview = set(self._preview)
            ids = [w for w in ids if w not in preview]
            self._set_path(ids)
        self._preview = []
        self._dirty = True
        # A click goes straight to the grid, so the dial-clearing that
        # ``_set_path`` does for programmatic changes has to happen here too.
        self.clearWellFractions()
        self.orderChanged.emit(list(ids))
