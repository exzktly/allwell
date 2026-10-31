"""Orchestration for the Nanowells data: load a dataset's tables, resolve replicate groups, export (D6, D8).

The page (Section V) calls these; nothing here draws. The compute modules beside it are pure; this module
is where the dataset on disk and the app's Sample Definitions come in.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from review.core.ui_helpers import EmptyReason

from domain.nanowell_roster import mixed_rules_note
from review.features.nanowell import cell_gate_compute as cg
from review.features.nanowell import rim_focus_compute as rfc
from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell import views_compute as vc
from review.features.nanowell.distribution_compute import (
    METRICS, PER_NANOWELL, TIME, WELLS, distribution_model, match_channel, metric_column,
)
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell.array_compute import array_scene, one_to_one
from review.features.nanowell import extremes_compute as ec
from review.features.nanowell import moving_compute as mvc
from review.features.nanowell import _anchor_queue as aq
from review.features.nanowell import _identity_controller as idc
from review.features.nanowell.identity_compute import identity_note, marks_legend, timepoint_marks
from review.features.nanowell import subset_compute as ss
from review.features.nanowell import statistics_compute as sc
from review.features.nanowell._statistics_render import render_statistics
from review.features.nanowell.image_compute import Crop, crops_for_sites, read_montage, site_crops
from review.features.nanowell.renderer import (
    ArrayDrawn, render_array, render_crop_grid, render_crop_strip, render_distribution, render_extremes, render_message,
    render_position, render_site_detail, render_trajectories,
)
from review.features.nanowell.state import (
    IDENTITY_COLOURS, IDENTITY_MODES, IMAGES, LAYER_SWITCHES, ArrayLayers, COLOURINGS, POSITIONS,
)
from review.features.nanowell.series_compute import crop_metric_lines, long_format, site_metrics, site_series
from review.features.nanowell.zoom_compute import (  # noqa: F401 — the band and its tests read them here
    BUTTON_STEP, WHEEL_STEP, ZOOM_IN_LIMIT, ZOOM_OUT_LIMIT, held_factor,
)

log = logging.getLogger("review")


def load_into(state, data_dir) -> None:
    """Read *data_dir*'s nanowell tables into *state* and compute the metrics at the state's baseline.

    A dataset with no ``nanowells/`` leaves ``state.data`` as ``None``; a scheme this version does not read
    leaves it ``None`` with the reason in ``state.load_error``.

    The counts are the **run's own**: there is no ``ctx`` here, so Cell Gating cannot be read and is not applied,
    and ``state.gate`` stays ``None`` — which the long-format export then states (``cell_gating``: "not applied").
    The page never loads through this (``ensure_loaded`` → ``gated_metrics`` does); it is a seam for tests that
    need the tables without a window. A caller that draws or exports for a person goes through ``ensure_loaded``.
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
    state.metrics = site_metrics(data, state.baseline, focus=state.rim_focus_rule())
    state.loaded_for = (str(data_dir), state.baseline)     # what the page's own load records (roll-up review X16)
    state.focus_for = state.rim_focus_rule().key()
    state.loaded_wells, state.parts_for = frozenset(data.available), str(data_dir)   # every well was read


def set_baseline(state, baseline: str) -> None:
    """Change the baseline and recompute every metric that depends on it — ungated, like :func:`load_into`, whose
    test seam this is; the page changes its baseline through ``set_page_baseline`` and ``ensure_loaded``."""
    state.baseline = baseline
    if state.data is not None:
        state.metrics = site_metrics(state.data, baseline, focus=state.rim_focus_rule())
        if state.loaded_for is not None:
            state.loaded_for = (state.loaded_for[0], baseline)
            state.focus_for = state.rim_focus_rule().key()


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


def export_long_csv(state, path, wells=None) -> int:
    """Write every site, timepoint and metric as a long-format CSV (tracker D8); returns the rows written. With
    *wells*, only theirs: the page reads only the wells it draws, so what else is loaded is whichever wells
    happened to be selected before.

    Written to a temporary name and moved into place, so a failed write never leaves half a table under the
    name a person will open.
    """
    if state.metrics is None or state.data is None:
        raise ValueError("There are no nanowell tables loaded to export.")
    metrics = state.metrics if wells is None else state.metrics[state.metrics["well"].isin(list(wells))]
    table = long_format(metrics, state.data.channels)
    table.insert(table.columns.get_loc("time_h") + 1, "time_source", state.data.time_source or "")
    table.insert(table.columns.get_loc("baseline_timepoint"), "baseline", state.baseline)
    # Every row is kept, and flagged: a file filtered to the page's Subset would lose rows a reader may want back,
    # and could not say which subset made it.
    # Which cells the counts count is said in the file itself, not only on the figure: a reader of the CSV alone
    # must be able to tell a gated count from the run's (cell population audit P3).
    table["cell_gating"] = (("not applied: the counts are the run's own" if state.gate is None else
                             cg.note(state.gate) or "Cell Gating leaves out no cell in these wells' nanowells"))
    table["in_subset"] = ss.inside(metrics, state.subset, state.data).to_numpy()
    table["subset"] = state.subset.label()
    table["subset_baseline"] = state.baseline
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
    """The wells the page draws, decided by ``plot_group_well_lists`` — the one rule every plotting tab uses, so a
    group-by variable, hidden sets and the solo-well toggles give this page the same wells as the others. Never
    every loaded well as a fallback: an empty selection is a stated empty state, not an arbitrary well."""
    from review.data.data_loading import plot_group_well_lists

    return sorted({w for _name, wells, _rset in plot_group_well_lists(ctx, fallback_to_all=False) for w in wells})


def tableless_wells(ctx) -> list[str]:
    """The wells the page draws that have no usable nanowell table: none in the folder, or one that was read and
    left out (``data.problems`` says why). A well whose table is still being read is not one of them."""
    st = _state(ctx)
    if st is None or st.data is None:
        return []
    have, available = set(st.data.wells), set(st.data.available)
    return [w for w in active_wells(ctx) if w not in have and (w not in available or w in st.loaded_wells)]


def page_groups(ctx) -> dict:
    """``group -> wells`` for the page: replicate sets over the active wells only (gap G9 of the plan)."""
    st = _state(ctx)
    excluded = set(st.excluded_wells) if st is not None else set()
    have = [w for w in active_wells(ctx) if (st is None or st.data is None or w in set(st.data.wells))
            and w not in excluded]
    return replicate_groups(ctx, have) if have else {}


def excluded_note(ctx) -> str:
    """The sentence every figure on the page carries while wells are excluded from it: which, and where to bring
    them back — a figure drawn short of a selected well says so (CLAUDE.md §7)."""
    st = _state(ctx)
    if st is None or not st.excluded_wells:
        return ""
    selected = set(active_wells(ctx))
    shown = [w for w in st.excluded_wells if w in selected] or list(st.excluded_wells)
    return f"Excluded on this page: {', '.join(shown)} (restore from Excluded on the band)."


def set_well_excluded(ctx, well: str, excluded: bool) -> None:
    """Leave *well* out of every figure on the page (or bring it back); kept with the page's settings. The rest of
    Review is untouched: this is the page's choice of wells, not the dataset's inclusion."""
    st = _state(ctx)
    if st is None or not well:
        return
    wells = [w for w in st.excluded_wells if w != well] + ([well] if excluded else [])
    if sorted(wells) == sorted(st.excluded_wells):
        return
    st.excluded_wells = sorted(wells)
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def restore_every_well(ctx) -> None:
    """Bring back every well excluded on the page."""
    st = _state(ctx)
    if st is None or not st.excluded_wells:
        return
    st.excluded_wells = []
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


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
    available = st.data.available
    if not available:
        return _empty("No well's nanowell table could be used.", " ".join(st.data.problems))
    from review.core.selection_controller import has_plottable_selection

    if not has_plottable_selection(ctx) or not page_groups(ctx):
        chosen = [w for w in active_wells(ctx) if w in set(available)]
        if chosen and st.computing:
            return _empty("Reading the nanowell tables…", "The figure appears when they are loaded.")
        if chosen and set(chosen) <= set(st.excluded_wells):
            return _empty("Every selected well is excluded on this page.",
                          f"Excluded: {', '.join(chosen)}. Restore them from Excluded on the band, or select "
                          "other wells on the plate.")
        if chosen:
            return _empty("None of the selected wells' nanowell tables could be used.",
                          " ".join(st.data.problems))
        return _empty("No nanowell wells selected.",
                      "Click wells with nanowell tables on the plate at the left: "
                      + ", ".join(available[:12]) + ("…" if len(available) > 12 else "") + ".")
    return None


def _refresh_empty(ctx) -> None:
    from core.safe import swallow
    from review.core.ui_helpers import refresh_empty_state_pages

    with swallow("refresh the Nanowells page's empty state"):
        refresh_empty_state_pages(ctx.window)


def _page_alive(ctx) -> bool:
    """Whether the page's canvas still exists as a Qt object — a worker's result can arrive after it is gone."""
    import shiboken6

    canvas = ctx.widgets.get_or_none("_nw_canvas")
    return canvas is not None and shiboken6.isValid(canvas)


def _call(ctx, name: str) -> None:
    fn = ctx.widgets.get_or_none(name)
    if callable(fn):
        fn()


def _metrics_of(data, baseline: str, gate=None, focus=None) -> pd.DataFrame | None:
    """The metrics of *data*, ``None`` with no data; with no well read yet (nothing selected) an empty table, so
    the page counts as loaded and says what to select rather than reading on."""
    if data is None:
        return None
    if not len(data.sites):
        return pd.DataFrame(columns=["well", "timepoint", "nw_q", "nw_r", "state"])
    return site_metrics(data, baseline, gate, focus)


def gate_version(ctx) -> int:
    """The version of which cells the analysis includes (``StatsCacheState.population_version``): every gating,
    curation or QC edit moves it."""
    return int(getattr(ctx.state.stats_cache, "population_version", 0))


def _cell_label(ctx, well: str, data) -> str | None:
    """The row cache's label for the per-cell CSV holding *well*'s cells: the well's own name, else the label
    registered for the CSV the nanowell tables found its cells in (``NanowellData.cell_tables``)."""
    paths = ctx.state.dataset.well_paths
    if well in paths:
        return well
    table = (getattr(data, "cell_tables", None) or {}).get(well)
    if table is None:
        return None
    wanted = Path(table).resolve()
    return next((label for label, path in paths.items() if Path(path).resolve() == wanted), None)


