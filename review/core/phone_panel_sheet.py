"""Phone layout's home for a plot's right-hand panel: a full-width sheet.

At the desk, a plot's Properties panel (and the Scatter cell viewer, which
shares the slot) opens in the plot's right dock — a layout sibling of the
figure, ``ui_helpers.make_plot_with_right_dock``. On a phone held upright that
does not work: the panel is ``Metrics.dock_w`` wide on a screen barely wider,
so the figure is squeezed to a sliver and the panel runs off the right edge;
and the panel is opened from the ⚙ Controls sheet, which is drawn *over* the
page, so the dock appeared underneath the very sheet the user had just tapped
in.

So in phone layout the dock is lent to a :class:`~widgets.drawer.Drawer` that
covers the window's full width, the Controls sheet is closed first, and the
panel's fixed width is lifted while it is lent so it fills the screen. The dock
goes back into its plot, at its index and with its width limits, when the
sheet closes — by the panel's own close button (which hides the dock), by the
dimmed strip or Esc, or because phone layout ended. At the desk nothing here
runs.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core import phone_layout
from core.safe import swallowed

_QWIDGETSIZE_MAX = 16_777_215

#: The attribute on the window holding its one panel sheet.
_SHEET_ATTR = "_aw_phone_panel_sheet"


class _HideWatcher(QObject):
    """Closes the sheet when the lent dock is hidden from inside — the panel's
    own ✕ calls ``dock.setVisible(False)``, which would otherwise leave an
    empty sheet on screen."""

    def __init__(self, dock: QWidget, sheet) -> None:
        super().__init__(dock)
        self._sheet = sheet

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if ev.type() == QEvent.Hide and self._sheet.isOpen():
            self._sheet.close()
        return False


def _close_controls_sheets(win: QWidget) -> None:
    """Close the ⚙ Controls sheet (any Drawer marked ``phoneSheet``)."""
    from widgets.drawer import Drawer
    for d in win.findChildren(Drawer):
        if d.property("phoneSheet") and d.isOpen():
            d.close()


def _sheet_for(win: QWidget):
    sheet = getattr(win, _SHEET_ATTR, None)
    if sheet is not None:
        return sheet
    from widgets.drawer import Drawer
    sheet = Drawer(win, edge="right", width_fraction=1.0, margin=0)
    sheet.setProperty("phonePanelSheet", True)
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    sheet.setContentWidget(holder)
    sheet._aw_lent = None
    sheet.closed.connect(lambda: _give_back(sheet))
    setattr(win, _SHEET_ATTR, sheet)

    def _on_layout(on: bool) -> None:
        if not on:
            try:
                sheet.closeNow()
                _give_back(sheet)
            except RuntimeError:
                phone_layout.remove_listener(_on_layout)  # the window went first

    phone_layout.add_listener(_on_layout)
    sheet.destroyed.connect(lambda *_: phone_layout.remove_listener(_on_layout))
    return sheet


def _widths(w: QWidget) -> tuple[int, int]:
    return w.minimumWidth(), w.maximumWidth()


def _lend(sheet, dock: QWidget) -> None:
    home = dock.parentWidget()
    lay = home.layout() if home is not None else None
    if lay is None:
        return
    children = [c for c in dock.findChildren(QWidget) if c.parentWidget() is dock]
    sheet._aw_lent = (dock, home, lay.indexOf(dock), _widths(dock),
                      [(c, _widths(c)) for c in children])
    sheet.contentWidget().layout().addWidget(dock)
    for w in (dock, *children):
        w.setMinimumWidth(0)
        w.setMaximumWidth(_QWIDGETSIZE_MAX)
    watcher = _HideWatcher(dock, sheet)
    dock.installEventFilter(watcher)
    dock._aw_phone_watcher = watcher


def _give_back(sheet) -> None:
    lent, sheet._aw_lent = sheet._aw_lent, None
    if lent is None:
        return
    dock, home, index, (lo, hi), children = lent
    try:
        watcher = getattr(dock, "_aw_phone_watcher", None)
        if watcher is not None:
            dock.removeEventFilter(watcher)
            dock._aw_phone_watcher = None
        lay = home.layout()
        if lay is not None:
            lay.insertWidget(max(0, index), dock)
        dock.setMinimumWidth(lo)
        dock.setMaximumWidth(hi)
        for c, (clo, chi) in children:
            c.setMinimumWidth(clo)
            c.setMaximumWidth(chi)
        # The sheet closing is the panel closing.
        dock.setVisible(False)
    except RuntimeError:
        swallowed()  # the plot went while its panel was out


def present(dock: QWidget | None) -> bool:
    """In phone layout, show *dock* as a full-width sheet. ``True`` when it did.

    Called after the dock has been made visible in its plot. At the desk it
    returns ``False`` and changes nothing.
    """
    if dock is None or not phone_layout.is_on():
        return False
    win = dock.window()
    if win is None or win is dock:
        return False
    _close_controls_sheets(win)
    sheet = _sheet_for(win)
    lent = sheet._aw_lent
    if lent is not None and lent[0] is not dock:
        sheet.closeNow()
        _give_back(sheet)
    if sheet._aw_lent is None:
        _lend(sheet, dock)
    dock.setVisible(True)
    sheet.open()
    sheet.raise_()
    return True
