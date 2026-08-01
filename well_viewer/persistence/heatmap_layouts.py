"""Heatmap layout persistence — ``heatmap_layouts`` section of ``persistence.json``.

The section wraps the layouts in an object so visual settings (cmap, scale
mode, vmin/vmax, rep-set average toggle) persist alongside. Legacy
``heatmap_layouts.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging
import math

from well_viewer.persistence import _doc
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


def _collect_settings(app) -> dict:
    def _f(v):
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            return float(v)
        return None
    slider = getattr(app, "_heatmap_tp_slider", None)
    hm = app._app_state.feature_slice("heatmap")
    return {
        # cmap / vmin / vmax are the typed HeatmapState display stamps (D-persist;
        # they moved off the loose ``app._heatmap_*`` runtime attrs).
        "cmap": str((hm.cmap_name if hm is not None else "") or ""),
        "scale_mode": str((hm.scale_mode if hm is not None else "Auto") or "Auto"),
        "vmin": _f(hm.vmin) if hm is not None else None,
        "vmax": _f(hm.vmax) if hm is not None else None,
        "repset_avg": bool(hm.repset_avg if hm is not None else False),
        "log_scale": bool(hm.log_scale if hm is not None else False),
        # Timepoint-slider position so the scrubbed timepoint restores between
        # sessions (clamped to the live slider range on restore).
        "tp_index": int(slider.value()) if slider is not None else 0,
    }


def save_to_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    layouts = list(ctx.runtime.heatmap.layouts or [])
    payload = {
        "layouts": [lay.to_dict() for lay in layouts],
        "settings": _collect_settings(app),
    }
    _doc.set_section(ctx, "heatmap_layouts", payload)


def load_from_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(ctx, "heatmap_layouts")
    if not isinstance(data, dict):
        return
    from well_viewer.features.heatmap.models import layouts_from_dict
    ctx.runtime.heatmap.layouts = layouts_from_dict(data.get("layouts", []) or [])
    settings = data.get("settings", {}) or {}
    hm = app._app_state.feature_slice("heatmap")
    if isinstance(settings, dict) and hm is not None:
        # Deposit onto the typed slice; ``apply_persisted_settings`` reads it back
        # via ``ctx.state`` (was the loose ``app._heatmap_persisted_settings``).
        hm.persisted_settings = settings
    if hasattr(app, "_heatmap_sidebar_table"):
        try:
            from well_viewer.features.heatmap.sidebar_view import (
                refresh_heatmap_layout_sidebar,
            )
            refresh_heatmap_layout_sidebar(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
    apply_persisted_settings(ctx)


def apply_persisted_settings(ctx) -> None:
    """Push any settings loaded from disk into the heatmap UI widgets.

    Safe to call before the heatmap tab is built — does nothing in that case.

    Fully ctx-native (D-persist): the loaded settings + the cmap/vmin/vmax
    display stamps live on the typed ``ctx.state.heatmap`` slice, and every
    widget handle resolves through ``ctx.widgets`` (they self-register at their
    heatmap tab build sites). Persistence names neither the god-object nor the
    never-serialised ``AppRuntime`` namespace.
    """
    hm = ctx.state.feature_slice("heatmap")
    settings = hm.persisted_settings if hm is not None else None
    if not isinstance(settings, dict) or not settings:
        return
    cmap_cb = ctx.widgets.get_or_none("_heatmap_cmap_cb")
    scale_cb = ctx.widgets.get_or_none("_heatmap_scale_cb")
    vmin_edit = ctx.widgets.get_or_none("_heatmap_vmin_edit")
    vmax_edit = ctx.widgets.get_or_none("_heatmap_vmax_edit")
    rs_cb = ctx.widgets.get_or_none("_heatmap_repset_avg_cb")
    # Bail until the tab is built; init/build will call us again.
    if cmap_cb is None and scale_cb is None and rs_cb is None:
        return

    cmap = str(settings.get("cmap") or "")
    if cmap and cmap_cb is not None:
        # ``_heatmap_cmap_cb`` is a LutSelector, not a QComboBox — use its
        # setLut(name, reversed) API and split the trailing "_r" convention
        # that the heatmap tab uses for reversed colormaps.
        base = cmap[:-2] if cmap.endswith("_r") else cmap
        rev = cmap.endswith("_r")
        blocked = cmap_cb.blockSignals(True)
        try:
            cmap_cb.setLut(base, rev)
        finally:
            cmap_cb.blockSignals(blocked)
        if hm is not None:
            hm.cmap_name = cmap

    scale_mode = str(settings.get("scale_mode") or "Auto")
    if scale_cb is not None:
        idx = scale_cb.findText(scale_mode)
        if idx >= 0:
            blocked = scale_cb.blockSignals(True)
            try:
                scale_cb.setCurrentIndex(idx)
            finally:
                scale_cb.blockSignals(blocked)
    if hm is not None:
        hm.scale_mode = scale_mode

    def _set_edit(edit, key):
        v = settings.get(key)
        if edit is not None and isinstance(v, (int, float)) and math.isfinite(float(v)):
            blocked = edit.blockSignals(True)
            try:
                edit.setText(f"{float(v):g}")
            finally:
                edit.blockSignals(blocked)
            if hm is not None:
                setattr(hm, key, float(v))

    _set_edit(vmin_edit, "vmin")
    _set_edit(vmax_edit, "vmax")

    repset_avg = bool(settings.get("repset_avg", False))
    if hm is not None:
        hm.repset_avg = repset_avg
    if rs_cb is not None:
        blocked = rs_cb.blockSignals(True)
        try:
            rs_cb.setChecked(repset_avg)
        finally:
            rs_cb.blockSignals(blocked)

    log_scale = bool(settings.get("log_scale", False))
    if hm is not None:
        hm.log_scale = log_scale
    log_cb = ctx.widgets.get_or_none("_heatmap_log_scale_cb")
    if log_cb is not None:
        blocked = log_cb.blockSignals(True)
        try:
            log_cb.setChecked(log_scale)
        finally:
            log_cb.blockSignals(blocked)

    # Timepoint slider position. The slider's range is populated by
    # ``refresh_heatmap_timepoints`` (called just before this on tab build), so
    # clamp to the live maximum; the caller's ``_redraw`` then renders the
    # restored timepoint. Signals are blocked so we don't re-enter the redraw
    # path here.
    tp_idx = settings.get("tp_index")
    if isinstance(tp_idx, int):
        slider = ctx.widgets.get_or_none("_heatmap_tp_slider")
        if slider is not None:
            clamped = max(0, min(tp_idx, slider.maximum()))
            blocked = slider.blockSignals(True)
            try:
                slider.setValue(clamped)
            finally:
                slider.blockSignals(blocked)
            tps = list((hm.tp_values if hm is not None else None) or [])
            label = ctx.widgets.get_or_none("_heatmap_tp_label")
            if label is not None and tps and clamped < len(tps):
                label.setText(f"Timepoint: {tps[clamped]:g} h")

    # Successfully applied — keep the dict around in case the tab rebuilds.
    # (No need to clear it; a subsequent save_to_data_dir overwrites the file
    # with current widget state anyway.)
