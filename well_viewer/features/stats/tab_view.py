"""Statistics-tab UI builders (Qt port)."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QLabel, QTextEdit,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import btn_primary, btn_secondary
from widgets.labeled_combo import LabeledComboBox
from widgets.page_body import build_page_body
from well_viewer.features.stats.controller import stats_active_group
from well_viewer.features.stats.controller import stats_apply_drag
from well_viewer.features.stats.controller import stats_on_test_change
from well_viewer.features.stats.controller import TEST_LABELS
from well_viewer.features.stats.controller import refresh_stats_test_inputs
from well_viewer.features.stats.controller import stats_refresh_group_list
from well_viewer.features.stats.controller import stats_refresh_fov_indicator
from well_viewer.features.stats.controller import stats_update_tp_menu
from well_viewer.features.stats.controller import stats_refresh_map
from functools import partial
from well_viewer.features.stats.controller import stats_grp_add
from well_viewer.features.stats.controller import stats_grp_clear_all
from well_viewer.features.stats.controller import stats_resync_from_sample_defs
from well_viewer.core.channel_state_controller import refresh_stats_property_combo
from well_viewer.core.safe import swallowed



def build_stats_tab(ctx, parent: QWidget) -> None:
    ctx.state.stats.groups = []
    ctx.state.stats.active_grp = -1
    build_stats_results_panel(ctx, parent)
    # The comparison-group editor belongs to this page, above its results and
    # below the control band. It is built after the results panel because that
    # is what lays the page body out, and inserted at the top of that body
    # rather than appended — the order on screen is band, groups, results.
    host = ctx.widgets.register("_stats_group_editor", QWidget(parent))
    build_stats_group_editor(ctx, host)
    ctx.widgets.get("_stats_page_body").layout().insertWidget(0, host)


def build_stats_group_editor(ctx, parent: QWidget) -> None:
    """Build the comparison-group editor — the plate that paints wells into a
    group, and the group cards beside it.

    This is part of the Statistics **page**, and deliberately not of the left
    rail. The rail carries exactly one well picker (``core/rail_plate.py``),
    and every other plate mounted there answers the same question: which wells
    am I looking at. The shared picker selects wells to plot, Segmentation's
    picks the one well to preview, the Image Table's picks the wells to tile.
    This plate answers a different question — a click on it writes a well into
    a *named comparison group* — and two verbs competing for one slot is a
    mode.

    A mode on a shared instrument is invisible, and in Split View it was also
    unreachable. The slot showed whichever plate belonged to the focused pane,
    so with Plots beside Statistics the plate on screen selected wells to plot
    while the ``+ Add`` button below it created comparison groups those wells
    never reached: the group came out empty, and nothing on screen said why.
    The only way to reach the plate that fills a group was to click into the
    Statistics pane first — a gesture unrelated to the task, and one the user
    had no reason to think was needed on a panel they were already looking at.

    Keeping the editor with the tab it serves removes the mode rather than
    labelling it. Statistics owns no rail section at all, so while a Statistics
    pane is on screen the rail belongs unambiguously to the other pane, and
    painting a group needs no focus change: the plate is inside the pane the
    user is already working in, beside the cards that name the group it paints
    into.
    """
    from ui import theme
    from well_viewer.core.ui_helpers import make_scrollable_canvas
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    from widgets.responsive_row import ResponsiveRow
    from widgets.collapsible_section import CollapsibleSection
    from widgets.plate_geometry import (
        PLATE_MAX_HEIGHT, PLATE_MIN_WIDTH, apply_plate_geometry,
    )
    from widgets.well_plate_selector import WellPlateSelector

    # A row that becomes a column when the pane cannot hold both panels. A plain
    # QHBoxLayout does not refuse to lay out children it has no room for — it
    # places them in order and runs the tail off its own edge, which on a 350px
    # pane (an 1100x800 window with the rail open and this pane split) left a
    # title reading "COMP GROU" and a button cut off mid-word. ResponsiveRow
    # flips on the panels' own minimums, so it stacks exactly when one line
    # would clip.
    outer = parent.layout()
    if outer is None:
        outer = QVBoxLayout(parent)
        parent.setLayout(outer)
    outer.setContentsMargins(0, 0, 0, theme.Spacing.md)
    outer.setSpacing(0)

    # Collapsible, because defining the groups is a phase of the work rather
    # than a constant: once they are built the strip is a 220px band the user
    # reads past on the way to the results, and folding it hands that height to
    # the results without leaving the page. The header's count is what a folded
    # section still has to say — a collapsed section that reports nothing is a
    # section the user has to open to find out whether they need it open.
    section = CollapsibleSection("Comparison groups", expanded=True)
    count_lbl = QLabel("", section)
    count_lbl.setObjectName("Muted")
    section.setValueWidget(count_lbl)
    ctx.widgets.register("_stats_group_count_lbl", count_lbl)
    outer.addWidget(section)
    ctx.widgets.register("_stats_group_section", section)

    row = ResponsiveRow(section, spacing=theme.Spacing.lg)
    section.addWidget(row)
    ctx.widgets.register("_stats_group_row", row)

    # ── The plate, on the left ──────────────────────────────────────────────
    plate = WellPlateSelector(section)
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # ``editor``, not ``rail``, and the difference is the size caps and nothing
    # else: both roles hide the built-in All / Invert / Clear row
    # (``plate_geometry`` shows it for ``picker`` alone), so an earlier version
    # of this comment claiming that as the reason was simply wrong. The reason
    # is that ``rail`` is *uncapped* — an exemption argued for Review's rail
    # picker specifically, where the plate is alone in a fixed-width column. On
    # a page that shares its width with the group list, uncapped means the map
    # grows to whatever the pane has and the list gets the remainder.
    apply_plate_geometry(plate, role="editor")
    # A floor as well as a cap, which the rail plate never needed: this one
    # shares its width with the group cards, and a box layout that cannot fit
    # both shrinks them together. Measured on an 1100x800 window with the rail
    # open and the pane split, that left the map 167px wide — 13px of well —
    # while the cards beside it were a sliver. The cards are text and scroll, so
    # they are the half that should give.
    # Width only. The grid sizes its cells to ``min(width / 12, height / 8)``,
    # so a height floor would matter if anything here compressed the map
    # vertically — and nothing does: the section hugs its content and the page
    # scrolls (``ResponsiveScroll``). Measured at nine window sizes with and
    # without a ``setMinimumHeight``, the map's geometry was identical in every
    # one, which makes it a line that reads like a safeguard and guards nothing.
    # ``tests/test_stats_group_editor_layout`` asserts the *outcome* — that a
    # cell never falls below what ``PLATE_MIN_WIDTH`` allows — so the day
    # something does compress the map, that fails rather than this quietly
    # holding.
    plate.setMinimumWidth(PLATE_MIN_WIDTH)
    # Top-aligned beside the cards; centred above them when the row stacks, so a
    # map narrower than the pane sits under the middle of what it feeds rather
    # than against one edge.
    row.add_panel(plate, row_alignment=Qt.AlignTop,
                  column_alignment=Qt.AlignHCenter)
    ctx.widgets.register("_stats_map_plate", plate)

    # ── The groups, on the right ────────────────────────────────────────────
    side = QWidget(section)
    col = QVBoxLayout(side)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(theme.Spacing.sm)

    # The instruction leads, because it names the order the two gestures go in
    # and the first of them is the one a user does not guess. It is a label of
    # its own rather than a section header's hint: the section title now lives
    # on the collapsible's header, which toggles on click and so cannot also
    # carry text and buttons.
    # "the map", not "the map beside it": the row stacks on a narrow pane and
    # the map is then above the list, so any word placing it would be wrong half
    # the time. The hint sits inside the editor, where the only map is this one.
    hint = QLabel("Select a group below, then drag wells on the map to "
                  "assign them to it.", side)
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    col.addWidget(hint)

    sa, inner = make_scrollable_canvas(side)
    # Bounded by the plate it sits beside, so however many groups are defined
    # the editor stays a strip at the top of the page and the results below it
    # keep the rest. Derived from the plate cap rather than chosen, so the two
    # columns cannot drift apart when that cap moves.
    sa.setMaximumHeight(PLATE_MAX_HEIGHT)
    col.addWidget(sa, 1)
    ctx.widgets.register("_stats_grp_canvas", sa)
    ctx.widgets.register("_stats_grp_inner", inner)

    # The actions sit at the foot of the list they act on. On the rail they were
    # in the section header, which put "+ Add" at the far end of the panel from
    # the cards it appends to — and in a split pane the title then wrapped to
    # two lines underneath them.
    # A wrapping row, not a QHBoxLayout: a box layout's minimum width is the
    # sum of its buttons, so three of them made this column about 370px at its
    # narrowest and pushed the map out of the row beside it at a pane width
    # where the two still fitted. It comes from ``widgets/`` rather than being a
    # ``FlowLayout`` built here, which is what ``test_control_band_adoption``
    # asks of a module on the band — the last thing this page needs is a second
    # thing that looks like a control row.
    from widgets.button_row import ButtonRow
    actions = ButtonRow(side)
    actions.add(btn_primary(actions, "+ Add group", partial(stats_grp_add, ctx)))
    actions.add(btn_secondary(actions, "Clear All",
                              partial(stats_grp_clear_all, ctx)))
    # Pull the comparison groups straight from the shared Sample-Definition
    # sets (S1).
    actions.add(btn_secondary(actions, "↻ Load from Replicates",
                              partial(stats_resync_from_sample_defs, ctx)))
    col.addWidget(actions)
    ctx.widgets.register("_stats_group_actions", actions)

    row.add_panel(side, stretch=1)
    ctx.widgets.register("_stats_group_side", side)

    ctx.state.stats.drag_adding = True
    ctx.state.stats.drag_visited = set()

    def _commit_plate_to_group(*_a) -> None:
        grp = stats_active_group(ctx)
        if grp is None:
            stats_refresh_map(ctx)        # no active group — revert the click
            return
        new = {w for w in plate.selectedWellIds() if w in ctx.state.dataset.well_paths}
        old = {w for w in grp.wells if w in ctx.state.dataset.well_paths}
        added, removed = new - old, old - new
        if not added and not removed:
            return
        # Reuse stats_apply_drag's "well in a loaded rep-set → add/remove the whole
        # set; else solo-well add/remove" logic, one token at a time.
        for tok in added:
            ctx.state.stats.drag_adding = True
            ctx.state.stats.drag_visited = set()
            stats_apply_drag(ctx, tok)
        for tok in removed:
            ctx.state.stats.drag_adding = False
            ctx.state.stats.drag_visited = set()
            stats_apply_drag(ctx, tok)
        stats_refresh_map(ctx)
        stats_refresh_group_list(ctx)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_group)

    stats_refresh_map(ctx)
    stats_refresh_group_list(ctx)


def build_stats_results_panel(ctx, parent: QWidget) -> None:
    """Build the right-hand stats controls/results panel.

    A8 teardown: takes ``ctx`` — every widget handle is registered through
    ``ctx.widgets`` (which stamps the legacy ``app._stats_*`` names the stats
    controllers read), the change/refresh callbacks route through their
    ctx-native controllers, and the Run/Property actions resolve through the
    ``ctx.runtime.stats`` namespace so the builder names no god-object.
    """
    reg = ctx.widgets

    # ── The control band ────────────────────────────────────────────────────
    # This tab used to state its parameters as a five-row label/field grid with
    # "Run test" on a sixth row and a separator under it — a form, where every
    # other tab in the pane has a control row. The same five selectors fit one
    # band: they are all scope (which cells the test runs on and what it
    # measures), and Run is the action.
    band, body = build_page_body(parent, content_margins=(12, 12, 12))
    body_layout = body.layout()
    body_layout.setSpacing(0)

    # Everything below the band scrolls. Three things on this page have a real
    # minimum height now — the comparison-group map, two rows of group cards,
    # and the results panel — and on a short pane in Split View their sum
    # exceeds what is there. Something has to give, and the honest answer is
    # that the page is longer than the window rather than that the map is nine
    # pixels of row. Horizontal scrolling is off, so width still drives the
    # editor's side-by-side / stacked flip instead of being escaped sideways.
    from widgets.responsive_row import ResponsiveScroll
    scroll = ResponsiveScroll(body)
    scroll.setFrameShape(QFrame.NoFrame)
    page = QWidget(scroll)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    scroll.setWidget(page)
    body_layout.addWidget(scroll)
    reg.register("_stats_page_scroll", scroll)
    # ``build_stats_tab`` inserts the comparison-group editor at the top of this
    # host once the rest of the page exists; it needs the handle to do so.
    reg.register("_stats_page_body", page)
    stats_ctrl = reg.register("_stats_ctrl", band)

    channel_cb = reg.register("_stats_channel_cb", QComboBox())
    channel_cb.addItems(["—"])
    channel_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    stats_ctrl.add_scope("Channel", channel_cb)

    # Per-tab Property combo — picks which CSV column drives the test.
    # Mirrors the Plotting band's Property combo's state, kept local so it
    # appears next to the channel/statistic selectors.
    from well_viewer.data.metric_labels import METRIC_ORDER as _ST_METRIC_ORDER
    property_cb = reg.register("_stats_property_cb", QComboBox())
    property_cb.addItems(_ST_METRIC_ORDER)
    property_cb.currentIndexChanged.connect(
        lambda _i: ctx.runtime.stats.property_change()
    )
    stats_ctrl.add_scope("Property", property_cb)

    statistic_cb = reg.register("_stats_statistic_cb", QComboBox())
    statistic_cb.addItems([
        "Mean (above threshold)",
        "Mean (all cells)",
        "Median (above threshold)",
        "Fraction above threshold",
    ])
    statistic_cb.setCurrentText("Mean (above threshold)")
    statistic_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    stats_ctrl.add_scope("Statistic", statistic_cb)

    tp_cb = reg.register("_stats_tp_cb", LabeledComboBox("Timepoint"))
    tp_cb.addItems(["—"])
    # The timepoint is a test parameter like any other, and it was the one
    # control that changed what would be tested without invalidating what had
    # been: the previous run's results stayed on screen, and its significance
    # brackets stayed on the Bar plot, describing a different timepoint.
    tp_cb.currentIndexChanged.connect(lambda _i: stats_on_test_change(ctx))
    stats_ctrl.add_scope("", tp_cb)

    test_cb = reg.register("_stats_test_cb", QComboBox())
    # From the controller's roster and nowhere else. A second hand-written list
    # here is how the combo comes to offer a test the run branch has never
    # heard of, which reaches the user as a Run that quietly does the default.
    test_cb.addItems(list(TEST_LABELS))
    test_cb.setCurrentText(TEST_LABELS[0])
    test_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    stats_ctrl.add_scope("Test", test_cb)

    # The three pickers the newer tests need, each hidden until a test asks for
    # it. Hidden rather than disabled, deliberately, and the opposite call from
    # the unit field on the Variables tab: greying a control out states a rule
    # the user can act on, and there is no rule here — a Welch t-test has no
    # control group, and a picker for one is not a thing they could satisfy.
    control_cb = reg.register("_stats_control_cb", LabeledComboBox("Control"))
    control_cb.currentIndexChanged.connect(lambda _i: stats_on_test_change(ctx))
    stats_ctrl.add_scope("", control_cb)

    var_a_cb = reg.register("_stats_var_a_cb", LabeledComboBox("Pair by"))
    var_a_cb.currentIndexChanged.connect(lambda _i: stats_on_test_change(ctx))
    stats_ctrl.add_scope("", var_a_cb)

    var_b_cb = reg.register("_stats_var_b_cb", LabeledComboBox("and"))
    var_b_cb.currentIndexChanged.connect(lambda _i: stats_on_test_change(ctx))
    stats_ctrl.add_scope("", var_b_cb)

    refresh_stats_test_inputs(ctx)

    # Acknowledge the replicate mode the test will use (driven by the shared
    # per-FOV toggle) so the tab is explicit about the unit of replication.
    # No word-wrap: inside a flow a wrapped label reports its longest-word width
    # as its minimum, so the row allocates it narrow and the single-line
    # "Replicates: …" caption wraps into a tall stack even with the whole row
    # free. Left un-wrapped it keeps one line and the flow places it whole.
    fov_indicator = reg.register("_stats_fov_indicator", QLabel(""))
    fov_indicator.setObjectName("MutedCaption")
    stats_ctrl.add_scope("", fov_indicator)

    stats_ctrl.add_action(btn_primary(stats_ctrl, "Run test", ctx.runtime.stats.run))
    stats_refresh_fov_indicator(ctx)

    fig_frame = reg.register("_stats_fig_frame", QWidget(page))
    ff_l = QVBoxLayout(fig_frame)
    ff_l.setContentsMargins(0, 0, 0, 0)

    from matplotlib.figure import Figure as _StatsFigure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _StatsFCA

    fig = reg.register("_stats_fig", _StatsFigure(figsize=(5, 2.8), dpi=96))
    reg.register("_stats_ax", fig.add_subplot(111))
    canvas_widget = reg.register("_stats_canvas_widget", _StatsFCA(fig))
    ff_l.addWidget(canvas_widget)
    layout.addWidget(fig_frame)
    # Hide the figure frame until a test that actually plots (KS) is run.
    # run_stats toggles visibility per-test; before the first run the user
    # sees the controls + result text only, no empty canvas.
    fig_frame.setVisible(False)

    res_frame = reg.register("_stats_res_frame", QWidget(page))
    rl = QVBoxLayout(res_frame)
    rl.setContentsMargins(0, 6, 0, 6)
    result_text = reg.register("_stats_result_text", QTextEdit(res_frame))
    result_text.setReadOnly(True)
    result_text.setLineWrapMode(QTextEdit.WidgetWidth)
    # Hint in the otherwise-blank results panel before the first run.
    # Set here for the first paint and rewritten by ``stats_refresh_group_list``
    # as groups come and go: with two groups already defined, a placeholder
    # telling the user to define two is the page disagreeing with what is on it
    # twenty pixels above.
    result_text.setPlaceholderText(
        "Define two comparison groups above, choose a test, then click "
        "Run test to see results here."
    )
    rl.addWidget(result_text)
    # Not selection-gated: this tab compares groups, and a group is built from
    # wells rather than being the plate selection. But its empty state was a
    # sentence in a QTextEdit placeholder, top-left of a large empty box, while
    # its neighbours centred theirs — and a user reads that difference as
    # inconsistency, not as a different gating rule. Same widget, own reason.
    from well_viewer.core.ui_helpers import EmptyReason, wrap_with_empty_state

    def _no_groups(c) -> EmptyReason | None:
        # Two, not one: this tab compares groups, so a single group cannot run a
        # test either. Gating on "any group at all" let the old top-left QTextEdit
        # placeholder — the inconsistency this replaced — come straight back the
        # moment the user added the first one.
        n = len(c.state.stats.groups or [])
        if n >= 2:
            return None
        if n == 1:
            return EmptyReason(
                "One comparison group so far.",
                "Add a second group to run a test — + Add group under "
                "Comparison groups above.",
            )
        return EmptyReason(
            "No comparison groups yet.",
            "Click + Add group under Comparison groups above, select the card "
            "it makes, then drag wells on the map.",
        )

    layout.addWidget(wrap_with_empty_state(
        ctx.window, res_frame, icon="sigma", reason=_no_groups,
    ), 1)

    stats_update_tp_menu(ctx)

    # Reshape the per-tab Property combo to match the currently-selected
    # stats channel (ratio channels collapse to ``Calculated Val``); then
    # sync the selection to the canonical ``_active_metric``.
    try:
        refresh_stats_property_combo(ctx)
        from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
        label = METRIC_KEY_TO_LABEL.get(
            ctx.state.channel.active_metric, "Mean Intensity"
        )
        idx = property_cb.findText(label)
        if idx >= 0:
            blocked = property_cb.blockSignals(True)
            try:
                property_cb.setCurrentIndex(idx)
            finally:
                property_cb.blockSignals(blocked)
    except Exception:
        swallowed()  # was: pass (silent except)
