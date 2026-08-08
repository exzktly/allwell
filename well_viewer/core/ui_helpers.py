"""Reusable Qt UI helpers (button factories, scroll area, name dialog, etc.)."""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)
import theme
from well_viewer.data.image_identity import normalize_tp
from well_viewer.core.safe import swallowed


def well_plate_tooltip_provider(ctx) -> Callable[[str], str]:
    """Tooltip-provider closure for ``WellPlateSelector.setWellTooltipProvider``.

    Returns ``"<well_id> — <label>"`` for wells the user has named in
    Sample Definitions, falling back to the bare well id otherwise. The
    closure reads ``ctx.state.dataset.well_labels`` on every hover
    so a label edit reflects immediately without re-wiring the provider.
    """
    def _tooltip(well_id: str) -> str:
        try:
            labels = getattr(ctx.state.dataset, "well_labels", None) or {}
            name = labels.get(well_id)
        except Exception:
            name = None
        if name and str(name).strip() and str(name).strip() != well_id:
            return f"{well_id} — {str(name).strip()}"
        return well_id
    return _tooltip

def parse_float_edit(edit, fallback=None, *, allow_auto: bool = False):
    """Parse a ``QLineEdit``'s text as ``float``; return *fallback* on blank,
    non-numeric, or (when *allow_auto*) the literal ``"auto"``.

    Consolidates the ``try: float(edit.text().strip()) except: fallback`` helper
    re-implemented across several feature views. A non-widget / ``None`` *edit*
    also yields *fallback* (so ``getattr(app, attr, None)`` can be passed
    directly).
    """
    try:
        txt = str(edit.text()).strip()
    except (AttributeError, TypeError):
        return fallback
    if allow_auto and txt.lower() == "auto":
        return fallback
    if not txt:
        return fallback
    try:
        return float(txt)
    except ValueError:
        return fallback


def set_widget_text(host, attr_name: str, text: str) -> None:
    """Set ``text`` on ``app.<attr_name>`` if that attribute exists and is a
    text widget — collapses the repeated ``lbl = getattr(...); if lbl: ...``
    status-setter pattern."""
    w = getattr(host, attr_name, None)
    if hasattr(w, "setText"):
        w.setText(text)


def set_combo_values(combo: object, values: list[str]) -> None:
    """Set combobox values for both Qt and legacy widget shims.

    Preserves the current selection when the new item list contains it, so a
    tk-style ``currentIndexChanged`` handler that calls back into a refresh
    function doesn't clobber the user's pick as a side-effect.

    Lives in ui_helpers (not runtime_app) so the channel-state
    controller can use it without creating a circular import back into
    the runtime monolith.
    """
    vals = [str(v) for v in values]
    if hasattr(combo, "clear") and hasattr(combo, "addItems"):
        block = getattr(combo, "blockSignals", None)
        prev_text = combo.currentText() if hasattr(combo, "currentText") else ""
        if callable(block):
            block(True)
        combo.clear()  # type: ignore[attr-defined]
        combo.addItems(vals)  # type: ignore[attr-defined]
        if prev_text and prev_text in vals and hasattr(combo, "setCurrentIndex"):
            combo.setCurrentIndex(vals.index(prev_text))  # type: ignore[attr-defined]
        if callable(block):
            block(False)
        return
    combo["values"] = vals  # type: ignore[index]


def set_combo_values_sectioned(combo: object, sections: list[tuple[str, list[str]]]) -> None:
    """Populate *combo* with grouped values under non-selectable section headers.

    ``sections`` is a list of ``(header, values)``. A ``header`` of ``""`` emits
    its values with no header row (the default/top group). Header rows are
    inserted as disabled items on the combo's own ``QStandardItemModel`` — the
    same mechanism :func:`populate_metric_combo` uses to grey out
    ``smFISH Count`` — so the user cannot select them and ``findText`` /
    ``currentText`` / ``setCurrentText`` operate on the real values exactly as
    with :func:`set_combo_values`. The previous selection is preserved when it
    still exists; otherwise the first selectable value is chosen.

    Empty groups (no values) are skipped, so a header never appears without
    entries beneath it.
    """
    if combo is None or not (hasattr(combo, "clear") and hasattr(combo, "addItem")):
        return
    prev_text = combo.currentText() if hasattr(combo, "currentText") else ""
    block = getattr(combo, "blockSignals", None)
    if callable(block):
        block(True)
    try:
        combo.clear()
        selectable: list[str] = []
        for header, values in sections:
            vals = [str(v) for v in (values or [])]
            if not vals:
                continue
            if header:
                combo.addItem(f"— {header} —")
                model = combo.model() if hasattr(combo, "model") else None
                item = model.item(combo.count() - 1) if model is not None else None
                if item is not None:
                    # Disabled → not selectable / skipped on keyboard navigation.
                    item.setEnabled(False)
                    item.setData("header", Qt.UserRole + 1)
            for v in vals:
                combo.addItem(v)
                selectable.append(v)
        target = prev_text if (prev_text and prev_text in selectable) else (
            selectable[0] if selectable else "")
        if target and hasattr(combo, "findText"):
            idx = combo.findText(target)
            if idx >= 0 and hasattr(combo, "setCurrentIndex"):
                combo.setCurrentIndex(idx)
    finally:
        if callable(block):
            block(False)