def gate_tally(ctx, data) -> cg.GateTally | None:
    """The cells Home's Cell Gating leaves out of *data*'s wells (``cell_gate_compute``), read from the row
    cache with the inclusion every other figure uses. Safe on a worker: the row cache is read there by the
    gating pass too. ``None`` with no data."""
    from review.data.data_loading import df_included_mask, get_rows

    if data is None or not len(data.sites):
        return None
    per_well, unread = [], []
    for well in data.wells:
        label = _cell_label(ctx, well, data)
        if label is None:
            counted = pd.to_numeric(data.sites.loc[data.sites["well"] == well, "n_cells"], errors="coerce")
            if counted.gt(0).any():                      # a well with no cell at all has nothing to gate
                unread.append(well)
            continue
        try:
            frame = get_rows(ctx, label)
        except Exception as exc:                         # a well that cannot be read keeps the run's counts
            log.warning("Nanowells: the cells of %s could not be read for Cell Gating (%s); its counts are the "
                        "run's own", well, exc)
            unread.append(well)
            continue
        if frame is not None and len(frame):
            per_well.append((well, frame, df_included_mask(frame).to_numpy()))
    return cg.tally(per_well, unread)


def gated_metrics(ctx, data, baseline: str, focus=None) -> pd.DataFrame | None:
    """:func:`site_metrics` with the cells Home's Cell Gating leaves out taken out and the nanowells whose rim was
    out of focus under *focus* (``rim_focus_compute.RimFocusRule``) left out — what the page draws and Batch Export
    exports, so the two never disagree."""
    return _metrics_of(data, baseline, gate_tally(ctx, data), focus)


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
    # Only the wells the page draws are read: a plate's other tables wait until they are selected, and a well
    # read once is kept (``st.well_parts``), so selecting it again reads nothing.
    wanted = frozenset(active_wells(ctx))
    if st.parts_for != str(data_dir or ""):
        st.well_parts, st.parts_for, st.loaded_wells = {}, str(data_dir or ""), frozenset()
    wells = wanted | st.loaded_wells
    key = (str(data_dir or ""), st.baseline)
    version = (gate_version(ctx), st.rim_focus_rule().key())
    if not force and st.computing and st.pending_for == (key, wells, version):
        return                                           # this very request is already running
    if (not force and st.loaded_for == key and wanted <= st.loaded_wells
            and (st.gated_for, st.focus_for) == version):
        if st.computing:
            # The current data is what is wanted again (a baseline changed and changed back): whatever is
            # running is for another key and now stale, so it must not land when it finishes.
            st.request += 1
            st.computing, st.pending_for = False, None
            _call(ctx, "_nw_show_computing")
        return
    st.request += 1
    request, baseline, focus = st.request, st.baseline, st.rim_focus_rule()
    st.computing, st.pending_for = True, (key, wells, version)
    same_wells = wanted <= st.loaded_wells and st.loaded_for is not None and st.loaded_for[0] == key[0]
    previous = st.data if same_wells else None
    parts = dict(st.well_parts)                          # the worker's own copy: it adds the wells it reads

    def job():
        # The cells Home's Cell Gating leaves out are tallied here too, at the version asked for: an edit made
        # while this runs moves the version, and the next draw asks again (``redraw``).
        if previous is not None:
            gate = gate_tally(ctx, previous)
            return previous, _metrics_of(previous, baseline, gate, focus), "", parts, gate
        try:
            data = load_nanowell_data(Path(data_dir), wells=sorted(wells), parts=parts) if data_dir else None
        except NanowellSchemeError as exc:
            return None, None, str(exc), parts, None
        gate = gate_tally(ctx, data)
        return data, _metrics_of(data, baseline, gate, focus), "", parts, gate

    def done(result):
        if request != st.request:
            return                                       # a newer request has been made; this one is stale
        data, metrics, error, read, gate = result
        st.data, st.metrics, st.load_error = data, metrics, error
        st.gate, (st.gated_for, st.focus_for) = gate, version
        st.loaded_for, st.computing = key, False
        if st.parts_for == key[0]:
            st.well_parts, st.loaded_wells = read, wells
        for problem in (data.problems if data is not None else []):
            log.warning("Nanowells: %s", problem)
        _apply_pending_anchor(ctx)
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)

    def failed(message):
        if request != st.request:
            return
        st.reset_data()
        st.computing, st.load_error, st.loaded_for = False, f"Loading failed: {message}", key
        st.gated_for, st.focus_for = version             # failed at this version: a redraw must not retry it
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
    stale_gate = st.loaded_for is not None and st.gated_for != gate_version(ctx)
    stale_focus = st.loaded_for is not None and st.focus_for != st.rim_focus_rule().key()
    if st.loaded_for is not None and (st.loaded_for[1] != st.baseline or stale_gate or stale_focus) \
            and not st.computing:
        # The baseline or the rim focus rule changed without a load (a reset, a restore), or Cell Gating changed
        # which cells count: what is on hand was computed for something else, so fetch the right metrics rather
        # than draw fold changes against the wrong baseline, counts that hold cells the gate has left out, or
        # counts from nanowells the rule now leaves out.
        ensure_loaded(ctx)
    _call(ctx, "_nw_refresh_controls")
    _call(ctx, "_nw_show_computing")
    _refresh_empty(ctx)
    if st.metrics is None or nanowell_empty_reason(ctx) is not None:
        idc.leave(ctx, st)
        return
    # The page's views share one figure; each keeps its own Properties settings, so a range typed for Distribution
    # never reaches the Array map or the Trajectories' hours (``plot_style.set_pref_scope``).
    from review.core.plot_style import set_pref_scope

    set_pref_scope(ctx, fig, "" if st.view == "distribution" else st.view)
    if st.loaded_for is not None and (st.loaded_for[1] != st.baseline or stale_focus):
        return                                           # the right metrics are on their way
    metric, channel, note = resolve_metric(st.view, st.metric, page_channel_name(ctx), st.data.channels)
    note = " ".join(filter(None, [note, excluded_note(ctx), cg.note(st.gate),
                                  rfc.note(st.metrics, st.rim_focus_rule(), page_wells(ctx))]))
    well = page_well(ctx)
    st.extreme_axes = {}
    if st.view == "identity":
        _draw_identity_page(ctx, st, fig, canvas, well=well, note=note)
        return
    idc.leave(ctx, st)
    if st.view == "extremes":
        _draw_extremes(ctx, st, fig, canvas, metric=metric, channel=channel, well=well, note=note)
        return
    tableless = tableless_wells(ctx)
    array = {}
    if st.view == "array":
        timepoint = page_timepoint(ctx, well)
        if st.region_for != (well, st.layers.positions):         # another well, or the other frame: fit it
            st.region, st.region_for = None, (well, st.layers.positions)
        image, image_note = _array_image(ctx, st, well, timepoint)
        array = {"layers": drawn_layers(st), "image": image, "image_note": image_note, "region": st.region,
                 "left_out": cg.left_out(st.gate)}
        hidden = OVERLAYS_HIDDEN_NOTE if st.overlays_hidden else ""
        note = " ".join(filter(None, [hidden, st.array_message, note]))
    st.hits, model = draw_view(
        fig, st.view, st.metrics, st.data, groups=page_groups(ctx), metric=metric, channel=channel,
        axis=st.axis, timepoint=shown_timepoint(ctx)[1] if st.view == "position" else st.timepoint,
        control=st.control, baseline=st.baseline, cohort=st.cohort, moving=st.moving,
        baseline_moving=st.baseline_moving, well=well,
        well_timepoint=page_timepoint(ctx, well), note=note, tableless=tableless,
        selected=[tuple(s) for s in st.selected_sites],
        responding_above=st.thresholds.get(metric_column(metric, channel)),
        subset=st.subset, stats_unit=st.stats_unit, stats_test=st.stats_test, stats_cache=_stats_cache(st), **array)
    if st.view == "statistics":
        pass                                             # its result is kept in ``st.stats_cache``; nothing to click
    elif isinstance(model, ArrayDrawn) or st.view == "array":
        st.drawn = model
        if model is not None and model.rendered is not None:
            _want_tiles(ctx, {(tile, None) for tile in model.rendered.unread} | set(model.rendered.wants))
        _call(ctx, "_nw_refresh_zoom")
    elif model is not None:
        st.model = model
    from review.core.plot_style import apply_export_style_to_current

    apply_export_style_to_current(ctx, fig, canvas)
    canvas.draw_idle()
    redraw_detail(ctx)


def _draw_identity_page(ctx, st, fig, canvas, *, well: str, note: str = "") -> None:
    """The Identity view on the page: through :func:`draw_view`, with the sidecar, montages and Blink frames the page
    holds (``_identity_controller.page_inputs``); the Blink timer follows what was drawn."""
    from review.core.plot_style import apply_export_style_to_current

    timepoint = idc.page_timepoint(st, well)
    st.hits, st.drawn = None, None                       # nothing to click and nothing to zoom
    _hits, drawn = draw_view(fig, "identity", st.metrics, st.data, groups=page_groups(ctx), metric=st.metric,
                             channel=None, axis=st.axis, timepoint=timepoint, control=st.control,
                             baseline=st.baseline, cohort=st.cohort, well=well, well_timepoint=timepoint,
                             note=note, identity=idc.page_inputs(ctx, st, well, timepoint, _fetch))
    idc.after_draw(ctx, st, drawn)
    apply_export_style_to_current(ctx, fig, canvas)
    canvas.draw_idle()
    redraw_detail(ctx)


_CHANNEL_METRICS = ("brightness", "signal", "brightness_fc", "signal_fc")


