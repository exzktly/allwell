"""Orchestration for the Nanowells data: load a dataset's tables, resolve replicate groups, export (D6, D8).

The page (Section V) calls these; nothing here draws. The compute modules beside it are pure; this module
is where the dataset on disk and the app's Sample Definitions come in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review.core.ui_helpers import EmptyReason

from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell.distribution_compute import distribution_model, match_channel
from review.features.nanowell.renderer import render_distribution, render_site_detail
from review.features.nanowell.series_compute import long_format, site_metrics, site_series

log = logging.getLogger("review")


def load_into(state, data_dir) -> None:
    """Read *data_dir*'s nanowell tables into *state* and compute the metrics at the state's baseline.

    A dataset with no ``nanowells/`` leaves ``state.data`` as ``None``; a scheme this version does not read
    leaves it ``None`` with the reason in ``state.load_error``.
    """
    state.reset_data()
    if not data_dir:
        return
    try:
        data = load_nanowell_data(Path(data_dir))
    except NanowellSchemeError as exc:
        state.load_error = str(exc)
        return
    if data is None:
        return
    for problem in data.problems:
        log.warning("Nanowells: %s", problem)
    state.data = data
    state.metrics = site_metrics(data, state.baseline)


def set_baseline(state, baseline: str) -> None:
    """Change the baseline and recompute every metric that depends on it."""
    state.baseline = baseline
    if state.data is not None:
        state.metrics = site_metrics(state.data, baseline)


def replicate_groups(ctx, wells) -> dict:
    """``group name -> wells`` from Sample Definitions' replicate sets, as every plot tab resolves them.

    Wells in no set stand as their own group, so a plate with no definitions compares well against well.
    Only wells with nanowell data are kept; a set left with none is dropped.
    """
    from review.domain.plot_members import plot_replicate_sets

    have = set(wells)
    groups, placed = {}, set()
    for rset in plot_replicate_sets(ctx) or []:
        members = [w for w in rset.wells if w in have]
        if members:
            groups[rset.name] = members
            placed.update(members)
    for well in sorted(have - placed):
        groups[well] = [well]
    return groups


def export_long_csv(state, path) -> int:
    """Write every site, timepoint and metric as a long-format CSV (tracker D8); returns the rows written.

    Written to a temporary name and moved into place, so a failed write never leaves half a table under the
    name a person will open.
    """
    if state.metrics is None or state.data is None:
        raise ValueError("There are no nanowell tables loaded to export.")
    table = long_format(state.metrics, state.data.channels)
    table.insert(table.columns.get_loc("time_h") + 1, "time_source", state.data.time_source or "")
    table.insert(table.columns.get_loc("baseline_timepoint"), "baseline", state.baseline)
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    table.to_csv(tmp, index=False)
    tmp.replace(path)
    return len(table)


# ── the page ─────────────────────────────────────────────────────────────────
# Everything below takes the ViewerContext. Widgets resolve through ``ctx.widgets`` and are absent until the
# page is first shown (it builds lazily), so every entry point is self-guarding.

#: How many nanowells can be compared in the site detail at once (decision U7: one colour each).
MAX_SELECTED = 6
#: How near, in screen pixels, a click must land to a dot to select it.
CLICK_RADIUS_PX = 6.0


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def active_wells(ctx) -> list[str]:
    """The wells the page draws: the plate selection, or in replicate mode every visible set's wells and the
    active solo wells — the same answer the plotting tabs and the plate highlight give. Never every loaded
    well as a fallback: an empty selection is a stated empty state, not an arbitrary well."""
    sel = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths or {}
    if sel.selections:
        wells = {w for s in sel.selections if not s.get("hidden") for w in (s.get("wells") or [])}
        wells |= set(sel.active_solo_wells or ())
    else:
        wells = set(sel.selected_wells or ())
    return sorted(w for w in wells if w in well_paths)


def page_groups(ctx) -> dict:
    """``group -> wells`` for the page: replicate sets over the active wells only (gap G9 of the plan)."""
    st = _state(ctx)
    have = [w for w in active_wells(ctx) if st is None or st.data is None or w in set(st.data.wells)]
    return replicate_groups(ctx, have) if have else {}


def _empty(title: str, hint: str):
    from review.core.ui_helpers import EmptyReason

    return EmptyReason(title, hint)


def nanowell_empty_reason(ctx) -> EmptyReason | None:
    """Why the page has nothing to draw, or ``None`` — each state of the interface plan's §4 by name."""
    st = _state(ctx)
    if st is None:
        return None
    if not ctx.state.dataset.data_dir:
        return _empty("No dataset loaded.", "Open a dataset analysed in nanowell mode.")
    if st.load_error:
        return _empty("This dataset's nanowell tables cannot be read.", st.load_error)
    if st.data is None:
        if st.computing or st.loaded_for is None:
            return _empty("Reading the nanowell tables…", "The figure appears when they are loaded.")
        return _empty("This dataset has no nanowell tables.",
                      "Nanowell tables are written by a run with Analyze ▸ Nanowells switched on "
                      "(nanowells/<well>.csv beside the per-well CSVs).")
    if not st.data.wells:
        return _empty("No well's nanowell table could be used.", " ".join(st.data.problems))
    from review.core.selection_controller import has_plottable_selection

    if not has_plottable_selection(ctx) or not page_groups(ctx):
        return _empty("No nanowell wells selected.",
                      "Click wells with nanowell tables on the plate at the left: "
                      + ", ".join(st.data.wells[:12]) + ("…" if len(st.data.wells) > 12 else "") + ".")
    return None


