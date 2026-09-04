"""Heatmap layout persistence — ``heatmap_layouts`` section of ``persistence.json``.

The section wraps the layouts in an object so visual settings (cmap, scale
mode, vmin/vmax, rep-set average, log scale, the per-cell ``±`` error, the
sample-map tick labels and the selected timepoint) persist alongside.
Legacy ``heatmap_layouts.json`` sidecars are migrated on first load by ``_doc``.

The timepoint is stored as a value, not as a position in the combo: adding
timepoints to a plate and reloading it (the add-timepoints workflow) rebuilds
that combo, so a saved index comes back naming a different hour. A saved value
this plate does not have restores nothing and says so rather than picking a
neighbour that would be indistinguishable from the real one.
"""

from __future__ import annotations

import logging
import math

from well_viewer.persistence import _doc
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


# The three widgets ``apply_persisted_settings`` treats as proof that the Heat
# Map tab has been built. Named once so the save side and the restore side
# cannot come to disagree about what "built" means.
_BUILT_MARKERS = ("_heatmap_cmap_cb", "_heatmap_scale_cb", "_heatmap_repset_avg_cb")


def _tab_was_built(app) -> bool:
    """Whether the Heat Map tab exists in this session.

    ``ctx.widgets.register`` also stamps ``app._<name>``, so these attributes
    appear exactly when the tab's controls do.
    """
    return any(getattr(app, name, None) is not None for name in _BUILT_MARKERS)


