"""Channel-state controller.

Hoisted out of `well_viewer.runtime_app` so the dropdown / threshold /
metric-combo machinery stops growing on the god-object. The four
methods these functions replace were ~360 lines combined and owned
most of the "dropdown disagrees with the plot" bug surface (audit
findings H7, H10, M7, L9).

Convention follows the architecture document (§5.5): each function
takes `app` (the `WellViewerApp` instance) as its first positional
argument; runtime_app keeps thin shim methods that just delegate
here. No `runtime_app` import inside this module — every collaborator
is reachable via dynamic attributes on `app`.
"""

from __future__ import annotations

import logging
import math  # noqa: F401 — keeps parity with the original method bodies
from typing import List

import numpy as np
import pandas as pd

from well_viewer.core import debug_flags as _debug_flags
from well_viewer.data.data_loading import (
    _all_fluor_values,
    detect_fluor_channels,
    detect_nuclear_channel_token,
    detect_review_image_channels,
    detect_shape_features,
    detect_smfish_channels,
    merge_fluor_channels,
    normalize_channel_tokens,
)
from well_viewer.domain.ratio_models import build_ratio_index, is_ratio_key, ratio_name_from_key
from well_viewer.core.ui_helpers import set_combo_values, set_combo_values_sectioned
from pipeline.shape_features import is_shape_column, label_for as shape_label_for
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


# ── Threshold / channel-list recompute ──────────────────────────────────────


def recalculate_threshold(app) -> None:
    """Re-detect channels from the loaded CSVs, sync combos, and rebuild
    the (lo, hi) intensity range used by the threshold sliders.

    Replaces ``partial(recalculate_threshold, WellViewerApp)``.
    """
    sample_df = next(
        (app._get_rows(lbl) for lbl in app._app_state.dataset.well_paths),
        pd.DataFrame(),
    )
    detected = detect_fluor_channels(sample_df)
    detected_smfish = detect_smfish_channels(sample_df)
    pipeline_fluor_raw = [
        str(tok).strip().lower()
        for tok in (
            app._app_state.dataset.pipeline_info.get("fluor_tokens", [])
            if isinstance(app._app_state.dataset.pipeline_info, dict) else []
        )
        if str(tok).strip()
    ]
    pipeline_fluor = normalize_channel_tokens(pipeline_fluor_raw)
    detected = normalize_channel_tokens(detected)
    seg_tok = ""
    if isinstance(app._app_state.dataset.pipeline_info, dict):
        seg_tok = str(app._app_state.dataset.pipeline_info.get("nuclear_token", "") or "").strip().lower()
    if not seg_tok:
        seg_tok = detect_nuclear_channel_token(sample_df)
    fluor_channels = merge_fluor_channels(pipeline_fluor, detected, seg_tok)
    if fluor_channels:
        app._app_state.channel.fluor_channels = fluor_channels
        if not app._app_state.channel.active_channel:
            app._app_state.channel.active_channel = fluor_channels[0]
        if not app._app_state.channel.active_image_channel:
            app._app_state.channel.active_image_channel = fluor_channels[0]
    app._seg_channel_token = seg_tok
    app._app_state.channel.review_image_channels = detect_review_image_channels(
        sample_df, app._app_state.channel.fluor_channels, seg_tok,
    )
    # Populate the available shape-feature columns before the dropdown is built
    # so they appear in the "Shape Features" section.
    app._app_state.channel.shape_features = detect_shape_features(sample_df)
    update_channel_selector(app)

    # Update smFISH channels, then let ChannelState re-assert its invariants:
    # drop a stale smfish_count metric the new channel can't support, and
    # re-derive the value column (a no-op while a ratio is active).
    channel_state = app._app_state.channel
    channel_state.smfish_channels = set(normalize_channel_tokens(detected_smfish))
    channel_state.normalize_metric()
    channel_state.sync_val_col()

    # Update metric selector visibility (back-compat: per-tab frames).
    for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
        if hasattr(app, frame_attr):
            getattr(app, frame_attr).hide()

    # Always refresh timepoint menus regardless of whether intensity
    # values exist — single-timepoint experiments still need the bar menu.
    if hasattr(app, "_bar_tp_cb"):
        app._update_bar_tp_menu()
    if hasattr(app, "_stats_tp_cb"):
        from well_viewer.features.stats.controller import stats_update_tp_menu
        stats_update_tp_menu(app)
    if hasattr(app, "_distribution_tp_cb"):
        try:
            from well_viewer.features.distribution.tab_view import refresh_distribution_timepoints
            refresh_distribution_timepoints(app)
        except Exception:
            _logger.exception("Distribution timepoint refresh failed")
    if hasattr(app, "_heatmap_tp_slider"):
        try:
            from well_viewer.features.heatmap.tab_view import refresh_heatmap_timepoints
            refresh_heatmap_timepoints(app)
        except Exception:
            _logger.exception("Heatmap timepoint refresh failed")
    if hasattr(app, "_scatter_ch_x_cb"):
        try:
            from well_viewer.features.scatter.controller import (
                update_scatter_menus,
                redraw_scatter_orchestrate,
                redraw_scatter_agg_orchestrate,
            )
            update_scatter_menus(app)
            redraw_scatter_orchestrate(app)
            redraw_scatter_agg_orchestrate(app)
        except Exception:
            _logger.exception("Scatter menu refresh failed")

    # Per-channel (min, max) cache — keyed on val_col so a channel toggle
    # reuses the previous channel's range when the user flips back. Audit
    # M7: the unconditional concat across every loaded well × every cell
    # was the hot loop on channel switch (~3.5M float scans for 96 wells
    # × 12 tp × 3k cells). The cache lives on ChannelState and is invalidated
    # by `load_directory` on dataset swap.
    val_col = channel_state.active_val_col
    cached = channel_state.cached_range(val_col)
    if cached is None:
        chunks = [
            _all_fluor_values(app._get_rows(lbl), val_col=val_col)
            for lbl in app._app_state.dataset.well_paths
        ]
        all_vals = np.concatenate(chunks) if chunks else np.empty(0)
        if all_vals.size == 0:
            return
        lo, hi = float(all_vals.min()), float(all_vals.max())
        if hi <= lo:
            hi = lo + 1.0
        channel_state.cache_range(val_col, lo, hi)
    else:
        lo, hi = cached
    channel_state.apply_range(lo, hi)

    # Hydrate persisted gating params from pipeline_info.json.
    app._load_gating_from_pipeline_info()
    if hasattr(app, "_cell_gating_fluor_gate_edits"):
        from well_viewer.features.grouping.cell_gating_tab_view import (
            cell_gating_load_cell_areas,
            cell_gating_load_threshold_frac_on,
        )
        cell_gating_load_cell_areas(app)
        # cell_gating_load_cell_areas rebuilds the per-channel fluor-gate rows
        # for the freshly-detected channels as fresh (default 0.0) edits, which
        # discards the values the load above just wrote. Re-apply the persisted
        # gating params onto the rebuilt rows — otherwise a cold open shows the
        # saved gates/thresholds but never applies them until the user
        # re-toggles a field. (The saved value is always current — every gate
        # edit persists immediately — so re-applying never clobbers a live edit.)
        app._load_gating_from_pipeline_info()
        cell_gating_load_threshold_frac_on(app)


