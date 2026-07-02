"""Live UI view-state persistence — ``view_state`` section of ``persistence.json``.

Captures the user's *view* (active tab, selected wells, active channel /
metric, fold-change scopes, per-plot-tab combos) so reopening a dataset
lands them where they left off. Per-tab combo restore is deferred: the
state is stashed on ``app._view_state_pending`` at load time and popped
into the live widgets the first time each tab becomes active (some tabs
build lazily on first visit).

Save triggers: app close and dataset switch. No continuous autosave —
view changes happen interactively and a save-per-keystroke would burn I/O
for no user benefit.
"""

from __future__ import annotations

import logging
from typing import Any

from well_viewer.persistence import _doc
from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")

# Current on-disk view_state schema. Bump when the shape of the dict below
# changes incompatibly, and register a step in ``_MIGRATIONS`` that upgrades the
# prior version to this one (audit X7) — otherwise a bump silently discards
# every user's saved view. ``snapshot`` stamps this value.
SCHEMA_VERSION = 1

# ``{n: fn}`` where ``fn(state_at_v_n) -> state_at_v_{n+1}``. Empty while only v1
# exists; each future bump adds one step so ``_migrate`` can walk an old saved
# doc forward instead of dropping it.
_MIGRATIONS: dict = {}


def _migrate(state: dict, from_version) -> "dict | None":
    """Upgrade a saved ``view_state`` dict to :data:`SCHEMA_VERSION`.

    Returns the migrated dict (with ``schema_version`` stamped), or ``None`` when
    the saved version cannot be reached — an unknown intermediate step, or a doc
    written by a *newer* app than this one (which we can't safely downgrade). The
    caller surfaces that as a discard. A missing / ``None`` version is treated as
    the original, pre-versioning schema (== v1).
    """
    version = from_version if isinstance(from_version, int) else 1
    work = dict(state)
    while version < SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            return None
        work = step(work)
        version += 1
    if version != SCHEMA_VERSION:
        return None  # doc is newer than we understand — don't guess.
    work["schema_version"] = SCHEMA_VERSION
    return work

# Plot sub-tab names that live inside the "Plotting" parent stack.
_PLOT_SUBTABS = (
    "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
)

# Image-review sub-tab names that live inside the "Image Browser" parent
# QTabWidget. The snapshot records the leaf (via _current_leaf_tab), so restore
# must route the leaf back through the rail entry + sub-tab selection.
_IMAGE_BROWSER_SUBTABS = (
    "Segmentation", "Image Table", "Cell Browser", "Playground",
)


# ── snapshot ───────────────────────────────────────────────────────────────

def _nondefault_export_prefs(app) -> dict:
    """Only the export-style prefs the user actually changed.

    Persisting the *full* dict (defaults included) froze the old default into
    ``persistence.json``: a later app version that changed a default would be
    silently overridden by the stale saved value for users who never touched
    it (audit X8). Saving only the diff lets unset keys track future defaults —
    on restore they merge onto the fresh ``DEFAULT_EXPORT_STYLE_PREFS`` copy the
    app is constructed with.
    """
    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
    cur = getattr(app, "_export_style_prefs", None) or {}
    return {
        k: v for k, v in cur.items()
        if k not in DEFAULT_EXPORT_STYLE_PREFS or v != DEFAULT_EXPORT_STYLE_PREFS[k]
    }


def snapshot(app) -> dict:
    """Build a JSON-serialisable view-state dict from current app state."""
    return {
        "schema_version": SCHEMA_VERSION,
        # X14: union back the wells that were saved for datasets/plates not
        # currently loaded (stashed on restore), so a subset-load-then-save does
        # not irreversibly prune wells the current dataset simply doesn't contain.
        "selected_wells": sorted(
            set(app._app_state.selection.selected_wells or [])
            | getattr(app, "_view_state_absent_wells", set())
        ),
        # Rep-set visibility (which groups are shown on the plots) + solo wells —
        # in group/replicate mode this, not ``selected_wells``, is what drives the
        # plate and the plot, so a plot saved in that mode restores what was shown.
        "selection_visibility": {
            "hidden_ids": [
                str(s.get("id")) for s in (app._app_state.selection.selections or [])
                if s.get("id") is not None and s.get("hidden")
            ],
            "solo_wells": sorted(
                set(app._app_state.selection.active_solo_wells or [])
                | getattr(app, "_view_state_absent_solo", set())
            ),
        },
        "active_centre_tab": _current_leaf_tab(app),
        "active_channel": str(app._app_state.channel.active_channel or ""),
        "active_metric": str(app._app_state.channel.active_metric or ""),
        "use_sem": bool(app._app_state.plot.use_sem),
        "use_fov_replicates": bool(app._app_state.plot.use_fov_replicates),
        "show_threshold_line": bool(app._app_state.plot.show_threshold_line),
        "show_significance": bool(app._app_state.plot.show_significance),
        "fold_change": {
            "vs_control_on": bool(app._app_state.plot.fc_vs_control_on),
            "control_label": str(app._app_state.plot.fc_control_label or ""),
            "vs_t0_on": bool(app._app_state.plot.fc_vs_t0_on),
            "baseline_tp": str(getattr(app._app_state.plot, "fc_baseline_tp", "") or ""),
            "op_ctrl": str(getattr(app._app_state.plot, "fc_op_ctrl", "ratio") or "ratio"),
            "op_t0": str(getattr(app._app_state.plot, "fc_op_t0", "ratio") or "ratio"),
            "order": str(getattr(app._app_state.plot, "fc_order", "ctrl_first") or "ctrl_first"),
        },
        "export_style_prefs": _nondefault_export_prefs(app),
        "export_axis_prefs": {
            str(k): dict(v)
            for k, v in (getattr(app, "_export_axis_prefs", {}) or {}).items()
            if isinstance(v, dict)
        },
        "tabs": _snapshot_tabs(app),
    }


def _current_leaf_tab(app) -> str:
    fn = getattr(app, "_current_centre_tab", None)
    if callable(fn):
        try:
            return str(fn() or "")
        except Exception:
            return ""
    return ""


