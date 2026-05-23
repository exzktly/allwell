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

from well_viewer import debug_flags as _debug_flags
from well_viewer.data_loading import (
    _all_fluor_values,
    detect_fluor_channels,
    detect_nuclear_channel_token,
    detect_review_image_channels,
    detect_smfish_channels,
    merge_fluor_channels,
    normalize_channel_tokens,
)
from well_viewer.ratio_models import is_ratio_key
from well_viewer.ui_helpers import set_combo_values
from functools import partial

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
    update_channel_selector(app)

    # Update smFISH channels, then let ChannelState re-assert its invariants:
    # drop a stale smfish_count metric the new channel can't support, and
    # re-derive the value column (a no-op while a ratio is active).
    channel_state = app._app_state.channel
    channel_state.smfish_channels = set(detected_smfish)
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
        from well_viewer.stats_controller import stats_update_tp_menu
        stats_update_tp_menu(app)
    if hasattr(app, "_distribution_tp_cb"):
        try:
            from well_viewer.tabs.distribution_tab_view import refresh_distribution_timepoints
            refresh_distribution_timepoints(app)
        except Exception:
            _logger.exception("Distribution timepoint refresh failed")
    if hasattr(app, "_heatmap_tp_slider"):
        try:
            from well_viewer.tabs.heatmap_tab_view import refresh_heatmap_timepoints
            refresh_heatmap_timepoints(app)
        except Exception:
            _logger.exception("Heatmap timepoint refresh failed")

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
    if hasattr(app, "_cell_gating_area_edit"):
        from well_viewer.tabs.cell_gating_tab_view import (
            cell_gating_load_cell_areas,
            cell_gating_load_threshold_frac_on,
        )
        cell_gating_load_cell_areas(app)
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
    else:
        # set_active_channel resets a stale ratio / smFISH metric so the new
        # channel composes a real column, and re-derives the value column.
        if not channel_state.set_active_channel(channel):
            return
        # Sync every metric combo to the (possibly reset) active metric so a
        # freshly-visible non-ratio channel shows the right Property label.
        from well_viewer.metric_labels import METRIC_KEY_TO_LABEL
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
    labels = (real_labels + ratio_labels) or ["—"]
    # Map the uppercase dropdown label back to the underlying channel key
    # used by set_active_channel.
    app._label_to_channel_key = {ch.upper(): ch for ch in app._app_state.channel.fluor_channels}
    for r in app._app_state.channel.ratio_metrics:
        app._label_to_channel_key[app._ratio_label_for(r)] = r.key()
    # Montage/preview includes the segmentation channel token.
    seg_tok = getattr(app, "_seg_channel_token", "")
    montage_chans = list(app._app_state.channel.fluor_channels)
    if seg_tok and seg_tok not in montage_chans:
        montage_chans.append(seg_tok)
    montage_labels = [ch.upper() for ch in montage_chans] or ["—"]
    review_labels = [ch.upper() for ch in (app._app_state.channel.review_image_channels or app._app_state.channel.fluor_channels)] or ["—"]
    for attr in ("_chan_cb_line", "_chan_cb_bar", "_chan_cb_distribution",
                 "_chan_cb_heatmap", "_plotting_channel_cb"):
        if hasattr(app, attr):
            set_combo_values(getattr(app, attr), labels)
    if hasattr(app, "_chan_cb_preview"):
        set_combo_values(app._chan_cb_preview, montage_labels)
    if hasattr(app, "_review_image_chan_cb"):
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

    # Plot tabs: only measurement channels.
    plot_label = _pick_valid(app._plot_chan_var.currentText(), labels, active_label)
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
                from well_viewer.review_image_renderer import refresh_review_image
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
    if hasattr(app, "_montage_chan_var"):
        app._montage_chan_var.setCurrentText(ch_upper)
    if hasattr(app, "_review_image_chan_var"):
        app._review_image_chan_var.setCurrentText(ch_upper)
    if hasattr(app, "_mon_lut_chan_lbl"):
        app._mon_lut_chan_lbl.setText(f"{ch_upper} LUT min:")
    if hasattr(app, "_review_lut_chan_lbl"):
        app._review_lut_chan_lbl.setText(f"{ch_upper} LUT min:")
    saved_review_lut = app._app_state.review_image.lut_by_channel.get(channel)
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
        from well_viewer.review_image_renderer import update_preview
        update_preview(app, app._app_state.preview.selected_well)
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 3] set_active_image_channel updated before=%r after=%r",
            prev_channel,
            app._app_state.channel.active_image_channel,
        )



def set_active_metric(app, metric: str) -> None:
    """Switch the active intensity property and redraw."""
    if metric == app._app_state.channel.active_metric:
        return
    from well_viewer.metric_labels import METRIC_KEY_TO_LABEL
    app._app_state.channel.active_metric = metric
    if not is_ratio_key(app._app_state.channel.active_val_col):
        app._app_state.channel.active_val_col = f"{app._app_state.channel.active_channel}_{app._app_state.channel.active_metric}"
    label = METRIC_KEY_TO_LABEL.get(metric, "Mean Intensity")
    # Sync every metric combo (per-tab hidden legacy combos + the
    # global ctxbar combo + the stats per-tab Property combo) without
    # retriggering this handler.
    for attr in ("_metric_var", "_metric_cb", "_metric_cb_bar",
                 "_plotting_metric_cb", "_stats_property_cb"):
        cb = getattr(app, attr, None)
        if cb is None:
            continue
        try:
            current = str(cb.currentText() or "")
        except Exception:
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
    refresh_metric_combo_for_channel(app)
    refresh_heatmap_aggregation_options(app)
    recalculate_threshold(app)
    app._invalidate_stats_cache()
    app._redraw()
    if hasattr(app, "_bar_tp_cb"):
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
    from well_viewer.metric_labels import (
        METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
    )
    ratio_key = (getattr(app, "_label_to_channel_key", None) or {}).get(channel_entry)
    is_ratio = bool(ratio_key and is_ratio_key(ratio_key))

    prev = str(cb.currentText() or "")
    blocked = cb.blockSignals(True)
    try:
        cb.clear()
        if is_ratio:
            cb.addItem(CALCULATED_VAL_LABEL)
            cb.setCurrentIndex(0)
            cb.setEnabled(True)
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
    from well_viewer.heatmap_controller import (
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
            from well_viewer.heatmap_controller import METRIC_MEAN_ALL
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
            pass
    # Cell Gating shows one row per fluor channel + one per ratio. Pull
    # the ratio rows into the table whenever the ratio set changes.
    if hasattr(app, "_cell_gating_area_edit"):
        try:
            from well_viewer.tabs.cell_gating_tab_view import cell_gating_load_cell_areas
            cell_gating_load_cell_areas(app)
        except Exception:
            pass
    # Ratio additions become virtual channel entries in every plot-tab
    # combo (including the global ctxbar chip), so refresh those now —
    # otherwise the new ratio doesn't appear in the dropdown until the
    # next dataset reload.
    if hasattr(app, "_update_channel_selector"):
        try:
            update_channel_selector(app)
        except Exception:
            pass