# ── Active channel switching ────────────────────────────────────────────────


def set_active_channel(app, channel: str) -> None:
    """Switch the active fluorescent channel and trigger a scope-aware redraw.

    ``channel`` may be a real channel token (e.g. ``"gfp"``) or a ratio
    key (``"ratio:<name>"``). Ratios bypass the per-channel metric
    selector and route reads through ``resolve_value``.

    Replaces ``partial(set_active_channel, WellViewerApp)``.
    """
    if not channel or channel == "—":
        return
    # ChannelState owns the channel/metric/val_col invariant — the mutators
    # below perform the pure state transition (and report whether anything
    # changed); this controller keeps the widget-sync that must stay in Qt.
    channel_state = app._app_state.channel
    if is_ratio_key(channel):
        ratio = channel_state.resolve_ratio(channel)
        if ratio is None:
            return
        if not channel_state.set_active_ratio(ratio):
            return
        # Hide the per-cell metric selector — ratios encode their own metrics.
        for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
            frame = getattr(app, frame_attr, None)
            if frame is not None:
                frame.setVisible(False)
    elif is_shape_column(channel):
        # Shape features are a single per-cell value (like ratios) — the column
        # name is the value column, and the per-cell metric selector doesn't apply.
        if not channel_state.set_active_shape_feature(channel):
            return
        for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
            frame = getattr(app, frame_attr, None)
            if frame is not None:
                frame.setVisible(False)
    else:
        # set_active_channel resets a stale ratio / smFISH metric so the new
        # channel composes a real column, and re-derives the value column.
        if not channel_state.set_active_channel(channel):
            return
        # Sync every metric combo to the (possibly reset) active metric so a
        # freshly-visible non-ratio channel shows the right Property label.
        from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
        label = METRIC_KEY_TO_LABEL.get(channel_state.active_metric, "Mean Intensity")
        for cb_attr in ("_metric_var", "_metric_cb", "_metric_cb_bar",
                        "_plotting_metric_cb"):
            cb = getattr(app, cb_attr, None)
            if cb is None:
                continue
            idx = cb.findText(label)
            if idx < 0:
                continue
            blocked = cb.blockSignals(True)
            try:
                cb.setCurrentIndex(idx)
            finally:
                cb.blockSignals(blocked)
    # Keep all plot-tab channel selectors in sync so switching channel on
    # one tab is reflected on the others.
    target_label = app._active_channel_label()
    for attr in ("_chan_cb_line", "_chan_cb_bar", "_chan_cb_distribution",
                 "_chan_cb_heatmap", "_plotting_channel_cb"):
        cb = getattr(app, attr, None)
        if cb is None:
            continue
        if str(cb.currentText() or "") == target_label:
            continue
        idx = cb.findText(target_label)
        if idx >= 0:
            blocked = cb.blockSignals(True)
            try:
                cb.setCurrentIndex(idx)
            finally:
                cb.blockSignals(blocked)
    # Reset threshold to the range of the new channel.
    recalculate_threshold(app)
    app._invalidate_stats_cache()
    refresh_metric_combo_for_channel(app)
    # Keep the ctxbar ThreshFracOn quick-edit showing the new channel's value.
    refresh_thresh_frac_quick_edit(app)
    # Announce on the bus. The RedrawCoordinator subscriber coalesces the
    # redraw — visible scope now, the other marked dirty for redraw on tab
    # switch (see redraw_coordinator.py / fold_change_scopes.py).
    app._event_bus.channel_changed.emit(channel_state)
    if hasattr(app, "_cdf_chan_lbl"):
        app._cdf_chan_lbl.setText(f"({target_label} x range)")
    if hasattr(app, "_bar_ylim_chan_lbl"):
        app._bar_ylim_chan_lbl.setText(f"{target_label} y:")


