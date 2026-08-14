"""Cell Gating tab builder (Qt).

Constructs the tab inside *parent* and registers its widgets on
``ctx.runtime.cell_gating``, wiring every control to a handler in
:mod:`controller`. The behaviour those handlers implement — the gating workers,
the optimizers, the scatter-gate plumbing — lives there; the CDF panel is drawn
by :mod:`renderer`, and the optimizer's own window by :mod:`optimizer_view`.

The tab answers two questions and is laid out as those two questions, in the
order the pipeline applies them:

1. **Cell inclusion** — which cells count. The QC debris gate (always applied),
   then either per-channel intensity floors or the scatter gates.
2. **Fraction-on** — which of the included cells count as "on". Either the
   per-channel cutoffs or a scatter gate.

Each question offers its two sources as a radio pair, because the code has
always treated them as exclusive: ticking the scatter option *replaces* the
per-channel numbers rather than composing with them, and each question's
per-channel table sits inside its own section rather than in a shared block
below both — the numbers have the same shape but answer different questions, and
a table serving both needs a heading that names neither. The CDFs the numbers are
placed against follow immediately, so an edit and its consequence are on one
screen.

* :func:`build_cell_gating_tab` — construct the UI inside *parent*.
* :func:`build_cell_gating_subtab` — build it and run the initial data load.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallowed
from widgets import menu_exec
from well_viewer.core.ui_helpers import (
    build_hline_separator,
    build_section_header,
    install_canvas_wheel_scroll,
)
from well_viewer.features.qc.tab_view import build_qc_gate_controls
from well_viewer.features.home.cell_gating.controller import (
    _GATE_BUTTON_ATTR,
    _GATE_CHOICES_ATTR,
    _GATE_HINT_ATTR,
    _GATE_RADIO_ATTR,
    _cell_gating_apply_scatter_gate_mode,
    _cell_gating_gate_names,
    _cell_gating_refresh_gate_filter,
    _cell_gating_refresh_gate_summary,
    _cell_gating_refresh_optimizer_controls,
    _cell_gating_set_gate_choices,
    cell_gating_auto_threshold,
    cell_gating_load_cell_areas,
    cell_gating_load_threshold_frac_on,
    cell_gating_on_use_scatter_gates_change,
    cell_gating_on_use_scatter_thresh_change,
    cell_gating_refresh_included_count,
    cell_gating_sync_scatter_gates,
    logger,
)
from well_viewer.features.home.cell_gating.optimizer_view import (
    build_optimizer_window, show_optimizer_window,
)
from well_viewer.features.home.cell_gating.renderer import _cell_gating_plot_cdf

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_GATING_PLOT_MIN_H = 420
_GATE_BTN_MIN_W = 150
_RADIO_INDENT = 20

# Widget-registry key for each layer's gate picker. The controller reads the
# buttons through ``_GATE_BUTTON_ATTR`` (the runtime attr the registry stamps);
# these are the register() names that produce them.
_GATE_BUTTON_HANDLE = {
    "gating": "_cell_gating_gating_gate_btn",
    "thresh": "_cell_gating_thresh_gate_btn",
}


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
    # ThreshCountOn edits exist only for the smFISH channels — a channel with no
    # ``_smfish_count`` column has no count metric to cut, so an always-present
    # field would be dead furniture on most rows.
    reg.register("_cell_gating_thresh_count_edits", {})
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
    # here — the radios below read the slice.

    # The tab is taller than a short window, so the whole thing scrolls as one
    # and the CDF keeps a real minimum height. There is deliberately no second,
    # inner scroll area: the per-channel table used to sit in a fixed 120px one,
    # which meant the tab's primary input surface scrolled inside the page that
    # was already scrolling, and stayed 120px tall on a window with room to
    # spare.
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
    root = QVBoxLayout(content)
    root.setContentsMargins(12, 12, 12, 12)

    control_frame = QWidget(parent)
    control_frame.setObjectName("TabCtrl")
    cf_layout = QVBoxLayout(control_frame)
    cf_layout.setContentsMargins(6, 6, 6, 6)

    _build_inclusion_section(ctx, control_frame, cf_layout)
    _build_fraction_on_section(ctx, control_frame, cf_layout)
    _build_readout_section(ctx, control_frame, cf_layout)
    root.addWidget(control_frame)

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

    _build_cdf_section(ctx, parent, root, FigureCanvas, Figure)

    # The optimizer is a tool, not tab state, so it lives in its own non-modal
    # window — built now (hidden) so its handles register with everything else.
    ctx.runtime.cell_gating.optimizer_window = build_optimizer_window(ctx, parent)
    _cell_gating_refresh_optimizer_controls(ctx)

    # Paint the themed empty state now so the canvas is never a bare white
    # block before a dataset loads (it only gets painted on data otherwise).
    _cell_gating_plot_cdf(ctx)


def _source_radio(parent: QWidget, layout: QVBoxLayout, label: str,
                  tooltip: str) -> QRadioButton:
    """The "per-channel numbers" half of a layer's radio pair, indented to sit
    under its section's lead-in line."""
    row = QWidget(parent)
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(_RADIO_INDENT, 0, 0, 0)
    radio = QRadioButton(label, row)
    radio.setToolTip(tooltip)
    row_layout.addWidget(radio)
    row_layout.addStretch(1)
    layout.addWidget(row)
    return radio