def wrap_with_empty_state(
    win: Any,
    host: QWidget,
    *,
    text: str = "Load a directory.",
    hint: str = "Open… is at the top of the left rail (⌘O).",
    icon: str = "line-chart",
) -> QStackedWidget:
    """Wrap *host* (a plot canvas / scroll area / card) in a ``QStackedWidget``
    so an :class:`EmptyState` placeholder can render in its place until a
    dataset is loaded.

    Page 0 is the EmptyState; page 1 is *host*. The stack starts on page 0
    so the user sees the placeholder immediately on win launch (matplotlib's
    default empty axes are otherwise visible as glaring white blocks).

    The stack registers itself on ``win._plot_empty_stacks`` and
    ``win._refresh_plot_empty_states()`` flips every registered stack based
    on whether ``win._app_state.dataset.well_paths`` is non-empty. Tab redraws should call
    that helper before they paint.
    """
    from widgets.empty_state import EmptyState  # local — widget module is heavy
    stack = QStackedWidget()
    stack.setObjectName("PlotEmptyStateStack")
    placeholder = EmptyState(text=text, icon=icon, hint=hint)
    stack.addWidget(placeholder)  # index 0
    stack.addWidget(host)         # index 1
    stack.setCurrentIndex(0)
    if not hasattr(win, "_plot_empty_stacks") or win._plot_empty_stacks is None:
        win._plot_empty_stacks = []
    win._plot_empty_stacks.append(stack)
    return stack


#: Object names of per-tab control-bar containers that act on the loaded
#: dataset. They are greyed out until a dataset is present (see
#: ``refresh_plot_empty_states``) so the empty state isn't a wall of live-looking
#: controls that do nothing. ``TabCtrl`` is the marker a tab's own control row
#: uses; ``ControlBand`` is the shared band (``widgets/control_band.py``), so a
#: tab picks the gating up by adopting it rather than by remembering a name.
_DATA_GATED_OBJECT_NAMES = ("TabCtrl", "ControlBand")


def refresh_plot_empty_states(win: Any) -> None:
    """Flip every registered plot-empty-state stack based on whether a
    dataset is currently loaded. Safe to call before any stacks have been
    registered (no-ops)."""
    stacks = getattr(win, "_plot_empty_stacks", None) or []
    has_data = bool(win._app_state.dataset.well_paths)
    target = 1 if has_data else 0
    for stack in stacks:
        try:
            if stack.currentIndex() != target:
                stack.setCurrentIndex(target)
        except Exception:
            swallowed()  # was: pass (silent except)
    # Grey out the per-tab control bars until a dataset is loaded. Disabling the
    # container cascades to its children; re-enabling restores each child's own
    # enabled state (so tab-local logic like smFISH's combo gating still wins).
    if isinstance(win, QWidget):
        for name in _DATA_GATED_OBJECT_NAMES:
            for bar in win.findChildren(QWidget, name):
                try:
                    bar.setEnabled(has_data)
                except Exception:
                    swallowed()


def build_section_header(
    parent: QWidget,
    title: str,
    *,
    hint: str | None = None,
    buttons: tuple = (),
    margins: tuple = (8, 4, 8, 4),
) -> QWidget:
    """Return a "Sidebar"-styled header row with a section title.

    ``buttons`` is a sequence of already-constructed QWidgets that are added
    after a stretch. ``hint`` is an optional muted label shown right after
    the title.
    """
    hdr = QWidget(parent)
    hdr.setObjectName("Sidebar")
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(*margins)
    lbl = QLabel(title, hdr)
    lbl.setProperty("role", "section")
    # Word-wrap the title so a long one (e.g. "COMPARISON GROUPS" with two action
    # buttons) does NOT report its full one-line width as the header's minimum —
    # that made the row wider than a narrow rail, which then either clipped the
    # buttons or forced the whole sidebar wider than its peers. With wrap on, the
    # title's minimum is its longest word, so the row fits the rail and the title
    # wraps to a second line only when there isn't room for one line.
    lbl.setWordWrap(True)
    if hint:
        hl.addWidget(lbl)
        hint_lbl = QLabel(hint, hdr)
        hint_lbl.setObjectName("Muted")
        # Word-wrap so a long inline hint doesn't report its full text width as
        # the header's minimum — which forced the whole (non-h-scrolling) Cell
        # Gating pane wider than a narrow window and clipped its controls. Given
        # a stretch factor so it takes the row's leftover width and wraps into it.
        hint_lbl.setWordWrap(True)
        hl.addWidget(hint_lbl, 1)
    else:
        # The title takes the stretch so the buttons sit at the right edge; being
        # the stretchy item, it also absorbs the shrink and wraps when narrow.
        hl.addWidget(lbl, 1)
    for btn in buttons:
        hl.addWidget(btn)
    return hdr


def wrap_ctrl_row_with_properties(ctrl_row, on_click, *,
                                  tooltip="Show / hide the figure properties panel"):
    """Wrap a plot tab's control row so the figure-properties button pins to the
    right edge of the plot area — aligned with where the export-style dock slides
    out (``make_plot_with_right_dock``).

    The control rows are ``FlowLayout``s, whose ``addStretch()`` and per-widget
    alignment are no-ops (widgets/flow_layout.py), so the button cannot be
    right-aligned inside them. Lift it into an outer ``QHBoxLayout``:
    ``[ctrl_row (stretch 1)] [properties IconButton, top-right]``. ``AlignTop``
    keeps the button in the top-right corner when the FlowLayout wraps to
    multiple lines. ``on_click`` is invoked with no args on click.
    """
    from widgets.icon_button import IconButton
    wrap = QWidget()
    hl = QHBoxLayout(wrap)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(4)
    hl.addWidget(ctrl_row, 1)
    btn = IconButton("sliders-horizontal", wrap, tooltip=tooltip)
    btn.clicked.connect(lambda _=False: on_click())
    hl.addWidget(btn, 0, Qt.AlignTop | Qt.AlignRight)
    return wrap