# ── Property/metric combo refresh ───────────────────────────────────────────


def refresh_metric_combo_for_channel(app) -> None:
    """Reshape every Property combo to match the active channel.

    For ratio channels each combo collapses to a single ``Calculated
    Val`` entry; for real channels the full intensity set is restored
    with ``smFISH Count`` greyed out when the channel isn't an smFISH
    one. Per-tab combos (heatmap / stats) follow the global channel.

    Replaces ``partial(refresh_metric_combo_for_channel, WellViewerApp)``.
    """
    try:
        channel_label = app._active_channel_label()
    except Exception:
        channel_label = ""
    smfish_chan = app._app_state.channel.active_channel
    for attr in ("_plotting_metric_cb", "_metric_cb", "_metric_cb_bar"):
        populate_metric_combo(app,
            getattr(app, attr, None),
            channel_entry=channel_label,
            smfish_channel=smfish_chan,
        )
    # Stats has its own channel combo, so its Property combo follows that one.
    refresh_stats_property_combo(app)


# ── Channel-selector / dropdown sync ────────────────────────────────────────


def update_channel_selector(app) -> None:
    """Refresh the channel dropdown values and selection to match loaded data.

    Replaces ``partial(update_channel_selector, WellViewerApp)``.
    """
    real_labels = [ch.upper() for ch in app._app_state.channel.fluor_channels]
    ratio_labels = app._ratio_dropdown_labels()
    # Shape features are channel-independent virtual channels shown under their
    # own dropdown section; their key is the shape_* column itself.
    shape_cols = list(app._app_state.channel.shape_features or [])
    shape_labels = [shape_label_for(c) or c for c in shape_cols]
    labels = (real_labels + ratio_labels + shape_labels) or ["—"]
    # Map the dropdown label back to the underlying channel key used by
    # set_active_channel (real token, ratio:<name> key, or shape_* column).
    app._label_to_channel_key = {ch.upper(): ch for ch in app._app_state.channel.fluor_channels}
    for r in app._app_state.channel.ratio_metrics:
        app._label_to_channel_key[app._ratio_label_for(r)] = r.key()
    for col, lbl in zip(shape_cols, shape_labels):
        app._label_to_channel_key[lbl] = col
    # Montage/preview includes the segmentation channel token.
    seg_tok = getattr(app, "_seg_channel_token", "")
    montage_chans = list(app._app_state.channel.fluor_channels)
    if seg_tok and seg_tok not in montage_chans:
        montage_chans.append(seg_tok)
    montage_labels = [ch.upper() for ch in montage_chans] or ["—"]
    review_labels = [ch.upper() for ch in (app._app_state.channel.review_image_channels or app._app_state.channel.fluor_channels)] or ["—"]
    # Plot tabs: real + ratio channels inline, shape features under their own
    # section header. Image tabs (preview/review) never get shape features.
    plot_sections = [("", real_labels + ratio_labels)]
    if shape_labels:
        plot_sections.append(("Shape Features", shape_labels))
    have_any = bool(real_labels or ratio_labels or shape_labels)
    for attr in ("_chan_cb_line", "_chan_cb_bar", "_chan_cb_distribution",
                 "_chan_cb_heatmap", "_plotting_channel_cb"):
        if hasattr(app, attr):
            if have_any:
                set_combo_values_sectioned(getattr(app, attr), plot_sections)
            else:
                set_combo_values(getattr(app, attr), ["—"])
    if getattr(app, "_chan_cb_preview", None) is not None:
        set_combo_values(app._chan_cb_preview, montage_labels)
    # The review-image channel combo builds lazily (Segmentation is an Image
    # Browser sub-tab), so it can still be the None default at dataset-load time;
    # it is (re)populated when the sub-tab first builds. See
    # image_browser.controller.build_image_browser_subtab.
    if getattr(app, "_review_image_chan_cb", None) is not None:
        set_combo_values(app._review_image_chan_cb, review_labels)
    active_label = app._active_channel_label()

    def _pick_valid(current: str, candidates: List[str], fallback_label: str) -> str:
        if current in candidates and current != "—":
            return current
        if fallback_label in candidates and fallback_label != "—":
            return fallback_label
        if candidates and candidates[0] != "—":
            return candidates[0]
        return "—"

    # Plot tabs: only measurement channels. Prefer the restored/active label
    # over the combo's current text so a saved channel survives a dataset
    # reopen — at load time the combo still shows its default (just-repopulated)
    # text, which would otherwise win over the channel we restored. Falls back
    # to the current text when the saved channel is stale (gone in this dataset).
    plot_label = _pick_valid(active_label, labels, app._plot_chan_var.currentText())
    app._plot_chan_var.setCurrentText(plot_label)

    # Image tabs: each validates against its own channel universe.
    active_image_label = app._app_state.channel.active_image_channel.upper()
    montage_var = getattr(app, "_montage_chan_var", None)
    if montage_var is not None:
        montage_label = _pick_valid(montage_var.currentText(), montage_labels, active_image_label)
        montage_var.setCurrentText(montage_label)
    else:
        montage_label = "—"
    review_var = getattr(app, "_review_image_chan_var", None)
    if review_var is not None:
        review_label = _pick_valid(review_var.currentText(), review_labels, active_image_label)
        review_var.setCurrentText(review_label)
    else:
        review_label = "—"

    # Keep active image channel anchored only when the current value is invalid.
    if active_image_label not in montage_labels and active_image_label not in review_labels:
        fallback_image_label = montage_label if montage_label != "—" else review_label
        if fallback_image_label != "—":
            set_active_image_channel(app, fallback_image_label.lower())

    # Keep active channel anchored to a valid plot channel.
    if active_label not in labels:
        if plot_label != "—":
            set_active_channel(app, app._channel_key_for_label(plot_label))
        else:
            # No valid plot channel — clear active state explicitly.
            app._app_state.channel.active_channel = ""

    # Force the visible global ctxbar combo to track the (now-valid)
    # active label.
    global_cb = getattr(app, "_plotting_channel_cb", None)
    if global_cb is not None:
        active_label_final = app._active_channel_label()
        if not active_label_final:
            # Empty-state: deselect the combo so it can't display a
            # stale label left over from the previous channel list.
            if global_cb.currentIndex() != -1:
                blocked = global_cb.blockSignals(True)
                try:
                    global_cb.setCurrentIndex(-1)
                finally:
                    global_cb.blockSignals(blocked)
        else:
            idx = global_cb.findText(active_label_final)
            if idx >= 0 and global_cb.currentIndex() != idx:
                blocked = global_cb.blockSignals(True)
                try:
                    global_cb.setCurrentIndex(idx)
                finally:
                    global_cb.blockSignals(blocked)
    # Refresh the global Property combo's per-item enable state now that
    # the channel list has changed.
    refresh_metric_combo_for_channel(app)

    # Same trick for the Segmentation tab's channel combo and the
    # back-compat Movie Montage combo — _pick_valid above preserves
    # currentText when valid, but if _active_image_channel has drifted,
    # the dropdown would disagree with the image actually drawn. Snap
    # the currentIndex so the first user click triggers the redraw.
    active_image_label_final = (app._app_state.channel.active_image_channel or "").upper()
    for _attr in ("_review_image_chan_cb", "_chan_cb_preview"):
        cb = getattr(app, _attr, None)
        if cb is None or not hasattr(cb, "findText"):
            continue
        idx = cb.findText(active_image_label_final) if active_image_label_final else -1
        if idx >= 0 and cb.currentIndex() != idx:
            blocked = cb.blockSignals(True)
            try:
                cb.setCurrentIndex(idx)
            finally:
                cb.blockSignals(blocked)

    # Back-compat sync.
    if hasattr(app, "_chan_var"):
        tab_label = app._current_centre_tab()
        if tab_label == "Segmentation":
            app._chan_var.setCurrentText(review_label)
        else:
            app._chan_var.setCurrentText(plot_label)


