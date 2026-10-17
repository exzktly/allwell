"""Phone layout's side rail: each pane's left panel, slid out over the page.

At the desk, Review's rail, the Analyze form and the Datasets tool list each
sit beside their pane's content in a splitter. A phone held upright has no
room for a column beside the page, so in phone layout the three panels are
lent to one left-edge :class:`~widgets.drawer.Drawer` instead: the band's ☰
slides the current mode's panel out *over* the page — the whole panel, Review's
sections and well plate included — with the page dimmed beneath it, and
tapping the dimmed strip, ☰ again, or a section closes it.

The drawer is the left panel's *expanded* state, so it is driven by the same
flag the desk's collapse toggle drives (``_sidebar_collapsed``, through
``tab_controller.apply_sidebar_visibility``, which calls
:func:`apply_phone_drawer` last). Every panel goes back into its splitter, at
its index, with its width pin and the splitter's sizes, when phone layout ends.

The drawer is hosted on the Review widget, which holds the band, rather than on
a splitter (a ``QSplitter`` adopts any child widget as a pane), and sits below
the band (``top_inset``) so ☰ and the mode bar stay in reach while it is open.
"""

from __future__ import annotations

from core import phone_layout
from core.safe import swallowed

DRAWER_KEY = "_phone_drawer"

#: How much of the window's width the open rail covers. Not all of it: the
#: dimmed strip left showing says the page is still there, and is the target
#: a thumb taps to close the rail.
DRAWER_WIDTH_FRACTION = 0.86


def _panels(ctx) -> dict:
    """The left panel of each mode, keyed by mode (missing ones left out)."""
    app = ctx.window  # transitional bridge (A3)
    out = {"review": getattr(app, "_sidebar_root", None)}
    for mode, attr in (("analyze", "_analyze_pane"), ("datasets", "_datasets_pane")):
        out[mode] = getattr(getattr(app, attr, None), "_left_sidebar", None)
    return {m: p for m, p in out.items() if p is not None}


def ensure_drawer(ctx):
    """The phone drawer, built on first use. Returns the ``Drawer``."""
    if ctx.widgets.has(DRAWER_KEY):
        return ctx.widgets.get(DRAWER_KEY)
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from widgets.drawer import Drawer

    app = ctx.window  # transitional bridge (A3)

    def _below_band() -> int:
        pane = getattr(app, "_h_pane", None)
        return pane.geometry().top() if pane is not None else 0

    drawer = Drawer(app, edge="left", width_fraction=DRAWER_WIDTH_FRACTION,
                    margin=0, top_inset=_below_band)
    drawer.setObjectName("PhoneRailDrawer")
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    drawer.setContentWidget(holder)
    # Closed from inside — the dimmed strip tapped, Esc — is the panel
    # collapsing, so the flag follows; closed by the flag, this is a no-op.
    drawer.closed.connect(lambda: _on_drawer_closed(ctx))
    return ctx.widgets.register(DRAWER_KEY, drawer)


def _on_drawer_closed(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    if phone_layout.is_on() and not getattr(app, "_sidebar_collapsed", False):
        from review.core.tab_controller import set_sidebar_collapsed
        set_sidebar_collapsed(ctx, True)


def _lend(panel, holder) -> None:
    """Move *panel* from its splitter into the drawer, remembering its place."""
    from PySide6.QtWidgets import QSplitter
    if getattr(panel, "_aw_phone_home", None) is not None:
        return
    splitter = panel.parentWidget()
    if not isinstance(splitter, QSplitter):
        return
    panel._aw_phone_home = (splitter, splitter.indexOf(panel), splitter.sizes(),
                            panel.minimumWidth(), panel.maximumWidth())
    holder.layout().addWidget(panel)
    panel.setMinimumWidth(0)
    panel.setMaximumWidth(16_777_215)   # Qt's QWIDGETSIZE_MAX


def _give_back(panel) -> None:
    """Put *panel* back exactly where :func:`_lend` found it."""
    home = getattr(panel, "_aw_phone_home", None)
    if home is None:
        return
    splitter, index, sizes, lo, hi = home
    panel._aw_phone_home = None
    try:
        splitter.insertWidget(index, panel)
        panel.setMinimumWidth(lo)
        panel.setMaximumWidth(hi)
        splitter.setSizes(sizes)
    except RuntimeError:
        swallowed()  # the splitter went with a window torn down first


def apply_phone_drawer(ctx, mode: str, collapsed: bool) -> None:
    """Lend the panels to the drawer and open or close it — or give them back.

    Runs last in ``apply_sidebar_visibility``, so in phone layout it has the
    final word on which panel shows: only the current mode's, and only in the
    drawer. Outside phone layout it returns the panels and re-applies the
    desk's own visibility rule, since a reparent can show a hidden widget.
    """
    panels = _panels(ctx)
    if phone_layout.is_on():
        drawer = ensure_drawer(ctx)
        holder = drawer.contentWidget()
        for m, panel in panels.items():
            _lend(panel, holder)
            panel.setVisible(m == mode)
        if collapsed:
            drawer.close()
        else:
            drawer.open()
        return
    if not ctx.widgets.has(DRAWER_KEY):
        return
    ctx.widgets.get(DRAWER_KEY).closeNow()
    for m, panel in panels.items():
        _give_back(panel)
        # The desk's rule (``apply_sidebar_visibility``): the rail shows in
        # Review only; the other two show whenever expanded, their own pane
        # being the one that hides them in the wrong mode.
        shown = not collapsed and (mode == "review" if m == "review" else True)
        panel.setVisible(shown)