def make_properties_button(ctx, parent: QWidget, figure_key: str, *,
                           tooltip="Show / hide the figure properties panel"):
    """The Properties toggle, for a tab whose controls are a ``ControlBand``.

    The band right-aligns its action zone itself, so a tab on the band adds this
    with ``band.add_action(...)`` and needs none of the
    :func:`wrap_ctrl_row_with_properties` scaffolding that a ``FlowLayout`` row
    required to get a button to the right edge.
    """
    from widgets.icon_button import IconButton
    btn = IconButton("sliders-horizontal", parent, tooltip=tooltip)
    btn.clicked.connect(lambda _=False: ctx.figures.open_style_panel(figure_key))
    return btn


#: Registry key of the Plotting page's single control band, built by
#: ``core/centre_view.py`` before any of the page's leaves.
PLOTTING_BAND_KEY = "_plotting_band"


def plotting_slot_name(title: str, mode: str | None = None) -> str:
    """The Plotting band's slot name for sub-tab *title* in *mode*.

    Most sub-tabs are one leaf and one slot. Scatter is two leaves behind one
    sub-tab — a Cells page and a Wells page it switches between — so its slots
    are named per mode, and the page shows whichever the mode selects.
    """
    return f"{title}/{mode}" if mode else title


def plotting_band_slot(ctx, title: str, mode: str | None = None):
    """The Plotting band's :class:`~widgets.control_band.ControlBandSlot` for a leaf.

    The five Plotting sub-tabs share one band. The controls every one of them
    scopes by — channel, property, threshold — are added to the band when the
    page is built; each leaf adds its own on top through a slot of its own, and
    the page shows one leaf's slot at a time. A leaf that built its own band
    instead put a second strip of chrome under the first.

    Falls back to a free-standing band if the page band is missing, so a leaf
    built in isolation (a unit test, a one-off harness) still gets a working
    control row rather than an ``AttributeError``.
    """
    from widgets.control_band import ControlBand
    band = ctx.widgets.get_or_none(PLOTTING_BAND_KEY)
    if band is None:
        band = ControlBand()
    return band.slot(plotting_slot_name(title, mode))


def build_hline_separator(parent: QWidget, *, thickness: int = theme.Metrics.hairline) -> QFrame:
    """Return the project's standard horizontal separator frame.

    The ``Separator`` object name is what makes it paint: ``theme.qss`` gives
    that name ``background-color: border_subtle`` and — the load-bearing half —
    ``border: none``. An ``HLine`` frame without one of the sanctioned names
    keeps Qt's default etched border instead, which is derived from the widget
    palette rather than the theme and measures 3 px of near-white
    (``#e6e9ef``) on this app's dark surfaces.

    *thickness* exists for the one divider that is deliberately heavier than a
    hairline (the Image Table's shelf rule); everything else takes the default.
    """
    sep = QFrame(parent)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(thickness)
    return sep


def build_vline_separator(parent: QWidget, *, thickness: int = theme.Metrics.hairline) -> QFrame:
    """The vertical twin of :func:`build_hline_separator`."""
    sep = QFrame(parent)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.VLine)
    sep.setFixedWidth(thickness)
    return sep


def clear_layout(layout) -> None:
    """Remove every widget and sub-layout from *layout*, freeing them."""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)