def set_active_image_channel(app, channel: str, *, preserve_review_view: bool = False) -> None:
    """Switch image-display channel for Movie Montage and Review Image."""
    channel_switch_debug = _debug_flags.review_image_channel_switch_debug_enabled()
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 3] set_active_image_channel requested=%r current=%r preserve_review_view=%s",
            channel,
            app._app_state.channel.active_image_channel,
            preserve_review_view,
        )
    if not channel or channel == "—":
        return
    if channel == app._app_state.channel.active_image_channel:
        if preserve_review_view:
            app._app_state.review_image.preserve_view_on_refresh = True
            if app._app_state.preview.selected_well:
                from well_viewer.features.image_browser.review_image.renderer import refresh_review_image
                refresh_review_image(app)
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 3] no-op channel switch; active remains=%r preserve_review_view=%s",
                app._app_state.channel.active_image_channel,
                preserve_review_view,
            )
        return
    prev_channel = app._app_state.channel.active_image_channel
    app._app_state.channel.active_image_channel = channel
    ch_upper = channel.upper()
    if hasattr(app, "_review_image_chan_var"):
        app._review_image_chan_var.setCurrentText(ch_upper)
    if hasattr(app, "_review_lut_chan_lbl"):
        app._review_lut_chan_lbl.setText(f"{ch_upper} LUT min:")
    saved_review_lut = app._app_state.image_view.lut_by_channel.get(channel)
    if saved_review_lut and hasattr(app, "_review_lmin_entry") and hasattr(app, "_review_lmax_entry"):
        app._review_lmin_entry.setText(f"{saved_review_lut[0]:.0f}")
        app._review_lmax_entry.setText(f"{saved_review_lut[1]:.0f}")
    if preserve_review_view:
        app._app_state.review_image.preserve_view_on_refresh = True
    if app._app_state.preview.selected_well:
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 3->4] reloading preview for selected_well=%r",
                app._app_state.preview.selected_well,
            )
        from well_viewer.features.image_browser.review_image.renderer import update_preview
        update_preview(app, app._app_state.preview.selected_well)
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 3] set_active_image_channel updated before=%r after=%r",
            prev_channel,
            app._app_state.channel.active_image_channel,
        )



