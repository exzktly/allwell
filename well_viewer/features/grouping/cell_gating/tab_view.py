"""Cell Gating tab builder (Qt).

Constructs the tab inside *parent* and registers its widgets on
``ctx.runtime.cell_gating``, wiring every control to a handler in
:mod:`controller`. The behaviour those handlers implement — the gating workers,
the optimizers, the scatter-gate plumbing — lives there; the CDF panel is drawn
by :mod:`renderer`. This module is the only one of the three that imports the
other two, which is what keeps the package acyclic.

* :func:`build_cell_gating_tab` — construct the UI inside *parent*.
* :func:`build_cell_gating_subtab` — build it and run the initial data load.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout,
    QWidget,
)

from widgets.flow_layout import FlowLayout, flow_group
from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import (
    build_hline_separator,
    build_section_header,
    install_canvas_wheel_scroll,
)
from well_viewer.features.qc.tab_view import build_qc_gate_controls
from well_viewer.features.grouping.cell_gating.controller import (
    _GATE_BUTTON_ATTR,
    _GATE_CHOICES_ATTR,
    _cell_gating_apply_scatter_gate_mode,
    _cell_gating_autofill_opt_range,
    _cell_gating_gate_names,
    _cell_gating_on_opt_scope_changed,
    _cell_gating_refresh_gate_filter,
    _cell_gating_refresh_gate_summary,
    _cell_gating_refresh_optimizer_controls,
    _cell_gating_set_gate_choices,
    cell_gating_auto_threshold,
    cell_gating_load_cell_areas,
    cell_gating_load_threshold_frac_on,
    cell_gating_on_use_scatter_gates_change,
    cell_gating_on_use_scatter_thresh_change,
    cell_gating_optimize_thresh_frac_on,
    cell_gating_refresh_included_count,
    cell_gating_sync_scatter_gates,
    logger,
)
from well_viewer.features.grouping.cell_gating.renderer import _cell_gating_plot_cdf


# ── builder ───────────────────────────────────────────────────────────────

def build_cell_gating_tab(ctx, parent: QWidget) -> None:
    """Build the Cell Gating tab inside *parent*.

    State and widget handles are attached to the app under the
    ``_cell_gating_*`` prefix.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    # A8 burn-down: Qt widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which stamps ``app._cell_gating_*`` so the controller's legacy readers resolve.
    # The (many) non-widget state fields + optimizer choices stay on the app.
    reg = ctx.widgets

    reg.register("_cell_gating_frame", parent)
    ctx.runtime.cell_gating.cell_areas = []
    # {channel_key → QLineEdit} widget maps — containers of widget handles, so
    # they go through the registry (stamps the legacy app attr for the many
    # hasattr/getattr probes). The gate *values* live on the typed
    # ctx.state.cell_gating slice (R6); these edits are the input surface.
    reg.register("_cell_gating_fluor_gate_edits", {})
    reg.register("_cell_gating_thresh_frac_edits", {})
    ctx.runtime.cell_gating.fluor_data = {}
    ctx.runtime.cell_gating.ratio_data = {}
    ctx.runtime.cell_gating.ax = None
    ctx.runtime.cell_gating.axes_stack = []
    ctx.runtime.cell_gating.worker = None
    # Scatter-gate inclusion mode: when on, the Scatter Plot's rectangular gates
    # define Included instead of the per-channel FluorGating. The gating and
    # thresholding layers each pick their OWN set of gates (a set of names, or
    # None ⇒ all gates, which auto-includes gates drawn later).
    # Epic-cellgating: the flags + per-layer gate selections are typed state on
    # ``ctx.state.cell_gating`` (defaults: False / None), so no seeding is needed
    # here — the checkboxes below read the slice.

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
        cf_layout.addWidget(build_qc_gate_controls(ctx, control_frame))
    except Exception:
        swallowed()  # QC controls are best-effort; never block the tab build

    cf_layout.addWidget(build_hline_separator(control_frame))
    cf_layout.addWidget(build_section_header(
        control_frame, "FluorGating (Cell Inclusion)",
    ))

    # MFI-only note: FluorGating + ThreshFracOn always run against per-cell
    # mean fluorescence intensity, regardless of which Property the user
    # picks in Plotting's band.
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
    use_sg_cb.setChecked(bool(ctx.state.cell_gating.use_scatter_gates))
    use_sg_cb.setToolTip(
        "Define the Included population from the named rectangular gates drawn "
        "on the Scatter Plot tab instead of the per-channel intensity "
        "thresholds. Pick which gate filters with the dropdown. Gates are saved "
        "with the dataset."
    )
    use_sg_cb.toggled.connect(
        lambda _on: cell_gating_on_use_scatter_gates_change(ctx)
    )
    reg.register("_cell_gating_use_scatter_gates_cb", use_sg_cb)
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
        lambda _=False: _open_gate_filter_menu(ctx, "gating")
    )
    reg.register("_cell_gating_gating_gate_btn", gating_btn)
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
    use_th_cb.setChecked(bool(ctx.state.cell_gating.use_scatter_thresh))
    use_th_cb.setToolTip(
        "Define the 'on' population for fraction-on stats as the cells inside "
        "the selected gate(s), instead of cells above the per-channel "
        "ThreshFracOn cutoff. Independent of cell inclusion — no cells removed."
    )
    use_th_cb.toggled.connect(
        lambda _on: cell_gating_on_use_scatter_thresh_change(ctx)
    )
    reg.register("_cell_gating_use_scatter_thresh_cb", use_th_cb)
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
        lambda _=False: _open_gate_filter_menu(ctx, "thresh")
    )
    reg.register("_cell_gating_thresh_gate_btn", thresh_btn)
    th_layout.addWidget(thresh_btn)
    th_layout.addStretch(1)
    cf_layout.addWidget(th_row)

    reg.register("_cell_gating_gate_summary", QLabel("", control_frame))
    ctx.widgets.get("_cell_gating_gate_summary").setObjectName("Muted")
    ctx.widgets.get("_cell_gating_gate_summary").setWordWrap(True)
    cf_layout.addWidget(ctx.widgets.get("_cell_gating_gate_summary"))

    # Live inclusion readout, directly under the controls that move it.
    reg.register("_cell_gating_included_count", QLabel("", control_frame))
    _count_lbl = ctx.widgets.get("_cell_gating_included_count")
    _count_lbl.setObjectName("Muted")
    _count_lbl.setWordWrap(True)
    _count_lbl.setToolTip(
        "Cells passing every inclusion layer — manual curation, the QC debris "
        "gate, the crop region, and the cell-gating / scatter-gate layer — "
        "across all loaded wells (not just the selected ones the curves below "
        "show). Fraction-on cutoffs (ThreshFracOn) do not change this number: "
        "they define which included cells count as 'on', not which cells count."
    )
    cf_layout.addWidget(_count_lbl)
    # QC edits rewrite QC_Pass without running the gating pass, and the QC
    # controls sit on this very tab — so the readout has to follow them too.
    # QC fires a feature-agnostic callback; this tab is what registers for it,
    # so neither feature names the other and both stay one-line removable.
    ctx.runtime.qc.changed_cb = lambda: cell_gating_refresh_included_count(ctx)
    # The scatter gating module fires this whenever a gate is drawn / edited /
    # cleared on the Scatter Plot tab: refresh the summary AND recompute Included
    # immediately so every other view tracks the gates without waiting for a tab
    # switch. (Safe: the per-cell scatter shows the full cloud by default, and
    # its opt-in "Apply gates" filter is suppressed mid-draw — see
    # scatter.collect.scatter_points_gated.)
    ctx.runtime.scatter.gates_changed_cb = lambda: cell_gating_sync_scatter_gates(ctx)
    _cell_gating_refresh_gate_filter(ctx)
    _cell_gating_refresh_gate_summary(ctx)
    _cell_gating_apply_scatter_gate_mode(ctx)
    cell_gating_refresh_included_count(ctx)

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
    auto_btn.clicked.connect(lambda _=False: cell_gating_auto_threshold(ctx))
    auto_row_layout.addWidget(auto_btn)
    auto_row_layout.addStretch(1)
    reg.register("_cell_gating_auto_btn", auto_btn)
    cf_layout.addWidget(auto_row)

    reg.register("_cell_gating_scroll", QScrollArea(control_frame))
    ctx.widgets.get("_cell_gating_scroll").setWidgetResizable(True)
    ctx.widgets.get("_cell_gating_scroll").setFrameShape(QFrame.NoFrame)
    ctx.widgets.get("_cell_gating_scroll").setFixedHeight(120)
    reg.register("_cell_gating_inner", QWidget())
    QVBoxLayout(ctx.widgets.get("_cell_gating_inner"))
    ctx.widgets.get("_cell_gating_scroll").setWidget(ctx.widgets.get("_cell_gating_inner"))
    cf_layout.addWidget(ctx.widgets.get("_cell_gating_scroll"))
    root.addWidget(control_frame)

    # CDF plot area
    reg.register("_cell_gating_figure", Figure(figsize=(8, 5), dpi=100))
    reg.register("_cell_gating_canvas", FigureCanvas(ctx.widgets.get("_cell_gating_figure")))

    reg.register("_cell_gating_plot_scroll", QScrollArea(parent))
    ctx.widgets.get("_cell_gating_plot_scroll").setWidgetResizable(True)
    ctx.widgets.get("_cell_gating_plot_scroll").setFrameShape(QFrame.NoFrame)
    ctx.widgets.get("_cell_gating_plot_scroll").setWidget(ctx.widgets.get("_cell_gating_canvas"))
    # Floor the plot height so the gating controls above can never squeeze the
    # CDFs into an unreadable sliver (the outer scroll absorbs any overflow).
    ctx.widgets.get("_cell_gating_plot_scroll").setMinimumHeight(420)
    install_canvas_wheel_scroll(ctx.widgets.get("_cell_gating_canvas"), ctx.widgets.get("_cell_gating_plot_scroll"))

    from widgets.mpl_toolbar import MplToolbar
    reg.register("_cell_gating_toolbar", MplToolbar(ctx.widgets.get("_cell_gating_canvas"), parent))

    reg.register("_cell_gating_status_label", QLabel("No data loaded", parent))
    ctx.widgets.get("_cell_gating_status_label").setObjectName("Muted")
    root.addWidget(ctx.widgets.get("_cell_gating_status_label"))

    opt_panel = QWidget(parent)
    opt_layout = QVBoxLayout(opt_panel)
    opt_layout.setContentsMargins(0, 4, 0, 0)
    opt_layout.addWidget(build_hline_separator(opt_panel))
    opt_layout.addWidget(build_section_header(
        opt_panel, "Optimize ThreshFracOn between two wells",
    ))
    opt_row = QWidget(opt_panel)
    # FlowLayout + flow_group: each label stays glued to its control, and the row
    # wraps onto extra lines at narrow width instead of clipping controls off-edge.
    opt_row_layout = FlowLayout(opt_row)
    opt_row_layout.setContentsMargins(0, 0, 0, 0)
    opt_channel = reg.register("_cell_gating_opt_channel_combo", QComboBox(opt_row))
    opt_channel.setMinimumWidth(140)
    opt_row_layout.addWidget(flow_group(opt_row, "Channel:", opt_channel))
    opt_well_a = reg.register("_cell_gating_opt_well_a_combo", QComboBox(opt_row))
    opt_well_a.setMinimumWidth(90)
    opt_row_layout.addWidget(flow_group(opt_row, "Well A:", opt_well_a))
    opt_well_b = reg.register("_cell_gating_opt_well_b_combo", QComboBox(opt_row))
    opt_well_b.setMinimumWidth(90)
    opt_row_layout.addWidget(flow_group(opt_row, "Well B:", opt_well_b))
    opt_tp = reg.register("_cell_gating_opt_timepoint_combo", QComboBox(opt_row))
    opt_tp.setMinimumWidth(110)
    opt_tp.setToolTip(
        "Restrict the fraction-on comparison to one timepoint, or pool all "
        "timepoints. Pooling across timepoints where the wells are both 'off' "
        "early dilutes the difference, so picking the relevant timepoint gives a "
        "sharper threshold."
    )
    opt_row_layout.addWidget(flow_group(opt_row, "Timepoint:", opt_tp))
    opt_min = reg.register("_cell_gating_opt_min_edit", QLineEdit("0.0", opt_row))
    opt_min.setFixedWidth(80)
    opt_row_layout.addWidget(flow_group(opt_row, "Min:", opt_min))
    opt_max = reg.register("_cell_gating_opt_max_edit", QLineEdit("100.0", opt_row))
    opt_max.setFixedWidth(80)
    opt_row_layout.addWidget(flow_group(opt_row, "Max:", opt_max))
    opt_steps = reg.register("_cell_gating_opt_steps_edit", QLineEdit("101", opt_row))
    opt_steps.setFixedWidth(64)
    opt_steps.setToolTip(
        "Number of thresholds scanned across the Min–Max range, evenly "
        "spaced via numpy.linspace. NOT a step size — \"101\" scans 101 "
        "thresholds (every 1 unit across a 0–100 range). Higher values "
        "give a finer scan at the cost of a little compute."
    )
    opt_row_layout.addWidget(flow_group(opt_row, "# Points:", opt_steps))
    from PySide6.QtWidgets import QPushButton as _QPushButton
    opt_btn = _QPushButton("Optimize ThreshFracOn", opt_row)
    opt_btn.setProperty("variant", "secondary")
    opt_btn.setToolTip(
        "Scan # Points thresholds between Min and Max for the selected "
        "channel, compute fraction-on in Well A and Well B at each "
        "threshold, then apply the threshold that maximizes their "
        "relative fraction-on gap, |Δ| / max(fa, fb)."
    )
    opt_btn.clicked.connect(lambda _=False: cell_gating_optimize_thresh_frac_on(ctx))
    reg.register("_cell_gating_opt_btn", opt_btn)
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
    fc_row_layout = FlowLayout(fc_row)
    fc_row_layout.setContentsMargins(0, 0, 0, 0)
    fc_control = reg.register("_cell_gating_opt_fc_control_combo", QComboBox(fc_row))
    fc_control.setMinimumWidth(120)
    fc_control.setToolTip(
        "Divide (or subtract) each well's fraction-on by a control well / "
        "replicate set's fraction-on at the same scanned threshold. \"—\" "
        "optimises over the raw fraction-on (the original behaviour).\n\n"
        "Note: with the Ratio operator a shared control cancels in the "
        "relative gap, so it only narrows the scan to where the control still "
        "has signal — use Subtract, or a Baseline, to actually reshape the "
        "A-vs-B separation."
    )
    fc_ctrl_op = reg.register("_cell_gating_opt_fc_ctrl_op_combo", QComboBox(fc_row))
    fc_ctrl_op.addItem("Ratio", "ratio")
    fc_ctrl_op.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(flow_group(fc_row, "Fold change —  Control:", fc_control, fc_ctrl_op))
    fc_baseline = reg.register("_cell_gating_opt_fc_baseline_combo", QComboBox(fc_row))
    fc_baseline.setMinimumWidth(110)
    fc_baseline.setToolTip(
        "Divide (or subtract) each well's fraction-on by its own fraction-on at "
        "a baseline timepoint. \"t0\" uses each well's earliest timepoint."
    )
    fc_t0_op = reg.register("_cell_gating_opt_fc_t0_op_combo", QComboBox(fc_row))
    fc_t0_op.addItem("Ratio", "ratio")
    fc_t0_op.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(flow_group(fc_row, "Baseline:", fc_baseline, fc_t0_op))
    fc_order = reg.register("_cell_gating_opt_fc_order_combo", QComboBox(fc_row))
    fc_order.addItem("Control first", "ctrl_first")
    fc_order.addItem("Baseline first", "t0_first")
    fc_row_layout.addWidget(flow_group(fc_row, "Order:", fc_order))
    fc_row_layout.addStretch(1)
    opt_layout.addWidget(fc_row)

    # Changing the channel or either well re-derives the available timepoints and
    # auto-fills Min/Max from the data range (so the scan actually spans the
    # values — the hardcoded 0–100 default sits below typical MFI data, which is
    # why a fresh Optimize used to return the range minimum). Changing only the
    # timepoint just re-derives the range.
    for _combo in (
        ctx.widgets.get("_cell_gating_opt_channel_combo"),
        ctx.widgets.get("_cell_gating_opt_well_a_combo"),
        ctx.widgets.get("_cell_gating_opt_well_b_combo"),
    ):
        _combo.currentIndexChanged.connect(
            lambda _idx=0: _cell_gating_on_opt_scope_changed(ctx)
        )
    ctx.widgets.get("_cell_gating_opt_timepoint_combo").currentIndexChanged.connect(
        lambda _idx=0: _cell_gating_autofill_opt_range(ctx)
    )
    opt_result_label = reg.register("_cell_gating_opt_result_label", QLabel(
        "Choose two wells and a threshold range, then optimize.",
        opt_panel,
    ))
    opt_result_label.setObjectName("Muted")
    opt_result_label.setWordWrap(True)
    opt_layout.addWidget(opt_result_label)
    root.addWidget(opt_panel)

    # CDFs anchor the bottom of the tab — controls + optimizer sit above,
    # CDFs (with their toolbar) are the visual readout the user reads while
    # tuning those controls, so we put them last.
    root.addWidget(build_hline_separator(parent))
    root.addWidget(build_section_header(
        parent, "Cumulative distributions (CDF)",
    ))
    root.addWidget(ctx.widgets.get("_cell_gating_plot_scroll"))
    root.addWidget(ctx.widgets.get("_cell_gating_toolbar"))

    _cell_gating_refresh_optimizer_controls(ctx)

    # Paint the themed empty state now so the canvas is never a bare white
    # block before a dataset loads (it only gets painted on data otherwise).
    _cell_gating_plot_cdf(ctx)