def _snapshot_tabs(app) -> dict[str, dict]:
    """Capture per-tab combo state for tabs we know how to round-trip.

    Tab files aren't modified — we read from the widget references the
    build functions already stash on ``app``. Lazily-built tabs the user
    never visited this session don't expose those widgets, so we layer
    any leftover entries from ``_view_state_pending`` underneath the
    live snapshot — that's the state we restored at load time and never
    overwrote, so it's still authoritative.
    """
    tabs: dict[str, dict] = {}
    pending = getattr(app, "_view_state_pending", None) or {}
    for name, slc in pending.items():
        if isinstance(slc, dict) and slc:
            tabs[str(name)] = dict(slc)
    bar = _snapshot_bar(app)
    if bar:
        tabs["Bar Plots"] = bar
    dist = _snapshot_distribution(app)
    if dist:
        tabs["Distribution"] = dist
    scat = _snapshot_scatter(app)
    if scat:
        tabs["Scatter Plot"] = scat
    img = _snapshot_image_table(app)
    if img:
        tabs["Image Table"] = img
    return tabs


def _combo_text(app, attr: str) -> str | None:
    w = getattr(app, attr, None)
    if w is None:
        return None
    try:
        return str(w.currentText())
    except Exception:
        return None


def _snapshot_bar(app) -> dict:
    out: dict[str, Any] = {}
    tp = _combo_text(app, "_bar_tp_cb")
    if tp is not None:
        out["timepoint"] = tp
    sels = app._app_state.bar.tp_selections if hasattr(app, "_app_state") else None
    if isinstance(sels, dict) and sels:
        out["timepoint_selections"] = {k: bool(v) for k, v in sels.items()}
    _bar = app._app_state.feature_slice("bar")
    if _bar is not None:
        out["swarm"] = bool(_bar.swarm)
        out["violin"] = bool(_bar.violin)
    slider = getattr(app, "_violin_slider", None)
    if slider is not None:
        try:
            out["violin_smooth"] = int(slider.value())
        except Exception:
            swallowed()  # was: pass (silent except)
    plot = getattr(app._app_state, "plot", None) if hasattr(app, "_app_state") else None
    if plot is not None:
        if hasattr(plot, "bar_group_mode"):
            out["group_mode"] = str(plot.bar_group_mode)
        if hasattr(plot, "bar_pool_replicates"):
            out["pool_replicates"] = bool(plot.bar_pool_replicates)
        if hasattr(plot, "bar_err_kind"):
            out["bar_err_kind"] = str(plot.bar_err_kind)
        if hasattr(plot, "bar_show_mode"):
            out["bar_show_mode"] = str(plot.bar_show_mode)
        # The user's drag-to-reorder bar layout. ``ordered_bar_keys``
        # falls back to the canonical order when the saved list doesn't
        # cover the current dataset's keys, so an empty / stale list is
        # benign.
        order = getattr(plot, "bar_order", None)
        if isinstance(order, list) and order:
            out["bar_order"] = [str(k) for k in order]
    return out


def _snapshot_distribution(app) -> dict:
    out: dict[str, Any] = {}
    tp = _combo_text(app, "_distribution_tp_cb")
    if tp is not None:
        out["timepoint"] = tp
    mode = _combo_text(app, "_distribution_mode_cb")
    if mode is not None:
        out["mode"] = mode
    layout = _combo_text(app, "_distribution_layout_cb")
    if layout is not None:
        out["layout"] = layout
    _dist = app._app_state.feature_slice("distribution")
    if _dist is not None:
        out["bins"] = int(_dist.bins)
        out["log_x"] = bool(_dist.log_x)
        out["log_y"] = bool(_dist.log_y)
    return out


def _snapshot_scatter(app) -> dict:
    out: dict[str, Any] = {}
    for key, attr in (
        # Per-cell ("Per-cell points") sub-tab axis selectors.
        ("ch_x", "_scatter_ch_x_cb"),
        ("metric_x", "_scatter_metric_x_cb"),
        ("ch_y", "_scatter_ch_y_cb"),
        ("metric_y", "_scatter_metric_y_cb"),
        ("timepoint", "_scatter_tp_cb"),
        # Per-well aggregate ("Per-well aggregate") sub-tab axis selectors —
        # these are independent combos and were previously not captured, so the
        # aggregate scatter axes reset to defaults on reopen.
        ("agg_ch_x", "_scatter_agg_ch_x_cb"),
        ("agg_metric_x", "_scatter_agg_metric_x_cb"),
        ("agg_ch_y", "_scatter_agg_ch_y_cb"),
        ("agg_metric_y", "_scatter_agg_metric_y_cb"),
    ):
        v = _combo_text(app, attr)
        if v is not None:
            out[key] = v
    # Which sub-tab (per-cell vs aggregate) was active.
    mode = getattr(app, "_scatter_mode", None)
    if mode:
        out["mode"] = str(mode)
    # Per-timepoint multi-select checkboxes for each sub-tab (the dataset-wide
    # timepoint filter the user can toggle). Mirrors the Bar tab's
    # ``timepoint_selections`` round-trip.
    for key, attr in (
        ("tp_selections", "_scatter_tp_selections"),
        ("agg_tp_selections", "_scatter_agg_tp_selections"),
    ):
        sel = getattr(app, attr, None)
        if isinstance(sel, dict) and sel:
            out[key] = {str(k): bool(v) for k, v in sel.items()}
    return out