def _collect_settings(app) -> dict:
    hm = app._app_state.feature_slice("heatmap")
    if not _tab_was_built(app):
        # Nothing has been read out of the widgets, because there are none.
        # ``load_from_data_dir`` parks the file's settings on
        # ``hm.persisted_settings`` and ``apply_persisted_settings`` bails on
        # exactly this test, so the typed fields below are all still at their
        # dataclass defaults — reading them here and writing the answer to disk
        # replaced a saved ``{cmap: magma, scale_mode: Manual, vmin: 10.0,
        # vmax: 900.0, repset_avg: True, log_scale: True, tp_value: 72.0}`` with
        # ``{cmap: '', scale_mode: Auto, vmin: None, vmax: None,
        # repset_avg: False, log_scale: False, tp_value: None}``. Any save at
        # all did it: another section's autosave writes the whole document.
        #
        # So hand back what was loaded, unchanged. A reset clears the slice
        # (``reset_scopes.restore_slice_defaults`` puts ``persisted_settings``
        # back to ``None``) before it saves, so a reset still empties the
        # section rather than being undone here.
        saved = getattr(hm, "persisted_settings", None) if hm is not None else None
        return dict(saved) if isinstance(saved, dict) else {}

    def _f(v) -> float | None:
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            return float(v)
        return None
    tp_cb = getattr(app, "_heatmap_tp_cb", None)
    tp_index = int(tp_cb.currentIndex()) if tp_cb is not None else 0
    # The timepoint the index is *pointing at*. The combo is rebuilt from
    # ``tp_values`` every time the plate's timepoints change, so an index alone
    # names a different hour after the add-timepoints workflow extends a run —
    # the value is what the user chose and the index is only where it happened
    # to sit. ``None`` when the tab has never been built (no combo, no values).
    tp_values = list(hm.tp_values or []) if hm is not None else []
    tp_value = (
        float(tp_values[tp_index]) if 0 <= tp_index < len(tp_values) else None
    )
    return {
        # cmap / vmin / vmax are the typed HeatmapState display stamps (D-persist;
        # they moved off the loose ``app._heatmap_*`` runtime attrs).
        "cmap": str((hm.cmap_name if hm is not None else "") or ""),
        "scale_mode": str((hm.scale_mode if hm is not None else "Auto") or "Auto"),
        "vmin": _f(hm.vmin) if hm is not None else None,
        "vmax": _f(hm.vmax) if hm is not None else None,
        "repset_avg": bool(hm.repset_avg if hm is not None else False),
        "log_scale": bool(hm.log_scale if hm is not None else False),
        "show_error": bool(hm.show_error if hm is not None else False),
        # Written even though it defaults to True, so a user who turned it off
        # gets it back off. The *read* side is what carries the default: a file
        # saved before this key existed has no opinion about it, and the tab
        # should behave for that plate the way it does for a new one.
        "label_from_sample_map": bool(
            hm.label_from_sample_map if hm is not None else True),
        # Selected timepoint, by value. ``tp_index`` is still written for
        # older builds reading this file, and is what a file written before
        # ``tp_value`` existed restores from; the value wins whenever it is
        # present.
        "tp_index": tp_index,
        "tp_value": tp_value,
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
    # A plate with no saved arrangements has none — carrying the previous
    # plate's across the swap left layouts in the sidebar naming wells and
    # metrics this dataset may not have, and its persisted settings driving
    # this dataset's heat map. So an absent section clears rather than returns.
    if not isinstance(data, dict):
        data = {}
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


def sync_widgets_to_slice(ctx) -> None:
    """Push the heat-map slice's current display settings into its widgets.

    The slice and the controls have to agree, and only one of the two paths
    that write the slice was pushing. ``apply_persisted_settings`` sets both as
    it reads a saved block; ``reset_scopes.reset_view`` called
    ``restore_slice_defaults`` and stopped, so after a Reset View every heat-map
    control still showed what the user had set while the slice held the
    default. That was survivable for as long as every setting defaulted to
    ``False`` — a checked box over a ``False`` slice is a control that has
    stopped working, but the *figure* was at least drawn from the default.
    ``label_from_sample_map`` is the first setting on this slice that defaults
    to ``True``, so the same gap now turns a feature back **on** underneath an
    unchecked box, which reads as the feature ignoring its own control.

    Safe before the tab is built: every handle resolves through
    ``ctx.widgets`` and a missing one is skipped. Signals are blocked
    throughout — this reflects state the caller has already decided, and a
    handler firing here would persist and redraw in the middle of it.
    """
    hm = ctx.state.feature_slice("heatmap")
    if hm is None:
        return

    def _check(name: str, value: bool) -> None:
        cb = ctx.widgets.get_or_none(name)
        if cb is None:
            return
        blocked = cb.blockSignals(True)
        try:
            cb.setChecked(bool(value))
        finally:
            cb.blockSignals(blocked)

    _check("_heatmap_repset_avg_cb", hm.repset_avg)
    _check("_heatmap_log_scale_cb", hm.log_scale)
    _check("_heatmap_show_error_cb", hm.show_error)
    _check("_heatmap_sample_labels_cb", hm.label_from_sample_map)

    scale_cb = ctx.widgets.get_or_none("_heatmap_scale_cb")
    if scale_cb is not None:
        idx = scale_cb.findText(str(hm.scale_mode or "Auto"))
        if idx >= 0:
            blocked = scale_cb.blockSignals(True)
            try:
                scale_cb.setCurrentIndex(idx)
            finally:
                scale_cb.blockSignals(blocked)

    for name, value in (("_heatmap_vmin_edit", hm.vmin),
                        ("_heatmap_vmax_edit", hm.vmax)):
        edit = ctx.widgets.get_or_none(name)
        if edit is None:
            continue
        blocked = edit.blockSignals(True)
        try:
            edit.setText("" if value is None or not math.isfinite(float(value))
                         else f"{float(value):g}")
        finally:
            edit.blockSignals(blocked)


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

    # The per-cell ``±`` and the sample-map tick labels. The defaults here are
    # the dataclass defaults on ``HeatmapState`` and differ from each other —
    # a file that predates either key restores the tab's own behaviour rather
    # than a blanket False, which for the tick labels would silently turn the
    # feature off for every plate saved before it existed.
    show_error = bool(settings.get("show_error", False))
    if hm is not None:
        hm.show_error = show_error
    err_cb = ctx.widgets.get_or_none("_heatmap_show_error_cb")
    if err_cb is not None:
        blocked = err_cb.blockSignals(True)
        try:
            err_cb.setChecked(show_error)
        finally:
            err_cb.blockSignals(blocked)

    from_sample_map = bool(settings.get("label_from_sample_map", True))
    if hm is not None:
        hm.label_from_sample_map = from_sample_map
    sm_cb = ctx.widgets.get_or_none("_heatmap_sample_labels_cb")
    if sm_cb is not None:
        blocked = sm_cb.blockSignals(True)
        try:
            sm_cb.setChecked(from_sample_map)
        finally:
            sm_cb.blockSignals(blocked)

    # Selected timepoint. The combo's items are populated by
    # ``refresh_heatmap_timepoints`` (called just before this on tab build);
    # the caller's ``_redraw`` then renders the restored timepoint.
    tp_cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    if tp_cb is not None and tp_cb.count():
        _restore_timepoint(hm, tp_cb, settings)

    # Successfully applied — keep the dict around in case the tab rebuilds.
    # (No need to clear it; a subsequent save_to_data_dir overwrites the file
    # with current widget state anyway.)


def _timepoint_index(tp_values: list, target: float) -> int | None:
    """Index of *target* in *tp_values*, or ``None`` when it is not there.

    Compared with a tolerance rather than ``==``: the values make a round trip
    through JSON and through ``f"{v:g} h"`` item text, and a timepoint read
    back from a CSV as ``23.999999999999996`` is the same hour the user chose.
    """
    for i, value in enumerate(tp_values):
        try:
            if math.isclose(float(value), target, rel_tol=1e-9, abs_tol=1e-9):
                return i
        except (TypeError, ValueError):
            continue
    return None


def _restore_timepoint(hm, tp_cb, settings: dict) -> None:
    """Put the timepoint combo back on the saved timepoint.

    By **value** whenever the saved settings carry one. The index this used to
    restore from is a position in a list the dataset owns, and the documented
    add-timepoints workflow (§9.7) re-runs a plate with extra timepoints and
    reloads it — which leaves every saved index pointing at a different hour,
    silently, on a tab whose whole output is one timepoint.

    A saved value that this plate no longer has is *not* fallen back to the
    index: the combo is left where ``refresh_heatmap_timepoints`` put it (the
    shared focus timepoint, else the first), and the mismatch is logged. Falling
    back would restore an arbitrary timepoint that looks exactly like a restored
    one. Only a file written before ``tp_value`` existed uses the index.
    """
    tp_value = settings.get("tp_value")
    if isinstance(tp_value, (int, float)) and not isinstance(tp_value, bool):
        target = float(tp_value)
        if not math.isfinite(target):
            return
        tp_values = list(hm.tp_values or []) if hm is not None else []
        index = _timepoint_index(tp_values, target)
        if index is None:
            _logger.info(
                "heatmap: the saved timepoint (%g) is not among this plate's "
                "timepoints (%s); showing the default one instead.",
                target, ", ".join(f"{float(v):g}" for v in tp_values) or "none",
            )
            return
    else:
        tp_idx = settings.get("tp_index")
        if not isinstance(tp_idx, int) or isinstance(tp_idx, bool):
            return
        index = max(0, min(tp_idx, tp_cb.count() - 1))
    if index >= tp_cb.count():
        return
    # Signals are blocked so we don't re-enter the redraw path here —
    # ``setCurrentIndex`` alone repaints the combo's own "Timepoint: …" text,
    # it isn't a separate label to update in step.
    blocked = tp_cb.blockSignals(True)
    try:
        tp_cb.setCurrentIndex(index)
    finally:
        tp_cb.blockSignals(blocked)
