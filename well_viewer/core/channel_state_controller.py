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
from well_viewer.domain.metric_registry import channel_supports_smfish
from well_viewer.domain.ratio_models import is_ratio_key, ratio_name_from_key
from well_viewer.core.ui_helpers import set_combo_values, set_combo_values_sectioned
from pipeline.shape_features import is_shape_column, label_for as shape_label_for
from well_viewer.core.safe import swallowed
import theme

_logger = logging.getLogger("well_viewer")


# ── Threshold / channel-list recompute ──────────────────────────────────────


def recalculate_threshold(ctx) -> None:
    """Re-detect channels from the loaded CSVs, sync combos, and rebuild
    the (lo, hi) intensity range used by the threshold sliders.

    Replaces ``partial(recalculate_threshold, WellViewerApp)``.

    A8: **fully ctx-native** — the former single god-object unwrap is gone. Rows
    load through ``get_rows(ctx)``; typed reads/writes go through ``ctx.state``;
    ``update_channel_selector`` is ctx-native; the metric-selector frames resolve
    via ``ctx.widgets``; the gating reload calls ``load_from_pipeline_info(ctx)``
    with the tab-built signal read off ``ctx.runtime.cell_gating``; and the
    ``_features`` ``on_channel_changed`` hook fan-out now dispatches ``ctx`` (all
    seven hooks — bar / stats / distribution / heatmap / multivariate / spatial /
    scatter — take ctx, guarding widget existence via ``ctx.widgets.has``)."""
    from well_viewer.data.data_loading import get_rows
    sample_df = next(
        (get_rows(ctx, lbl) for lbl in ctx.state.dataset.well_paths),
        pd.DataFrame(),
    )
    detected = detect_fluor_channels(sample_df)
    detected_smfish = detect_smfish_channels(sample_df)
    pipeline_fluor_raw = [
        str(tok).strip().lower()
        for tok in (
            ctx.state.dataset.pipeline_info.get("fluor_tokens", [])
            if isinstance(ctx.state.dataset.pipeline_info, dict) else []
        )
        if str(tok).strip()
    ]
    pipeline_fluor = normalize_channel_tokens(pipeline_fluor_raw)
    detected = normalize_channel_tokens(detected)
    seg_tok = ""
    if isinstance(ctx.state.dataset.pipeline_info, dict):
        seg_tok = str(ctx.state.dataset.pipeline_info.get("nuclear_token", "") or "").strip().lower()
    if not seg_tok:
        seg_tok = detect_nuclear_channel_token(sample_df)
    fluor_channels = merge_fluor_channels(pipeline_fluor, detected, seg_tok)
    if fluor_channels:
        ctx.state.channel.fluor_channels = fluor_channels
        if not ctx.state.channel.active_channel:
            ctx.state.channel.active_channel = fluor_channels[0]
        if not ctx.state.channel.active_image_channel:
            ctx.state.channel.active_image_channel = fluor_channels[0]
    ctx.state.channel.seg_channel_token = seg_tok
    ctx.state.channel.review_image_channels = detect_review_image_channels(
        sample_df, ctx.state.channel.fluor_channels, seg_tok,
    )
    # Populate the available shape-feature columns before the dropdown is built
    # so they appear in the "Shape Features" section.
    ctx.state.channel.shape_features = detect_shape_features(sample_df)

    # The smFISH channel set has to be current *before* update_channel_selector
    # runs: that call ends in refresh_metric_combo_for_channel, which greys out
    # the "smFISH Count" item for any channel not in this set. Assigning it
    # afterwards left the item disabled against freshly-detected smFISH columns
    # until something else re-ran the refresh — so after a pipeline run (or an
    # Apply Global Threshold, which recalculates through here once the count
    # columns exist) the user had to change the channel and change it back
    # before smFISH Count could be picked.
    channel_state = ctx.state.channel
    channel_state.smfish_channels = set(normalize_channel_tokens(detected_smfish))
    update_channel_selector(ctx)

    # Let ChannelState re-assert its invariants: drop a stale smfish_count
    # metric the new channel can't support, and re-derive the value column
    # (a no-op while a ratio is active).
    channel_state.normalize_metric()
    channel_state.sync_val_col()

    # Update metric selector visibility (back-compat: per-tab frames).
    for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
        _frame = ctx.widgets.get_or_none(frame_attr)
        if _frame is not None:
            _frame.hide()

    # Always refresh timepoint menus regardless of whether intensity
    # values exist — single-timepoint experiments still need the bar menu.
    # Per-feature channel-change refresh (timepoint menus + scatter redraw) is
    # dispatched through the registry's ``on_channel_changed`` hooks — each hook
    # body (and its ``hasattr`` guard) is the relocated block that used to sit
    # here; see ``features/registry.py``. Cell-gating reload stays inline below
    # (it is interleaved with the core ``_load_gating_from_pipeline_info`` call
    # and runs only after the no-data early return).
    feats = ctx.features
    if feats is not None:
        for _desc in feats.with_hook("on_channel_changed"):
            _desc.on_channel_changed(ctx)

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
            _all_fluor_values(get_rows(ctx, lbl), val_col=val_col)
            for lbl in ctx.state.dataset.well_paths
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
    from well_viewer.persistence.cell_gating import load_from_pipeline_info
    load_from_pipeline_info(ctx)
    if ctx.runtime.cell_gating.fluor_gate_edits is not None:
        from well_viewer.features.grouping.cell_gating.controller import (
            cell_gating_load_cell_areas,
            cell_gating_load_threshold_count_on,
            cell_gating_load_threshold_frac_on,
        )
        cell_gating_load_cell_areas(ctx)
        # cell_gating_load_cell_areas rebuilds the per-channel fluor-gate rows
        # for the freshly-detected channels as fresh (default 0.0) edits, which
        # discards the values the load above just wrote. Re-apply the persisted
        # gating params onto the rebuilt rows — otherwise a cold open shows the
        # saved gates/thresholds but never applies them until the user
        # re-toggles a field. (The saved value is always current — every gate
        # edit persists immediately — so re-applying never clobbers a live edit.)
        load_from_pipeline_info(ctx)
        # Both cutoff banks, not just the intensity one: a stale count edit that
        # survives the rebuild is copied back into the typed slice by the next
        # ``_cell_gating_sync_gate_state`` and then persisted, which destroys the
        # saved ThreshCountOn.
        cell_gating_load_threshold_frac_on(ctx)
        cell_gating_load_threshold_count_on(ctx)