def _snapshot_image_table(app) -> dict:
    """Capture the Image Table grid: dimensions, per-cell well/chan/tp/fov,
    overlay toggles, and per-channel LUT min/max edits.
    """
    cells = getattr(app, "_image_table_cells", None)
    if not cells:
        return {}
    out: dict[str, Any] = {
        "rows": int(getattr(app._app_state.image_table, "rows", len(cells))),
        "cols": int(getattr(app._app_state.image_table, "cols",
                             len(cells[0]) if cells else 0)),
    }
    grid: list[list[dict[str, str]]] = []
    for row in cells:
        row_out: list[dict[str, str]] = []
        for cell in row:
            slc: dict[str, str] = {}
            for key, ck in (
                ("well", "well_cb"),
                ("chan", "chan_cb"),
                ("tp", "tp_cb"),
                ("fov", "fov_cb"),
            ):
                w = cell.get(ck) if isinstance(cell, dict) else None
                if w is not None:
                    try:
                        slc[key] = str(w.currentText())
                    except Exception:
                        swallowed()  # was: pass (silent except)
            row_out.append(slc)
        grid.append(row_out)
    out["cells"] = grid
    # Grid axes (which dimension varies down rows vs across cols). Captured so a
    # restore lays the selector grid out the same way before per-cell values are
    # replayed — otherwise the per-row channel/well combos + labels can land on
    # the wrong axis.
    for key, fld in (("row_axis", "row_axis"), ("col_axis", "col_axis")):
        v = getattr(app._app_state.image_table, fld, None)
        if isinstance(v, str) and v:
            out[key] = v
    # Per-row channel / well convenience combos (one per grid row); their value
    # is mirrored into each cell, but capturing them keeps the per-row UI in sync.
    for key, attr in (
        ("row_chans", "_image_table_row_chan_cbs"),
        ("row_wells", "_image_table_row_well_cbs"),
    ):
        cbs = getattr(app, attr, None)
        if cbs:
            try:
                out[key] = [str(cb.currentText()) for cb in cbs]
            except Exception:
                swallowed()
    for key, attr in (
        ("global_chan", "_image_table_global_chan_cb"),
        ("global_tp",   "_image_table_global_tp_cb"),
        ("global_fov",  "_image_table_global_fov_cb"),
    ):
        v = _combo_text(app, attr)
        if v is not None:
            out[key] = v
    # Per-row LUT + display options (top-hat / boundaries / binary), keyed by
    # row index. Display is now per-row (grid_view.build_lut_row), not a set of
    # global toggles.
    luts = getattr(app, "_image_table_lut", None) or {}
    color_cbs = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    row_luts: dict[str, dict[str, Any]] = {}
    if isinstance(luts, dict):
        for r, edits in luts.items():
            if not isinstance(edits, dict):
                continue
            slot: dict[str, Any] = {}
            mn = edits.get("min")
            mx = edits.get("max")
            # Raw/Corrected is a "source" combo (it replaced the old top-hat
            # checkbox), so the displayed image stage round-trips.
            src = edits.get("source")
            bound = edits.get("boundaries")
            binw = edits.get("binary")
            logw = edits.get("log")
            try:
                if mn is not None:
                    slot["min"] = str(mn.text())
                if mx is not None:
                    slot["max"] = str(mx.text())
                slot["auto"] = bool(edits.get("auto", True))
                if src is not None:
                    slot["source"] = str(src.currentData() or "raw")
                if bound is not None:
                    slot["boundaries"] = bool(bound.isChecked())
                if binw is not None:
                    slot["binary"] = str(binw.currentData() or "")
                if logw is not None:
                    slot["log"] = bool(logw.isChecked())
                # Per-row colormap (LutSelector: lut()/isReversed()).
                if isinstance(r, int) and 0 <= r < len(color_cbs):
                    sel = color_cbs[r]
                    slot["cmap"] = str(sel.lut())
                    slot["cmap_reversed"] = bool(sel.isReversed())
            except Exception:
                swallowed()  # silent except
                continue
            if slot:
                row_luts[str(r)] = slot
    if row_luts:
        out["row_luts"] = row_luts
    # Crop region (shared CropTool; (y0,x0,y1,x1) source pixels or None) plus the
    # display-only zoom, so a restored grid re-crops/re-scales. The ON-threshold
    # is intentionally NOT captured here — it's a global value shared with Cell
    # Gating and the plot tabs, so a saved-plot restore must not mutate it.
    ct = getattr(app, "_image_table_crop_tool", None)
    if ct is not None:
        try:
            out["crop"] = list(ct.crop) if ct.crop else None
            out["crop_mode"] = bool(ct.mode)
        except Exception:
            swallowed()
    try:
        out["zoom"] = float(app._app_state.image_table.zoom)
    except Exception:
        swallowed()
    return out


# ── restore ────────────────────────────────────────────────────────────────

