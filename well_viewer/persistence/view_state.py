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
from well_viewer.features.image_table.controller import image_table_apply_dimensions
from well_viewer.core.channel_state_controller import set_active_channel

_logger = logging.getLogger("well_viewer")

# Plot sub-tab names that live inside the "Plotting" parent stack.
_PLOT_SUBTABS = (
    "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
)


# ── snapshot ───────────────────────────────────────────────────────────────

def snapshot(app) -> dict:
    """Build a JSON-serialisable view-state dict from current app state."""
    return {
        "schema_version": 1,
        "selected_wells": sorted(app._app_state.selection.selected_wells or []),
        "active_centre_tab": _current_leaf_tab(app),
        "active_channel": str(app._app_state.channel.active_channel or ""),
        "active_metric": str(app._app_state.channel.active_metric or ""),
        "use_sem": bool(app._app_state.plot.use_sem),
        "fold_change": {
            "vs_control_on": bool(app._app_state.plot.fc_vs_control_on),
            "control_label": str(app._app_state.plot.fc_control_label or ""),
            "vs_t0_on": bool(app._app_state.plot.fc_vs_t0_on),
        },
        "export_style_prefs": dict(getattr(app, "_export_style_prefs", {}) or {}),
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
    sels = getattr(app, "_bar_tp_selections", None)
    if isinstance(sels, dict) and sels:
        out["timepoint_selections"] = {k: bool(v) for k, v in sels.items()}
    if hasattr(app, "_bar_swarm"):
        out["swarm"] = bool(app._bar_swarm)
    if hasattr(app, "_bar_violin"):
        out["violin"] = bool(app._bar_violin)
    slider = getattr(app, "_violin_slider", None)
    if slider is not None:
        try:
            out["violin_smooth"] = int(slider.value())
        except Exception:
            pass
    plot = getattr(app._app_state, "plot", None) if hasattr(app, "_app_state") else None
    if plot is not None:
        if hasattr(plot, "bar_group_mode"):
            out["group_mode"] = str(plot.bar_group_mode)
        if hasattr(plot, "bar_pool_replicates"):
            out["pool_replicates"] = bool(plot.bar_pool_replicates)
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
    if hasattr(app, "_distribution_bins"):
        out["bins"] = int(app._distribution_bins)
    if hasattr(app, "_distribution_log_x"):
        out["log_x"] = bool(app._distribution_log_x)
    return out


def _snapshot_scatter(app) -> dict:
    out: dict[str, Any] = {}
    for key, attr in (
        ("ch_x", "_scatter_ch_x_cb"),
        ("metric_x", "_scatter_metric_x_cb"),
        ("ch_y", "_scatter_ch_y_cb"),
        ("metric_y", "_scatter_metric_y_cb"),
        ("timepoint", "_scatter_tp_cb"),
    ):
        v = _combo_text(app, attr)
        if v is not None:
            out[key] = v
    return out


def _snapshot_image_table(app) -> dict:
    """Capture the Image Table grid: dimensions, per-cell well/chan/tp/fov,
    overlay toggles, and per-channel LUT min/max edits.
    """
    cells = getattr(app, "_image_table_cells", None)
    if not cells:
        return {}
    out: dict[str, Any] = {
        "rows": int(getattr(app, "_image_table_rows", len(cells))),
        "cols": int(getattr(app, "_image_table_cols",
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
                        pass
            row_out.append(slc)
        grid.append(row_out)
    out["cells"] = grid
    for key, attr in (
        ("global_chan", "_image_table_global_chan_cb"),
        ("global_tp",   "_image_table_global_tp_cb"),
        ("global_fov",  "_image_table_global_fov_cb"),
    ):
        v = _combo_text(app, attr)
        if v is not None:
            out[key] = v
    toggles = {
        "tophat": bool(getattr(app, "_image_table_use_tophat", False)),
        "boundaries": bool(getattr(app, "_image_table_show_boundaries", False)),
        "binary": bool(getattr(app, "_image_table_show_binary", False)),
    }
    out["toggles"] = toggles
    luts = getattr(app, "_image_table_lut", None) or {}
    chan_luts: dict[str, dict[str, str]] = {}
    if isinstance(luts, dict):
        for ch, edits in luts.items():
            if not isinstance(edits, dict):
                continue
            slot: dict[str, str] = {}
            mn = edits.get("min")
            mx = edits.get("max")
            try:
                if mn is not None:
                    slot["min"] = str(mn.text())
                if mx is not None:
                    slot["max"] = str(mx.text())
            except Exception:
                continue
            if slot:
                chan_luts[str(ch)] = slot
    if chan_luts:
        out["channel_luts"] = chan_luts
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
        valid = {str(w) for w in wells if isinstance(w, str)}
        valid &= set(app._app_state.dataset.well_paths or {})
        if valid and hasattr(app, "_set_selected_wells"):
            try:
                app._set_selected_wells(valid, commit=False)
            except Exception:
                _logger.debug("view_state: _set_selected_wells failed", exc_info=True)

    if "use_sem" in state:
        try:
            app._app_state.plot.use_sem = bool(state["use_sem"])
        except Exception:
            pass

    fc = state.get("fold_change") or {}
    if isinstance(fc, dict):
        if "vs_control_on" in fc:
            app._app_state.plot.fc_vs_control_on = bool(fc.get("vs_control_on"))
        if "control_label" in fc:
            app._app_state.plot.fc_control_label = str(fc.get("control_label") or "")
        if "vs_t0_on" in fc:
            app._app_state.plot.fc_vs_t0_on = bool(fc.get("vs_t0_on"))
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
        existing = getattr(app, "_export_style_prefs", None)
        if isinstance(existing, dict):
            existing.update(prefs_in)
        else:
            app._export_style_prefs = dict(prefs_in)

    # Active channel — validated against the canonical list. Fall through
    # silently when the saved channel no longer exists in the new dataset.
    ch = state.get("active_channel")
    fluor = list(app._app_state.channel.fluor_channels or [])
    if (
        isinstance(ch, str) and ch
        and ch in fluor
        and hasattr(app, "_set_active_channel")
    ):
        try:
            set_active_channel(app, ch)
        except Exception:
            _logger.debug("view_state: _set_active_channel failed", exc_info=True)

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
        else:
            outer.setCurrentByName(name)
    except Exception:
        _logger.debug("view_state: tab activation failed for %r", name, exc_info=True)


def apply_pending_tab_state(app, tab_name: str) -> None:
    """Pop *tab_name*'s pending state (if any) into live widgets.

    Called from ``_on_tab_change`` so a tab that built lazily still receives
    its restored combo values the first time the user lands on it.
    """
    pending = getattr(app, "_view_state_pending", None)
    if not pending:
        return
    state = pending.pop(tab_name, None)
    if not state:
        return
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


def _set_combo_if_valid(app, attr: str, value: str) -> None:
    w = getattr(app, attr, None)
    if w is None:
        return
    try:
        items = [w.itemText(i) for i in range(w.count())]
        if value in items:
            w.setCurrentText(value)
    except Exception:
        pass


def _apply_bar(app, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(app, "_bar_tp_cb", str(state["timepoint"]))
    if "timepoint_selections" in state and isinstance(state["timepoint_selections"], dict):
        try:
            app._bar_tp_selections = {
                str(k): bool(v) for k, v in state["timepoint_selections"].items()
            }
        except Exception:
            pass
    if "swarm" in state:
        app._bar_swarm = bool(state["swarm"])
    if "violin" in state:
        app._bar_violin = bool(state["violin"])
    slider = getattr(app, "_violin_slider", None)
    if slider is not None and "violin_smooth" in state:
        try:
            slider.setValue(int(state["violin_smooth"]))
        except Exception:
            pass
    plot = getattr(app._app_state, "plot", None) if hasattr(app, "_app_state") else None
    if plot is not None:
        if "group_mode" in state and str(state["group_mode"]) in ("well", "timepoint"):
            plot.bar_group_mode = str(state["group_mode"])
        if "pool_replicates" in state:
            plot.bar_pool_replicates = bool(state["pool_replicates"])


def _apply_distribution(app, state: dict) -> None:
    if "timepoint" in state:
        _set_combo_if_valid(app, "_distribution_tp_cb", str(state["timepoint"]))
    if "mode" in state:
        _set_combo_if_valid(app, "_distribution_mode_cb", str(state["mode"]))
    if "layout" in state:
        _set_combo_if_valid(app, "_distribution_layout_cb", str(state["layout"]))
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
                pass
        app._distribution_bins = bins
    if "log_x" in state:
        app._distribution_log_x = bool(state["log_x"])
        cb = getattr(app, "_distribution_log_x_cb", None)
        if cb is not None:
            try:
                cb.setChecked(bool(state["log_x"]))
            except Exception:
                pass


def _apply_scatter(app, state: dict) -> None:
    for key, attr in (
        ("ch_x", "_scatter_ch_x_cb"),
        ("metric_x", "_scatter_metric_x_cb"),
        ("ch_y", "_scatter_ch_y_cb"),
        ("metric_y", "_scatter_metric_y_cb"),
        ("timepoint", "_scatter_tp_cb"),
    ):
        if key in state:
            _set_combo_if_valid(app, attr, str(state[key]))


def _apply_image_table(app, state: dict) -> None:
    """Restore the Image Table grid (dimensions, per-cell combos, toggles,
    per-channel LUTs) and re-render via image_table_generate.

    The tab builder constructs widgets lazily; this only runs once the tab
    is on screen, so the widget references are guaranteed to exist.
    """
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
                pass
        if changed and hasattr(app, "_image_table_apply_dimensions"):
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
                        pass

    for key, attr in (
        ("global_chan", "_image_table_global_chan_cb"),
        ("global_tp",   "_image_table_global_tp_cb"),
        ("global_fov",  "_image_table_global_fov_cb"),
    ):
        if key in state and isinstance(state[key], str):
            _set_combo_if_valid(app, attr, state[key])

    toggles = state.get("toggles") or {}
    if isinstance(toggles, dict):
        for key, attr, btn in (
            ("tophat",     "_image_table_use_tophat",       "_image_table_tophat_btn"),
            ("boundaries", "_image_table_show_boundaries",  "_image_table_boundaries_btn"),
            ("binary",     "_image_table_show_binary",      "_image_table_binary_btn"),
        ):
            if key not in toggles:
                continue
            try:
                v = bool(toggles[key])
            except Exception:
                continue
            setattr(app, attr, v)
            b = getattr(app, btn, None)
            if b is not None and hasattr(b, "setChecked"):
                blocked = b.blockSignals(True)
                try:
                    b.setChecked(v)
                finally:
                    b.blockSignals(blocked)

    chan_luts = state.get("channel_luts") or {}
    live_luts = getattr(app, "_image_table_lut", None) or {}
    if isinstance(chan_luts, dict) and isinstance(live_luts, dict):
        for ch, slot in chan_luts.items():
            edits = live_luts.get(ch)
            if not isinstance(edits, dict) or not isinstance(slot, dict):
                continue
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
                    pass

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
    if sv is not None and sv != 1:
        _logger.warning("view_state schema_version=%r; ignoring saved state.", sv)
        return
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
    for attr, default in (
        ("_bar_swarm", False),
        ("_bar_violin", False),
    ):
        if hasattr(app, attr):
            setattr(app, attr, default)
    slider = getattr(app, "_violin_slider", None)
    if slider is not None:
        try:
            slider.setValue(100)
        except Exception:
            pass


def _reset_distribution(app) -> None:
    mode = getattr(app, "_distribution_mode_cb", None)
    if mode is not None:
        try:
            mode.setCurrentText("Histogram + KDE")
        except Exception:
            pass
    layout = getattr(app, "_distribution_layout_cb", None)
    if layout is not None:
        try:
            layout.setCurrentText("Overlay")
        except Exception:
            pass
    spin = getattr(app, "_distribution_bins_spin", None)
    if spin is not None:
        try:
            spin.setValue(40)
        except Exception:
            pass
    app._distribution_bins = 40
    app._distribution_log_x = False
    cb = getattr(app, "_distribution_log_x_cb", None)
    if cb is not None:
        try:
            cb.setChecked(False)
        except Exception:
            pass


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
            pass
