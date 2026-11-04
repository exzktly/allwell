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
    QBoxLayout, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui import theme

from review.domain.plate_layout import PLATE_COLS
from review.core.ui_helpers import btn_primary, btn_secondary
from widgets.separator import build_hline_separator
from review.features.home.groups.controller import (
    refresh_replicates_button, rep_add, rep_replicates_from_variables,
    rep_quick_pairs_from_dropdowns,
)
from review.features.home.groups.panel_view import build_replicate_panel
from widgets.spin_box import SpinBox


#: Registry keys of the parts of this layer its phone form rearranges.
PAGE_KEY = "_rep_page"
QUICK_ROW_KEY = "_rep_quick_row"
ACTIVE_ROW_KEY = "_rep_active_row"


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
    from review.core.selection_controller import rep_clear_all
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setSpacing(0)
    # A left inset, for the same reason ``variables/editor_view`` carries one:
    # the rail sits hard against the pane's left edge and the collapse toggle
    # floats over that boundary, overhanging the centre pane by half its own
    # width at about the vertical middle of the panel
    # (``core/app_shell_view.position_rail_toggle``). This layer's content was
    # the only one of the three starting at x=0 — measured, Variables' list and
    # section title start at 12 and Well Labels' grid at 13, because each pays
    # this inset in its own way, and Replicates paid none. The toggle therefore
    # landed on the "Group along:" label and the plate beside it.
    #
    # Set unconditionally rather than only when this function creates the
    # layout: under Sample Map the caller supplies the page and its layout, so
    # the branch above never runs and the margins set inside it never applied.
    layout.setContentsMargins(theme.Spacing.md, 0, 0, 0)
    ctx.widgets.register(PAGE_KEY, parent)

    # The plate this layer edits, in the page rather than the rail. Above the
    # list because that is the reading order of the sentence the two make: this
    # group, these wells.
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

    # Quick Replicates: three labelled fields and the button that applies them.
    # Each label sits with its field in a pair of its own, so the phone form
    # (:func:`set_replicates_phone_form`) can stack the pairs as rows of a form
    # while every label stays beside the field it names. At the desk the pairs
    # lay out exactly as the flat row did — no margins, the row's own spacing.
    hdr2r = ctx.widgets.register(QUICK_ROW_KEY, QWidget(parent))
    hdr2r_l = QHBoxLayout(hdr2r)
    hdr2r_l.setContentsMargins(0, 4, 0, 4)
    layout.addWidget(hdr2r)

    pair_cb = QComboBox(hdr2r)
    pair_cb.addItems(["Rows (A01+A02)", "Columns (A01+B01)"])
    pair_cb.setCurrentText("Rows (A01+A02)")
    hdr2r_l.addWidget(_field_pair(hdr2r, "Group along:", pair_cb))
    ctx.widgets.register("_rep_quick_pair_dir_var", pair_cb)

    size_sb = SpinBox(hdr2r)
    size_sb.setRange(1, len(PLATE_COLS))
    size_sb.setValue(2)
    size_sb.setToolTip(
        "How many consecutive wells form one replicate group "
        "(2 = pairs, 3 = triplicates, …). A partial trailing run becomes a "
        "smaller group.")
    hdr2r_l.addWidget(_field_pair(hdr2r, "Replicates per group:", size_sb))
    ctx.widgets.register("_rep_quick_size_var", size_sb)

    iter_cb = QComboBox(hdr2r)
    iter_cb.addItems(["Across rows", "Down columns"])
    iter_cb.setCurrentText("Across rows")
    hdr2r_l.addWidget(_field_pair(hdr2r, "Order:", iter_cb))
    ctx.widgets.register("_rep_quick_iter_order_var", iter_cb)
    hdr2r_l.addStretch(1)
    hdr2r_l.addWidget(ctx.widgets.register(
        "_rep_quick_apply",
        btn_primary(hdr2r, "Apply Quick Replicates",
                    lambda: rep_quick_pairs_from_dropdowns(ctx))))

    _build_from_variables_row(ctx, parent, layout)

    layout.addWidget(build_hline_separator(parent))

    # Active-group banner — until the user clicks a group below, plate
    # clicks have no effect. Make that state legible so the user isn't
    # left wondering why nothing happens.
    from ui import theme as _theme
    active_row = ctx.widgets.register(ACTIVE_ROW_KEY, QWidget(parent))
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
    from review.features.home.groups.selections_view import wire_selections_list as _wire
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
        "above to add or remove them. Expand a group to edit its replicate "
        "structure.",
        parent,
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    hint.setContentsMargins(0, 6, 0, 8)
    layout.addWidget(hint)


def _field_pair(parent: QWidget, text: str, field: QWidget) -> QWidget:
    """A label and the field it names, as one widget of the Quick Replicates row."""
    pair = QWidget(parent)
    pair.setObjectName("QuickReplicatesField")
    row = QHBoxLayout(pair)
    row.setContentsMargins(0, 0, 0, 0)
    label = QLabel(text, pair)
    row.addWidget(label)
    field.setParent(pair)
    row.addWidget(field)
    return pair


