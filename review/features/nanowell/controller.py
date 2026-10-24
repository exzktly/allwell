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
from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell import views_compute as vc
from review.features.nanowell.distribution_compute import (
    METRICS, PER_NANOWELL, distribution_model, match_channel, metric_column,
)
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell.array_compute import array_scene, one_to_one
from review.features.nanowell.image_compute import Crop, read_montage, site_crops
from review.features.nanowell.renderer import (
    ArrayDrawn, render_array, render_distribution, render_message, render_position, render_site_detail,
    render_trajectories,
)
from review.features.nanowell.state import IMAGES, LAYER_SWITCHES, ArrayLayers, COLOURINGS, POSITIONS
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
    state.loaded_for = (str(data_dir), state.baseline)     # what the page's own load records (roll-up review X16)
    state.loaded_wells, state.parts_for = frozenset(data.available), str(data_dir)   # every well was read


def set_baseline(state, baseline: str) -> None:
    """Change the baseline and recompute every metric that depends on it."""
    state.baseline = baseline
    if state.data is not None:
        state.metrics = site_metrics(state.data, baseline)
        if state.loaded_for is not None:
            state.loaded_for = (state.loaded_for[0], baseline)


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
    available = st.data.available
    if not available:
        return _empty("No well's nanowell table could be used.", " ".join(st.data.problems))
    from review.core.selection_controller import has_plottable_selection

    if not has_plottable_selection(ctx) or not page_groups(ctx):
        chosen = [w for w in active_wells(ctx) if w in set(available)]
        if chosen and st.computing:
            return _empty("Reading the nanowell tables…", "The figure appears when they are loaded.")
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


def _call(ctx, name: str) -> None:
    fn = ctx.widgets.get_or_none(name)
    if callable(fn):
        fn()


def _metrics_of(data, baseline: str) -> pd.DataFrame | None:
    """The metrics of *data*, ``None`` with no data; with no well read yet (nothing selected) an empty table, so
    the page counts as loaded and says what to select rather than reading on."""
    if data is None:
        return None
    if not len(data.sites):
        return pd.DataFrame(columns=["well", "timepoint", "nw_q", "nw_r", "state"])
    return site_metrics(data, baseline)


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
    if not force and st.computing and st.pending_for == (key, wells):
        return                                           # this very request is already running
    if not force and st.loaded_for == key and wanted <= st.loaded_wells:
        if st.computing:
            # The current data is what is wanted again (a baseline changed and changed back): whatever is
            # running is for another key and now stale, so it must not land when it finishes.
            st.request += 1
            st.computing, st.pending_for = False, None
            _call(ctx, "_nw_show_computing")
        return
    st.request += 1
    request, baseline = st.request, st.baseline
    st.computing, st.pending_for = True, (key, wells)
    same_wells = wanted <= st.loaded_wells and st.loaded_for is not None and st.loaded_for[0] == key[0]
    previous = st.data if same_wells else None
    parts = dict(st.well_parts)                          # the worker's own copy: it adds the wells it reads

    def job():
        if previous is not None:
            return previous, _metrics_of(previous, baseline), "", parts
        try:
            data = load_nanowell_data(Path(data_dir), wells=sorted(wells), parts=parts) if data_dir else None
        except NanowellSchemeError as exc:
            return None, None, str(exc), parts
        return data, _metrics_of(data, baseline), "", parts

    def done(result):
        if request != st.request:
            return                                       # a newer request has been made; this one is stale
        data, metrics, error, read = result
        st.data, st.metrics, st.load_error = data, metrics, error
        st.loaded_for, st.computing = key, False
        if st.parts_for == key[0]:
            st.well_parts, st.loaded_wells = read, wells
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
    metric, channel, note = resolve_metric(st.view, st.metric, ctx.state.channel.active_channel, st.data.channels)
    well = page_well(ctx)
    tableless = tableless_wells(ctx)
    array = {}
    if st.view == "array":
        timepoint = page_timepoint(ctx, well)
        if st.region_for != (well, st.layers.positions):         # another well, or the other frame: fit it
            st.region, st.region_for = None, (well, st.layers.positions)
        image, image_note = _array_image(ctx, st, well, timepoint)
        array = {"layers": drawn_layers(st), "image": image, "image_note": image_note, "region": st.region}
        hidden = OVERLAYS_HIDDEN_NOTE if st.overlays_hidden else ""
        note = " ".join(filter(None, [hidden, st.array_message, note]))
    st.hits, model = draw_view(
        fig, st.view, st.metrics, st.data, groups=page_groups(ctx), metric=metric, channel=channel,
        axis=st.axis, timepoint=shown_timepoint(ctx)[1] if st.view == "position" else st.timepoint,
        control=st.control, baseline=st.baseline, cohort=st.cohort, well=well,
        well_timepoint=page_timepoint(ctx, well), note=note, tableless=tableless,
        selected=[tuple(s) for s in st.selected_sites],
        responding_above=st.thresholds.get(metric_column(metric, channel)), **array)
    if isinstance(model, ArrayDrawn) or st.view == "array":
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