def restore(app, state: dict) -> None:
    """Apply *state* in two phases — cross-cutting now, per-tab deferred.

    Per-tab combo values are stashed on ``app._view_state_pending`` and
    flushed by ``apply_pending_tab_state`` when each tab becomes active.
    """
    if not isinstance(state, dict):
        return

    # Cross-cutting state — apply eagerly.

    wells = state.get("selected_wells") or []
    if isinstance(wells, list):
        saved = {str(w) for w in wells if isinstance(w, str)}
        current = set(app._app_state.dataset.well_paths or {})
        valid = saved & current
        # X14: remember wells saved for datasets/plates not currently loaded so
        # ``snapshot`` can re-persist them — a subset load must not prune them.
        app._view_state_absent_wells = saved - current
        if valid and hasattr(app, "_set_selected_wells"):
            try:
                app._set_selected_wells(valid, commit=False)
            except Exception:
                _logger.debug("view_state: _set_selected_wells failed", exc_info=True)

    # Rep-set visibility (group mode) — re-apply each selection's hidden flag and
    # the solo-well set so a plot saved in replicate mode restores the groups it
    # was showing. Matched by selection id; absent (older saved plots) → skipped.
    vis = state.get("selection_visibility")
    if isinstance(vis, dict):
        hidden_ids = {str(x) for x in (vis.get("hidden_ids") or [])}
        for sel in (app._app_state.selection.selections or []):
            if sel.get("id") is not None:
                sel["hidden"] = str(sel.get("id")) in hidden_ids
        solo = vis.get("solo_wells")
        if isinstance(solo, list):
            wp = set(app._app_state.dataset.well_paths or {})
            saved_solo = {str(w) for w in solo}
            # X14: preserve solo wells belonging to other datasets (see above).
            app._view_state_absent_solo = saved_solo - wp
            app._app_state.selection.active_solo_wells = saved_solo & wp
        if hasattr(app, "_refresh_sidebar_map"):
            try:
                app._refresh_sidebar_map()
            except Exception:
                swallowed()

    if "use_sem" in state:
        try:
            app._app_state.plot.use_sem = bool(state["use_sem"])
        except Exception:
            swallowed()  # was: pass (silent except)

    if "show_threshold_line" in state:
        try:
            app._app_state.plot.show_threshold_line = bool(state["show_threshold_line"])
        except Exception:
            swallowed()

    if "show_significance" in state:
        try:
            app._app_state.plot.show_significance = bool(state["show_significance"])
        except Exception:
            swallowed()

    if "use_fov_replicates" in state:
        # The FOV-spread toggle on Line Graphs / Bar Plots. Set the field, then
        # sync the toggle buttons; ``refresh_fov_btn_state`` is a no-op until
        # those widgets exist (lazy tabs), reading the field directly so a later
        # build still reflects the restored value.
        try:
            app._app_state.plot.use_fov_replicates = bool(state["use_fov_replicates"])
            if hasattr(app, "_refresh_fov_btn_state"):
                app._refresh_fov_btn_state()
        except Exception:
            swallowed()  # was: pass (silent except)

    fc = state.get("fold_change") or {}
    if isinstance(fc, dict):
        if "vs_control_on" in fc:
            app._app_state.plot.fc_vs_control_on = bool(fc.get("vs_control_on"))
        if "control_label" in fc:
            app._app_state.plot.fc_control_label = str(fc.get("control_label") or "")
        if "vs_t0_on" in fc:
            app._app_state.plot.fc_vs_t0_on = bool(fc.get("vs_t0_on"))
        if "baseline_tp" in fc:
            app._app_state.plot.fc_baseline_tp = str(fc.get("baseline_tp") or "")
        # Legacy single-op snapshot: fan out to both axes via the
        # ``fc_op`` property setter so old saved state restores cleanly.
        if "op" in fc:
            op_in = str(fc.get("op") or "ratio")
            app._app_state.plot.fc_op = op_in if op_in in ("ratio", "subtract") else "ratio"
        if "op_ctrl" in fc:
            op_in = str(fc.get("op_ctrl") or "ratio")
            app._app_state.plot.fc_op_ctrl = op_in if op_in in ("ratio", "subtract") else "ratio"
        if "op_t0" in fc:
            op_in = str(fc.get("op_t0") or "ratio")
            app._app_state.plot.fc_op_t0 = op_in if op_in in ("ratio", "subtract") else "ratio"
        if "order" in fc:
            o_in = str(fc.get("order") or "ctrl_first")
            app._app_state.plot.fc_order = o_in if o_in in ("ctrl_first", "t0_first") else "ctrl_first"
        # Push the restored state into any fold-change combos that have
        # already been built. Lazily-built tabs (Bar) will pick the
        # values up on first install via _repopulate_*_combo, which both
        # read app._fc_* directly — so this only matters for combos that
        # exist at restore time (e.g. Line, built eagerly).
        try:
            from well_viewer.domain.fold_change_controls import _sync_widgets_to_state
            _sync_widgets_to_state(app)
        except Exception:
            _logger.debug("view_state: fold-change combo sync failed", exc_info=True)

    prefs_in = state.get("export_style_prefs")
    if isinstance(prefs_in, dict):
        # Axis-scoped keys are persisted per-figure (below); strip any that
        # linger in a pre-split saved global dict so they can't leak into every
        # tab (or the batch-export path) on load.
        from well_viewer.core.plot_style import AXIS_SCOPED_PREF_KEYS
        clean_in = {k: v for k, v in prefs_in.items() if k not in AXIS_SCOPED_PREF_KEYS}
        existing = getattr(app, "_export_style_prefs", None)
        if isinstance(existing, dict):
            existing.update(clean_in)
        else:
            app._export_style_prefs = dict(clean_in)

    axis_in = state.get("export_axis_prefs")
    if isinstance(axis_in, dict):
        app._export_axis_prefs = {
            str(k): dict(v) for k, v in axis_in.items() if isinstance(v, dict)
        }

    # Active channel. Direct field assignment mirrors the active_metric path
    # below: the combos may not exist yet, but ``recalculate_threshold`` (which
    # runs right after view_state.load in load_controller) calls
    # ``update_channel_selector``, which picks the active label off the field we
    # just set — and validates it against the dataset's channels, falling back
    # when the saved channel is stale. We must NOT gate on
    # ``ch in fluor_channels`` here: at view_state.load time ``fluor_channels`` is
    # still EMPTY (populated only later by ``recalculate_threshold``), so that
    # guard dropped every restored channel and each reopen reverted to the default
    # first fluor channel. Setting the field also covers ratio / shape channels,
    # which never appear in ``fluor_channels``.
    ch = state.get("active_channel")
    if isinstance(ch, str) and ch:
        try:
            app._app_state.channel.active_channel = ch
            set_active_channel(app, ch)
        except Exception:
            _logger.debug("view_state: active_channel restore failed", exc_info=True)

    metric = state.get("active_metric")
    if isinstance(metric, str) and metric:
        # Direct field assignment so tabs built lazily after this point
        # pick up the saved metric for their initial render.
        app._app_state.channel.active_metric = metric
        # Push the value into every metric combo widget that exists right
        # now (e.g. the global ctxbar's _plotting_metric_cb, built early
        # during centre-view construction). Without this the dropdown
        # reverts to its default first item ("Mean Intensity") on app
        # reopen because set_active_metric — which usually drives the
        # combo sync — early-returns when the field is already at the
        # target value. The user then has to "wiggle" the dropdown to
        # force the sync, which was particularly visible when smFISH
        # count was the last-selected metric.
        try:
            from well_viewer.core.channel_state_controller import sync_metric_combos
            sync_metric_combos(app, metric)
        except Exception:
            _logger.debug("view_state: metric combo sync failed", exc_info=True)

    # Re-derive the value column from the *restored* channel + metric. The
    # channel was restored above via set_active_channel, which derived
    # active_val_col from the metric as it stood *before* this metric restore;
    # the metric restore then field-set the metric (and synced combos) but left
    # active_val_col stale. Without this the post-load redraw plots the wrong
    # column until the user toggles the channel. sync_val_col skips ratio/shape.
    try:
        app._app_state.channel.sync_val_col()
    except Exception:
        _logger.debug("view_state: active_val_col re-derive failed", exc_info=True)

    # Stash per-tab state for deferred apply on first tab visit.
    tabs = state.get("tabs") or {}
    if isinstance(tabs, dict) and tabs:
        app._view_state_pending = {str(k): dict(v) for k, v in tabs.items() if isinstance(v, dict)}

    # Active tab is restored last so its on-change hook triggers any
    # lazy build with cross-cutting state already in place. Drives the
    # nested plotting stack when a plot sub-tab is requested.
    target = state.get("active_centre_tab")
    if isinstance(target, str) and target:
        _activate_tab(app, target)


