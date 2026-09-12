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
from review.features.response.controller import (
    FRACTION_LABEL, refresh_response_metric_combo, refresh_series_control,
    refresh_x_combo,
    update_timepoint_button,
)
from review.features.response.response_compute import FIT_MODELS, FIT_NONE


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
    # From the slice, which the controller keeps in step with the combo — and
    # which is what the member seam reads. A snapshot taken from the widget
    # alone could not be taken at all on a headless path.
    if slice_.x_variable_id:
        out["x_variable_id"] = str(slice_.x_variable_id)
    # From the slice, which is where the Series menu keeps its answer — and in
    # the user's own pick order, which is the order the curves are labelled in.
    if slice_.series_variable_ids:
        out["series_variable_ids"] = [str(v) for v in slice_.series_variable_ids]
    # The tab's own Channel and Property, by their combo labels — the same
    # strings ``populate_metric_combo`` produces, so a restore needs no second
    # vocabulary. Empty means "was following the shared choice", which restores
    # as following it rather than pinning whatever it happened to be.
    for key, attribute in (("channel", "channel"),
                           ("metric_label", "metric_label")):
        value = str(getattr(slice_, attribute, "") or "")
        if value:
            out[key] = value
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

    raw_series = state.get("series_variable_ids")
    if isinstance(raw_series, (list, tuple)):
        slice_.series_variable_ids = [str(v) for v in raw_series if v]
    else:
        # A plot saved by the release whose Series was one combo. Read the
        # single id rather than dropping it: a restored figure with no split is
        # a different figure from the one that was saved, and it looks like a
        # complete restore.
        one = str(state.get("series_variable_id", "") or "")
        slice_.series_variable_ids = [one] if one else []
    slice_.channel = str(state.get("channel", "") or "")
    metric_label = str(state.get("metric_label", "") or "")
    if not metric_label and str(state.get("y_metric", "") or "") == "fraction":
        # A plot saved by the release that carried a separate Y combo. Without
        # this the figure reopens on the mean with nothing saying the restore
        # was partial — a restored plot that is not the plot that was saved,
        # which is the silent substitution this whole block exists to prevent.
        metric_label = FRACTION_LABEL
    slice_.metric_label = metric_label
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
            # The slice mirrors the combo, and the signals that normally keep
            # it in step are blocked here. Left unwritten, the axis the member
            # seam reads would be whatever the combo had settled on before the
            # restore — so a saved plot grouped by Cell line would come back
            # crossed against the wrong variable, or not crossed at all.
            slice_.x_variable_id = str(x_variable_id)

    # After the x combo, never before: ``refresh_series_control`` drops whichever
    # variable is on X, so reconciling the series against a stale x would drop
    # the wrong one.
    refresh_series_control(ctx)

    ch_combo = ctx.widgets.get_or_none("_response_ch_cb")
    if ch_combo is not None and slice_.channel:
        index = ch_combo.findText(slice_.channel)
        if index >= 0:
            ch_combo.blockSignals(True)
            ch_combo.setCurrentIndex(index)
            ch_combo.blockSignals(False)
    # After the channel, never before: the Property list is reshaped for the
    # channel, so seeding it against a stale one offers a list the restored
    # label may not be in.
    metric_combo = ctx.widgets.get_or_none("_response_metric_cb")
    if metric_combo is not None:
        refresh_response_metric_combo(ctx)
        if slice_.metric_label:
            index = metric_combo.findText(slice_.metric_label)
            if index >= 0:
                metric_combo.blockSignals(True)
                metric_combo.setCurrentIndex(index)
                metric_combo.blockSignals(False)

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
