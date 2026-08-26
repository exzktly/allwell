"""Cell Gating behaviour: the gating workers, the threshold optimizers, the
scatter-gate plumbing, and the handlers the persistence layer re-fires.

Everything here is orchestration over ``ctx`` — it reads and writes the typed
``ctx.state.cell_gating`` slice, drives the two ``QThread`` workers, and pushes
results at the widgets the tab built. It imports the drawing layers
(:mod:`renderer`, :mod:`opt_curve_view`) and the pure math
(:mod:`gating_compute`); it never imports :mod:`tab_view`, which imports *it*.

Public surface (unchanged by the module split — these are the names the core
controllers, the feature registry, and the persistence layer call):

* :func:`cell_gating_load_cell_areas` — repopulate cell-area + per-channel
  fluorescence CDFs from the currently-selected wells.
* :func:`cell_gating_load_threshold_frac_on` — push the typed slice's
  ThreshFracOn values onto the line-edits.
* :func:`cell_gating_get_fluor_gate(ctx, channel)` /
  :func:`cell_gating_get_thresh_frac_on(ctx, channel)` — read the current
  threshold for *channel* from the typed ``CellGatingState`` slice (R6); the
  line-edits are the input surface, not the source of truth.
* :func:`cell_gating_on_gating_change` /
  :func:`cell_gating_on_threshold_frac_on_change` — re-fired by the persistence
  layer when defaults are restored.
* :func:`cell_gating_start_gating_worker` — kick off the background
  ``GatingWorker`` after a programmatic load.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from well_viewer.core import debug_flags as _debug_flags
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates,
    ratio_label_for,
    refresh_thresh_frac_quick_edit,
    seg_channel_token_of,
)
from well_viewer.core.lazy import lazy as _lazy
from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.core.safe import swallowed
from well_viewer.core.selection_controller import invalidate_stats_cache
from well_viewer.data.data_loading import get_rows
from well_viewer.domain.gating_state import (
    default_thresh_count_on, default_thresh_frac_on,
)
from well_viewer.domain.metric_registry import channel_supports_smfish
from well_viewer.domain.selections_model import active_replicate_sets
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.features.home.cell_gating.gating_compute import (
    _cell_gating_first_frame_rows,
    _cell_gating_valid_rows,
    _distinct_timepoints,
    _earliest_timepoint,
    _gate_geom_sig,
    _optimize_thresh_frac_on_between_wells,
    _timepoint_label,
    _valid_values_for_channel,
    apply_fluor_gating,
    format_inclusion_counts,
    format_inclusion_funnel,
    inclusion_counts,
    inclusion_funnel,
)
from well_viewer.features.home.cell_gating.opt_curve_view import (
    _cell_gating_show_opt_curve_popup,
)
from well_viewer.features.home.cell_gating.renderer import _cell_gating_plot_cdf

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_CH_LBL_W = 160
_GATE_EDIT_W = 90
_THRESH_EDIT_W = 90

# Lazy so Cell Gating imports without Image Browser present (keeps both
# deletable); resolves on first call.
apply_review_overrides_to_cache = _lazy(
    "well_viewer.features.image_browser.review_image.controller",
    "apply_review_overrides_to_cache")

logger = logging.getLogger("well_viewer.gating_worker")


class GatingWorker(QThread):
    """Apply cell-gating thresholds to every cached well row off the GUI thread.

    Emits ``progress`` after each well is processed so the status bar advances
    incrementally (1/N → 2/N → …). On clean completion, invalidates the stats
    cache and emits ``finished_ok``.

    The cancellation flag is checked between wells; setting it via
    ``cancel()`` aborts cleanly without touching the stats cache.
    """

    progress = Signal(int, int)  # (current, total)
    finished_ok = Signal()
    failed = Signal(str)         # emitted with the error message on any crash

    def __init__(self, ctx, parent: object | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        # A raise in _run_inner previously killed the QThread with no signal:
        # finished_ok never fired, the UI stayed stuck, and the exception
        # vanished into sys.excepthook (audit E7). Route it through ``failed``
        # so the caller can release the UI and report it.
        try:
            self._run_inner()
        except Exception as exc:
            logger.exception("gating worker failed")
            self.failed.emit(str(exc))

    def _run_inner(self) -> None:
        debug = _debug_flags.cell_gating_debug_enabled()
        # Cell-area gating is retired here — it now lives in the QC layer
        # (QC_Pass at the df_included_mask chokepoint). This worker gates on
        # fluorescence only; QC composes with its Included result downstream.
        fluor_gates = get_all_fluor_gates(self._ctx)
        # "Use scatter gates" mode replaces the per-channel FluorGating: the
        # filtering gate selected on the Cell Gating tab — a single named gate,
        # or the union of all gates — defines the Included population instead of
        # the intensity floors.
        use_scatter = bool(self._ctx.state.cell_gating.use_scatter_gates)
        # Thresholding has its OWN gate selection (independent of the gating
        # layer): cells inside its gate(s) define the "on" population for
        # fraction-on (Gate_On column).
        use_thresh = bool(self._ctx.state.cell_gating.use_scatter_thresh)
        gating_gates = cell_gating_selected_scatter_gates(self._ctx, "gating")
        thresh_gates = cell_gating_selected_scatter_gates(self._ctx, "thresh")
        ratios = self._ctx.state.channel.ratio_index or {}
        labels = list(self._ctx.state.dataset.well_paths)
        total = len(labels)

        if debug:
            logger.info(
                "GatingWorker start: wells=%d fluor_gates=%s",
                total, fluor_gates,
            )

        included_total = 0
        excluded_total = 0

        for idx, label in enumerate(labels):
            if self._cancelled:
                if debug:
                    logger.info(
                        "GatingWorker cancelled at well %d/%d (%s)",
                        idx, total, label,
                    )
                # Cancelling does not un-write the wells already gated. Their
                # frames carry new inclusion columns and the version counter has
                # already moved, but the stats cache was only ever cleared on the
                # success path below — so a cancelled scan used to leave group
                # statistics computed from the pre-gating rows. Clear them here
                # too: a half-applied gating is still an applied gating.
                invalidate_stats_cache(self._ctx)
                return

            df = get_rows(self._ctx, label)
            row_count = 0 if df is None else len(df)
            # Per-well gating math lives in cell_gating_compute.apply_fluor_gating
            # (audit A3) — it writes Included / Gate_Pass / Gate_On and returns
            # the included count. Pure (df + gate config in), so it's unit-tested
            # without a GUI fixture.
            well_included = apply_fluor_gating(
                df,
                fluor_gates=fluor_gates,
                use_scatter=use_scatter,
                gating_gates=gating_gates,
                use_thresh=use_thresh,
                thresh_gates=thresh_gates,
                ratios=ratios,
            )

            # This well's inclusion columns just changed, so any memoized
            # aggregation of it must decline. Bumped per well rather than once
            # at the end: the scan yields to the GUI thread between wells, so a
            # redraw can land mid-scan and must not be served a stale entry for
            # a well already rewritten.
            self._ctx.state.dataset.note_rows_changed()

            included_total += well_included
            excluded_total += row_count - well_included

            if debug:
                logger.info(
                    "GatingWorker progress %d/%d well=%s rows=%d included=%d",
                    idx + 1, total, label, row_count, well_included,
                )

            self.progress.emit(idx + 1, total)
            # Yield to the GUI thread so the progress bar can repaint and the
            # cancel button stays responsive between wells.
            time.sleep(0.01)

        if not self._cancelled:
            # Re-apply user overrides on top of the gating-computed Included
            # so per-cell curation in the Segmentation tab is not clobbered by
            # threshold-based recomputes.
            try:
                apply_review_overrides_to_cache(self._ctx)
            except Exception:
                swallowed()  # was: pass (silent except)
            # CRITICAL: Only invalidate stats cache, NOT _refresh_review_csv_rows.
            # That call deep-copies every cached row across every well and
            # blows up memory by orders of magnitude.
            invalidate_stats_cache(self._ctx)
            if debug:
                logger.info(
                    "GatingWorker finished: wells=%d included=%d excluded=%d",
                    total, included_total, excluded_total,
                )
            self.finished_ok.emit()


# ── public API ────────────────────────────────────────────────────────────

def cell_gating_load_cell_areas(ctx) -> None:
    """Repopulate cell-area + per-channel fluorescence CDFs from selected wells."""
    if not ctx.runtime.cell_gating.fluor_gate_edits is not None:
        return  # tab not built yet
    ctx.runtime.cell_gating.cell_areas = []
    ctx.runtime.cell_gating.fluor_data = {}
    ctx.runtime.cell_gating.ratio_data = {}
    labels = _cell_gating_source_wells(ctx)
    ratios = list(ctx.state.channel.ratio_metrics or [])

    for label in labels:
        df = get_rows(ctx, label)
        if df is None or df.empty:
            continue
        frame_df, _ = _cell_gating_first_frame_rows(df)
        if frame_df is None or frame_df.empty:
            continue
        # Respect the crop region + QC debris gate (not the fluorescence gate —
        # these CDFs are used to set it).
        frame_df = _cell_gating_valid_rows(frame_df)
        if frame_df.empty:
            continue
        if "area_px" in frame_df.columns:
            area = pd.to_numeric(frame_df["area_px"], errors="coerce").to_numpy()
            ctx.runtime.cell_gating.cell_areas.extend(
                float(a) for a in area[np.isfinite(area) & (area > 0)]
            )
        for channel in ctx.state.channel.fluor_channels:
            val_col = f"{channel}_mean_intensity"
            if val_col not in frame_df.columns:
                continue
            v = pd.to_numeric(frame_df[val_col], errors="coerce").to_numpy()
            positive = v[np.isfinite(v) & (v > 0)]
            if positive.size:
                ctx.runtime.cell_gating.fluor_data.setdefault(channel, []).extend(
                    float(x) for x in positive
                )
        for rm in ratios:
            num_col = rm.numerator_col()
            den_col = rm.denominator_col()
            if num_col not in frame_df.columns or den_col not in frame_df.columns:
                continue
            num = pd.to_numeric(frame_df[num_col], errors="coerce").to_numpy()
            den = pd.to_numeric(frame_df[den_col], errors="coerce").to_numpy()
            eps = float(rm.epsilon or 0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = num / (den + eps) if eps else np.where(den != 0, num / den, np.nan)
            keep = np.isfinite(ratio) & (ratio > 0)
            if keep.any():
                ctx.runtime.cell_gating.ratio_data.setdefault(rm.display_label(), []).extend(
                    float(x) for x in ratio[keep]
                )

    _cell_gating_build_channel_controls(ctx)
    _cell_gating_refresh_optimizer_controls(ctx)

    if ctx.runtime.cell_gating.cell_areas:
        ctx.runtime.cell_gating.axes_stack = []
        _cell_gating_plot_cdf(ctx)
        # Name what was actually used. ``_cell_gating_wells`` falls back to every
        # loaded well when nothing is selected, and this line called them
        # "selected well(s)" regardless — so a plate with a cleared selection
        # read "Loaded 24 cells from 4 selected well(s)" with nothing selected,
        # attributing a choice to the user that the fallback had made for them.
        scoped = bool([
            label for label in ctx.state.selection.selected_wells
            if label in ctx.state.dataset.well_paths
        ])
        where = (f"{len(labels)} selected well(s)" if scoped
                 else f"all {len(labels)} wells (no wells selected)")
        ctx.runtime.cell_gating.status_label.setText(
            f"Loaded {len(ctx.runtime.cell_gating.cell_areas)} cells from "
            f"{where}, first frame of first FOV"
        )
    else:
        ctx.runtime.cell_gating.status_label.setText("No cell data found")


def cell_gating_load_threshold_frac_on(ctx) -> None:
    """Push the typed slice's ThreshFracOn values onto the line-edits (R6)."""
    edits = ctx.runtime.cell_gating.thresh_frac_edits or {}
    if not edits:
        return
    for channel, value in ctx.state.cell_gating.thresh_frac_on.items():
        if channel in edits:
            edits[channel].setText(str(value))


