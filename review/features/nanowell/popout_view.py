"""The **Nanowell crops** pop-out's second row of controls: what its grid shows, and saving it.

**Timepoints** chooses which of the timepoints are panels (**All**, **None**, or a tick each), **Channels** adds channels beside the page's
image channel (a row of crops each), **Grid** sets the shape the panels are laid out in, and **Save ▾** saves,
copies or files the figure. Each writes one ``NanowellState`` field through the controller
(``set_popout_timepoints``, ``set_popout_channels``, ``set_popout_grid``, ``save_popout_figure``); the rules those
fields follow are ``crop_grid_compute``'s.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton

from review.features.nanowell import controller as nwc
from review.features.nanowell.crop_grid_compute import POPOUT_MAX_COLS, grid_label
from widgets.owned_action import owned_action

_TPS_TIP = ("Which of the timepoints the pop-out shows, one panel each (in the Hex layout, one composite each, side "
            "by side). Fewer panels are drawn larger. All shows every timepoint; None clears every tick, so a few can "
            "be ticked from nothing.")
_CHANNELS_TIP = ("Channels drawn beside the image channel, a row of crops each, every row on a brightness scale of "
                 "its own (the channel's Display range when it has one). The image channel is always drawn first.")
_GRID_TIP = ("The shape of the pop-out's grid. Auto: one channel wraps into a near-square grid, several take a row "
             "each. One row: every timepoint across. One column: every timepoint down, one column per channel. "
             "N across: the timepoints wrap every N panels.")
_SAVE_TIP = "Save the nanowell over time as it is drawn here: a figure file, the clipboard, or Saved Plots"


def _menu_button(parent, name: str, tip: str, ctx) -> tuple[QToolButton, QMenu]:
    button = QToolButton(parent)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolTip(tip)
    button.setProperty("variant", "secondary")
    button.setProperty("hasMenu", True)
    menu = QMenu(button)
    button.setMenu(menu)
    ctx.widgets.register(f"_nw_popout_{name}_btn", button)
    ctx.widgets.register(f"_nw_popout_{name}_menu", menu)
    return button, menu


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


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
    """The row: **Timepoints**, **Channels**, **Grid** and **Save ▾**, each timepoint named by *timepoint_text*
    ``(tp, times_h)`` as the page's Timepoint control names it. Registers ``_nw_popout_refresh`` (each
    button's text put back on the state) and ``_nw_popout_rebuild_menus`` (what opening each menu does)."""
    from review.core.plot_card_actions import copy_active_card_as_svg, save_card_to_gallery
    from review.core.ui_helpers import add_menu_action, make_action_menu_button

    tps_btn, tps_menu = _menu_button(window, "tps", _TPS_TIP, ctx)
    ch_btn, ch_menu = _menu_button(window, "channels", _CHANNELS_TIP, ctx)
    grid_btn, grid_menu = _menu_button(window, "grid", _GRID_TIP, ctx)

    def shown_tps(st) -> set:
        if st is None or st.popout_tps_none:
            return set()
        tps = _timepoints(st)
        chosen = set(st.popout_tps) & set(tps)
        return chosen or set(tps)

    def toggle_tp(tp: str, on: bool) -> None:
        st = _state(ctx)
        tps = _timepoints(st)
        shown = shown_tps(st) | {tp} if on else shown_tps(st) - {tp}
        if not shown:
            nwc.set_popout_no_timepoints(ctx)         # the last tick taken away is None, said on the figure
            return
        nwc.set_popout_timepoints(ctx, [] if shown >= set(tps) else [t for t in tps if t in shown])

    def rebuild_tps() -> None:
        st = _state(ctx)
        tps_menu.clear()
        every = owned_action(tps_menu, "All")
        every.triggered.connect(lambda _=False: nwc.set_popout_timepoints(ctx, []))
        nothing = owned_action(tps_menu, "None")
        nothing.triggered.connect(lambda _=False: nwc.set_popout_no_timepoints(ctx))
        tps_menu.addSeparator()
        shown = shown_tps(st)
        times = st.data.times_h if st is not None and st.data is not None else {}
        for tp in _timepoints(st):
            action = owned_action(tps_menu, timepoint_text(tp, times))
            action.setCheckable(True)
            action.setChecked(tp in shown)
            action.setData(tp)
            action.toggled.connect(lambda on, tp=tp: toggle_tp(tp, on))

    def toggle_channel(channel: str, on: bool) -> None:
        st = _state(ctx)
        extras = set(nwc.popout_channels(st)[1:])
        extras = extras | {channel} if on else extras - {channel}
        nwc.set_popout_channels(ctx, [c for c in _channels(st) if c in extras])

    def rebuild_channels() -> None:
        st = _state(ctx)
        ch_menu.clear()
        drawn = nwc.popout_channels(st)
        for channel in _channels(st):
            first = channel == drawn[0]
            action = owned_action(ch_menu, _channel_name(st, channel) + (" (image channel)" if first else ""))
            action.setCheckable(True)
            action.setChecked(channel in drawn)
            action.setEnabled(not first)
            action.setData(channel)
            action.toggled.connect(lambda on, channel=channel: toggle_channel(channel, on))

    def rebuild_grid() -> None:
        st = _state(ctx)
        grid_menu.clear()
        n_tps = max(1, len(shown_tps(st)))
        shapes = [("auto", 0), ("row", 0), ("column", 0)] + [
            ("columns", n) for n in range(2, min(POPOUT_MAX_COLS, n_tps - 1) + 1)]
        for grid, cols in shapes:
            action = owned_action(grid_menu, grid_label(grid, cols).removeprefix("Grid: ").capitalize())
            action.setCheckable(True)
            action.setChecked(st is not None and (st.popout_grid, st.popout_cols) == (grid, cols))
            action.setData((grid, cols))
            action.triggered.connect(lambda _=False, grid=grid, cols=cols: nwc.set_popout_grid(ctx, grid, cols))

    def refresh() -> None:
        st = _state(ctx)
        tps = _timepoints(st)
        shown = shown_tps(st)
        tps_btn.setText("Timepoints: none" if not shown else "Timepoints: all" if len(shown) >= len(tps)
                        else f"Timepoints: {len(shown)} of {len(tps)}")
        drawn = nwc.popout_channels(st)
        ch_btn.setText("Channels: 1" if len(drawn) == 1 else f"Channels: {len(drawn)}")
        ch_btn.setEnabled(len(_channels(st)) > 1)
        grid_btn.setText(grid_label(st.popout_grid, st.popout_cols) if st is not None else "Grid: auto")

    tps_menu.aboutToShow.connect(rebuild_tps)
    ch_menu.aboutToShow.connect(rebuild_channels)
    grid_menu.aboutToShow.connect(rebuild_grid)
    ctx.widgets.register("_nw_popout_refresh", refresh)
    ctx.widgets.register("_nw_popout_rebuild_menus", lambda: (rebuild_tps(), rebuild_channels(), rebuild_grid()))

    save_btn, save_menu = make_action_menu_button("Save ▾", window, tooltip=_SAVE_TIP)
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