# ── Active channel switching ────────────────────────────────────────────────


def set_active_channel(ctx, channel: str) -> None:
    """Switch the active fluorescent channel and trigger a scope-aware redraw.

    ``channel`` may be a real channel token (e.g. ``"gfp"``) or a ratio
    key (``"ratio:<name>"``). Ratios bypass the per-channel metric
    selector and route reads through ``resolve_value``.

    Replaces ``partial(set_active_channel, WellViewerApp)``.

    A8/D-thresh: ctx-native — typed state reads off ``ctx.state.channel``, every
    combo/frame resolves through ``ctx.widgets``, the channel_changed announce
    goes on ``ctx.bus``, and the threshold recompute / stats-cache / quick-edit
    fan-out all take ``ctx``. No god-object unwrap here (the one seam lives in
    ``recalculate_threshold``).
    """
    from well_viewer.core.selection_controller import invalidate_stats_cache
    if not channel or channel == "—":
        return
    # ChannelState owns the channel/metric/val_col invariant — the mutators
    # below perform the pure state transition (and report whether anything
    # changed); this controller keeps the widget-sync that must stay in Qt.
    channel_state = ctx.state.channel
    if is_ratio_key(channel):
        ratio = channel_state.resolve_ratio(channel)
        if ratio is None:
            return
        if not channel_state.set_active_ratio(ratio):
            return
        # Hide the per-cell metric selector — ratios encode their own metrics.
        for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
            frame = ctx.widgets.get_or_none(frame_attr)
            if frame is not None:
                frame.setVisible(False)
    elif is_shape_column(channel):
        # Shape features are a single per-cell value (like ratios) — the column
        # name is the value column, and the per-cell metric selector doesn't apply.
        if not channel_state.set_active_shape_feature(channel):
            return
        for frame_attr in ("_metric_selector_frame", "_metric_selector_frame_bar"):
            frame = ctx.widgets.get_or_none(frame_attr)
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
            cb = ctx.widgets.get_or_none(cb_attr)
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
    target_label = active_channel_label(ctx)
    for attr in ("_chan_cb_line", "_chan_cb_bar", "_chan_cb_distribution",
                 "_chan_cb_heatmap", "_plotting_channel_cb"):
        cb = ctx.widgets.get_or_none(attr)
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
    recalculate_threshold(ctx)
    invalidate_stats_cache(ctx)
    refresh_metric_combo_for_channel(ctx)
    # Keep the band's ThreshFracOn quick-edit showing the new channel's value.
    refresh_thresh_frac_quick_edit(ctx)
    # Announce on the bus. The RedrawCoordinator subscriber coalesces the
    # redraw — visible scope now, the other marked dirty for redraw on tab
    # switch (see redraw_coordinator.py / fold_change_scopes.py).
    ctx.bus.channel_changed.emit(channel_state)
    cdf_lbl = ctx.widgets.get_or_none("_cdf_chan_lbl")
    if cdf_lbl is not None:
        cdf_lbl.setText(f"({target_label} x range)")
    bar_ylim_lbl = ctx.widgets.get_or_none("_bar_ylim_chan_lbl")
    if bar_ylim_lbl is not None:
        bar_ylim_lbl.setText(f"{target_label} y:")


# ── Property/metric combo refresh ───────────────────────────────────────────


def refresh_metric_combo_for_channel(ctx) -> None:
    """Reshape every Property combo to match the active channel.

    For ratio channels each combo collapses to a single ``Calculated
    Val`` entry; for real channels the full intensity set is restored
    with ``smFISH Count`` greyed out when the channel isn't an smFISH
    one. Per-tab combos (heatmap / stats) follow the global channel.

    Replaces ``partial(refresh_metric_combo_for_channel, WellViewerApp)``.

    A8/D1: ctx-native — the channel label composes off ``ctx.state.channel``
    (the ctx ``active_channel_label`` leaf), the per-tab Property combos resolve
    through ``ctx.widgets`` (registered in centre_view / lineplot / barplot), and
    the stats fan-out already takes ``ctx``. No god-object unwrap.
    """
    try:
        channel_label = active_channel_label(ctx)
    except Exception:
        channel_label = ""
    smfish_chan = ctx.state.channel.active_channel
    for attr in ("_plotting_metric_cb", "_metric_cb", "_metric_cb_bar"):
        populate_metric_combo(ctx,
            ctx.widgets.get_or_none(attr),
            channel_entry=channel_label,
            smfish_channel=smfish_chan,
        )
    # Stats has its own channel combo, so its Property combo follows that one.
    refresh_stats_property_combo(ctx)


# ── Channel-selector / dropdown sync ────────────────────────────────────────