def cell_gating_load_threshold_count_on(ctx) -> None:
    """Push the typed slice's ThreshCountOn values onto the line-edits.

    Only the smFISH channels carry a count edit, so a stored cutoff for a channel
    without one simply has no widget to seed — the slice stays the source of
    truth either way.
    """
    edits = ctx.runtime.cell_gating.thresh_count_edits or {}
    if not edits:
        return
    for channel, value in ctx.state.cell_gating.thresh_count_on.items():
        if channel in edits:
            edits[channel].setText(str(value))


def cell_gating_apply_persisted_state(ctx) -> None:
    """Mirror the typed ``CellGatingState`` slice onto the Cell Gating tab's live
    widgets after a sidecar load.

    Persistence writes the loaded gate values + scatter-gate config to
    ``ctx.state.cell_gating`` (the source of truth) and then calls this so the
    tab's input surface reflects it: the FluorGating / ThreshFracOn line-edits,
    the two scatter-gate checkboxes, and the gate-filter buttons / FluorGating
    greyout / summary label. No-op for any widget not built yet — persistence
    never touches widgets itself, keeping it ``ctx``-native (this is the feature
    seam it delegates the mirror to).
    """
    fluor_edits = ctx.runtime.cell_gating.fluor_gate_edits or {}
    for channel, value in ctx.state.cell_gating.fluor_gates.items():
        edit = fluor_edits.get(channel)
        if edit is not None:
            edit.setText(str(value))
    cell_gating_load_threshold_frac_on(ctx)
    cell_gating_load_threshold_count_on(ctx)
    for attr, flag in (
        ("use_scatter_gates_cb", ctx.state.cell_gating.use_scatter_gates),
        ("use_scatter_thresh_cb", ctx.state.cell_gating.use_scatter_thresh),
    ):
        cb = getattr(ctx.runtime.cell_gating, attr, None)
        if cb is not None:
            blocked = cb.blockSignals(True)
            try:
                cb.setChecked(flag)
            finally:
                cb.blockSignals(blocked)
    _cell_gating_refresh_gate_filter(ctx)
    _cell_gating_apply_scatter_gate_mode(ctx)
    _cell_gating_refresh_gate_summary(ctx)


def _cell_gating_sync_gate_state(ctx) -> None:
    """Parse every gate edit into the typed ``CellGatingState`` slice (R6).

    The line-edits are the input surface; the typed slice is the source of
    truth the accessors / persistence / plots read. Both ``editingFinished``
    handlers call this after their validate-all pass, and every programmatic
    ``setText`` path either routes through those handlers or (persistence
    load) writes the slice directly — so slice and widgets cannot diverge.

    Unparsable text leaves that key's last-committed value in place. (The old
    widget-reading accessors returned the 0.0 / 50.0 *defaults* for garbage
    text even though the change handlers refused to act on it — i.e. a stray
    keystroke could silently disable a gate for any concurrent redraw. Keeping
    the last-committed value is the strictly safer semantic.)
    """
    st = ctx.state.cell_gating
    for channel, edit in (ctx.runtime.cell_gating.fluor_gate_edits or {}).items():
        try:
            st.fluor_gates[channel] = float(edit.text())
        except ValueError:
            swallowed(f"cell gating: keep last FluorGating for {channel!r} (non-numeric)")
    for channel, edit in (ctx.runtime.cell_gating.thresh_frac_edits or {}).items():
        try:
            st.thresh_frac_on[channel] = float(edit.text())
        except ValueError:
            swallowed(f"cell gating: keep last ThreshFracOn for {channel!r} (non-numeric)")
    for channel, edit in (ctx.runtime.cell_gating.thresh_count_edits or {}).items():
        try:
            st.thresh_count_on[channel] = float(edit.text())
        except ValueError:
            swallowed(f"cell gating: keep last ThreshCountOn for {channel!r} (non-numeric)")


def _cell_gating_resolve_key(ctx, channel: str) -> str:
    """Map a channel arg onto the key used for the Cell Gating rows.

    Real fluor channels (``"gfp"``, ``"mcherry"``) are stored under their
    bare name. Ratios are stored under their full key (``"ratio:<name>"``).
    Callers can pass either the bare ratio name (``ctx.state.channel.active_channel``
    after a ratio switch) or the full ``ratio:`` key — resolve to whichever
    form actually exists in the typed slice so the user's edit is honoured.

    Matching is case-insensitive. The rows are keyed by the normalised channel
    token (``"gfp"``), but several callers hand over the channel as their
    dropdown *displays* it — the scatter tab derives ``ch_x_base`` from the
    upper-cased axis label, for one. A case-sensitive lookup missed, returned
    the default (0.0 = gate off), and the Cell Gating tab's fluorescence gate
    silently did nothing on those plots.

    A8 teardown: pure ``ctx.state.cell_gating`` (post-R6), so it takes ``ctx``."""
    cg = ctx.state.cell_gating
    # Both maps are keyed alike and are populated together, but either can be
    # the only one filled in (a gate edited before a threshold, a partial
    # persisted block), so consult the union rather than one of them. Read them
    # defensively: this resolver is reached from the review-image and plotting
    # leaves, whose unit tests stand the slice up as a minimal namespace
    # carrying only the map that leaf needs.
    known = list(getattr(cg, "thresh_frac_on", None) or ())
    known += list(getattr(cg, "fluor_gates", None) or ())
    known += list(getattr(cg, "thresh_count_on", None) or ())
    if channel in known:
        return channel
    candidate = f"ratio:{channel}"
    if candidate in known:
        return candidate
    folded = str(channel).strip().lower()
    ratio_folded = f"ratio:{folded}"
    for key in known:
        if str(key).strip().lower() in (folded, ratio_folded):
            return key
    return channel


