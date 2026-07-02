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
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QVBoxLayout, QWidget,
)

from ui.theme import get_color
from well_viewer.core import debug_flags as _debug_flags
from well_viewer.core.ui_helpers import (
    build_hline_separator,
    build_section_header,
    install_canvas_wheel_scroll,
)
from well_viewer.data.data_loading import resolve_value_series
from well_viewer.features.qc.tab_view import build_qc_gate_controls
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy
# Lazy so Cell Gating imports without Image Browser / scatter present (keeps both
# deletable); resolves on first call.
apply_review_overrides_to_cache = _lazy("well_viewer.features.image_browser.review_image.controller", "apply_review_overrides_to_cache")
compute_gate_inclusion = _lazy("well_viewer.features.scatter.gating", "compute_gate_inclusion")


logger = logging.getLogger("well_viewer.gating_worker")


@dataclass(frozen=True)
class ThreshFracOptimizationResult:
    """Best threshold scan result for a two-well fraction-on comparison."""

    threshold: float
    frac_a: float
    frac_b: float
    difference: float
    n_a: int
    n_b: int


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

    def __init__(self, app, parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self._app = app
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
        fluor_gates = self._app._get_all_fluor_gates()
        # "Use scatter gates" mode replaces the per-channel FluorGating: the
        # filtering gate selected on the Cell Gating tab — a single named gate,
        # or the union of all gates — defines the Included population instead of
        # the intensity floors.
        use_scatter = getattr(self._app, "_cell_gating_use_scatter_gates", False)
        # Thresholding has its OWN gate selection (independent of the gating
        # layer): cells inside its gate(s) define the "on" population for
        # fraction-on (Gate_On column).
        use_thresh = getattr(self._app, "_cell_gating_use_scatter_thresh", False)
        gating_gates = cell_gating_selected_scatter_gates(self._app, "gating")
        thresh_gates = cell_gating_selected_scatter_gates(self._app, "thresh")
        ratios = self._app._app_state.channel.ratio_index or {}
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
                # Gating layer → Included / Gate_Pass.
                if use_scatter:
                    # Scatter-gate inclusion REPLACES the per-channel FluorGating:
                    # Included is all-pass for this layer and the gate membership
                    # lives in Gate_Pass, so the per-cell scatter keeps the full
                    # cloud while every other view filters via df_included_mask.
                    # Mode on + no selected gates ⇒ no restriction.
                    if gating_gates:
                        gate_mask = pd.Series(
                            compute_gate_inclusion(df, gating_gates, ratios),
                            index=df.index,
                        )
                    else:
                        gate_mask = pd.Series(True, index=df.index)
                    df["Included"] = 1
                    df["Gate_Pass"] = gate_mask.astype(int)
                    well_included = int(gate_mask.sum())
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
                    # Neutralise any Gate_Pass left over from a previous
                    # scatter-gate pass so intensity gating isn't still filtered.
                    df["Gate_Pass"] = 1
                    well_included = int(mask.sum())

                # Thresholding layer → Gate_On (fraction-on by gate), using its
                # OWN gate selection. Only read by the aggregation when
                # thresholding is active (see stats_compute._thresh_on_col);
                # neutral (1) otherwise so a stale membership can't leak in.
                if use_thresh and thresh_gates:
                    df["Gate_On"] = compute_gate_inclusion(
                        df, thresh_gates, ratios).astype(int)
                else:
                    df["Gate_On"] = 1

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
                swallowed()  # was: pass (silent except)
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
    # Scatter-gate inclusion mode: when on, the Scatter Plot's rectangular gates
    # define Included instead of the per-channel FluorGating. The gating and
    # thresholding layers each pick their OWN set of gates (a set of names, or
    # None ⇒ all gates, which auto-includes gates drawn later).
    ctx.app._cell_gating_use_scatter_gates = getattr(
        ctx.app, "_cell_gating_use_scatter_gates", False
    )
    ctx.app._cell_gating_gating_choices = getattr(
        ctx.app, "_cell_gating_gating_choices", None
    )
    ctx.app._cell_gating_thresh_choices = getattr(
        ctx.app, "_cell_gating_thresh_choices", None
    )
    # Thresholding mode (independent of gating): cells inside the selected
    # gate(s) count as "on" for fraction-on stats, replacing the ThreshFracOn
    # cutoff. Cells are not removed.
    ctx.app._cell_gating_use_scatter_thresh = getattr(
        ctx.app, "_cell_gating_use_scatter_thresh", False
    )

    # The tab stacks a tall column of gating controls *above* the CDF plot and
    # the optimizer panel. On shorter windows that column squeezed the plot down
    # to a sliver (the CDFs were clipped to their top edge). Host the whole tab
    # in one vertical scroll area and give the plot a real minimum height: the
    # CDFs always render full-size, and anything that overflows a short window is
    # reachable by scrolling instead of collapsing the plot.
    outer_layout = parent.layout()
    if outer_layout is None:
        outer_layout = QVBoxLayout(parent)
        parent.setLayout(outer_layout)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_scroll = QScrollArea(parent)
    outer_scroll.setWidgetResizable(True)
    outer_scroll.setFrameShape(QFrame.NoFrame)
    outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    outer_layout.addWidget(outer_scroll)
    content = QWidget()
    outer_scroll.setWidget(content)
    # Section builders below append to ``root``; point it at the scrollable
    # content so the existing ``root.addWidget(...)`` calls need no changes.
    root = QVBoxLayout(content)
    root.setContentsMargins(12, 12, 12, 12)

    control_frame = QWidget(parent)
    control_frame.setObjectName("TabCtrl")
    cf_layout = QVBoxLayout(control_frame)
    cf_layout.setContentsMargins(6, 6, 6, 6)

    # QC debris gate — the fundamental inclusion layer. It is ALWAYS applied
    # (QC_Pass at the df_included_mask chokepoint), composing with FluorGating
    # and the scatter gates below, so it leads the tab. Editing here re-flags the
    # whole dataset live and refreshes the Segmentation overlay.
    cf_layout.addWidget(build_section_header(
        control_frame, "QC debris gate (always applied)",
        hint="Size / solidity debris gating — a fundamental layer applied to "
             "every cell, on top of the FluorGating thresholds and any scatter "
             "gates below.",
    ))
    try:
        cf_layout.addWidget(build_qc_gate_controls(ctx.app, control_frame))
    except Exception:
        swallowed()  # QC controls are best-effort; never block the tab build

    cf_layout.addWidget(build_hline_separator(control_frame))
    cf_layout.addWidget(build_section_header(
        control_frame, "FluorGating (Cell Inclusion)",
    ))

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

    # Scatter-gate inclusion. The user draws named rectangular gates on the
    # Scatter Plot tab; ticking this replaces the FluorGating intensity floors,
    # and the dropdown picks which gate filters (or all of them, unioned).
    from PySide6.QtWidgets import QCheckBox as _QCheckBox, QPushButton as _QPushButton
    sg_row = QWidget(control_frame)
    sg_layout = QHBoxLayout(sg_row)
    sg_layout.setContentsMargins(0, 0, 0, 0)
    use_sg_cb = _QCheckBox(
        "Use scatter-plot gates for cell inclusion (replaces FluorGating)",
        sg_row,
    )
    use_sg_cb.setChecked(bool(ctx.app._cell_gating_use_scatter_gates))
    use_sg_cb.setToolTip(
        "Define the Included population from the named rectangular gates drawn "
        "on the Scatter Plot tab instead of the per-channel intensity "
        "thresholds. Pick which gate filters with the dropdown. Gates are saved "
        "with the dataset."
    )
    use_sg_cb.toggled.connect(
        lambda _on, _app=ctx.app: cell_gating_on_use_scatter_gates_change(_app)
    )
    ctx.app._cell_gating_use_scatter_gates_cb = use_sg_cb
    sg_layout.addWidget(use_sg_cb)
    sg_layout.addSpacing(8)
    sg_layout.addWidget(QLabel("Gates:", sg_row))
    gating_btn = _QPushButton("All gates", sg_row)
    gating_btn.setMinimumWidth(150)
    gating_btn.setToolTip(
        "Which scatter gates define the Included population (union of the "
        "checked gates). Independent of the fraction-on selection below."
    )
    gating_btn.clicked.connect(
        lambda _=False, _app=ctx.app: _open_gate_filter_menu(_app, "gating")
    )
    ctx.app._cell_gating_gating_gate_btn = gating_btn
    sg_layout.addWidget(gating_btn)
    sg_layout.addStretch(1)
    cf_layout.addWidget(sg_row)

    # Thresholding mode — independent of the gating checkbox above, with its OWN
    # gate selection: instead of removing cells, counts cells inside the chosen
    # gate(s) as "on" for fraction-on stats (replaces ThreshFracOn).
    th_row = QWidget(control_frame)
    th_layout = QHBoxLayout(th_row)
    th_layout.setContentsMargins(0, 0, 0, 0)
    use_th_cb = _QCheckBox(
        "Use scatter gate for fraction-on (thresholding, replaces ThreshFracOn)",
        th_row,
    )
    use_th_cb.setChecked(bool(ctx.app._cell_gating_use_scatter_thresh))
    use_th_cb.setToolTip(
        "Define the 'on' population for fraction-on stats as the cells inside "
        "the selected gate(s), instead of cells above the per-channel "
        "ThreshFracOn cutoff. Independent of cell inclusion — no cells removed."
    )
    use_th_cb.toggled.connect(
        lambda _on, _app=ctx.app: cell_gating_on_use_scatter_thresh_change(_app)
    )
    ctx.app._cell_gating_use_scatter_thresh_cb = use_th_cb
    th_layout.addWidget(use_th_cb)
    th_layout.addSpacing(8)
    th_layout.addWidget(QLabel("Gates:", th_row))
    thresh_btn = _QPushButton("All gates", th_row)
    thresh_btn.setMinimumWidth(150)
    thresh_btn.setToolTip(
        "Which scatter gates define the fraction-on 'on' population (union of "
        "the checked gates). Independent of the inclusion selection above."
    )
    thresh_btn.clicked.connect(
        lambda _=False, _app=ctx.app: _open_gate_filter_menu(_app, "thresh")
    )
    ctx.app._cell_gating_thresh_gate_btn = thresh_btn
    th_layout.addWidget(thresh_btn)
    th_layout.addStretch(1)
    cf_layout.addWidget(th_row)

    ctx.app._cell_gating_gate_summary = QLabel("", control_frame)
    ctx.app._cell_gating_gate_summary.setObjectName("Muted")
    ctx.app._cell_gating_gate_summary.setWordWrap(True)
    cf_layout.addWidget(ctx.app._cell_gating_gate_summary)
    # The scatter gating module fires this whenever a gate is drawn / edited /
    # cleared on the Scatter Plot tab: refresh the summary AND recompute Included
    # immediately so every other view tracks the gates without waiting for a tab
    # switch. (Safe now that the per-cell scatter never filters itself by the
    # gates — see df_included_mask(include_gate=False).)
    ctx.app._scatter_gates_changed_cb = lambda _app=ctx.app: cell_gating_sync_scatter_gates(_app)
    _cell_gating_refresh_gate_filter(ctx.app)
    _cell_gating_refresh_gate_summary(ctx.app)
    _cell_gating_apply_scatter_gate_mode(ctx.app)

    # Auto-threshold all channels: runs the same Otsu pass the pipeline's
    # final stage executes (well_viewer.auto_threshold) and writes the
    # results into the ThreshFracOn edits for every loaded channel — it
    # is the fraction-on cut used in downstream plots, NOT the per-cell
    # FluorGating inclusion threshold. Status messages stream to the log
    # drawer via the standard logger. Sits in its own row directly above
    # the per-channel FluorGating / ThreshFracOn table it modifies, so it
    # never floats away from the controls it acts on.
    cf_layout.addWidget(build_hline_separator(control_frame))
    cf_layout.addWidget(build_section_header(
        control_frame, "ThreshFracOn (Fraction-On Cut)",
        hint="Per-channel cut used for fraction-on in downstream plots / stats "
             "(separate from the FluorGating inclusion thresholds above).",
    ))
    from PySide6.QtWidgets import QPushButton as _QPushButton
    auto_row = QWidget(control_frame)
    auto_row_layout = QHBoxLayout(auto_row)
    auto_row_layout.setContentsMargins(0, 0, 0, 0)
    auto_btn = _QPushButton("Auto-threshold all channels (ThreshFracOn)", auto_row)
    auto_btn.setProperty("variant", "secondary")
    auto_btn.setToolTip(
        "Compute a default ThreshFracOn cut per channel via Otsu on "
        "a per-cell + matched-background-pixel distribution sampled from "
        "the first, middle, and last timepoint of every FOV. The "
        "per-cell FluorGating inclusion thresholds are NOT modified.\n\n"
        "Progress + per-channel results are written to the log drawer."
    )
    auto_btn.clicked.connect(lambda _=False: cell_gating_auto_threshold(ctx.app))
    auto_row_layout.addWidget(auto_btn)
    auto_row_layout.addStretch(1)
    ctx.app._cell_gating_auto_btn = auto_btn
    cf_layout.addWidget(auto_row)

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
    # Floor the plot height so the gating controls above can never squeeze the
    # CDFs into an unreadable sliver (the outer scroll absorbs any overflow).
    ctx.app._cell_gating_plot_scroll.setMinimumHeight(420)
    install_canvas_wheel_scroll(ctx.app._cell_gating_canvas, ctx.app._cell_gating_plot_scroll)

    from widgets.mpl_toolbar import MplToolbar
    ctx.app._cell_gating_toolbar = MplToolbar(ctx.app._cell_gating_canvas, parent)

    ctx.app._cell_gating_status_label = QLabel("No data loaded", parent)
    ctx.app._cell_gating_status_label.setObjectName("Muted")
    root.addWidget(ctx.app._cell_gating_status_label)

    opt_panel = QWidget(parent)
    opt_layout = QVBoxLayout(opt_panel)
    opt_layout.setContentsMargins(0, 4, 0, 0)
    opt_layout.addWidget(build_hline_separator(opt_panel))
    opt_layout.addWidget(build_section_header(
        opt_panel, "Optimize ThreshFracOn between two wells",
    ))
    opt_row = QWidget(opt_panel)
    opt_row_layout = QHBoxLayout(opt_row)
    opt_row_layout.setContentsMargins(0, 0, 0, 0)
    opt_row_layout.addWidget(QLabel("Channel:", opt_row))
    ctx.app._cell_gating_opt_channel_combo = QComboBox(opt_row)
    ctx.app._cell_gating_opt_channel_combo.setMinimumWidth(140)
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_channel_combo)
    opt_row_layout.addWidget(QLabel("Well A:", opt_row))
    ctx.app._cell_gating_opt_well_a_combo = QComboBox(opt_row)
    ctx.app._cell_gating_opt_well_a_combo.setMinimumWidth(90)
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_well_a_combo)
    opt_row_layout.addWidget(QLabel("Well B:", opt_row))
    ctx.app._cell_gating_opt_well_b_combo = QComboBox(opt_row)
    ctx.app._cell_gating_opt_well_b_combo.setMinimumWidth(90)
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_well_b_combo)
    opt_row_layout.addWidget(QLabel("Timepoint:", opt_row))
    ctx.app._cell_gating_opt_timepoint_combo = QComboBox(opt_row)
    ctx.app._cell_gating_opt_timepoint_combo.setMinimumWidth(110)
    ctx.app._cell_gating_opt_timepoint_combo.setToolTip(
        "Restrict the fraction-on comparison to one timepoint, or pool all "
        "timepoints. Pooling across timepoints where the wells are both 'off' "
        "early dilutes the difference, so picking the relevant timepoint gives a "
        "sharper threshold."
    )
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_timepoint_combo)
    opt_row_layout.addWidget(QLabel("Min:", opt_row))
    ctx.app._cell_gating_opt_min_edit = QLineEdit("0.0", opt_row)
    ctx.app._cell_gating_opt_min_edit.setFixedWidth(80)
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_min_edit)
    opt_row_layout.addWidget(QLabel("Max:", opt_row))
    ctx.app._cell_gating_opt_max_edit = QLineEdit("100.0", opt_row)
    ctx.app._cell_gating_opt_max_edit.setFixedWidth(80)
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_max_edit)
    opt_row_layout.addWidget(QLabel("# Points:", opt_row))
    ctx.app._cell_gating_opt_steps_edit = QLineEdit("101", opt_row)
    ctx.app._cell_gating_opt_steps_edit.setFixedWidth(64)
    ctx.app._cell_gating_opt_steps_edit.setToolTip(
        "Number of thresholds scanned across the Min–Max range, evenly "
        "spaced via numpy.linspace. NOT a step size — \"101\" scans 101 "
        "thresholds (every 1 unit across a 0–100 range). Higher values "
        "give a finer scan at the cost of a little compute."
    )
    opt_row_layout.addWidget(ctx.app._cell_gating_opt_steps_edit)
    from PySide6.QtWidgets import QPushButton as _QPushButton
    opt_btn = _QPushButton("Optimize ThreshFracOn", opt_row)
    opt_btn.setProperty("variant", "secondary")
    opt_btn.setToolTip(
        "Scan # Points thresholds between Min and Max for the selected "
        "channel, compute fraction-on in Well A and Well B at each "
        "threshold, then apply the threshold that maximizes their "
        "relative fraction-on gap, |Δ| / max(fa, fb)."
    )
    opt_btn.clicked.connect(lambda _=False: cell_gating_optimize_thresh_frac_on(ctx.app))
    ctx.app._cell_gating_opt_btn = opt_btn
    opt_row_layout.addWidget(opt_btn)
    opt_row_layout.addStretch(1)
    opt_layout.addWidget(opt_row)

    # Optional fold-change row: optimise over a fold-change-adjusted fraction-on
    # (the same vs-control / vs-t0 normalisation the plot tabs use), so the
    # threshold that best separates A and B is chosen on the *normalised* metric.
    # These controls are INDEPENDENT of the plotting tabs' fold-change state —
    # they live only here and are read at optimise time, so configuring them
    # never disturbs the Bar / Line plots.
    fc_row = QWidget(opt_panel)
    fc_row_layout = QHBoxLayout(fc_row)
    fc_row_layout.setContentsMargins(0, 0, 0, 0)
    fc_row_layout.addWidget(QLabel("Fold change —  Control:", fc_row))
    ctx.app._cell_gating_opt_fc_control_combo = QComboBox(fc_row)
    ctx.app._cell_gating_opt_fc_control_combo.setMinimumWidth(120)
    ctx.app._cell_gating_opt_fc_control_combo.setToolTip(
        "Divide (or subtract) each well's fraction-on by a control well / "
        "replicate set's fraction-on at the same scanned threshold. \"—\" "
        "optimises over the raw fraction-on (the original behaviour).\n\n"
        "Note: with the Ratio operator a shared control cancels in the "
        "relative gap, so it only narrows the scan to where the control still "
        "has signal — use Subtract, or a Baseline, to actually reshape the "
        "A-vs-B separation."
    )
    fc_row_layout.addWidget(ctx.app._cell_gating_opt_fc_control_combo)
    ctx.app._cell_gating_opt_fc_ctrl_op_combo = QComboBox(fc_row)
    ctx.app._cell_gating_opt_fc_ctrl_op_combo.addItem("Ratio", "ratio")
    ctx.app._cell_gating_opt_fc_ctrl_op_combo.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(ctx.app._cell_gating_opt_fc_ctrl_op_combo)
    fc_row_layout.addWidget(QLabel("Baseline:", fc_row))
    ctx.app._cell_gating_opt_fc_baseline_combo = QComboBox(fc_row)
    ctx.app._cell_gating_opt_fc_baseline_combo.setMinimumWidth(110)
    ctx.app._cell_gating_opt_fc_baseline_combo.setToolTip(
        "Divide (or subtract) each well's fraction-on by its own fraction-on at "
        "a baseline timepoint. \"t0\" uses each well's earliest timepoint."
    )
    fc_row_layout.addWidget(ctx.app._cell_gating_opt_fc_baseline_combo)
    ctx.app._cell_gating_opt_fc_t0_op_combo = QComboBox(fc_row)
    ctx.app._cell_gating_opt_fc_t0_op_combo.addItem("Ratio", "ratio")
    ctx.app._cell_gating_opt_fc_t0_op_combo.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(ctx.app._cell_gating_opt_fc_t0_op_combo)
    fc_row_layout.addWidget(QLabel("Order:", fc_row))
    ctx.app._cell_gating_opt_fc_order_combo = QComboBox(fc_row)
    ctx.app._cell_gating_opt_fc_order_combo.addItem("Control first", "ctrl_first")
    ctx.app._cell_gating_opt_fc_order_combo.addItem("Baseline first", "t0_first")
    fc_row_layout.addWidget(ctx.app._cell_gating_opt_fc_order_combo)
    fc_row_layout.addStretch(1)
    opt_layout.addWidget(fc_row)

    # Changing the channel or either well re-derives the available timepoints and
    # auto-fills Min/Max from the data range (so the scan actually spans the
    # values — the hardcoded 0–100 default sits below typical MFI data, which is
    # why a fresh Optimize used to return the range minimum). Changing only the
    # timepoint just re-derives the range.
    for _combo in (
        ctx.app._cell_gating_opt_channel_combo,
        ctx.app._cell_gating_opt_well_a_combo,
        ctx.app._cell_gating_opt_well_b_combo,
    ):
        _combo.currentIndexChanged.connect(
            lambda _idx=0, _app=ctx.app: _cell_gating_on_opt_scope_changed(_app)
        )
    ctx.app._cell_gating_opt_timepoint_combo.currentIndexChanged.connect(
        lambda _idx=0, _app=ctx.app: _cell_gating_autofill_opt_range(_app)
    )
    ctx.app._cell_gating_opt_result_label = QLabel(
        "Choose two wells and a threshold range, then optimize.",
        opt_panel,
    )
    ctx.app._cell_gating_opt_result_label.setObjectName("Muted")
    ctx.app._cell_gating_opt_result_label.setWordWrap(True)
    opt_layout.addWidget(ctx.app._cell_gating_opt_result_label)
    root.addWidget(opt_panel)

    # CDFs anchor the bottom of the tab — controls + optimizer sit above,
    # CDFs (with their toolbar) are the visual readout the user reads while
    # tuning those controls, so we put them last.
    root.addWidget(build_hline_separator(parent))
    root.addWidget(build_section_header(
        parent, "Cumulative distributions (CDF)",
    ))
    root.addWidget(ctx.app._cell_gating_plot_scroll)
    root.addWidget(ctx.app._cell_gating_toolbar)

    _cell_gating_refresh_optimizer_controls(ctx.app)

    # Paint the themed empty state now so the canvas is never a bare white
    # block before a dataset loads (it only gets painted on data otherwise).
    _cell_gating_plot_cdf(ctx.app)


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