def update_channel_selector(ctx) -> None:
    """Refresh the channel dropdown values and selection to match loaded data.

    Replaces ``partial(update_channel_selector, WellViewerApp)``.
    """
    real_labels = [ch.upper() for ch in ctx.state.channel.fluor_channels]
    ratio_labels = [ratio_label_for(ctx, _r) for _r in ctx.state.channel.ratio_metrics]
    # Shape features are channel-independent virtual channels shown under their
    # own dropdown section; their key is the shape_* column itself.
    shape_cols = list(ctx.state.channel.shape_features or [])
    shape_labels = [shape_label_for(c) or c for c in shape_cols]
    labels = (real_labels + ratio_labels + shape_labels) or ["—"]
    # Map the dropdown label back to the underlying channel key used by
    # set_active_channel (real token, ratio:<name> key, or shape_* column).
    ctx.state.channel.label_to_channel_key = {ch.upper(): ch for ch in ctx.state.channel.fluor_channels}
    for r in ctx.state.channel.ratio_metrics:
        ctx.state.channel.label_to_channel_key[ratio_label_for(ctx, r)] = r.key()
    for col, lbl in zip(shape_cols, shape_labels):
        ctx.state.channel.label_to_channel_key[lbl] = col
    # Montage/preview includes the segmentation channel token.
    seg_tok = ctx.state.channel.seg_channel_token or ""
    montage_chans = list(ctx.state.channel.fluor_channels)
    if seg_tok and seg_tok not in montage_chans:
        montage_chans.append(seg_tok)
    montage_labels = [ch.upper() for ch in montage_chans] or ["—"]
    review_labels = [ch.upper() for ch in (ctx.state.channel.review_image_channels or ctx.state.channel.fluor_channels)] or ["—"]
    # Plot tabs: real + ratio channels inline, shape features under their own
    # section header. Image tabs (preview/review) never get shape features.
    plot_sections = [("", real_labels + ratio_labels)]
    if shape_labels:
        plot_sections.append(("Shape Features", shape_labels))
    have_any = bool(real_labels or ratio_labels or shape_labels)
    for attr in ("_chan_cb_line", "_chan_cb_bar", "_chan_cb_distribution",
                 "_chan_cb_heatmap", "_plotting_channel_cb"):
        cb = ctx.widgets.get_or_none(attr)
        if cb is not None:
            if have_any:
                set_combo_values_sectioned(cb, plot_sections)
            else:
                set_combo_values(cb, ["—"])
    _prev_cb = ctx.widgets.get_or_none("_chan_cb_preview")
    if _prev_cb is not None:
        set_combo_values(_prev_cb, montage_labels)
    # The review-image channel combo builds lazily (Segmentation is an Image
    # Browser sub-tab), so it can still be the None default at dataset-load time;
    # it is (re)populated when the sub-tab first builds. See
    # image_browser.controller.build_image_browser_subtab.
    _ri_cb = ctx.widgets.get_or_none("_review_image_chan_cb")
    if _ri_cb is not None:
        set_combo_values(_ri_cb, review_labels)
    active_label = active_channel_label(ctx)

    def _pick_valid(current: str, candidates: list[str], fallback_label: str) -> str:
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
    _plot_var = ctx.widgets.get_or_none("_plot_chan_var")
    plot_label = _pick_valid(active_label, labels, _plot_var.currentText() if _plot_var is not None else "")
    if _plot_var is not None:
        _plot_var.setCurrentText(plot_label)

    # Image tabs: each validates against its own channel universe.
    active_image_label = ctx.state.channel.active_image_channel.upper()
    montage_var = ctx.widgets.get_or_none("_montage_chan_var")
    if montage_var is not None:
        montage_label = _pick_valid(montage_var.currentText(), montage_labels, active_image_label)
        montage_var.setCurrentText(montage_label)
    else:
        montage_label = "—"
    review_var = ctx.widgets.get_or_none("_review_image_chan_var")
    if review_var is not None:
        review_label = _pick_valid(review_var.currentText(), review_labels, active_image_label)
        review_var.setCurrentText(review_label)
    else:
        review_label = "—"

    # Keep active image channel anchored only when the current value is invalid.
    if active_image_label not in montage_labels and active_image_label not in review_labels:
        fallback_image_label = montage_label if montage_label != "—" else review_label
        if fallback_image_label != "—":
            set_active_image_channel(ctx, fallback_image_label.lower())

    # Keep active channel anchored to a valid plot channel.
    if active_label not in labels:
        if plot_label != "—":
            set_active_channel(ctx, channel_key_for_label(ctx, plot_label))
        else:
            # No valid plot channel — clear active state explicitly.
            ctx.state.channel.active_channel = ""

    # Force the visible Plotting's band combo to track the (now-valid)
    # active label.
    global_cb = ctx.widgets.get_or_none("_plotting_channel_cb")
    if global_cb is not None:
        active_label_final = active_channel_label(ctx)
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
    refresh_metric_combo_for_channel(ctx)

    # Same trick for the Segmentation tab's channel combo and the
    # back-compat Movie Montage combo — _pick_valid above preserves
    # currentText when valid, but if _active_image_channel has drifted,
    # the dropdown would disagree with the image actually drawn. Snap
    # the currentIndex so the first user click triggers the redraw.
    active_image_label_final = (ctx.state.channel.active_image_channel or "").upper()
    for _attr in ("_review_image_chan_cb", "_chan_cb_preview"):
        cb = ctx.widgets.get_or_none(_attr)
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
    _chan_var = ctx.widgets.get_or_none("_chan_var")
    if _chan_var is not None:
        from well_viewer.core.tab_controller import current_centre_tab
        tab_label = current_centre_tab(ctx)
        if tab_label == "Segmentation":
            _chan_var.setCurrentText(review_label)
        else:
            _chan_var.setCurrentText(plot_label)


def set_active_image_channel(ctx, channel: str, *, preserve_review_view: bool = False) -> None:
    """Switch image-display channel for Movie Montage and Review Image."""
    channel_switch_debug = _debug_flags.review_image_channel_switch_debug_enabled()
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 3] set_active_image_channel requested=%r current=%r preserve_review_view=%s",
            channel,
            ctx.state.channel.active_image_channel,
            preserve_review_view,
        )
    if not channel or channel == "—":
        return
    # review_image / preview are Image Browser slices — None when it is disabled.
    _review_image = ctx.state.feature_slice("review_image")
    _preview = ctx.state.feature_slice("preview")
    if channel == ctx.state.channel.active_image_channel:
        if preserve_review_view and _review_image is not None:
            _review_image.preserve_view_on_refresh = True
            if _preview is not None and _preview.selected_well:
                from well_viewer.features.image_browser.review_image.renderer import refresh_review_image_ctx
                refresh_review_image_ctx(ctx)
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 3] no-op channel switch; active remains=%r preserve_review_view=%s",
                ctx.state.channel.active_image_channel,
                preserve_review_view,
            )
        return
    prev_channel = ctx.state.channel.active_image_channel
    ctx.state.channel.active_image_channel = channel
    ch_upper = channel.upper()
    ri_chan_var = ctx.widgets.get_or_none("_review_image_chan_var")
    if ri_chan_var is not None:
        ri_chan_var.setCurrentText(ch_upper)
    ri_lut_lbl = ctx.widgets.get_or_none("_review_lut_chan_lbl")
    if ri_lut_lbl is not None:
        ri_lut_lbl.setText(f"{ch_upper} LUT min:")
    saved_review_lut = ctx.state.image_view.lut_by_channel.get(channel)
    ri_lmin = ctx.widgets.get_or_none("_review_lmin_entry")
    ri_lmax = ctx.widgets.get_or_none("_review_lmax_entry")
    if saved_review_lut and ri_lmin is not None and ri_lmax is not None:
        ri_lmin.setText(f"{saved_review_lut[0]:.0f}")
        ri_lmax.setText(f"{saved_review_lut[1]:.0f}")
    if preserve_review_view and _review_image is not None:
        _review_image.preserve_view_on_refresh = True
    if _preview is not None and _preview.selected_well:
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 3->4] reloading preview for selected_well=%r",
                _preview.selected_well,
            )
        from well_viewer.features.image_browser.review_image.renderer import update_preview_ctx
        update_preview_ctx(ctx, _preview.selected_well)
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 3] set_active_image_channel updated before=%r after=%r",
            prev_channel,
            ctx.state.channel.active_image_channel,
        )