def cell_gating_get_fluor_gate(ctx, channel: str) -> float:
    """FluorGating threshold for *channel* from the typed slice (0.0 = off)."""
    key = _cell_gating_resolve_key(ctx, channel)
    val = ctx.state.cell_gating.fluor_gates.get(key)
    return 0.0 if val is None else float(val)


def cell_gating_get_thresh_frac_on(ctx, channel: str) -> float:
    """ThreshFracOn cutoff for *channel* from the typed slice.

    A channel with no stored cutoff falls back to its default — 50.0 for a
    readout channel, 0.0 for the nuclear/segmentation channel (see
    ``well_viewer.domain.gating_state.default_thresh_frac_on``)."""
    key = _cell_gating_resolve_key(ctx, channel)
    val = ctx.state.cell_gating.thresh_frac_on.get(key)
    if val is None:
        return default_thresh_frac_on(channel, seg_channel_token_of(ctx))
    return float(val)


def cell_gating_on_gating_change(ctx) -> None:
    try:
        for edit in ctx.runtime.cell_gating.fluor_gate_edits.values():
            float(edit.text())
    except ValueError:
        return

    _cell_gating_sync_gate_state(ctx)
    ctx.runtime.cell_gating.axes_stack = []
    _cell_gating_plot_cdf(ctx)
    _cell_gating_persist_params(ctx)
    cell_gating_start_gating_worker(ctx)


def cell_gating_on_use_scatter_gates_change(ctx) -> None:
    """Toggle scatter-gate inclusion: persist, restyle, and recompute Included.

    Mirrors :func:`cell_gating_on_gating_change` — reads the checkbox into the
    flag, greys the now-inactive FluorGating edits, saves the cell_gating block,
    and kicks the background gating pass so every plot/stat/export picks up the
    new population through the ``Included`` chokepoint.
    """
    cb = ctx.runtime.cell_gating.use_scatter_gates_cb
    ctx.state.cell_gating.use_scatter_gates = bool(cb.isChecked()) if cb is not None else False
    _cell_gating_apply_scatter_gate_mode(ctx)
    _cell_gating_refresh_gate_filter(ctx)  # enable/disable this layer's Gates button
    _cell_gating_refresh_gate_summary(ctx)
    _cell_gating_persist_params(ctx)
    ctx.runtime.cell_gating.last_gate_sig = _scatter_gate_signature(ctx)
    cell_gating_start_gating_worker(ctx)


def cell_gating_on_use_scatter_thresh_change(ctx) -> None:
    """Toggle scatter-gate thresholding (fraction-on by gate).

    Independent of the gating/inclusion toggle: it only changes how fraction-on
    is computed (Gate_On column vs the ThreshFracOn cutoff). Persist the flag and
    re-run the gating pass so the Gate_On column + the stats cache refresh.
    """
    cb = ctx.runtime.cell_gating.use_scatter_thresh_cb
    ctx.state.cell_gating.use_scatter_thresh = bool(cb.isChecked()) if cb is not None else False
    _cell_gating_apply_scatter_gate_mode(ctx)
    _cell_gating_refresh_gate_filter(ctx)  # enable/disable this layer's Gates button
    _cell_gating_refresh_gate_summary(ctx)
    _cell_gating_persist_params(ctx)
    ctx.runtime.cell_gating.last_gate_sig = _scatter_gate_signature(ctx)
    cell_gating_start_gating_worker(ctx)


# The gating (inclusion) and thresholding (fraction-on) layers each keep their
# own gate selection so they can be configured separately.
_GATE_MODES = ("gating", "thresh")
_GATE_CHOICES_ATTR = {
    "gating": "gating_choices",
    "thresh": "thresh_choices",
}
_GATE_BUTTON_ATTR = {
    "gating": "gating_gate_btn",
    "thresh": "thresh_gate_btn",
}
# The "scatter gates" radio of each layer's source pair, and the hint shown
# beneath it while no gate exists to pick. Stamped by the tab builder; absent
# until the tab is built, so every reader guards for None.
_GATE_RADIO_ATTR = {
    "gating": "gating_source_radio",
    "thresh": "thresh_source_radio",
}
_GATE_HINT_ATTR = {
    "gating": "gating_no_gates_hint",
    "thresh": "thresh_no_gates_hint",
}
_GATE_MODE_ACTIVE_ATTR = {
    "gating": "use_scatter_gates",
    "thresh": "use_scatter_thresh",
}


def cell_gating_selected_scatter_gates(ctx, mode: str = "gating") -> list:
    """Gates selected for *mode* ("gating" / "thresh"), as a union.

    Each layer's selection is a set of gate names; ``None`` means "all gates"
    (the default, which also auto-includes gates drawn later). An explicit
    (possibly empty) set selects exactly those names."""
    gates = ctx.state.cell_gating.scatter_gates or []
    choices = getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None)
    if choices is None:
        return list(gates)
    chosen = set(choices)
    return [g for g in gates if str(g.get("name") or "") in chosen]


def _cell_gating_gate_names(ctx) -> list:
    return [str(g.get("name") or "")
            for g in (ctx.state.cell_gating.scatter_gates or [])
            if str(g.get("name") or "")]


def _cell_gating_set_gate_choices(ctx, mode: str, choices) -> None:
    """Commit *mode*'s selection (``None`` = all), then persist + recompute.

    Collapses a full explicit set back to ``None`` so newly drawn gates keep
    being auto-included while the user is effectively at "all"."""
    if choices is not None and set(choices) == set(_cell_gating_gate_names(ctx)):
        choices = None
    setattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode],
            None if choices is None else set(choices))
    _cell_gating_refresh_gate_filter(ctx)
    _cell_gating_refresh_gate_summary(ctx)
    _cell_gating_persist_params(ctx)
    if _cell_gating_scatter_mode_on(ctx):
        ctx.runtime.cell_gating.last_gate_sig = _scatter_gate_signature(ctx)
        cell_gating_start_gating_worker(ctx)


def _cell_gating_gate_button_text(ctx, mode: str) -> str:
    names = _cell_gating_gate_names(ctx)
    if not names:
        return "No gates"
    choices = getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None)
    if choices is None:
        return "All gates"
    chosen = [n for n in names if n in set(choices)]
    if not chosen:
        return "None"
    if len(chosen) == 1:
        return chosen[0]
    return f"{len(chosen)} of {len(names)} gates"


def _cell_gating_refresh_gate_filter(ctx) -> None:
    """Sync both Gates buttons (text + enabled) to the current gates, pruning any
    explicitly-selected names that no longer exist (in each layer's set).

    Also drives each layer's "scatter gates" radio and its "no gates drawn yet"
    hint. Gates are drawn on a *different* tab, so whether that option is
    selectable can change while this tab sits open — deciding it once at build
    time would leave the option permanently dead for anyone who opened Cell
    Gating before drawing their first gate.
    """
    names = set(_cell_gating_gate_names(ctx))
    for mode in _GATE_MODES:
        choices = getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None)
        if choices is not None:
            pruned = set(choices) & names
            setattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode],
                    None if (pruned == names and names) else pruned)
        btn = getattr(ctx.runtime.cell_gating, _GATE_BUTTON_ATTR[mode], None)
        if btn is not None:
            btn.setText(_cell_gating_gate_button_text(ctx, mode))
            # Each Gates button is only relevant when its own layer is active.
            btn.setEnabled(bool(getattr(ctx.state.cell_gating, _GATE_MODE_ACTIVE_ATTR[mode], False)))
        radio = getattr(ctx.runtime.cell_gating, _GATE_RADIO_ATTR[mode], None)
        if radio is not None:
            radio.setEnabled(bool(names))
        hint = getattr(ctx.runtime.cell_gating, _GATE_HINT_ATTR[mode], None)
        if hint is not None:
            hint.setVisible(not names)


def cell_gating_refresh_included_count(ctx, *, pending: bool = False,
                                       failed: bool = False) -> None:
    """Repaint the tab's "Included for analysis" line from the row cache.

    Recomputed rather than fed from ``GatingWorker``'s own tally, for two
    reasons. The worker does not always run — a dataset whose gates are all at
    their defaults skips the pass entirely on load (see
    ``load_controller._kick_off_gating_after_load``), which would leave a
    worker-fed label blank on the commonest path. And its tally counts the
    gating layer alone, which cannot be put on one line with a QC number
    without the two failing to reconcile. Reading ``df_included_mask`` gives the
    population every plot and export actually uses.

    The walk is four vectorised comparisons per cached well — cheaper than the
    ``qc_excluded_count`` pass this tab already runs on every debounced QC
    keystroke. No-ops until the label exists.
    """
    lbl = ctx.runtime.cell_gating.included_count
    if lbl is None:
        return
    frames = list((ctx.state.dataset.cache or {}).values())
    included, total, qc_failed = inclusion_counts(frames)
    ctx.runtime.cell_gating.last_counts = (included, total, qc_failed)
    lbl.setText(format_inclusion_counts(
        included, total, qc_failed, pending=pending, failed=failed))

    # The same population, broken down by the layer that removed each cell. One
    # aggregate number says a gate did something; it cannot say which gate.
    funnel = ctx.runtime.cell_gating.funnel_label
    if funnel is not None:
        funnel.setText(format_inclusion_funnel(inclusion_funnel(frames)))