def resolve_metric(view: str, metric: str, active_channel, channels) -> tuple[str, str | None, str]:
    """The metric drawn, the shared channel spelled as the data spells it, and a note when the chosen metric
    cannot be drawn: a channel metric with no site intensities for the shared channel, or a metric with one
    value per nanowell on Trajectories, which draws values over time. The page and Batch Export both ask."""
    channel = match_channel(active_channel, channels)
    if view == "trajectories" and metric in PER_NANOWELL:
        return "n", None, (f"{METRICS[metric][0]} has one value per nanowell, not one per timepoint, so "
                           f"Trajectories shows Cell count.")
    if view == "extremes" and metric in PER_NANOWELL:
        return ec.FALLBACK, None, (f"{METRICS[metric][0]} has one value per nanowell, so no fold change over time: "
                                   f"the nanowells are ranked by {METRICS[ec.FALLBACK][0].lower()}.")
    if metric in _CHANNEL_METRICS and channel is None:
        drawn = ec.FALLBACK if view == "extremes" else "n"
        return drawn, None, (f"{metric.replace('_', ' ')} needs a channel with site intensities; the channel "
                             f"({active_channel or 'none chosen'}) has none, so {METRICS[drawn][0]} is shown.")
    if view == "extremes":
        return ec.fold_change_metric(metric)[0], channel, ""
    return metric, channel, ""


def page_channel_name(ctx) -> str:
    """The channel the page's Brightness and Nanowell fluorescence are for, as the user named it: the page's own *Channel*
    choice when it is set; else the app's shared channel selector when the tables carry that channel; else the
    tables' first fluorescence channel. The shared selector lives on the Plotting band, which this page does not
    show, so a shared channel the tables lack (the phase channel, a ratio) must not leave every fluorescence metric
    greyed with no control on the page to change it. The callers resolve the name with :func:`match_channel`."""
    st = _state(ctx)
    shared = ctx.state.channel.active_channel or ""
    if st is None:
        return shared
    if st.channel:
        return st.channel
    channels = list(st.data.channels) if st.data is not None else []
    if match_channel(shared, channels) is None and channels:
        return channels[0]
    return shared


def page_channel(ctx) -> str | None:
    """The page's channel spelled as the data spells it, or ``None`` when the data has no site intensities for it."""
    st = _state(ctx)
    if st is None or st.data is None:
        return None
    return match_channel(page_channel_name(ctx), st.data.channels)


def page_image_channel(st) -> str:
    """The channel the page's pictures are of: ``""`` for the phase image, or one of the run's fluorescence channels
    spelled as the data spells it. A saved choice the dataset does not have falls back to phase."""
    if st is None or st.data is None or not st.image_channel:
        return ""
    return match_channel(st.image_channel, getattr(st.data, "image_channels", ())) or ""


def display_key(st) -> str:
    """The name the page keeps its image channel's **Display range** under (``NanowellState.display_ranges``): the
    fluorescence channel, or the run's phase token for the phase image."""
    channel = page_image_channel(st)
    if channel:
        return channel.lower()
    phase = (getattr(st.data, "phase_token", "") or "") if st is not None and st.data is not None else ""
    return phase.lower() or "phase"


def display_range(ctx) -> tuple[float, float] | None:
    """The **Display range** ``(low, high)`` the page's pictures of its image channel are drawn with, in the raw
    image's units; ``None`` when unset, and each picture then takes its own percentile scale (Auto)."""
    st = _state(ctx)
    return None if st is None else st.display_ranges.get(display_key(st))


def set_display_range(ctx, low, high) -> None:
    """Draw the page's image channel between *low* and *high*, or — either ``None`` — back to Auto.

    The range is the page's own (``NanowellState.display_ranges``) and kept with its settings: it holds for every
    render of the channel — the crop strip, the pop-out, the Extremes montage and the Array view's full resolution —
    until it is changed here again. Other image viewers keep their own ranges and never change it. A range whose
    high is not above its low is refused rather than drawn black."""
    st = _state(ctx)
    if st is None:
        return
    key = display_key(st)
    if low is None or high is None:
        st.display_ranges.pop(key, None)
    elif float(high) > float(low):
        st.display_ranges[key] = (float(low), float(high))
    else:
        return
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def draw_view(fig, view: str, metrics, data, *, groups: dict, metric: str, channel: str | None, axis: str,
              timepoint: str, control: str, baseline: str, cohort: bool, well: str, well_timepoint: str,
              note: str = "", tableless=(), selected=(), responding_above: float | None = None, moving: int = 0,
              baseline_moving: int = 0, layers: ArrayLayers | None = None, image=None, image_note: str = "",
              region=None, subset: ss.Subset = ss.Subset(), identity: dict | None = None,
              stats_unit: str = sc.NANOWELLS, stats_test: str = "welch",
              stats_cache: dict | None = None, left_out=frozenset()) -> tuple[object, object]:
    """Draw *view* (Distribution, Array, Trajectories, Position, Identity or Statistics) into *fig*: ``(hit map,
    model)``.

    **The one composition of each view**, called by the page and by Batch Export's Nanowells mode (tracker I4),
    so a saved batch figure is the page's figure for the same choices — the notes, the exclusions and the
    empty states included — and fixing one fixes both. *groups* is ``group -> wells``; *timepoint* is the
    timepoint Position and *Along x: Wells* compare at; *well* and *well_timepoint* are what the Array view shows,
    with *layers* (the lattice map when none are given — a batch figure), *image* under it and *region* where it
    looks (:func:`draw_array`), and *left_out* the cells its Cells layer draws hollow; its model is the
    ``renderer.ArrayDrawn``. The hit map is ``None`` for views with nothing to click. *responding_above* is the user's cut for *metric*
    (tracker I9), which the Distribution view states the fraction responding at. *subset* is the page's
    *Subset* (``subset_compute``): every view but the Array draws only the nanowells inside it, saying how many it
    left out; the Array view draws them all and dims the rest. *moving* is the moving average's window in
    timepoints (``moving_compute``; 0 is off), which Distribution and Trajectories apply and the other views,
    each of one timepoint or one value per nanowell, do not. *baseline_moving* is the baseline's own window for a
    fold change, used only with *moving* on and never with the Earliest baseline
    (``moving_compute.baseline_window_for`` decides, here, so the page and Batch Export cannot differ).

    The Identity view draws *well* at *well_timepoint* against the well's reference timepoint
    (``_identity_controller.draw_identity``), with *identity* the page's inputs to it — its mode, the sidecar and
    montages it holds — or, without them, the distance map from the sidecar on disk; its model is the
    ``_identity_render.IdentityDrawn``. It has no metric, so the metric, Subset and grouping are not used.

    The Statistics view tests the samples the Distribution view draws *Along x: Wells* at *timepoint*
    (:func:`statistics_result`): the wells' nanowells (*stats_unit* ``nanowells``) or the groups' well medians
    (``wells``), by *stats_test*, every pair or each against *control*. Its model is the
    ``statistics_compute.StatisticsResult``; *stats_cache*, a dict the caller keeps, spares re-running the tests
    when nothing they depend on changed.
    """
    if view == "identity":
        return None, idc.draw_identity(fig, data, well=well, timepoint=well_timepoint, note=note, **(identity or {}))
    wells = [w for members in groups.values() for w in members]
    all_metrics = metrics
    metrics, filtered = ss.apply(metrics, subset, baseline, [well] if view == "array" else wells, data)
    if view != "array" and filtered is not None and filtered.kept == 0:
        render_message(fig, ss.empty_text(filtered), gid="nw-start-empty")
        return None, None
    # Wells whose rosters two versions decided differently count their rim differently; the figure says so (X35a-R4).
    mixed = mixed_rules_note({w: data.roster_rules.get(w) for w in wells if w in getattr(data, "roster_rules", {})})
    note = " ".join(filter(None, [note, f"{mixed}." if mixed else ""]))
    # A well analysed again when a timepoint was added holds rewritten earlier rows; the figure says so (X35a-R8).
    again = sorted(w for w in set(wells) if w in getattr(data, "reanalysed", {}))
    if again:
        note = " ".join(filter(None, [note, f"{', '.join(again)}: analysed again over every timepoint when a timepoint "
                                            "was added, so their earlier timepoints' numbers were rewritten."]))
    # Whether each address names one nanowell over time is checked per timepoint; a figure over wells not all
    # confirmed says so, and names the contradicted timepoints it left out (NANOWELL_IDENTITY_TRACKER C3).
    note = " ".join(filter(None, [note, identity_note(data, [well] if view == "array" else wells)]))
    if view == "array":
        dimmed = frozenset() if filtered is None else filtered.outside_sites
        if filtered is not None:
            note = " ".join(filter(None, [f"{filtered.total - filtered.kept:,} outside {filtered.label()}, drawn "
                                          "dimmed.", note]))
        drawn = draw_array(fig, all_metrics, data, well=well, timepoint=well_timepoint, metric=metric,
                           channel=channel, layers=layers, image=image, image_note=image_note, region=region,
                           selected=selected, note=note, dimmed=dimmed, left_out=left_out)
        return (drawn.hits if drawn is not None else None), drawn
    if filtered is not None:
        note = " ".join(filter(None, [ss.note(filtered), note]))
    baseline_moving = mvc.baseline_window_for(moving, baseline_moving, baseline)
    if view == "trajectories":
        marks = {w: timepoint_marks(data, w) for w in wells}
        column = metric_column(metric, channel)
        render_trajectories(fig, vc.trajectory_panels(metrics, wells, column, cohort=cohort, marks=marks,
                                                      moving=moving, baseline_moving=baseline_moving),
                            cohort=cohort, label=_label(metric, channel), time_source=data.time_source,
                            note=" ".join(filter(None, [mvc.describe(moving, column, baseline_moving), note])))
        return None, None
    if view == "position":
        series, missing = vc.position_series(metrics, wells, timepoint, metric_column(metric, channel),
                                             kinds={w: vc.lattice_kind(data, w, timepoint) for w in wells})
        # A well the Subset emptied is named in the filter's note, not as one without addresses.
        missing = [w for w in missing if filtered is None or filtered.per_well.get(w, 1) > 0]
        if missing:
            note = " ".join(filter(None, [note, f"No addressed nanowells at {timepoint}, so not drawn: "
                                                f"{', '.join(missing)}."]))
        render_position(fig, series, label=_label(metric, channel), timepoint=timepoint, note=note)
        return None, None
    if view == "statistics":
        model = distribution_model(metrics, metric, channels=data.channels, channel=channel, axis=WELLS,
                                   groups=groups, timepoint=timepoint, time_source=data.time_source,
                                   baseline=baseline, cohort=cohort)
        result = statistics_result(model, groups, unit=stats_unit, test=stats_test, control=control,
                                   cache=stats_cache)
        if tableless:
            note = " ".join(filter(None, [f"Selected without a nanowell table, so not compared: "
                                          f"{', '.join(tableless)}.", note]))
        render_statistics(fig, result, note=note)
        return None, result
    model = distribution_model(metrics, metric, channels=data.channels, channel=channel, axis=axis, groups=groups,
                               timepoint=timepoint, control=control, time_source=data.time_source,
                               baseline=baseline, cohort=cohort, responding_above=responding_above,
                               moving=moving, baseline_moving=baseline_moving)
    model.starting = subset.start_bounds()
    if model.axis == TIME:
        model.identity_marks = {(w, tp): v for w in wells for tp, v in timepoint_marks(data, w).items()}
        legend = marks_legend(model.identity_marks.values())
        if legend:
            model.notes.append(legend)
    if tableless:
        model.notes.insert(0, f"Selected without a nanowell table, so not drawn and not in any group's statistic: "
                              f"{', '.join(tableless)}.")
    if note:
        model.notes.insert(0, note)
    return render_distribution(fig, model, selected=selected), model