def _gate_source_row(ctx, parent: QWidget, mode: str, *, label: str,
                     tooltip: str, btn_tooltip: str) -> QRadioButton:
    """The "scatter gates" half of a layer's radio pair, plus its gate picker.

    The picker sits inside its own layer's row. Both pickers used to read "All
    gates" and stand 37 lines apart, with only the checkbox text above to say
    which layer each belonged to.

    The radio and its "no gates drawn" hint are stamped on the runtime so
    ``_cell_gating_refresh_gate_filter`` can re-enable them the moment a gate is
    drawn on the Scatter Plot tab — deciding it once here would leave the option
    dead for the rest of the session.
    """
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(_RADIO_INDENT, 0, 0, 0)
    radio = QRadioButton(label, row)
    radio.setToolTip(tooltip)
    layout.addWidget(radio)
    layout.addSpacing(8)
    btn = QPushButton("All gates", row)
    btn.setMinimumWidth(_GATE_BTN_MIN_W)
    btn.setToolTip(btn_tooltip)
    btn.clicked.connect(lambda _=False: _open_gate_filter_menu(ctx, mode))
    layout.addWidget(btn)
    layout.addStretch(1)
    parent.layout().addWidget(row)

    # A dead control says why it is dead and where to go. Both pickers used to
    # sit fully enabled over an empty menu whose only entry read "No gates drawn
    # yet".
    hint = QLabel("No gates drawn yet — draw one on the Scatter Plot tab.", parent)
    hint.setObjectName("Muted")
    hint.setContentsMargins(_RADIO_INDENT * 2, 0, 0, 0)
    parent.layout().addWidget(hint)

    ctx.widgets.register(_GATE_BUTTON_HANDLE[mode], btn)
    setattr(ctx.runtime.cell_gating, _GATE_RADIO_ATTR[mode], radio)
    setattr(ctx.runtime.cell_gating, _GATE_HINT_ATTR[mode], hint)
    return radio


def _build_inclusion_section(ctx, frame: QWidget, layout: QVBoxLayout) -> None:
    """Section 1 — which cells count at all."""
    layout.addWidget(build_section_header(
        frame, "Cell inclusion — which cells count",
        hint="Layers that remove cells from every plot, statistic and export.",
    ))

    # The QC debris gate leads: it is applied to every cell no matter what the
    # rest of this section says, so it cannot be one of the alternatives below.
    layout.addWidget(QLabel("Always applied:", frame))
    try:
        layout.addWidget(build_qc_gate_controls(ctx, frame))
    except Exception:
        swallowed()  # QC controls are best-effort; never block the tab build

    layout.addWidget(QLabel("Then include cells by:", frame))
    per_channel = _source_radio(
        frame, layout, "Per-channel intensity minimum (table below)",
        "Exclude cells whose mean fluorescence intensity falls below the "
        "channel's Include ≥ floor.")

    scatter = _gate_source_row(
        ctx, frame, "gating",
        label="Scatter-plot gates",
        tooltip="Define the Included population from the named rectangular "
                "gates drawn on the Scatter Plot tab instead of the per-channel "
                "intensity thresholds. Gates are saved with the dataset.",
        btn_tooltip="Which scatter gates define the Included population (union "
                    "of the checked gates). Independent of the fraction-on "
                    "selection below.",
    )
    _wire_source_radios(
        ctx, frame, per_channel, scatter,
        active=bool(ctx.state.cell_gating.use_scatter_gates),
        handle="_cell_gating_use_scatter_gates_cb",
        on_change=cell_gating_on_use_scatter_gates_change,
    )
    _mount_threshold_table(ctx, frame, layout, "_cell_gating_inner")