def _cell_gating_valid_mask(frame_df):
    """Boolean mask: rows that pass crop AND QC (absent columns ⇒ all-pass).

    The per-channel fluorescence gate (``Included``) is deliberately NOT applied:
    the CDFs and the threshold optimizer are what the user reads to SET that
    gate, so filtering by it would be circular — raising the threshold would
    shrink the very curve being read, and the optimizer would only see cells
    that already pass its own (asynchronously-rewritten) cut. Crop + QC are
    independent of the fluorescence gate, so they show the valid population
    without that feedback loop.
    """
    mask = pd.Series(True, index=frame_df.index)
    for col in ("QC_Pass", "Crop_Pass"):
        if col in frame_df.columns:
            vals = pd.to_numeric(frame_df[col], errors="coerce").fillna(1)
            mask = mask & vals.eq(1)
    return mask


def _cell_gating_valid_rows(frame_df):
    """Rows of ``frame_df`` selected by :func:`_cell_gating_valid_mask`."""
    return frame_df[_cell_gating_valid_mask(frame_df)]


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
    _cell_gating_refresh_optimizer_controls(app)

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


def cell_gating_on_use_scatter_gates_change(app) -> None:
    """Toggle scatter-gate inclusion: persist, restyle, and recompute Included.

    Mirrors :func:`cell_gating_on_gating_change` — reads the checkbox into the
    flag, greys the now-inactive FluorGating edits, saves the cell_gating block,
    and kicks the background gating pass so every plot/stat/export picks up the
    new population through the ``Included`` chokepoint.
    """
    cb = getattr(app, "_cell_gating_use_scatter_gates_cb", None)
    app._cell_gating_use_scatter_gates = bool(cb.isChecked()) if cb is not None else False
    _cell_gating_apply_scatter_gate_mode(app)
    _cell_gating_refresh_gate_filter(app)  # enable/disable this layer's Gates button
    _cell_gating_refresh_gate_summary(app)
    _cell_gating_persist_params(app)
    app._cell_gating_last_gate_sig = _scatter_gate_signature(app)
    cell_gating_start_gating_worker(app)