def sync_metric_combos(ctx, metric: str) -> None:
    """Push *metric* (an ``active_metric`` key like ``"smfish_count"``) into
    every metric-combo widget the app knows about, without retriggering the
    change handler.

    Used by ``set_active_metric`` after a real metric change and by
    ``persistence.view_state.apply_state`` after a restore-time direct
    assignment. The latter bypasses ``set_active_metric``'s early-return
    when the field is already at the target value, so without this helper
    the dropdown reverts to the combo's default ("Mean Intensity") on app
    reopen and the user has to "wiggle" the dropdown to force a sync.

    A8/D-thresh: ctx-native — every metric combo resolves through
    ``ctx.widgets`` (all five are registered by their tab builders).
    """
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
    label = METRIC_KEY_TO_LABEL.get(metric, "Mean Intensity")
    for attr in ("_metric_var", "_metric_cb", "_metric_cb_bar",
                 "_plotting_metric_cb", "_stats_property_cb"):
        cb = ctx.widgets.get_or_none(attr)
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


def set_active_metric(ctx, metric: str) -> None:
    """Switch the active intensity property and redraw.

    A8/D-thresh: ctx-native — the metric/val_col mutation lands on
    ``ctx.state.channel``, the stale-cache recovery clears
    ``ctx.state.dataset.cache`` directly, and the combo-sync / threshold /
    stats / redraw fan-out all take ``ctx``. No god-object unwrap.
    """
    from well_viewer.core.plot_orchestrator import redraw_app
    from well_viewer.core.selection_controller import invalidate_stats_cache
    channel = ctx.state.channel
    if metric == channel.active_metric:
        return
    channel.active_metric = metric
    if not is_ratio_key(channel.active_val_col) and not is_shape_column(channel.active_val_col):
        channel.active_val_col = f"{channel.active_channel}_{channel.active_metric}"
    active_val_col = channel.active_val_col
    if not is_ratio_key(active_val_col) and not is_shape_column(active_val_col):
        cache = getattr(ctx.state.dataset, "cache", {}) or {}
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
                channel.active_channel,
                metric,
                sorted(first_df.columns.tolist()),
            )
            # Drop the per-well DataFrame cache (the ctx-native equivalent of
            # ``app._invalidate_dataset_cache()`` — a bare ``cache.clear()``).
            ctx.state.dataset.cache.clear()
    sync_metric_combos(ctx, metric)
    refresh_metric_combo_for_channel(ctx)
    refresh_heatmap_aggregation_options(ctx)
    recalculate_threshold(ctx)
    # The quick-edit Steppers take their target — and therefore their caption,
    # range and value — from this metric, so switching it without re-syncing
    # them leaves a box displaying the intensity cutoff while typing into it
    # writes the count one. That is the exact substitution ``set_on_cutoff``
    # exists to prevent, so the refresh belongs here and not only on the tab
    # switch that used to be the first thing to correct it.
    refresh_thresh_frac_quick_edit(ctx)
    invalidate_stats_cache(ctx)
    redraw_app(ctx)
    if ctx.widgets.get_or_none("_bar_tp_cb") is not None:
        ctx.features.redraw(ctx, "Bar Plots")



def populate_metric_combo(
    ctx,
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
    # A8: pure typed-state read — the combo (``cb``) is passed in, and the only
    # app touch is the channel slice, so read it off ``ctx.state`` directly with
    # no god-object unwrap.
    channel = ctx.state.channel
    from well_viewer.data.metric_labels import (
        METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
    )
    mapped_key = channel.label_to_channel_key.get(channel_entry)
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
                channel.active_metric, "Mean Intensity"
            )
            idx = cb.findText(fallback)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        sm_idx = cb.findText("smFISH Count")
        if sm_idx >= 0:
            model = cb.model()
            item = model.item(sm_idx) if model is not None else None
            if item is not None:
                # Case-insensitive by way of the shared helper: the scatter and
                # stats combos pass the displayed label ("GFP") where this one
                # passes the normalised token ("gfp"), and a raw ``in`` test
                # against the normalised set greyed smFISH Count out for good on
                # the upper-case side.
                item.setEnabled(
                    channel_supports_smfish(smfish_channel or "", channel.smfish_channels)
                )
    finally:
        cb.blockSignals(blocked)