def _cell_gating_scatter_mode_on(ctx) -> bool:
    """True when either scatter-gate layer (gating or thresholding) is active —
    both consume the selected gate, so the dropdown is relevant for either."""
    return bool(bool(ctx.state.cell_gating.use_scatter_gates)
                or bool(ctx.state.cell_gating.use_scatter_thresh))


def _scatter_gate_signature(ctx):
    """A hashable snapshot of what drives the gating pass — the per-layer gate
    selections (geometry + axis cols) and the two mode flags, so toggling a
    mode or changing either selection re-runs.

    Used to skip a redundant gating pass when re-entering the tab with nothing
    changed — see :func:`cell_gating_sync_scatter_gates`."""
    return (
        bool(bool(ctx.state.cell_gating.use_scatter_gates)),
        bool(bool(ctx.state.cell_gating.use_scatter_thresh)),
        _gate_geom_sig(cell_gating_selected_scatter_gates(ctx, "gating")),
        _gate_geom_sig(cell_gating_selected_scatter_gates(ctx, "thresh")),
    )


def cell_gating_sync_scatter_gates(ctx) -> None:
    """Re-sync the Cell Gating tab to the scatter gates.

    Called immediately whenever a gate is drawn / edited / cleared on the Scatter
    Plot tab (via ``_scatter_gates_changed_cb``) and on tab (re)activation as a
    backstop. Refresh the summary label always; when scatter-gate inclusion is on
    and the gate set changed since the last pass (signature-guarded so repeat
    calls are cheap), re-run the gating worker so every other plot/stat/export
    reflects the new gates. Recomputing on every change is safe for the per-cell
    scatter, which keeps the full cloud by default and — through
    ``collect.scatter_points_gated`` — suppresses its opt-in gate filter while a
    gate is being drawn or edge-dragged, so the gated cluster's own points never
    vanish from under the box being drawn.
    """
    if not ctx.runtime.cell_gating.use_scatter_gates_cb is not None:
        return  # tab not built yet
    _cell_gating_refresh_gate_filter(ctx)
    _cell_gating_apply_scatter_gate_mode(ctx)
    _cell_gating_refresh_gate_summary(ctx)
    if not _cell_gating_scatter_mode_on(ctx):
        return
    sig = _scatter_gate_signature(ctx)
    if sig != ctx.runtime.cell_gating.last_gate_sig:
        ctx.runtime.cell_gating.last_gate_sig = sig
        cell_gating_start_gating_worker(ctx)


def _cell_gating_apply_scatter_gate_mode(ctx) -> None:
    """Grey out the edits each scatter-gate mode replaces, so an editable field
    never misleads:

    * scatter-gate *inclusion* on → the FluorGating edits no longer drive
      Included, so disable them;
    * scatter-gate *thresholding* (fraction-on) on → the gate(s) define the "on"
      population instead of the per-channel ThreshFracOn cut, so disable those.

    (Each layer's Gates button is enabled per-layer in
    _cell_gating_refresh_gate_filter.)"""
    gating_on = bool(bool(ctx.state.cell_gating.use_scatter_gates))
    for edit in (ctx.runtime.cell_gating.fluor_gate_edits or {}).values():
        edit.setEnabled(not gating_on)
    thresh_on = bool(bool(ctx.state.cell_gating.use_scatter_thresh))
    for edit in (ctx.runtime.cell_gating.thresh_frac_edits or {}).values():
        edit.setEnabled(not thresh_on)


def _gate_target_phrase(ctx, mode: str) -> str:
    """Short description of *mode*'s selected gates for the summary line."""
    gates = cell_gating_selected_scatter_gates(ctx, mode)
    if not gates:
        return "no gates selected"
    if getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None) is None:
        return f"all {len(gates)} gate(s)"
    if len(gates) == 1:
        return f"gate “{gates[0].get('name')}”"
    return "gates " + ", ".join(str(g.get("name")) for g in gates)


def _cell_gating_refresh_gate_summary(ctx) -> None:
    """Update the gate summary label describing each active layer's selection.
    Refreshed on build, toggle, gate change, and load."""
    lbl = ctx.runtime.cell_gating.gate_summary
    if lbl is None:
        return
    if not _cell_gating_scatter_mode_on(ctx):
        lbl.setText("")
        return
    if not (ctx.state.cell_gating.scatter_gates or []):
        lbl.setText(
            "No scatter gates drawn yet — draw one on the Scatter Plot tab."
        )
        return
    parts = []
    if bool(ctx.state.cell_gating.use_scatter_gates):
        parts.append(f"Inclusion: {_gate_target_phrase(ctx, 'gating')}")
    if bool(ctx.state.cell_gating.use_scatter_thresh):
        parts.append(f"Fraction-on: {_gate_target_phrase(ctx, 'thresh')}")
    lbl.setText("  •  ".join(parts))


def cell_gating_on_threshold_frac_on_change(ctx) -> None:
    try:
        for edit in ctx.runtime.cell_gating.thresh_frac_edits.values():
            float(edit.text())
        _cell_gating_sync_gate_state(ctx)
        _cell_gating_persist_params(ctx)
        invalidate_stats_cache(ctx)
        # Redraw this tab's own CDFs too. They carry the ON-cutoff line now,
        # and ``cell_gating_on_gating_change`` — the FluorGating sibling ten
        # lines of code away — has always redrawn them for its own line. Without
        # this the cutoff the user just typed stayed where the old one was, on
        # the very panel they are looking at to choose it.
        ctx.runtime.cell_gating.axes_stack = []
        _cell_gating_plot_cdf(ctx)
        # ``_redraw`` only fans out to the default-scope plots (line /
        # distribution / heat map). Bar and scatter redraw via the fold-change
        # scope registry, so also emit ``plot_options_changed`` — otherwise a
        # ThreshFracOn edit leaves the (now default) Bar Plots tab stale.
        redraw_app(ctx)
        ctx.bus.plot_options_changed.emit(ctx.state.plot)
        # Mirror the new value onto Plotting's band quick-edit so it reflects
        # the set threshold (e.g. after auto-threshold or a Cell Gating edit)
        # instead of showing its 50.0 default. Now ctx-native (D-thresh); it
        # no-ops per-stepper for any quick-edit not yet built.
        refresh_thresh_frac_quick_edit(ctx)
    except ValueError:
        swallowed("cell gating: non-numeric ThreshFracOn edit ignored")


def cell_gating_on_threshold_count_on_change(ctx) -> None:
    """ThreshCountOn edit committed — same fan-out as its intensity sibling.

    Only the count metrics read this cutoff, but which metric a plot is drawing
    is not knowable from here, so the redraw is unconditional exactly as it is
    for ThreshFracOn.
    """
    try:
        for edit in ctx.runtime.cell_gating.thresh_count_edits.values():
            float(edit.text())
        _cell_gating_sync_gate_state(ctx)
        _cell_gating_persist_params(ctx)
        invalidate_stats_cache(ctx)
        redraw_app(ctx)
        ctx.bus.plot_options_changed.emit(ctx.state.plot)
        refresh_thresh_frac_quick_edit(ctx)
    except ValueError:
        swallowed("cell gating: non-numeric ThreshCountOn edit ignored")


def cell_gating_start_gating_worker(ctx) -> None:
    # If a previous worker is still running, cancel it before starting
    # a new pass so threshold edits don't pile up.
    prev = ctx.runtime.cell_gating.worker
    if prev is not None and prev.isRunning():
        prev.cancel()
        # Bounded, and on the GUI thread. ``cancel()`` is what makes the restart
        # safe — a cancelled worker disowns whatever it computed — so the wait is
        # only a courtesy to let the thread unwind at its next checkpoint. An
        # unbounded ``wait()`` here froze the window for as long as the previous
        # pass took to finish reading a well, which on a network mount is not a
        # bounded amount of time.
        prev.wait(50)

    worker = GatingWorker(ctx)
    worker.progress.connect(lambda cur, tot: _cell_gating_on_progress(ctx, cur, tot))
    worker.finished_ok.connect(lambda: _cell_gating_on_finished(ctx))
    worker.failed.connect(lambda msg: _cell_gating_on_failed(ctx, msg))
    ctx.runtime.cell_gating.worker = worker
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is not None:
        bar.setRange(0, max(1, len(ctx.state.dataset.well_paths)))
        bar.setValue(0)
        bar.show()
    # Mark the counts in-flight. A cancelled pass emits no terminal signal, but
    # every cancel above is immediately followed by this restart, so the marker
    # is always re-armed and never strands the label on a stale number.
    cell_gating_refresh_included_count(ctx, pending=True)
    worker.start()


