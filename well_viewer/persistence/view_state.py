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

from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.bar_colors import BAR_COLOR_MODES

import logging
from typing import Any

from well_viewer.persistence import _doc
from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.core.safe import swallow, swallowed
from well_viewer.core.ui_helpers import multiselect_button_text

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


def _migrate(state: dict, from_version) -> dict | None:
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

def _nondefault_export_prefs(ctx) -> dict:
    """Only the export-style prefs the user actually changed.

    Persisting the *full* dict (defaults included) froze the old default into
    ``persistence.json``: a later app version that changed a default would be
    silently overridden by the stale saved value for users who never touched
    it (audit X8). Saving only the diff lets unset keys track future defaults —
    on restore they merge onto the fresh ``DEFAULT_EXPORT_STYLE_PREFS`` copy the
    app is constructed with.
    """
    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
    cur = ctx.state.export_prefs.style_prefs or {}
    return {
        k: v for k, v in cur.items()
        if k not in DEFAULT_EXPORT_STYLE_PREFS or v != DEFAULT_EXPORT_STYLE_PREFS[k]
    }


def snapshot(ctx) -> dict:
    """Build a JSON-serialisable view-state dict from current app state."""
    return {
        "schema_version": SCHEMA_VERSION,
        # X14: union back the wells that were saved for datasets/plates not
        # currently loaded (stashed on restore), so a subset-load-then-save does
        # not irreversibly prune wells the current dataset simply doesn't contain.
        "selected_wells": sorted(
            set(ctx.state.selection.selected_wells or [])
            | (ctx.runtime.view_state.absent_wells or set())
        ),
        # Rep-set visibility (which groups are shown on the plots) + solo wells —
        # in group/replicate mode this, not ``selected_wells``, is what drives the
        # plate and the plot, so a plot saved in that mode restores what was shown.
        "selection_visibility": {
            "hidden_ids": [
                str(s.get("id")) for s in (ctx.state.selection.selections or [])
                if s.get("id") is not None and s.get("hidden")
            ],
            "solo_wells": sorted(
                set(ctx.state.selection.active_solo_wells or [])
                | (ctx.runtime.view_state.absent_solo or set())
            ),
        },
        "active_centre_tab": _current_leaf_tab(ctx),
        "active_channel": str(ctx.state.channel.active_channel or ""),
        "active_metric": str(ctx.state.channel.active_metric or ""),
        "use_sem": bool(ctx.state.plot.use_sem),
        "use_fov_replicates": bool(ctx.state.plot.use_fov_replicates),
        "show_threshold_line": bool(ctx.state.plot.show_threshold_line),
        "show_significance": bool(ctx.state.plot.show_significance),
        # The time-axis label correction, both halves. Captured because it
        # changes what every time axis in a restored view reads, and a saved
        # plot whose axis said 4, 8, 12 must not come back saying 1, 2, 3 with
        # no explanation. Saved as two keys rather than one because they are two
        # independent statements about the plate; an older file carrying only
        # ``time_scale`` restores to a zero offset, which is that file's truth.
        "time_scale": float(ctx.state.plot.time_scale),
        "time_offset": float(ctx.state.plot.time_offset),
        # Per-panel legend-trace visibility, the user's Line-graph draw order (the
        # Bar draw order rides in the per-tab block), and the cross-tab focus
        # timepoint — all shape the rendered plot but were previously uncaptured.
        "legend_visible": {
            str(k): bool(v)
            for k, v in (ctx.state.plot.legend_visible or {}).items()
        },
        "line_order_rsets": [str(x) for x in (ctx.state.plot.line_order_rsets or [])],
        "line_order_wells": [str(x) for x in (ctx.state.plot.line_order_wells or [])],
        "focus_timepoint": ctx.state.plot.focus_timepoint,
        "fold_change": _snapshot_fold_change(ctx),
        "export_style_prefs": _nondefault_export_prefs(ctx),
        "export_axis_prefs": _snapshot_axis_prefs(ctx),
        "tabs": _snapshot_tabs(ctx),
    }


def _snapshot_axis_prefs(ctx) -> dict:
    """The ``export_axis_prefs`` block: per-figure axis and mark preferences.

    Keys produced by ``plot_style.figure_pref_key``'s fallback are dropped
    rather than written. That fallback spells the key ``fig:<id(fig)>``, a
    memory address — stable within a session and different in the next one, so
    such an entry can never match on reload. Writing it saves nothing and
    accumulates: every session added another dead block to the file, and
    ``figure_prefs`` creates one on *read*, so simply opening a tab grew it.
    A figure that lands here has a real bug behind it (its registry name is
    missing from ``_FIG_PREF_KEY_MAP``), which is why the fallback also warns.
    """
    return {
        str(k): dict(v)
        for k, v in (ctx.state.export_prefs.axis_prefs or {}).items()
        if isinstance(v, dict) and not str(k).startswith("fig:")
    }