def sync_metric_combos(app, metric: str) -> None:
    """Push *metric* (an ``active_metric`` key like ``"smfish_count"``) into
    every metric-combo widget the app knows about, without retriggering the
    change handler.

    Used by ``set_active_metric`` after a real metric change and by
    ``persistence.view_state.apply_state`` after a restore-time direct
    assignment. The latter bypasses ``set_active_metric``'s early-return
    when the field is already at the target value, so without this helper
    the dropdown reverts to the combo's default ("Mean Intensity") on app
    reopen and the user has to "wiggle" the dropdown to force a sync.
    """
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
    label = METRIC_KEY_TO_LABEL.get(metric, "Mean Intensity")
    for attr in ("_metric_var", "_metric_cb", "_metric_cb_bar",
                 "_plotting_metric_cb", "_stats_property_cb"):
        cb = getattr(app, attr, None)
        if cb is None:
            continue
        try:
            current = str(cb.currentText() or "")
        except Exception:
            swallowed()  # silent except
            continue
        if current == label:
            continue
        idx = cb.findText(label)
        if idx < 0:
            continue
        blocked = cb.blockSignals(True)
        try:
            cb.setCurrentIndex(idx)
        finally:
            cb.blockSignals(blocked)


def set_active_metric(app, metric: str) -> None:
    """Switch the active intensity property and redraw."""
    if metric == app._app_state.channel.active_metric:
        return
    app._app_state.channel.active_metric = metric
    if not is_ratio_key(app._app_state.channel.active_val_col) and not is_shape_column(app._app_state.channel.active_val_col):
        app._app_state.channel.active_val_col = f"{app._app_state.channel.active_channel}_{app._app_state.channel.active_metric}"
    active_val_col = app._app_state.channel.active_val_col
    if not is_ratio_key(active_val_col) and not is_shape_column(active_val_col):
        cache = getattr(app._app_state.dataset, "cache", {}) or {}
        first_df = next(iter(cache.values()), None)
        if (
            first_df is not None
            and active_val_col not in first_df.columns
            and not any(c.lower() == active_val_col.lower() for c in first_df.columns)
        ):
            _logger.warning(
                "set_active_metric: active_val_col=%r not present in first "
                "cached well DataFrame (channel=%r, metric=%r). Dropping "
                "the per-well DataFrame cache and re-reading from disk to "
                "recover from a stale cache (e.g. pre-normalisation "
                "mixed-case headers). Cached columns were: %s",
                active_val_col,
                app._app_state.channel.active_channel,
                metric,
                sorted(first_df.columns.tolist()),
            )
            if hasattr(app, "_invalidate_dataset_cache"):
                app._invalidate_dataset_cache()
    sync_metric_combos(app, metric)
    refresh_metric_combo_for_channel(app)
    refresh_heatmap_aggregation_options(app)
    recalculate_threshold(app)
    app._invalidate_stats_cache()
    app._redraw()
    if hasattr(app, "_bar_tp_cb"):
        from well_viewer.features.barplot.renderer import redraw_bars
        redraw_bars(app)