class _AutoThresholdWorker(QThread):
    """Run :func:`well_viewer.auto_threshold.compute_auto_thresholds` on a
    background thread so the GUI stays responsive while images load.

    Emits one ``progress(str)`` per logged status line plus a final
    ``done(dict)`` with the computed ``{channel: threshold}`` map (empty
    if nothing was sampled). On failure, ``failed(str)`` carries the
    exception message; ``done`` is still emitted with ``{}`` so callers
    can re-enable the trigger button.
    """

    progress = Signal(str)
    done = Signal(object)        # Dict[str, float]
    failed = Signal(str)

    def __init__(self, ctx, out_dir, channels, *, well_labels=None,
                 in_dir=None, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._out_dir = out_dir
        self._channels = list(channels)
        self._well_labels = list(well_labels) if well_labels else []
        self._in_dir = in_dir
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the pass to stop and disown whatever it produces.

        ``compute_auto_thresholds`` is one long call, so this cannot interrupt
        it mid-well; what it can do is stop the *result* being applied. Without
        it, restarting the pass only waited 50 ms for the previous worker and
        then started another, so two live threads could both reach ``done`` and
        the later-finishing one — not the later-started one — decided the
        thresholds. At teardown it kept a thread alive writing into a pane that
        was going away.
        """
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # QThread override
        # Stream traceback into the log drawer too — the previous "fail
        # silently with just an exception message" behaviour made it
        # impossible to tell *why* the button hung up.
        import logging as _logging
        import traceback as _tb
        _log = _logging.getLogger("well_viewer.data.auto_threshold")
        try:
            from well_viewer.data.auto_threshold import compute_auto_thresholds
            result = compute_auto_thresholds(
                self._out_dir,
                fluor_channels=self._channels,
                progress=lambda msg: self.progress.emit(str(msg)),
                well_labels=self._well_labels or None,
                in_dir=self._in_dir,
                # An explicit predicate, not an exception raised from the
                # progress callback: ``_emit`` wraps that callback in
                # ``except Exception``, so a cancel signalled that way is
                # swallowed and the scan runs to completion regardless.
                should_cancel=lambda: self._cancelled,
            )

        except Exception as exc:
            _log.error(
                "Auto-threshold worker failed: %s\n%s",
                exc, _tb.format_exc(),
            )
            # A cancelled worker owns none of this. The restart path starts a
            # second pass immediately, and ``_on_failed`` / ``_on_done`` re-enable
            # the button, reset its label and post "no thresholds were updated" —
            # so a cancelled worker that then raises would report the *new* pass
            # as finished and failed while it is still running. The cancel check
            # therefore has to cover the failure path, not just the success one.
            if not self._cancelled:
                self.failed.emit(str(exc))
                self.done.emit({})
            return
        if self._cancelled:
            return
        self.done.emit(result or {})


def cell_gating_auto_threshold(ctx) -> None:
    """Kick off the Auto-threshold pass for every loaded fluorescence channel.

    Mirrors what the ``process_microscopy`` pipeline runs as its final
    step: per-cell mean + matched random background pixel pooled across
    every well's first/middle/last timepoint, then Otsu. The result lands
    in ``ctx.runtime.cell_gating.thresh_frac_edits`` (the **ThreshFracOn** column,
    i.e. the fraction-on cut used by downstream plots) and is persisted
    to ``pipeline_info.json::cell_gating.thresh_frac_on`` so the next load
    picks the new defaults up. The per-cell ``FluorGating`` inclusion
    thresholds are never touched by this pass.

    Status streams to the log drawer via the standard logging API (the
    ``logging.Handler`` installed by ``allwell._attach_log_ring_buffer``
    captures every record).
    """
    if not ctx.runtime.cell_gating.fluor_gate_edits is not None:
        return
    out_dir = ctx.state.dataset.data_dir
    if not out_dir:
        try:
            ctx.notify.set_status(
                "Auto-threshold: load a dataset first."
            )
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    channels = [str(ch).strip().lower() for ch in (ctx.state.channel.fluor_channels or []) if str(ch).strip()]
    if not channels:
        try:
            ctx.notify.set_status(
                "Auto-threshold: no fluorescence channels detected in the loaded dataset."
            )
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    # Stop any previous worker still running.
    prev = ctx.runtime.cell_gating.auto_worker
    if prev is not None and prev.isRunning():
        try:
            # Cancel, then wait only briefly. The cancel is what makes the
            # restart safe: the old pass stops at its next well boundary and,
            # whatever it computed, ``run`` refuses to emit ``done`` once
            # cancelled — so the later-*started* pass decides the thresholds
            # rather than the later-*finishing* one. Blocking the GUI thread
            # for the full stop instead would freeze the window for as long as
            # the current well takes to read.
            prev.cancel()
            prev.wait(50)
        except Exception:
            swallowed()  # was: pass (silent except)
    btn = ctx.runtime.cell_gating.auto_btn
    if btn is not None:
        btn.setEnabled(False)
        btn.setText("Auto-thresholding…")

    def _on_progress(msg: str) -> None:
        try:
            ctx.notify.set_status(msg)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _on_done(result) -> None:
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Auto-threshold all channels")
        if not isinstance(result, dict) or not result:
            try:
                ctx.notify.set_status(
                    "Auto-threshold finished — no thresholds were updated."
                )
            except Exception:
                swallowed()  # was: pass (silent except)
            return
        edits = ctx.runtime.cell_gating.thresh_frac_edits or {}
        written: list[str] = []
        for ch, thr in result.items():
            edit = edits.get(ch)
            if edit is None:
                continue
            try:
                edit.setText(f"{float(thr):.2f}")
                written.append(f"{ch.upper()}={float(thr):.2f}")
            except Exception:
                swallowed()  # silent except
                continue
        # Persist + re-render the CDF + kick the gating worker exactly as
        # the user typing a new ThreshFracOn would.
        try:
            cell_gating_on_threshold_frac_on_change(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
        try:
            ctx.notify.set_status(
                "Auto-threshold updated " + ", ".join(written) if written
                else "Auto-threshold finished — no ThreshFracOn edits were touched."
            )
        except Exception:
            swallowed()  # was: pass (silent except)

    def _on_failed(msg: str) -> None:
        import logging as _logging
        _logging.getLogger("well_viewer.data.auto_threshold").error(
            "Auto-threshold failed: %s", msg,
        )
        try:
            ctx.notify.set_status(f"Auto-threshold failed: {msg}")
        except Exception:
            swallowed()  # was: pass (silent except)

    well_labels = list(ctx.state.dataset.well_paths or {})
    in_dir = ctx.state.dataset.in_dir
    import logging as _logging
    _logging.getLogger("well_viewer.data.auto_threshold").info(
        "Auto-threshold: starting — data_dir=%s in_dir=%s wells=%d channels=%s",
        out_dir, in_dir, len(well_labels), ",".join(channels),
    )
    worker = _AutoThresholdWorker(
        ctx, out_dir, channels,
        well_labels=well_labels, in_dir=in_dir,
    )
    worker.progress.connect(_on_progress)
    worker.done.connect(_on_done)
    worker.failed.connect(_on_failed)
    ctx.runtime.cell_gating.auto_worker = worker
    worker.start()


def _cell_gating_opt_fc_inputs(ctx, *, channel_key, well_a, well_b, timepoint, ratios):
    """Resolve the optimizer's fold-change config into optimizer kwargs + a note.

    Reads the optimizer-local FC combos (independent of the plot tabs), resolves
    the chosen control label into a pooled control population at the scan
    timepoint, and each well's baseline-timepoint population. Returns
    ``(kwargs, note)`` where *kwargs* feeds ``_optimize_thresh_frac_on_between_wells``
    / ``_scan_fc_fractions`` (empty ⇒ raw fraction-on) and *note* is a status
    suffix describing the active normalisation.
    """
    from well_viewer.domain.fold_change import resolve_control_wells

    ctrl_combo = ctx.runtime.cell_gating.opt_fc_control_combo
    base_combo = ctx.runtime.cell_gating.opt_fc_baseline_combo
    ctrl_label = str(ctrl_combo.currentText()).strip() if ctrl_combo is not None else ""
    baseline_data = base_combo.currentData() if base_combo is not None else None

    kwargs: dict = {
        "op_ctrl": (ctx.runtime.cell_gating.opt_fc_ctrl_op_combo.currentData()
                    if ctx.runtime.cell_gating.opt_fc_ctrl_op_combo else "ratio") or "ratio",
        "op_t0": (ctx.runtime.cell_gating.opt_fc_t0_op_combo.currentData()
                  if ctx.runtime.cell_gating.opt_fc_t0_op_combo else "ratio") or "ratio",
        "order": (ctx.runtime.cell_gating.opt_fc_order_combo.currentData()
                  if ctx.runtime.cell_gating.opt_fc_order_combo else "ctrl_first") or "ctrl_first",
    }
    notes: list = []

    # vs control — pool the resolved control wells' valid values at this scan tp.
    if ctrl_label and ctrl_label != "—":
        ctrl_wells = resolve_control_wells(ctx, ctrl_label)
        chunks = [
            _valid_values_for_channel(get_rows(ctx, w), channel_key, ratios, timepoint)
            for w in ctrl_wells if w in (ctx.state.dataset.well_paths or {})
        ]
        chunks = [c for c in chunks if c.size]
        if chunks:
            kwargs["ctrl_values"] = np.concatenate(chunks)
            notes.append(f"vs {ctrl_label}")

    # vs baseline timepoint — each well's own population at the baseline tp.
    if baseline_data is not None:
        def _base_vals(well):
            df = get_rows(ctx, well)
            tp = _earliest_timepoint(df) if baseline_data == _OPT_FC_T0_TOKEN else baseline_data
            if tp is None:
                return np.array([], dtype=float)
            return _valid_values_for_channel(df, channel_key, ratios, tp)
        kwargs["base_values_a"] = _base_vals(well_a)
        kwargs["base_values_b"] = _base_vals(well_b)
        notes.append("vs t0" if baseline_data == _OPT_FC_T0_TOKEN
                     else f"vs {_timepoint_label(baseline_data)}")

    note = f" (fold change {', '.join(notes)})" if notes else ""
    return kwargs, note


def cell_gating_optimize_thresh_frac_on(ctx) -> None:
    """Scan a threshold range and apply the best two-well ThreshFracOn cut."""
    label = ctx.runtime.cell_gating.opt_result_label

    def _status(msg: str) -> None:
        if label is not None:
            label.setText(msg)
        try:
            ctx.notify.set_status(msg)
        except Exception:
            swallowed()

    try:
        channel_combo = ctx.runtime.cell_gating.opt_channel_combo
        well_a_combo = ctx.runtime.cell_gating.opt_well_a_combo
        well_b_combo = ctx.runtime.cell_gating.opt_well_b_combo
        channel_key = str(channel_combo.currentData() or channel_combo.currentText()).strip()
        well_a = str(well_a_combo.currentText()).strip()
        well_b = str(well_b_combo.currentText()).strip()
        if not channel_key:
            _status("Optimize ThreshFracOn: choose a channel first.")
            return
        if not well_a or not well_b:
            _status("Optimize ThreshFracOn: choose two wells first.")
            return
        if well_a == well_b:
            _status("Optimize ThreshFracOn: choose two different wells.")
            return
        lo = float(ctx.runtime.cell_gating.opt_min_edit.text())
        hi = float(ctx.runtime.cell_gating.opt_max_edit.text())
        steps = int(float(ctx.runtime.cell_gating.opt_steps_edit.text()))
        tp_combo = ctx.runtime.cell_gating.opt_timepoint_combo
        timepoint = tp_combo.currentData() if tp_combo is not None else None
    except ValueError:
        _status("Optimize ThreshFracOn: Min, Max, and # Points must be numeric.")
        return
    except Exception as exc:
        _status(f"Optimize ThreshFracOn: controls are not ready ({exc}).")
        return
    # # Points is the NUMBER OF thresholds we scan (numpy.linspace's third
    # arg), NOT a step size. A small value (e.g. 2) only samples the
    # endpoints — argmax(|Δ|) then lands at whichever boundary has the
    # bigger diff, which is a useless answer. Refuse the degenerate range
    # with a user-facing message so the field's semantics is unmissable.
    if steps < 10:
        _status(
            "Optimize ThreshFracOn: # Points must be at least 10 — it's the "
            "number of thresholds scanned across the Min–Max range, not a "
            "step size."
        )
        return

    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
    try:
        fc_kwargs, fc_note = _cell_gating_opt_fc_inputs(
            ctx, channel_key=channel_key, well_a=well_a, well_b=well_b,
            timepoint=timepoint, ratios=ratios,
        )
    except Exception as exc:
        _status(f"Optimize ThreshFracOn: fold-change inputs not ready ({exc}).")
        return

    try:
        result = _optimize_thresh_frac_on_between_wells(
            get_rows(ctx, well_a),
            get_rows(ctx, well_b),
            channel_key=channel_key,
            min_threshold=lo,
            max_threshold=hi,
            steps=steps,
            ratios=ratios,
            timepoint=timepoint,
            **fc_kwargs,
        )
    except ValueError as exc:
        _status(f"Optimize ThreshFracOn: {exc}")
        return

    tp_note = ("" if timepoint is None else f" at {_timepoint_label(timepoint)}") + fc_note
    # A zero best difference means the scan range never crossed where the two
    # wells' fraction-on diverge (e.g. the range sits entirely below/above the
    # data). Applying ``lo`` there would be the old "always the smallest value"
    # bug — surface it instead of silently writing a meaningless threshold.
    if not (result.difference > 0):
        _status(
            f"Optimize ThreshFracOn: {well_a} and {well_b} have the same "
            f"fraction-on across [{lo:g}, {hi:g}]{tp_note} for {channel_key}. "
            "Adjust Min/Max to span the data, or pick different wells/timepoint."
        )
        return

    edit = (ctx.runtime.cell_gating.thresh_frac_edits or {}).get(channel_key)
    if edit is None:
        _status(f"Optimize ThreshFracOn: no ThreshFracOn field for {channel_key}.")
        return
    edit.setText(f"{result.threshold:.6g}")
    cell_gating_on_threshold_frac_on_change(ctx)
    _status(
        f"Optimized {channel_key}{tp_note}: threshold={result.threshold:.6g}; "
        f"{well_a} fraction-on={result.frac_a:.3f} (n={result.n_a}), "
        f"{well_b} fraction-on={result.frac_b:.3f} (n={result.n_b}); "
        f"|Δ|/max={result.difference:.3f}."
    )
    # Validation popup: show fraction-on(A), fraction-on(B), and |Δ| as
    # functions of the scanned threshold, with the chosen threshold marked.
    # Lets the user eyeball whether argmax landed where they'd expect, vs.
    # what the full diff curve actually looks like.
    try:
        _cell_gating_show_opt_curve_popup(
            ctx,
            channel_key=channel_key,
            well_a=well_a,
            well_b=well_b,
            timepoint=timepoint,
            lo=lo,
            hi=hi,
            steps=steps,
            result=result,
            fc_kwargs=fc_kwargs,
        )
    except Exception:
        swallowed("cell gating: optimizer curve popup failed")


# ── internal helpers ──────────────────────────────────────────────────────


def _cell_gating_build_channel_controls(ctx) -> None:
    """Rebuild both per-channel threshold tables — one per decision.

    The inclusion floor and the fraction-on cutoff are per-channel numbers of
    the same shape, which is a poor reason to put them in one table: one decides
    whether a cell is in the dataset at all, the other only how the cells that
    are in it get counted. A single table needs a heading that names neither,
    and the tab already had that failure in its original form (both columns
    under the ThreshFracOn header). Each column therefore lives under the
    section that owns the decision it feeds.

    Two symptoms make the split concrete rather than tidy-minded. The columns
    grey out independently — ``_cell_gating_apply_scatter_gate_mode`` disables
    whichever layer a scatter gate has replaced — so a shared table spends much
    of its life half-dead under a neutral heading. And the two are rarely edited
    together: the inclusion floors usually stay at their defaults while the ON
    cutoffs are auto-thresholded and optimised.
    """
    rt = ctx.runtime.cell_gating
    # ``getattr`` rather than attribute access: either host is absent until the
    # tab has been built, and a caller may drive this with only one of them.
    _build_threshold_grid(ctx, getattr(rt, "inner", None), kind="include")
    _build_threshold_grid(ctx, getattr(rt, "thresh_inner", None), kind="on")


def _build_threshold_grid(ctx, host, *, kind: str) -> None:
    """Fill *host* with a ``Channel | <bound>`` table for one decision.

    ``kind`` is ``"include"`` (the FluorGating inclusion floor) or ``"on"`` (the
    ThreshFracOn fraction-on cutoff). The edit widgets are created once and
    cached per channel, so a rebuild re-parents the existing fields rather than
    dropping the user's unsaved keystrokes.
    """
    from PySide6.QtWidgets import QGridLayout

    if host is None:
        return
    inner_layout = host.layout()
    while inner_layout.count():
        item = inner_layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            if w not in _threshold_edit_widgets(ctx):
                w.deleteLater()

    channels = list(ctx.state.channel.fluor_channels)
    # Ratios show up as virtual channels in the plot dropdowns; surface
    # them here too so the user can give each ratio a FluorGating /
    # ThreshFracOn entry. They're keyed by ``ratio.key()`` (e.g.
    # ``ratio:gfp_over_mcherry``) in the edits dicts, distinct from the
    # plain fluor channel keys.
    ratios = list(ctx.state.channel.ratio_metrics or [])
    row_specs: list[tuple[str, str]] = [(ch, ch.upper() + " Channel:") for ch in channels]
    for r in ratios:
        try:
            label = ratio_label_for(ctx, r)
        except Exception:
            label = r.name
        row_specs.append((r.key(), f"{label} (ratio):"))

    if not row_specs:
        lbl = QLabel("No channels loaded", host)
        lbl.setObjectName("Muted")
        inner_layout.addWidget(lbl)
        return

    include = kind == "include"
    # Column headers name the layer each column feeds and the measurement it is
    # made against. The fraction-on table carries two, because an intensity
    # Property and an smFISH Count Property are cut against different units:
    # ThreshFracOn is a brightness, ThreshCountOn a spot tally.
    if include:
        headers = [("Include \u2265   (FluorGating, per-cell MFI)",
                    "Cells whose mean fluorescence intensity in this channel is "
                    "below the floor are excluded from analysis entirely.")]
    else:
        headers = [
            ("ON \u2265   (ThreshFracOn, per-cell MFI)",
             "Included cells at or above this cut count as 'on' when the "
             "selected Property is an intensity. It removes no cells."),
            ("ON >   (ThreshCountOn, spot count)",
             "Included cells above this count as 'on' when the selected "
             "Property is an smFISH Count. -1 counts every cell, including the "
             "ones carrying no spots."),
        ]

    table = QWidget(host)
    grid = QGridLayout(table)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(4)
    grid.setColumnMinimumWidth(0, _CH_LBL_W)
    grid.setColumnStretch(len(headers) + 1, 1)

    # The column header names its layer and the measurement it is made against —
    # per-cell MFI, whatever Property the plotting tabs are showing. That caveat
    # used to be a three-line paragraph at the top of the tab, far from either
    # field it qualifies.
    head = QLabel("Channel", table)
    head.setObjectName("Muted")
    grid.addWidget(head, 0, 0)
    for col, (text, tip) in enumerate(headers, start=1):
        head = QLabel(text, table)
        head.setObjectName("Muted")
        head.setToolTip(tip)
        grid.addWidget(head, 0, col)

    seg_tok = seg_channel_token_of(ctx)
    for r, (channel, row_label) in enumerate(row_specs, start=1):
        ch_lbl = QLabel(row_label, table)
        ch_lbl.setFixedWidth(_CH_LBL_W)
        grid.addWidget(ch_lbl, r, 0)

        # R6: the typed slice is the source of truth — seed the slice with the
        # historical defaults for new rows, and seed the edit's text FROM the
        # slice (so values persisted/loaded before this row was built appear).
        gate_state = ctx.state.cell_gating
        if include:
            edits = ctx.runtime.cell_gating.fluor_gate_edits
            if channel not in edits:
                edit = QLineEdit(str(gate_state.fluor_gates.setdefault(channel, 0.0)), table)
                edit.setFixedWidth(_GATE_EDIT_W)
                edit.editingFinished.connect(
                    lambda _c=ctx: cell_gating_on_gating_change(_c))
                edits[channel] = edit
        else:
            edits = ctx.runtime.cell_gating.thresh_frac_edits
            if channel not in edits:
                # Seed the slice with this channel's default — 50.0 for a
                # readout channel, 0.0 for the nuclear/segmentation one, which
                # has no meaningful ON/OFF brightness cut.
                seeded = gate_state.thresh_frac_on.setdefault(
                    channel, default_thresh_frac_on(channel, seg_tok))
                edit = QLineEdit(str(seeded), table)
                edit.setFixedWidth(_THRESH_EDIT_W)
                edit.editingFinished.connect(
                    lambda _c=ctx: cell_gating_on_threshold_frac_on_change(_c))
                edits[channel] = edit
        edits[channel].setParent(table)
        grid.addWidget(edits[channel], r, 1)

        if not include:
            # The count metrics are cut against their own tally, not a
            # brightness, so they carry a separate cutoff — and only the
            # channels that actually have a ``_smfish_count`` column get the
            # field. The others show an em-dash: "this channel has no count
            # metric" is a different fact from "a cutoff I have not set", and an
            # empty cell in a column of editable cells reads as the second.
            count_edits = ctx.runtime.cell_gating.thresh_count_edits
            if channel_supports_smfish(channel, ctx.state.channel.smfish_channels):
                if channel not in count_edits:
                    seeded_count = gate_state.thresh_count_on.setdefault(
                        channel, default_thresh_count_on(channel))
                    count_edit = QLineEdit(str(seeded_count), table)
                    count_edit.setFixedWidth(_THRESH_EDIT_W)
                    count_edit.setToolTip(
                        "A cell is ON when its spot count is above this. -1 "
                        "counts every cell, including the ones carrying no spots."
                    )
                    count_edit.editingFinished.connect(
                        lambda _c=ctx: cell_gating_on_threshold_count_on_change(_c)
                    )
                    count_edits[channel] = count_edit
                count_edits[channel].setParent(table)
                grid.addWidget(count_edits[channel], r, 2)
            else:
                absent = QLabel("\u2014", table)
                absent.setObjectName("Muted")
                absent.setToolTip(
                    "This channel has no smFISH count column, so there is no "
                    "count cutoff to set.")
                grid.addWidget(absent, r, 2)

    inner_layout.addWidget(table)


def _threshold_edit_widgets(ctx) -> set:
    """Every cached threshold edit, so a rebuild re-parents rather than deletes
    them — a deleted field takes the user's unsaved keystrokes with it."""
    rt = ctx.runtime.cell_gating
    return (set((rt.fluor_gate_edits or {}).values())
            | set((rt.thresh_frac_edits or {}).values())
            | set((rt.thresh_count_edits or {}).values()))


def _cell_gating_optimizer_channel_specs(ctx) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = [
        (ch, ch.upper()) for ch in list(ctx.state.channel.fluor_channels)
    ]
    for ratio in list(ctx.state.channel.ratio_metrics or []):
        try:
            label = ratio_label_for(ctx, ratio)
        except Exception:
            label = ratio.name
        specs.append((ratio.key(), f"{label} (ratio)"))
    return specs


def _cell_gating_refresh_optimizer_controls(ctx) -> None:
    """Refresh optimizer channel/well dropdowns after data or ratios change."""
    channel_combo = ctx.runtime.cell_gating.opt_channel_combo
    well_a_combo = ctx.runtime.cell_gating.opt_well_a_combo
    well_b_combo = ctx.runtime.cell_gating.opt_well_b_combo
    if channel_combo is None or well_a_combo is None or well_b_combo is None:
        return

    prev_channel = channel_combo.currentData()
    channel_combo.blockSignals(True)
    channel_combo.clear()
    for key, label in _cell_gating_optimizer_channel_specs(ctx):
        channel_combo.addItem(label, key)
    if prev_channel:
        idx = channel_combo.findData(prev_channel)
        if idx >= 0:
            channel_combo.setCurrentIndex(idx)
    channel_combo.blockSignals(False)

    wells = sorted(list(ctx.state.dataset.well_paths or {}), key=parse_rc)
    for combo in (well_a_combo, well_b_combo):
        prev = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(wells)
        if prev:
            idx = combo.findText(prev)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)
    if well_b_combo.count() > 1 and well_b_combo.currentIndex() == well_a_combo.currentIndex():
        well_b_combo.setCurrentIndex(1)

    btn = ctx.runtime.cell_gating.opt_btn
    if btn is not None:
        btn.setEnabled(bool(channel_combo.count() and len(wells) >= 2))

    _cell_gating_refresh_opt_timepoints(ctx)
    _cell_gating_refresh_opt_fc_controls(ctx)
    _cell_gating_autofill_opt_range(ctx)