def _snapshot_fold_change(ctx) -> dict:
    """The ``fold_change`` block: everything the normalization needs to come back.

    Split out of :func:`snapshot` so it can be exercised against a bare
    ``PlotState`` — the whole-app snapshot needs a built window, which put the
    only round-trip test for this block in the slow lane and left the per-member
    mapping with no fast-lane coverage at all.
    """
    plot = ctx.state.plot
    return {
        "vs_control_on": bool(plot.fc_vs_control_on),
        # The authoritative form: an object, because a string cannot say
        # whether a name means a well or a replicate set and group names
        # are unrestricted free text. ``control_label`` is still written
        # beside it, carrying the plain token, so a rolled-back build finds
        # something it understands rather than nothing.
        "control": (plot.fc_control.to_storage()
                    if plot.fc_control is not None else None),
        "control_label": (plot.fc_control.display_name
                          if plot.fc_control is not None else ""),
        "control_mode": str(plot.fc_control_mode or "single"),
        "control_render": str(plot.fc_control_render or "self"),
        # Written verbatim and unfiltered — entries for members that are not
        # currently plotted are exactly what the retain-don't-drop contract is
        # about, and filtering here would quietly enforce the opposite across a
        # save/load cycle.
        "control_map": {
            str(k): v.to_storage()
            for k, v in (plot.fc_control_map or {}).items()
            if v is not None
        },
        "vs_t0_on": bool(plot.fc_vs_t0_on),
        "baseline_tp": str(getattr(plot, "fc_baseline_tp", "") or ""),
        "op_ctrl": str(getattr(plot, "fc_op_ctrl", "ratio") or "ratio"),
        "op_t0": str(getattr(plot, "fc_op_t0", "ratio") or "ratio"),
        "order": str(getattr(plot, "fc_order", "ctrl_first") or "ctrl_first"),
    }


def _current_leaf_tab(ctx) -> str:
    from well_viewer.core.tab_controller import current_centre_tab
    try:
        return str(current_centre_tab(ctx) or "")
    except Exception:
        return ""


def _snapshot_tabs(ctx) -> dict[str, dict]:
    """Capture per-tab combo state for tabs we know how to round-trip.

    Tab files aren't modified — we read from the widget references the
    build functions already stash on ``app``. Lazily-built tabs the user
    never visited this session don't expose those widgets, so we layer
    any leftover entries from ``_view_state_pending`` underneath the
    live snapshot — that's the state we restored at load time and never
    overwrote, so it's still authoritative.
    """
    tabs: dict[str, dict] = {}
    pending = ctx.runtime.view_state.pending or {}
    for name, slc in pending.items():
        if isinstance(slc, dict) and slc:
            tabs[str(name)] = dict(slc)
    bar = _snapshot_bar(ctx)
    if bar:
        tabs["Bar Plots"] = bar
    dist = _snapshot_distribution(ctx)
    if dist:
        tabs["Distribution"] = dist
    scat = _snapshot_scatter(ctx)
    if scat:
        tabs["Scatter Plot"] = scat
    # Feature-owned view-state (e.g. Image Table). Each feature declares a
    # ``view_state_io`` hook returning a ``(snapshot, apply)`` pair; we fan out
    # so this module never names the feature. A profiled-out / binary-stripped
    # feature isn't in the registry, so its block simply isn't captured — an
    # existing block from another build survives untouched in ``pending``.
    for d in ctx.features.with_hook("view_state_io"):
        try:
            snapshot, _apply = d.view_state_io()
            blk = snapshot(ctx)
        except Exception:
            _logger.debug("view_state: %s snapshot failed", d.name, exc_info=True)
            continue
        if blk:
            tabs[d.name] = blk
    return tabs


def _combo_text(ctx, attr: str) -> str | None:
    w = ctx.widgets.get_or_none(attr)
    if w is None:
        return None
    try:
        return str(w.currentText())
    except Exception:
        return None


def _snapshot_bar(ctx) -> dict:
    out: dict[str, Any] = {}
    tp = _combo_text(ctx, "_bar_tp_cb")
    if tp is not None:
        out["timepoint"] = tp
    sels = ctx.state.bar.tp_selections
    if isinstance(sels, dict) and sels:
        out["timepoint_selections"] = {k: bool(v) for k, v in sels.items()}
    _bar = ctx.state.feature_slice("bar")
    if _bar is not None:
        out["swarm"] = bool(_bar.swarm)
        out["violin"] = bool(_bar.violin)
    slider = ctx.widgets.get_or_none("_violin_slider")
    if slider is not None:
        try:
            out["violin_smooth"] = int(slider.value())
        except Exception:
            swallowed()  # was: pass (silent except)
    plot = getattr(ctx.state, "plot", None)
    if plot is not None:
        if hasattr(plot, "bar_group_mode"):
            out["group_mode"] = str(plot.bar_group_mode)
        if hasattr(plot, "bar_pool_replicates"):
            out["pool_replicates"] = bool(plot.bar_pool_replicates)
        if hasattr(plot, "bar_err_kind"):
            out["bar_err_kind"] = str(plot.bar_err_kind)
        if hasattr(plot, "bar_show_mode"):
            out["bar_show_mode"] = str(plot.bar_show_mode)
        if hasattr(plot, "bar_color_mode"):
            out["bar_color_mode"] = str(plot.bar_color_mode)
        # The user's drag-to-reorder bar layout. ``ordered_bar_keys``
        # falls back to the canonical order when the saved list doesn't
        # cover the current dataset's keys, so an empty / stale list is
        # benign.
        order = getattr(plot, "bar_order", None)
        if isinstance(order, list) and order:
            out["bar_order"] = [str(k) for k in order]
    return out