#: The Distribution model's notes the Statistics view carries: what changes which values were compared (the fixed
#: cohort) and how the times were read. The rest describe the beeswarm (packing, the joined line, the fraction
#: responding) and say nothing about a test.
_STATS_NOTES = ("Fixed cohort", "Times are")


def statistics_result(model, groups: dict, *, unit: str, test: str, control: str = "",
                      cache: dict | None = None) -> sc.StatisticsResult:
    """The Statistics view's result for a Distribution *model* drawn *Along x: Wells*: its samples of *unit*
    (``statistics_compute.samples_from_model``) compared by *test*.

    *cache*, when given, holds the last result under everything it was computed from — the samples' names and
    values, the unit, test, control, metric and timepoint — and that result is returned when they all match, so a
    redraw for a theme or a resize does not run the tests and the bootstrap again. Any change to the values (a
    Subset, the cohort, the baseline, an excluded well) changes the key."""
    samples = sc.samples_from_model(model, groups, unit)
    key = (unit, test, control, model.column, model.timepoint,
           tuple((s.name, s.wells, s.values.tobytes()) for s in samples))
    if cache is not None and cache.get("key") == key:
        return cache["result"]
    result = sc.compare(samples, test, control=control, unit=unit, label=model.label, timepoint=model.timepoint)
    result.notes = [n for n in model.notes if n.startswith(_STATS_NOTES)]
    if cache is not None:
        cache.clear()
        cache.update(key=key, result=result)
    return result


def _stats_cache(st) -> dict:
    if not isinstance(st.stats_cache, dict):
        st.stats_cache = {}
    return st.stats_cache


def _label(metric: str, channel: str | None) -> str:
    return METRICS[metric][0] + (f" · {channel}" if METRICS[metric][1] and channel else "")


def page_wells(ctx) -> list[str]:
    """The wells the page draws, in group order: the wells the Array view can show."""
    return [w for members in page_groups(ctx).values() for w in members]


def page_well(ctx) -> str:
    """The one well the Array view shows: the page's choice if it is drawn, else the first."""
    st = _state(ctx)
    wells = page_wells(ctx)
    return st.well if st is not None and st.well in wells else (wells[0] if wells else "")


def page_timepoint(ctx, well: str) -> str:
    """The timepoint the Array view shows: the page's choice if the well has it, else its first."""
    st = _state(ctx)
    tps = st.data.timepoints(well) if st is not None and st.data is not None else []
    return st.timepoint if st.timepoint in tps else (tps[0] if tps else "")


def shown_timepoint(ctx) -> tuple[list, str]:
    """The timepoints the page's timepoint control offers for the current view, in order, and the one drawn.

    The Array view shows one well, so it offers that well's timepoints and starts at its first
    (the seeding); Position and *Along x: Wells* compare wells at one timepoint, so they offer every
    timepoint and start at the latest (U13). One ``timepoint`` on the state serves all of them, so a choice
    made on one view carries to the next wherever that timepoint exists.
    """
    st = _state(ctx)
    if st is None or st.data is None:
        return [], ""
    if st.view == "array":
        well = page_well(ctx)
        return list(st.data.timepoints(well)), page_timepoint(ctx, well)
    if st.view == "identity":
        # One well's timepoint against its reference: its first not confirmed unless one is chosen.
        well = page_well(ctx)
        return list(st.data.timepoints(well)), idc.page_timepoint(st, well)
    if st.view == "extremes":
        # One well's nanowells, ranked at one of its timepoints: its latest unless one is chosen (as U13).
        tps = list(st.data.timepoints(page_well(ctx)))
        return tps, st.timepoint if st.timepoint in tps else (tps[-1] if tps else "")
    tps = list(st.data.timepoints())
    if (st.view == "distribution" and st.axis == "wells") or st.view == "statistics":
        # The figure draws the drawn wells' timepoints, so the combo offers those and names the one drawn — not
        # the dataset's latest, which the drawn wells may not have (roll-up review X17).
        have = {tp for wells in page_groups(ctx).values() for well in wells for tp in st.data.timepoints(well)}
        tps = [tp for tp in tps if tp in have] or tps
    return tps, st.timepoint if st.timepoint in tps else (tps[-1] if tps else "")


# ── the Array view ───────────────────────────────────────────────────────────
def batch_array_layers() -> ArrayLayers:
    """The layers a figure drawn without the page's own is drawn with — a Batch Export figure: the lattice map,
    with no image, since the image layers read a montage or the raw tiles per acquisition."""
    return ArrayLayers.preset("map")


def draw_array(fig, metrics, data, *, well: str, timepoint: str, metric: str, channel: str | None,
               layers: ArrayLayers | None = None, image=None, image_note: str = "", region=None, selected=(),
               note: str = "", dimmed=frozenset(), left_out=frozenset()) -> ArrayDrawn | None:
    """**The one composition of the Array view**, for the page and for Batch Export (through :func:`draw_view`).

    *layers* says what is drawn (the lattice map when none are given); *image* is the run montage or the tile
    store the caller holds for the well frame, *image_note* what it says about it (still reading, or why there is
    none). *left_out* is the cells Cell Gating leaves out (``cell_gate_compute.GateTally.left_out``), which the
    Cells layer draws hollow. ``None`` when there is nothing at all to draw, and the figure then says why.
    """
    layers = layers or batch_array_layers()
    if not well:
        render_message(fig, "Select a well with a nanowell table.", gid="nw-array-missing")
        return None
    scene = array_scene(metrics, data, well, timepoint, metric_column(metric, channel), positions=layers.positions,
                        anchors=layers.anchor, cells=layers.cells, left_out=left_out)
    if scene.frame == "well" and image is not None and hasattr(image, "source"):
        # A plate placed from a scan pattern records no tile size; the tile store read it from the first image.
        width, height = image.source.tile_size_um
        scene.tiles = [t if t.width_um and t.height_um else replace(t, width_um=width, height_um=height)
                       for t in scene.tiles]
    in_well = scene.frame == "well"
    if not len(scene.sites) and not (in_well and ((layers.tiles and scene.tiles) or image is not None)):
        says = f"{well} at {timepoint} has {scene.no_places}, so there is nothing to draw."
        if in_well and layers.image != "off" and image_note:
            says += f" {image_note}"
        if not in_well and (layers.image != "off" or layers.tiles or layers.anchor):
            says += (" The image, the tiles and the anchor are in the well frame, not on the lattice: switch "
                     "Positions to Measured to draw them.")
        render_message(fig, says, gid="nw-array-missing")
        return None
    return render_array(fig, scene, layers, label=_label(metric, channel), image=image, image_note=image_note,
                        selected=selected, region=region, note=note, dimmed=dimmed)


def _array_image(ctx, st, well: str, timepoint: str) -> tuple[object, str]:
    """``(image, what to say about it)`` for the Array view's image layer at (*well*, *timepoint*): the run's
    montage, read on a worker once; or the raw tiles, held in a store whose tiles are read as the view asks."""
    kind = st.layers.image if not st.layers.lattice() else "off"
    channel = page_image_channel(st)
    if kind != "tiles" or st.tile_key != (well, timepoint, channel):
        st.release_tiles()                               # another well, timepoint or channel, or no tiles: let them go
    if kind == "off":
        return None, ""
    if kind == "montage":
        # The run kept a phase montage only: said on the figure, so a fluorescence choice is never shown phase
        # pixels without a word (CLAUDE.md §7).
        phase_only = (f"The run montage is the phase image; choose Full resolution to see {channel}."
                      if channel else
                      "The run montage is an 8-bit picture, drawn as the run saved it; the Display range applies to "
                      "Full resolution." if display_range(ctx) is not None else "")
        if (well, timepoint) in st.montages:
            montage, why = st.montages[(well, timepoint)]
            return montage, " ".join(filter(None, [phase_only, "" if montage is not None else why]))
        # The montage is a PNG of up to 4096 px in a zip: read it on a worker, and draw it when it arrives.
        data = st.data
        _fetch(ctx, "montages", (well, timepoint), lambda: read_montage(data, well, timepoint),
               failed=lambda message: (None, f"The montage could not be read: {message}"))
        return None, " ".join(filter(None, [phase_only, "Reading the montage…"]))
    if st.tile_key is None:
        st.tile_key = (well, timepoint, channel)
        source, st.tile_why = fmc.montage_source(st.data, well, timepoint, channel)
        st.tile_store = fmc.TileStore(source) if source is not None else None
        if source is not None:
            _load_tiles(ctx, [t.tile for t in source.tiles], level=None)
    store = st.tile_store
    if store is None:
        return None, st.tile_why
    store.display = display_range(ctx)
    source, failed = store.source, dict(store.failed)
    total, read = len(source.tiles), store.loaded()
    said = [f"Full resolution, {channel or 'phase'}, from the raw tiles in {source.archive.name}"
            + (f", on the display range {store.display[0]:.4g} to {store.display[1]:.4g}."
               if store.display is not None else ".")]
    if read + len(failed) < total:
        said.append(f"Reading tiles: {read} of {total}…")
    placed = {str(t.get("tile")) for t in ((st.data.grids.get(well) or {}).get(timepoint) or {}).get("tiles") or []}
    unfound = sorted(placed - {t.tile for t in source.tiles})
    if unfound:
        said.append(f"{len(unfound)} tile(s) have no image in {source.archive.name} and are not drawn: "
                    f"{', '.join(unfound)}.")
    if failed:
        said.append(f"{len(failed)} tile(s) could not be read and are not drawn: "
                    + "; ".join(f"{tile} ({why})" for tile, why in sorted(failed.items())))
    return store, " ".join(said)