def _cell_gating_on_opt_scope_changed(ctx) -> None:
    """React to a channel/well change: re-list timepoints, then re-fit the range."""
    _cell_gating_refresh_opt_timepoints(ctx)
    _cell_gating_refresh_opt_fc_controls(ctx)
    _cell_gating_autofill_opt_range(ctx)


_OPT_FC_T0_TOKEN = "__t0__"


def _cell_gating_refresh_opt_fc_controls(ctx) -> None:
    """Populate the optimizer's fold-change Control + Baseline combos.

    Control candidates mirror the plot tabs' Control combo (well tokens +
    replicate-set names, with the ``" (well)"`` collision suffix) via
    ``core.fold_change_controls_view._all_member_labels``. Baseline offers "—",
    "t0 (first timepoint)", and the explicit timepoints shared by wells A/B.
    Selections are preserved across refreshes; this state is local to the
    optimizer and never touches ``ctx.state.plot.fc_*``.
    """
    from well_viewer.core.fold_change_controls_view import _all_member_labels

    ctrl = ctx.runtime.cell_gating.opt_fc_control_combo
    base = ctx.runtime.cell_gating.opt_fc_baseline_combo
    if ctrl is not None:
        prev = ctrl.currentText()
        items = ["—", *_all_member_labels(ctx)]
        if [ctrl.itemText(i) for i in range(ctrl.count())] != items:
            blocked = ctrl.blockSignals(True)
            try:
                ctrl.clear()
                ctrl.addItems(items)
                idx = ctrl.findText(prev)
                ctrl.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                ctrl.blockSignals(blocked)

    if base is not None:
        well_a_combo = ctx.runtime.cell_gating.opt_well_a_combo
        well_b_combo = ctx.runtime.cell_gating.opt_well_b_combo
        seen: set = set()
        tps: list = []
        for combo_w in (well_a_combo, well_b_combo):
            if combo_w is None:
                continue
            label = str(combo_w.currentText()).strip()
            if not label or label not in (ctx.state.dataset.well_paths or {}):
                continue
            for tp in _distinct_timepoints(get_rows(ctx, label)):
                if tp not in seen:
                    seen.add(tp)
                    tps.append(tp)
        tps.sort(key=lambda t: (0, t[1]) if t[0] == "hours" else (1, str(t[1])))
        prev = base.currentData()
        blocked = base.blockSignals(True)
        try:
            base.clear()
            base.addItem("—", None)
            base.addItem("t0 (first timepoint)", _OPT_FC_T0_TOKEN)
            for tp in tps:
                base.addItem(_timepoint_label(tp), tp)
            idx = base.findData(prev) if prev is not None else 0
            base.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            base.blockSignals(blocked)