def _snapshot_distribution(ctx) -> dict:
    out: dict[str, Any] = {}
    tp = _combo_text(ctx, "_distribution_tp_cb")
    if tp is not None:
        out["timepoint"] = tp
    mode = _combo_text(ctx, "_distribution_mode_cb")
    if mode is not None:
        out["mode"] = mode
    layout = _combo_text(ctx, "_distribution_layout_cb")
    if layout is not None:
        out["layout"] = layout
    _dist = ctx.state.feature_slice("distribution")
    if _dist is not None:
        out["bins"] = int(_dist.bins)
        out["log_x"] = bool(_dist.log_x)
        out["log_y"] = bool(_dist.log_y)
    return out


def _snapshot_scatter(ctx) -> dict:
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
        v = _combo_text(ctx, attr)
        if v is not None:
            out[key] = v
    # Which sub-tab (per-cell vs aggregate) was active, plus the per-cell display
    # toggles ("Color by time" and "Show gates") — both shape the rendered scatter
    # and were previously not captured, so a restored scatter lost them.
    _sc = ctx.state.feature_slice("scatter")
    if _sc is not None:
        if _sc.mode:
            out["mode"] = str(_sc.mode)
        out["color_by_tp"] = bool(_sc.color_by_tp)
        out["gates_hidden"] = bool(_sc.gates_hidden)
        out["apply_gates_to_points"] = bool(_sc.apply_gates_to_points)
    # Per-timepoint multi-select checkboxes for each sub-tab (the dataset-wide
    # timepoint filter the user can toggle). Mirrors the Bar tab's
    # ``timepoint_selections`` round-trip.
    for key, sel in (
        ("tp_selections", ctx.runtime.scatter.tp_selections),
        ("agg_tp_selections", ctx.runtime.scatter.agg_tp_selections),
    ):
        if isinstance(sel, dict) and sel:
            out[key] = {str(k): bool(v) for k, v in sel.items()}
    return out


# ── restore ────────────────────────────────────────────────────────────────

def _apply_fold_change(ctx, fc) -> None:
    """Read the ``fold_change`` block back onto ``ctx.state.plot``.

    Every key is guarded, and every enum falls back to its default rather than
    raising: this runs inside :func:`restore`, where an exception costs the
    user the *entire* view state, so a block written by a different build
    should cost its own settings and nothing else.
    """
    if not isinstance(fc, dict) or not fc:
        return
    plot = ctx.state.plot
    if "vs_control_on" in fc:
        plot.fc_vs_control_on = bool(fc.get("vs_control_on"))
    # ``control`` wins when present. A document written before it existed
    # carries only ``control_label``, which is read as a legacy reference and
    # resolved by the precedence it was saved under; the next save writes the
    # concrete form, so the ambiguity is retired once per document rather than
    # living forever.
    if "control" in fc and fc.get("control") is not None:
        plot.fc_control = ControlRef.from_storage(fc.get("control"))
    elif "control_label" in fc:
        plot.fc_control = ControlRef.legacy(fc.get("control_label"))
    if "control_mode" in fc:
        mode_in = str(fc.get("control_mode") or "single")
        plot.fc_control_mode = (
            mode_in if mode_in in ("single", "per_member") else "single")
    if "control_render" in fc:
        render_in = str(fc.get("control_render") or "self")
        plot.fc_control_render = (
            render_in if render_in in ("self", "hidden") else "self")
    raw_map = fc.get("control_map")
    if isinstance(raw_map, dict):
        restored: dict = {}
        for key, raw_ref in raw_map.items():
            ref = ControlRef.from_storage(raw_ref)
            if ref is not None:
                restored[str(key)] = ref
        plot.fc_control_map = restored
    if "vs_t0_on" in fc:
        plot.fc_vs_t0_on = bool(fc.get("vs_t0_on"))
    if "baseline_tp" in fc:
        plot.fc_baseline_tp = str(fc.get("baseline_tp") or "")
    # Legacy single-op snapshot: fan out to both axes via the ``fc_op``
    # property setter so old saved state restores cleanly.
    if "op" in fc:
        op_in = str(fc.get("op") or "ratio")
        plot.fc_op = op_in if op_in in ("ratio", "subtract") else "ratio"
    if "op_ctrl" in fc:
        op_in = str(fc.get("op_ctrl") or "ratio")
        plot.fc_op_ctrl = op_in if op_in in ("ratio", "subtract") else "ratio"
    if "op_t0" in fc:
        op_in = str(fc.get("op_t0") or "ratio")
        plot.fc_op_t0 = op_in if op_in in ("ratio", "subtract") else "ratio"
    if "order" in fc:
        o_in = str(fc.get("order") or "ctrl_first")
        plot.fc_order = o_in if o_in in ("ctrl_first", "t0_first") else "ctrl_first"


