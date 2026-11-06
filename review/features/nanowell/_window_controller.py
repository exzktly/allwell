"""The Distribution window's controller: the drag that draws it, the controls that edit it, and what it shows.

A press on the Distribution figure starts either a click (select one nanowell, as it always did) or a window; the
release tells them apart by how far the pointer moved (``controller.CLICK_SLOP_PX``). A window is kept per metric
and axis on ``NanowellState.windows`` and read against the model the figure was last drawn from
(``NanowellState.window_model``), so its members are the model's, not the drawing's (``window_compute``).

Reaches ``controller`` the way ``_qc_controller`` does, through :func:`_nwc`, because the controller imports this
module.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

from review.features.nanowell import array_hover_compute as ahc
from review.features.nanowell import composite_compute as cc
from review.features.nanowell import controller as _controller
from review.features.nanowell import subset_compute as ss
from review.features.nanowell import window_compute as wc
from review.features.nanowell._composite_render import render_composite
from review.features.nanowell._window_render import DRAG_GID, draw_drag, draw_window
from review.features.nanowell.crop_grid_compute import shown_timepoints
from review.features.nanowell.renderer import DENSITY_ABOVE, MAX_PANELS, render_message, render_site_rows
from review.features.nanowell.state import POPOUT_LAYOUTS, POPOUT_SOURCES
from review.features.nanowell.zoom_compute import wheel_limits

#: Rows of the window's nanowells the pop-out shows at a time.
WINDOW_PAGE = 24


def _nwc():
    return _controller


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def current(st):
    """``(window, result)`` for what the figure draws now — the window kept for its metric and axis and its members
    on the model — or ``(None, None)``."""
    model = st.window_model
    if model is None:
        return None, None
    window = st.windows.get(wc.window_key(model.column, model.axis))
    return (window, wc.members(model, window)) if window is not None else (None, None)


def after_draw(ctx, st, model) -> None:
    """Called by ``controller.redraw`` after a view is drawn; on the Distribution view, keep the model the figure was
    drawn from and draw the window on it. (The check is here, not in ``redraw``, which is at the complexity cap.)"""
    if st.view != "distribution":
        return
    st.window_model = model
    window, result = current(st)
    if window is not None and st.hits is not None:
        draw_window(st.hits, window, result)
    _nwc()._call(ctx, "_nw_window_refresh")
    _nwc()._call(ctx, "_nw_popout_window_refresh")


def on_press(ctx, st, event) -> None:
    """A press on a Distribution panel: remember where, until the release says whether it was a click or a drag."""
    if event.inaxes is None or event.button != 1:
        return
    st.window_press = (event.inaxes, float(event.x), float(event.y))


def on_motion(ctx, st, event) -> None:
    """A drag in progress: the rectangle follows the pointer, drawn on the axes the press began on."""
    press = st.window_press
    if press is None or event.x is None:
        return
    ax, x, y = press
    if abs(event.x - x) + abs(event.y - y) <= _nwc().CLICK_SLOP_PX:
        return
    _remove_drag(ax)
    inverse = ax.transData.inverted()
    (dx0, dy0), (dx1, dy1) = inverse.transform((x, y)), inverse.transform((event.x, event.y))
    draw_drag(ax, dx0, dy0, dx1, dy1)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()


def on_release(ctx, st, event) -> None:
    """The end of a press: within the slop it is a click and selects the nanowell under it (or clears the selection on
    empty space); beyond it the drag is a window, its columns snapped to the ones it overlaps."""
    press, st.window_press = st.window_press, None
    if press is None:
        return
    ax, x, y = press
    _remove_drag(ax)
    ex, ey = (x, y) if event.x is None else (float(event.x), float(event.y))
    nwc = _nwc()
    if abs(ex - x) + abs(ey - y) <= nwc.CLICK_SLOP_PX:
        site = st.hits.nearest(ax, x, y, nwc.CLICK_RADIUS_PX) if st.hits is not None else None
        nwc._select(ctx, site)
        return
    model = st.window_model
    index = dict(st.hits.panels).get(ax) if st.hits is not None else None
    if model is None or index is None:
        return
    inverse = ax.transData.inverted()
    (x0, y0), (x1, y1) = inverse.transform((x, y)), inverse.transform((ex, ey))
    keys = wc.snap(model, index, x0, x1)
    if not keys:
        ctx.notify.set_status("The window covers no column: drag across the dots you want.")
        nwc.redraw(ctx)
        return
    panel = model.panels[index].title
    if _drop_window_gate(ctx, st):
        # One window gate at a time: the new box selects from every nanowell, not from what the last one kept.
        # Dropping the gate redraws, so the members are read off the model drawn without it.
        ctx.notify.set_status("The earlier window was taken out of the Subset: this one selects from every "
                              "nanowell.")
        model = st.window_model
    draft = wc.DistributionWindow(model.column, model.axis, keys, float(min(y0, y1)), float(max(y0, y1)),
                                  panel=panel, drawn_under=wc.settings_key(st))
    _store(ctx, st, replace(draft, n_at_drawing=len(wc.members(model, draft).rows)))


def _remove_drag(ax) -> None:
    for patch in [p for p in ax.patches if p.get_gid() == DRAG_GID]:
        patch.remove()


def _store(ctx, st, window) -> None:
    """Keep *window* for the metric and axis drawn (``None`` removes it), start the pop-out's pages over, save, and
    draw again."""
    model = st.window_model
    if model is None:
        return
    key = wc.window_key(model.column, model.axis)
    if window is None:
        st.windows.pop(key, None)
    else:
        st.windows[key] = window
    st.window_page, st.composite_view = 0, None
    nwc = _nwc()
    nwc._save_soon(ctx)
    nwc.redraw(ctx)
    nwc.draw_popout(ctx)


def clear_window(ctx) -> None:
    """**✕**: remove the window drawn for this metric and axis."""
    st = _state(ctx)
    if st is not None and current(st)[0] is not None:
        _store(ctx, st, None)


def set_window_range(ctx, lo: float, hi: float) -> None:
    """The Low and High fields: the window's range, swapped if given the wrong way round; keys, rule and panel stay."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is None or not (math.isfinite(lo) and math.isfinite(hi)):
        return
    lo, hi = min(lo, hi), max(lo, hi)
    _store(ctx, st, replace(window, lo=lo, hi=hi))