# ── sub-tab entry point ───────────────────────────────────────────────────


def build_cell_gating_subtab(ctx) -> None:
    """Build the Cell Gating sub-tab content if it hasn't been built yet.

    Called on first sub-tab activation and from persistence when persisted
    thresholds need to be applied immediately. Idempotent.
    """
    if bool(ctx.runtime.cell_gating.subtab_built):
        return
    frame = ctx.runtime.cell_gating.subtab_frame
    if frame is None:
        return
    try:
        build_cell_gating_tab(ctx, frame)
        if ctx.state.dataset.well_paths:
            try:
                from well_viewer.persistence.cell_gating import (
                    load_from_pipeline_info,
                )
                cell_gating_load_cell_areas(ctx)
                load_from_pipeline_info(ctx)
                cell_gating_load_threshold_frac_on(ctx)
            except Exception:
                logger.exception("Cell Gating post-build sync failed")
    except Exception:
        logger.exception("Cell Gating sub-tab build failed")
    finally:
        ctx.runtime.cell_gating.subtab_built = True


def _open_gate_filter_menu(ctx, mode: str) -> None:
    """Pop up a checkbox menu under *mode*'s Gates button (multi-select).

    Mirrors the scatter timepoint picker: one checkbox per named gate plus
    All / None actions. Checking several combines them (union)."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    btn = getattr(ctx.runtime.cell_gating, _GATE_BUTTON_ATTR[mode], None)
    if btn is None:
        return
    names = _cell_gating_gate_names(ctx)
    choices = getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None)
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
                cur = getattr(ctx.state.cell_gating, _GATE_CHOICES_ATTR[mode], None)
                cur = set(names) if cur is None else set(cur)
                cur.add(name) if checked else cur.discard(name)
                _cell_gating_set_gate_choices(ctx, mode, cur)
            return _on

        cb.toggled.connect(_make())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)
    if names:
        menu.addSeparator()
        all_a = QAction("All", menu)
        all_a.triggered.connect(
            lambda _=False: _cell_gating_set_gate_choices(ctx, mode, None))
        menu.addAction(all_a)
        none_a = QAction("None", menu)
        none_a.triggered.connect(
            lambda _=False: _cell_gating_set_gate_choices(ctx, mode, set()))
        menu.addAction(none_a)
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