def _want_tiles(ctx, wanted) -> None:
    """Read what the view asked for, once: a read already on its way is not sent again."""
    st = _state(ctx)
    by_level: dict = {}
    for tile, level in set(wanted) - st.tile_pending:
        by_level.setdefault(level, []).append(tile)
    for level, names in by_level.items():
        _load_tiles(ctx, sorted(names), level=level)


def _load_tiles(ctx, names, *, level) -> None:
    """Read *names* into the tile store on a worker — their coarse levels, and *level* when it is finer — and
    draw again when they arrive. A store moved away from since (another well, timepoint or dataset) is cancelled
    at its next tile, and its result delivers to nothing."""
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    store = st.tile_store
    if store is None:
        return
    keys = {(name, level) for name in names}
    st.tile_pending |= keys
    total = len(names)

    def job(reporter):
        for k, name in enumerate(names, 1):
            store.load([name], level=level)
            reporter.progress(k, total)
        return names

    def done(_names) -> None:
        if st.tile_store is store:
            st.tile_pending -= keys
            redraw(ctx)

    def failed(message) -> None:
        log.warning("Nanowells: tiles of %s could not be read: %s", store.source.well, message)
        if st.tile_store is store:
            st.tile_pending -= keys
            st.tile_why = f"Some tiles could not be read: {message}"
            redraw(ctx)

    def progress(done_count, of) -> None:
        # The first read of a whole well says how far it has got, a tenth at a time.
        if level is None and st.tile_store is store and done_count < of and done_count % max(of // 10, 1) == 0:
            redraw(ctx)

    st.tile_bridges = st.tile_bridges[-8:] + [run_off_thread(job, on_done=done, on_failed=failed,
                                                             on_progress=progress)]


# ── the Array view's layers ──────────────────────────────────────────────────
#: Said on the figure while the *Overlays* button hides the coloured layers, so a view with no nanowells drawn
#: cannot be mistaken for a timepoint that has none (CLAUDE.md §7: degraded output must say so).
OVERLAYS_HIDDEN_NOTE = "Overlays hidden — press Overlays to show the nanowells, tiles, anchor and cells again."


def drawn_layers(st) -> ArrayLayers:
    """The layers the Array view draws: the ones switched on, less the coloured overlays while *Overlays* hides
    them."""
    return st.layers.without_overlays() if st.overlays_hidden else st.layers


def set_overlays(ctx, shown: bool) -> None:
    """*Overlays*: take every coloured layer off the Array view at once, or put back exactly the ones that were on."""
    st = _state(ctx)
    if st is None or st.overlays_hidden == (not shown):
        return
    st.overlays_hidden = not shown
    if st.overlays_hidden:
        st.anchor_mode = False                       # an anchor click with the anchor not drawn would land unseen
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_layer(ctx, name: str, value) -> None:
    """Turn one of the Array view's layers on or off, or choose its image, positions or colouring."""
    st = _state(ctx)
    allowed = {"image": IMAGES, "positions": POSITIONS, "colour": COLOURINGS}
    if st is None or (name in allowed and value not in allowed[name]) or (
            name not in allowed and name not in LAYER_SWITCHES) or getattr(st.layers, name) == value:
        return
    setattr(st.layers, name, value if name in allowed else bool(value))
    if name == "positions" or (name == "anchor" and not value):
        st.anchor_mode = False
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def apply_array_preset(ctx, name: str) -> None:
    """Set every layer at once to one of the views the Array view replaced (``ArrayLayers.preset``)."""
    st = _state(ctx)
    if st is None or name not in ArrayLayers.PRESETS:
        return
    st.layers = ArrayLayers.preset(name)
    st.region, st.anchor_mode = None, False
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def array_fit(ctx) -> None:
    """Show everything drawn."""
    st = _state(ctx)
    if st is not None:
        st.region = None
        redraw(ctx)


def array_um_per_px(ctx) -> float | None:
    """The image's own pixel size, for *1:1*: the raw tiles' or the run montage's; ``None`` without an image."""
    st = _state(ctx)
    drawn = st.drawn if st is not None else None
    if drawn is None or drawn.frame != "well":
        return None
    if st.layers.image == "tiles" and st.tile_store is not None:
        return st.tile_store.source.pixel_size_um
    montage = st.montages.get((page_well(ctx), page_timepoint(ctx, page_well(ctx))), (None, ""))[0]
    return montage.um_per_px if st.layers.image == "montage" and montage is not None else None


def array_one_to_one(ctx) -> None:
    """One image pixel per figure pixel, about the centre of the view."""
    st, um = _state(ctx), array_um_per_px(ctx)
    if um is None or st.drawn is None:
        return
    st.region = one_to_one(st.drawn.region, *st.drawn.size_px, um)
    redraw(ctx)


#: A press that moves less than this many pixels before it is released is a click, not a drag.
CLICK_SLOP_PX = 4


def array_zoom_factor(drawn) -> float | None:
    """How far the Array view is zoomed against *Fit* (1 is fitted, 2 shows half the width); ``None`` with
    nothing drawn."""
    if drawn is None or drawn.fit is None or drawn.region is None:
        return None
    width = drawn.region[2] - drawn.region[0]
    return (drawn.fit[2] - drawn.fit[0]) / width if width > 0 else None


def _zoom_to(ctx, st, factor: float, at_um) -> None:
    """Zoom the drawn view by *factor* (under 1 zooms in) about *at_um*, within the zoom limits, and draw it."""
    drawn = st.drawn
    # A view already past a limit (a refit to a new axes shape can put it there) is not stepped further past it;
    # otherwise the step is shortened to land on the limit (``zoom_compute.held_factor``).
    factor = held_factor(array_zoom_factor(drawn), factor)
    if factor == 1.0:
        return
    st.region = fmc.zoom_region(drawn.region, factor, at_um)
    redraw(ctx)


#: A double-clicked nanowell fills the view's shorter side with this much room around it (1 is edge to edge).
FOCUS_MARGIN = 1.25


def nanowell_at(drawn, x_um: float, y_um: float, slop_um: float) -> int | None:
    """The index of the nanowell under the well-frame point, in *drawn*'s scene: the one whose disc holds it, or
    the nearest within *slop_um* of its rim; ``None`` on empty space. Found whether or not the nanowells are drawn,
    so a double-click works with the overlays hidden."""
    sites = getattr(drawn, "sites", None)
    if sites is None or not len(sites.x):
        return None
    gap = np.hypot(np.asarray(sites.x) - x_um, np.asarray(sites.y) - y_um) - np.asarray(sites.radius)
    k = int(np.argmin(gap))
    return k if gap[k] <= slop_um else None


def focus_nanowell_at(ctx, event) -> None:
    """A double-click on the Array view: centre the nanowell under it, zoom until it fills the view, and select it
    so its site detail opens. On empty space it does nothing."""
    st = _state(ctx)
    drawn = st.drawn if st is not None else None
    if drawn is None or drawn.region is None or event.inaxes is not drawn.ax or event.xdata is None:
        return
    slop = CLICK_RADIUS_PX * (drawn.region[2] - drawn.region[0]) / max(drawn.size_px[0], 1)
    k = nanowell_at(drawn, event.xdata, event.ydata, slop)
    if k is None:
        return
    sites = drawn.sites
    x, y, half = float(sites.x[k]), float(sites.y[k]), float(sites.radius[k]) * FOCUS_MARGIN
    st.region = (x - half, y - half, x + half, y + half)        # widened to the axes' shape when drawn
    site = sites.sites[k]
    if site[1] is not None and site[2] is not None:
        _select(ctx, site, False)                                # redraws, at the new region
    else:
        redraw(ctx)                                              # an unaddressed nanowell cannot be selected


def on_canvas_resized(ctx) -> None:
    """The page's canvas settled at a new size: redraw the Array view if its axes no longer have the size its region
    was fitted to. Without it the view kept the old fit — a resize squashed every nanowell into an oval, and a draw
    made while the canvas was still being laid out (a few pixels tall) showed one flattened row of the array.

    A canvas no longer on screen is left alone. The re-fit fires a moment after the resize, and by then the page may
    have been closed: a redraw there queues matplotlib's deferred draw, which then runs on a canvas already deleted
    (``RuntimeError: Internal C++ object (FigureCanvasQTAgg) already deleted``). A hidden canvas has nothing to re-fit
    for; showing it again resizes it, which re-fits it then."""
    st = _state(ctx)
    drawn = st.drawn if st is not None else None
    if st is None or st.view != "array" or drawn is None or drawn.ax is None or drawn.ax.figure is None:
        return
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is None or not canvas.isVisible():
        return
    box = drawn.ax.get_window_extent()
    if (max(int(round(box.width)), 1), max(int(round(box.height)), 1)) != tuple(drawn.size_px):
        redraw(ctx)


def array_zoom(ctx, factor: float) -> None:
    """*−* / *+*: zoom the Array view by *factor* about the centre of what it shows."""
    st = _state(ctx)
    drawn = st.drawn if st is not None and st.view == "array" else None
    if drawn is None or drawn.region is None:
        return
    x0, y0, x1, y1 = drawn.region
    _zoom_to(ctx, st, factor, ((x0 + x1) / 2, (y0 + y1) / 2))


def _array_press(st, event) -> None:
    drawn = st.drawn
    if drawn is None or event.button != 1 or event.inaxes is not drawn.ax:
        return
    ax = drawn.ax
    st.press = (event.x, event.y, ax.get_xlim(), ax.get_ylim(), ax.transData.inverted().frozen(), _adding(event))


def set_identity_colour(ctx, colour: str) -> None:
    """Choose what the Identity view's Distance map colours by (``state.IDENTITY_COLOURS``); kept with the page's
    settings."""
    st = _state(ctx)
    if st is None or colour not in IDENTITY_COLOURS or st.identity_colour == colour:
        return
    st.identity_colour = colour
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_identity_focus(ctx, text: str) -> None:
    """Go to the nanowell *text* names on the Identity view (``q,r`` or a ring label; ``""``: none,
    ``identity_focus_compute``): forget the kept zoom so the redraw centres on it, and mark it on every mode."""
    st = _state(ctx)
    text = (text or "").strip()
    if st is None or (text == st.identity_focus and not text):
        return
    st.identity_focus = text
    st.identity_zoom, st.identity_zoom_for = {}, None
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_identity_mode(ctx, mode: str) -> None:
    """Choose what the Identity view shows (``state.IDENTITY_MODES``); kept with the page's settings."""
    st = _state(ctx)
    if st is None or mode not in IDENTITY_MODES or st.identity_mode == mode:
        return
    st.identity_mode = mode
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def on_motion(ctx, event) -> None:
    """A drag on the Array view pans it; the pixels follow at once and the image is drawn again on release. On the
    Identity view a drag pans the panel pressed, and on *Side by side* the pointer lights the address under it."""
    st = _state(ctx)
    if st is not None and st.view == "identity":
        idc.on_motion(ctx, event)
        return
    if st is None or st.press is None or st.view != "array" or st.drawn is None or event.x is None:
        return
    x, y, xlim, ylim, inverse, _adding_ = st.press
    (px, py), (cx, cy) = inverse.transform((x, y)), inverse.transform((event.x, event.y))
    st.drawn.ax.set_xlim(xlim[0] + px - cx, xlim[1] + px - cx)
    st.drawn.ax.set_ylim(ylim[0] + py - cy, ylim[1] + py - cy)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()


def on_release(ctx, event) -> None:
    """The end of a press on the Array view: a drag keeps where it panned to; a click sets the anchor (with *Set
    anchor* on) or selects the nanowell under it, as on the Distribution figure."""
    st = _state(ctx)
    if st is not None and st.view == "identity":
        idc.on_release(ctx, event)
        return
    press = st.press if st is not None else None
    if press is None:
        return
    st.press = None
    if st.view != "array" or st.drawn is None or event.button != 1:
        return
    x, y, _xlim, _ylim, inverse, adding = press
    ex, ey = (x, y) if event.x is None else (event.x, event.y)
    if abs(ex - x) + abs(ey - y) > CLICK_SLOP_PX:
        (x0, x1), (y1, y0) = st.drawn.ax.get_xlim(), st.drawn.ax.get_ylim()
        st.region = (x0, y0, x1, y1)
        redraw(ctx)
        return
    if st.anchor_mode and st.drawn.frame == "well":
        set_anchor_at(ctx, *inverse.transform((x, y)))
        return
    _select(ctx, st.hits.nearest(st.drawn.ax, x, y, CLICK_RADIUS_PX) if st.hits is not None else None, adding)


def on_scroll(ctx, event) -> None:
    """The wheel zooms the Array view about the pointer — or about the view's centre when the pointer is on the
    figure but off the image (the colour bar, the footer). Over VNC from a phone a two-finger swipe arrives as the
    wheel, at wherever the remote pointer was last left, which is often not over the image."""
    st = _state(ctx)
    if st is not None and st.view == "identity":
        idc.on_scroll(ctx, event)                # every Identity sub-view zooms its own panels
        return
    drawn = st.drawn if st is not None and st.view == "array" else None
    if drawn is None or drawn.region is None or not event.step:
        return
    if event.inaxes is drawn.ax and event.xdata is not None:
        at = (event.xdata, event.ydata)
    else:
        x0, y0, x1, y1 = drawn.region
        at = ((x0 + x1) / 2, (y0 + y1) / 2)
    _zoom_to(ctx, st, WHEEL_STEP ** event.step, at)


# ── the anchor ───────────────────────────────────────────────────────────────
def anchor_controls(ctx) -> tuple[bool, bool, bool]:
    """Whether *Set anchor*, *Clear anchor* and *Re-run anchored wells…* can do anything now: an anchor is set only
    in the well frame, with the anchor layer on, where it can change the result (``ANCHORABLE``)."""
    st = _state(ctx)
    if st is None or st.data is None or st.view != "array":
        return False, False, False
    well = page_well(ctx)
    timepoint = page_timepoint(ctx, well)
    status = ((st.data.grids.get(well) or {}).get(timepoint) or {}).get("status")
    can = (bool(well) and drawn_layers(st).anchor and not st.layers.lattice() and fmc.anchors_file(st.data) is not None
           and status in fmc.ANCHORABLE)
    anchor, _problem = fmc.read_well_anchor(st.data, well, timepoint) if can else (None, "")
    return can, can and anchor is not None, bool(fmc.anchored_wells(st.data))


def set_anchor_mode(ctx, on: bool) -> None:
    """*Set anchor*: while on, a click on the Array view anchors the well there; a drag still pans."""
    st = _state(ctx)
    if st is not None:
        st.anchor_mode = bool(on) and anchor_controls(ctx)[0]
        _call(ctx, "_nw_refresh_controls")


def open_for_anchor(ctx, well: str, timepoint: str, *, zoom: float | None = None) -> bool:
    """The ``nanowell.open_anchor`` service: show *well* at *timepoint* on the Array view, ready for *Set anchor*.

    QC Triage and Home's health card offer it for a timepoint the run left unaddressed. The well is added to the
    plate selection (never replacing it) so the page draws it; the view, well and timepoint are chosen; the anchor
    layer is turned on in the well frame with the overlays shown, since an anchor is set only there; and the page
    is brought forward. *Set anchor* itself waits for the well's tables (:func:`_apply_pending_anchor`), because
    whether the timepoint can take an anchor is read from its grid record. With *zoom* the view opens that many
    times *Fit* about the centre of the whole-well montage (``full_montage_compute.CentreZoom``) rather than
    fitted: the anchor queue's way in. Returns whether the page was shown."""
    st = _state(ctx)
    notebook = getattr(ctx.window, "_notebook", None)
    if st is None or notebook is None or not well:
        return False
    selected = set(ctx.state.selection.selected_wells)
    if well not in selected:
        from review.core.selection_controller import set_selected_wells
        set_selected_wells(ctx, selected | {well})
    st.view, st.well, st.timepoint = "array", well, timepoint or ""
    if not st.layers.anchor or st.layers.lattice():
        st.layers = replace(st.layers, anchor=True, positions="measured" if st.layers.lattice() else
                            st.layers.positions)
    st.overlays_hidden, st.region, st.array_message = False, None, ""
    st.anchor_pending = (well, timepoint or "")
    st.anchor_zoom = float(zoom) if zoom else None
    _save_soon(ctx)
    if not notebook.setCurrentByName("Nanowells"):
        st.anchor_pending = st.anchor_zoom = None
        return False
    if ctx.widgets.get_or_none("_nw_canvas") is not None:
        ensure_loaded(ctx)
        _apply_pending_anchor(ctx)
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)
    return True


