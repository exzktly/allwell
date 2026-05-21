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
    QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from well_viewer.ui_helpers import btn_primary, btn_secondary


def build_replicate_panel(app, parent: QWidget) -> None:
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
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)
    plate.setSelectionMode("select")
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])
    # The plate is the only widget in this sidebar now — give it room to
    # expand vertically with the column. heightForWidth keeps the aspect.
    plate.setMinimumHeight(280)
    # Preferred (not MinimumExpanding) so the plate hugs its hint size and a
    # trailing addStretch absorbs leftover vertical space. Matches the main
    # sidebar plate's policy — without this the plate drifts down with the
    # sidebar's available height instead of staying pinned to the top.
    sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    sp.setHeightForWidth(True)
    plate.setSizePolicy(sp)
    layout.addWidget(plate, 0)
    layout.addStretch(1)
    app._rep_map_plate = plate

    def _commit_plate_to_current(*_a) -> None:
        # Well-membership edits should only land while the user is on the
        # GROUPS sub-tab of Sample Definitions. When the user is on Cell
        # Gating / Well Labels / Ratios / Notes, the Groups plate's
        # selectionDragFinished can still fire (e.g. from programmatic
        # refresh ticks) and silently rewrite the current group's
        # composition — exactly the "wells get reassigned even though I'm
        # not on the Groups tab" bug. Guard against it here.
        sub_tabs = getattr(app, "_sample_definitions_subtabs", None)
        if sub_tabs is not None:
            try:
                if sub_tabs.tabText(sub_tabs.currentIndex()) != "Groups":
                    return
            except Exception:
                pass
        sid = app._app_state.selection.current_selection_id
        sel = app._sel_by_id(sid) if sid else None
        if sel is None:
            app._rep_refresh_map()      # no current selection — revert the click
            return
        new = sorted((w for w in plate.selectedWellIds() if w in app._well_paths),
                     key=app._parse_rc)
        old = sorted((w for w in (sel.get("wells") or []) if w in app._well_paths),
                     key=app._parse_rc)
        if new == old:
            return
        # _sel_set_composition enforces well-exclusivity (the current group wins)
        # and runs _rebuild_all(), which re-pushes the plate via _rep_refresh_map.
        app._sel_set_composition(sid, new)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_current)


def build_replicate_groups_centre(app, parent: QWidget) -> None:
    """Sample-Definitions CENTRE — Groups sub-tab.

    Hoists the legacy "below the plate" GROUPS chrome out of the sidebar.
    Contents (top → bottom):
      - GROUPS header with Clear-all / + Add buttons
      - Quick Replicates row (Pair + Order dropdowns + Apply button)
      - SavedSelectionsList (composable) over ``app._app_state.selection.selections``
      - 1-line usage hint
    """
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    hdr = QWidget(parent)
    hdr_l = QHBoxLayout(hdr)
    hdr_l.setContentsMargins(10, 8, 10, 4)
    layout.addWidget(hdr)
    hdr_lbl = QLabel("GROUPS", hdr)
    hdr_lbl.setProperty("role", "section")
    hdr_l.addWidget(hdr_lbl)
    hdr_l.addStretch(1)
    hdr_l.addWidget(btn_secondary(hdr, "Clear All", app._rep_clear_all))
    hdr_l.addWidget(btn_primary(hdr, "+ Add", app._rep_add))

    # Quick Replicates dropdowns
    hdr2r = QWidget(parent)
    hdr2r_l = QHBoxLayout(hdr2r)
    hdr2r_l.setContentsMargins(10, 4, 10, 4)
    layout.addWidget(hdr2r)

    pair_lbl = QLabel("Pair:", hdr2r)
    hdr2r_l.addWidget(pair_lbl)
    app._rep_quick_pair_dir_cb = QComboBox(hdr2r)
    app._rep_quick_pair_dir_cb.addItems(["Rows (A01+A02)", "Columns (A01+B01)"])
    app._rep_quick_pair_dir_cb.setCurrentText("Rows (A01+A02)")
    hdr2r_l.addWidget(app._rep_quick_pair_dir_cb)
    app._rep_quick_pair_dir_var = app._rep_quick_pair_dir_cb

    order_lbl = QLabel("Order:", hdr2r)
    hdr2r_l.addWidget(order_lbl)
    app._rep_quick_iter_order_cb = QComboBox(hdr2r)
    app._rep_quick_iter_order_cb.addItems(["Across rows", "Down columns"])
    app._rep_quick_iter_order_cb.setCurrentText("Across rows")
    hdr2r_l.addWidget(app._rep_quick_iter_order_cb)
    app._rep_quick_iter_order_var = app._rep_quick_iter_order_cb
    hdr2r_l.addStretch(1)
    hdr2r_l.addWidget(btn_primary(hdr2r, "Apply Quick Replicates",
                                  app._rep_quick_pairs_from_dropdowns))

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
    active_l.setContentsMargins(10, 6, 10, 6)
    active_l.setSpacing(6)
    active_caption = QLabel("ACTIVE GROUP", active_row)
    active_caption.setStyleSheet(
        f"color: {_theme.Colors.text_muted}; "
        f"font-size: {_theme.Typography.caption_size}px; "
        f"letter-spacing: 0.08em; font-weight: 600;"
    )
    active_l.addWidget(active_caption)
    app._rep_active_group_lbl = QLabel("None selected — click a group below to start", active_row)
    app._rep_active_group_lbl.setObjectName("ActiveGroupValue")
    app._rep_active_group_lbl.setStyleSheet(
        f"QLabel#ActiveGroupValue {{ "
        f"color: {_theme.Colors.text_secondary}; "
        f"font-size: {_theme.Typography.small_size}px; "
        f"font-weight: 600; }}"
    )
    active_l.addWidget(app._rep_active_group_lbl)
    active_l.addStretch(1)
    layout.addWidget(active_row)

    # The groups card-list — a widgets.SavedSelectionsList (composable)
    # over app._app_state.selection.selections.
    from widgets.saved_selections_list import SavedSelectionsList
    from well_viewer.views.grouping_view import wire_selections_list as _wire
    app._rep_list = SavedSelectionsList(parent)
    app._rep_list.setComposable(True)
    layout.addWidget(app._rep_list, 1)
    _wire(app, app._rep_list)

    hint = QLabel(
        "Click a group below to make it the ACTIVE group — its row "
        "highlights with an accent stripe. Then click wells on the plate "
        "map (left sidebar) to add or remove them. Expand a group to edit "
        "its replicate structure.",
        parent,
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    hint.setContentsMargins(10, 6, 10, 8)
    layout.addWidget(hint)