_CHANNEL_METRICS = ("brightness", "signal", "brightness_fc", "signal_fc")


def resolve_metric(view: str, metric: str, active_channel, channels) -> tuple[str, str | None, str]:
    """The metric drawn, the shared channel spelled as the data spells it, and a note when the chosen metric
    cannot be drawn: a channel metric with no site intensities for the shared channel, or a metric with one
    value per nanowell on Trajectories, which draws values over time. The page and Batch Export both ask."""
    channel = match_channel(active_channel, channels)
    if view == "trajectories" and metric in PER_NANOWELL:
        return "n", None, (f"{METRICS[metric][0]} has one value per nanowell, not one per timepoint, so "
                           f"Trajectories shows Cell count.")
    if metric in _CHANNEL_METRICS and channel is None:
        return "n", None, (f"{metric.replace('_', ' ')} needs a channel with site intensities; the shared channel "
                           f"({active_channel}) has none, so Cell count is shown.")
    return metric, channel, ""


def draw_view(fig, view: str, metrics, data, *, groups: dict, metric: str, channel: str | None, axis: str,
              timepoint: str, control: str, baseline: str, cohort: bool, well: str, well_timepoint: str,
              note: str = "", tableless=(), selected=(), responding_above: float | None = None,
              layers: ArrayLayers | None = None, image=None, image_note: str = "",
              region=None) -> tuple[object, object]:
    """Draw *view* (Distribution, Array, Trajectories or Position) into *fig*: ``(hit map, model)``.

    **The one composition of each view**, called by the page and by Batch Export's Nanowells mode (tracker I4),
    so a saved batch figure is the page's figure for the same choices — the notes, the exclusions and the
    empty states included — and fixing one fixes both. *groups* is ``group -> wells``; *timepoint* is the
    timepoint Position and *Along x: Wells* compare at; *well* and *well_timepoint* are what the Array view shows,
    with *layers* (the lattice map when none are given — a batch figure), *image* under it and *region* where it
    looks (:func:`draw_array`); its model is the ``renderer.ArrayDrawn``.
    The hit map is ``None`` for views with nothing to click. *responding_above* is the user's cut for *metric*
    (tracker I9), which the Distribution view states the fraction responding at.
    """
    wells = [w for members in groups.values() for w in members]
    # Wells whose rosters two versions decided differently count their rim differently; the figure says so (X35a-R4).
    mixed = mixed_rules_note({w: data.roster_rules.get(w) for w in wells if w in getattr(data, "roster_rules", {})})
    note = " ".join(filter(None, [note, f"{mixed}." if mixed else ""]))
    # A well analysed again when a timepoint was added holds rewritten earlier rows; the figure says so (X35a-R8).
    again = sorted(w for w in set(wells) if w in getattr(data, "reanalysed", {}))
    if again:
        note = " ".join(filter(None, [note, f"{', '.join(again)}: analysed again over every timepoint when a timepoint "
                                            "was added, so their earlier timepoints' numbers were rewritten."]))
    if view == "array":
        drawn = draw_array(fig, metrics, data, well=well, timepoint=well_timepoint, metric=metric, channel=channel,
                           layers=layers, image=image, image_note=image_note, region=region, selected=selected,
                           note=note)
        return (drawn.hits if drawn is not None else None), drawn
    if view == "trajectories":
        render_trajectories(fig, vc.trajectory_panels(metrics, wells, metric_column(metric, channel), cohort=cohort),
                            cohort=cohort, label=_label(metric, channel), time_source=data.time_source, note=note)
        return None, None
    if view == "position":
        series, missing = vc.position_series(metrics, wells, timepoint, metric_column(metric, channel),
                                             kinds={w: vc.lattice_kind(data, w, timepoint) for w in wells})
        if missing:
            note = " ".join(filter(None, [note, f"No addressed nanowells at {timepoint}, so not drawn: "
                                                f"{', '.join(missing)}."]))
        render_position(fig, series, label=_label(metric, channel), timepoint=timepoint, note=note)
        return None, None
    model = distribution_model(metrics, metric, channels=data.channels, channel=channel, axis=axis, groups=groups,
                               timepoint=timepoint, control=control, time_source=data.time_source,
                               baseline=baseline, cohort=cohort, responding_above=responding_above)
    if tableless:
        model.notes.insert(0, f"Selected without a nanowell table, so not drawn and not in any group's statistic: "
                              f"{', '.join(tableless)}.")
    if note:
        model.notes.insert(0, note)
    return render_distribution(fig, model, selected=selected), model


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
    tps = list(st.data.timepoints())
    if st.view == "distribution" and st.axis == "wells":
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
               note: str = "") -> ArrayDrawn | None:
    """**The one composition of the Array view**, for the page and for Batch Export (through :func:`draw_view`).

    *layers* says what is drawn (the lattice map when none are given); *image* is the run montage or the tile
    store the caller holds for the well frame, *image_note* what it says about it (still reading, or why there is
    none). ``None`` when there is nothing at all to draw, and the figure then says why.
    """
    layers = layers or batch_array_layers()
    if not well:
        render_message(fig, "Select a well with a nanowell table.", gid="nw-array-missing")
        return None
    scene = array_scene(metrics, data, well, timepoint, metric_column(metric, channel), positions=layers.positions,
                        anchors=layers.anchor)
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
                        selected=selected, region=region, note=note)


