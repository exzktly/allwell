"""Replicate panel builder (Qt port).

Sample Definitions tab is split between three places:

  - **Left sidebar** (this module, ``build_replicate_panel``): the plate
    map only. Clicking a well edits the *current* selection's well
    membership.
  - **Centre — Groups sub-tab** (``build_replicate_groups_centre``): the
    GROUPS header, Quick Replicates row, and the editable
    SavedSelectionsList over ``app._app_state.selection.selections``. Hoisted out of the
    sidebar so the user has full vertical room for the list and the
    plate keeps its proper aspect ratio.
  - **Centre — Well Labels sub-tab**: see ``label_grid_view``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSpinBox,
    QVBoxLayout, QWidget,
)

from well_viewer.domain.plate_layout import PLATE_COLS
from well_viewer.core.ui_helpers import btn_primary, btn_secondary
from well_viewer.features.grouping.label_grid_view import build_label_grid
from well_viewer.core.selection_controller import import_selections_from_json
from well_viewer.core.selection_controller import update_active_group_label
from well_viewer.core.selection_controller import groups_centre_refresh
from well_viewer.features.grouping.controller import (
    rep_add, rep_refresh_map, rep_quick_pairs_from_dropdowns,
)
from well_viewer.core.safe import swallowed


class _NotesEdit(QPlainTextEdit):
    """Notes box that commits on focus-out instead of on every keystroke.

    ``QPlainTextEdit`` has no ``editingFinished`` signal, so we persist when the
    widget loses focus (clicking elsewhere, switching tab, closing the window) —
    one save per editing session rather than one per letter.
    """

    def __init__(self, parent, on_commit) -> None:
        super().__init__(parent)
        self._on_commit = on_commit

    def focusOutEvent(self, event) -> None:  # Qt override
        super().focusOutEvent(event)
        try:
            self._on_commit()
        except Exception:  # never let an autosave failure escape the event loop
            swallowed()  # was: pass (silent except)


from widgets.flow_layout import FlowLayout as _FlowLayout

def build_replicate_panel(ctx, parent: QWidget) -> None:
    """Sample-Definitions LEFT sidebar: the plate map only.

    The GROUPS panel (header + Quick Replicates + SavedSelectionsList +
    hint line) lives in the centre area now; see
    ``build_replicate_groups_centre``.
    """

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    from widgets.well_plate_selector import WellPlateSelector
    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)
    plate.setSelectionMode("select")
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])
    from well_viewer.core.ui_helpers import (
        apply_sidebar_plate_geometry, well_plate_tooltip_provider,
    )
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # Shared sidebar-plate sizing (see ui_helpers.apply_sidebar_plate_geometry):
    # scales to the rail width, keeps aspect, no fixed min-height floor. The
    # trailing addStretch below pins the plate to the top of the column.
    apply_sidebar_plate_geometry(plate)
    layout.addWidget(plate, 0)
    layout.addStretch(1)
    ctx.widgets.register("_rep_map_plate", plate)

    def _commit_plate_to_current(*_a) -> None:
        from well_viewer.core.selection_controller import sel_by_id, sel_set_composition
        from well_viewer.domain.viewer_state import parse_rc
        # Well-membership edits should only land while the user is on the
        # GROUPS sub-tab of Sample Definitions. When the user is on Cell
        # Gating / Well Labels / Ratios / Notes, the Groups plate's
        # selectionDragFinished can still fire (e.g. from programmatic
        # refresh ticks) and silently rewrite the current group's
        # composition — exactly the "wells get reassigned even though I'm
        # not on the Groups tab" bug. Guard against it here.
        sub_tabs = getattr(ctx.window, "_sample_definitions_subtabs", None)
        if sub_tabs is not None:
            try:
                if sub_tabs.tabText(sub_tabs.currentIndex()) != "Groups":
                    return
            except Exception:
                swallowed()  # was: pass (silent except)
        sid = ctx.state.selection.current_selection_id
        sel = sel_by_id(ctx, sid) if sid else None
        if sel is None:
            rep_refresh_map(ctx)        # no current selection — revert the click
            return
        well_paths = ctx.state.dataset.well_paths
        new = sorted((w for w in plate.selectedWellIds() if w in well_paths),
                     key=parse_rc)
        old = sorted((w for w in (sel.get("wells") or []) if w in well_paths),
                     key=parse_rc)
        if new == old:
            return
        # sel_set_composition enforces well-exclusivity (the current group wins)
        # and runs ctx.coordinator.rebuild_all(), which re-pushes the plate via rep_refresh_map.
        sel_set_composition(ctx, sid, new)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_current)


def build_replicate_groups_centre(ctx, parent: QWidget) -> None:
    """Sample-Definitions CENTRE — Groups sub-tab.

    Hoists the legacy "below the plate" GROUPS chrome out of the sidebar.
    Contents (top → bottom):
      - GROUPS header with Clear-all / + Add buttons
      - Quick Replicates row (Pair + Order dropdowns + Apply button)
      - SavedSelectionsList (composable) over ``ctx.state.selection.selections``
      - 1-line usage hint
    """
    from well_viewer.core.selection_controller import rep_clear_all
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    hdr = QWidget(parent)
    hdr_l = QHBoxLayout(hdr)
    hdr_l.setContentsMargins(0, 8, 0, 4)
    layout.addWidget(hdr)
    hdr_l.addStretch(1)
    hdr_l.addWidget(btn_secondary(hdr, "Clear All", lambda: rep_clear_all(ctx)))
    hdr_l.addWidget(btn_primary(hdr, "+ Add", lambda: rep_add(ctx)))

    # Quick Replicates dropdowns
    hdr2r = QWidget(parent)
    hdr2r_l = QHBoxLayout(hdr2r)
    hdr2r_l.setContentsMargins(0, 4, 0, 4)
    layout.addWidget(hdr2r)

    pair_lbl = QLabel("Group along:", hdr2r)
    hdr2r_l.addWidget(pair_lbl)
    pair_cb = QComboBox(hdr2r)
    pair_cb.addItems(["Rows (A01+A02)", "Columns (A01+B01)"])
    pair_cb.setCurrentText("Rows (A01+A02)")
    hdr2r_l.addWidget(pair_cb)
    ctx.widgets.register("_rep_quick_pair_dir_var", pair_cb)

    size_lbl = QLabel("Replicates per group:", hdr2r)
    hdr2r_l.addWidget(size_lbl)
    size_sb = QSpinBox(hdr2r)
    size_sb.setRange(1, len(PLATE_COLS))
    size_sb.setValue(2)
    size_sb.setToolTip(
        "How many consecutive wells form one replicate group "
        "(2 = pairs, 3 = triplicates, …). A partial trailing run becomes a "
        "smaller group.")
    hdr2r_l.addWidget(size_sb)
    ctx.widgets.register("_rep_quick_size_var", size_sb)

    order_lbl = QLabel("Order:", hdr2r)
    hdr2r_l.addWidget(order_lbl)
    iter_cb = QComboBox(hdr2r)
    iter_cb.addItems(["Across rows", "Down columns"])
    iter_cb.setCurrentText("Across rows")
    hdr2r_l.addWidget(iter_cb)
    ctx.widgets.register("_rep_quick_iter_order_var", iter_cb)
    hdr2r_l.addStretch(1)
    hdr2r_l.addWidget(btn_primary(hdr2r, "Apply Quick Replicates",
                                  lambda: rep_quick_pairs_from_dropdowns(ctx)))

    sep = QFrame(parent)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    layout.addWidget(sep)

    # Active-group banner — until the user clicks a group below, plate
    # clicks have no effect. Make that state legible so the user isn't
    # left wondering why nothing happens.
    import theme as _theme
    active_row = QWidget(parent)
    active_row.setObjectName("ActiveGroupBanner")
    active_l = QHBoxLayout(active_row)
    active_l.setContentsMargins(0, 6, 0, 6)
    active_l.setSpacing(6)
    active_caption = QLabel("ACTIVE GROUP", active_row)
    active_caption.setStyleSheet(
        f"color: {_theme.Colors.text_muted}; "
        f"font-size: {_theme.Typography.caption_size}px; "
        f"letter-spacing: 0.08em; font-weight: 600;"
    )
    active_l.addWidget(active_caption)
    active_lbl = ctx.widgets.register(
        "_rep_active_group_lbl",
        QLabel("None selected — click a group below to start", active_row))
    active_lbl.setObjectName("ActiveGroupValue")
    active_lbl.setStyleSheet(
        f"QLabel#ActiveGroupValue {{ "
        f"color: {_theme.Colors.text_secondary}; "
        f"font-size: {_theme.Typography.small_size}px; "
        f"font-weight: 600; }}"
    )
    active_l.addWidget(active_lbl)
    active_l.addStretch(1)
    layout.addWidget(active_row)

    # The groups card-list — a widgets.SavedSelectionsList (composable)
    # over ctx.state.selection.selections.
    from widgets.saved_selections_list import SavedSelectionsList
    from well_viewer.features.grouping.tab_view import wire_selections_list as _wire
    rep_list = ctx.widgets.register("_rep_list", SavedSelectionsList(parent))
    rep_list.setComposable(True)
    layout.addWidget(rep_list, 1)
    _wire(ctx, rep_list)

    hint = QLabel(
        "Click a group above to make it the ACTIVE group — its row "
        "highlights with an accent stripe. Then click wells on the plate "
        "map (left sidebar) to add or remove them. Expand a group to edit "
        "its replicate structure.",
        parent,
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    hint.setContentsMargins(0, 6, 0, 8)
    layout.addWidget(hint)


def build_sample_definitions_centre(ctx, parent: QWidget) -> None:
    """Centre panel for the Sample Definitions tab.

    Layout:
      - top toolbar: Save / Load / Clear All for everything on the tab
      - sub-tabs: Groups / Well Labels / Ratios / Cell Gating (lazy-built
        on first open) / Notes
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QFrame as _QFrame,
        QTabWidget as _QTabWidget,
        QVBoxLayout as _QVBoxLayout,
        QWidget as _QWidget,
    )
    from well_viewer.features.grouping.cell_gating.controller import cell_gating_sync_scatter_gates
    from well_viewer.features.grouping.cell_gating.tab_view import build_cell_gating_subtab
    from well_viewer.features.scatter.ratio_panel_view import build_ratios_inline_panel
    from well_viewer.core.ui_helpers import notes_schedule_save
    from well_viewer.persistence import sample_definitions as _sd
    from widgets.icon_button import IconButton as _IconButton

    outer_layout = parent.layout()
    if outer_layout is None:
        outer_layout = _QVBoxLayout(parent)
        parent.setLayout(outer_layout)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    # ── Top toolbar: Import / Clear All ─────────────────────────────────
    # Every definition on this tab (well labels, replicate sets, bar groups,
    # ratios, and cell gating) AUTOSAVES to the data folder on edit —
    # selections/labels/notes/plot-groups + ratios via the persistence registry,
    # cell gating via _cell_gating_persist_params. So there is no manual
    # Save/Reload button: Import merges an external JSON, Clear All wipes the
    # definitions.
    toolbar = _QWidget(parent)
    toolbar.setObjectName("TabCtrl")
    tl = _FlowLayout(toolbar)
    tl.setContentsMargins(8, 6, 8, 6)
    tl.setSpacing(6)

    import_ib = _IconButton("plus", toolbar)
    import_ib.setToolTip("Import selections from a JSON file (merges into the current set).")
    import_ib.clicked.connect(lambda _=False: import_selections_from_json(ctx))
    tl.addWidget(import_ib)

    clear_ib = _IconButton("x", toolbar)
    clear_ib.setToolTip(
        "Clear every definition on this tab — well labels, replicate "
        "sets, bar groups, ratio metrics, and cell-gating thresholds."
    )
    clear_ib.clicked.connect(lambda _=False: _sd.clear_all(ctx))
    tl.addWidget(clear_ib)

    tl.addStretch(1)
    outer_layout.addWidget(toolbar)

    sep_top = _QFrame(parent)
    sep_top.setObjectName("Separator")
    sep_top.setFrameShape(_QFrame.HLine)
    sep_top.setFixedHeight(1)
    outer_layout.addWidget(sep_top)

    # ── Sub-tabs ────────────────────────────────────────────────────────
    sub_tabs = _QTabWidget(parent)
    sub_tabs.setObjectName("SampleDefinitionsSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    outer_layout.addWidget(sub_tabs, 1)
    # Widget handle for the plate-commit guard (read via ctx.window in
    # build_replicate_panel) — a Qt-nav stamp, not behaviour coupling.
    ctx.window._sample_definitions_subtabs = sub_tabs

    # Sub-tab 0: Cell Gating — the global per-cell config users reach for most,
    # so it leads. Built explicitly below (it's the default tab, so the lazy
    # currentChanged build wouldn't fire for it).
    cell_gating_tab = _QWidget(sub_tabs)
    _QVBoxLayout(cell_gating_tab).setContentsMargins(0, 0, 0, 0)
    sub_tabs.addTab(cell_gating_tab, "Cell Gating")
    ctx.runtime.cell_gating.subtab_frame = cell_gating_tab
    ctx.runtime.cell_gating.subtab_built = False

    # Sub-tab 1: Groups — hoisted out of the sidebar so the
    # SavedSelectionsList has full vertical room and the sidebar plate
    # can stay full-aspect.
    groups_tab = _QWidget(sub_tabs)
    _QVBoxLayout(groups_tab).setContentsMargins(12, 12, 12, 12)
    build_replicate_groups_centre(ctx, groups_tab)
    sub_tabs.addTab(groups_tab, "Groups")

    # Sub-tab 1: Well Labels only
    labels_tab = _QWidget(sub_tabs)
    _QVBoxLayout(labels_tab).setContentsMargins(12, 12, 12, 12)
    build_label_grid(ctx, labels_tab)
    sub_tabs.addTab(labels_tab, "Well Labels")

    # Sub-tab 2: Ratio Metrics only
    ratio_tab = _QWidget(sub_tabs)
    rl = _QVBoxLayout(ratio_tab)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.addWidget(build_ratios_inline_panel(ctx, ratio_tab))
    rl.addStretch(1)
    sub_tabs.addTab(ratio_tab, "Ratios")

    # Sub-tab 4: Pipeline Info — the run's recorded parameters (read-only),
    # including which background method actually ran.
    from well_viewer.features.grouping.pipeline_info_view import (
        build_pipeline_info_subtab,
    )
    pinfo_tab = _QWidget(sub_tabs)
    _QVBoxLayout(pinfo_tab).setContentsMargins(0, 0, 0, 0)
    build_pipeline_info_subtab(ctx, pinfo_tab)
    sub_tabs.addTab(pinfo_tab, "Pipeline Info")

    # Sub-tab 5: Notes — freeform per-project text, persisted via
    # sample_definitions.json's "notes" field.
    notes_tab = _QWidget(sub_tabs)
    nl = _QVBoxLayout(notes_tab)
    nl.setContentsMargins(12, 12, 12, 12)
    # Commit on focus-out (not per keystroke): a freeform notes box shouldn't
    # rewrite pipeline_info.json on every letter typed.
    # Register the Notes editor so the ctx-native sample-definitions loader
    # (persistence._apply_unified_state) writes migrated notes back through
    # ctx.widgets; register() also stamps app._notes_edit for the legacy readers.
    notes_edit = ctx.widgets.register(
        "_notes_edit", _NotesEdit(notes_tab, lambda: notes_schedule_save(ctx)))
    notes_edit.setPlaceholderText("Project notes…")
    notes_edit.setPlainText(ctx.state.selection.notes_text or "")
    nl.addWidget(notes_edit, 1)
    sub_tabs.addTab(notes_tab, "Notes")

    def _on_sample_def_subtab(idx: int) -> None:
        name = sub_tabs.tabText(idx)
        if name == "Cell Gating":
            build_cell_gating_subtab(ctx)
            cell_gating_sync_scatter_gates(ctx)
        if name == "Pipeline Info":
            cb = ctx.runtime.grouping.pipeline_info_refresh
            if callable(cb):
                cb()      # re-read in case the dataset changed
        # Entering the Groups sub-tab deselects any previously-active
        # group so a click on the sidebar plate has no consequence
        # until the user explicitly picks one. The active-group banner
        # built by ``build_replicate_groups_centre`` updates via
        # ``groups_centre_refresh``.
        if name == "Groups":
            if ctx.state.selection.current_selection_id is not None:
                ctx.state.selection.current_selection_id = None
                groups_centre_refresh(ctx)
                ctx.coordinator.refresh_sidebar_map()
            if ctx.widgets.has("_rep_active_group_lbl"):
                update_active_group_label(ctx)
    sub_tabs.currentChanged.connect(_on_sample_def_subtab)
    # The visible bar lives in the shell's mode band beside the mode selector,
    # not above this tab's body. ``mirror_tab_widget`` hides this widget's own
    # bar and drives it from one up there, leaving the QTabWidget's API — which
    # the plate-commit guard, view-state and several tests read — untouched.
    from well_viewer.core.app_shell_view import mirror_tab_widget
    mirror_tab_widget(ctx.window, "Sample Definitions", sub_tabs)
    # Cell Gating leads and is the default tab, so build it now (currentChanged
    # won't fire for the already-current index 0).
    build_cell_gating_subtab(ctx)