def _btn(parent: QWidget | None, text: str, command: Callable[[], Any] | None,
         variant: str, *, icon: str | None = None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setProperty("variant", variant)
    if icon:
        try:
            from widgets import icons as _icons
            dpr = b.devicePixelRatioF() if hasattr(b, "devicePixelRatioF") else 1.0
            b.setIcon(_icons.make_icon(icon, 14, dpr=dpr or 1.0))
        except Exception:
            swallowed()  # was: pass (silent except)
    if command is not None:
        b.clicked.connect(lambda _=False: command())
    return b


def btn_primary(parent: QWidget, text: str, command, *, padx: int = 8, pady: int = 2,
                icon: str | None = None, **_kw) -> QPushButton:
    return _btn(parent, text, command, "primary", icon=icon)


def btn_secondary(parent: QWidget, text: str, command, *, padx: int = 6, pady: int = 2,
                  icon: str | None = None, **_kw) -> QPushButton:
    return _btn(parent, text, command, "secondary", icon=icon)


def btn_card(parent: QWidget, text: str, command, *, padx: int = 4, **_kw) -> QPushButton:
    return _btn(parent, text, command, "card")


def btn_danger(parent: QWidget, text: str, command, *, padx: int = 4, **_kw) -> QPushButton:
    return _btn(parent, text, command, "danger")


def make_scrollable_canvas(parent: QWidget, **_kw) -> tuple[QScrollArea, QWidget]:
    """Return (scroll_area, inner_widget). Caller lays widgets inside inner."""
    sa = QScrollArea(parent)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    inner = QWidget(sa)
    sa.setWidget(inner)
    QVBoxLayout(inner)
    return sa, inner


class _PlotDockHost(QWidget):
    """Container that hosts the plot area + a right-side dock as layout
    siblings.

    Earlier the dock floated as a child overlay so that opening it never
    resized matplotlib. That design clipped the dock when the host was
    narrower than the panel's fixed width — content (incl. the close button)
    fell off the window's right edge. We now lay the dock as a real layout
    sibling: opening it does shrink the plot (a one-shot matplotlib redraw),
    but the panel always renders at its full width with its close button
    reachable.

    ``set_overlay_dock`` is kept as a compatibility shim so existing callers
    that pass a width hint still update the dock's fixed width.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay_dock: QWidget | None = None

    def set_overlay_dock(self, dock: QWidget, width: int = 0) -> None:
        self._overlay_dock = dock
        if width > 0:
            dock.setFixedWidth(int(width))


def make_plot_with_right_dock(parent: QWidget) -> tuple[QWidget, QVBoxLayout, QWidget]:
    """Build a plot area with a right-side dock as a layout sibling inside
    ``parent``.

    Returns ``(plot_area, plot_layout, right_dock)`` — callers lay their
    figure/toolbar into ``plot_layout`` and the export-style sidebar docks
    into ``right_dock``. ``right_dock`` is a layout sibling of ``plot_area``
    so toggling it visible does shrink the plot (and re-renders the figure
    once) — the trade-off accepted to keep the dock's full width on screen.
    """
    root = parent.layout()
    if root is None:
        root = QVBoxLayout(parent)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

    host = _PlotDockHost(parent)
    host_layout = QHBoxLayout(host)
    host_layout.setContentsMargins(0, 0, 0, 0)
    host_layout.setSpacing(0)

    plot_area = QWidget(host)
    plot_layout = QVBoxLayout(plot_area)
    plot_layout.setContentsMargins(0, 0, 0, 0)
    plot_layout.setSpacing(0)
    host_layout.addWidget(plot_area, 1)

    right_dock = QWidget(host)
    right_dock_layout = QVBoxLayout(right_dock)
    right_dock_layout.setContentsMargins(0, 0, 0, 0)
    right_dock_layout.setSpacing(0)
    right_dock.setVisible(False)
    right_dock._dock_host = host  # type: ignore[attr-defined]
    host_layout.addWidget(right_dock, 0)
    host.set_overlay_dock(right_dock, 0)

    root.addWidget(host, 1)
    return plot_area, plot_layout, right_dock


def mount_widget_in_plot_dock(dock: QWidget, widget: QWidget, width: int = 0) -> None:
    """Show *widget* in a plot tab's right *dock*, hiding any sibling panel
    already docked there.

    A plot tab's right dock (``make_plot_with_right_dock``) is a shared
    slide-out: the Export-Style / Properties sidebar and the Scatter cell viewer
    both live there. This keeps exactly one of them visible at a time, so the
    two coexist without overlapping — open one and the other slides away.
    """
    if dock is None or widget is None:
        return
    lay = dock.layout()
    if lay is None:
        return
    if lay.indexOf(widget) < 0:
        lay.addWidget(widget)
    for i in range(lay.count()):
        sibling = lay.itemAt(i).widget()
        if sibling is not None and sibling is not widget:
            sibling.hide()
    widget.show()
    host = getattr(dock, "_dock_host", None)
    if host is not None and hasattr(host, "set_overlay_dock"):
        host.set_overlay_dock(dock, width)
    dock.setVisible(True)


def hide_plot_dock(dock: QWidget) -> None:
    """Hide a plot tab's right dock (and whatever panel it currently holds)."""
    if dock is not None:
        dock.setVisible(False)


def send_plot_to_batch_export(win, mode: str) -> None:
    """S5: jump from a plot card to Batch Export with the matching chart-type.

    The batch panels read the live plot spec (channel / property / threshold /
    SEM / fold-change) straight off ``win._app_state``, so "capturing the
    current plot" reduces to navigating to the Batch Export tab and selecting
    the matching mode. Navigating builds the tab (so ``_batch_export_set_mode``
    exists) before we set it. Lives in this core helper — not in the
    batch_export feature — so the plot tab views can import it at module scope
    without a feature-to-feature cycle; it touches only ``win`` attributes.
    """
    nb = getattr(win, "_notebook", None)
    if nb is not None:
        nb.setCurrentByName("Batch Export")
    setter = win._ctx.runtime.batch_export.set_mode
    if callable(setter):
        setter(mode)
    label = {"line": "Line", "bar": "Bar", "scatter_cells": "Scatter (cells)",
             "scatter_agg": "Scatter (aggregate)"}.get(mode, mode)
    win._set_status(f"Batch Export → {label}: using the current plot settings.")


def _with_caret(text: str) -> str:
    """Append the drop-down caret unless the caller already wrote one.

    Both menu-button factories hide QToolButton's native menu indicator, on the
    grounds that the label carries a caret of its own. That was a claim about
    the call sites rather than something the helpers guaranteed, and six of them
    passed a bare "Export" — so those buttons dropped a menu with nothing on
    them to say so. Adding it here makes the comment true by construction.
    """
    text = str(text)
    return text if text.rstrip().endswith("▾") else f"{text} ▾"


def make_popover_button(text: str, parent: QWidget):
    """A compact toolbar button that opens a small panel ("popover") on click.

    Returns ``(button, content_widget, content_layout)`` — add controls to
    ``content_layout`` and they appear in the popup. Used to collapse rarely-
    changed plotting controls (e.g. Fold change) off the always-visible control
    bar (C1), trading one row of vertical space for one click.
    """
    from PySide6.QtWidgets import QToolButton, QMenu, QWidgetAction

    btn = QToolButton(parent)
    btn.setText(_with_caret(text))
    btn.setPopupMode(QToolButton.InstantPopup)
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    btn.setProperty("variant", "secondary")
    # The label carries the ▾ caret (added above if the caller left it off), so
    # hide QToolButton's native menu-indicator — the two would otherwise render
    # as overlapping double arrows.
    btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
    menu = QMenu(btn)
    content = QWidget(menu)
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(10, 8, 10, 8)
    content_layout.setSpacing(4)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(content)
    menu.addAction(wa)
    btn.setMenu(menu)
    return btn, content, content_layout


def make_action_menu_button(text: str, parent: QWidget, tooltip: str = ""):
    """A compact toolbar button that drops a menu of *actions* on click.

    Returns ``(button, menu)`` — add ``QAction``s (or use ``add_menu_action``)
    to ``menu``. Unlike :func:`make_popover_button` (which hosts live widgets in
    a panel), this is for click-and-close commands — Export CSV, Save figure,
    etc. Used to collapse a cluster of action buttons into one entry so a
    control row stays reachable when Split View narrows the pane.
    """
    from PySide6.QtWidgets import QToolButton, QMenu

    btn = QToolButton(parent)
    btn.setText(_with_caret(text))
    btn.setPopupMode(QToolButton.InstantPopup)
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    btn.setProperty("variant", "secondary")
    if tooltip:
        btn.setToolTip(tooltip)
    # The label carries the ▾ caret (added above if the caller left it off), so
    # hide the native indicator — mirrors make_popover_button.
    btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
    menu = QMenu(btn)
    btn.setMenu(menu)
    return btn, menu


def add_menu_action(menu, label: str, callback, *, tooltip: str = ""):
    """Add a simple ``QAction`` to *menu* that calls *callback* on trigger."""
    from PySide6.QtGui import QAction

    action = QAction(label, menu)
    if tooltip:
        action.setToolTip(tooltip)
    action.triggered.connect(lambda _=False: callback())
    menu.addAction(action)
    return action


def install_canvas_wheel_scroll(canvas, scroll_area) -> None:
    """Forward wheel events from a matplotlib canvas to ``scroll_area``.

    FigureCanvasQTAgg accepts wheel events for its own toolbar zoom, so a
    QScrollArea wrapping it never sees them. This installs a wheelEvent
    override that forwards the vertical wheel delta to the scroll area's
    vertical scrollbar when no modifier key is held.
    """
    orig_wheel = getattr(canvas, "wheelEvent", None)

    def _wheel(event):
        if event.modifiers() == Qt.NoModifier:
            vbar = scroll_area.verticalScrollBar()
            if vbar is not None and (vbar.maximum() - vbar.minimum()) > 0:
                vbar.setValue(vbar.value() - event.angleDelta().y())
                event.accept()
                return
        if callable(orig_wheel):
            orig_wheel(event)

    canvas.wheelEvent = _wheel


def ask_name_dialog(parent: QWidget, *, title: str, prompt: str, default: str,
                    width: int = 30, strip: bool = True, **_kw) -> str | None:
    text, ok = QInputDialog.getText(parent, title, prompt, QLineEdit.Normal, default)
    if not ok:
        return None
    if strip:
        text = text.strip()
        return text or None
    # Caller wants intentional whitespace preserved (e.g. Add Prefix /
    # Add Suffix on well labels). Still treat an empty box as a cancel.
    return text if text else None


_THEMED_NAV_CLS = None


def _themed_nav_toolbar_class():
    """Return a cached NavigationToolbar2QT subclass that recolors icons from theme tokens.

    The base class decides icon color from the QPalette background value, which
    QSS does not update — so in Light theme icons came out white on a styled
    light background. This subclass reads the current theme's ``TXT_PRI`` and
    repaints the icons directly.
    """
    global _THEMED_NAV_CLS
    if _THEMED_NAV_CLS is not None:
        return _THEMED_NAV_CLS

    from matplotlib import cbook
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

    class _ThemedNavToolbar(NavigationToolbar2QT):
        def _icon(self, name):
            path_regular = cbook._get_data_path("images", name)
            path_large = path_regular.with_name(
                path_regular.name.replace(".png", "_large.png")
            )
            filename = str(path_large if path_large.exists() else path_regular)
            pm = QPixmap(filename)
            pm.setDevicePixelRatio(self.devicePixelRatioF() or 1)
            try:
                import theme
                icon_color = QColor(theme.Colors.text_primary)
            except Exception:
                icon_color = self.palette().color(self.foregroundRole())
            mask = pm.createMaskFromColor(QColor("black"), Qt.MaskMode.MaskOutColor)
            pm.fill(icon_color)
            pm.setMask(mask)
            return QIcon(pm)

        def refresh_icons(self) -> None:
            """Rebuild action icons after a theme change."""
            for _text, _tip, image_file, callback in self.toolitems:
                if image_file is None:
                    continue
                action = self._actions.get(callback)
                if action is None:
                    continue
                action.setIcon(self._icon(image_file + ".png"))

    _THEMED_NAV_CLS = _ThemedNavToolbar
    return _ThemedNavToolbar


def make_band_controls(ctx, parent: QWidget, *, with_fov: bool = False) -> QWidget:
    """Build the shared "Error Band: SEM/SD" (+ optional "Spread: FOV") toggle
    widget. The SEM button is wired through ``app._toggle_sem`` and registered in
    ``app._sem_btns`` so ``_toggle_sem`` can update every instance; the FOV button
    (``with_fov=True``) goes through ``app._toggle_fov_replicates`` /
    ``app._fov_btns`` and is auto-disabled whenever replicate groups are active
    (``_refresh_fov_btn_state``). Used both by :func:`attach_plot_toolbar` (legacy
    toolbar) and by the v2 ``PlotCard`` controls row.

    A8: fully ctx-native — reads the SEM flag off ``ctx.state.plot``, wires the
    ctx-native ``core.plot_toggles`` handlers, and registers the buttons on the
    ``ctx.runtime.sem`` / ``ctx.runtime.fov`` banks (mirrors the batch-export
    controls row)."""
    from well_viewer.core.plot_toggles import (
        refresh_fov_btn_state,
        toggle_fov_replicates,
        toggle_sem,
    )
    w = QWidget(parent)
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)

    eb_lbl = QLabel("Error Band", w)
    eb_lbl.setObjectName("Muted")
    h.addWidget(eb_lbl)
    initial = bool(ctx.state.plot.use_sem)
    sem_btn = QPushButton("SEM" if initial else "SD", w)
    sem_btn.setProperty("variant", "sem" if initial else "sem_warn")
    sem_btn.clicked.connect(lambda _=False: toggle_sem(ctx))
    h.addWidget(sem_btn)
    if ctx.runtime.sem.btns is None:
        ctx.runtime.sem.btns = []
    ctx.runtime.sem.btns.append(sem_btn)
    if not ctx.runtime.sem.btn:
        ctx.runtime.sem.btn = sem_btn

    if with_fov:
        sp_lbl = QLabel("Spread", w)
        sp_lbl.setObjectName("Muted")
        h.addWidget(sp_lbl)
        fov_btn = QPushButton("FOV", w)
        fov_btn.setProperty("variant", "toggle")
        fov_btn.clicked.connect(lambda _=False: toggle_fov_replicates(ctx))
        h.addWidget(fov_btn)
        if ctx.runtime.fov.btns is None:
            ctx.runtime.fov.btns = []
        ctx.runtime.fov.btns.append(fov_btn)
        if not ctx.runtime.fov.btn:
            ctx.runtime.fov.btn = fov_btn
        refresh_fov_btn_state(ctx)

    h.addStretch(1)
    return w