def populate_metric_combo(
    app,
    cb,
    *,
    channel_entry: str,
    smfish_channel: Optional[str] = None,
    include_fraction: bool = False,
) -> None:
    """Repopulate a Property combo to match the channel context.

    Ratio channels (resolved via ``_label_to_channel_key``) collapse the
    combo to a single ``Calculated Val`` entry — ratios are a single
    computed value per cell, so the intensity flavours don't exist for
    them. Real channels get the full ``METRIC_ORDER`` set (optionally
    appended with ``Fraction above threshold`` for the agg-scatter
    combos) with ``smFISH Count`` greyed out unless ``smfish_channel``
    names a channel in ``_smfish_channels``.

    The current selection is preserved when the previous label still
    exists in the new item list; otherwise we fall back to the label
    for ``_active_metric``.
    """
    if cb is None:
        return
    from well_viewer.data.metric_labels import (
        METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
    )
    mapped_key = (getattr(app, "_label_to_channel_key", None) or {}).get(channel_entry)
    is_ratio = bool(mapped_key and is_ratio_key(mapped_key))
    is_shape = bool(mapped_key and is_shape_column(mapped_key))

    prev = str(cb.currentText() or "")
    blocked = cb.blockSignals(True)
    try:
        cb.clear()
        if is_ratio or is_shape:
            # A single computed value per cell — no intensity-metric flavours.
            cb.addItem(CALCULATED_VAL_LABEL)
            cb.setCurrentIndex(0)
            # Shape features have no metric choice at all; disable the combo so
            # it reads as inert rather than a one-item dropdown.
            cb.setEnabled(not is_shape)
            return
        items = list(METRIC_ORDER)
        if include_fraction:
            items.append("Fraction above threshold")
        cb.addItems(items)
        cb.setEnabled(True)
        if prev in items:
            cb.setCurrentText(prev)
        else:
            fallback = METRIC_KEY_TO_LABEL.get(
                app._app_state.channel.active_metric, "Mean Intensity"
            )
            idx = cb.findText(fallback)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        sm_idx = cb.findText("smFISH Count")
        if sm_idx >= 0:
            model = cb.model()
            item = model.item(sm_idx) if model is not None else None
            if item is not None:
                smf_ok = (
                    smfish_channel is not None
                    and smfish_channel in app._app_state.channel.smfish_channels
                )
                item.setEnabled(smf_ok)
    finally:
        cb.blockSignals(blocked)



def refresh_heatmap_aggregation_options(app) -> None:
    """Grey out threshold-based heatmap aggregations for non-MFI properties.

    The ThreshFracOn cut is defined against mean fluorescence intensity
    (see Cell Gating tab), so "Mean above threshold" and "Fraction above
    threshold" only make sense when the active Property is MFI. When the
    user picks Total / Max / Min / Std / smFISH Count, those options
    disable so they're not silently misapplied.
    """
    cb = getattr(app, "_heatmap_metric_cb", None)
    if cb is None:
        return
    from well_viewer.features.heatmap.controller import (
        METRIC_MEAN, METRIC_FRACTION,
    )
    is_mfi = app._app_state.channel.active_metric == "mean_intensity"
    model = cb.model()
    if model is None:
        return
    for label in (METRIC_MEAN, METRIC_FRACTION):
        idx = cb.findText(label)
        if idx < 0:
            continue
        item = model.item(idx)
        if item is not None:
            item.setEnabled(is_mfi)
    # If the currently selected aggregation just went unavailable, fall
    # back to "Mean (all cells)" so the heatmap stays valid.
    if not is_mfi:
        current = str(cb.currentText() or "")
        if current in (METRIC_MEAN, METRIC_FRACTION):
            from well_viewer.features.heatmap.controller import METRIC_MEAN_ALL
            alt = cb.findText(METRIC_MEAN_ALL)
            if alt >= 0:
                blocked = cb.blockSignals(True)
                try:
                    cb.setCurrentIndex(alt)
                finally:
                    cb.blockSignals(blocked)



def refresh_stats_property_combo(app) -> None:
    """Reshape the stats Property combo to match the stats channel combo."""
    ch_cb = getattr(app, "_stats_channel_cb", None)
    m_cb = getattr(app, "_stats_property_cb", None)
    if m_cb is None:
        return
    channel = str(ch_cb.currentText() or "") if ch_cb is not None else ""
    # Strip the " (spots)" suffix so smfish-enable matching works.
    smf_base = channel.split(" ")[0] if channel else app._app_state.channel.active_channel
    populate_metric_combo(app,
        m_cb, channel_entry=channel, smfish_channel=smf_base,
    )



def on_plot_channel_selected(app, source=None) -> None:
    """Channel-switch handler for line/bar/distribution/heatmap plot tabs.

    Each plot tab connects its channel combobox via a lambda that
    captures the source combobox and passes it as ``source`` here.
    ``QObject.sender()`` is unreliable when the slot is a Python
    lambda in PySide6, and ``app._plot_chan_var`` is bound to the
    line-tab combobox only — so falling back to either of those
    leaks the line-tab value when other tabs change channels and
    ``_set_active_channel`` short-circuits because the "new" channel
    equals the current one. Prefer the explicit ``source`` widget.
    """
    label = ""
    if source is not None and hasattr(source, "currentText"):
        try:
            label = str(source.currentText() or "")
        except Exception:
            label = ""
    if not label:
        try:
            sender = app.sender()
        except Exception:
            sender = None
        if sender is not None and hasattr(sender, "currentText"):
            try:
                label = str(sender.currentText() or "")
            except Exception:
                label = ""
    if not label:
        label = app._plot_chan_var.currentText()
    # Route real channels and ratios via the label→key map so ratio
    # selections (e.g. "GFP/MCHERRY") resolve to a ``ratio:<name>`` key.
    set_active_channel(app, app._channel_key_for_label(label))