def cell_gating_on_use_scatter_thresh_change(app) -> None:
    """Toggle scatter-gate thresholding (fraction-on by gate).

    Independent of the gating/inclusion toggle: it only changes how fraction-on
    is computed (Gate_On column vs the ThreshFracOn cutoff). Persist the flag and
    re-run the gating pass so the Gate_On column + the stats cache refresh.
    """
    cb = getattr(app, "_cell_gating_use_scatter_thresh_cb", None)
    app._cell_gating_use_scatter_thresh = bool(cb.isChecked()) if cb is not None else False
    _cell_gating_apply_scatter_gate_mode(app)
    _cell_gating_refresh_gate_filter(app)  # enable/disable this layer's Gates button
    _cell_gating_refresh_gate_summary(app)
    _cell_gating_persist_params(app)
    app._cell_gating_last_gate_sig = _scatter_gate_signature(app)
    cell_gating_start_gating_worker(app)


# The gating (inclusion) and thresholding (fraction-on) layers each keep their
# own gate selection so they can be configured separately.
_GATE_MODES = ("gating", "thresh")
_GATE_CHOICES_ATTR = {
    "gating": "_cell_gating_gating_choices",
    "thresh": "_cell_gating_thresh_choices",
}
_GATE_BUTTON_ATTR = {
    "gating": "_cell_gating_gating_gate_btn",
    "thresh": "_cell_gating_thresh_gate_btn",
}
_GATE_MODE_ACTIVE_ATTR = {
    "gating": "_cell_gating_use_scatter_gates",
    "thresh": "_cell_gating_use_scatter_thresh",
}


