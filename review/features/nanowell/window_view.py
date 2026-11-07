"""The Distribution window's strip and the pop-out's controls for it.

The **strip** sits between the control band and the figure, shown on the Distribution view while a window exists
for the metric and axis drawn: the sentence ``_window_controller.strip_text`` writes, on a line of its own the width of
the strip, and beneath it the controls — typed **Low** and **High** fields (precision, and a way to edit on a phone,
where dragging is clumsy), the *every | any timepoint* choice, **This panel only**, **Open in pop-out**, **Use as
Subset** and **✕** — in a flow that wraps onto as many lines as the width needs, so none is cut off on a phone. Each
control writes the window through the controller; the strip reads it back through ``_nw_window_refresh``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from review.features.nanowell import _window_controller as wnc
from review.features.nanowell.state import POPOUT_LAYOUTS
from widgets.flow_layout import FlowLayout, flow_group, sync_flow_height

_RANGE_TIP = "The window's range of the metric, both ends inclusive. Type a number and press Enter."
_RULE_TIP = ("Over several timepoints: keep a nanowell only if it is inside the range at every covered timepoint, or "
             "if it is inside at any one of them. Both counts are in the sentence.")
_PANEL_TIP = "Cover only the panel the window was drawn in, rather than every panel."
_POPOUT_TIP = "Show the nanowells inside the window in the pop-out, as rows of crops over time."
_SUBSET_TIP = ("Keep only the nanowells inside the window: they are added to the Subset, applied at once, and the "
               "others leave the Distribution (and every view the Subset governs). The Subset holds one window at a "
               "time: drawing another box takes this one out first, so the new box selects from every nanowell. The "
               "Subset's editor lists it and removes it.")
_CLEAR_TIP = "Clear the window"
#: The widest the strip's Low and High fields grow, in pixels: a number of a few digits.
_FIELD_MAX_W = 80


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def build_window_strip(ctx, parent) -> QWidget:
    """The strip; registers ``_nw_window_label``, ``_nw_window_lo``, ``_nw_window_hi``, ``_nw_window_rule_cb``,
    ``_nw_window_panel_box``, ``_nw_window_popout_btn``, ``_nw_window_clear_btn`` and ``_nw_window_refresh``."""
    strip = QWidget(parent)
    strip.setObjectName("NanowellWindowStrip")
    # The sentence takes the strip's whole width on a line of its own, and the controls flow beneath it, wrapping onto
    # as many lines as the width needs: one row of a sentence and eight controls was cut off at phone width. Each
    # caption travels with its field, so a wrap never strands "Low" from its box.
    column = QVBoxLayout(strip)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    label = QLabel("", strip)
    label.setWordWrap(True)
    column.addWidget(label)
    controls_host = QWidget(strip)
    row = FlowLayout(controls_host, h_spacing=8)
    sync_flow_height(controls_host)
    column.addWidget(controls_host)
    low, high = QLineEdit(controls_host), QLineEdit(controls_host)
    for field in (low, high):
        field.setToolTip(_RANGE_TIP)
        field.setMaximumWidth(_FIELD_MAX_W)
    rule = QComboBox(controls_host)
    rule.addItem("every timepoint", "every")
    rule.addItem("any timepoint", "any")
    rule.setToolTip(_RULE_TIP)
    panel_only = QCheckBox("This panel only", controls_host)
    panel_only.setToolTip(_PANEL_TIP)
    popout = QPushButton("Open in pop-out", controls_host)
    popout.setProperty("variant", "secondary")
    popout.setToolTip(_POPOUT_TIP)
    use_subset = QPushButton("Use as Subset", controls_host)
    use_subset.setProperty("variant", "secondary")
    use_subset.setToolTip(_SUBSET_TIP)
    clear = QPushButton("✕", controls_host)
    clear.setProperty("variant", "secondary")
    clear.setToolTip(_CLEAR_TIP)
    for caption, field in (("Low", low), ("High", high)):
        pair = QWidget(controls_host)
        inner = QHBoxLayout(pair)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(4)
        inner.addWidget(QLabel(caption, pair))
        inner.addWidget(field)
        field.setParent(pair)
        row.addWidget(pair)
    for widget in (rule, panel_only, popout, use_subset, clear):
        row.addWidget(widget)
    controls = (low, high, rule, panel_only)

    def refresh() -> None:
        st = _state(ctx)
        window = wnc.current(st)[0] if st is not None else None
        strip.setVisible(st is not None and st.view == "distribution" and window is not None)
        if window is None:
            return
        blocked = [w.blockSignals(True) for w in controls]
        try:
            label.setText(wnc.strip_text(st))
            low.setText(f"{window.lo:.6g}")
            high.setText(f"{window.hi:.6g}")
            rule.setCurrentIndex(rule.findData(window.rule))
            rule.setVisible(window.axis == "time" and len(window.keys) > 1)
            panel_only.setChecked(window.panel_only)
        finally:
            for widget, was in zip(controls, blocked):
                widget.blockSignals(was)

    def range_typed() -> None:
        try:
            lo, hi = float(low.text()), float(high.text())
        except ValueError:
            refresh()                                    # not a number: put the window's own back
            return
        wnc.set_window_range(ctx, lo, hi)

    low.editingFinished.connect(range_typed)
    high.editingFinished.connect(range_typed)
    rule.currentIndexChanged.connect(lambda _i: wnc.set_window_rule(ctx, rule.currentData()))
    panel_only.toggled.connect(lambda on: wnc.set_window_panel_only(ctx, on))
    popout.clicked.connect(lambda _=False: wnc.open_window_popout(ctx))
    use_subset.clicked.connect(lambda _=False: wnc.use_window_as_subset(ctx))
    clear.clicked.connect(lambda _=False: wnc.clear_window(ctx))
    reg = ctx.widgets
    reg.register("_nw_window_label", label)
    reg.register("_nw_window_lo", low)
    reg.register("_nw_window_hi", high)
    reg.register("_nw_window_rule_cb", rule)
    reg.register("_nw_window_panel_box", panel_only)
    reg.register("_nw_window_popout_btn", popout)
    reg.register("_nw_window_subset_btn", use_subset)
    reg.register("_nw_window_clear_btn", clear)
    reg.register("_nw_window_refresh", refresh)
    strip.hide()
    return strip


_SOURCE_TIP = "What the pop-out shows: the selected nanowell over time, or the nanowells inside the Distribution window."
_LAYOUT_TIP = ("How the window's nanowells are laid out: one row of crops over time for each (Rows), or all at once "
               "as an artificial hex array, a patch for each source well, once per timepoint chosen in Timepoints, side by "
               "side (Hex).")
_LAYOUT_NAMES = {"rows": "Rows", "hex": "Hex"}
_SORT_TIP = ("The order the hex composite's tiles are placed in, filling each well's patch row by row from its top "
             "left: the window's rank, or a metric's value at the first timepoint shown. The title says which.")
_ORDER_TIP = "Largest first (descending) or smallest first (ascending)."
_LABEL_TIP = ("A metric written in each tile's top-left corner, at the timepoint of the panel it is in (a dash where "
              "the nanowell has no value). Zoom in to read it on a full page.")
_COLOUR_TIP = ("What the stripe under each tile of the hex composite says, keyed under the title: the tile's source "
               "well, its well's value of a sample variable (the same colours that variable has on every plot), or "
               "nothing.")


def build_popout_window_controls(ctx, window) -> QHBoxLayout:
    """The pop-out's row for the window: **Show** (this nanowell | the window), **Layout**, and ◀ Page k of n ▶. The
    hex composite's timepoints are the **Timepoints** menu's, drawn side by side (``popout_view``), so there is no
    stepper here. While the window is shown, the row above's **Go to nanowell** is hidden: it names one nanowell, and
    the figure is of many from several wells. **Colour** (the hex layout only) chooses what the composite's stripes say, and **Sort** and its
    order what its tiles are placed by, **Corner** the metric written in each tile's corner.
    Registers ``_nw_popout_source_cb``, ``_nw_popout_layout_cb``, ``_nw_popout_colour_cb``, ``_nw_popout_page_prev``,
    ``_nw_popout_page_label``, ``_nw_popout_page_next`` and ``_nw_popout_window_refresh``."""
    source = QComboBox(window)
    source.addItem("This nanowell", "selected")
    source.addItem("Window", "window")
    source.setToolTip(_SOURCE_TIP)
    layout = QComboBox(window)
    for key in POPOUT_LAYOUTS:
        layout.addItem(_LAYOUT_NAMES[key], key)
    layout.setToolTip(_LAYOUT_TIP)
    colour = QComboBox(window)
    colour.setToolTip(_COLOUR_TIP)
    colour_caption = QLabel("Colour", window)
    sort = QComboBox(window)
    sort.setToolTip(_SORT_TIP)
    for label, key in wnc.composite_sort_choices():
        sort.addItem(label, key)
    order = QComboBox(window)
    order.setToolTip(_ORDER_TIP)
    order.addItem("Descending", True)
    order.addItem("Ascending", False)
    sort_caption = QLabel("Sort", window)
    corner = QComboBox(window)
    corner.setToolTip(_LABEL_TIP)
    for label, key in wnc.composite_label_choices():
        corner.addItem(label, key)
    corner_caption = QLabel("Corner", window)
    prev, following = QToolButton(window), QToolButton(window)
    prev.setText("◀")
    following.setText("▶")
    page = QLabel("", window)
    pager = (prev, page, following)
    row = QHBoxLayout()
    show_caption, layout_caption = QLabel("Show", window), QLabel("Layout", window)
    row.addWidget(flow_group(window, show_caption, source))
    layout_group = flow_group(window, layout_caption, layout)
    row.addWidget(layout_group)
    # Each caption travels with its box in a group, and a group is hidden whole so no caption is left without its box;
    # the controls inside are hidden with it, so each reads as hidden to whoever holds it.
    hex_groups = (flow_group(window, colour_caption, colour), flow_group(window, sort_caption, sort, order),
                  flow_group(window, corner_caption, corner))
    hex_only = (colour_caption, colour, sort_caption, sort, order, corner_caption, corner)
    for group in hex_groups:
        row.addWidget(group)
    for widget in pager:
        row.addWidget(widget)

    def refresh() -> None:
        st = _state(ctx)
        if st is None:
            return
        in_window = st.popout_source == "window"
        result = wnc.current(st)[1]
        blocked = [w.blockSignals(True) for w in (source, layout, colour, sort, order, corner)]
        try:
            source.setItemText(1, f"Window ({len(result.rows):,})" if result is not None and result.rows else "Window")
            source.setCurrentIndex(source.findData(st.popout_source))
            layout.setCurrentIndex(layout.findData(st.popout_layout))
            for widget in (layout_group, layout_caption, layout):   # a layout for the window's nanowells: none for one
                widget.setVisible(in_window)
            colour.clear()
            for label, key in wnc.composite_colour_choices(ctx):
                colour.addItem(label, key)
            colour.setCurrentIndex(max(0, colour.findData(wnc.composite_colour_key(ctx, st))))
            sort.setCurrentIndex(max(0, sort.findData(st.composite_sort)))
            order.setCurrentIndex(order.findData(bool(st.composite_desc)))
            corner.setCurrentIndex(max(0, corner.findData(st.composite_label)))
        finally:
            for widget, was in zip((source, layout, colour, sort, order, corner), blocked):
                widget.blockSignals(was)
        for widget in hex_groups + hex_only:
            widget.setVisible(in_window and st.popout_layout == "hex")
        pages = wnc.page_count(st)
        page.setText(f"Page {st.window_page + 1} of {pages}")
        for widget in pager:
            widget.setVisible(in_window and pages > 1)
        zoom = ctx.widgets.get_or_none("_nw_popout_zoom_refresh")
        if callable(zoom):
            zoom()
        go_to = ctx.widgets.get_or_none("_nw_popout_go_to_btn")
        if go_to is not None:
            go_to.setVisible(not in_window)              # one nanowell's button: no meaning over the window's many

    source.currentIndexChanged.connect(lambda _i: wnc.set_popout_source(ctx, source.currentData()))
    layout.currentIndexChanged.connect(lambda _i: wnc.set_popout_layout(ctx, layout.currentData()))
    colour.currentIndexChanged.connect(lambda _i: wnc.set_composite_colour(ctx, colour.currentData()))
    sort.currentIndexChanged.connect(lambda _i: wnc.set_composite_sort(ctx, sort.currentData()))
    order.currentIndexChanged.connect(lambda _i: wnc.set_composite_desc(ctx, order.currentData()))
    corner.currentIndexChanged.connect(lambda _i: wnc.set_composite_label(ctx, corner.currentData()))
    prev.clicked.connect(lambda _=False: wnc.step_window_page(ctx, -1))
    following.clicked.connect(lambda _=False: wnc.step_window_page(ctx, 1))
    reg = ctx.widgets
    reg.register("_nw_popout_source_cb", source)
    reg.register("_nw_popout_layout_cb", layout)
    reg.register("_nw_popout_colour_cb", colour)
    reg.register("_nw_popout_sort_cb", sort)
    reg.register("_nw_popout_order_cb", order)
    reg.register("_nw_popout_label_cb", corner)
    reg.register("_nw_popout_page_prev", prev)
    reg.register("_nw_popout_page_label", page)
    reg.register("_nw_popout_page_next", following)
    reg.register("_nw_popout_window_refresh", refresh)
    refresh()
    return row


_HIDE_TIP = "Hide the pop-out's control rows to give the figure the whole window; show them again here."
_RESET_ZOOM_TIP = ("Back to the whole composite: undoes the wheel's zoom and a drag's pan. Greyed while the view is "
                   "already whole; the Hex layout only.")


def build_popout_chrome(ctx, window, rows) -> QHBoxLayout:
    """The bar above the pop-out's control rows, always visible: **Hide controls** (and **Show controls**) folds
    the rows away — the figure takes the whole window — and **Reset zoom** returns the hex composite to the whole
    of it after the wheel or a drag moved it. *rows* is the widget holding the control rows.

    Registers ``_nw_popout_controls_btn``, ``_nw_popout_reset_zoom_btn`` and ``_nw_popout_zoom_refresh`` (the reset
    button shown for the hex layout in Window mode and enabled while the view is not whole — the controller calls it
    whenever the zoom, the layout or the source changes)."""
    toggle = QToolButton(window)
    toggle.setCheckable(True)
    toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
    toggle.setProperty("variant", "secondary")
    toggle.setToolTip(_HIDE_TIP)
    reset = QPushButton("Reset zoom", window)
    reset.setProperty("variant", "secondary")
    reset.setToolTip(_RESET_ZOOM_TIP)

    def set_hidden(hidden: bool) -> None:
        rows.setVisible(not hidden)
        toggle.setText("Show controls ▸" if hidden else "Hide controls ▾")

    def zoom_refresh() -> None:
        st = _state(ctx)
        hex_shown = st is not None and st.popout_source == "window" and st.popout_layout == "hex"
        reset.setVisible(hex_shown)
        reset.setEnabled(hex_shown and st.composite_view is not None)

    toggle.toggled.connect(set_hidden)
    reset.clicked.connect(lambda _=False: wnc.reset_composite_zoom(ctx))
    reg = ctx.widgets
    reg.register("_nw_popout_controls_btn", toggle)
    reg.register("_nw_popout_reset_zoom_btn", reset)
    reg.register("_nw_popout_zoom_refresh", zoom_refresh)
    set_hidden(False)
    zoom_refresh()
    bar = QHBoxLayout()
    bar.addWidget(toggle)
    bar.addStretch(1)
    bar.addWidget(reset)
    return bar