def _apply_pending_anchor(ctx) -> None:
    """Turn *Set anchor* on for the timepoint :func:`open_for_anchor` was asked for, once its tables are loaded.

    When the shown timepoint cannot take an anchor (its grid status is not one an anchor changes, the anchors file
    has no place, the well is not drawn), the figure says why rather than leaving the button silently off."""
    st = _state(ctx)
    if st is None or st.anchor_pending is None or st.data is None or st.computing:
        return
    well, timepoint = st.anchor_pending
    zoom, st.anchor_pending, st.anchor_zoom = st.anchor_zoom, None, None
    if page_well(ctx) != well:
        st.array_message = (f"{well} is not among the wells this page draws (a hidden set or a group-by variable "
                            "may leave it out), so its anchor cannot be set here.")
        return
    if zoom:
        # Set only now that the page draws this well: a draw before its tables landed showed another well, and the
        # change of well refits the view. ``region_for`` is stamped so the next draw keeps it.
        st.region, st.region_for = fmc.CentreZoom(zoom), (well, st.layers.positions)
    if anchor_controls(ctx)[0]:
        st.anchor_mode = True
        return
    status = ((st.data.grids.get(well) or {}).get(page_timepoint(ctx, well)) or {}).get("status") or "no status"
    st.array_message = (f"{well} at {page_timepoint(ctx, well)} is {status.replace('_', ' ')}: an anchor set by hand "
                        "cannot change that, so Set anchor is off.")


def _anchor_change(ctx, change, failure: str) -> tuple[str, str] | None:
    """Apply *change* to the shown well and timepoint's anchor; the ``(well, timepoint)`` changed, or ``None`` when
    nothing was (no anchors file, or it could not be written — said on the figure)."""
    from util.nanowell_anchors import AnchorsFileError

    st = _state(ctx)
    path = fmc.anchors_file(st.data) if st is not None and st.data is not None else None
    if path is None:
        return None
    well = page_well(ctx)
    timepoint = page_timepoint(ctx, well)
    changed = None
    try:
        change(path, well, timepoint)
        st.array_message, changed = "", (well, timepoint)
    except (AnchorsFileError, OSError) as exc:
        st.array_message = f"{failure}: {exc}"
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)
    return changed


def set_anchor_at(ctx, x_um: float, y_um: float) -> None:
    """Anchor the shown well at the shown timepoint at the well-frame point clicked (``in/nanowell_anchors.json``):
    the run snaps it to the nearest nanowell, so it need only land on the central square."""
    from util.nanowell_anchors import set_anchor

    changed = _anchor_change(ctx, lambda path, well, tp: set_anchor(path, well, tp, (float(x_um), float(y_um))),
                             "The anchor could not be saved")
    if changed is not None:
        aq.anchor_set(ctx, *changed)                 # the Anchor queue, when walking, moves on to the next


def clear_anchor(ctx) -> None:
    from util.nanowell_anchors import clear_anchor as clear

    _anchor_change(ctx, clear, "The anchor could not be removed")


def rerun_anchored(ctx) -> bool:
    """Open Analyze on this dataset limited to the anchored wells, each forced so every timepoint is located again
    with its anchor (Incremental off: every well is forced anyway, and a remote nanowell run refuses it)."""
    from review.core.analyze_handoff import open_in_analyze

    st = _state(ctx)
    wells = fmc.anchored_wells(st.data) if st is not None and st.data is not None else []
    if wells and open_in_analyze(ctx, force_wells=wells, only_wells=wells, incremental=False):
        return True
    if st is not None:
        st.array_message = "Analyze could not be opened on this dataset from here."
        redraw(ctx)
    return False