def multiselect_button_text(prefix: str, selections: dict) -> str:
    """Self-describing text for a checkbox-menu multi-select button.

    Folds *prefix* and the selection count into one string
    ("Timepoints: 2/5 selected") for a band control whose button IS the
    label — used in place of a button + separate "(2/5 selected)" QLabel
    pair, the pattern ``LabeledComboBox`` uses for a plain combo.
    """
    total = len(selections)
    count = sum(1 for v in selections.values() if v)
    if not total:
        return f"{prefix}: 0 selected"
    if count == total:
        return f"{prefix}: All {count} selected"
    return f"{prefix}: {count}/{total} selected"


def apply_plot_card_decorators(ctx, card, tab_name: str) -> None:
    """Apply every feature-registered plot-card decorator to *card*.

    Decouples plot tabs from feature-specific card add-ons (e.g. Saved Plots'
    "save to gallery" hover button): a feature declares a ``plot_card_decorator``
    on its ``FeatureDescriptor`` and this fans it out. A profiled-out feature
    isn't in the registry, so its decorator never runs — and its module is never
    imported here, which is what lets a subset binary omit it entirely.
    """
    for descriptor in ctx.features.with_hook("plot_card_decorator"):
        try:
            descriptor.plot_card_decorator()(ctx, card, tab_name)
        except Exception:
            import logging
            logging.getLogger("well_viewer").exception(
                "plot-card decorator from %r failed", descriptor.name)