def set_window_rule(ctx, rule: str) -> None:
    """*every* or *any* timepoint (``window_compute.RULES``); anything else is ignored."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is not None and rule in wc.RULES and rule != window.rule:
        _store(ctx, st, replace(window, rule=rule))


def set_window_panel_only(ctx, on: bool) -> None:
    """**This panel only**: cover the panel the window was drawn in, or every panel."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is not None and bool(on) != window.panel_only:
        _store(ctx, st, replace(window, panel_only=bool(on)))


def strip_text(st) -> str:
    """The sentence the strip and the pop-out write about the window — range, counts, members with no dot on the
    figure and why, and what changed since it was drawn; ``""`` with no window."""
    window, result = current(st)
    if window is None:
        return ""
    model = st.window_model
    hidden = (wc.not_drawn(result, model, st.hits, max_panels=MAX_PANELS, density_above=DENSITY_ABOVE)
              if st.hits is not None else {})
    said = wc.label_text(window, result, hidden, model.axis)
    changed = wc.changed_sentence(window, wc.settings_key(st), len(result.rows))
    return f"{said} {changed}".strip()


# ── Use as Subset ────────────────────────────────────────────────────────────
def _without_window_gate(subset):
    """*subset* with its window conditions taken out: the Subset holds one window gate at a time."""
    return ss.Subset(tuple(c for c in subset.conditions if not ss.is_window(c.field)), subset.match)


def _drop_window_gate(ctx, st) -> bool:
    """Take the window condition out of the page's Subset (and redraw); whether there was one."""
    kept = _without_window_gate(st.subset)
    if len(kept.conditions) == len(st.subset.conditions):
        return False
    return bool(_nwc().set_subset(ctx, kept))


def _window_name(subset) -> str:
    """``Window k`` for the smallest k not already the name of a window condition in *subset*."""
    taken = {c.values[0] for c in subset.conditions if ss.is_window(c.field) and c.values}
    return next(f"Window {k}" for k in itertools.count(1) if f"Window {k}" not in taken)


