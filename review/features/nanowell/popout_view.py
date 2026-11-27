"""The **Nanowell crops** pop-out's second row of controls: what its grid shows, and saving it.

**Timepoints** chooses which of the timepoints are panels (**All**, **None**, or a tick each, on the application's one
multi-select page, ``widgets.multi_select``), **Channels** adds channels beside the page's
image channel (a row of crops each), **Grid** sets the shape the panels are laid out in — or, while the pop-out shows many nanowells as rows of crops
(the Distribution window's in the Rows layout, or the rare-responder tests' flagged ones), how many rows a page holds,
the pager recounting its pages to match — and **Share ▾** saves,
copies or files the figure. Each writes one ``NanowellState`` field through the controller
(``set_popout_timepoints``, ``set_popout_channels``, ``set_popout_grid`` / ``set_popout_rows``,
``save_popout_figure``); the rules those
fields follow are ``crop_grid_compute``'s.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton

from review.features.nanowell import controller as nwc
from review.features.nanowell.crop_grid_compute import POPOUT_MAX_COLS, POPOUT_ROW_CHOICES, grid_label, rows_label
from widgets.menu_marks import mark_current
from widgets.multi_select import MultiSelectButton
from widgets.owned_action import owned_action
from ui import buttons

_TPS_TIP = ("Which of the timepoints the pop-out shows, one panel each (in the Hex layout, one composite each, side "
            "by side). Fewer panels are drawn larger. All shows every timepoint; None clears every tick, so a few can "
            "be ticked from nothing.")
_CHANNELS_TIP = ("Channels drawn beside the image channel, a row of crops each, every row on a brightness scale of "
                 "its own (the channel's Display range when it has one). The image channel is always drawn first.")
_GRID_TIP = ("The shape of the pop-out's grid. Auto: one channel wraps into a near-square grid, several take a row "
             "each. One row: every timepoint across. One column: every timepoint down, one column per channel. "
             "N across: the timepoints wrap every N panels. While many nanowells are shown, a row each: how many rows "
             "a page holds — fewer rows are drawn larger, and the pages are recounted.")
_SAVE_TIP = "Save the nanowell over time as it is drawn here: a figure file, the clipboard, or Saved Plots"


def _menu_button(parent, name: str, tip: str, ctx) -> tuple[QToolButton, QMenu]:
    button = buttons.tool_button(parent)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolTip(tip)
    button.setProperty("variant", "default")
    button.setProperty("hasMenu", True)
    menu = QMenu(button)
    button.setMenu(menu)
    ctx.widgets.register(f"_nw_popout_{name}_btn", button)
    ctx.widgets.register(f"_nw_popout_{name}_menu", menu)
    return button, menu


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def _picker(parent, name: str, tip: str, title: str, ctx) -> MultiSelectButton:
    """A list to tick, opened as the one multi-select page in a small window over the pop-out (which has no
    inspector of its own)."""
    picker = MultiSelectButton(parent, noun=name, title=title)
    picker.setToolTip(tip)
    ctx.widgets.register(f"_nw_popout_{name}_btn", picker)
    return picker


def _timepoints(st) -> list:
    return [str(tp) for tp in st.data.timepoints()] if st is not None and st.data is not None else []


def _channels(st) -> list:
    """Every image channel the run read: ``""`` for phase, then its fluorescence channels."""
    if st is None or st.data is None:
        return []
    return [""] + list(getattr(st.data, "image_channels", None) or [])


def _channel_name(st, channel: str) -> str:
    if channel:
        return channel
    phase = (getattr(st.data, "phase_token", "") or "") if st is not None and st.data is not None else ""
    return f"Phase ({phase})" if phase else "Phase"


def build_popout_view_controls(ctx, window, timepoint_text) -> QHBoxLayout:
    """The row: **Timepoints**, **Channels**, **Grid** and **Share ▾**, each timepoint named by *timepoint_text*
    ``(tp, times_h)`` as the page's Timepoint control names it. Registers ``_nw_popout_refresh`` (each
    button's text put back on the state) and ``_nw_popout_rebuild_menus`` (what opening each menu does)."""
    from review.core.plot_card_actions import copy_active_card_as_svg, save_card_to_gallery
    from review.core.ui_helpers import add_menu_action, make_action_menu_button

    tps_btn = _picker(window, "tps", _TPS_TIP, "Timepoints", ctx)
    ch_btn = _picker(window, "channels", _CHANNELS_TIP, "Channels", ctx)
    grid_btn, grid_menu = _menu_button(window, "grid", _GRID_TIP, ctx)

    def shown_tps(st) -> set:
        if st is None or st.popout_tps_none:
            return set()
        tps = _timepoints(st)
        chosen = set(st.popout_tps) & set(tps)
        return chosen or set(tps)

    def tps_chosen() -> None:
        st = _state(ctx)
        tps = _timepoints(st)
        shown = set(tps_btn.checkedData())
        if not shown:
            nwc.set_popout_no_timepoints(ctx)         # the last tick taken away is None, said on the figure
            return
        nwc.set_popout_timepoints(ctx, [] if shown >= set(tps) else [t for t in tps if t in shown])

    def rebuild_tps() -> None:
        st = _state(ctx)
        shown = shown_tps(st)
        times = st.data.times_h if st is not None and st.data is not None else {}
        blocked = tps_btn.blockSignals(True)
        try:
            tps_btn.clear()
            for tp in _timepoints(st):
                tps_btn.addItem(timepoint_text(tp, times), tp)
            tps_btn.setCheckedData(list(shown))
        finally:
            tps_btn.blockSignals(blocked)

    def channels_chosen() -> None:
        st = _state(ctx)
        drawn = nwc.popout_channels(st)
        chosen = set(ch_btn.checkedData())
        if drawn and drawn[0] not in chosen:          # the image channel is always drawn: its tick comes back
            rebuild_channels()
        nwc.set_popout_channels(ctx, [c for c in _channels(st) if c in chosen and c != (drawn[0] if drawn else None)])

    def rebuild_channels() -> None:
        st = _state(ctx)
        drawn = nwc.popout_channels(st)
        blocked = ch_btn.blockSignals(True)
        try:
            ch_btn.clear()
            for channel in _channels(st):
                first = bool(drawn) and channel == drawn[0]
                ch_btn.addItem(_channel_name(st, channel) + (" (image channel, always drawn)" if first else ""),
                               channel)
            ch_btn.setCheckedData(list(drawn))
            ch_btn.setLocked(drawn[:1])
        finally:
            ch_btn.blockSignals(blocked)

    def many_rows(st) -> bool:
        """Rows of many nanowells, paged: the Grid control sets how many rows a page holds, not a grid's shape."""
        return st is not None and (st.popout_source == "responders"
                                   or (st.popout_source == "window" and st.popout_layout != "hex"))

    def rebuild_grid() -> None:
        st = _state(ctx)
        grid_menu.clear()
        if many_rows(st):
            grid_menu.addSection("Rows per page")
            for rows in POPOUT_ROW_CHOICES:
                # A command, the current one marked: not a tick (``widgets.menu_marks``).
                action = mark_current(owned_action(grid_menu, ""), f"{rows} row{'' if rows == 1 else 's'}",
                                      st.popout_rows == rows)
                action.setData(("rows", rows))
                action.triggered.connect(lambda _=False, rows=rows: nwc.set_popout_rows(ctx, rows))
            return
        n_tps = max(1, len(shown_tps(st)))
        shapes = [("auto", 0), ("row", 0), ("column", 0)] + [
            ("columns", n) for n in range(2, min(POPOUT_MAX_COLS, n_tps - 1) + 1)]
        for grid, cols in shapes:
            action = mark_current(owned_action(grid_menu, ""),
                                  grid_label(grid, cols).removeprefix("Grid: ").capitalize(),
                                  st is not None and (st.popout_grid, st.popout_cols) == (grid, cols))
            action.setData((grid, cols))
            action.triggered.connect(lambda _=False, grid=grid, cols=cols: nwc.set_popout_grid(ctx, grid, cols))

    def refresh() -> None:
        st = _state(ctx)
        rebuild_tps()
        rebuild_channels()
        ch_btn.setEnabled(len(_channels(st)) > 1)
        grid_btn.setText("Grid: auto" if st is None else rows_label(st.popout_rows) if many_rows(st)
                         else grid_label(st.popout_grid, st.popout_cols))
        for button in (tps_btn._button, ch_btn._button, grid_btn):
            button.ensurePolished()                     # measured with the stylesheet's caret room, not before it
            button.setMinimumWidth(button.sizeHint().width())
        # The hex composite draws the image channel alone, one composite per timepoint in a row, on tiles too small
        # for a line of numbers: Channels, Grid and Metrics would change nothing there, so they are not offered.
        hex_shown = st is not None and st.popout_source == "window" and st.popout_layout == "hex"
        ch_btn.setVisible(not hex_shown)
        grid_btn.setVisible(not hex_shown)
        metrics_box = ctx.widgets.get_or_none("_nw_popout_metrics_box")
        if metrics_box is not None:
            metrics_box.setVisible(not hex_shown)

    tps_btn.setFaceFormatter(lambda chosen, every: "Timepoints: none" if not chosen else "Timepoints: all"
                             if len(chosen) >= len(every) else f"Timepoints: {len(chosen)} of {len(every)}")
    ch_btn.setFaceFormatter(lambda chosen, _every: f"Channels: {len(chosen)}")
    tps_btn.changed.connect(tps_chosen)
    ch_btn.changed.connect(channels_chosen)
    grid_menu.aboutToShow.connect(rebuild_grid)
    ctx.widgets.register("_nw_popout_refresh", refresh)
    ctx.widgets.register("_nw_popout_rebuild_menus", lambda: (rebuild_tps(), rebuild_channels(), rebuild_grid()))

    save_btn, save_menu = make_action_menu_button("Share ▾", window, tooltip=_SAVE_TIP)
    add_menu_action(save_menu, "Save figure…", lambda: nwc.save_popout_figure(ctx))
    add_menu_action(save_menu, "Copy figure as SVG", lambda: copy_active_card_as_svg(ctx, "_nw_popout_card"))
    save_menu.addSeparator()
    add_menu_action(save_menu, "Save to Saved Plots", lambda: save_card_to_gallery(ctx, "_nw_popout_card"))
    ctx.widgets.register("_nw_popout_save_btn", save_btn)
    ctx.widgets.register("_nw_popout_save_menu", save_menu)

    row = QHBoxLayout()
    for button in (tps_btn, ch_btn, grid_btn):
        row.addWidget(button)
    row.addStretch(1)
    row.addWidget(save_btn)
    refresh()
    return row