def set_ratio_metrics(app, ratios: Iterable[RatioMetric]) -> None:
    """Replace the ratio list and rebuild the index + UI."""
    app._app_state.channel.ratio_metrics = list(ratios)
    app._rebuild_ratio_index()
    panel = getattr(app, "_ratio_panel", None)
    if panel is not None:
        try:
            panel.refresh_from_app()
        except Exception:
            swallowed()  # was: pass (silent except)
    # Cell Gating shows one row per fluor channel + one per ratio. Pull
    # the ratio rows into the table whenever the ratio set changes.
    if hasattr(app, "_cell_gating_fluor_gate_edits"):
        try:
            from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_load_cell_areas
            cell_gating_load_cell_areas(app)
        except Exception:
            swallowed()  # was: pass (silent except)
    # Ratio additions become virtual channel entries in every plot-tab
    # combo (including the global ctxbar chip), so refresh those now —
    # otherwise the new ratio doesn't appear in the dropdown until the
    # next dataset reload.
    if hasattr(app, "_update_channel_selector"):
        try:
            update_channel_selector(app)
        except Exception:
            swallowed()  # was: pass (silent except)



def on_preview_channel_selected(app, _e=None) -> None:
    """Channel-switch handler for the Movie Montage tab."""
    selected_ui_value = ""
    if hasattr(app, "_chan_cb_preview"):
        selected_ui_value = str(app._chan_cb_preview.currentText() or "").strip()
    if not selected_ui_value and hasattr(app, "_montage_chan_var"):
        selected_ui_value = app._montage_chan_var.currentText()
    if _debug_flags.movie_montage_debug_enabled():
        _logger.debug(
            "preview_channel_selected ui_value=%r active_before=%r",
            selected_ui_value,
            app._app_state.channel.active_image_channel,
        )
    set_active_image_channel(app, selected_ui_value.lower())



def on_metric_selected_global(app) -> None:
    """Handle the global Property combo (ctxbar) change."""
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
    cb = getattr(app, "_plotting_metric_cb", None)
    if cb is None:
        return
    metric_label = str(cb.currentText() or "")
    metric = METRIC_LABEL_TO_KEY.get(metric_label, "mean_intensity")
    # smFISH count only makes sense on smfish channels; gracefully fall
    # back to mean intensity so the user sees a redraw instead of an
    # empty plot.
    if metric == "smfish_count" and app._app_state.channel.active_channel not in app._app_state.channel.smfish_channels:
        metric = "mean_intensity"
    set_active_metric(app, metric)



def channel_key_for_label(app, label: str) -> str:
    """Map a dropdown label back to the ``_set_active_channel`` argument.

    Real channels return their lowercase token; ratios return their
    ``ratio:<name>`` key.
    """
    mapping = getattr(app, "_label_to_channel_key", None) or {}
    if label in mapping:
        return mapping[label]
    # Fallback when the dropdown was populated before the mapping was
    # built — assume real channel and lowercase the label.
    return str(label or "").lower()



def rebuild_ratio_index(app) -> None:
    """Refresh the resolver-friendly ratio index and any dependent UI."""
    app._app_state.channel.ratio_index = build_ratio_index(app._app_state.channel.ratio_metrics)
    # If the active val_col references a deleted ratio, fall back to the
    # first real fluorescence channel so plots stay valid.
    if is_ratio_key(app._app_state.channel.active_val_col) and app._app_state.channel.active_val_col not in app._app_state.channel.ratio_index:
        fallback = (app._app_state.channel.fluor_channels or ["gfp"])[0]
        app._app_state.channel.active_channel = fallback
        app._app_state.channel.active_val_col = f"{fallback}_{app._app_state.channel.active_metric}"
    update_channel_selector(app)
    app._invalidate_stats_cache()
    if hasattr(app, "_redraw"):
        try:
            app._redraw()
        except Exception:
            swallowed()  # was: pass (silent except)



def get_all_fluor_gates(app) -> Dict[str, float]:
    """Get FluorGating thresholds for all loaded channels.

    Returns a dict mapping channel name -> gate threshold, e.g., {"gfp": 10.0, "mcherry": 20.0}.
    Used by aggregate_with_threshold_df to apply consistent gating across all channels.
    """
    gates = {}
    for channel in app._app_state.channel.fluor_channels:
        gates[channel] = app._get_fluor_gate(channel)
    return gates



def get_thresh_frac_on(app, channel: Optional[str] = None) -> float:
    """Get ThreshFracOn threshold. Uses active channel if not specified."""
    if channel is None:
        channel = app._app_state.channel.active_channel
    # _redraw can fire before the cell-gating tab is built (e.g. from
    # sidebar releases during early load); fall back to the default.
    if not hasattr(app, "_cell_gating_thresh_frac_edits"):
        return app._app_state.channel.threshold
    from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_get_thresh_frac_on
    return cell_gating_get_thresh_frac_on(app, channel)


