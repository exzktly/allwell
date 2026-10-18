"""Phone layout's side rail: each pane's left panel, slid out over the page.

At the desk, Review's rail, the Analyze form and the Datasets tool list each
sit beside their pane's content in a splitter. A phone held upright has no
room for a column beside the page, so in phone layout the three panels are
lent to one left-edge :class:`RailDrawer` instead: the band's ☰ slides the
current mode's panel out *over* the page — the whole panel, Review's sections
and well plate included — with the page dimmed beneath it, and tapping the
dimmed strip, ☰ again, or a section closes it.

It serves all three panes, so it lives here rather than in any one of them,
and it imports no pane. The window builds it (``AllWellApp._build_phone_shell``,
the one place that knows where each panel lives) and hands it:

* ``panels`` — the left panel of each mode, asked for every time it is needed;
* ``top_inset`` — where the top bar ends, so ☰ and the mode bar stay in reach;
* ``on_closed_inside`` — what a close from inside (the dimmed strip, Esc)
  means for the flag that drives it.

The drawer is the left panel's *expanded* state, so it is driven by the same
flag the desk's collapse toggle drives, through Review's
``tab_controller.apply_sidebar_visibility``, which calls :meth:`RailDrawer.apply`
last. Every panel goes back into its splitter, at its index, with its width pin
and the splitter's sizes, when phone layout ends.

The drawer is hosted on the Review widget, which holds the band, rather than on
a splitter (a ``QSplitter`` adopts any child widget as a pane).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from core import phone_layout
from core.safe import swallowed
from widgets.drawer import Drawer

#: The key the drawer is registered under in Review's widget registry, where
#: ``tab_controller.apply_sidebar_visibility`` finds it.
DRAWER_KEY = "_phone_drawer"

#: How much of the window's width the open rail covers. Not all of it: the
#: dimmed strip left showing says the page is still there, and is the target
#: a thumb taps to close the rail.
DRAWER_WIDTH_FRACTION = 0.86

_QWIDGETSIZE_MAX = 16_777_215


class RailDrawer(Drawer):
    """The left-edge drawer phone layout lends the panes' left panels to."""

    def __init__(self, host: QWidget, *,
                 panels: Callable[[], dict[str, QWidget]],
                 top_inset: Callable[[], int],
                 on_closed_inside: Callable[[], None]) -> None:
        super().__init__(host, edge="left", width_fraction=DRAWER_WIDTH_FRACTION,
                         margin=0, top_inset=top_inset)
        # Keeps the object name "Drawer", which its own sheet styles by.
        self.setProperty("phoneRail", True)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setContentWidget(holder)
        self._panels = panels
        #: Each lent panel's place at the desk, keyed by the panel's id; the
        #: panel itself is held in the value, so the id cannot be reused while
        #: the entry exists: (panel, splitter, index, sizes, min width, max width).
        self._homes: dict[int, tuple] = {}
        # Closed from inside — the dimmed strip tapped, Esc — is the panel
        # collapsing, so the flag follows; closed by the flag, a no-op there.
        self.closed.connect(on_closed_inside)

    def is_lent(self, panel: QWidget) -> bool:
        return id(panel) in self._homes

    def lend(self, panel: QWidget) -> None:
        """Move *panel* from its splitter into the drawer, remembering its place."""
        if self.is_lent(panel):
            return
        splitter = panel.parentWidget()
        if not isinstance(splitter, QSplitter):
            return
        self._homes[id(panel)] = (panel, splitter, splitter.indexOf(panel),
                                  splitter.sizes(), panel.minimumWidth(),
                                  panel.maximumWidth())
        self.contentWidget().layout().addWidget(panel)
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(_QWIDGETSIZE_MAX)

    def give_back(self, panel: QWidget) -> None:
        """Put *panel* back exactly where :meth:`lend` found it."""
        home = self._homes.pop(id(panel), None)
        if home is None:
            return
        _panel, splitter, index, sizes, lo, hi = home
        try:
            splitter.insertWidget(index, panel)
            panel.setMinimumWidth(lo)
            panel.setMaximumWidth(hi)
            splitter.setSizes(sizes)
        except RuntimeError:
            swallowed()  # the splitter went with a window torn down first

    def apply(self, mode: str, collapsed: bool) -> None:
        """Lend the panels and open or close the drawer — or give them back.

        Runs last in ``apply_sidebar_visibility``, so in phone layout it has
        the final word on which panel shows: only the current mode's, and only
        in the drawer. Outside phone layout it returns the panels and re-applies
        the desk's own visibility rule, since a reparent can show a hidden
        widget.
        """
        panels = self._panels()
        if phone_layout.is_on():
            for m, panel in panels.items():
                self.lend(panel)
                panel.setVisible(m == mode)
            if collapsed:
                self.close()
            else:
                self.open()
            return
        self.closeNow()
        for m, panel in panels.items():
            self.give_back(panel)
            # The desk's rule (``apply_sidebar_visibility``): the rail shows in
            # Review only; the other two show whenever expanded, their own pane
            # being the one that hides them in the wrong mode.
            shown = not collapsed and (mode == "review" if m == "review" else True)
            panel.setVisible(shown)