def _refresh_empty(ctx) -> None:
    from core.safe import swallow
    from review.core.ui_helpers import refresh_empty_state_pages

    with swallow("refresh the Nanowells page's empty state"):
        refresh_empty_state_pages(ctx.window)


def _call(ctx, name: str) -> None:
    fn = ctx.widgets.get_or_none(name)
    if callable(fn):
        fn()


def ensure_loaded(ctx, *, force: bool = False) -> None:
    """Load the dataset's tables and compute the metrics on a worker, unless they are current.

    Each request is numbered; a result that arrives after a newer request was made is dropped, so a slow
    load for one baseline can never replace the figure for the baseline chosen after it.
    """
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    if st is None:
        return
    data_dir = ctx.state.dataset.data_dir
    key = (str(data_dir or ""), st.baseline)
    if not force and st.computing and st.pending_for == key:
        return                                           # this very request is already running
    if not force and st.loaded_for == key:
        if st.computing:
            # The current data is what is wanted again (a baseline changed and changed back): whatever is
            # running is for another key and now stale, so it must not land when it finishes.
            st.request += 1
            st.computing, st.pending_for = False, None
            _call(ctx, "_nw_show_computing")
        return
    st.request += 1
    request, baseline = st.request, st.baseline
    st.computing, st.pending_for = True, key
    previous = st.data if st.loaded_for is not None and st.loaded_for[0] == key[0] else None

    def job():
        if previous is not None:
            return previous, site_metrics(previous, baseline), ""
        try:
            data = load_nanowell_data(Path(data_dir)) if data_dir else None
        except NanowellSchemeError as exc:
            return None, None, str(exc)
        return data, (site_metrics(data, baseline) if data is not None else None), ""

    def done(result):
        if request != st.request:
            return                                       # a newer request has been made; this one is stale
        data, metrics, error = result
        st.data, st.metrics, st.load_error = data, metrics, error
        st.loaded_for, st.computing = key, False
        for problem in (data.problems if data is not None else []):
            log.warning("Nanowells: %s", problem)
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)

    def failed(message):
        if request != st.request:
            return
        st.reset_data()
        st.computing, st.load_error, st.loaded_for = False, f"Loading failed: {message}", key
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)

    st.bridge = run_off_thread(job, on_done=done, on_failed=failed)
    _call(ctx, "_nw_show_computing")
    _refresh_empty(ctx)