def _activate_tab(app, name: str) -> None:
    outer = getattr(app, "_notebook", None)
    if outer is None:
        return
    try:
        if name in _PLOT_SUBTABS:
            outer.setCurrentByName("Plotting")
            sub = getattr(app, "_plotting_notebook", None)
            if sub is not None:
                sub.setCurrentByName(name)
        elif name in _IMAGE_BROWSER_SUBTABS:
            outer.setCurrentByName("Image Browser")
            sub = getattr(app, "_image_browser_subtabs", None)
            if sub is not None:
                for i in range(sub.count()):
                    if sub.tabText(i) == name:
                        sub.setCurrentIndex(i)
                        break
        else:
            outer.setCurrentByName(name)
    except Exception:
        _logger.debug("view_state: tab activation failed for %r", name, exc_info=True)


def apply_pending_tab_state(app, tab_name: str) -> bool:
    """Pop *tab_name*'s pending state (if any) into live widgets.

    Called from ``_on_tab_change`` so a tab that built lazily still receives
    its restored combo values the first time the user lands on it.

    Returns ``True`` when pending state for *tab_name* was found and applied, so
    the caller can redraw — the apply runs *after* the tab's initial draw, and
    some restored state (the Bar timepoint multi-select, swarm/violin, scatter
    axes) changes what should be plotted.
    """
    pending = getattr(app, "_view_state_pending", None)
    if not pending:
        return False
    state = pending.pop(tab_name, None)
    if not state:
        return False
    try:
        if tab_name == "Bar Plots":
            _apply_bar(app, state)
        elif tab_name == "Distribution":
            _apply_distribution(app, state)
        elif tab_name == "Scatter Plot":
            _apply_scatter(app, state)
        elif tab_name == "Image Table":
            _apply_image_table(app, state)
    except Exception:
        _logger.debug("view_state: apply_pending_tab_state(%r) failed", tab_name, exc_info=True)
    return True


def _set_combo_if_valid(app, attr: str, value: str) -> None:
    w = getattr(app, attr, None)
    if w is None:
        return
    try:
        items = [w.itemText(i) for i in range(w.count())]
        if value in items:
            w.setCurrentText(value)
            return
        # Case-insensitive fallback: tolerate casing drift between when the
        # state was saved and the current item list (e.g. a channel token
        # persisted as "gfp" before the combos switched to displaying "GFP").
        # Without this an old saved scatter-axis selection would silently reset
        # to the first channel on reload.
        low = str(value).lower()
        for it in items:
            if it.lower() == low:
                w.setCurrentText(it)
                return
    except Exception:
        swallowed()  # was: pass (silent except)