def set_replicates_phone_form(ctx, on: bool) -> None:
    """The Replicates layer as one column in phone layout, or as the desk has it.

    The desk lays the layer out for a pane a thousand pixels wide: Quick
    Replicates is one row of three labelled fields and a button, and the
    active-group banner one line. On a phone:

    - **the plate is the canvas plate** (``role="canvas"``, the Variables
      layer's) — the largest the app draws, since it is the surface this layer
      is operated on, and at the editor role's caps a well was under 30 px;
    - **Quick Replicates is a form**: each field a row, its label in a column
      ``SHEET_LABEL_PT`` wide (the Controls sheet's), the field stretched over
      the rest, rows ``TOUCH_GAP_PT`` apart, and *Apply Quick Replicates* the
      full width under them;
    - **the active-group banner** puts its caption above the group's name, which
      wraps;
    - **no left inset of its own** — the rail toggle it clears is hidden in
      phone layout, and the Sample Map body gives every layer the same gutters.

    Idempotent, and undone exactly by ``on=False``.
    """
    from core import phone_layout
    from widgets.plate_geometry import apply_plate_geometry

    reg = ctx.widgets
    unit = theme.touch_unit() if on else None
    on = bool(on and unit)
    page = reg.get_or_none(PAGE_KEY)
    if page is not None and page.layout() is not None:
        page.layout().setContentsMargins(0 if on else theme.Spacing.md, 0, 0, 0)
    plate = reg.get_or_none("_rep_map_plate")
    if plate is not None:
        apply_plate_geometry(plate, role="canvas" if on else "editor")

    row = reg.get_or_none(QUICK_ROW_KEY)
    if row is not None:
        lay = row.layout()
        lay.setDirection(QBoxLayout.TopToBottom if on else QBoxLayout.LeftToRight)
        lay.setSpacing(round(phone_layout.TOUCH_GAP_PT * unit) if on else -1)
        label_w = round(phone_layout.SHEET_LABEL_PT * unit) if on else None
        for pair in row.findChildren(QWidget, "QuickReplicatesField"):
            pair_lay = pair.layout()
            label = pair_lay.itemAt(0).widget()
            field = pair_lay.itemAt(1).widget()
            label.setWordWrap(on)
            if on:
                label.setFixedWidth(label_w)
            else:
                label.setMinimumWidth(0)
                label.setMaximumWidth(_QT_MAX)
            pair_lay.setStretchFactor(field, 1 if on else 0)

    # The group list scrolls inside itself, and in a page that scrolls it is
    # given only its minimum — measured, two groups' rows and the From selection
    # button in about 110 px, the second row half under the button. Four rows
    # and the button are kept in view; a longer list scrolls inside its own box,
    # which keeps the drag while it has rows to scroll.
    groups = reg.get_or_none("_rep_list")
    if groups is not None:
        groups.setMinimumHeight(
            round(LIST_ROWS_ON_PHONE * phone_layout.TOUCH_ROW_PT * unit) if on else 0)

    active = reg.get_or_none(ACTIVE_ROW_KEY)
    if active is not None:
        active.layout().setDirection(
            QBoxLayout.TopToBottom if on else QBoxLayout.LeftToRight)
        value = reg.get_or_none("_rep_active_group_lbl")
        if value is not None:
            value.setWordWrap(on)


#: Qt's QWIDGETSIZE_MAX, a widget's default maximum width.
_QT_MAX = 16_777_215

#: How many touch rows of the group list stay in view in phone layout — four
#: groups and the list's *From selection* button.
LIST_ROWS_ON_PHONE = 5


#: The button's tooltip while it is enabled — kept on the widget as a property
#: so ``refresh_replicates_button`` can swap in the disabled reason and back.
_FROM_VARIABLES_TIP = (
    "Replace the groups with one replicate group per condition: wells whose "
    "values are identical for every variable on the Variables tab become "
    "replicates of one group. The groups are ordinary groups afterwards — "
    "rename them, recolour them, add or remove wells. Wells with no values "
    "join no group.")


def _build_from_variables_row(ctx, parent: QWidget, layout) -> None:
    """``[Make replicates from variables]`` — identical assignments into groups.

    Beside Quick Replicates because it answers the same request from the other
    end: Quick Replicates groups wells by where they sit on the plate, this by
    what was done to them, which the Variables tab already records.
    """
    row = QWidget(parent)
    row_l = QHBoxLayout(row)
    row_l.setContentsMargins(0, 4, 0, 4)
    layout.addWidget(row)
    row_l.addStretch(1)
    button = ctx.widgets.register(
        "_rep_from_var_btn",
        btn_primary(row, "Make replicates from variables",
                    lambda: rep_replicates_from_variables(ctx)))
    button.setProperty("enabledTip", _FROM_VARIABLES_TIP)
    row_l.addWidget(button)
    refresh_replicates_button(ctx)
