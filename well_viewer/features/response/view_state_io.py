"""Saving and restoring a Response Curves figure.

A saved plot that restores with a different figure than the one that was saved
is the silent substitution §7 forbids, and it is the one this tab is most
exposed to: the fit is the whole point of the page, and the fit is not
recoverable from the plate. Without a block here a restored response plot came
back with no curve, no EC50 and whatever timepoint the picker seeded from —
looking, in every respect, like a complete restore.

Reached through the registry's ``view_state_io`` hook, so ``persistence`` never
names this feature and a build without it stores nothing rather than erroring.
"""

from __future__ import annotations

from typing import Any

# Module scope, not function-local: ``controller`` imports nothing from here —
# the registry reaches this module lazily — so there is no cycle to work around,
# and a deferred feature import that is not breaking a cycle is one the budget
# guard counts for nothing.
from well_viewer.features.response.controller import (
    refresh_x_combo, update_timepoint_button,
)
from well_viewer.features.response.response_compute import FIT_MODELS, FIT_NONE


def snapshot_response(ctx) -> dict:
    """The tab's own choices: the x variable, the timepoints, the fit.

    The variable is stored by **id**, not by its name or the combo's index. A
    name is renamed and an index moves when another variable is added, and both
    would restore the figure against a different variable while looking as
    though nothing had gone wrong.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return {}
    out: dict[str, Any] = {
        "tp_selections": {str(k): bool(v)
                          for k, v in (slice_.tp_selections or {}).items()},
        "fit_model": str(getattr(slice_, "fit_model", FIT_NONE) or FIT_NONE),
    }
    for key, attribute in (("fit_top", "fit_top"), ("fit_bottom", "fit_bottom")):
        value = getattr(slice_, attribute, None)
        # Stored only when pinned. ``None`` and ``0.0`` are different requests —
        # "fit this plateau" and "this plateau is zero" — and a block that wrote
        # 0.0 for an unset bottom would turn every restored 4PL into a Hill.
        if value is not None:
            out[key] = float(value)
    combo = ctx.widgets.get_or_none("_response_x_cb")
    if combo is not None and combo.currentData():
        out["x_variable_id"] = str(combo.currentData())
    series = ctx.widgets.get_or_none("_response_series_cb")
    if series is not None and series.currentData():
        out["series_variable_id"] = str(series.currentData())
    out["y_metric"] = str(getattr(slice_, "y_metric", "mean") or "mean")
    return out


def apply_response(ctx, state: dict) -> None:
    """Restore *state*, then push it onto the controls.

    Writing the slice alone is half the job: the Fit popover's combo and its two
    line edits are seeded when the popover is built and never read the slice
    again, so a restore that skipped them left a figure drawn with a 4PL above a
    control reading ``None``. That is the defect the bar tab's significance
    picker had, recorded in ARCHITECTURE — the control the user would reach for
    is exactly the one showing the wrong thing.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None or not isinstance(state, dict):
        return

    selections = state.get("tp_selections")
    if isinstance(selections, dict):
        slice_.tp_selections = {str(k): bool(v) for k, v in selections.items()}

    model = str(state.get("fit_model", FIT_NONE) or FIT_NONE)
    slice_.fit_model = model if model in FIT_MODELS else FIT_NONE
    for key in ("fit_top", "fit_bottom"):
        raw = state.get(key)
        try:
            setattr(slice_, key, None if raw is None else float(raw))
        except (TypeError, ValueError):
            # A block hand-edited to nonsense frees the plateau rather than
            # refusing to restore: the rest of the figure is still recoverable,
            # and a free plateau is the tab's own default.
            setattr(slice_, key, None)

    slice_.series_variable_id = str(state.get("series_variable_id", "") or "")
    from well_viewer.features.response.collect import Y_MEAN, Y_METRICS
    y_metric = str(state.get("y_metric", Y_MEAN) or Y_MEAN)
    slice_.y_metric = y_metric if y_metric in Y_METRICS else Y_MEAN
    _sync_controls(ctx, slice_, state.get("x_variable_id"))


def _sync_controls(ctx, slice_, x_variable_id) -> None:
    """Push the restored state onto the widgets, signals blocked.

    Blocked because each of these emits a redraw, and a redraw part-way through
    a restore draws a figure from half the state — which then looks like the
    restore itself was wrong.
    """
    combo = ctx.widgets.get_or_none("_response_x_cb")
    if combo is not None and x_variable_id:
        refresh_x_combo(ctx)
        index = combo.findData(str(x_variable_id))
        if index >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    # After the x combo, never before: ``refresh_series_combo`` excludes
    # whichever variable is on X, so seeding the series against a stale x would
    # offer a list with the wrong variable missing from it.
    series = ctx.widgets.get_or_none("_response_series_cb")
    if series is not None:
        from well_viewer.features.response.controller import refresh_series_combo
        refresh_series_combo(ctx)
        index = series.findData(slice_.series_variable_id or "")
        series.blockSignals(True)
        series.setCurrentIndex(index if index >= 0 else 0)
        series.blockSignals(False)

    y_combo = ctx.widgets.get_or_none("_response_y_cb")
    if y_combo is not None:
        index = y_combo.findData(slice_.y_metric)
        y_combo.blockSignals(True)
        y_combo.setCurrentIndex(index if index >= 0 else 0)
        y_combo.blockSignals(False)

    fit_combo = ctx.widgets.get_or_none("_response_fit_cb")
    if fit_combo is not None:
        index = fit_combo.findData(slice_.fit_model)
        if index >= 0:
            fit_combo.blockSignals(True)
            fit_combo.setCurrentIndex(index)
            fit_combo.blockSignals(False)

    for which in ("top", "bottom"):
        edit = ctx.widgets.get_or_none(f"_response_fit_{which}")
        if edit is None:
            continue
        value = getattr(slice_, f"fit_{which}", None)
        edit.blockSignals(True)
        edit.setText("" if value is None else f"{value:g}")
        edit.blockSignals(False)

    update_timepoint_button(ctx)