def _build_fraction_on_section(ctx, frame: QWidget, layout: QVBoxLayout) -> None:
    """Section 2 — which of the included cells count as ON."""
    layout.addWidget(build_hline_separator(frame))
    layout.addWidget(build_section_header(
        frame, "Fraction-on — which included cells count as ON",
        hint="Removes no cells; decides which of the included ones are 'on' for "
             "fraction-on statistics.",
    ))

    per_channel = _source_radio(
        frame, layout, "Per-channel ON cutoff (table below)",
        "Count an included cell as 'on' when its mean fluorescence intensity "
        "reaches the channel's ON ≥ cutoff.")

    scatter = _gate_source_row(
        ctx, frame, "thresh",
        label="Scatter-plot gate",
        tooltip="Count the cells inside the selected gate(s) as 'on' instead of "
                "the cells above the per-channel cutoff. Independent of cell "
                "inclusion — no cells are removed.",
        btn_tooltip="Which scatter gates define the fraction-on 'on' population "
                    "(union of the checked gates). Independent of the inclusion "
                    "selection above.",
    )
    _wire_source_radios(
        ctx, frame, per_channel, scatter,
        active=bool(ctx.state.cell_gating.use_scatter_thresh),
        handle="_cell_gating_use_scatter_thresh_cb",
        on_change=cell_gating_on_use_scatter_thresh_change,
    )
    _mount_threshold_table(ctx, frame, layout, "_cell_gating_thresh_inner")

    auto_row = QWidget(frame)
    auto_row_layout = QHBoxLayout(auto_row)
    auto_row_layout.setContentsMargins(_RADIO_INDENT, 4, 0, 0)
    auto_btn = QPushButton("Auto-threshold all channels", auto_row)
    auto_btn.setProperty("variant", "secondary")
    auto_btn.setToolTip(
        "Compute a default ON cutoff per channel via Otsu on "
        "a per-cell + matched-background-pixel distribution sampled from "
        "the first, middle, and last timepoint of every FOV. The "
        "per-cell inclusion floors are NOT modified.\n\n"
        "Progress + per-channel results are written to the log drawer."
    )
    auto_btn.clicked.connect(lambda _=False: cell_gating_auto_threshold(ctx))
    auto_row_layout.addWidget(auto_btn)
    opt_btn = QPushButton("Optimize ON cutoff…", auto_row)
    opt_btn.setProperty("variant", "secondary")
    opt_btn.setToolTip(
        "Open the optimizer: scan a range of cutoffs for one channel and apply "
        "the one that best separates two wells' fraction-on.")
    opt_btn.clicked.connect(lambda _=False: show_optimizer_window(ctx))
    auto_row_layout.addWidget(opt_btn)
    auto_row_layout.addStretch(1)
    ctx.widgets.register("_cell_gating_auto_btn", auto_btn)
    ctx.widgets.register("_cell_gating_open_optimizer_btn", opt_btn)
    layout.addWidget(auto_row)


def _wire_source_radios(ctx, frame: QWidget, per_channel: QRadioButton,
                        scatter: QRadioButton, *, active: bool, handle: str,
                        on_change) -> None:
    """Make one layer's two sources exclusive and wire the change handler.

    Only the scatter radio is registered and only it drives the handler: the
    pair is exclusive, so one signal carries the whole state, and the controller
    keeps reading a single ``isChecked()`` exactly as it did from the checkbox
    this replaced.
    """
    group = QButtonGroup(frame)
    group.setExclusive(True)
    group.addButton(per_channel)
    group.addButton(scatter)
    scatter.setChecked(active)
    per_channel.setChecked(not active)
    ctx.widgets.register(handle, scatter)
    scatter.toggled.connect(lambda _on: on_change(ctx))
    # Keep the group alive for as long as its buttons: a QButtonGroup parented
    # to the frame is fine, but the reference here documents that exclusivity is
    # what makes the single registered handle sufficient.
    ctx.runtime.cell_gating.button_groups = (
        list(ctx.runtime.cell_gating.button_groups or []) + [group])