def redraw(ctx) -> None:
    """Draw the current view from the computed metrics; never computes."""
    st = _state(ctx)
    fig, canvas = ctx.widgets.get_or_none("_nw_fig"), ctx.widgets.get_or_none("_nw_canvas")
    if st is None or fig is None or canvas is None:
        return
    if st.loaded_for is not None and st.loaded_for[1] != st.baseline and not st.computing:
        # The baseline changed without a load (a reset, a restore): what is on hand was computed for another
        # one, so fetch the right metrics rather than draw fold changes against the wrong baseline.
        ensure_loaded(ctx)
    _call(ctx, "_nw_refresh_controls")
    _call(ctx, "_nw_show_computing")
    _refresh_empty(ctx)
    if st.metrics is None or nanowell_empty_reason(ctx) is not None:
        return
    if st.loaded_for is not None and st.loaded_for[1] != st.baseline:
        return                                           # the right metrics are on their way
    channel = match_channel(ctx.state.channel.active_channel, st.data.channels)
    metric = st.metric
    if metric in ("brightness", "signal", "brightness_fc", "signal_fc") and channel is None:
        metric = "n"                                     # the channel metric cannot be drawn; say so below
    model = distribution_model(st.metrics, metric, channels=st.data.channels, channel=channel, axis=st.axis,
                               groups=page_groups(ctx), timepoint=st.timepoint, control=st.control,
                               time_source=st.data.time_source, baseline=st.baseline)
    tableless = [w for w in active_wells(ctx) if w not in set(st.data.wells)]
    if tableless:
        model.notes.insert(0, f"Selected without a nanowell table, so not drawn and not in any group's statistic: "
                              f"{', '.join(tableless)}.")
    if metric != st.metric:
        model.notes.insert(0, f"{st.metric.replace('_', ' ')} needs a channel with site intensities; the shared "
                              f"channel ({ctx.state.channel.active_channel}) has none, so Cell count is shown.")
    selected = [tuple(s) for s in st.selected_sites]
    st.hits = render_distribution(fig, model, selected=selected)
    st.model = model
    from review.core.plot_style import apply_export_style_to_current

    apply_export_style_to_current(ctx, fig, canvas)
    canvas.draw_idle()
    redraw_detail(ctx)


def redraw_detail(ctx) -> None:
    """Draw the site detail for the selected nanowells, or hide it when none is selected."""
    st = _state(ctx)
    fig, canvas = ctx.widgets.get_or_none("_nw_detail_fig"), ctx.widgets.get_or_none("_nw_detail_canvas")
    show = ctx.widgets.get_or_none("_nw_show_detail")
    if st is None or fig is None or canvas is None:
        return
    series = []
    if st.metrics is not None:
        for well, q, r in st.selected_sites:
            frame = site_series(st.metrics, well, q, r)
            if len(frame):
                series.append(((well, q, r), frame))
    if callable(show):
        show(bool(series))
    if not series:
        return
    channel = match_channel(ctx.state.channel.active_channel, st.data.channels)
    render_site_detail(fig, series, channel=channel, time_source=st.data.time_source)
    canvas.draw_idle()


def on_click(ctx, event) -> None:
    """A click on the Distribution figure: select the nanowell under it, add it with Shift (or ⌘/Ctrl), or
    clear the selection on empty space. The plate selection is never touched."""
    st = _state(ctx)
    if st is None or st.hits is None or event.inaxes is None or event.button != 1:
        return
    site = st.hits.nearest(event.inaxes, event.x, event.y, CLICK_RADIUS_PX)
    adding = _adding(event)
    current = [list(s) for s in st.selected_sites]
    if site is None:
        if adding:
            return
        st.selected_sites = []
    else:
        site = [str(site[0]), int(site[1]), int(site[2])]
        if not adding:
            st.selected_sites = [site]
        elif site in current:
            st.selected_sites = [s for s in current if s != site]
        else:
            st.selected_sites = (current + [site])[-MAX_SELECTED:]
    selection_changed(ctx)