# ── the Extremes view ───────────────────────────────────────────────────────
def _draw_extremes(ctx, st, fig, canvas, *, metric: str, channel: str | None, well: str, note: str) -> None:
    """The page well's *N* largest and *N* smallest fold changes at the page's timepoint, each a row of crops over
    every timepoint (``extremes_compute``, ``renderer.render_extremes``). The crops of the nanowells not yet cut
    are read on a worker, all in one pass, into ``st.crops`` — the store the site detail reads, so a row clicked
    opens its detail with nothing more to cut."""
    from review.core.plot_style import apply_export_style_to_current

    st.hits, st.drawn = None, None                       # no dots to hit and nothing to zoom: rows are clicked
    timepoint = shown_timepoint(ctx)[1]
    column = metric_column(metric, channel)
    metrics, filtered = ss.apply(st.metrics, st.subset, st.baseline, [well], st.data)
    if filtered is not None and filtered.kept == 0:
        render_message(fig, ss.empty_text(filtered), gid="nw-start-empty")
        apply_export_style_to_current(ctx, fig, canvas)
        canvas.draw_idle()
        redraw_detail(ctx)
        return
    if filtered is not None:
        note = " ".join(filter(None, [ss.note(filtered), note]))
    model = ec.extremes(metrics, well, timepoint, column, st.extremes_n, st.data.channels)
    missing = tuple(sorted(site for site in model.sites() if site not in st.crops))
    image_channel = page_image_channel(st)
    if missing:
        data, metrics = st.data, st.metrics

        def deliver(found) -> None:
            st.crops.update(found)

        _fetch(ctx, "extreme_crops", missing,
               lambda: crops_for_sites(data, metrics, missing, channel=image_channel),
               failed=lambda message: {site: {"": Crop(None, "", f"The crops could not be cut: {message}")}
                                       for site in missing},
               deliver=deliver)
    st.extreme_axes = render_extremes(fig, model, {site: st.crops[site] for site in model.sites()
                                                   if site in st.crops},
                                      label=_label(metric, channel), note=note, cells=st.crop_cells,
                                      image_channel=image_channel, numbers=st.crop_metrics,
                                      display_range=display_range(ctx),
                                      timepoints=extremes_columns(model.timepoints, model.timepoint, st.extremes_tps),
                                      metrics=crop_metrics(st, model.sites(), channel),
                                      left_out=cg.left_out(st.gate))
    apply_export_style_to_current(ctx, fig, canvas)
    canvas.draw_idle()
    redraw_detail(ctx)


def extremes_columns(timepoints, ranked: str, chosen) -> list:
    """The timepoints the Extremes montage shows, in the well's order: those *chosen* (every one when none is), and
    always the ranking timepoint *ranked*, which orders the rows."""
    chosen = set(chosen or ())
    return [tp for tp in timepoints if not chosen or tp in chosen or tp == ranked]


def crop_metrics(st, sites, channel) -> dict:
    """``site -> {timepoint: [caption lines]}``: each nanowell's computed measurements at each timepoint, for the
    captions under its crops (``series_compute.crop_metric_lines``)."""
    out: dict = {}
    if st.metrics is None:
        return out
    for site in sites:
        well, q, r = site
        frame = site_series(st.metrics, well, q, r)
        out[tuple(site)] = {str(row["timepoint"]): crop_metric_lines(row, channel)
                            for row in frame.to_dict("records")}
    return out


def set_extremes_timepoints(ctx, timepoints) -> None:
    """Show these timepoints as the Extremes montage's columns (every one when empty)."""
    st = _state(ctx)
    if st is None:
        return
    st.extremes_tps = [str(tp) for tp in timepoints]
    _save_soon(ctx)
    redraw(ctx)


def crops_site(st) -> tuple | None:
    """The nanowell whose crops the detail's strip and the pop-out show: the first selected one with a series, the
    one the site detail leads with (:func:`redraw_detail`); ``None`` when no selected nanowell has one."""
    if st is None or st.metrics is None:
        return None
    for well, q, r in st.selected_sites:
        if len(site_series(st.metrics, well, q, r)):
            return (well, q, r)
    return None


def pop_out_crops(ctx) -> None:
    """**Pop out**: open (or bring forward) the window that shows the selected nanowell's crops at every timepoint,
    large, and draw it. The window follows the selection until it is closed; the page's own view is untouched."""
    _call(ctx, "_nw_open_popout")
    draw_popout(ctx)


def go_to_site_in_blink(ctx) -> None:
    """**Go to nanowell**: show the nanowell whose crops are shown (:func:`crops_site`) on the Identity view's Blink,
    in its well, zoomed onto it (``identity_focus_compute``) — at the page's timepoint when the well has it, else the
    one the view opens on."""
    st = _state(ctx)
    site = crops_site(st)
    if site is None:
        ctx.notify.set_status("Select a nanowell first: Go to nanowell shows the selected one on Identity ▸ Blink.")
        return
    well, q, r = site
    st.view, st.well, st.identity_mode = "identity", well, "blink"
    st.identity_focus = f"{int(q)},{int(r)}"
    st.identity_zoom, st.identity_zoom_for = {}, None
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def draw_popout(ctx) -> None:
    """Draw the pop-out window's crops, when it is open: the first selected nanowell's crops at every timepoint on
    one brightness scale (``renderer.render_crop_grid``), or a sentence saying a nanowell has to be selected."""
    st = _state(ctx)
    window = ctx.widgets.get_or_none("_nw_popout")
    fig, canvas = ctx.widgets.get_or_none("_nw_popout_fig"), ctx.widgets.get_or_none("_nw_popout_canvas")
    if st is None or window is None or fig is None or canvas is None or not window.isVisible():
        return
    site = crops_site(st)
    if site is None:
        render_message(fig, "Select a nanowell on the plot to see its crops here.", gid="nw-popout-empty")
    else:
        image_channel = page_image_channel(st)
        if site not in st.crops:
            data, metrics = st.data, st.metrics
            _fetch(ctx, "crops", site, lambda: site_crops(data, metrics, site, channel=image_channel),
                   failed=lambda message: {"": Crop(None, "", f"The crops could not be cut: {message}")})
        render_crop_grid(fig, st.crops.get(site), site, cells=st.crop_cells,
                         metrics=crop_metrics(st, [site], page_channel(ctx)).get(site) if st.crop_metrics else None,
                         image_channel=image_channel, display_range=display_range(ctx), labels=st.popout_labels,
                         left_out=cg.left_out(st.gate))
    canvas.draw_idle()


def redraw_detail(ctx) -> None:
    """Draw the site detail for the selected nanowells — their series, and the first one's crops in the strip under
    the **Pop out** button — or hide it when none is selected; and the pop-out window, which follows the selection."""
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
    draw_popout(ctx)
    if not series:
        return
    channel = page_channel(ctx)
    image_channel = page_image_channel(st)
    first = tuple(series[0][0])
    crops = st.crops.get(first)
    if crops is None:
        data, metrics = st.data, st.metrics
        _fetch(ctx, "crops", first, lambda: site_crops(data, metrics, first, channel=image_channel),
               failed=lambda message: {"": Crop(None, "", f"The crops could not be cut: {message}")})
    render_site_detail(fig, series, channel=channel, time_source=st.data.time_source,
                       outside=_outside_badges(st, [site for site, _frame in series]))
    canvas.draw_idle()
    strip_fig, strip_canvas = ctx.widgets.get_or_none("_nw_strip_fig"), ctx.widgets.get_or_none("_nw_strip_canvas")
    if strip_fig is not None and strip_canvas is not None:
        render_crop_strip(strip_fig, crops, first, cells=st.crop_cells, image_channel=image_channel,
                          display_range=display_range(ctx), left_out=cg.left_out(st.gate))
        strip_canvas.draw_idle()


def _outside_badges(st, sites) -> dict:
    """``site -> sentence`` for each of *sites* the *Subset* leaves out of the page's figures."""
    if not st.subset.is_active():
        return {}
    out = {}
    for site in sites:
        said = ss.site_badge(site_series(st.metrics, *site), st.subset, st.data)
        if said:
            out[tuple(site)] = said
    return out


#: Montages kept read: a montage is up to 4096 px square, so the slider's recent steps only.
MONTAGES_KEPT = 6


#: The reads that cut crops, which are of the page's image channel.
_CROP_KINDS = ("crops", "extreme_crops")


def _fetch(ctx, kind: str, key, job, *, failed, deliver=None) -> None:
    """Read ``st.<kind>[key]`` on a worker, once, and redraw when it arrives.

    A failed read stores ``failed(message)`` — a value that says why — so the figure states the failure and
    the read is not relaunched on every redraw. A result for a dataset loaded since is dropped without
    touching the new dataset's bookkeeping. With *deliver*, the value is handed to it instead of stored under
    *key* (a read of many nanowells' crops at once puts each into ``st.crops``).
    """
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    # Crops are of one channel: the tag carries it, and crops cut for a channel since left are dropped, never stored
    # under the channel chosen now (:func:`set_image_channel` empties the store).
    image_channel = page_image_channel(st) if kind in _CROP_KINDS else None
    tag = (kind, key, image_channel)
    if tag in st.fetching:
        return
    st.fetching.add(tag)
    data, holder = st.data, []

    def finish(value) -> None:
        # The bridge has delivered, so it is let go whatever happens to the value.
        if holder and holder[0] in st.fetch_bridges:
            st.fetch_bridges.remove(holder[0])
        if st.data is not data:
            return
        st.fetching.discard(tag)
        if image_channel is not None and page_image_channel(st) != image_channel:
            return
        if not _page_alive(ctx):
            # The page was deleted while the read ran (the window closed): there is nothing to redraw, and
            # redrawing would reach into deleted Qt widgets.
            return
        if deliver is not None:
            deliver(value)
        else:
            store = getattr(st, kind)
            store[key] = value
            while kind == "montages" and len(store) > MONTAGES_KEPT:
                store.pop(next(iter(store)))
        (redraw_detail if kind == "crops" else redraw)(ctx)

    def on_failed(message) -> None:
        log.warning("Nanowells: %s for %s could not be read: %s", kind, key, message)
        finish(failed(message))

    bridge = run_off_thread(job, on_done=finish, on_failed=on_failed)
    holder.append(bridge)
    st.fetch_bridges.append(bridge)