def _apply_bar(app, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(app, "_bar_tp_cb", str(state["timepoint"]))
    if "timepoint_selections" in state and isinstance(state["timepoint_selections"], dict):
        try:
            # Intersect with the dataset's current timepoint keys so a saved
            # selection from a slightly differently-formatted run doesn't leave
            # orphan keys that the popup ignores. ``update_bar_tp_menu`` keys
            # via ``f"{t:.4g}"``; we keep whatever is in the saved file as the
            # authoritative selection unless the key is no longer present.
            current = dict(app._app_state.bar.tp_selections or {})
            saved = {str(k): bool(v) for k, v in state["timepoint_selections"].items()}
            merged = {k: (saved[k] if k in saved else False) for k in current}
            # When the saved set covers every current timepoint (common when
            # nothing's changed between runs), use it verbatim.
            if set(saved.keys()) >= set(current.keys()):
                merged = {k: saved.get(k, False) for k in current}
            app._app_state.bar.tp_selections = merged or saved
        except Exception:
            swallowed()  # was: pass (silent except)
        # Sync the hidden back-compat combo + the "(X selected)" label so every
        # downstream reader (export, status text, the popup itself) reflects
        # the restored selection. Without this the popup defaults checkboxes
        # from a stale ``tp_selections`` snapshot taken before the restore.
        # Inlined (rather than importing the bar controller helpers) so this
        # module doesn't add a new deferred ``from well_viewer.features ...``
        # import — the cycle-budget guard in test_deferred_imports caps those.
        try:
            sels = app._app_state.bar.tp_selections or {}
            hidden = getattr(app, "_bar_tp_cb", None)
            if hidden is not None and sels:
                for tp_str in sorted(sels.keys(), key=lambda s: float(s)):
                    if sels[tp_str]:
                        hidden.setCurrentText(tp_str)
                        break
            lbl = getattr(app, "_bar_tp_label", None)
            if lbl is not None:
                total = len(sels)
                count = sum(1 for v in sels.values() if v)
                if total and count == total:
                    lbl.setText(f"(All {count} selected)")
                else:
                    lbl.setText(f"({count}/{total} selected)" if total else "(0 selected)")
        except Exception:
            _logger.debug("view_state: bar tp post-restore sync failed", exc_info=True)
    _bar = app._app_state.feature_slice("bar")
    if _bar is not None:
        if "swarm" in state:
            _bar.swarm = bool(state["swarm"])
        if "violin" in state:
            _bar.violin = bool(state["violin"])
    slider = getattr(app, "_violin_slider", None)
    if slider is not None and "violin_smooth" in state:
        try:
            slider.setValue(int(state["violin_smooth"]))
        except Exception:
            swallowed()  # was: pass (silent except)
    plot = getattr(app._app_state, "plot", None) if hasattr(app, "_app_state") else None
    if plot is not None:
        if "group_mode" in state and str(state["group_mode"]) in ("well", "timepoint"):
            plot.bar_group_mode = str(state["group_mode"])
        if "pool_replicates" in state:
            plot.bar_pool_replicates = bool(state["pool_replicates"])
        if str(state.get("bar_err_kind")) in ("none", "sem", "sd", "ci95"):
            plot.bar_err_kind = str(state["bar_err_kind"])
        if str(state.get("bar_show_mode")) in ("mean", "mean_spread", "all_pts"):
            plot.bar_show_mode = str(state["bar_show_mode"])
        if "bar_order" in state and isinstance(state["bar_order"], list):
            plot.bar_order = [str(k) for k in state["bar_order"]]


def _apply_distribution(app, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(app, "_distribution_tp_cb", str(state["timepoint"]))
    if "mode" in state:
        _set_combo_if_valid(app, "_distribution_mode_cb", str(state["mode"]))
    if "layout" in state:
        _set_combo_if_valid(app, "_distribution_layout_cb", str(state["layout"]))
    _dist = app._app_state.feature_slice("distribution")
    if "bins" in state:
        try:
            bins = int(state["bins"])
        except (TypeError, ValueError):
            return
        spin = getattr(app, "_distribution_bins_spin", None)
        if spin is not None:
            try:
                spin.setValue(bins)
            except Exception:
                swallowed()  # was: pass (silent except)
        if _dist is not None:
            _dist.bins = bins
    if "log_x" in state:
        if _dist is not None:
            _dist.log_x = bool(state["log_x"])
        cb = getattr(app, "_distribution_log_x_cb", None)
        if cb is not None:
            try:
                cb.setChecked(bool(state["log_x"]))
            except Exception:
                swallowed()  # was: pass (silent except)
    if "log_y" in state:
        if _dist is not None:
            _dist.log_y = bool(state["log_y"])
        cb = getattr(app, "_distribution_log_y_cb", None)
        if cb is not None:
            try:
                cb.setChecked(bool(state["log_y"]))
            except Exception:
                swallowed()  # was: pass (silent except)


def _apply_scatter(app, state: dict) -> None:
    for key, attr in (
        ("ch_x", "_scatter_ch_x_cb"),
        ("metric_x", "_scatter_metric_x_cb"),
        ("ch_y", "_scatter_ch_y_cb"),
        ("metric_y", "_scatter_metric_y_cb"),
        ("timepoint", "_scatter_tp_cb"),
        ("agg_ch_x", "_scatter_agg_ch_x_cb"),
        ("agg_metric_x", "_scatter_agg_metric_x_cb"),
        ("agg_ch_y", "_scatter_agg_ch_y_cb"),
        ("agg_metric_y", "_scatter_agg_metric_y_cb"),
    ):
        if key in state:
            _set_combo_if_valid(app, attr, str(state[key]))
    # Restore the per-timepoint multi-select filters, intersected with the
    # timepoints the current dataset actually has (a saved tp may be gone).
    for skey, attr in (
        ("tp_selections", "_scatter_tp_selections"),
        ("agg_tp_selections", "_scatter_agg_tp_selections"),
    ):
        saved = state.get(skey)
        live = getattr(app, attr, None)
        if isinstance(saved, dict) and isinstance(live, dict):
            for tp_str in live:
                if tp_str in saved:
                    live[tp_str] = bool(saved[tp_str])
    # Refresh the timepoint-selector summary labels so the restored filter is
    # reflected in the UI, then re-apply the active sub-tab last (it repopulates
    # the axis combos but preserves the values we just set, and redraws).
    for fn_name in ("_update_scatter_tp_selection_display",
                    "_update_tp_selection_display"):
        fn = getattr(app, fn_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                swallowed()
    mode = state.get("mode")
    set_mode = getattr(app, "_scatter_set_mode", None)
    if mode and callable(set_mode):
        try:
            set_mode(str(mode))
        except Exception:
            swallowed()


def _apply_image_table(app, state: dict) -> None:
    """Restore the Image Table grid (dimensions, per-cell combos, toggles,
    per-channel LUTs) and re-render via image_table_generate.

    The tab builder constructs widgets lazily; this only runs once the tab
    is on screen, so the widget references are guaranteed to exist.
    """
    # One deferred feature import for the whole function (persistence stays
    # feature-free at module scope — see test_layering / test_deferred_imports);
    # both helpers are used below.
    try:
        from well_viewer.features.image_browser.image_table.controller import (
            image_table_apply_dimensions,
            image_table_rebuild_lut_row,
            image_table_set_zoom,
        )
    except Exception:  # pragma: no cover - import failure leaves these no-ops
        image_table_apply_dimensions = image_table_set_zoom = None
        image_table_rebuild_lut_row = None

    # Grid axes first (before the dimension rebuild), so the per-row channel/well
    # combos + labels are laid out on the same axes the plot was saved with. The
    # per-cell combos restored below remain authoritative for the actual values.
    for key, which in (("row_axis", "row"), ("col_axis", "col")):
        axis = state.get(key)
        if not isinstance(axis, str) or not axis:
            continue
        cb = getattr(app, f"_image_table_{which}_axis_cb", None)
        if cb is not None:
            try:
                idx = cb.findData(axis)
                if idx >= 0:
                    blocked = cb.blockSignals(True)
                    try:
                        cb.setCurrentIndex(idx)
                    finally:
                        cb.blockSignals(blocked)
            except Exception:
                swallowed()
        try:
            setattr(app._app_state.image_table, f"{which}_axis", axis)
            setattr(app, f"_image_table_{which}_axis", axis)
        except Exception:
            swallowed()

    # Dimensions — set the spinners first; the apply_dimensions hook
    # rebuilds the selector grid + cells list against the new size.
    rows = state.get("rows")
    cols = state.get("cols")
    if isinstance(rows, int) or isinstance(cols, int):
        rows_spin = getattr(app, "_image_table_rows_spin", None)
        cols_spin = getattr(app, "_image_table_cols_spin", None)
        # Block the per-spinner valueChanged → apply_dimensions firing twice;
        # call apply_dimensions once after both values are set.
        changed = False
        for spin, val in ((rows_spin, rows), (cols_spin, cols)):
            if spin is None or not isinstance(val, int):
                continue
            try:
                if int(spin.value()) != int(val):
                    blocked = spin.blockSignals(True)
                    try:
                        spin.setValue(int(val))
                    finally:
                        spin.blockSignals(blocked)
                    changed = True
            except Exception:
                swallowed()  # was: pass (silent except)
        if changed and image_table_apply_dimensions is not None:
            try:
                image_table_apply_dimensions(app)
            except Exception:
                _logger.debug("image_table: apply_dimensions failed", exc_info=True)

    cells = state.get("cells") or []
    live = getattr(app, "_image_table_cells", None) or []
    if isinstance(cells, list) and live:
        for r, row_state in enumerate(cells):
            if r >= len(live) or not isinstance(row_state, list):
                continue
            for c, cell_state in enumerate(row_state):
                if c >= len(live[r]) or not isinstance(cell_state, dict):
                    continue
                cell = live[r][c]
                if not isinstance(cell, dict):
                    continue
                for key, ck in (
                    ("well", "well_cb"),
                    ("chan", "chan_cb"),
                    ("tp", "tp_cb"),
                    ("fov", "fov_cb"),
                ):
                    val = cell_state.get(key)
                    if not isinstance(val, str) or not val:
                        continue
                    w = cell.get(ck)
                    if w is None:
                        continue
                    try:
                        items = [w.itemText(i) for i in range(w.count())]
                        if val in items:
                            blocked = w.blockSignals(True)
                            try:
                                w.setCurrentText(val)
                            finally:
                                w.blockSignals(blocked)
                    except Exception:
                        swallowed()  # was: pass (silent except)

    # Per-row channel / well convenience combos — set to match (the per-cell
    # values are already restored above and stay authoritative).
    for key, attr in (
        ("row_chans", "_image_table_row_chan_cbs"),
        ("row_wells", "_image_table_row_well_cbs"),
    ):
        texts = state.get(key)
        cbs = getattr(app, attr, None)
        if not isinstance(texts, list) or not cbs:
            continue
        for cb, text in zip(cbs, texts):
            if not isinstance(text, str) or not text or cb is None:
                continue
            try:
                if cb.findText(text) >= 0:
                    blocked = cb.blockSignals(True)
                    try:
                        cb.setCurrentText(text)
                    finally:
                        cb.blockSignals(blocked)
            except Exception:
                swallowed()

    for key, attr in (
        ("global_chan", "_image_table_global_chan_cb"),
        ("global_tp",   "_image_table_global_tp_cb"),
        ("global_fov",  "_image_table_global_fov_cb"),
    ):
        if key in state and isinstance(state[key], str):
            _set_combo_if_valid(app, attr, state[key])

    # Rebuild the per-row levels & display table now that the channel combos are
    # restored. The per-row labels ("Row N · CHAN") + colour selectors are
    # derived from the channel combos when the table is built; restore set those
    # combos with signals blocked, so the usual channel-change rebuild never
    # fired and the labels showed the pre-restore channels. Rebuilding here (it
    # preserves prior per-row values across rebuilds) makes the labels match the
    # restored channels before the row_luts values are re-applied below.
    if image_table_rebuild_lut_row is not None:
        try:
            image_table_rebuild_lut_row(app)
        except Exception:
            _logger.debug("image_table: per-row LUT table rebuild failed", exc_info=True)

    # Per-row LUT + display restore (row_luts keyed by str(row index)). Display
    # (top-hat / boundaries / binary) is per-row now, so it round-trips here
    # alongside min/max rather than as separate global toggles.
    row_luts = state.get("row_luts") or {}
    live_luts = getattr(app, "_image_table_lut", None) or {}
    color_cbs = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    if isinstance(row_luts, dict) and isinstance(live_luts, dict):
        for r_key, slot in row_luts.items():
            try:
                r = int(r_key)
            except (TypeError, ValueError):
                continue
            edits = live_luts.get(r)
            if not isinstance(edits, dict) or not isinstance(slot, dict):
                continue
            # Min/max first (via setText, which doesn't fire textEdited under
            # blockSignals) so the explicit "auto" restore below isn't flipped.
            for k in ("min", "max"):
                v = slot.get(k)
                edit = edits.get(k)
                if edit is None or not isinstance(v, str):
                    continue
                try:
                    blocked = edit.blockSignals(True)
                    try:
                        edit.setText(v)
                    finally:
                        edit.blockSignals(blocked)
                except Exception:
                    swallowed()  # was: pass (silent except)
            if "auto" in slot:
                edits["auto"] = bool(slot["auto"])
            for k in ("boundaries", "log"):
                w = edits.get(k)
                if w is None or k not in slot:
                    continue
                try:
                    blocked = w.blockSignals(True)
                    try:
                        w.setChecked(bool(slot[k]))
                    finally:
                        w.blockSignals(blocked)
                except Exception:
                    swallowed()
            # Raw/Corrected ("source") + Binary are userData combos; legacy
            # snapshots only carried a "tophat" bool → map True→corrected.
            src_val = slot.get("source")
            if src_val is None and "tophat" in slot:
                src_val = "corrected" if slot.get("tophat") else "raw"
            for cb_key, want in (("source", src_val), ("binary", slot.get("binary"))):
                w = edits.get(cb_key)
                if w is None or not isinstance(want, str):
                    continue
                try:
                    for i in range(w.count()):
                        if w.itemData(i) == want:
                            blocked = w.blockSignals(True)
                            try:
                                w.setCurrentIndex(i)
                            finally:
                                w.blockSignals(blocked)
                            break
                except Exception:
                    swallowed()
            # Per-row colormap (LutSelector).
            cmap = slot.get("cmap")
            if isinstance(cmap, str) and cmap and isinstance(r, int) and 0 <= r < len(color_cbs):
                sel = color_cbs[r]
                try:
                    blocked = sel.blockSignals(True)
                    try:
                        sel.setLut(cmap, reversed=bool(slot.get("cmap_reversed", False)))
                    finally:
                        sel.blockSignals(blocked)
                except Exception:
                    swallowed()

    # Crop region: re-apply the saved rectangle (or clear it) + crop mode.
    # notify=False so the CropTool's on_change (image_table_generate) does NOT
    # fire here — restore must not composite the grid off disk during dataset
    # load; the restored crop applies on the next user-triggered Generate.
    ct = getattr(app, "_image_table_crop_tool", None)
    if ct is not None and "crop" in state:
        try:
            crop = state.get("crop")
            if isinstance(crop, (list, tuple)) and len(crop) == 4:
                ct.set_crop(tuple(int(v) for v in crop), notify=False)
            else:
                ct.clear(notify=False)
            if bool(state.get("crop_mode", False)) != bool(ct.mode):
                ct.toggle_mode(notify=False)
        except Exception:
            swallowed()

    # Zoom (display-only) via its feature setter (imported once at the top).
    if isinstance(state.get("zoom"), (int, float)) and image_table_set_zoom is not None:
        try:
            image_table_set_zoom(app, float(state["zoom"]))
        except Exception:
            _logger.debug("image_table: zoom restore failed", exc_info=True)

    # Intentionally NOT calling image_table_generate here — restore only
    # pre-fills the dropdowns, the user clicks Generate when they actually
    # want images loaded off disk.


# ── save / load entry points ───────────────────────────────────────────────

def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    try:
        state = snapshot(app)
    except Exception:
        _logger.exception("view_state: snapshot failed; not saving")
        return
    _doc.set_section(app, "view_state", state)


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    state = _doc.get_section(app, "view_state")
    if not isinstance(state, dict):
        return
    sv = state.get("schema_version")
    if sv is not None and sv != SCHEMA_VERSION:
        # X7: try to migrate an old doc forward instead of silently dropping the
        # user's entire saved view. Only discard when it genuinely can't be
        # reached (unknown step, or a doc from a newer app).
        migrated = _migrate(state, sv)
        if migrated is None:
            _logger.warning(
                "view_state schema_version=%r cannot be migrated to %d; "
                "ignoring saved state.", sv, SCHEMA_VERSION)
            return
        _logger.info(
            "view_state migrated from schema_version=%r to %d.", sv, SCHEMA_VERSION)
        state = migrated
    try:
        restore(app, state)
    except Exception:
        _logger.exception("view_state: restore failed; continuing with defaults")


def reset(app) -> None:
    """Wipe the live view-state (selections + fold-change + per-tab combos)
    and drop the ``view_state`` section from ``persistence.json``.

    Caller is responsible for confirming with the user — this is destructive.
    Dataset-level state (loaded wells, channels, ratios, heatmap layouts) is
    intentionally left alone; only the user's *view* selections reset.
    """
    # Selected wells.
    if hasattr(app, "_set_selected_wells"):
        try:
            app._set_selected_wells(set(), commit=False)
        except Exception:
            _logger.debug("reset: clearing selected wells failed", exc_info=True)
    # Fold-change scopes.
    app._app_state.plot.fc_vs_control_on = False
    app._app_state.plot.fc_control_label = ""
    app._app_state.plot.fc_vs_t0_on = False
    app._app_state.plot.fc_baseline_tp = ""
    app._app_state.plot.fc_op_ctrl = "ratio"
    app._app_state.plot.fc_op_t0 = "ratio"
    app._app_state.plot.fc_order = "ctrl_first"
    try:
        from well_viewer.domain.fold_change_controls import _sync_widgets_to_state
        _sync_widgets_to_state(app)
    except Exception:
        _logger.debug("reset: fold-change sync failed", exc_info=True)
    # Per-tab combo defaults.
    _reset_bar(app)
    _reset_distribution(app)
    _reset_scatter(app)
    # Drop any pending state stashed from a previous restore.
    app._view_state_pending = None
    # Drop the view_state section so a fresh open doesn't repopulate.
    if app._app_state.dataset.data_dir:
        try:
            _doc.set_section(app, "view_state", None)
        except Exception:
            _logger.debug("reset: persistence.json update failed", exc_info=True)
    # Force a redraw on whatever tab is active. The scope coalescer redraws
    # the visible fold-change tab (bar / line) and marks the rest dirty; the
    # default-scope _redraw() handles line / distribution / heat map.
    from well_viewer.domain.fold_change_scopes import redraw_scopes_or_defer
    try:
        redraw_scopes_or_defer(app)
    except Exception:
        _logger.debug("reset: scope redraw failed", exc_info=True)
    try:
        app._redraw()
    except Exception:
        _logger.debug("reset: default redraw failed", exc_info=True)


def _reset_bar(app) -> None:
    _bar = app._app_state.feature_slice("bar")
    if _bar is not None:
        _bar.swarm = False
        _bar.violin = False
    slider = getattr(app, "_violin_slider", None)
    if slider is not None:
        try:
            slider.setValue(100)
        except Exception:
            swallowed()  # was: pass (silent except)


def _reset_distribution(app) -> None:
    mode = getattr(app, "_distribution_mode_cb", None)
    if mode is not None:
        try:
            mode.setCurrentText("Histogram + KDE")
        except Exception:
            swallowed()  # was: pass (silent except)
    layout = getattr(app, "_distribution_layout_cb", None)
    if layout is not None:
        try:
            layout.setCurrentText("Overlay")
        except Exception:
            swallowed()  # was: pass (silent except)
    spin = getattr(app, "_distribution_bins_spin", None)
    if spin is not None:
        try:
            spin.setValue(40)
        except Exception:
            swallowed()  # was: pass (silent except)
    _dist = app._app_state.feature_slice("distribution")
    if _dist is not None:
        _dist.bins = 40
        _dist.log_x = False
        _dist.log_y = False
    cb = getattr(app, "_distribution_log_x_cb", None)
    if cb is not None:
        try:
            cb.setChecked(False)
        except Exception:
            swallowed()  # was: pass (silent except)
    cb = getattr(app, "_distribution_log_y_cb", None)
    if cb is not None:
        try:
            cb.setChecked(False)
        except Exception:
            swallowed()  # was: pass (silent except)


def _reset_scatter(app) -> None:
    # Per-axis combos: revert to the first item if any, leave alone otherwise.
    for attr in ("_scatter_ch_x_cb", "_scatter_metric_x_cb",
                 "_scatter_ch_y_cb", "_scatter_metric_y_cb",
                 "_scatter_tp_cb"):
        w = getattr(app, attr, None)
        if w is None:
            continue
        try:
            if w.count() > 0:
                w.setCurrentIndex(0)
        except Exception:
            swallowed()  # was: pass (silent except)