def restore(ctx, state: dict, *, prefer_home: bool = False) -> list[str]:
    """Apply *state* in two phases — cross-cutting now, per-tab deferred.

    Per-tab combo values are stashed on ``ctx.runtime.view_state.pending`` and
    flushed by ``apply_pending_tab_state`` when each tab becomes active.

    *prefer_home* asks for the Home page instead of the snapshot's own
    ``active_centre_tab``. It is for the arrival of a dataset and nothing else;
    every other caller wants the tab the snapshot names, because it is
    re-applying a view the user asked to see rather than opening a folder.

    Returns the names of the settings that could **not** be restored, in the
    order they were attempted, and empty when everything applied.

    Every block here is applied independently and a failure in one must not
    cost the others: a saved view referring to a channel this dataset does not
    have should still bring back the wells, the draw order and the tab. But
    that means a restore can half-succeed, and it used to do so silently —
    fourteen handlers logged at DEBUG and the function returned ``None``, so a
    caller could not tell a complete restore from one that applied four blocks
    of fourteen. The user asked to see a particular figure, got a different one,
    and nothing said why. :func:`_report_restore_gaps` is the other half of
    that: the shortfall is stated on the status line and in the app log, not
    only in a debug breadcrumb.
    """
    failed: list[str] = []
    if not isinstance(state, dict):
        return failed

    # Cross-cutting state — apply eagerly.

    wells = state.get("selected_wells") or []
    if isinstance(wells, list):
        saved = {str(w) for w in wells if isinstance(w, str)}
        current = set(ctx.state.dataset.well_paths or {})
        valid = saved & current
        # X14: remember wells saved for datasets/plates not currently loaded so
        # ``snapshot`` can re-persist them — a subset load must not prune them.
        ctx.runtime.view_state.absent_wells = saved - current
        if valid:
            from well_viewer.core.selection_controller import set_selected_wells
            try:
                set_selected_wells(ctx, valid, commit=False)
            except Exception:
                failed.append("selected wells")
                _logger.debug("view_state: set_selected_wells failed", exc_info=True)

    # Rep-set visibility (group mode) — re-apply each selection's hidden flag and
    # the solo-well set so a plot saved in replicate mode restores the groups it
    # was showing. Matched by selection id; absent (older saved plots) → skipped.
    vis = state.get("selection_visibility")
    if isinstance(vis, dict):
        hidden_ids = {str(x) for x in (vis.get("hidden_ids") or [])}
        for sel in (ctx.state.selection.selections or []):
            if sel.get("id") is not None:
                sel["hidden"] = str(sel.get("id")) in hidden_ids
        solo = vis.get("solo_wells")
        if isinstance(solo, list):
            wp = set(ctx.state.dataset.well_paths or {})
            saved_solo = {str(w) for w in solo}
            # X14: preserve solo wells belonging to other datasets (see above).
            ctx.runtime.view_state.absent_solo = saved_solo - wp
            ctx.state.selection.active_solo_wells = saved_solo & wp
        try:
            ctx.coordinator.refresh_sidebar_map()
        except Exception:
            failed.append("group visibility")
            swallowed()

    if "use_sem" in state:
        try:
            ctx.state.plot.use_sem = bool(state["use_sem"])
        except Exception:
            failed.append("error-bar mode")
            swallowed()  # was: pass (silent except)

    if "show_threshold_line" in state:
        try:
            ctx.state.plot.show_threshold_line = bool(state["show_threshold_line"])
        except Exception:
            failed.append("threshold line")
            swallowed()

    if "show_significance" in state:
        try:
            ctx.state.plot.show_significance = bool(state["show_significance"])
        except Exception:
            failed.append("significance marks")
            swallowed()

    if "time_scale" in state:
        # Through ``coerce_factor`` rather than ``float()``: a hand-edited or
        # truncated file could hold a zero or a negative, which would collapse or
        # mirror every time axis. An unusable value restores the off state.
        try:
            from well_viewer.domain.time_rescale import coerce_factor
            ctx.state.plot.time_scale = coerce_factor(state["time_scale"])
        except Exception:
            failed.append("time scale")
            swallowed()

    if "time_offset" in state:
        # ``coerce_offset`` is the wider door of the two: a negative offset is
        # ordinary and zero is the off state, so only a non-number or a
        # non-finite value is refused. Restored under its own key so a file
        # written before the offset existed leaves it at zero rather than
        # inheriting whatever the previous plate had.
        try:
            from well_viewer.domain.time_rescale import coerce_offset
            ctx.state.plot.time_offset = coerce_offset(state["time_offset"])
        except Exception:
            failed.append("time offset")
            swallowed()

    lv = state.get("legend_visible")
    if isinstance(lv, dict):
        try:
            ctx.state.plot.legend_visible = {str(k): bool(v) for k, v in lv.items()}
        except Exception:
            failed.append("legend visibility")
            swallowed()

    for _key in ("line_order_rsets", "line_order_wells"):
        _val = state.get(_key)
        if isinstance(_val, list):
            try:
                setattr(ctx.state.plot, _key, [str(x) for x in _val])
            except Exception:
                failed.append("draw order")
                swallowed()

    if "focus_timepoint" in state:
        _ft = state.get("focus_timepoint")
        try:
            ctx.state.plot.focus_timepoint = None if _ft is None else float(_ft)
        except Exception:
            failed.append("focus timepoint")
            swallowed()

    if "use_fov_replicates" in state:
        # The FOV-spread toggle on Line Graphs / Bar Plots. Set the field, then
        # sync the toggle buttons; ``refresh_fov_btn_state`` is a no-op until
        # those widgets exist (lazy tabs), reading the field directly so a later
        # build still reflects the restored value.
        try:
            ctx.state.plot.use_fov_replicates = bool(state["use_fov_replicates"])
            from well_viewer.core.plot_toggles import refresh_fov_btn_state
            refresh_fov_btn_state(ctx)
        except Exception:
            failed.append("FOV replicates")
            swallowed()  # was: pass (silent except)

    _apply_fold_change(ctx, state.get("fold_change") or {})
    # Push the restored state into any fold-change combos that have already
    # been built. Lazily-built tabs (Bar) pick the values up on first install
    # via _repopulate_*_combo, which read the state directly — so this only
    # matters for combos that exist at restore time (e.g. Line, built eagerly).
    try:
        from well_viewer.core.fold_change_controls_view import _sync_widgets_to_state
        _sync_widgets_to_state(ctx)
    except Exception:
        failed.append("fold-change controls")
        _logger.debug("view_state: fold-change combo sync failed", exc_info=True)

    prefs_in = state.get("export_style_prefs")
    if isinstance(prefs_in, dict):
        # Axis-scoped keys are persisted per-figure (below); strip any that
        # linger in a pre-split saved global dict so they can't leak into every
        # tab (or the batch-export path) on load.
        from well_viewer.core.plot_style import (
            AXIS_SCOPED_PREF_KEYS,
            DEFAULT_EXPORT_STYLE_PREFS,
        )
        clean_in = {k: v for k, v in prefs_in.items() if k not in AXIS_SCOPED_PREF_KEYS}
        existing = ctx.state.export_prefs.style_prefs
        if not isinstance(existing, dict):
            # Seed the full default set before layering the saved (non-default)
            # diff on top. ``export_style_prefs`` is persisted as a diff only, so
            # a bare ``dict(clean_in)`` would leave the prefs missing default keys
            # (crashing the Properties sidebar's bracket reads on restore).
            existing = dict(DEFAULT_EXPORT_STYLE_PREFS)
            ctx.state.export_prefs.style_prefs = existing
        existing.update(clean_in)

    axis_in = state.get("export_axis_prefs")
    if isinstance(axis_in, dict):
        ctx.state.export_prefs.axis_prefs = {
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
            ctx.state.channel.active_channel = ch
            set_active_channel(ctx, ch)
        except Exception:
            failed.append("active channel")
            _logger.debug("view_state: active_channel restore failed", exc_info=True)

    metric = state.get("active_metric")
    if isinstance(metric, str) and metric:
        # Direct field assignment so tabs built lazily after this point
        # pick up the saved metric for their initial render.
        ctx.state.channel.active_metric = metric
        # Push the value into every metric combo widget that exists right
        # now (e.g. Plotting's band combo _plotting_metric_cb, built early
        # during centre-view construction). Without this the dropdown
        # reverts to its default first item ("Mean Intensity") on app
        # reopen because set_active_metric — which usually drives the
        # combo sync — early-returns when the field is already at the
        # target value. The user then has to "wiggle" the dropdown to
        # force the sync, which was particularly visible when smFISH
        # count was the last-selected metric.
        try:
            from well_viewer.core.channel_state_controller import sync_metric_combos
            sync_metric_combos(ctx, metric)
        except Exception:
            failed.append("metric")
            _logger.debug("view_state: metric combo sync failed", exc_info=True)

    # Re-derive the value column from the *restored* channel + metric. The
    # channel was restored above via set_active_channel, which derived
    # active_val_col from the metric as it stood *before* this metric restore;
    # the metric restore then field-set the metric (and synced combos) but left
    # active_val_col stale. Without this the post-load redraw plots the wrong
    # column until the user toggles the channel. sync_val_col skips ratio/shape.
    try:
        ctx.state.channel.sync_val_col()
    except Exception:
        failed.append("value column")
        _logger.debug("view_state: active_val_col re-derive failed", exc_info=True)

    # Stash per-tab state for deferred apply on first tab visit.
    tabs = state.get("tabs") or {}
    if isinstance(tabs, dict) and tabs:
        ctx.runtime.view_state.pending = {str(k): dict(v) for k, v in tabs.items() if isinstance(v, dict)}

    # Active tab is restored last so its on-change hook triggers any
    # lazy build with cross-cutting state already in place. Drives the
    # nested plotting stack when a plot sub-tab is requested.
    #
    # **Home wins over the saved tab when a dataset is arriving.** Opening a
    # dataset is the moment a user most needs to be told what they just opened —
    # whether its run finished, what is missing from it, what it was run with —
    # and landing instead on whatever page they happened to quit from puts a plot
    # of a plate they have not been oriented to in front of them. That applies to
    # the arrival of a dataset only, which is why it is gated on *prefer_home*: a
    # caller replaying a snapshot the user picked (Saved Plots' Restore) is
    # answering a request to see one particular figure, and sending that to Home
    # would leave the restored settings applied to a tab nobody is looking at.
    target = state.get("active_centre_tab")
    if prefer_home and _has_home(ctx):
        _activate_tab(ctx, "Home")
        _report_restore_gaps(ctx, failed)
        return failed
    if isinstance(target, str) and target:
        _activate_tab(ctx, target)
    # After the tab activation, not before: activating a tab triggers its
    # redraw, and those write their own text to the status line. Reporting
    # first would put this message on screen and then have it overwritten
    # before the user could read it.
    _report_restore_gaps(ctx, failed)
    return failed


def _report_restore_gaps(ctx, failed: list[str]) -> None:
    """Say on the status line and in the app log what did not come back.

    Silence here is the failure mode the whole change exists to remove, so
    this is deliberately not conditional on a debug flag: a restored view that
    differs from the saved one is a wrong answer presented as a right one, and
    the user is the only one who can tell whether the difference matters.
    """
    if not failed:
        return
    _logger.warning(
        "Restored the saved view with %d setting(s) missing: %s",
        len(failed), ", ".join(failed),
    )
    with swallow("view_state: reporting restore gaps"):
        ctx.notify.set_status(
            f"Restored with {len(failed)} setting(s) missing: "
            f"{', '.join(failed)}"
        )


def _has_home(ctx) -> bool:
    """Whether this build has a Home page to land on.

    A feature-subset profile can leave it out entirely, and there the saved tab
    is the best answer available rather than a page that does not exist.
    """
    notebook = getattr(ctx.window, "_notebook", None)
    names = getattr(notebook, "pageNames", None)
    return bool(callable(names) and "Home" in (names() or ()))


def _activate_tab(ctx, name: str) -> None:
    outer = getattr(ctx.window, "_notebook", None)
    if outer is None:
        return
    try:
        if name in _PLOT_SUBTABS:
            outer.setCurrentByName("Plotting")
            sub = getattr(ctx.window, "_plotting_notebook", None)
            if sub is not None:
                sub.setCurrentByName(name)
        elif name in _IMAGE_BROWSER_SUBTABS:
            outer.setCurrentByName("Image Browser")
            sub = ctx.runtime.image_browser.subtabs
            if sub is not None:
                for i in range(sub.count()):
                    if sub.tabText(i) == name:
                        sub.setCurrentIndex(i)
                        break
        else:
            outer.setCurrentByName(name)
    except Exception:
        _logger.debug("view_state: tab activation failed for %r", name, exc_info=True)


#: Tabs whose per-tab block this module snapshots itself. Every other block
#: comes from a feature's ``view_state_io`` hook, so the full set is these plus
#: whatever the registry currently carries — see :func:`owns_tab_block`.
_BUILTIN_TAB_BLOCKS = ("Bar Plots", "Distribution", "Scatter Plot")


def owns_tab_block(ctx, tab_name: str) -> bool:
    """Whether *tab_name* stores per-tab state of its own in a snapshot.

    Lets a caller tell "this saved view has no block for the tab because the tab
    keeps nothing" from "…because it was saved before the tab kept anything".
    The first is normal for most tabs — Line Graphs has no block and never had —
    and the second is a saved view that will silently restore less than the user
    expects, which is worth saying out loud.
    """
    if tab_name in _BUILTIN_TAB_BLOCKS:
        return True
    try:
        d = ctx.features.get(tab_name)
    except Exception:
        return False
    return bool(d is not None and d.view_state_io)


def apply_pending_tab_state(ctx, tab_name: str) -> bool:
    """Pop *tab_name*'s pending state (if any) into live widgets.

    Called from ``_on_tab_change`` so a tab that built lazily still receives
    its restored combo values the first time the user lands on it.

    Returns ``True`` when pending state for *tab_name* was found and applied, so
    the caller can redraw — the apply runs *after* the tab's initial draw, and
    some restored state (the Bar timepoint multi-select, swarm/violin, scatter
    axes) changes what should be plotted.
    """
    pending = ctx.runtime.view_state.pending
    if not pending:
        return False
    state = pending.pop(tab_name, None)
    if not state:
        return False
    try:
        if tab_name == "Bar Plots":
            _apply_bar(ctx, state)
        elif tab_name == "Distribution":
            _apply_distribution(ctx, state)
        elif tab_name == "Scatter Plot":
            _apply_scatter(ctx, state)
        else:
            # Feature-owned view-state (e.g. Image Table). Resolve the feature's
            # ``view_state_io`` apply half via the registry so this module never
            # names it; a profiled-out tab isn't in the registry, so its pending
            # block is dropped here (left in ``pending`` for another build).
            d = ctx.features.get(tab_name)
            io = d.view_state_io() if (d is not None and d.view_state_io) else None
            if io is not None:
                io[1](ctx, state)
    except Exception:
        _logger.debug("view_state: apply_pending_tab_state(%r) failed", tab_name, exc_info=True)
    return True


def _set_combo_if_valid(ctx, attr: str, value: str) -> None:
    w = ctx.widgets.get_or_none(attr)
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


def _apply_bar(ctx, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(ctx, "_bar_tp_cb", str(state["timepoint"]))
    if "timepoint_selections" in state and isinstance(state["timepoint_selections"], dict):
        try:
            # Intersect with the dataset's current timepoint keys so a saved
            # selection from a slightly differently-formatted run doesn't leave
            # orphan keys that the popup ignores. ``update_bar_tp_menu`` keys
            # via ``f"{t:.4g}"``; we keep whatever is in the saved file as the
            # authoritative selection unless the key is no longer present.
            current = dict(ctx.state.bar.tp_selections or {})
            saved = {str(k): bool(v) for k, v in state["timepoint_selections"].items()}
            merged = {k: (saved[k] if k in saved else False) for k in current}
            # When the saved set covers every current timepoint (common when
            # nothing's changed between runs), use it verbatim.
            if set(saved.keys()) >= set(current.keys()):
                merged = {k: saved.get(k, False) for k in current}
            ctx.state.bar.tp_selections = merged or saved
        except Exception:
            swallowed()  # was: pass (silent except)
        # Sync the hidden back-compat combo + the picker button's "Timepoints: …
        # selected" readout so every downstream reader (export, status text, the
        # popup itself) reflects the restored selection. Without this the popup
        # defaults checkboxes from a stale ``tp_selections`` snapshot taken
        # before the restore.
        try:
            sels = ctx.state.bar.tp_selections or {}
            hidden = ctx.widgets.get_or_none("_bar_tp_cb")
            if hidden is not None and sels:
                for tp_str in sorted(sels.keys(), key=lambda s: float(s)):
                    if sels[tp_str]:
                        hidden.setCurrentText(tp_str)
                        break
            btn = ctx.widgets.get_or_none("_bar_tp_button")
            if btn is not None:
                btn.setText(multiselect_button_text("Timepoints", sels))
        except Exception:
            _logger.debug("view_state: bar tp post-restore sync failed", exc_info=True)
    _bar = ctx.state.feature_slice("bar")
    if _bar is not None:
        if "swarm" in state:
            _bar.swarm = bool(state["swarm"])
        if "violin" in state:
            _bar.violin = bool(state["violin"])
    slider = ctx.widgets.get_or_none("_violin_slider")
    if slider is not None and "violin_smooth" in state:
        try:
            slider.setValue(int(state["violin_smooth"]))
        except Exception:
            swallowed()  # was: pass (silent except)
    plot = getattr(ctx.state, "plot", None)
    if plot is not None:
        if "group_mode" in state and str(state["group_mode"]) in ("well", "timepoint"):
            plot.bar_group_mode = str(state["group_mode"])
        if "pool_replicates" in state:
            plot.bar_pool_replicates = bool(state["pool_replicates"])
        if str(state.get("bar_err_kind")) in ("none", "sem", "sd", "ci95"):
            plot.bar_err_kind = str(state["bar_err_kind"])
        if str(state.get("bar_show_mode")) in ("mean", "mean_spread", "all_pts"):
            plot.bar_show_mode = str(state["bar_show_mode"])
        if str(state.get("bar_color_mode")) in BAR_COLOR_MODES:
            plot.bar_color_mode = str(state["bar_color_mode"])
        if "bar_order" in state and isinstance(state["bar_order"], list):
            plot.bar_order = [str(k) for k in state["bar_order"]]


def _apply_distribution(ctx, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(ctx, "_distribution_tp_cb", str(state["timepoint"]))
    if "mode" in state:
        _set_combo_if_valid(ctx, "_distribution_mode_cb", str(state["mode"]))
    if "layout" in state:
        _set_combo_if_valid(ctx, "_distribution_layout_cb", str(state["layout"]))
    _dist = ctx.state.feature_slice("distribution")
    if "bins" in state:
        try:
            bins = int(state["bins"])
        except (TypeError, ValueError):
            return
        spin = ctx.widgets.get_or_none("_distribution_bins_spin")
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
        cb = ctx.widgets.get_or_none("_distribution_log_x_cb")
        if cb is not None:
            try:
                cb.setChecked(bool(state["log_x"]))
            except Exception:
                swallowed()  # was: pass (silent except)
    if "log_y" in state:
        if _dist is not None:
            _dist.log_y = bool(state["log_y"])
        cb = ctx.widgets.get_or_none("_distribution_log_y_cb")
        if cb is not None:
            try:
                cb.setChecked(bool(state["log_y"]))
            except Exception:
                swallowed()  # was: pass (silent except)


def _apply_scatter(ctx, state: dict) -> None:
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
            _set_combo_if_valid(ctx, attr, str(state[key]))
    # Restore the per-timepoint multi-select filters, intersected with the
    # timepoints the current dataset actually has (a saved tp may be gone).
    for skey, live in (
        ("tp_selections", ctx.runtime.scatter.tp_selections),
        ("agg_tp_selections", ctx.runtime.scatter.agg_tp_selections),
    ):
        saved = state.get(skey)
        if isinstance(saved, dict) and isinstance(live, dict):
            for tp_str in live:
                if tp_str in saved:
                    live[tp_str] = bool(saved[tp_str])
    # Refresh the timepoint-selector summary labels so the restored filter is
    # reflected in the UI, then re-apply the active sub-tab last (it repopulates
    # the axis combos but preserves the values we just set, and redraws). The two
    # display refreshers are ctx-native functions the scatter cells/agg builders
    # stash on ``ctx.runtime.scatter`` (like ``set_mode``/``gate_redraw_cb``), so
    # persistence reaches them without importing the feature module.
    for ref in (ctx.runtime.scatter.tp_display_refresh,
                ctx.runtime.scatter.agg_tp_display_refresh):
        if callable(ref):
            try:
                ref(ctx)
            except Exception:
                swallowed()
    # Per-cell display toggles. Set the typed state (the source of truth every
    # renderer reads) and sync the "Color by time" button label/checked when it
    # exists; the "Show gates" sidebar toggle reads gates_hidden off state when it
    # is (re)built, so no widget sync is needed there.
    _sc = ctx.state.feature_slice("scatter")
    if _sc is not None:
        if "color_by_tp" in state:
            _sc.color_by_tp = bool(state["color_by_tp"])
            btn = ctx.widgets.get_or_none("_scatter_color_by_tp_btn")
            if btn is not None:
                try:
                    btn.setChecked(_sc.color_by_tp)
                    btn.setText(
                        "Color by time: On" if _sc.color_by_tp else "Color by time: Off")
                except Exception:
                    swallowed()
        if "gates_hidden" in state:
            _sc.gates_hidden = bool(state["gates_hidden"])
        if "apply_gates_to_points" in state:
            _sc.apply_gates_to_points = bool(state["apply_gates_to_points"])
            btn = ctx.widgets.get_or_none("_scatter_apply_gates_btn")
            if btn is not None:
                try:
                    btn.setChecked(_sc.apply_gates_to_points)
                    btn.setText(
                        "Apply gates: On" if _sc.apply_gates_to_points
                        else "Apply gates: Off")
                except Exception:
                    swallowed()
    mode = state.get("mode")
    set_mode = ctx.runtime.scatter.set_mode
    if mode and callable(set_mode):
        try:
            set_mode(str(mode))
        except Exception:
            swallowed()


# ── save / load entry points ───────────────────────────────────────────────

def save_to_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    try:
        state = snapshot(ctx)
    except Exception:
        _logger.exception("view_state: snapshot failed; not saving")
        return
    _doc.set_section(ctx, "view_state", state)


def forget_absent_wells(ctx) -> None:
    """Drop the cross-plate "absent" stashes (X14).

    ``restore`` remembers the wells a saved view named that the plate in front
    of it does not hold, so :func:`snapshot` can union them back and a subset
    load does not prune them. Those two sets describe *one particular
    persistence.json*, and they are the only piece of view state that is
    written by ``restore`` and read by ``snapshot`` without either being keyed
    to a data directory — so carrying them into another dataset unions one
    plate's wells into another plate's saved selection.

    ``restore`` reassigns both, but it only runs when the new plate has a
    ``view_state`` section; when it has none the assignment never happens and
    the previous plate's wells survive. Hence a clear that runs before that
    decision rather than inside it.
    """
    ctx.runtime.view_state.absent_wells = set()
    ctx.runtime.view_state.absent_solo = set()


def load_from_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    # Before the section is read, and therefore before every path that can
    # return without reaching ``restore``: a plate with no saved view, a doc
    # that cannot be migrated, a section that is not a dict. Each of those
    # left the previous plate's absent wells in place to be re-saved here.
    forget_absent_wells(ctx)
    state = _doc.get_section(ctx, "view_state")
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
        # The dataset-arrival path, and the only one that lands on Home.
        restore(ctx, state, prefer_home=True)
    except Exception:
        _logger.exception("view_state: restore failed; continuing with defaults")


def reset(ctx) -> None:
    """Return every field :func:`snapshot` captures to its default, in place.

    Caller is responsible for confirming with the user — this is destructive.
    Dataset-level state (loaded wells, channels, gating, ratios) is left alone;
    only the user's *view* resets.

    Two properties this has to hold, both of which it previously did not:

    **What resets and what is dropped from disk are the same set.** The old
    version cleared selected wells, the fold-change scopes and three tabs'
    combos, then deleted the whole ``view_state`` section — which also carried
    the time-axis scale, legend visibility, draw order, focus timepoint, error-
    band mode and export preferences. Those stayed on screen and vanished from
    disk, so the app showed one thing and the next open showed another, with
    nothing to indicate a reset had half-happened.

    **The section is re-saved, not deleted.** Writing the post-reset snapshot
    makes screen and disk agree by construction, so a field added to
    ``PlotState`` later cannot reintroduce the divergence: it is reset by
    ``restore_defaults`` and then written in the same state it is displayed in.

    ``active_channel`` / ``active_metric`` are deliberately kept. They are
    derived from the loaded dataset rather than chosen as a preference, and the
    declared default ("") is not a state the app can draw.
    """
    from well_viewer.domain.state_defaults import restore_defaults

    app = ctx.window  # transitional bridge (B4)
    # Selected wells, plus the wells stashed for plates that are not currently
    # loaded — snapshot() unions those back in, so leaving them would re-save a
    # selection the reset just cleared.
    if hasattr(app, "_set_selected_wells"):
        try:
            app._set_selected_wells(set(), commit=False)
        except Exception:
            _logger.debug("reset: clearing selected wells failed", exc_info=True)
    ctx.runtime.view_state.absent_wells = set()
    ctx.runtime.view_state.absent_solo = set()

    # Every cross-tab plot option and fold-change field in one move, including
    # ones added after this function was written.
    restore_defaults(ctx.state.plot)

    # Group visibility + solo are selection state the view owns: a reset shows
    # every group again rather than leaving rows hidden with no visible cause.
    sel = ctx.state.selection
    for s in sel.selections or []:
        s["hidden"] = False
    sel.active_solo_wells = set()

    # Publication-style and per-figure axis preferences.
    restore_defaults(ctx.state.export_prefs)

    try:
        from well_viewer.core.fold_change_controls_view import _sync_widgets_to_state
        _sync_widgets_to_state(ctx)
    except Exception:
        _logger.debug("reset: fold-change sync failed", exc_info=True)
    # Per-tab combo defaults.
    _reset_bar(app)
    _reset_distribution(app)
    _reset_scatter(app)
    # Drop any pending state stashed from a previous restore.
    ctx.runtime.view_state.pending = None
    # Persist the reset view rather than deleting the section, so the saved copy
    # and the screen cannot disagree about what was reset.
    if app._app_state.dataset.data_dir:
        try:
            save_to_data_dir(ctx)
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