def _array_image(ctx, st, well: str, timepoint: str) -> tuple[object, str]:
    """``(image, what to say about it)`` for the Array view's image layer at (*well*, *timepoint*): the run's
    montage, read on a worker once; or the raw tiles, held in a store whose tiles are read as the view asks."""
    kind = st.layers.image if not st.layers.lattice() else "off"
    if kind != "tiles" or st.tile_key != (well, timepoint):
        st.release_tiles()                               # another well or timepoint, or no tiles: let them go
    if kind == "off":
        return None, ""
    if kind == "montage":
        if (well, timepoint) in st.montages:
            montage, why = st.montages[(well, timepoint)]
            return montage, ("" if montage is not None else why)
        # The montage is a PNG of up to 4096 px in a zip: read it on a worker, and draw it when it arrives.
        data = st.data
        _fetch(ctx, "montages", (well, timepoint), lambda: read_montage(data, well, timepoint),
               failed=lambda message: (None, f"The montage could not be read: {message}"))
        return None, "Reading the montage…"
    if st.tile_key is None:
        st.tile_key = (well, timepoint)
        source, st.tile_why = fmc.montage_source(st.data, well, timepoint)
        st.tile_store = fmc.TileStore(source) if source is not None else None
        if source is not None:
            _load_tiles(ctx, [t.tile for t in source.tiles], level=None)
    store = st.tile_store
    if store is None:
        return None, st.tile_why
    source, failed = store.source, dict(store.failed)
    total, read = len(source.tiles), store.loaded()
    said = [f"Full resolution, from the raw tiles in {source.archive.name}."]
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
OVERLAYS_HIDDEN_NOTE = "Overlays hidden — press Overlays to show the nanowells, tiles and anchor again."


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
#: One wheel notch zooms by this factor; the *−* and *+* buttons step by two notches.
WHEEL_STEP = 0.8
BUTTON_STEP = WHEEL_STEP ** 2
#: How far the Array view zooms, against what *Fit* shows: out to a quarter of it, in to 500 times it — past
#: either the view is a speck or one pixel of the raw tiles is wider than the screen.
ZOOM_OUT_LIMIT, ZOOM_IN_LIMIT = 0.25, 500.0


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
    now = array_zoom_factor(drawn)
    if now is not None:
        # The zoom after this step, held inside the limits: the step is shortened to land on the limit.
        target = min(max(now / factor, ZOOM_OUT_LIMIT), ZOOM_IN_LIMIT)
        factor = now / target
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