def _adding(event) -> bool:
    """Whether the click adds to the selection: Shift, or ⌘ / Ctrl, held. Matplotlib reports the held keys as
    ``event.modifiers`` (3.7 and later) and sometimes as ``event.key``; either is enough."""
    held = {str(m).lower() for m in (getattr(event, "modifiers", None) or ())}
    held |= set(str(event.key or "").lower().split("+"))
    return bool(held & {"shift", "control", "ctrl", "cmd", "super", "meta"})


def selection_changed(ctx) -> None:
    """The nanowells selected for the site detail changed: save, and redraw both figures."""
    _save_soon(ctx)
    redraw(ctx)


def _save_soon(ctx) -> None:
    from core.safe import swallow

    with swallow("schedule the Nanowells page's save"):
        ctx.persistence.schedule_save("nanowell")


# ── lifecycle hooks and setters ──────────────────────────────────────────────
def _load_config(ctx) -> None:
    """Put this dataset's saved page choices on the state, once per dataset (D9): a dataset without a saved
    block gets the defaults, so nothing of the previous dataset's well, timepoint or nanowells carries over."""
    from core.safe import swallow

    st = _state(ctx)
    data_dir = str(ctx.state.dataset.data_dir or "")
    if st is None or st.config_for == data_dir:
        return
    st.config_for = data_dir
    with swallow("load the Nanowells page's saved choices"):
        ctx.persistence.load("nanowell")


def nw_on_activate(ctx) -> None:
    _load_config(ctx)
    if ctx.widgets.get_or_none("_nw_canvas") is None:
        return
    _call(ctx, "_nw_refresh_controls")
    ensure_loaded(ctx)
    redraw(ctx)


def nw_on_dataset_loaded(ctx) -> None:
    """A new dataset: drop the old tables; load again when the page is next shown (or now, if it is)."""
    st = _state(ctx)
    if st is None:
        return
    st.request += 1                                      # any load still running is for the old dataset
    st.reset_data()
    st.computing = False
    st.hits = None
    st.config_for = None
    _load_config(ctx)
    visible = ctx.widgets.get_or_none("_nw_page_visible")
    if ctx.widgets.get_or_none("_nw_canvas") is not None and callable(visible) and visible():
        ensure_loaded(ctx)
        redraw(ctx)


def nw_on_selection_changed(ctx) -> None:
    if ctx.widgets.get_or_none("_nw_canvas") is not None:
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)


def nw_on_channel_changed(ctx) -> None:
    nw_on_selection_changed(ctx)


def set_view(ctx, view: str) -> None:
    _set(ctx, "view", view)


def set_metric(ctx, metric: str) -> None:
    _set(ctx, "metric", metric)


def set_axis(ctx, axis: str) -> None:
    _set(ctx, "axis", axis)


def set_timepoint(ctx, timepoint: str) -> None:
    _set(ctx, "timepoint", timepoint or "")


def set_control(ctx, control: str) -> None:
    _set(ctx, "control", control or "")


def set_page_baseline(ctx, baseline: str) -> None:
    st = _state(ctx)
    if st is None or baseline == st.baseline:
        return
    st.baseline = baseline
    _save_soon(ctx)
    ensure_loaded(ctx)
    redraw(ctx)


def _set(ctx, name: str, value) -> None:
    st = _state(ctx)
    if st is None or getattr(st, name) == value:
        return
    setattr(st, name, value)
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def export_page_csv(ctx) -> None:
    """*Save nanowell data…*: every nanowell, timepoint and metric of the dataset, long format (D8)."""
    from review.core.csv_export import ask_save_csv

    st = _state(ctx)
    if st is None or st.metrics is None:
        ctx.notify.set_status("There are no nanowell tables loaded to save.")
        return
    path = ask_save_csv(ctx, "Save nanowell data", "nanowell_metrics.csv")
    if not path:
        return
    try:
        rows = export_long_csv(st, path)
    except OSError as exc:
        log.warning("Nanowell export to %s failed: %s", path, exc)
        ctx.notify.set_status(f"Could not write {path}: {exc}")
        return
    ctx.notify.set_status(f"Wrote {rows:,} rows → {path}")
