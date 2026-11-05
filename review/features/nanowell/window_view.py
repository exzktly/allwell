"""The Distribution window's strip and the pop-out's controls for it.

The **strip** is a row between the control band and the figure, shown on the Distribution view while a window exists
for the metric and axis drawn: the sentence ``_window_controller.strip_text`` writes, typed **Low** and **High**
fields (precision, and a way to edit on a phone, where dragging is clumsy), the *every | any timepoint* choice,
**This panel only**, **Open in pop-out** and **✕**. Each control writes the window through the controller; the strip
reads it back through ``_nw_window_refresh``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
                               QWidget)

from review.features.nanowell import _window_controller as wnc
from review.features.nanowell.state import POPOUT_LAYOUTS

_RANGE_TIP = "The window's range of the metric, both ends inclusive. Type a number and press Enter."
_RULE_TIP = ("Over several timepoints: keep a nanowell only if it is inside the range at every covered timepoint, or "
             "if it is inside at any one of them. Both counts are in the sentence.")
_PANEL_TIP = "Cover only the panel the window was drawn in, rather than every panel."
_POPOUT_TIP = "Show the nanowells inside the window in the pop-out, as rows of crops over time."
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
    row = QHBoxLayout(strip)
    row.setContentsMargins(0, 0, 0, 0)
    label = QLabel("", strip)
    label.setWordWrap(True)
    low, high = QLineEdit(strip), QLineEdit(strip)
    for field in (low, high):
        field.setToolTip(_RANGE_TIP)
        field.setMaximumWidth(_FIELD_MAX_W)
    rule = QComboBox(strip)
    rule.addItem("every timepoint", "every")
    rule.addItem("any timepoint", "any")
    rule.setToolTip(_RULE_TIP)
    panel_only = QCheckBox("This panel only", strip)
    panel_only.setToolTip(_PANEL_TIP)
    popout = QPushButton("Open in pop-out", strip)
    popout.setProperty("variant", "secondary")
    popout.setToolTip(_POPOUT_TIP)
    clear = QPushButton("✕", strip)
    clear.setProperty("variant", "secondary")
    clear.setToolTip(_CLEAR_TIP)
    row.addWidget(label, 1)
    for caption, field in (("Low", low), ("High", high)):
        row.addWidget(QLabel(caption, strip))
        row.addWidget(field)
    for widget in (rule, panel_only, popout, clear):
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
    clear.clicked.connect(lambda _=False: wnc.clear_window(ctx))
    reg = ctx.widgets
    reg.register("_nw_window_label", label)
    reg.register("_nw_window_lo", low)
    reg.register("_nw_window_hi", high)
    reg.register("_nw_window_rule_cb", rule)
    reg.register("_nw_window_panel_box", panel_only)
    reg.register("_nw_window_popout_btn", popout)
    reg.register("_nw_window_clear_btn", clear)
    reg.register("_nw_window_refresh", refresh)
    strip.hide()
    return strip


_SOURCE_TIP = "What the pop-out shows: the selected nanowell over time, or the nanowells inside the Distribution window."
_LAYOUT_TIP = "How the window's nanowells are laid out: one row of crops over time for each (Rows)."
_LAYOUT_NAMES = {"rows": "Rows", "hex": "Hex"}


def build_popout_window_controls(ctx, window) -> QHBoxLayout:
    """The pop-out's row for the window: **Show** (this nanowell | the window), **Layout**, and ◀ Page k of n ▶.
    Registers ``_nw_popout_source_cb``, ``_nw_popout_layout_cb``, ``_nw_popout_page_prev``, ``_nw_popout_page_label``,
    ``_nw_popout_page_next`` and ``_nw_popout_window_refresh``."""
    source = QComboBox(window)
    source.addItem("This nanowell", "selected")
    source.addItem("Window", "window")
    source.setToolTip(_SOURCE_TIP)
    layout = QComboBox(window)
    for key in POPOUT_LAYOUTS:
        layout.addItem(_LAYOUT_NAMES[key], key)
    layout.setToolTip(_LAYOUT_TIP)
    prev, following = QToolButton(window), QToolButton(window)
    prev.setText("◀")
    following.setText("▶")
    page = QLabel("", window)
    pager = (prev, page, following)
    row = QHBoxLayout()
    row.addWidget(QLabel("Show", window))
    row.addWidget(source)
    row.addWidget(QLabel("Layout", window))
    row.addWidget(layout)
    row.addStretch(1)
    for widget in pager:
        row.addWidget(widget)

    def refresh() -> None:
        st = _state(ctx)
        if st is None:
            return
        in_window = st.popout_source == "window"
        result = wnc.current(st)[1]
        blocked = [w.blockSignals(True) for w in (source, layout)]
        try:
            source.setItemText(1, f"Window ({len(result.rows):,})" if result is not None and result.rows else "Window")
            source.setCurrentIndex(source.findData(st.popout_source))
            layout.setCurrentIndex(layout.findData(st.popout_layout))
            layout.setEnabled(in_window)
        finally:
            for widget, was in zip((source, layout), blocked):
                widget.blockSignals(was)
        pages = wnc.page_count(st)
        page.setText(f"Page {st.window_page + 1} of {pages}")
        for widget in pager:
            widget.setVisible(in_window and pages > 1)

    source.currentIndexChanged.connect(lambda _i: wnc.set_popout_source(ctx, source.currentData()))
    layout.currentIndexChanged.connect(lambda _i: wnc.set_popout_layout(ctx, layout.currentData()))
    prev.clicked.connect(lambda _=False: wnc.step_window_page(ctx, -1))
    following.clicked.connect(lambda _=False: wnc.step_window_page(ctx, 1))
    reg = ctx.widgets
    reg.register("_nw_popout_source_cb", source)
    reg.register("_nw_popout_layout_cb", layout)
    reg.register("_nw_popout_page_prev", prev)
    reg.register("_nw_popout_page_label", page)
    reg.register("_nw_popout_page_next", following)
    reg.register("_nw_popout_window_refresh", refresh)
    refresh()
    return row
