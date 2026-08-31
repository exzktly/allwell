"""Home — **Groups** tab: the GROUPS header, Quick Replicates and the group list.

The editable ``SavedSelectionsList`` over ``ctx.state.selection.selections``,
hoisted out of the sidebar so the list has full vertical room and the plate keeps
its aspect ratio. The plate that edits the selected group's well membership is
the rail's, and lives in ``groups/panel_view.py`` -- §3 keeps a feature's main
tab page here and its sidebar panels in a ``*_view.py``.

Home's own module (``features/home/tab_view.py``) owns the notebook, the control
band and the Import action; this supplies one tab's body. Well Labels is
``home/labels/tab_view.py``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from well_viewer.domain.plate_layout import PLATE_COLS
from well_viewer.core.ui_helpers import btn_primary, btn_secondary, build_hline_separator
from well_viewer.features.home.groups.controller import (
    rep_add, rep_quick_pairs_from_dropdowns,
)


def build_replicate_groups_centre(ctx, parent: QWidget) -> None:
    """Sample-Definitions CENTRE — Groups sub-tab.

    Hoists the legacy "below the plate" GROUPS chrome out of the sidebar.
    Contents (top → bottom):
      - the plate that edits the current group's membership
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

    # The plate this layer edits, in the page rather than the rail. Above the
    # list because that is the reading order of the sentence the two make: this
    # group, these wells.
    from well_viewer.features.home.groups.panel_view import build_replicate_panel
    plate_host = QWidget(parent)
    QVBoxLayout(plate_host).setContentsMargins(0, 0, 0, 0)
    build_replicate_panel(ctx, plate_host, role="editor")
    layout.addWidget(plate_host)

    hdr = QWidget(parent)
    hdr_l = QHBoxLayout(hdr)
    hdr_l.setContentsMargins(0, 8, 0, 4)
    layout.addWidget(hdr)
    hdr_l.addStretch(1)
    # Named for its scope: an unqualified "Clear All" on a page that also holds
    # well labels, ratios and gating reads as if it clears those too.
    hdr_l.addWidget(btn_secondary(hdr, "Clear all groups", lambda: rep_clear_all(ctx)))
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

    layout.addWidget(build_hline_separator(parent))

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
    from well_viewer.features.home.groups.selections_view import wire_selections_list as _wire
    rep_list = ctx.widgets.register("_rep_list", SavedSelectionsList(parent))
    rep_list.setComposable(True)
    layout.addWidget(rep_list, 1)
    _wire(ctx, rep_list)

    # What a figure will actually call these groups, when that is not their
    # name. Empty and hidden until the substitution applies to something —
    # ``selections_model.relabelled_groups_clause`` returns "" otherwise, and a
    # line that is always on screen is one nobody reads on the day it matters.
    relabel = ctx.widgets.register("_rep_relabel_note", QLabel("", parent))
    relabel.setObjectName("Muted")
    relabel.setWordWrap(True)
    relabel.setVisible(False)
    layout.addWidget(relabel)

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