def cell_gating_selected_scatter_gates(app, mode: str = "gating") -> list:
    """Gates selected for *mode* ("gating" / "thresh"), as a union.

    Each layer's selection is a set of gate names; ``None`` means "all gates"
    (the default, which also auto-includes gates drawn later). An explicit
    (possibly empty) set selects exactly those names."""
    gates = getattr(app, "_scatter_gates", None) or []
    choices = getattr(app, _GATE_CHOICES_ATTR[mode], None)
    if choices is None:
        return list(gates)
    chosen = set(choices)
    return [g for g in gates if str(g.get("name") or "") in chosen]


def _cell_gating_gate_names(app) -> list:
    return [str(g.get("name") or "")
            for g in (getattr(app, "_scatter_gates", None) or [])
            if str(g.get("name") or "")]


def _cell_gating_set_gate_choices(app, mode: str, choices) -> None:
    """Commit *mode*'s selection (``None`` = all), then persist + recompute.

    Collapses a full explicit set back to ``None`` so newly drawn gates keep
    being auto-included while the user is effectively at "all"."""
    if choices is not None and set(choices) == set(_cell_gating_gate_names(app)):
        choices = None
    setattr(app, _GATE_CHOICES_ATTR[mode],
            None if choices is None else set(choices))
    _cell_gating_refresh_gate_filter(app)
    _cell_gating_refresh_gate_summary(app)
    _cell_gating_persist_params(app)
    if _cell_gating_scatter_mode_on(app):
        app._cell_gating_last_gate_sig = _scatter_gate_signature(app)
        cell_gating_start_gating_worker(app)