def on_click(ctx, event) -> None:
    """A press on the figure. On the Distribution figure: select the nanowell under it, add it with Shift (or
    ⌘/Ctrl), or clear the selection on empty space. On the Array view a press starts a drag or a click, which
    :func:`on_release` tells apart. The plate selection is never touched."""
    st = _state(ctx)
    if st is not None and st.view == "identity":
        idc.on_press(ctx, event)                 # a pan, or a double-click back to the whole view
        return
    if st is not None and st.view == "extremes":
        if event.button == 1:
            _select(ctx, st.extreme_axes.get(event.inaxes), _adding(event))
        return
    if st is not None and st.view == "array":
        if event.dblclick and event.button == 1 and not st.anchor_mode:
            focus_nanowell_at(ctx, event)
            return
        _array_press(st, event)
        return
    if st is None or st.hits is None or event.inaxes is None or event.button != 1:
        return
    _select(ctx, st.hits.nearest(event.inaxes, event.x, event.y, CLICK_RADIUS_PX), _adding(event))


def _select(ctx, site, adding: bool) -> None:
    """Select *site* (``None``: empty space), or with *adding* add it to the selection or take it out."""
    st = _state(ctx)
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
    ensure_loaded(ctx)
    _apply_pending_anchor(ctx)                   # the tables were already loaded: no load will land to apply it
    _call(ctx, "_nw_refresh_controls")
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
        visible = ctx.widgets.get_or_none("_nw_page_visible")
        if callable(visible) and visible():
            ensure_loaded(ctx)                   # a well selected now is read now; a hidden page reads on show
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)


def nw_on_channel_changed(ctx) -> None:
    nw_on_selection_changed(ctx)


def set_view(ctx, view: str) -> None:
    _set(ctx, "view", view)


def set_starting_cells(ctx, lo, hi) -> None:
    """Make the *Subset* the Starting cells filter of *lo* to *hi* cells at the baseline (``None`` for no bound on
    that side) — the Subset control's presets. Bounds that are not whole numbers ≥ 0, or a minimum above the
    maximum, change nothing: the controls are put back to the subset that is set."""
    try:
        subset = ss.Subset.starting(lo, hi)
    except ValueError:
        _call(ctx, "_nw_refresh_controls")
        return
    set_subset(ctx, subset)


def set_subset(ctx, subset) -> bool:
    """Make *subset* the page's *Subset* and redraw; ``False`` (and nothing changed) when it is not one — a range
    whose lower bound is above its upper, a tile condition with no tile chosen."""
    st = _state(ctx)
    if st is None:
        return False
    try:
        subset = subset.validated()
    except ValueError:
        _call(ctx, "_nw_refresh_controls")
        return False
    if st.subset == subset:
        _call(ctx, "_nw_refresh_controls")
        return True
    st.subset = subset
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)
    return True


def subset_tiles(ctx) -> list[str]:
    """The imaging fields (tiles) the page's wells' nanowells were cut from, for the Subset editor's tile choice."""
    st = _state(ctx)
    if st is None or st.metrics is None or "owner" not in st.metrics:
        return []
    rows = st.metrics[st.metrics["well"].isin(page_wells(ctx))]
    return sorted({str(v) for v in rows["owner"].dropna() if str(v)})


def set_crop_cells(ctx, on: bool) -> None:
    """Draw the counted cells on every crop the page shows (the strip, the pop-out, the Extremes montage), or not."""
    st = _state(ctx)
    if st is None or st.crop_cells == bool(on):
        return
    st.crop_cells = bool(on)
    _save_soon(ctx)
    redraw(ctx)


def set_channel(ctx, channel: str) -> None:
    """The fluorescence channel the page's Brightness and Nanowell fluorescence are drawn for."""
    _set(ctx, "channel", channel or "")


def set_image_channel(ctx, channel: str) -> None:
    """The channel every picture on the page is of (``""``: phase). The crops cut so far, the reads in flight and the
    full-resolution tiles are of the old channel, so they are let go and cut again."""
    st = _state(ctx)
    if st is None or st.image_channel == (channel or ""):
        return
    st.image_channel = channel or ""
    st.crops = {}
    st.fetching = {tag for tag in st.fetching if tag[0] not in _CROP_KINDS}
    st.release_tiles()
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_popout_labels(ctx, on: bool) -> None:
    """Write the pop-out's timepoint over each crop and its caption, or leave the crops bare."""
    st = _state(ctx)
    if st is None or st.popout_labels == bool(on):
        return
    st.popout_labels = bool(on)
    _save_soon(ctx)
    draw_popout(ctx)


def set_crop_metrics(ctx, on: bool) -> None:
    """Write the numbers on the Extremes montage's crops (each one's fold change and metrics line) and under the
    pop-out's, or leave them bare."""
    st = _state(ctx)
    if st is None or st.crop_metrics == bool(on):
        return
    st.crop_metrics = bool(on)
    _save_soon(ctx)
    redraw(ctx)


def set_extremes_n(ctx, count: int) -> None:
    """How many nanowells each of the Extremes view's two blocks shows."""
    _set(ctx, "extremes_n", ec.clamp_n(count))


def set_stats_unit(ctx, unit: str) -> None:
    """Compare wells (their nanowells the values) or groups (their wells' medians the values) on Statistics."""
    if unit in sc.UNITS:
        _set(ctx, "stats_unit", unit)


def set_stats_test(ctx, test: str) -> None:
    """The test the Statistics view runs (``statistics_compute.TESTS``)."""
    if test in sc.TEST_KEYS:
        _set(ctx, "stats_test", test)


def set_cohort(ctx, on: bool) -> None:
    _set(ctx, "cohort", bool(on))


def set_moving(ctx, window: int) -> None:
    """The moving average's window in timepoints; 0 (or 1) turns it off."""
    _set(ctx, "moving", mvc.clamp_window(window))


def set_baseline_moving(ctx, window: int) -> None:
    """The baseline's own window for a fold change, in timepoints; 0 (or 1) keeps the baseline one measured value.
    Used only while the moving average is on (``moving_compute.baseline_window_for``)."""
    _set(ctx, "baseline_moving", mvc.clamp_window(window))


def set_metric(ctx, metric: str) -> None:
    _set(ctx, "metric", metric)


def set_axis(ctx, axis: str) -> None:
    _set(ctx, "axis", axis)


def set_well(ctx, well: str) -> None:
    _set(ctx, "well", well or "")


def set_timepoint(ctx, timepoint: str) -> None:
    _set(ctx, "timepoint", timepoint or "")


def set_control(ctx, control: str) -> None:
    _set(ctx, "control", control or "")


def drawn_metric_column(ctx) -> str | None:
    """The metrics column the page's metric resolves to now (the shared channel applied), or ``None`` before a
    dataset with nanowells is loaded. The "responding above" cut is kept per column (tracker I9)."""
    st = _state(ctx)
    if st is None or st.data is None:
        return None
    metric, channel, _note = resolve_metric(st.view, st.metric, page_channel_name(ctx), st.data.channels)
    return metric_column(metric, channel)


def parse_threshold(text: str) -> float | None:
    """A typed cut as a finite number, or ``None`` for an empty field or anything that is not one."""
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def set_responding_above(ctx, text: str) -> None:
    """Set the cut for the metric the page draws, or clear it with an empty field (tracker I9). Text that is not
    a number changes nothing: the field is put back to the cut that is set."""
    st = _state(ctx)
    column = drawn_metric_column(ctx)
    if st is None or column is None:
        return
    value = parse_threshold(text)
    if (value is None and str(text).strip()) or st.thresholds.get(column) == value:
        _call(ctx, "_nw_refresh_controls")
        return
    if value is None:
        st.thresholds.pop(column, None)
    else:
        st.thresholds[column] = value
    _save_soon(ctx)
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_page_baseline(ctx, baseline: str) -> None:
    st = _state(ctx)
    if st is None or baseline == st.baseline:
        return
    st.baseline = baseline
    _save_soon(ctx)
    ensure_loaded(ctx)
    redraw(ctx)


def set_rim_focus(ctx, *, on: bool | None = None, fraction: float | None = None) -> None:
    """Turn the rim focus rule on or off, or change its fraction, and recompute the metrics with it. A fraction
    out of range (``rim_focus_compute.clamp_fraction``) is refused with the range named, and nothing changes."""
    st = _state(ctx)
    if st is None:
        return
    if fraction is not None:
        accepted = rfc.clamp_fraction(fraction)
        if accepted is None:
            ctx.notify.set_status(f"Rim focus: give a fraction from {rfc.MIN_FRACTION:g} to {rfc.MAX_FRACTION:g}.")
            _call(ctx, "_nw_refresh_controls")
            return
        fraction = accepted
    new_on = st.rim_focus_on if on is None else bool(on)
    new_fraction = st.rim_focus_fraction if fraction is None else fraction
    if (new_on, new_fraction) == (st.rim_focus_on, st.rim_focus_fraction):
        return
    st.rim_focus_on, st.rim_focus_fraction = new_on, new_fraction
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
    """*Save nanowell data…*: every nanowell, timepoint and metric of the wells the page draws, long format
    (D8)."""
    from review.core.csv_export import ask_save_csv

    st = _state(ctx)
    if st is None or st.metrics is None:
        ctx.notify.set_status("There are no nanowell tables loaded to save.")
        return
    stale_gate = st.gated_for != gate_version(ctx)
    if (st.computing or st.loaded_for is None or st.loaded_for[1] != st.baseline
            or st.focus_for != st.rim_focus_rule().key() or stale_gate):
        # The metrics on hand were computed for another baseline or rim focus rule (roll-up review X12), or before a
        # gating, curation or QC edit moved which cells count (cell population audit P9): written now, the file
        # would carry counts the page no longer draws, under the current settings' labels. A stale gate also starts
        # the recompute.
        if stale_gate and not st.computing:
            ensure_loaded(ctx)
        ctx.notify.set_status(f"The nanowell metrics for baseline {st.baseline!r} are still being computed; "
                              "save again once the page has drawn them.")
        return
    wells = page_wells(ctx)
    if not wells:
        ctx.notify.set_status("Select wells with nanowell tables first: the file holds the wells the page draws.")
        return
    path = ask_save_csv(ctx, "Save nanowell data", "nanowell_metrics.csv")
    if not path:
        return
    try:
        rows = export_long_csv(st, path, wells)
    except OSError as exc:
        log.warning("Nanowell export to %s failed: %s", path, exc)
        ctx.notify.set_status(f"Could not write {path}: {exc}")
        return
    ctx.notify.set_status(f"Wrote {rows:,} rows → {path}")