def refresh_heatmap_aggregation_options(ctx) -> None:
    """Grey out the threshold-based heatmap aggregations for metrics that have
    no cutoff of their own.

    "Mean above threshold" and "Fraction above threshold" need a cutoff the
    active Property is actually measured against. Mean intensity has one
    (ThreshFracOn) and the spot counts have one (ThreshCountOn), so both offer
    these aggregations — the heat map computes them the same way the bar, line
    and scatter paths already do. Total / Max / Min / Std do not: ThreshFracOn is
    a *mean* intensity, and cutting a total (area × mean) with it would silently
    misapply a number from another scale. Those stay disabled and fall back to
    "Mean (all cells)".

    The rule is ``metric_registry.metric_has_own_cutoff``, not an equality
    against mean intensity: that spelling also disabled the counts, on the
    premise that the only per-channel cutoff was a brightness, which stopped
    being true when counts got their own.

    A8: takes ``ctx``; the heatmap metric combo comes from ``ctx.widgets`` and
    the active metric from typed state, so it names no god-object.
    """
    cb = ctx.widgets.get_or_none("_heatmap_metric_cb")
    if cb is None:
        return
    from well_viewer.features.heatmap.controller import (
        METRIC_MEAN, METRIC_FRACTION,
    )
    from well_viewer.domain.metric_registry import metric_has_own_cutoff
    thresholdable = metric_has_own_cutoff(ctx.state.channel.active_metric)
    model = cb.model()
    if model is None:
        return
    for label in (METRIC_MEAN, METRIC_FRACTION):
        idx = cb.findText(label)
        if idx < 0:
            continue
        item = model.item(idx)
        if item is not None:
            item.setEnabled(thresholdable)
    # If the currently selected aggregation just went unavailable, fall
    # back to "Mean (all cells)" so the heatmap stays valid.
    if not thresholdable:
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



def refresh_stats_property_combo(ctx) -> None:
    """Reshape the stats Property combo to match the stats channel combo.

    A8 teardown: takes ``ctx`` — both combos come from ``ctx.widgets`` and the
    channel fallback from typed state, so it names no god-object."""
    ch_cb = ctx.widgets.get_or_none("_stats_channel_cb")
    m_cb = ctx.widgets.get_or_none("_stats_property_cb")
    if m_cb is None:
        return
    channel = str(ch_cb.currentText() or "") if ch_cb is not None else ""
    # Strip the " (spots)" suffix so smfish-enable matching works.
    smf_base = channel.split(" ")[0] if channel else ctx.state.channel.active_channel
    populate_metric_combo(ctx,
        m_cb, channel_entry=channel, smfish_channel=smf_base,
    )



def on_plot_channel_selected(ctx, source=None) -> None:
    """Channel-switch handler for line/bar/distribution/heatmap plot tabs.

    Each plot tab connects its channel combobox via a lambda that
    captures the source combobox and passes it as ``source`` here.
    ``QObject.sender()`` is unreliable when the slot is a Python
    lambda in PySide6, and ``app._plot_chan_var`` is bound to the
    line-tab combobox only — so falling back to either of those
    leaks the line-tab value when other tabs change channels and
    ``_set_active_channel`` short-circuits because the "new" channel
    equals the current one. Prefer the explicit ``source`` widget.

    A8/D-thresh: fully ctx-native — the source combo's text, the ``sender()``
    fallback (via ``ctx.window`` — the QMainWindow qua Qt object), the
    ``_plot_chan_var`` last-resort read (``ctx.widgets``), and the
    label→key + set_active_channel handoff all route through ``ctx``.
    """
    label = ""
    if source is not None and hasattr(source, "currentText"):
        try:
            label = str(source.currentText() or "")
        except Exception:
            label = ""
    if not label:
        try:
            sender = ctx.window.sender()
        except Exception:
            sender = None
        if sender is not None and hasattr(sender, "currentText"):
            try:
                label = str(sender.currentText() or "")
            except Exception:
                label = ""
    if not label:
        plot_chan_var = ctx.widgets.get_or_none("_plot_chan_var")
        label = plot_chan_var.currentText() if plot_chan_var is not None else ""
    # Route real channels and ratios via the label→key map so ratio
    # selections (e.g. "GFP/MCHERRY") resolve to a ``ratio:<name>`` key.
    set_active_channel(ctx, channel_key_for_label(ctx, label))