def _open_gate_filter_menu(app, mode: str) -> None:
    """Pop up a checkbox menu under *mode*'s Gates button (multi-select).

    Mirrors the scatter timepoint picker: one checkbox per named gate plus
    All / None actions. Checking several combines them (union)."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    btn = getattr(app, _GATE_BUTTON_ATTR[mode], None)
    if btn is None:
        return
    names = _cell_gating_gate_names(app)
    choices = getattr(app, _GATE_CHOICES_ATTR[mode], None)
    selected = set(names) if choices is None else set(choices)

    menu = QMenu(btn)
    if not names:
        empty = QAction("No gates drawn yet", menu)
        empty.setEnabled(False)
        menu.addAction(empty)
    for nm in names:
        cb = QCheckBox(nm, menu)
        cb.setChecked(nm in selected)

        def _make(name=nm):
            def _on(checked: bool) -> None:
                cur = getattr(app, _GATE_CHOICES_ATTR[mode], None)
                cur = set(names) if cur is None else set(cur)
                cur.add(name) if checked else cur.discard(name)
                _cell_gating_set_gate_choices(app, mode, cur)
            return _on

        cb.toggled.connect(_make())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)
    if names:
        menu.addSeparator()
        all_a = QAction("All", menu)
        all_a.triggered.connect(
            lambda _=False: _cell_gating_set_gate_choices(app, mode, None))
        menu.addAction(all_a)
        none_a = QAction("None", menu)
        none_a.triggered.connect(
            lambda _=False: _cell_gating_set_gate_choices(app, mode, set()))
        menu.addAction(none_a)
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


def _cell_gating_gate_button_text(app, mode: str) -> str:
    names = _cell_gating_gate_names(app)
    if not names:
        return "No gates"
    choices = getattr(app, _GATE_CHOICES_ATTR[mode], None)
    if choices is None:
        return "All gates"
    chosen = [n for n in names if n in set(choices)]
    if not chosen:
        return "None"
    if len(chosen) == 1:
        return chosen[0]
    return f"{len(chosen)} of {len(names)} gates"


def _cell_gating_refresh_gate_filter(app) -> None:
    """Sync both Gates buttons (text + enabled) to the current gates, pruning any
    explicitly-selected names that no longer exist (in each layer's set)."""
    names = set(_cell_gating_gate_names(app))
    for mode in _GATE_MODES:
        choices = getattr(app, _GATE_CHOICES_ATTR[mode], None)
        if choices is not None:
            pruned = set(choices) & names
            setattr(app, _GATE_CHOICES_ATTR[mode],
                    None if (pruned == names and names) else pruned)
        btn = getattr(app, _GATE_BUTTON_ATTR[mode], None)
        if btn is not None:
            btn.setText(_cell_gating_gate_button_text(app, mode))
            # Each Gates button is only relevant when its own layer is active.
            btn.setEnabled(bool(getattr(app, _GATE_MODE_ACTIVE_ATTR[mode], False)))


def _cell_gating_scatter_mode_on(app) -> bool:
    """True when either scatter-gate layer (gating or thresholding) is active —
    both consume the selected gate, so the dropdown is relevant for either."""
    return bool(getattr(app, "_cell_gating_use_scatter_gates", False)
                or getattr(app, "_cell_gating_use_scatter_thresh", False))


def _gate_geom_sig(gates):
    return tuple(
        (g.get("x_col"), g.get("y_col"),
         round(float(g.get("x0", 0.0)), 6), round(float(g.get("y0", 0.0)), 6),
         round(float(g.get("x1", 0.0)), 6), round(float(g.get("y1", 0.0)), 6))
        for g in gates
    )


def _scatter_gate_signature(app):
    """A hashable snapshot of what drives the gating pass — the per-layer gate
    selections (geometry + axis cols) and the two mode flags, so toggling a
    mode or changing either selection re-runs.

    Used to skip a redundant gating pass when re-entering the tab with nothing
    changed — see :func:`cell_gating_sync_scatter_gates`."""
    return (
        bool(getattr(app, "_cell_gating_use_scatter_gates", False)),
        bool(getattr(app, "_cell_gating_use_scatter_thresh", False)),
        _gate_geom_sig(cell_gating_selected_scatter_gates(app, "gating")),
        _gate_geom_sig(cell_gating_selected_scatter_gates(app, "thresh")),
    )


def cell_gating_sync_scatter_gates(app) -> None:
    """Re-sync the Cell Gating tab to the scatter gates.

    Called immediately whenever a gate is drawn / edited / cleared on the Scatter
    Plot tab (via ``_scatter_gates_changed_cb``) and on tab (re)activation as a
    backstop. Refresh the summary label always; when scatter-gate inclusion is on
    and the gate set changed since the last pass (signature-guarded so repeat
    calls are cheap), re-run the gating worker so every other plot/stat/export
    reflects the new gates. The per-cell scatter keeps the full cloud regardless
    (``df_included_mask(include_gate=False)``), so recomputing on every change is
    safe — the gated cluster's own points never vanish from under the gate.
    """
    if not hasattr(app, "_cell_gating_use_scatter_gates_cb"):
        return  # tab not built yet
    _cell_gating_refresh_gate_filter(app)
    _cell_gating_apply_scatter_gate_mode(app)
    _cell_gating_refresh_gate_summary(app)
    if not _cell_gating_scatter_mode_on(app):
        return
    sig = _scatter_gate_signature(app)
    if sig != getattr(app, "_cell_gating_last_gate_sig", None):
        app._cell_gating_last_gate_sig = sig
        cell_gating_start_gating_worker(app)


def _cell_gating_apply_scatter_gate_mode(app) -> None:
    """Grey out the edits each scatter-gate mode replaces, so an editable field
    never misleads:

    * scatter-gate *inclusion* on → the FluorGating edits no longer drive
      Included, so disable them;
    * scatter-gate *thresholding* (fraction-on) on → the gate(s) define the "on"
      population instead of the per-channel ThreshFracOn cut, so disable those.

    (Each layer's Gates button is enabled per-layer in
    _cell_gating_refresh_gate_filter.)"""
    gating_on = bool(getattr(app, "_cell_gating_use_scatter_gates", False))
    for edit in (getattr(app, "_cell_gating_fluor_gate_edits", {}) or {}).values():
        edit.setEnabled(not gating_on)
    thresh_on = bool(getattr(app, "_cell_gating_use_scatter_thresh", False))
    for edit in (getattr(app, "_cell_gating_thresh_frac_edits", {}) or {}).values():
        edit.setEnabled(not thresh_on)


def _gate_target_phrase(app, mode: str) -> str:
    """Short description of *mode*'s selected gates for the summary line."""
    gates = cell_gating_selected_scatter_gates(app, mode)
    if not gates:
        return "no gates selected"
    if getattr(app, _GATE_CHOICES_ATTR[mode], None) is None:
        return f"all {len(gates)} gate(s)"
    if len(gates) == 1:
        return f"gate “{gates[0].get('name')}”"
    return "gates " + ", ".join(str(g.get("name")) for g in gates)


def _cell_gating_refresh_gate_summary(app) -> None:
    """Update the gate summary label describing each active layer's selection.
    Refreshed on build, toggle, gate change, and load."""
    lbl = getattr(app, "_cell_gating_gate_summary", None)
    if lbl is None:
        return
    if not _cell_gating_scatter_mode_on(app):
        lbl.setText("")
        return
    if not (getattr(app, "_scatter_gates", None) or []):
        lbl.setText(
            "No scatter gates drawn yet — draw one on the Scatter Plot tab."
        )
        return
    parts = []
    if getattr(app, "_cell_gating_use_scatter_gates", False):
        parts.append(f"Inclusion: {_gate_target_phrase(app, 'gating')}")
    if getattr(app, "_cell_gating_use_scatter_thresh", False):
        parts.append(f"Fraction-on: {_gate_target_phrase(app, 'thresh')}")
    lbl.setText("  •  ".join(parts))


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
        # Mirror the new value onto the plotting ctxbar quick-edit so it reflects
        # the set threshold (e.g. after auto-threshold or a Cell Gating edit)
        # instead of showing its 50.0 default.
        refresh = getattr(app, "_refresh_thresh_frac_quick_edit", None)
        if callable(refresh):
            refresh()
    except ValueError:
        swallowed("cell gating: non-numeric ThreshFracOn edit ignored")


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
    worker.failed.connect(lambda msg: _cell_gating_on_failed(app, msg))
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
            self.failed.emit(str(exc))
            self.done.emit({})
            return
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
    ``logging.Handler`` installed by ``allwell._attach_log_ring_buffer``
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
            swallowed()  # was: pass (silent except)
        return
    channels = [str(ch).strip().lower() for ch in (app._app_state.channel.fluor_channels or []) if str(ch).strip()]
    if not channels:
        try:
            app._set_status(
                "Auto-threshold: no fluorescence channels detected in the loaded dataset."
            )
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    # Stop any previous worker still running.
    prev = getattr(app, "_cell_gating_auto_worker", None)
    if prev is not None and prev.isRunning():
        try:
            prev.wait(50)
        except Exception:
            swallowed()  # was: pass (silent except)
    btn = getattr(app, "_cell_gating_auto_btn", None)
    if btn is not None:
        btn.setEnabled(False)
        btn.setText("Auto-thresholding…")

    def _on_progress(msg: str) -> None:
        try:
            app._set_status(msg)
        except Exception:
            swallowed()  # was: pass (silent except)

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
                swallowed()  # was: pass (silent except)
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
                swallowed()  # silent except
                continue
        # Persist + re-render the CDF + kick the gating worker exactly as
        # the user typing a new ThreshFracOn would.
        try:
            cell_gating_on_threshold_frac_on_change(app)
        except Exception:
            swallowed()  # was: pass (silent except)
        try:
            app._set_status(
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
            app._set_status(f"Auto-threshold failed: {msg}")
        except Exception:
            swallowed()  # was: pass (silent except)

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


def _earliest_timepoint(df):
    """The earliest ``(kind, value)`` timepoint token present in *df* (or None)."""
    tps = _distinct_timepoints(df)
    if not tps:
        return None
    return sorted(tps, key=lambda t: (0, t[1]) if t[0] == "hours" else (1, str(t[1])))[0]


def _cell_gating_opt_fc_inputs(app, *, channel_key, well_a, well_b, timepoint, ratios):
    """Resolve the optimizer's fold-change config into optimizer kwargs + a note.

    Reads the optimizer-local FC combos (independent of the plot tabs), resolves
    the chosen control label into a pooled control population at the scan
    timepoint, and each well's baseline-timepoint population. Returns
    ``(kwargs, note)`` where *kwargs* feeds ``_optimize_thresh_frac_on_between_wells``
    / ``_scan_fc_fractions`` (empty ⇒ raw fraction-on) and *note* is a status
    suffix describing the active normalisation.
    """
    from well_viewer.domain.fold_change import resolve_control_wells

    ctrl_combo = getattr(app, "_cell_gating_opt_fc_control_combo", None)
    base_combo = getattr(app, "_cell_gating_opt_fc_baseline_combo", None)
    ctrl_label = str(ctrl_combo.currentText()).strip() if ctrl_combo is not None else ""
    baseline_data = base_combo.currentData() if base_combo is not None else None

    kwargs: dict = {
        "op_ctrl": (app._cell_gating_opt_fc_ctrl_op_combo.currentData()
                    if getattr(app, "_cell_gating_opt_fc_ctrl_op_combo", None) else "ratio") or "ratio",
        "op_t0": (app._cell_gating_opt_fc_t0_op_combo.currentData()
                  if getattr(app, "_cell_gating_opt_fc_t0_op_combo", None) else "ratio") or "ratio",
        "order": (app._cell_gating_opt_fc_order_combo.currentData()
                  if getattr(app, "_cell_gating_opt_fc_order_combo", None) else "ctrl_first") or "ctrl_first",
    }
    notes: list = []

    # vs control — pool the resolved control wells' valid values at this scan tp.
    if ctrl_label and ctrl_label != "—":
        ctrl_wells = resolve_control_wells(app, ctrl_label)
        chunks = [
            _valid_values_for_channel(app._get_rows(w), channel_key, ratios, timepoint)
            for w in ctrl_wells if w in (app._app_state.dataset.well_paths or {})
        ]
        chunks = [c for c in chunks if c.size]
        if chunks:
            kwargs["ctrl_values"] = np.concatenate(chunks)
            notes.append(f"vs {ctrl_label}")

    # vs baseline timepoint — each well's own population at the baseline tp.
    if baseline_data is not None:
        def _base_vals(well):
            df = app._get_rows(well)
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


def cell_gating_optimize_thresh_frac_on(app) -> None:
    """Scan a threshold range and apply the best two-well ThreshFracOn cut."""
    label = getattr(app, "_cell_gating_opt_result_label", None)

    def _status(msg: str) -> None:
        if label is not None:
            label.setText(msg)
        try:
            app._set_status(msg)
        except Exception:
            swallowed()

    try:
        channel_combo = app._cell_gating_opt_channel_combo
        well_a_combo = app._cell_gating_opt_well_a_combo
        well_b_combo = app._cell_gating_opt_well_b_combo
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
        lo = float(app._cell_gating_opt_min_edit.text())
        hi = float(app._cell_gating_opt_max_edit.text())
        steps = int(float(app._cell_gating_opt_steps_edit.text()))
        tp_combo = getattr(app, "_cell_gating_opt_timepoint_combo", None)
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

    ratios = getattr(app._app_state.channel, "ratio_index", None) or {}
    try:
        fc_kwargs, fc_note = _cell_gating_opt_fc_inputs(
            app, channel_key=channel_key, well_a=well_a, well_b=well_b,
            timepoint=timepoint, ratios=ratios,
        )
    except Exception as exc:
        _status(f"Optimize ThreshFracOn: fold-change inputs not ready ({exc}).")
        return

    try:
        result = _optimize_thresh_frac_on_between_wells(
            app._get_rows(well_a),
            app._get_rows(well_b),
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

    edit = (getattr(app, "_cell_gating_thresh_frac_edits", {}) or {}).get(channel_key)
    if edit is None:
        _status(f"Optimize ThreshFracOn: no ThreshFracOn field for {channel_key}.")
        return
    edit.setText(f"{result.threshold:.6g}")
    cell_gating_on_threshold_frac_on_change(app)
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
            app,
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


def _cell_gating_show_opt_curve_popup(
    app,
    *,
    channel_key: str,
    well_a: str,
    well_b: str,
    timepoint,
    lo: float,
    hi: float,
    steps: int,
    result: "ThreshFracOptimizationResult",
    fc_kwargs: "dict | None" = None,
) -> None:
    """Open (or refresh) a non-modal window plotting the optimizer's diff
    curve. Re-derives values_a, values_b, and the per-threshold fractions
    locally (through the SAME fold-change scan the optimizer uses) so the
    plotted curve matches the metric ``argmax`` ran on.

    The window stays open across consecutive Optimize clicks — subsequent
    clicks just repaint the existing axes, so the user can iterate on
    wells / timepoints / range and watch the curve update.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    ratios = getattr(app._app_state.channel, "ratio_index", None) or {}
    values_a = _valid_values_for_channel(
        app._get_rows(well_a), channel_key, ratios, timepoint,
    )
    values_b = _valid_values_for_channel(
        app._get_rows(well_b), channel_key, ratios, timepoint,
    )
    thresholds = np.linspace(lo, hi, int(steps))
    # Use the SAME scan the optimizer ran (raw, or fold-change-adjusted) so the
    # popup curve is exactly the metric argmax operated on.
    frac_a, frac_b, diffs = _scan_fc_fractions(
        thresholds, values_a, values_b, **(fc_kwargs or {}),
    )

    popup = getattr(app, "_cell_gating_opt_curve_popup", None)
    if popup is None or not popup.isVisible():
        popup = QDialog(getattr(app, "_cell_gating_frame", None) or app)
        popup.setWindowTitle("Optimizer curve")
        popup.resize(820, 460)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        # Wire the matplotlib toolbar so the user can pan/zoom/save the
        # curve while keeping the popup non-modal.
        from widgets.mpl_toolbar import MplToolbar
        toolbar = MplToolbar(canvas, popup)
        layout.addWidget(toolbar)
        popup.finished.connect(
            lambda _r: setattr(app, "_cell_gating_opt_curve_popup", None)
        )
        popup._fig = fig
        popup._canvas = canvas
        app._cell_gating_opt_curve_popup = popup
        popup.show()

    fig = popup._fig
    fig.clear()
    bg_panel = get_color("BG_PANEL")
    txt_pri = get_color("TXT_PRI")
    txt_mut = get_color("TXT_MUT")
    accent = get_color("ACCENT")
    fig.patch.set_facecolor(bg_panel)
    ax = fig.add_subplot(111)
    ax.set_facecolor(bg_panel)
    for spine in ax.spines.values():
        spine.set_color(txt_mut)
    ax.tick_params(colors=txt_mut)

    ax.plot(thresholds, frac_a, color="#4FC3F7", lw=1.4,
            label=f"fraction-on({well_a})")
    ax.plot(thresholds, frac_b, color="#FF8A65", lw=1.4,
            label=f"fraction-on({well_b})")
    ax.plot(thresholds, diffs, color=accent, lw=1.8,
            label="|Δ| / max(|fa|, |fb|)")
    ax.axvline(
        result.threshold, color=txt_pri, lw=1.2, ls="--",
        label=f"chosen threshold = {result.threshold:.4g}",
    )
    ax.set_xlim(float(thresholds[0]), float(thresholds[-1]))
    ax.set_ylim(*_opt_curve_ylim(frac_a, frac_b, diffs))
    ax.set_xlabel(f"Threshold ({channel_key})", color=txt_pri)
    ax.set_ylabel("Fraction-on / relative gap", color=txt_pri)
    tp_note = "" if timepoint is None else f" @ {_timepoint_label(timepoint)}"
    ax.set_title(
        f"{channel_key}: {well_a} vs {well_b}{tp_note} "
        f"— scan {lo:g}…{hi:g}, {int(steps)} points",
        color=txt_pri, fontsize=11,
    )
    leg = ax.legend(loc="upper right", framealpha=0.0, facecolor="none", fontsize=9)
    if leg is not None:
        for txt in leg.get_texts():
            txt.set_color(txt_pri)
    fig.tight_layout()
    popup._canvas.draw_idle()
    popup.raise_()
    popup.activateWindow()


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


def _cell_gating_optimizer_channel_specs(app) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = [
        (ch, ch.upper()) for ch in list(app._app_state.channel.fluor_channels)
    ]
    for ratio in list(app._app_state.channel.ratio_metrics or []):
        try:
            label = app._ratio_label_for(ratio)
        except Exception:
            label = ratio.name
        specs.append((ratio.key(), f"{label} (ratio)"))
    return specs


def _cell_gating_refresh_optimizer_controls(app) -> None:
    """Refresh optimizer channel/well dropdowns after data or ratios change."""
    channel_combo = getattr(app, "_cell_gating_opt_channel_combo", None)
    well_a_combo = getattr(app, "_cell_gating_opt_well_a_combo", None)
    well_b_combo = getattr(app, "_cell_gating_opt_well_b_combo", None)
    if channel_combo is None or well_a_combo is None or well_b_combo is None:
        return

    prev_channel = channel_combo.currentData()
    channel_combo.blockSignals(True)
    channel_combo.clear()
    for key, label in _cell_gating_optimizer_channel_specs(app):
        channel_combo.addItem(label, key)
    if prev_channel:
        idx = channel_combo.findData(prev_channel)
        if idx >= 0:
            channel_combo.setCurrentIndex(idx)
    channel_combo.blockSignals(False)

    wells = sorted(list(app._app_state.dataset.well_paths or {}), key=app._parse_rc)
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

    btn = getattr(app, "_cell_gating_opt_btn", None)
    if btn is not None:
        btn.setEnabled(bool(channel_combo.count() and len(wells) >= 2))

    _cell_gating_refresh_opt_timepoints(app)
    _cell_gating_refresh_opt_fc_controls(app)
    _cell_gating_autofill_opt_range(app)


def _cell_gating_on_opt_scope_changed(app) -> None:
    """React to a channel/well change: re-list timepoints, then re-fit the range."""
    _cell_gating_refresh_opt_timepoints(app)
    _cell_gating_refresh_opt_fc_controls(app)
    _cell_gating_autofill_opt_range(app)


_OPT_FC_T0_TOKEN = "__t0__"


def _cell_gating_refresh_opt_fc_controls(app) -> None:
    """Populate the optimizer's fold-change Control + Baseline combos.

    Control candidates mirror the plot tabs' Control combo (well tokens +
    replicate-set names, with the ``" (well)"`` collision suffix) via
    ``fold_change_controls._all_member_labels``. Baseline offers "—",
    "t0 (first timepoint)", and the explicit timepoints shared by wells A/B.
    Selections are preserved across refreshes; this state is local to the
    optimizer and never touches ``app._app_state.plot.fc_*``.
    """
    from well_viewer.domain.fold_change_controls import _all_member_labels

    ctrl = getattr(app, "_cell_gating_opt_fc_control_combo", None)
    base = getattr(app, "_cell_gating_opt_fc_baseline_combo", None)
    if ctrl is not None:
        prev = ctrl.currentText()
        items = ["—", *_all_member_labels(app)]
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
        well_a_combo = getattr(app, "_cell_gating_opt_well_a_combo", None)
        well_b_combo = getattr(app, "_cell_gating_opt_well_b_combo", None)
        seen: set = set()
        tps: list = []
        for combo_w in (well_a_combo, well_b_combo):
            if combo_w is None:
                continue
            label = str(combo_w.currentText()).strip()
            if not label or label not in (app._app_state.dataset.well_paths or {}):
                continue
            for tp in _distinct_timepoints(app._get_rows(label)):
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


def _cell_gating_refresh_opt_timepoints(app) -> None:
    """Populate the optimizer timepoint dropdown from the two selected wells."""
    combo = getattr(app, "_cell_gating_opt_timepoint_combo", None)
    well_a_combo = getattr(app, "_cell_gating_opt_well_a_combo", None)
    well_b_combo = getattr(app, "_cell_gating_opt_well_b_combo", None)
    if combo is None or well_a_combo is None or well_b_combo is None:
        return
    seen: set = set()
    tps: list = []
    for combo_w in (well_a_combo, well_b_combo):
        label = str(combo_w.currentText()).strip()
        if not label or label not in (app._app_state.dataset.well_paths or {}):
            continue
        for tp in _distinct_timepoints(app._get_rows(label)):
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


def _cell_gating_autofill_opt_range(app) -> None:
    """Fit Min/Max to the selected channel's data range over the two wells.

    Pools the included finite values from Well A and Well B (at the chosen
    timepoint) and writes their min/max into the Min/Max edits, so the scan spans
    where fraction-on actually changes rather than the hardcoded 0–100 default.
    """
    min_edit = getattr(app, "_cell_gating_opt_min_edit", None)
    max_edit = getattr(app, "_cell_gating_opt_max_edit", None)
    channel_combo = getattr(app, "_cell_gating_opt_channel_combo", None)
    well_a_combo = getattr(app, "_cell_gating_opt_well_a_combo", None)
    well_b_combo = getattr(app, "_cell_gating_opt_well_b_combo", None)
    tp_combo = getattr(app, "_cell_gating_opt_timepoint_combo", None)
    if not all((min_edit, max_edit, channel_combo, well_a_combo, well_b_combo)):
        return
    channel_key = str(channel_combo.currentData() or channel_combo.currentText()).strip()
    well_a = str(well_a_combo.currentText()).strip()
    well_b = str(well_b_combo.currentText()).strip()
    valid = app._app_state.dataset.well_paths or {}
    if not channel_key or well_a not in valid or well_b not in valid:
        return
    timepoint = tp_combo.currentData() if tp_combo is not None else None
    ratios = getattr(app._app_state.channel, "ratio_index", None) or {}
    try:
        va = _valid_values_for_channel(app._get_rows(well_a), channel_key, ratios, timepoint)
        vb = _valid_values_for_channel(app._get_rows(well_b), channel_key, ratios, timepoint)
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


def _fraction_on_for_threshold(values: np.ndarray, threshold: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.count_nonzero(values > threshold) / values.size)


def _frac_curve(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """Fraction-on of *values* at each scanned threshold."""
    return np.array(
        [_fraction_on_for_threshold(values, float(t)) for t in thresholds],
        dtype=float,
    )


def _scan_fc_fractions(
    thresholds: np.ndarray,
    values_a: np.ndarray,
    values_b: np.ndarray,
    *,
    ctrl_values: "np.ndarray | None" = None,
    base_values_a: "np.ndarray | None" = None,
    base_values_b: "np.ndarray | None" = None,
    op_ctrl: str = "ratio",
    op_t0: str = "ratio",
    order: str = "ctrl_first",
):
    """Per-threshold (fa, fb, relative-gap) curves, optionally fold-changed.

    Each well's raw fraction-on is computed at every scanned threshold, then —
    when control / baseline value arrays are supplied — adjusted at the SAME
    threshold via :func:`fold_change.scale_bar_value` (so numerator and the
    fold-change denominators stay self-consistent). ``ctrl_values`` is the pooled
    control population; ``base_values_*`` are each well's baseline-timepoint
    population. With all three ``None`` the result is the raw fraction-on scan
    (identical to the pre-fold-change behaviour). Returns ``(fa, fb, diffs)``
    where ``diffs`` is the relative gap ``|fa-fb| / max(|fa|, |fb|)`` (non-finite
    / zero-magnitude coerced to ``0.0`` so ``argmax`` is meaningful).

    The denominator uses the **magnitude** ``max(|fa|, |fb|)`` rather than
    ``max(fa, fb)``: under the *subtract* fold-change operator the adjusted
    fractions are differences that can go negative (a well below its control /
    baseline), and a signed ``max(fa, fb)`` denominator would zero the gap across
    the whole negative region and spike at the sign crossing — inverting the
    curve and the chosen threshold. Magnitude is sign-safe and, for the
    non-negative raw / ratio cases, identical to the old ``max(fa, fb)``.
    """
    from well_viewer.domain.fold_change import scale_bar_value

    fa = _frac_curve(values_a, thresholds)
    fb = _frac_curve(values_b, thresholds)

    fc_on = ctrl_values is not None or base_values_a is not None or base_values_b is not None
    if fc_on:
        fc = ctrl_values is not None and ctrl_values.size > 0
        fctrl = _frac_curve(ctrl_values, thresholds) if fc else None
        fbase_a = _frac_curve(base_values_a, thresholds) if base_values_a is not None else None
        fbase_b = _frac_curve(base_values_b, thresholds) if base_values_b is not None else None
        for i in range(len(thresholds)):
            fa[i], _ = scale_bar_value(
                fa[i], 0.0,
                control_mean=(fctrl[i] if fctrl is not None else None),
                t0_mean=(fbase_a[i] if fbase_a is not None else None),
                op_ctrl=op_ctrl, op_t0=op_t0, order=order,
            )
            fb[i], _ = scale_bar_value(
                fb[i], 0.0,
                control_mean=(fctrl[i] if fctrl is not None else None),
                t0_mean=(fbase_b[i] if fbase_b is not None else None),
                op_ctrl=op_ctrl, op_t0=op_t0, order=order,
            )

    abs_gap = np.abs(fa - fb)
    # Sign-safe denominator: magnitude, not signed max — see docstring. For
    # non-negative raw / ratio fractions this equals the old max(fa, fb).
    larger = np.maximum(np.abs(fa), np.abs(fb))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = np.divide(abs_gap, larger, out=np.zeros_like(abs_gap), where=larger > 0)
    diffs = np.where(np.isfinite(diffs), diffs, 0.0)
    return fa, fb, diffs


def _opt_curve_ylim(*series) -> tuple:
    """Y-limits for the optimizer-curve popup.

    Defaults to the familiar [0, 1] fraction-on frame, but expands to cover
    whatever the plotted curves actually span (with padding). Under fold change
    the adjusted fractions can be ratios (>1) or differences (<0), and the
    relative gap can exceed 1 when fa/fb straddle zero — without this they'd be
    clipped above / below the axis and vanish.
    """
    ys = np.concatenate([np.asarray(s, dtype=float).ravel() for s in series]) \
        if series else np.array([], dtype=float)
    ys = ys[np.isfinite(ys)]
    if not ys.size:
        return (-0.02, 1.02)
    ymin = min(-0.02, float(ys.min()))
    ymax = max(1.02, float(ys.max()))
    pad = 0.02 * (ymax - ymin)
    return (ymin - pad, ymax + pad)


def _timepoint_label(timepoint) -> str:
    """Human-readable label for a ``(kind, value)`` timepoint token."""
    kind, value = timepoint
    if kind == "hours":
        return f"{value:g} h"
    return str(value)


def _distinct_timepoints(df) -> list[tuple[str, object]]:
    """Ordered distinct ``(kind, value)`` timepoint tokens present in ``df``.

    Prefers the numeric ``timepoint_hours`` column; falls back to the
    ``timepoint`` string column. Returns ``[]`` when neither is available.
    """
    if df is None or getattr(df, "empty", True):
        return []
    if "timepoint_hours" in df.columns:
        h = pd.to_numeric(df["timepoint_hours"], errors="coerce").dropna().unique()
        if len(h):
            return [("hours", float(v)) for v in sorted(float(x) for x in h)]
    if "timepoint" in df.columns:
        s = df["timepoint"].fillna("").astype(str).str.strip()
        vals = [v for v in s.unique() if v != ""]

        def _key(v: str):
            try:
                return (0, float(v), "")
            except ValueError:
                return (1, 0.0, v.lower())

        return [("str", v) for v in sorted(vals, key=_key)]
    return []


def _filter_rows_by_timepoint(df, timepoint):
    """Rows of ``df`` at ``timepoint`` (a ``(kind, value)`` token).

    ``None`` ⇒ all rows. Unknown / missing timepoint columns ⇒ all rows (so the
    optimizer degrades to its previous all-timepoints behaviour).
    """
    if timepoint is None or df is None or df.empty:
        return df
    kind, value = timepoint
    if kind == "hours" and "timepoint_hours" in df.columns:
        col = pd.to_numeric(df["timepoint_hours"], errors="coerce")
        return df.loc[col == value]
    if "timepoint" in df.columns:
        col = df["timepoint"].fillna("").astype(str).str.strip()
        sel = col == str(value)
        if not sel.any():
            num = pd.to_numeric(col, errors="coerce")
            try:
                sel = num == float(value)
            except (TypeError, ValueError):
                pass
        return df.loc[sel]
    return df


def _valid_values_for_channel(df, channel_key: str, ratios=None,
                              timepoint=None) -> np.ndarray:
    """Finite per-cell values for ``channel_key`` over the QC+Crop-valid
    population (at ``timepoint`` if given). Mirrors the CDF population — the
    fluorescence ``Included`` gate is deliberately not applied so the optimizer
    isn't filtered by the very threshold it's helping pick (see
    :func:`_cell_gating_valid_mask`)."""
    if df is None or df.empty:
        return np.array([], dtype=float)
    df = _filter_rows_by_timepoint(df, timepoint)
    if df is None or df.empty:
        return np.array([], dtype=float)
    val_col = channel_key if str(channel_key).startswith("ratio:") else f"{channel_key}_mean_intensity"
    values = resolve_value_series(df, val_col, ratios).to_numpy(dtype=float)
    mask = _cell_gating_valid_mask(df).to_numpy(copy=False) & np.isfinite(values)
    return values[mask]


def _optimize_thresh_frac_on_between_wells(
    df_a,
    df_b,
    *,
    channel_key: str,
    min_threshold: float,
    max_threshold: float,
    steps: int,
    ratios=None,
    timepoint=None,
    ctrl_values=None,
    base_values_a=None,
    base_values_b=None,
    op_ctrl: str = "ratio",
    op_t0: str = "ratio",
    order: str = "ctrl_first",
) -> ThreshFracOptimizationResult:
    """Return the threshold maximizing the **relative** fraction-on gap:
    ``|fraction_on(A) - fraction_on(B)| / max(fraction_on(A), fraction_on(B))``.

    The absolute gap ``|fa - fb|`` peaks wherever the populations diverge
    *most in count* — useful when both wells share an "on" tail. But for two
    skewed fluorescence distributions where most cells are barely-on, the
    absolute gap stays small even where the well *ratio* differs sharply
    (one well 5% on, the other 0.5% on is a 10× difference but only 0.045
    in absolute terms). Normalising by the larger fraction surfaces those
    relative differences instead, which is what a fraction-on cut is
    supposed to discriminate. When both fractions are zero we treat the
    relative gap as zero rather than emit ``nan``.

    ``timepoint`` is an optional ``(kind, value)`` token (see
    :func:`_distinct_timepoints`); when given, only rows at that timepoint
    contribute to each well's fraction-on.
    """
    lo = float(min_threshold)
    hi = float(max_threshold)
    if not np.isfinite(lo) or not np.isfinite(hi):
        raise ValueError("threshold range must be finite.")
    if hi < lo:
        raise ValueError("max threshold must be greater than or equal to min threshold.")
    if int(steps) < 2:
        raise ValueError("steps must be at least 2.")
    if int(steps) > 10001:
        raise ValueError("steps must be 10,001 or fewer.")

    values_a = _valid_values_for_channel(df_a, channel_key, ratios, timepoint)
    values_b = _valid_values_for_channel(df_b, channel_key, ratios, timepoint)
    if values_a.size == 0:
        raise ValueError("Well A has no valid finite values for the selected channel.")
    if values_b.size == 0:
        raise ValueError("Well B has no valid finite values for the selected channel.")

    thresholds = np.linspace(lo, hi, int(steps))
    # Raw fraction-on scan, optionally fold-change-adjusted at each threshold
    # (control / baseline value arrays supplied by the caller). With all None
    # this is the original raw-fraction relative-gap scan.
    fa, fb, diffs = _scan_fc_fractions(
        thresholds, values_a, values_b,
        ctrl_values=ctrl_values,
        base_values_a=base_values_a,
        base_values_b=base_values_b,
        op_ctrl=op_ctrl, op_t0=op_t0, order=order,
    )

    # The threshold where the relative fraction-on gap is biggest.
    # ``np.argmax`` returns the FIRST index attaining the maximum, so when
    # a run of thresholds ties for the max gap (fraction-on is a step
    # function, so ties are common between two separated populations) the
    # lowest such threshold is reported.
    best_idx = int(np.argmax(diffs))
    best_t = float(thresholds[best_idx])
    frac_a = float(fa[best_idx])
    frac_b = float(fb[best_idx])
    return ThreshFracOptimizationResult(
        threshold=best_t,
        frac_a=frac_a,
        frac_b=frac_b,
        difference=float(diffs[best_idx]),
        n_a=int(values_a.size),
        n_b=int(values_b.size),
    )


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


def _focus_cdf_xaxis(ax, vals) -> None:
    """Clamp the x-axis to the data range (+ small pad) so a gate line at 0 —
    or far outside the data — doesn't stretch the axis and cram the CDF against
    one edge. Called after the gate axvline so it has the final say."""
    if len(vals) == 0:
        return
    lo = float(np.min(vals))
    hi = float(np.max(vals))
    pad = (hi - lo) * 0.04 if hi > lo else (abs(hi) * 0.05 or 1.0)
    ax.set_xlim(lo - pad, hi + pad)


def _cell_gating_plot_cdf(app) -> None:
    ratio_data = getattr(app, "_cell_gating_ratio_data", {}) or {}
    if (not app._cell_gating_cell_areas
            and not app._cell_gating_fluor_data
            and not ratio_data):
        # No data — paint a themed empty state instead of leaving the default
        # white matplotlib canvas (a giant white block against the dark app).
        fig = getattr(app, "_cell_gating_figure", None)
        if fig is not None:
            fig.clf()
            fig.set_facecolor(get_color("BG_APP"))
            ax = fig.add_subplot(111, facecolor=get_color("BG_PANEL"))
            ax.set_axis_off()
            ax.text(
                0.5, 0.5,
                "No dataset loaded.\nOpen a results folder to view cell-gating "
                "distributions.",
                transform=ax.transAxes, ha="center", va="center",
                color=get_color("TXT_MUT"), fontsize=11, wrap=True,
            )
            canvas = getattr(app, "_cell_gating_canvas", None)
            if canvas is not None:
                canvas.draw_idle()
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
            swallowed()  # was: pass (silent except)
        _focus_cdf_xaxis(axes[0], areas)

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
            swallowed("cell gating CDF: no fluor-gate line (unparsable/missing)")
        _focus_cdf_xaxis(ax, vals)
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
        _focus_cdf_xaxis(ax, vals)
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


def _cell_gating_on_failed(app, msg: str) -> None:
    # Release the UI (hide the progress bar) and surface the error instead of
    # leaving the status bar stuck mid-pass (audit E7).
    bar = getattr(app, "_progress_bar", None)
    if bar is not None:
        bar.setValue(0)
        bar.hide()
    logger.error("Cell gating failed: %s", msg)
    show = getattr(app, "show_status_message", None)
    if callable(show):
        try:
            show(f"Cell gating failed: {msg}")
        except Exception:
            logger.exception("failed to display gating-error status")


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
            swallowed(f"cell gating: skip non-numeric ThreshFracOn for {channel!r}")