def _mount_threshold_table(ctx, frame: QWidget, layout: QVBoxLayout,
                          handle: str) -> None:
    """Host for one decision's per-channel table, indented under its radio pair.

    Each table sits inside the section that owns the decision it feeds rather
    than in a shared "Per-channel thresholds" block below both. A shared block
    needs a heading that names neither decision, which is the shape of the
    original defect (both columns under the ThreshFracOn header) with a more
    neutral label on it — and since the two columns grey out independently, that
    block is half-dead whenever either layer is driven by a scatter gate.

    The rows themselves come from ``controller._cell_gating_build_channel_controls``
    on data load; this only frames the container.
    """
    host = ctx.widgets.register(handle, QWidget(frame))
    host_layout = QVBoxLayout(host)
    host_layout.setContentsMargins(_RADIO_INDENT, 4, 0, 4)
    layout.addWidget(host)


def _build_readout_section(ctx, frame: QWidget, layout: QVBoxLayout) -> None:
    """The gate summary, the per-layer funnel, and the Included total."""
    layout.addWidget(build_hline_separator(frame))

    summary = ctx.widgets.register("_cell_gating_gate_summary", QLabel("", frame))
    summary.setObjectName("Muted")
    summary.setWordWrap(True)
    layout.addWidget(summary)

    # Which layer removed what. One aggregate figure says a gate did something;
    # it cannot say which gate, and a layer that was never applied is reported
    # as "not applied" rather than as a zero it did not measure.
    funnel = ctx.widgets.register("_cell_gating_funnel_label", QLabel("", frame))
    funnel.setObjectName("Muted")
    funnel.setToolTip(
        "Each inclusion layer applied on top of the ones above it, in the order "
        "the app composes them. Manual curation and the intensity gate share "
        "one line because they write the same column and cannot be separated "
        "from the data.")
    layout.addWidget(funnel)

    count = ctx.widgets.register("_cell_gating_included_count", QLabel("", frame))
    count.setWordWrap(True)
    count.setToolTip(
        "Cells passing every inclusion layer — manual curation, the QC debris "
        "gate, the crop region, and the cell-gating / scatter-gate layer — "
        "across all loaded wells (not just the selected ones the curves below "
        "show). The fraction-on cutoffs (ThreshFracOn / ThreshCountOn) do not "
        "change this number: they define which included cells count as 'on', "
        "not which cells count."
    )
    layout.addWidget(count)


def _build_cdf_section(ctx, parent: QWidget, root: QVBoxLayout,
                       FigureCanvas, Figure) -> None:
    """The CDFs, directly under the controls they are read against."""
    reg = ctx.widgets
    reg.register("_cell_gating_figure", Figure(figsize=(8, 5), dpi=100))
    canvas = reg.register("_cell_gating_canvas",
                          FigureCanvas(ctx.widgets.get("_cell_gating_figure")))

    plot_scroll = reg.register("_cell_gating_plot_scroll", QScrollArea(parent))
    plot_scroll.setWidgetResizable(True)
    plot_scroll.setFrameShape(QFrame.NoFrame)
    plot_scroll.setWidget(canvas)
    # Floor the plot height so the gating controls above can never squeeze the
    # CDFs into an unreadable sliver (the outer scroll absorbs any overflow).
    plot_scroll.setMinimumHeight(_GATING_PLOT_MIN_H)
    install_canvas_wheel_scroll(canvas, plot_scroll)

    from widgets.mpl_toolbar import MplToolbar
    toolbar = reg.register("_cell_gating_toolbar", MplToolbar(canvas, parent))

    status = reg.register("_cell_gating_status_label", QLabel("No data loaded", parent))
    status.setObjectName("Muted")

    root.addWidget(build_hline_separator(parent))
    root.addWidget(build_section_header(
        parent, "Cumulative distributions (CDF)",
        hint="Where the thresholds above fall in the data. The optimizer used "
             "to sit between these and the controls they explain.",
    ))
    root.addWidget(status)
    root.addWidget(plot_scroll)
    root.addWidget(toolbar)


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
    menu_exec.exec_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))