def _cell_gating_refresh_opt_timepoints(ctx) -> None:
    """Populate the optimizer timepoint dropdown from the two selected wells."""
    combo = ctx.runtime.cell_gating.opt_timepoint_combo
    well_a_combo = ctx.runtime.cell_gating.opt_well_a_combo
    well_b_combo = ctx.runtime.cell_gating.opt_well_b_combo
    if combo is None or well_a_combo is None or well_b_combo is None:
        return
    seen: set = set()
    tps: list = []
    for combo_w in (well_a_combo, well_b_combo):
        label = str(combo_w.currentText()).strip()
        if not label or label not in (ctx.state.dataset.well_paths or {}):
            continue
        for tp in _distinct_timepoints(get_rows(ctx, label)):
            if tp not in seen:
                seen.add(tp)
                tps.append(tp)
    tps.sort(key=lambda t: (0, t[1]) if t[0] == "hours" else (1, str(t[1])))

    prev = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("All timepoints", None)
    for tp in tps:
        combo.addItem(_timepoint_label(tp), tp)
    if prev is not None:
        # Qt's findData can't match a Python tuple stored as itemData, so search
        # by value to preserve the user's timepoint across refreshes.
        restored = next(
            (i for i in range(combo.count()) if combo.itemData(i) == prev), 0
        )
        combo.setCurrentIndex(restored)
    combo.blockSignals(False)


