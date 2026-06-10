"""Cell Gating tab builder (Qt port).

Public surface (matches the convention used by every other
``tabs/*_tab_view.py``):

* :func:`build_cell_gating_tab` — construct the UI inside *parent*;
  stash widget refs and tab state on ``app._cell_gating_*``.
* :func:`cell_gating_load_cell_areas` — repopulate cell-area + per-channel
  fluorescence CDFs from the currently-selected wells.
* :func:`cell_gating_load_threshold_frac_on` — re-fill the
  ThreshFracOn line-edits from ``app._thresh_frac_on_saved``.
* :func:`cell_gating_get_fluor_gate(app, channel)` /
  :func:`cell_gating_get_thresh_frac_on(app, channel)` — read the current
  threshold for *channel* from the UI.
* :func:`cell_gating_on_gating_change` /
  :func:`cell_gating_on_threshold_frac_on_change` — re-fired by the
  persistence layer when defaults are restored.
* :func:`cell_gating_start_gating_worker` — kick off the background
  ``GatingWorker`` after a programmatic load.
* :func:`cell_gating_update_theme` — repaint figure facecolor after a
  theme change (currently a stub since light theme is parked).

The original implementation lived in ``well_viewer/cell_gating_tab.py``
as a ``CellGatingTab`` QWidget subclass. Phase 15.1 flattened it onto
this builder + module-level helper shape to match every other tab.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ui.theme import get_color
from well_viewer.core import debug_flags as _debug_flags
from well_viewer.core.ui_helpers import attach_plot_toolbar, install_canvas_wheel_scroll
from well_viewer.features.review_image.controller import apply_review_overrides_to_cache


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

    def __init__(self, app, parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from well_viewer.core import status_signal as _status_signal
        _status_signal.busy_push()
        try:
            self._run_inner()
        except Exception:
            _status_signal.signal_failed()
            raise
        finally:
            _status_signal.busy_pop()

    def _run_inner(self) -> None:
        debug = _debug_flags.cell_gating_debug_enabled()
        # Cell-area gating is retired here — it now lives in the QC layer
        # (QC_Pass at the df_included_mask chokepoint). This worker gates on
        # fluorescence only; QC composes with its Included result downstream.
        fluor_gates = self._app._get_all_fluor_gates()
        labels = list(self._app._app_state.dataset.well_paths)
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
                return

            df = self._app._get_rows(label)
            row_count = 0 if df is None else len(df)
            if df is None or df.empty:
                well_included = 0
            else:
                mask = pd.Series(True, index=df.index)
                for channel, gate_threshold in fluor_gates.items():
                    col = f"{channel}_mean_intensity"
                    if col not in df.columns:
                        mask = pd.Series(False, index=df.index)
                        break
                    v = pd.to_numeric(df[col], errors="coerce")
                    mask &= v.notna() & (v > gate_threshold)
                df["Included"] = mask.astype(int)
                well_included = int(mask.sum())

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
                apply_review_overrides_to_cache(self._app, )
            except Exception:
                pass
            # CRITICAL: Only invalidate stats cache, NOT _refresh_review_csv_rows.
            # That call deep-copies every cached row across every well and
            # blows up memory by orders of magnitude.
            self._app._invalidate_stats_cache()
            if debug:
                logger.info(
                    "GatingWorker finished: wells=%d included=%d excluded=%d",
                    total, included_total, excluded_total,
                )
            self.finished_ok.emit()


# ── builder ───────────────────────────────────────────────────────────────


def build_cell_gating_tab(ctx, parent: QWidget) -> None:
    """Build the Cell Gating tab inside *parent*.

    State and widget handles are attached to ``ctx.app`` under the
    ``_cell_gating_*`` prefix.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    ctx.app._cell_gating_frame = parent
    ctx.app._cell_gating_cell_areas = []
    ctx.app._cell_gating_fluor_gate_edits = {}
    ctx.app._cell_gating_thresh_frac_edits = {}
    ctx.app._cell_gating_fluor_data = {}
    ctx.app._cell_gating_ratio_data = {}
    ctx.app._cell_gating_ax = None
    ctx.app._cell_gating_axes_stack = []
    ctx.app._cell_gating_worker = None

    root = parent.layout()
    if root is None:
        root = QVBoxLayout(parent)
        parent.setLayout(root)
    root.setContentsMargins(8, 8, 8, 8)

    control_frame = QWidget(parent)
    control_frame.setObjectName("TabCtrl")
    cf_layout = QVBoxLayout(control_frame)
    cf_layout.setContentsMargins(6, 6, 6, 6)

    # Cell-area gating retired — it now lives in the QC layer (Segmentation
    # tab → QC debris gate), enforced via QC_Pass at the df_included_mask
    # chokepoint. This tab gates on fluorescence only.

    title_row = QWidget(control_frame)
    tr = QHBoxLayout(title_row)
    tr.setContentsMargins(0, 0, 0, 0)
    title = QLabel("FluorGating (Cell Inclusion)", title_row)
    title.setProperty("role", "section")
    tr.addWidget(title)
    tr.addStretch(1)
    # Auto-threshold all channels: runs the same Otsu pass the pipeline's
    # final stage executes (well_viewer.auto_threshold) and writes the
    # results into the ThreshFracOn edits for every loaded channel — it
    # is the fraction-on cut used in downstream plots, NOT the per-cell
    # FluorGating inclusion threshold. Status messages stream to the log
    # drawer via the standard logger.
    from PySide6.QtWidgets import QPushButton as _QPushButton
    auto_btn = _QPushButton("Auto-threshold all channels", title_row)
    auto_btn.setProperty("variant", "secondary")
    auto_btn.setToolTip(
        "Compute a default ThreshFracOn cut per channel via Otsu on "
        "a per-cell + matched-background-pixel distribution sampled from "
        "the first, middle, and last timepoint of every FOV. The "
        "per-cell FluorGating inclusion thresholds are NOT modified.\n\n"
        "Progress + per-channel results are written to the log drawer."
    )
    auto_btn.clicked.connect(lambda _=False: cell_gating_auto_threshold(ctx.app))
    tr.addWidget(auto_btn)
    ctx.app._cell_gating_auto_btn = auto_btn
    cf_layout.addWidget(title_row)

    # MFI-only note: FluorGating + ThreshFracOn always run against per-cell
    # mean fluorescence intensity, regardless of which Property the user
    # picks in the plotting ctxbar.
    mfi_note = QLabel(
        "Note: FluorGating (cell inclusion) and ThreshFracOn cuts are "
        "applied against per-cell mean fluorescence intensity (MFI) only, "
        "independent of the Property selected for plots, stats, and exports.",
        control_frame,
    )
    mfi_note.setObjectName("Muted")
    mfi_note.setWordWrap(True)
    cf_layout.addWidget(mfi_note)

    ctx.app._cell_gating_scroll = QScrollArea(control_frame)
    ctx.app._cell_gating_scroll.setWidgetResizable(True)
    ctx.app._cell_gating_scroll.setFrameShape(QFrame.NoFrame)
    ctx.app._cell_gating_scroll.setFixedHeight(120)
    ctx.app._cell_gating_inner = QWidget()
    QVBoxLayout(ctx.app._cell_gating_inner)
    ctx.app._cell_gating_scroll.setWidget(ctx.app._cell_gating_inner)
    cf_layout.addWidget(ctx.app._cell_gating_scroll)
    root.addWidget(control_frame)

    # CDF plot area
    ctx.app._cell_gating_figure = Figure(figsize=(8, 5), dpi=100)
    ctx.app._cell_gating_canvas = FigureCanvas(ctx.app._cell_gating_figure)

    ctx.app._cell_gating_plot_scroll = QScrollArea(parent)
    ctx.app._cell_gating_plot_scroll.setWidgetResizable(True)
    ctx.app._cell_gating_plot_scroll.setFrameShape(QFrame.NoFrame)
    ctx.app._cell_gating_plot_scroll.setWidget(ctx.app._cell_gating_canvas)
    install_canvas_wheel_scroll(ctx.app._cell_gating_canvas, ctx.app._cell_gating_plot_scroll)
    root.addWidget(ctx.app._cell_gating_plot_scroll, 1)

    from widgets.mpl_toolbar import MplToolbar
    ctx.app._cell_gating_toolbar = MplToolbar(ctx.app._cell_gating_canvas, parent)
    root.addWidget(ctx.app._cell_gating_toolbar)

    ctx.app._cell_gating_status_label = QLabel("No data loaded", parent)
    ctx.app._cell_gating_status_label.setObjectName("Muted")
    root.addWidget(ctx.app._cell_gating_status_label)