def on_motion(ctx, event) -> None:
    """A drag on the Array view pans it; the pixels follow at once and the image is drawn again on release."""
    st = _state(ctx)
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


def _anchor_change(ctx, change, failure: str) -> None:
    from util.nanowell_anchors import AnchorsFileError

    st = _state(ctx)
    path = fmc.anchors_file(st.data) if st is not None and st.data is not None else None
    if path is None:
        return
    well = page_well(ctx)
    try:
        change(path, well, page_timepoint(ctx, well))
        st.array_message = ""
    except (AnchorsFileError, OSError) as exc:
        st.array_message = f"{failure}: {exc}"
    _call(ctx, "_nw_refresh_controls")
    redraw(ctx)


def set_anchor_at(ctx, x_um: float, y_um: float) -> None:
    """Anchor the shown well at the shown timepoint at the well-frame point clicked (``in/nanowell_anchors.json``):
    the run snaps it to the nearest nanowell, so it need only land on the central square."""
    from util.nanowell_anchors import set_anchor

    _anchor_change(ctx, lambda path, well, tp: set_anchor(path, well, tp, (float(x_um), float(y_um))),
                   "The anchor could not be saved")


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
    first = tuple(series[0][0])
    crops = st.crops.get(first)
    if crops is None:
        data, metrics = st.data, st.metrics
        _fetch(ctx, "crops", first, lambda: site_crops(data, metrics, first),
               failed=lambda message: {"": Crop(None, "", f"The crops could not be cut: {message}")})
    render_site_detail(fig, series, channel=channel, time_source=st.data.time_source, crops=crops)
    canvas.draw_idle()


#: Montages kept read: a montage is up to 4096 px square, so the slider's recent steps only.
MONTAGES_KEPT = 6


def _fetch(ctx, kind: str, key, job, *, failed) -> None:
    """Read ``st.<kind>[key]`` on a worker, once, and redraw when it arrives.

    A failed read stores ``failed(message)`` — a value that says why — so the figure states the failure and
    the read is not relaunched on every redraw. A result for a dataset loaded since is dropped without
    touching the new dataset's bookkeeping.
    """
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    tag = (kind, key)
    if tag in st.fetching:
        return
    st.fetching.add(tag)
    data, holder = st.data, []

    def finish(value) -> None:
        if st.data is not data:
            return
        st.fetching.discard(tag)
        if holder and holder[0] in st.fetch_bridges:
            st.fetch_bridges.remove(holder[0])
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
        visible = ctx.widgets.get_or_none("_nw_page_visible")
        if callable(visible) and visible():
            ensure_loaded(ctx)                   # a well selected now is read now; a hidden page reads on show
        _call(ctx, "_nw_refresh_controls")
        redraw(ctx)


def nw_on_channel_changed(ctx) -> None:
    nw_on_selection_changed(ctx)


def set_view(ctx, view: str) -> None:
    _set(ctx, "view", view)


def set_cohort(ctx, on: bool) -> None:
    _set(ctx, "cohort", bool(on))


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
    metric, channel, _note = resolve_metric(st.view, st.metric, ctx.state.channel.active_channel, st.data.channels)
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
    if st.computing or st.loaded_for is None or st.loaded_for[1] != st.baseline:
        # The metrics on hand were computed for another baseline; written now, the file would label them with
        # the new one (roll-up review X12).
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