def use_window_as_subset(ctx) -> None:
    """**Use as Subset**: freeze the nanowells inside the window into the page's Subset as its window condition
    (``subset_compute.WINDOW``), applied at once, so the Distribution — and every view the Subset governs — loses the
    nanowells outside it. The Subset holds **one window gate at a time**: a window condition already in it is
    replaced, not narrowed further (a box drawn while one is applied has already taken it out, ``on_release``). The
    window is then cleared. It refuses, and says why, when the Subset's other conditions keep nanowells that meet
    *any* of several (a window could not narrow it), when they already hold the most conditions, when the window
    holds none or too many nanowells."""
    st = _state(ctx)
    window, result = current(st) if st is not None else (None, None)
    if window is None:
        return
    nwc = _nwc()
    before = st.subset
    subset = _without_window_gate(before)
    had_gate = len(subset.conditions) != len(before.conditions)
    if had_gate:
        # The window's members are read off the figure drawn without the old gate, or replacing it would keep only
        # what both hold — stacking by another name. (A window kept for another metric can come back under a gate.)
        nwc.set_subset(ctx, subset)
        window, result = current(st)
        if window is None:
            nwc.set_subset(ctx, before)
            return
    sites = tuple(result.sites())
    if not sites:
        refusal = "No nanowell is inside the window, so there is nothing to keep."
    elif subset.match == "any" and len(subset.conditions) > 1:
        refusal = ("The subset keeps nanowells that meet any condition, so a window could not narrow it: set it to all "
                   "first.")
    elif len(subset.conditions) >= ss.MAX_CONDITIONS:
        refusal = f"A subset holds at most {ss.MAX_CONDITIONS} conditions."
    elif len(sites) > ss.MAX_WINDOW_SITES:
        refusal = (f"The window holds {len(sites):,} nanowells; a subset keeps at most {ss.MAX_WINDOW_SITES:,} in "
                   "one window.")
    else:
        refusal = ""
    if refusal:
        if had_gate:
            nwc.set_subset(ctx, before)                  # refused: the Subset is put back as it was
        ctx.notify.set_status(refusal)
        return
    name = _window_name(subset)
    condition = ss.Condition(ss.WINDOW, "in", values=(name,), sites=sites)
    key = wc.window_key(st.window_model.column, st.window_model.axis)
    st.windows.pop(key, None)
    st.window_page = 0
    if nwc.set_subset(ctx, ss.Subset(subset.conditions + (condition,), "all")):
        ctx.notify.set_status(f"“{name}” is now part of the Subset: {len(sites):,} nanowells kept."
                              + (" It replaces the earlier window." if had_gate else ""))
    else:
        st.windows[key] = window                         # not a subset after all: the window stays as it was
    nwc._call(ctx, "_nw_window_refresh")
    nwc._call(ctx, "_nw_popout_window_refresh")
    nwc.draw_popout(ctx)


# ── the pop-out ──────────────────────────────────────────────────────────────
def page_size(st) -> int:
    """How many of the window's nanowells a page holds: a page of rows, or a page of the composite's tiles."""
    return cc.COMPOSITE_PAGE if st.popout_layout == "hex" else WINDOW_PAGE