def attach_plot_toolbar(
    layout,
    canvas,
    parent: QWidget,
    app: Any = None,
    *,
    with_sem: bool = True,
    with_fov: bool = False,
) -> Any:
    """Create a themed matplotlib nav toolbar, append it to *layout* (at bottom),
    and optionally embed the shared :func:`make_band_controls` widget."""
    cls = _themed_nav_toolbar_class()
    toolbar = cls(canvas, parent)
    toolbar.setObjectName("PlotToolbar")
    if with_sem and app is not None:
        toolbar.addSeparator()
        toolbar.addWidget(make_band_controls(app._ctx, toolbar, with_fov=with_fov))
    layout.addWidget(toolbar)
    return toolbar


def refresh_plot_toolbar_icons(widget: QWidget) -> None:
    """Find every embedded ``PlotToolbar`` under *widget* and recolor its icons."""
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
    for tb in widget.findChildren(NavigationToolbar2QT):
        if tb.objectName() == "PlotToolbar" and hasattr(tb, "refresh_icons"):
            tb.refresh_icons()


# ── Thumbnail rendering ──────────────────────────────────────────────────────

def apply_log_scale(arr, lo, hi):
    """Map ``arr`` (and the LUT window ``lo``/``hi``) into log10 space.

    Single source of truth for the Image Table's log-intensity display, shared by
    the live thumbnail (:func:`make_fluor_thumb`) and the export composer
    (:func:`lut_colormap_to_rgb`) so the two render paths agree. Non-positive
    pixels are floored to the smallest positive value present (``1.0`` when there
    is none) so ``log10`` stays finite; ``lo``/``hi`` are floored the same way.
    ``None`` bounds pass through as ``None`` so the caller's auto-fit still works
    (now in log space). Returns ``(arr_log, lo_log, hi_log)``.
    """
    import numpy as _np

    a = _np.asarray(arr, dtype=_np.float32)
    pos = a[a > 0]
    floor = float(pos.min()) if pos.size else 1.0
    arr_log = _np.log10(_np.clip(a, floor, None))

    def _b(v):
        if v is None:
            return None
        return float(_np.log10(max(float(v), floor)))

    return arr_log, _b(lo), _b(hi)