def set_thresh_frac_on(app, value, channel: Optional[str] = None) -> None:
    """Set ThreshFracOn for a channel (the active channel by default).

    Writes *through* the Cell Gating tab's edit — the single source of truth that
    persistence reads from — then runs the existing change handler (validate →
    save → persist → redraw). This is the write half of the plotting-tab
    quick-edit; editing here stays consistent with the Cell Gating tab,
    Auto-threshold, stats, and exports. No-op until a dataset is loaded.
    """
    if not app._app_state.dataset.data_dir:
        return
    if channel is None:
        channel = app._app_state.channel.active_channel
    if not channel or channel == "—":
        return
    # The Cell Gating tab is built lazily; materialize it (same pattern as
    # persistence.cell_gating.load_from_pipeline_info) so its edits dict exists.
    if not hasattr(app, "_cell_gating_thresh_frac_edits"):
        build = getattr(app, "_centre_build_pending", None)
        if callable(build):
            build("Sample Definitions")
        from well_viewer.features.grouping.cell_gating_tab_view import build_cell_gating_subtab
        build_cell_gating_subtab(app)
    from well_viewer.features.grouping.cell_gating_tab_view import (
        _cell_gating_resolve_key,
        cell_gating_on_threshold_frac_on_change,
    )
    edits = getattr(app, "_cell_gating_thresh_frac_edits", {}) or {}
    edit = edits.get(_cell_gating_resolve_key(app, channel))
    if edit is None:
        return
    blocked = edit.blockSignals(True)
    try:
        edit.setText(str(float(value)))
    finally:
        edit.blockSignals(blocked)
    cell_gating_on_threshold_frac_on_change(app)


def refresh_thresh_frac_quick_edit(app) -> None:
    """Sync the ON-threshold quick-edit Steppers to the active channel's
    ThreshFracOn.

    There are now three of these quick-edits — the Plotting ctxbar, the
    Segmentation (Review Image) controls row, and the Image Table controls row —
    all editing the same per-channel value (the active channel's ON/OFF
    intensity threshold). Signals are blocked so this display sync doesn't loop
    back through ``set_thresh_frac_on`` (``Stepper.setValue`` emits
    ``valueChanged``). No-op for any stepper that hasn't been built yet.
    """
    # Keep the status-bar threshold chip (F1) in sync — runs on channel switch
    # and every threshold edit, the same triggers as the ctxbar stepper.
    refresh_chips = getattr(app, "_refresh_status_chips", None)
    if callable(refresh_chips):
        refresh_chips()
    value = float(get_thresh_frac_on(app))
    for attr in (
        "_plotting_thresh_frac_stepper",
        "_review_thresh_frac_stepper",
        "_image_table_thresh_frac_stepper",
    ):
        stepper = getattr(app, attr, None)
        if stepper is None:
            continue
        blocked = stepper.blockSignals(True)
        try:
            stepper.setValue(value)
        finally:
            stepper.blockSignals(blocked)


def get_fluor_gate(app, channel: str) -> float:
    """Get FluorGating threshold for a channel."""
    if hasattr(app, "_cell_gating_fluor_gate_edits"):
        from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_get_fluor_gate
        return cell_gating_get_fluor_gate(app, channel)
    return 0.0



def active_channel_label(app) -> str:
    """Return the dropdown label corresponding to the active channel."""
    active_val_col = app._app_state.channel.active_val_col
    if is_ratio_key(active_val_col):
        ratio_name = ratio_name_from_key(active_val_col)
        for r in app._app_state.channel.ratio_metrics:
            if r.name == ratio_name:
                return app._ratio_label_for(r)
        return ratio_name.upper()
    if is_shape_column(active_val_col):
        return shape_label_for(active_val_col) or active_val_col
    return app._app_state.channel.active_channel.upper()



def ratio_label_for(app, ratio: RatioMetric) -> str:
    """Return the dropdown label for a ratio (e.g. ``"GFP/MCHERRY"``).

    Disambiguates by appending ``[name]`` if two ratios share a label.
    """
    base = ratio.display_label()
    same_base = [r for r in app._app_state.channel.ratio_metrics if r.display_label() == base]
    if len(same_base) > 1:
        return f"{base} [{ratio.name}]"
    return base



def on_metric_selected(app) -> None:
    """Handle the per-tab (legacy hidden) metric selector change."""
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
    metric_label = app._metric_var.currentText()
    metric = METRIC_LABEL_TO_KEY.get(metric_label, "mean_intensity")
    set_active_metric(app, metric)



def open_ratio_panel(app) -> None:
    """Bring the Sample Definitions tab forward — the ratio editor lives there now."""
    nb = getattr(app, "_notebook", None)
    if nb is None:
        return
    nb.setCurrentByName("Sample Definitions")