# ── public API ────────────────────────────────────────────────────────────


def build_cell_gating_subtab(app) -> None:
    """Build the Cell Gating sub-tab content if it hasn't been built yet.

    Called on first sub-tab activation and from persistence when persisted
    thresholds need to be applied immediately. Idempotent.
    """
    if getattr(app, "_cell_gating_subtab_built", False):
        return
    frame = getattr(app, "_cell_gating_subtab_frame", None)
    if frame is None:
        return
    try:
        build_cell_gating_tab(app._ctx, frame)
        if app._app_state.dataset.well_paths:
            try:
                cell_gating_load_cell_areas(app)
                app._load_gating_from_pipeline_info()
                cell_gating_load_threshold_frac_on(app)
            except Exception:
                logger.exception("Cell Gating post-build sync failed")
    except Exception:
        logger.exception("Cell Gating sub-tab build failed")
    finally:
        app._cell_gating_subtab_built = True


def _cell_gating_valid_rows(frame_df):
    """Rows that pass the crop region AND the QC debris gate (absent columns ⇒
    all-pass). The per-channel fluorescence gate is deliberately NOT applied:
    these CDFs are what the user reads to SET that gate, so filtering by it (via
    the ``Included`` column) would be circular — raising the threshold would
    shrink the very curve being read. Crop + QC are independent of the gate, so
    the CDFs show the valid population without that feedback loop.
    """
    mask = pd.Series(True, index=frame_df.index)
    for col in ("QC_Pass", "Crop_Pass"):
        if col in frame_df.columns:
            vals = pd.to_numeric(frame_df[col], errors="coerce").fillna(1)
            mask = mask & vals.eq(1)
    return frame_df[mask]