def make_fluor_thumb(arr, sz_w: int, sz_h: int,
                     lo: float | None, hi: float | None,
                     crop=None,
                     tint: tuple[float, float, float] | None = None,
                     cmap: str | None = None,
                     log_scale: bool = False):
    """Render a greyscale float32 array as a QPixmap with LUT [lo, hi].

    Rendering selection (in priority order):
      * If ``cmap`` is a matplotlib colormap name (e.g. ``"viridis"``,
        ``"Reds"``, ``"Greens"``…), the LUT-clipped intensity is mapped
        through that colormap across its full gradient — low intensity →
        cmap(0.0), high intensity → cmap(1.0). This is the canonical
        multi-colour LUT path the Image Table uses.
      * Else if ``tint`` is an ``(r, g, b)`` triple, the LUT-clipped
        intensity is multiplied by the tint (legacy single-colour ramp).
      * Else the result is grayscale.

    When ``crop`` is a (y0, x0, y1, x1) tuple the array is sliced to that
    sub-region before LUT application and scaling.
    """
    import numpy as _np

    if arr is None:
        return None
    arr = _np.asarray(arr, dtype=_np.float32)
    if crop is not None:
        y0, x0, y1, x1 = crop
        ih, iw = arr.shape[:2]
        y0 = max(0, min(int(y0), ih))
        y1 = max(y0, min(int(y1), ih))
        x0 = max(0, min(int(x0), iw))
        x1 = max(x0, min(int(x1), iw))
        if y1 > y0 and x1 > x0:
            arr = arr[y0:y1, x0:x1]
    if log_scale:
        arr, lo, hi = apply_log_scale(arr, lo, hi)
    alo = lo if lo is not None else float(arr.min())
    ahi = hi if hi is not None else float(arr.max())
    if ahi <= alo:
        ahi = alo + 1.0
    norm = (_np.clip(arr, alo, ahi) - alo) / (ahi - alo)
    if cmap:
        try:
            from widgets.lut_cmaps import lut_cmap
            mpl_cmap = lut_cmap(cmap)
        except (KeyError, Exception):
            mpl_cmap = None
        if mpl_cmap is not None:
            mapped = mpl_cmap(norm)[..., :3]  # drop alpha
            rgb = (mapped * 255).astype(_np.uint8).copy()
        else:
            disp = (norm * 255).astype(_np.uint8)
            rgb = _np.stack([disp, disp, disp], axis=-1).copy()
    elif tint is not None:
        disp = (norm * 255).astype(_np.uint8)
        r, g, b = (max(0.0, min(1.0, float(c))) for c in tint)
        df = disp.astype(_np.float32)
        rgb = _np.stack([
            (df * r).astype(_np.uint8),
            (df * g).astype(_np.uint8),
            (df * b).astype(_np.uint8),
        ], axis=-1).copy()
    else:
        disp = (norm * 255).astype(_np.uint8)
        rgb = _np.stack([disp, disp, disp], axis=-1).copy()
    h, w, _ = rgb.shape
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    return pm.scaled(int(sz_w), int(sz_h), Qt.KeepAspectRatio, Qt.SmoothTransformation)


def lut_colormap_to_rgb(arr, lo: float | None = None,
                        hi: float | None = None, cmap_name: str = "",
                        log_scale: bool = False):
    """Map a 2-D array to an (H, W, 3) uint8 RGB via a [lo, hi] LUT window and an
    optional matplotlib colormap. ``lo``/``hi`` default to the array min/max;
    an empty/None ``cmap_name`` (or an unknown one) falls back to greyscale.

    ``log_scale`` maps the array + window into log10 space first (see
    :func:`apply_log_scale`) so the LUT spans the data logarithmically.

    Shared by the Image Table cell renderer and the Playground renderer so the
    LUT + colormap math has a single source of truth.
    """
    import numpy as _np

    a = _np.asarray(arr, dtype=_np.float32)
    if log_scale:
        a, lo, hi = apply_log_scale(a, lo, hi)
    alo = lo if lo is not None else float(a.min())
    ahi = hi if hi is not None else float(a.max())
    if ahi <= alo:
        ahi = alo + 1.0
    norm = _np.clip((a - alo) / (ahi - alo), 0.0, 1.0)
    if cmap_name:
        try:
            from widgets.lut_cmaps import lut_cmap
            return (lut_cmap(cmap_name)(norm)[..., :3] * 255).astype(_np.uint8).copy()
        except Exception:
            swallowed()  # was: pass (silent except)
    gray = (norm * 255).astype(_np.uint8)
    return _np.stack([gray, gray, gray], axis=-1)