def page_count(st) -> int:
    """How many pages the window's nanowells make in the layout shown (1 with none)."""
    result = current(st)[1]
    return max(1, -(-len(result.rows) // page_size(st))) if result is not None else 1


def open_window_popout(ctx) -> None:
    """**Open in pop-out**: show the window's nanowells there, from the first page, and bring the window forward."""
    st = _state(ctx)
    if st is None:
        return
    st.popout_source, st.window_page = "window", 0
    nwc = _nwc()
    nwc._save_soon(ctx)
    nwc._call(ctx, "_nw_popout_refresh")
    nwc._call(ctx, "_nw_popout_window_refresh")
    nwc._call(ctx, "_nw_open_popout")
    nwc.draw_popout(ctx)


def set_popout_source(ctx, source: str) -> None:
    """**Show**: the selected nanowell, or the window's (``state.POPOUT_SOURCES``)."""
    if source in POPOUT_SOURCES:
        _nwc()._set_popout(ctx, popout_source=source)


def set_popout_layout(ctx, layout: str) -> None:
    """**Layout** of the window's nanowells (``state.POPOUT_LAYOUTS``); the pages start over."""
    if layout in POPOUT_LAYOUTS:
        _nwc()._set_popout(ctx, popout_layout=layout, window_page=0, composite_view=None)


def step_window_page(ctx, delta: int) -> None:
    """◀ ▶: move *delta* pages, kept within the pages there are."""
    st = _state(ctx)
    if st is None:
        return
    page = min(max(st.window_page + delta, 0), page_count(st) - 1)
    if page != st.window_page:
        st.window_page, st.composite_view = page, None   # another page is another composite: drawn unzoomed
    _nwc()._call(ctx, "_nw_popout_window_refresh")
    _nwc().draw_popout(ctx)


def draw_window_popout(ctx, st, fig) -> None:
    """The pop-out in Window mode: the nanowells inside the window, ranked as ``window_compute.members`` ranks them,
    as rows of crops over time (a page of :data:`WINDOW_PAGE`) or as the hex composite (a page of
    ``composite_compute.COMPOSITE_PAGE`` tiles, drawn once per timepoint the **Timepoints** menu shows, side by side in
    one row, at the zoom the wheel left, ``NanowellState.composite_view``). With **Timepoints: None** it says that instead of drawing. The crops of the page are cut on
    a worker, all in one pass, and each row or tile says so until its crops arrive."""
    st.popout_hits, st.popout_tiles = {}, []
    window, result = current(st)
    if window is None:
        render_message(fig, "Draw a window on the Distribution view to see its nanowells here.",
                       gid="nw-popout-window-empty")
        return
    rows = result.rows
    if not rows:
        render_message(fig, strip_text(st) + " No nanowell is inside it.", gid="nw-popout-window-empty")
        return
    nwc = _nwc()
    if st.popout_tps_none:
        render_message(fig, nwc.NO_TIMEPOINTS_SAID, gid="nw-popout-no-timepoints")
        return
    st.window_page = min(max(st.window_page, 0), page_count(st) - 1)
    if st.popout_layout == "hex":
        comp = cc.composite(cc.page(rows, st.window_page))
        sites = [t.source for t in comp.tiles]
        _want_crops(ctx, st, sites)
        st.popout_tiles = render_composite(
            fig, comp, {s: nwc.gated_crops(st, s, st.crops[s]) for s in sites if s in st.crops},
            shown_timepoints(st.data.timepoints(), st.popout_tps), n_total=len(rows),
            display_range=nwc.display_range(ctx), note=strip_text(st), cells=st.crop_cells, view=st.composite_view,
            labels=st.popout_labels)
        return
    first = st.window_page * WINDOW_PAGE
    shown = rows[first:first + WINDOW_PAGE]
    sites = [row.site for row in shown]
    _want_crops(ctx, st, sites)
    image_channel = nwc.page_image_channel(st)
    metric, channel, _note = nwc.resolve_metric("distribution", st.metric, nwc.page_channel_name(ctx),
                                                st.data.channels)
    label = nwc._label(metric, channel, st.data)
    timepoints = [str(tp) for tp in st.data.timepoints()]
    model = wc.WindowRows(result.rank_key, st.window_model.column, timepoints)
    st.popout_hits = render_site_rows(
        fig, model, [(f"Rows {first + 1}–{first + len(shown)} of {len(rows):,}", shown)],
        {s: nwc.gated_crops(st, s, st.crops[s]) for s in sites if s in st.crops},
        label=label, empty=strip_text(st), title=f"Distribution window · {label}", head=strip_text(st),
        cells=st.crop_cells, numbers=st.crop_metrics, image_channel=image_channel,
        display_range=nwc.display_range(ctx),
        timepoints=nwc.extremes_columns(timepoints, result.rank_key, st.popout_tps),
        metrics=nwc.crop_metrics(st, sites, channel) if st.crop_metrics else None, with_well=True)


def _want_crops(ctx, st, sites) -> None:
    """Keep the crops of *sites* (the page on screen) in the store and cut those it lacks on a worker, in one pass."""
    nwc = _nwc()
    st.crops.protected = set(sites) | {tuple(s) for s in st.selected_sites}
    missing = tuple(sorted(s for s in sites if s not in st.crops))
    if not missing:
        return
    data, metrics, image_channel = st.data, st.metrics, nwc.page_image_channel(st)
    nwc._fetch(ctx, "window_crops", missing,
               lambda: nwc.crops_for_sites(data, metrics, missing, channel=image_channel),
               failed=lambda message: {s: {"": nwc.Crop(None, "", f"The crops could not be cut: {message}")}
                                       for s in missing},
               deliver=lambda found: st.crops.update(found))


def _tile_at(st, event) -> tuple | None:
    """The composite's ``(x, y, site, rank)`` under the pointer (within half a tile pitch of its centre), or ``None``."""
    if not st.popout_tiles or event.inaxes is None or event.xdata is None or event.ydata is None:
        return None
    best = min(st.popout_tiles, key=lambda t: math.hypot(t[0] - event.xdata, t[1] - event.ydata))
    return best if math.hypot(best[0] - event.xdata, best[1] - event.ydata) <= 0.5 else None


def on_popout_click(ctx, event) -> None:
    """A click on a crop of the pop-out's window rows, or on a tile of its composite, selects that real nanowell (the
    site detail follows)."""
    st = _state(ctx)
    if st is None or event.button != 1:
        return
    tile = _tile_at(st, event)
    if tile is not None:
        _nwc()._select(ctx, tile[2])
    elif event.inaxes in st.popout_hits:
        _nwc()._select(ctx, st.popout_hits[event.inaxes])


def on_popout_motion(ctx, event) -> None:
    """Pointing at a composite tile names its real nanowell in the status bar and shows the Array view's card for it
    at the timepoint of the panel under the pointer (``array_hover_compute.hover_text``); off a tile the card goes."""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip

    st = _state(ctx)
    tile = _tile_at(st, event) if st is not None else None
    if tile is None:
        if st is not None and st.popout_tiles:
            QToolTip.hideText()
        return
    well, q, r = tile[2]
    ctx.notify.set_status(f"Tile {tile[3]} · {well} {q},{r}")
    text = tile_card(st, tile[2], getattr(event.inaxes, "_aw_composite_tp", None))
    if text:
        QToolTip.showText(QCursor.pos(), f"Tile {tile[3]} of the composite\n{text}",
                          ctx.widgets.get_or_none("_nw_popout_canvas"))


def tile_card(st, site, timepoint) -> str:
    """The Array view's hover card for the real nanowell *site* at *timepoint* (``""`` without one): its address,
    state, counts, fluorescence, fold changes, rim focus and place, read from its row of the nanowell table."""
    if timepoint is None or st.metrics is None or st.data is None:
        return ""
    well, q, r = site
    rows = ahc.rows_by_address(st.metrics, well, str(timepoint))
    return ahc.hover_text(ahc.row_for(rows, site, None, None), site, timepoint=str(timepoint),
                          channels=list(st.data.channels), rule=st.rim_focus_rule())


def on_popout_scroll(ctx, event) -> None:
    """The wheel over the hex composite zooms every panel alike about the pointer, as the Array view's wheel does
    (``zoom_compute.wheel_limits``, held within its zoom limits of the unzoomed view); the zoom is kept on
    ``NanowellState.composite_view`` so the crops arriving on a worker redraw it where it was."""
    st = _state(ctx)
    ax = event.inaxes
    home = getattr(ax, "_aw_composite_home", None)
    if st is None or home is None or not event.step or event.xdata is None:
        return
    xlim, ylim = wheel_limits(ax.get_xlim(), ax.get_ylim(), (event.xdata, event.ydata), event.step,
                              home_xlim=home[0])
    st.composite_view = None if abs((xlim[1] - xlim[0]) - (home[0][1] - home[0][0])) < 1e-9 else (xlim, ylim)
    fig = ax.figure
    for other in fig.axes:
        if getattr(other, "_aw_composite_home", None) is not None:
            other.set_xlim(*xlim)
            other.set_ylim(*ylim)
    canvas = ctx.widgets.get_or_none("_nw_popout_canvas")
    if canvas is not None:
        canvas.draw_idle()