def cell_gating_load_cell_areas(app) -> None:
    """Repopulate cell-area + per-channel fluorescence CDFs from selected wells."""
    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        return  # tab not built yet
    app._cell_gating_cell_areas = []
    app._cell_gating_fluor_data = {}
    app._cell_gating_ratio_data = {}
    labels = _cell_gating_source_wells(app)
    ratios = list(app._app_state.channel.ratio_metrics or [])

    for label in labels:
        df = app._get_rows(label)
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
            app._cell_gating_cell_areas.extend(
                float(a) for a in area[np.isfinite(area) & (area > 0)]
            )
        for channel in app._app_state.channel.fluor_channels:
            val_col = f"{channel}_mean_intensity"
            if val_col not in frame_df.columns:
                continue
            v = pd.to_numeric(frame_df[val_col], errors="coerce").to_numpy()
            positive = v[np.isfinite(v) & (v > 0)]
            if positive.size:
                app._cell_gating_fluor_data.setdefault(channel, []).extend(
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
                app._cell_gating_ratio_data.setdefault(rm.display_label(), []).extend(
                    float(x) for x in ratio[keep]
                )

    _cell_gating_build_channel_controls(app)

    if app._cell_gating_cell_areas:
        app._cell_gating_axes_stack = []
        _cell_gating_plot_cdf(app)
        app._cell_gating_status_label.setText(
            f"Loaded {len(app._cell_gating_cell_areas)} cells from "
            f"{len(labels)} selected well(s), first frame of first FOV"
        )
    else:
        app._cell_gating_status_label.setText("No cell data found")


def cell_gating_load_threshold_frac_on(app) -> None:
    """Re-fill ThreshFracOn line-edits from ``app._thresh_frac_on_saved``."""
    if not hasattr(app, "_cell_gating_thresh_frac_edits"):
        return
    if hasattr(app, "_thresh_frac_on_saved"):
        for channel, value in app._thresh_frac_on_saved.items():
            if channel in app._cell_gating_thresh_frac_edits:
                app._cell_gating_thresh_frac_edits[channel].setText(str(value))


def _cell_gating_resolve_key(app, channel: str) -> str:
    """Map a channel arg onto the key used in the Cell Gating edits dicts.

    Real fluor channels (``"gfp"``, ``"mcherry"``) are stored under their
    bare name. Ratios are stored under their full key (``"ratio:<name>"``).
    Callers can pass either the bare ratio name (``app._app_state.channel.active_channel``
    after a ratio switch) or the full ``ratio:`` key — resolve to whichever
    form actually exists in the edits dict so the user's edit is honoured.
    """
    edits = (getattr(app, "_cell_gating_thresh_frac_edits", {}) or {})
    if channel in edits:
        return channel
    candidate = f"ratio:{channel}"
    if candidate in edits:
        return candidate
    return channel


def cell_gating_get_fluor_gate(app, channel: str) -> float:
    key = _cell_gating_resolve_key(app, channel)
    edit = (getattr(app, "_cell_gating_fluor_gate_edits", {}) or {}).get(key)
    if edit is None:
        return 0.0
    try:
        return float(edit.text())
    except ValueError:
        return 0.0


def cell_gating_get_thresh_frac_on(app, channel: str) -> float:
    key = _cell_gating_resolve_key(app, channel)
    edit = (getattr(app, "_cell_gating_thresh_frac_edits", {}) or {}).get(key)
    if edit is None:
        return 50.0
    try:
        return float(edit.text())
    except ValueError:
        return 50.0


def cell_gating_on_gating_change(app) -> None:
    try:
        for edit in app._cell_gating_fluor_gate_edits.values():
            float(edit.text())
    except ValueError:
        return

    app._cell_gating_axes_stack = []
    _cell_gating_plot_cdf(app)
    _cell_gating_persist_params(app)
    cell_gating_start_gating_worker(app)


def cell_gating_on_threshold_frac_on_change(app) -> None:
    try:
        for edit in app._cell_gating_thresh_frac_edits.values():
            float(edit.text())
        _cell_gating_save_threshold_frac_on(app)
        _cell_gating_persist_params(app)
        app._invalidate_stats_cache()
        # ``_redraw`` only fans out to the default-scope plots (line /
        # distribution / heat map). Bar and scatter redraw via the fold-change
        # scope registry, so also emit ``plot_options_changed`` — otherwise a
        # ThreshFracOn edit leaves the (now default) Bar Plots tab stale.
        app._redraw()
        app._event_bus.plot_options_changed.emit(app._app_state.plot)
    except ValueError:
        pass


def cell_gating_start_gating_worker(app) -> None:
    # If a previous worker is still running, cancel it before starting
    # a new pass so threshold edits don't pile up.
    prev = app._cell_gating_worker
    if prev is not None and prev.isRunning():
        prev.cancel()
        prev.wait()

    worker = GatingWorker(app)
    worker.progress.connect(lambda cur, tot: _cell_gating_on_progress(app, cur, tot))
    worker.finished_ok.connect(lambda: _cell_gating_on_finished(app))
    app._cell_gating_worker = worker
    bar = getattr(app, "_progress_bar", None)
    if bar is not None:
        bar.setRange(0, max(1, len(app._app_state.dataset.well_paths)))
        bar.setValue(0)
        bar.show()
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

    def __init__(self, app, out_dir, channels, *, well_labels=None,
                 in_dir=None, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._out_dir = out_dir
        self._channels = list(channels)
        self._well_labels = list(well_labels) if well_labels else []
        self._in_dir = in_dir

    def run(self) -> None:  # noqa: D401 - QThread override
        # Stream traceback into the log drawer too — the previous "fail
        # silently with just an exception message" behaviour made it
        # impossible to tell *why* the button hung up.
        import logging as _logging
        import traceback as _tb
        _log = _logging.getLogger("well_viewer.data.auto_threshold")
        from well_viewer.core import status_signal as _status_signal
        _status_signal.busy_push()
        try:
            from well_viewer.data.auto_threshold import compute_auto_thresholds
            result = compute_auto_thresholds(
                self._out_dir,
                fluor_channels=self._channels,
                progress=lambda msg: self.progress.emit(str(msg)),
                well_labels=self._well_labels or None,
                in_dir=self._in_dir,
            )
        except Exception as exc:
            _log.error(
                "Auto-threshold worker failed: %s\n%s",
                exc, _tb.format_exc(),
            )
            _status_signal.signal_failed()
            self.failed.emit(str(exc))
            self.done.emit({})
            return
        finally:
            _status_signal.busy_pop()
        self.done.emit(result or {})


def cell_gating_auto_threshold(app) -> None:
    """Kick off the Auto-threshold pass for every loaded fluorescence channel.

    Mirrors what the ``process_microscopy`` pipeline runs as its final
    step: per-cell mean + matched random background pixel pooled across
    every well's first/middle/last timepoint, then Otsu. The result lands
    in ``app._cell_gating_thresh_frac_edits`` (the **ThreshFracOn** column,
    i.e. the fraction-on cut used by downstream plots) and is persisted
    to ``pipeline_info.json::cell_gating.thresh_frac_on`` so the next load
    picks the new defaults up. The per-cell ``FluorGating`` inclusion
    thresholds are never touched by this pass.

    Status streams to the log drawer via the standard logging API (the
    ``logging.Handler`` installed by ``all_well._attach_log_ring_buffer``
    captures every record).
    """
    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        return
    out_dir = app._app_state.dataset.data_dir
    if not out_dir:
        try:
            app._set_status(
                "Auto-threshold: load a dataset first."
            )
        except Exception:
            pass
        return
    channels = [str(ch).strip().lower() for ch in (app._app_state.channel.fluor_channels or []) if str(ch).strip()]
    if not channels:
        try:
            app._set_status(
                "Auto-threshold: no fluorescence channels detected in the loaded dataset."
            )
        except Exception:
            pass
        return
    # Stop any previous worker still running.
    prev = getattr(app, "_cell_gating_auto_worker", None)
    if prev is not None and prev.isRunning():
        try:
            prev.wait(50)
        except Exception:
            pass
    btn = getattr(app, "_cell_gating_auto_btn", None)
    if btn is not None:
        btn.setEnabled(False)
        btn.setText("Auto-thresholding…")

    def _on_progress(msg: str) -> None:
        try:
            app._set_status(msg)
        except Exception:
            pass

    def _on_done(result) -> None:
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Auto-threshold all channels")
        if not isinstance(result, dict) or not result:
            try:
                app._set_status(
                    "Auto-threshold finished — no thresholds were updated."
                )
            except Exception:
                pass
            return
        edits = app._cell_gating_thresh_frac_edits or {}
        written: list[str] = []
        for ch, thr in result.items():
            edit = edits.get(ch)
            if edit is None:
                continue
            try:
                edit.setText(f"{float(thr):.2f}")
                written.append(f"{ch.upper()}={float(thr):.2f}")
            except Exception:
                continue
        # Persist + re-render the CDF + kick the gating worker exactly as
        # the user typing a new ThreshFracOn would.
        try:
            cell_gating_on_threshold_frac_on_change(app)
        except Exception:
            pass
        try:
            app._set_status(
                "Auto-threshold updated " + ", ".join(written) if written
                else "Auto-threshold finished — no ThreshFracOn edits were touched."
            )
        except Exception:
            pass

    def _on_failed(msg: str) -> None:
        import logging as _logging
        _logging.getLogger("well_viewer.data.auto_threshold").error(
            "Auto-threshold failed: %s", msg,
        )
        try:
            app._set_status(f"Auto-threshold failed: {msg}")
        except Exception:
            pass

    well_labels = list(app._app_state.dataset.well_paths or {})
    in_dir = app._app_state.dataset.in_dir
    import logging as _logging
    _logging.getLogger("well_viewer.data.auto_threshold").info(
        "Auto-threshold: starting — data_dir=%s in_dir=%s wells=%d channels=%s",
        out_dir, in_dir, len(well_labels), ",".join(channels),
    )
    worker = _AutoThresholdWorker(
        app, out_dir, channels,
        well_labels=well_labels, in_dir=in_dir,
    )
    worker.progress.connect(_on_progress)
    worker.done.connect(_on_done)
    worker.failed.connect(_on_failed)
    app._cell_gating_auto_worker = worker
    worker.start()


def cell_gating_update_theme(app) -> None:
    fig = getattr(app, "_cell_gating_figure", None)
    if fig is not None:
        fig.set_facecolor(get_color("BG_APP"))
    if (getattr(app, "_cell_gating_cell_areas", None)
            or getattr(app, "_cell_gating_fluor_data", None)):
        _cell_gating_plot_cdf(app)


# ── internal helpers ──────────────────────────────────────────────────────


def _cell_gating_build_channel_controls(app) -> None:
    inner_layout = app._cell_gating_inner.layout()
    while inner_layout.count():
        item = inner_layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()

    channels = list(app._app_state.channel.fluor_channels)
    # Ratios show up as virtual channels in the plot dropdowns; surface
    # them here too so the user can give each ratio a FluorGating /
    # ThreshFracOn entry. They're keyed by ``ratio.key()`` (e.g.
    # ``ratio:gfp_over_mcherry``) in the edits dicts, distinct from the
    # plain fluor channel keys.
    ratios = list(app._app_state.channel.ratio_metrics or [])
    row_specs: list[tuple[str, str]] = [(ch, ch.upper() + " Channel:") for ch in channels]
    for r in ratios:
        try:
            label = app._ratio_label_for(r)
        except Exception:
            label = r.name
        row_specs.append((r.key(), f"{label} (ratio):"))

    if not row_specs:
        lbl = QLabel("No channels loaded", app._cell_gating_inner)
        lbl.setObjectName("Muted")
        inner_layout.addWidget(lbl)
        return

    for channel, row_label in row_specs:
        ch_row = QWidget(app._cell_gating_inner)
        rl = QHBoxLayout(ch_row)
        rl.setContentsMargins(0, 0, 0, 0)

        ch_lbl = QLabel(row_label, ch_row)
        ch_lbl.setFixedWidth(160)
        rl.addWidget(ch_lbl)

        rl.addWidget(QLabel("FluorGating:", ch_row))
        if channel not in app._cell_gating_fluor_gate_edits:
            gate_edit = QLineEdit("0.0", ch_row)
            gate_edit.setFixedWidth(90)
            gate_edit.editingFinished.connect(
                lambda _app=app: cell_gating_on_gating_change(_app)
            )
            app._cell_gating_fluor_gate_edits[channel] = gate_edit
        rl.addWidget(app._cell_gating_fluor_gate_edits[channel])

        rl.addWidget(QLabel("ThreshFracOn:", ch_row))
        if channel not in app._cell_gating_thresh_frac_edits:
            thresh_edit = QLineEdit("50.0", ch_row)
            thresh_edit.setFixedWidth(90)
            thresh_edit.editingFinished.connect(
                lambda _app=app: cell_gating_on_threshold_frac_on_change(_app)
            )
            app._cell_gating_thresh_frac_edits[channel] = thresh_edit
        rl.addWidget(app._cell_gating_thresh_frac_edits[channel])
        rl.addStretch(1)

        inner_layout.addWidget(ch_row)
    inner_layout.addStretch(1)


def _cell_gating_first_frame_rows(df) -> tuple:
    """Return ``(sub_df, description)`` for the first FOV's first timepoint."""
    if df is None or df.empty:
        return df, ""

    fov_series = (df["fov"].fillna("1").astype(str).str.strip().replace("", "1")
                  if "fov" in df.columns
                  else pd.Series(["1"] * len(df), index=df.index))

    def _fov_token(token: str) -> tuple:
        try:
            return (0, float(token))
        except ValueError:
            return (1, token.lower())

    unique_fovs = sorted(fov_series.unique(), key=_fov_token)
    first_fov = unique_fovs[0] if unique_fovs else "1"
    same_fov = df.loc[fov_series == first_fov]
    if same_fov.empty:
        return same_fov, ""

    tp_h = (pd.to_numeric(same_fov.get("timepoint_hours"), errors="coerce")
            if "timepoint_hours" in same_fov.columns
            else pd.Series(np.nan, index=same_fov.index, dtype=float))
    tp_str = (same_fov.get("timepoint", pd.Series([""] * len(same_fov),
                                                   index=same_fov.index))
              .fillna("").astype(str).str.strip())
    tp_str_num = pd.to_numeric(tp_str, errors="coerce")

    if tp_h.notna().any():
        min_h = float(tp_h.min())
        mask = (tp_h == min_h)
        frame_desc_tp = f"{min_h:g}"
    elif tp_str_num.notna().any():
        min_n = float(tp_str_num.min())
        mask = tp_str_num == min_n
        frame_desc_tp = f"{min_n:g}"
    else:
        non_empty = tp_str[tp_str != ""]
        if non_empty.empty:
            mask = pd.Series(True, index=same_fov.index)
            frame_desc_tp = ""
        else:
            first = sorted(non_empty.unique(), key=str.lower)[0]
            mask = tp_str == first
            frame_desc_tp = first

    frame_df = same_fov.loc[mask]
    frame_desc = f"fov={first_fov}, tp={frame_desc_tp}"
    return frame_df, frame_desc


def _cell_gating_source_wells(app) -> list[str]:
    active_rsets = []
    if hasattr(app, "_rep_sets_active"):
        active_rsets = app._rep_sets_active()

    if active_rsets:
        seen: set[str] = set()
        ordered: list[str] = []
        for rset in active_rsets:
            for well in rset.wells:
                if well in app._app_state.dataset.well_paths and well not in seen:
                    seen.add(well)
                    ordered.append(well)
        return sorted(ordered, key=app._parse_rc)

    selected = [
        label for label in app._app_state.selection.selected_wells
        if label in app._app_state.dataset.well_paths
    ]
    # Fall back to every loaded well so the CDFs render on the first open
    # after a data load (before the user has made an explicit selection).
    # Once they pick wells in the sidebar, that selection scopes the plot.
    if not selected:
        selected = list(app._app_state.dataset.well_paths)
    return sorted(selected, key=app._parse_rc)


def _cell_gating_plot_cdf(app) -> None:
    ratio_data = getattr(app, "_cell_gating_ratio_data", {}) or {}
    if (not app._cell_gating_cell_areas
            and not app._cell_gating_fluor_data
            and not ratio_data):
        return

    bg_app = get_color("BG_APP")
    bg_panel = get_color("BG_PANEL")
    txt_pri = get_color("TXT_PRI")
    txt_mut = get_color("TXT_MUT")
    accent = get_color("ACCENT")
    warn = get_color("WARN")

    fig = app._cell_gating_figure
    fig.clf()
    fig.set_facecolor(bg_app)

    n_plots = 1 + len(app._cell_gating_fluor_data) + len(ratio_data)
    n_cols = 1 if n_plots == 1 else 2
    n_rows = (n_plots + n_cols - 1) // n_cols
    plot_height_per_row = 3.8
    fig_height = max(5.0, n_rows * plot_height_per_row)

    axes = []
    for i in range(n_plots):
        ax = fig.add_subplot(n_rows, n_cols, i + 1, facecolor=bg_panel)
        axes.append(ax)

    if app._cell_gating_cell_areas:
        areas = np.array(sorted(app._cell_gating_cell_areas))
        cdf = np.arange(1, len(areas) + 1) / len(areas)
        axes[0].plot(areas, cdf, linewidth=2, color=accent, alpha=0.8)
        axes[0].fill_between(areas, cdf, alpha=0.2, color=accent)
        axes[0].set_xlabel("Cell Area (pixels)", color=txt_pri, fontsize=9)
        axes[0].set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        axes[0].set_title("Cell Area Distribution", color=txt_pri,
                          fontsize=10, fontweight="bold")
        axes[0].grid(True, alpha=0.2, color=txt_mut)
        axes[0].tick_params(colors=txt_mut, labelsize=8)

        # Mark the QC layer's min-area gate (cell-area gating is QC-owned now).
        try:
            from well_viewer.features.qc.controller import get_qc_params
            _qc_min = get_qc_params(app).min_area
            if _qc_min is not None:
                axes[0].axvline(x=float(_qc_min), color=warn,
                                linestyle="--", linewidth=2, alpha=0.7)
        except Exception:
            pass

    colors = [accent, "#FF9500", "#FF3B30", "#34C759"]
    next_idx = 1
    for channel, values in sorted(app._cell_gating_fluor_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        vals = np.array(sorted(values))
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, linewidth=2, color=color, alpha=0.8)
        ax.fill_between(vals, cdf, alpha=0.2, color=color)
        ax.set_xlabel(f"{channel.upper()} Intensity", color=txt_pri, fontsize=9)
        ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        ax.set_title(f"{channel.upper()} Distribution", color=txt_pri,
                     fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, color=txt_mut)
        ax.tick_params(colors=txt_mut, labelsize=8)

        try:
            fluor_gate = float(
                app._cell_gating_fluor_gate_edits[channel].text()
            )
            ax.axvline(x=fluor_gate, color=warn,
                       linestyle="--", linewidth=2, alpha=0.7)
        except (ValueError, KeyError):
            pass
        next_idx += 1

    for ratio_label, values in sorted(ratio_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        vals = np.array(sorted(values))
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, linewidth=2, color=color, alpha=0.8)
        ax.fill_between(vals, cdf, alpha=0.2, color=color)
        ax.set_xlabel(f"{ratio_label} ratio", color=txt_pri, fontsize=9)
        ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        ax.set_title(f"{ratio_label} Distribution", color=txt_pri,
                     fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, color=txt_mut)
        ax.tick_params(colors=txt_mut, labelsize=8)
        next_idx += 1

    app._cell_gating_ax = axes[0]

    if not app._cell_gating_axes_stack:
        limits = [(ax.get_xlim(), ax.get_ylim()) for ax in axes]
        app._cell_gating_axes_stack.append(limits)

    fig.tight_layout(pad=1.3)
    app._cell_gating_canvas.draw_idle()

    # Constrain height only; width is driven by the scroll area's
    # setWidgetResizable(True) so the canvas fills the viewport.
    dpi = fig.get_dpi()
    fig_h_px = max(1, int(fig_height * dpi))
    app._cell_gating_canvas.setMinimumHeight(fig_h_px)


def _cell_gating_on_progress(app, current: int, total: int) -> None:
    bar = getattr(app, "_progress_bar", None)
    if bar is None:
        return
    if bar.maximum() != total:
        bar.setRange(0, total)
    bar.setValue(current)


def _cell_gating_on_finished(app) -> None:
    bar = getattr(app, "_progress_bar", None)
    if bar is not None:
        bar.setValue(0)
        bar.hide()
    app._redraw()


def _cell_gating_persist_params(app) -> None:
    """Save current gating values into pipeline_info.json (no-op when at defaults)."""
    save = getattr(app, "_save_gating_to_pipeline_info", None)
    if save is None:
        return
    try:
        save()
    except Exception:
        logger.exception("Failed to save gating params to pipeline_info.json")


def _cell_gating_save_threshold_frac_on(app) -> None:
    if not hasattr(app, "_thresh_frac_on_saved"):
        app._thresh_frac_on_saved = {}
    for channel, edit in app._cell_gating_thresh_frac_edits.items():
        try:
            app._thresh_frac_on_saved[channel] = float(edit.text())
        except ValueError:
            pass