def make_overlay_thumb(arr, sz_w: int, sz_h: int,
                       lo: float | None = None, hi: float | None = None,
                       crop=None):
    """Render a 2-D or 3-D array as a QPixmap scaled to (sz_w, sz_h).

    If ``lo``/``hi`` are given they override the per-image auto range so the
    Movie Montage overlay LUT controls can tune brightness/contrast. For 3-D
    RGB overlays the [lo, hi] window is applied uniformly across channels.
    When ``crop`` is supplied, the same (y0, x0, y1, x1) sub-region is used
    so overlay thumbnails align with the cropped fluorescence thumbnails.
    """
    import numpy as _np

    if arr is None:
        return None
    arr = _np.asarray(arr)
    if crop is not None:
        y0, x0, y1, x1 = crop
        ih, iw = arr.shape[:2]
        y0 = max(0, min(int(y0), ih))
        y1 = max(y0, min(int(y1), ih))
        x0 = max(0, min(int(x0), iw))
        x1 = max(x0, min(int(x1), iw))
        if y1 > y0 and x1 > x0:
            arr = arr[y0:y1, x0:x1]
    if arr.ndim == 2:
        arr_f = arr.astype(_np.float32)
        alo = lo if lo is not None else float(arr_f.min())
        ahi = hi if hi is not None else float(arr_f.max())
        if ahi <= alo:
            ahi = alo + 1.0
        disp = ((_np.clip(arr_f, alo, ahi) - alo) / (ahi - alo) * 255).astype(_np.uint8)
        rgb = _np.stack([disp, disp, disp], axis=-1).copy()
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        a = arr[:, :, :3].astype(_np.float32)
        alo = lo if lo is not None else float(a.min())
        ahi = hi if hi is not None else float(a.max())
        if ahi <= alo:
            ahi = alo + 1.0
        a = _np.clip((a - alo) / (ahi - alo) * 255.0, 0, 255).astype(_np.uint8)
        rgb = _np.ascontiguousarray(a)
    else:
        return None
    h, w, _ = rgb.shape
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    return pm.scaled(int(sz_w), int(sz_h), Qt.KeepAspectRatio, Qt.SmoothTransformation)


# ── Small value / widget helpers ─────────────────────────────────────────────

def safe_int_or_none(value: object) -> int | None:
    """Best-effort coercion of ``value`` to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def mute_color(hex_color: str, factor: float = 0.5) -> str:
    """Blend *hex_color* 50% toward ``theme.Colors.text_muted``.

    Used to dim hidden rep-sets on the plate map so they are visually
    distinct from visible ones while still showing their hue.
    """
    mh = theme.Colors.text_muted.lstrip("#")
    grey = (int(mh[0:2], 16), int(mh[2:4], 16), int(mh[4:6], 16))
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    mr = int(r + (grey[0] - r) * factor)
    mg = int(g + (grey[1] - g) * factor)
    mb = int(b + (grey[2] - b) * factor)
    return f"#{mr:02X}{mg:02X}{mb:02X}"



def apply_theme(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Apply the application-wide stylesheet.

    Single source of truth is the repo-root ``theme`` module. Previously
    this re-applied the legacy ``ui/theme`` per-theme QSS, which clobbered
    ``theme.qss()`` whenever a ``WellViewerApp`` was constructed (including
    embedded under AllWell).
    """
    try:
        import theme  # type: ignore
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.qss())
    except Exception:
        swallowed()  # was: pass (silent except)



def toast(ctx, msg: str, *, kind: str = "info", msec: int = 3500) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Show a transient v2 Toast over the main window.

    ``kind`` matches widgets.toast.Toast: 'info' / 'success' / 'warn' /
    'danger'. Falls back to _set_status if anything goes wrong so the
    message is never silently lost.
    """
    try:
        from widgets.toast import Toast
        Toast.show_message(app, msg, kind=kind, msec=msec)
    except Exception:
        swallowed()  # was: pass (silent except)
    app._set_status(msg)



def show_progress(ctx, maximum: int, msg: str = "") -> None:
    app = ctx.window  # transitional bridge (A3)
    """Display the progress bar and set its maximum value."""
    app._progress_bar.setValue(0)
    app._progress_bar.setMaximum(max(1, maximum))
    app._progress_bar.setVisible(True)
    if msg:
        app._set_status(msg)
    QApplication.processEvents()



def step_progress(ctx, value: int, msg: str = "") -> None:
    app = ctx.window  # transitional bridge (A3)
    """Advance the progress bar to *value* and repaint immediately."""
    app._progress_bar.setValue(value)
    if msg:
        app._set_status(msg)
    QApplication.processEvents()



def toggle_log(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    app._log_visible = not app._log_visible
    if app._log_visible:
        app._log_frame.setVisible(True)
        app._log_btn.setText("Log ▾")
    else:
        app._log_frame.setVisible(False)
        app._log_btn.setText("Log ▴")



def notes_schedule_save(ctx) -> None:
    """Capture the Notes text and queue a debounced save.

    The widget read must stay here (the registry never touches widgets);
    the debounce + disk write live in the PersistenceRegistry.
    """
    edit = ctx.widgets.get_or_none("_notes_edit")
    if edit is not None:
        ctx.state.selection.notes_text = edit.toPlainText()
    ctx.persistence.schedule_save("notes")



def norm_timepoint(ctx, value: object) -> str:
    """The ctx-shaped leaf over :func:`data.image_identity.normalize_tp`.

    A ctx signature so features can reach the canonical timepoint key through
    the context rather than importing the data layer directly; the rule itself
    lives in one place, because a second copy of it drifted once already (this
    one kept the truthiness test that turned a numeric ``0`` into ``""`` after
    the original had been fixed, and had to be fixed a second time to catch
    up)."""
    return normalize_tp(value)