def _cell_gating_autofill_opt_range(ctx) -> None:
    """Fit Min/Max to the selected channel's data range over the two wells.

    Pools the included finite values from Well A and Well B (at the chosen
    timepoint) and writes their min/max into the Min/Max edits, so the scan spans
    where fraction-on actually changes rather than the hardcoded 0–100 default.
    """
    min_edit = ctx.runtime.cell_gating.opt_min_edit
    max_edit = ctx.runtime.cell_gating.opt_max_edit
    channel_combo = ctx.runtime.cell_gating.opt_channel_combo
    well_a_combo = ctx.runtime.cell_gating.opt_well_a_combo
    well_b_combo = ctx.runtime.cell_gating.opt_well_b_combo
    tp_combo = ctx.runtime.cell_gating.opt_timepoint_combo
    if not all((min_edit, max_edit, channel_combo, well_a_combo, well_b_combo)):
        return
    channel_key = str(channel_combo.currentData() or channel_combo.currentText()).strip()
    well_a = str(well_a_combo.currentText()).strip()
    well_b = str(well_b_combo.currentText()).strip()
    valid = ctx.state.dataset.well_paths or {}
    if not channel_key or well_a not in valid or well_b not in valid:
        return
    timepoint = tp_combo.currentData() if tp_combo is not None else None
    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
    try:
        va = _valid_values_for_channel(get_rows(ctx, well_a), channel_key, ratios, timepoint)
        vb = _valid_values_for_channel(get_rows(ctx, well_b), channel_key, ratios, timepoint)
    except Exception:
        swallowed("cell gating: optimizer range autofill failed")
        return
    pooled = np.concatenate([va, vb]) if (va.size or vb.size) else np.array([], dtype=float)
    if pooled.size == 0:
        return
    lo = float(np.nanmin(pooled))
    hi = float(np.nanmax(pooled))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return
    if hi <= lo:
        hi = lo + 1.0
    min_edit.setText(f"{lo:.6g}")
    max_edit.setText(f"{hi:.6g}")


def _cell_gating_source_wells(ctx) -> list[str]:
    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)

    if active_rsets:
        seen: set[str] = set()
        ordered: list[str] = []
        for rset in active_rsets:
            for well in rset.wells:
                if well in ctx.state.dataset.well_paths and well not in seen:
                    seen.add(well)
                    ordered.append(well)
        return sorted(ordered, key=parse_rc)

    selected = [
        label for label in ctx.state.selection.selected_wells
        if label in ctx.state.dataset.well_paths
    ]
    # Fall back to every loaded well so the CDFs render on the first open
    # after a data load (before the user has made an explicit selection).
    # Once they pick wells in the sidebar, that selection scopes the plot.
    if not selected:
        selected = list(ctx.state.dataset.well_paths)
    return sorted(selected, key=parse_rc)


def _cell_gating_on_progress(ctx, current: int, total: int) -> None:
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is None:
        return
    if bar.maximum() != total:
        bar.setRange(0, total)
    bar.setValue(current)


def cell_gating_recompute_now(ctx) -> bool:
    """Run the gating pass **synchronously**, on the calling thread.

    The normal path is a ``QThread`` whose completion is delivered by a queued
    signal, which is right for a user editing a threshold and wrong for a caller
    that must read the result before it returns. The Saved Plots export is that
    caller: it replays a saved plot's gating and captures the figure a line
    later, and with the async path that capture reads the *previous* pass's
    ``Included`` flags — so the figure is drawn under a gating that is neither
    the plot's nor the user's, and nothing on it says so.

    Any worker already in flight is cancelled first, so the pass this runs is the
    one whose result survives. Returns False when the recompute could not run, so
    a caller that depends on it can decline rather than capture the wrong thing.
    """
    prev = ctx.runtime.cell_gating.worker
    if prev is not None and prev.isRunning():
        prev.cancel()
        # A literal, deliberately: ``test_state_lifecycle`` proves this bound is
        # small by reading it out of the syntax tree, and a named constant is an
        # ``ast.Name`` it cannot evaluate. Same 50 ms and same reasoning as the
        # restart in ``cell_gating_start_gating_worker`` — ``cancel()`` is the
        # mechanism, the wait is a courtesy to let the thread reach a checkpoint.
        prev.wait(50)
    worker = GatingWorker(ctx)
    try:
        worker._run_inner()
    except Exception:
        logger.exception("gating: synchronous recompute failed")
        return False
    # The same three things ``_cell_gating_on_finished`` does, minus the progress
    # bar it never showed: the pass has rewritten Included / Gate_Pass / Gate_On
    # on every cached frame, so any statistic computed from them is stale.
    invalidate_stats_cache(ctx)
    cell_gating_refresh_included_count(ctx)
    return True


def _cell_gating_on_finished(ctx) -> None:
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is not None:
        bar.setValue(0)
        bar.hide()
    # The pass just rewrote Included / Gate_Pass / Gate_On on every cached row
    # frame, so any group statistic computed from them is stale by definition.
    # Belt to the cache key's braces: the key folds in the gate state, but it
    # cannot know about a rewrite that leaves the key's inputs untouched (a QC
    # or crop layer moving underneath, say), and a stale aggregate is invisible
    # — the plot simply redraws the old numbers.
    invalidate_stats_cache(ctx)
    cell_gating_refresh_included_count(ctx)
    redraw_app(ctx)


def _cell_gating_on_failed(ctx, msg: str) -> None:
    # Release the UI (hide the progress bar) and surface the error instead of
    # leaving the status bar stuck mid-pass (audit E7).
    bar = ctx.widgets.get_or_none("_progress_bar")
    if bar is not None:
        bar.setValue(0)
        bar.hide()
    logger.error("Cell gating failed: %s", msg)
    # Mark the counts as describing settings that were never applied, rather
    # than leaving a confident-looking number behind a failed pass.
    cell_gating_refresh_included_count(ctx, failed=True)
    try:
        ctx.notify.set_status(f"Cell gating failed: {msg}")
    except Exception:
        logger.exception("failed to display gating-error status")


def _cell_gating_persist_params(ctx) -> None:
    """Save current gating values into pipeline_info.json (no-op when at defaults)."""
    from well_viewer.persistence.cell_gating import save_to_pipeline_info
    try:
        save_to_pipeline_info(ctx)
    except Exception:
        logger.exception("Failed to save gating params to pipeline_info.json")