def set_ratio_metrics(ctx, ratios: Iterable[RatioMetric]) -> None:
    """Replace the ratio list and rebuild the index + UI."""
    # The typed channel slice sets the catalogue + rebuilds the ratio index in
    # lockstep (the two must never disagree).
    ctx.state.channel.set_ratio_metrics(list(ratios))
    panel = ctx.widgets.get_or_none("_ratio_panel")
    if panel is not None:
        try:
            panel.refresh_from_app()
        except Exception:
            swallowed()  # was: pass (silent except)
    # Cell Gating shows one row per fluor channel + one per ratio. Pull
    # the ratio rows into the table whenever the ratio set changes.
    if ctx.widgets.has("_cell_gating_fluor_gate_edits"):
        try:
            from well_viewer.features.grouping.cell_gating.controller import cell_gating_load_cell_areas
            cell_gating_load_cell_areas(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
    # Ratio additions become virtual channel entries in every plot-tab
    # combo (including Plotting's band chip), so refresh those now —
    # otherwise the new ratio doesn't appear in the dropdown until the
    # next dataset reload.
    # ctx-native: update_channel_selector self-guards on missing widgets
    # (get_or_none per combo), so no app-attr guard is needed. The stale
    # ``hasattr(app, ...)`` here referenced an undefined ``app`` (this function
    # takes ``ctx``), raising NameError and aborting the ratio/channel refresh —
    # which left the channel selectors empty.
    try:
        update_channel_selector(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)



def on_metric_selected_global(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Handle the global Property combo (Plotting's band) change."""
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
    set_active_metric(ctx, metric)



def channel_key_for_label(ctx, label: str) -> str:
    """Map a dropdown label back to the ``_set_active_channel`` argument.

    Real channels return their lowercase token; ratios return their
    ``ratio:<name>`` key.

    A8/D-thresh: ctx-native — pure ``ctx.state.channel`` + the ctx
    ``ratio_label_for`` leaf."""
    channel = ctx.state.channel
    mapping = channel.label_to_channel_key
    if label in mapping:
        return mapping[label]
    # Fallback when the dropdown was populated before ``_label_to_channel_key``
    # was built. Resolve a ratio label to its ``ratio:<name>`` key straight from
    # the ratio metrics (the same label the map is keyed on, disambiguation
    # included) BEFORE assuming a real channel — otherwise a ratio read in this
    # window is lossily lowercased into a bogus real-channel token like
    # ``"gfp/mcherry"`` (audit X5).
    for r in getattr(channel, "ratio_metrics", None) or []:
        if label == ratio_label_for(ctx, r):
            return r.key()
    # Otherwise assume a real channel and lowercase the label.
    return str(label or "").lower()



def get_all_fluor_gates(ctx) -> Dict[str, float]:
    """Get FluorGating thresholds for all loaded channels.

    Returns a dict mapping channel name -> gate threshold, e.g., {"gfp": 10.0, "mcherry": 20.0}.
    Used by aggregate_with_threshold_df to apply consistent gating across all channels.

    A8 teardown: pure typed state (post-R6 the values live on
    ``ctx.state.cell_gating``), so it takes ``ctx`` — the shared render leaf every
    plot's gating reads."""
    return {
        channel: get_fluor_gate(ctx, channel)
        for channel in ctx.state.channel.fluor_channels
    }



def seg_channel_token_of(ctx) -> str:
    """The dataset's nuclear/segmentation channel token, or ``""``.

    Read defensively: the threshold leaves are reached from unit tests that
    stand ``ctx.state`` up as a minimal namespace carrying only the slices that
    leaf needs, and an absent token simply means "no channel is the nuclear
    one". The token itself is seeded from ``pipeline_info.json``'s
    ``nuclear_token`` by :func:`recalculate_threshold`.
    """
    channel_state = getattr(ctx.state, "channel", None)
    return str(getattr(channel_state, "seg_channel_token", "") or "")


def _gating_row_key(ctx, channel: str) -> str:
    """The Cell Gating row key *channel* is stored under.

    The one import of the feature-side resolver, shared by every cutoff accessor
    here — ``well_viewer/core`` carries a hard-capped budget of feature imports
    (``tests/test_guardrails.py::test_core_feature_import_budget``), so the
    accessors route through this rather than importing it apiece.
    """
    from well_viewer.features.grouping.cell_gating.controller import _cell_gating_resolve_key
    return _cell_gating_resolve_key(ctx, channel)


def get_thresh_frac_on(ctx, channel: Optional[str] = None) -> float:
    """Get ThreshFracOn threshold. Uses active channel if not specified.

    This is the ON cutoff for the *fluorescence-intensity* metrics only; the
    integer count metrics carry their own (:func:`get_thresh_count_on`), and
    neither is reached directly by a plot path — they go through
    :func:`on_cutoff_for_column`.

    A8 teardown: pure typed state, so it takes ``ctx``. Post-R6 the per-channel
    ThreshFracOn cutoffs live on the ``CellGatingState`` slice; a channel with no
    configured cutoff falls back to its default — 50.0 for a readout channel,
    0.0 for the nuclear/segmentation channel, per
    :func:`well_viewer.domain.gating_state.default_thresh_frac_on`."""
    if channel is None:
        channel = ctx.state.channel.active_channel
    from well_viewer.domain.gating_state import default_thresh_frac_on
    key = _gating_row_key(ctx, channel)
    tf = ctx.state.cell_gating.thresh_frac_on
    if key in tf:
        return tf[key]
    return default_thresh_frac_on(channel, seg_channel_token_of(ctx))


def get_thresh_count_on(ctx, channel: Optional[str] = None) -> float:
    """Get ThreshCountOn — the ON cutoff for the integer *count* metrics.

    A cell is "on" when its spot tally exceeds this. A channel with no configured
    cutoff falls back to :func:`well_viewer.domain.gating_state.default_thresh_count_on`,
    which sits below the minimum a tally can take so every cell — including the
    zero-spot ones — is in the ON population.

    Read defensively: this is reached from leaves whose unit tests stand the
    cell-gating slice up as a minimal namespace carrying only the map they need.
    """
    if channel is None:
        channel = ctx.state.channel.active_channel
    from well_viewer.domain.gating_state import default_thresh_count_on
    key = _gating_row_key(ctx, channel)
    tc = getattr(ctx.state.cell_gating, "thresh_count_on", None) or {}
    if key in tc:
        return float(tc[key])
    return default_thresh_count_on(channel)


def on_cutoff_for_column(ctx, val_col: str, *, channel: Optional[str] = None) -> float:
    """The ON cutoff *val_col* is measured against — the one resolver.

    Every aggregate plot path (bar, line, scatter-aggregate, heatmap, stats,
    export) reduces its values through ``value > cutoff``, and each used to pick
    that cutoff for itself. They disagreed: a spot tally was handed the channel's
    fluorescence cutoff, which starts at 50.0 and sits above the entire range a
    count can occupy, so the ON population came back empty and the plot drew
    nothing at all.

    ``cutoff_kind_for_column`` classifies the column and this turns the answer
    into a number: shape/morphology keeps every cell, counts read ThreshCountOn,
    intensities read ThreshFracOn. ``tests/test_on_cutoff_sweep.py`` fails when a
    path reaches for one of those accessors directly instead.

    ``channel`` defaults to the token carried by the column itself, so a caller
    holding a per-axis channel (the scatter-aggregate tab) passes it and the ones
    following the app's active channel need not.

    Ratio keys carry no channel token and no cutoff of their own. The three plot
    paths that offer ratios disagree about what a ratio's cutoff should be, so
    each of those handles ratios in front of this resolver rather than folding
    the disagreement in here; a ratio key reaching this function falls back to
    the active channel's ThreshFracOn, which is what it resolved to before.
    """
    from well_viewer.domain.metric_registry import (
        CUTOFF_COUNT, CUTOFF_NONE, cutoff_kind_for_column,
    )
    kind = cutoff_kind_for_column(val_col)
    if kind == CUTOFF_NONE:
        return -1.0
    if channel is None:
        from well_viewer.data.metric_labels import split_metric_col
        parts = split_metric_col(val_col) if isinstance(val_col, str) else None
        channel = parts[0] if parts else ctx.state.channel.active_channel
    if kind == CUTOFF_COUNT:
        return get_thresh_count_on(ctx, channel)
    return get_thresh_frac_on(ctx, channel)


def ensure_cell_gating_tab_built(ctx) -> bool:
    """Materialize the lazily-built Cell Gating sub-tab and return whether its
    widget maps now exist.

    The Cell Gating tab is only constructed on first activation, but persistence
    load and the plotting-tab ThreshFracOn quick-edit both need its QLineEdit
    maps to exist *now* (so the loaded/edited value has a widget to seed). This
    is the single ``app``-bound bundling seam for that lazy build: it force-flips
    the centre stack to the tab's page (``ctx.runtime.centre.build_pending``) and
    builds the sub-tab. Fully ``ctx``-native — the fluor-edits map (the "tab is
    built" signal) reads through ``ctx.runtime.cell_gating`` and the lazy-build
    closure through ``ctx.runtime.centre``, so persistence and the quick-edit
    call this instead of unwrapping the app themselves.
    """
    if ctx.runtime.cell_gating.fluor_gate_edits is None:
        build = ctx.runtime.centre.build_pending
        if callable(build):
            build("Sample Definitions")
        from well_viewer.features.grouping.cell_gating.tab_view import build_cell_gating_subtab
        build_cell_gating_subtab(ctx)
    return ctx.runtime.cell_gating.fluor_gate_edits is not None


def set_thresh_frac_on(ctx, value, channel: Optional[str] = None) -> None:
    """Set ThreshFracOn for a channel (the active channel by default).

    Writes *through* the Cell Gating tab's edit — the single source of truth that
    persistence reads from — then runs the existing change handler (validate →
    save → persist → redraw). This is the write half of the plotting-tab
    quick-edit; editing here stays consistent with the Cell Gating tab,
    Auto-threshold, stats, and exports. No-op until a dataset is loaded.

    A8 / Epic-cellgating: fully ``ctx``-native — the lazy tab build routes
    through :func:`ensure_cell_gating_tab_built` (the one app-bound seam) and the
    edits map is reached via ``ctx.runtime.cell_gating`` (the same stamped
    handle, without naming the god-object here).
    """
    if not ctx.state.dataset.data_dir:
        return
    if channel is None:
        channel = ctx.state.channel.active_channel
    if not channel or channel == "—":
        return
    # The Cell Gating tab is built lazily; materialize it so its edits dict exists.
    if not ensure_cell_gating_tab_built(ctx):
        return
    _write_gating_edit(ctx, channel, value, count=False)


def set_thresh_count_on(ctx, value, channel: Optional[str] = None) -> None:
    """Set ThreshCountOn for a channel (the active channel by default).

    The count-metric half of :func:`set_thresh_frac_on`, writing through the same
    Cell Gating edit surface. Only the smFISH channels carry a count edit, so
    this no-ops for a channel that has none.
    """
    if not ctx.state.dataset.data_dir:
        return
    if channel is None:
        channel = ctx.state.channel.active_channel
    if not channel or channel == "—":
        return
    if not ensure_cell_gating_tab_built(ctx):
        return
    _write_gating_edit(ctx, channel, value, count=True)


def _write_gating_edit(ctx, channel: str, value, *, count: bool) -> None:
    """Push *value* through the Cell Gating edit for *channel* and fire its handler.

    The single feature import behind both cutoff setters — ``well_viewer/core``
    carries a hard-capped budget of feature imports, so the two share one
    statement rather than repeating it.
    """
    from well_viewer.features.grouping.cell_gating.controller import (
        _cell_gating_resolve_key,
        cell_gating_on_threshold_count_on_change,
        cell_gating_on_threshold_frac_on_change,
    )
    runtime = ctx.runtime.cell_gating
    edits = (runtime.thresh_count_edits if count else runtime.thresh_frac_edits) or {}
    edit = edits.get(_cell_gating_resolve_key(ctx, channel))
    if edit is None:
        return
    blocked = edit.blockSignals(True)
    try:
        edit.setText(str(float(value)))
    finally:
        edit.blockSignals(blocked)
    if count:
        cell_gating_on_threshold_count_on_change(ctx)
    else:
        cell_gating_on_threshold_frac_on_change(ctx)


def set_on_cutoff(ctx, value, channel: Optional[str] = None) -> None:
    """Set whichever ON cutoff the active metric is measured against.

    The write half of the quick-edit Steppers. They show one box whose meaning
    follows the selected Property, so the write has to follow it too: pointing
    the display at the count cutoff while the write still landed in
    ThreshFracOn would destroy the user's intensity cutoff and leave the count
    one untouched — a box that reads one number and edits another.

    Decided by the same predicate the display sync uses, ``channel`` included,
    so the two cannot disagree about a channel that has no count cutoff to write.
    """
    if quick_edit_cutoff_is_count(ctx, channel):
        set_thresh_count_on(ctx, value, channel)
    else:
        set_thresh_frac_on(ctx, value, channel)


def quick_edit_cutoff_is_count(ctx, channel: Optional[str] = None) -> bool:
    """True when a quick-edit Stepper is editing the *count* cutoff.

    The Steppers carry no Property picker of their own, so they follow the app's
    active metric — a spot-count readout retargets them onto ThreshCountOn.

    ``channel`` matters for the one quick-edit that has its own channel picker.
    A channel with no ``_smfish_count`` column has no count cutoff and no Cell
    Gating field to write through, so retargeting there would caption the box
    "ON spots", show a value it cannot store, and drop the user's edit on the
    floor. Such a channel keeps the intensity cutoff, which it does have.
    """
    from well_viewer.domain.metric_registry import (
        CUTOFF_COUNT, channel_supports_smfish, cutoff_kind_for_metric_key,
    )
    if cutoff_kind_for_metric_key(ctx.state.channel.active_metric) != CUTOFF_COUNT:
        return False
    if channel is None:
        return True
    return channel_supports_smfish(channel, ctx.state.channel.smfish_channels)


def refresh_thresh_frac_quick_edit(ctx) -> None:
    """Sync the ON-cutoff quick-edit Steppers to the active channel's cutoff.

    There are three of these quick-edits — Plotting's band, the Segmentation
    (Review Image) controls row, and the Image Table controls row — all editing
    one per-channel value. *Which* value follows the selected Property: an
    intensity metric edits ThreshFracOn, a spot count edits ThreshCountOn. The
    caption and the range move with the target in the same pass, so the number on
    screen is always the number being written; the range has to widen *before*
    the value is set, because ``Stepper.setValue`` clamps to it and the count
    default is negative — a stepper still pinned at ``minimum=0`` would silently
    display 0, which means "cells with at least one spot" rather than "every
    cell".

    Signals are blocked so this display sync doesn't loop back through the write
    handler (``Stepper.setValue`` emits ``valueChanged``). No-op for any stepper
    that hasn't been built yet.

    A8/D-thresh: ctx-native — the status-chip refresh and the cutoff read both
    take ``ctx``, and each quick-edit stepper resolves through ``ctx.widgets``.
    """
    _COUNT_TIP = (
        "A cell is ON when its spot count is above this — drives the "
        "fraction-on series and the binary overlay. -1 counts every cell, "
        "including the ones carrying no spots. Same value as the Cell Gating "
        "tab's ThreshCountOn."
    )
    _INTENSITY_TIP = (
        "ON/OFF intensity threshold for the active channel — cells above it "
        "count toward the fraction-above- and mean-above-threshold series. "
        "Edits the same value as the Cell Gating tab's ThreshFracOn."
    )

    def _sync(attr: str, *, channel: Optional[str] = None,
              label_attr: str | None = None) -> None:
        stepper = ctx.widgets.get_or_none(attr)
        if stepper is None:
            return
        count = quick_edit_cutoff_is_count(ctx, channel)
        value = (get_thresh_count_on if count else get_thresh_frac_on)(ctx, channel)
        blocked = stepper.blockSignals(True)
        try:
            stepper.setRange(-1.0 if count else 0.0, 1e9)
            stepper.setDecimals(0 if count else 1)
            stepper.setSingleStep(1.0)
            # The unit inside the box the user types into — the one place they
            # are certainly looking — and what stops a bare "-1" reading as a
            # sentinel or an error rather than "no cut".
            stepper.setSuffix(" spots" if count else "")
            stepper.setValue(float(value))
            stepper.setToolTip(_COUNT_TIP if count else _INTENSITY_TIP)
            # setRange / setDecimals / setSuffix each re-run Stepper._update_size,
            # which resets the width bounds — undoing the setFixedWidth its host
            # row applied at build time and reflowing the band. Re-pin it — but
            # never below the width the stepper just worked out it needs. The
            # host rows pin the token deliberately tighter than the Stepper's
            # own preference, which is fine for "50.0" and clips "20 spots";
            # a two-digit count is an ordinary cutoff to type, so widen for the
            # suffix and keep the tight token everywhere else.
            stepper.setFixedWidth(
                max(theme.Metrics.stepper_w, stepper.maximumWidth()) if count
                else theme.Metrics.stepper_w)
        finally:
            stepper.blockSignals(blocked)
        lbl = ctx.widgets.get_or_none(label_attr) if label_attr else None
        if lbl is not None:
            lbl.setText("ON spots" if count else "ON threshold")

    # Plotting + Segmentation quick-edits have no channel picker of their own —
    # they edit the app's ACTIVE channel, so they follow it.
    _sync("_plotting_thresh_frac_stepper", label_attr="_plotting_thresh_frac_lbl")
    _sync("_review_thresh_frac_stepper", label_attr="_review_thresh_frac_lbl")

    # The Image Table quick-edit has its OWN channel dropdown, so it must follow
    # THAT channel — not the active one. Syncing it to the active channel here was
    # clobbering the user's per-channel value with the active channel's (often 0),
    # so a threshold set on the Image Table appeared to reset to zero.
    # It also decides count-vs-intensity from that channel: a channel with no
    # spot-count column keeps the intensity cutoff, which is the only one it has.
    it_cb = ctx.widgets.get_or_none("_image_table_thresh_chan_cb")
    it_chan = (it_cb.currentText().strip().lower() or None) if it_cb is not None else None
    _sync("_image_table_thresh_frac_stepper", channel=it_chan,
          label_attr="_image_table_thresh_frac_lbl")


def get_fluor_gate(ctx, channel: str) -> float:
    """Get FluorGating threshold for a channel.

    A8 teardown: reads the typed ``CellGatingState`` slice (post-R6), so it takes
    ``ctx``. The slice defaults an unset channel to 0.0 (gate off) — the same
    value the old tab-not-built guard returned — independent of whether the
    lazily-built Cell Gating tab exists."""
    from well_viewer.features.grouping.cell_gating.controller import cell_gating_get_fluor_gate
    return cell_gating_get_fluor_gate(ctx, channel)



def active_channel_label(ctx) -> str:
    """Return the dropdown label corresponding to the active channel.

    A8 teardown: pure ``ctx.state.channel`` + the ctx ``ratio_label_for`` leaf."""
    active_val_col = ctx.state.channel.active_val_col
    if is_ratio_key(active_val_col):
        ratio_name = ratio_name_from_key(active_val_col)
        for r in ctx.state.channel.ratio_metrics:
            if r.name == ratio_name:
                return ratio_label_for(ctx, r)
        return ratio_name.upper()
    if is_shape_column(active_val_col):
        return shape_label_for(active_val_col) or active_val_col
    return ctx.state.channel.active_channel.upper()



def ratio_label_for(ctx, ratio: RatioMetric) -> str:
    """Return the dropdown label for a ratio (e.g. ``"GFP/MCHERRY"``).

    Disambiguates by appending ``[name]`` if two ratios share a label.

    A8 teardown: pure ``ctx.state.channel``."""
    base = ratio.display_label()
    same_base = [r for r in ctx.state.channel.ratio_metrics if r.display_label() == base]
    if len(same_base) > 1:
        return f"{base} [{ratio.name}]"
    return base



def on_metric_selected(ctx) -> None:
    """Handle the per-tab (legacy hidden) metric selector change.

    A8/D-thresh: ctx-native — the metric combo resolves through ``ctx.widgets``
    and the set_active_metric handoff takes ``ctx``."""
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
    metric_var = ctx.widgets.get_or_none("_metric_var")
    metric_label = metric_var.currentText() if metric_var is not None else ""
    metric = METRIC_LABEL_TO_KEY.get(metric_label, "mean_intensity")
    set_active_metric(ctx, metric)



